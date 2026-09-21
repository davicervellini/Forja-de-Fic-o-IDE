"""
orchestrator.py — Orquestração do pipeline de geração e refinamento.

Coordena as 3 fases (Rascunho → Polimento → Resumo de Continuidade),
gerencia o fluxo single e batch, e mantém o estado da história (StoryProject)
para garantir persistência e consistência narrativa.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable
from pathlib import Path

from pipeline import config
from pipeline.project import StoryProject
from pipeline.api import generate_text, GenerationInterrupted, OllamaError
from pipeline.io_utils import write_file, save_fragment
from pipeline.prompts import (
    SYSTEM_DRAFTING,
    SYSTEM_REFINING,
    SYSTEM_SUMMARIZING,
    SYSTEM_UPDATING,
    SYSTEM_MERGING,
    build_drafting_prompt,
    build_refining_prompt,
    build_summarizing_prompt,
    build_updating_prompt,
    build_merging_prompt,
)
from pipeline.akashic import extract_style_block

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════

@dataclass
class ChapterResult:
    """Resultado do processamento de um capítulo."""
    chapter_num: int
    premise: str
    draft: str = ""
    final: str = ""
    summary: str = ""
    status: str = "pending"  # pending, drafting, polishing, summarizing, done, error
    error: str = ""


@dataclass
class PipelineCallbacks:
    """
    Callbacks para comunicação com a GUI/CLI.
    Todos são opcionais; se não fornecidos, são no-ops.
    """
    on_status: Callable[[str], None] = field(default=lambda msg: None)
    on_token: Callable[[str, str], None] = field(default=lambda phase, token: None)
    on_phase_complete: Callable[[str, str, int], None] = field(
        default=lambda phase, text, ch_num: None
    )
    on_chapter_start: Callable[[int], None] = field(default=lambda ch_num: None)
    on_chapter_complete: Callable[[int, ChapterResult], None] = field(
        default=lambda ch_num, result: None
    )
    on_pipeline_complete: Callable[[list], None] = field(default=lambda results: None)
    on_error: Callable[[str, int | None], None] = field(
        default=lambda msg, ch_num: None
    )


# ═══════════════════════════════════════════════════════════════
# PIPELINE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class PipelineOrchestrator:
    """
    Orquestra o pipeline completo de geração de ficção, operando
    diretamente sobre um objeto StoryProject.
    """

    def __init__(
        self,
        project: StoryProject,
        cancel_event: threading.Event | None = None,
        model_drafting: str | None = None,
        model_refining: str | None = None,
        model_summarizing: str | None = None,
        drafting_temperature: float | None = None,
        refining_temperature: float | None = None,
        summarizing_temperature: float | None = None,
        drafting_num_ctx: int | None = None,
        refining_num_ctx: int | None = None,
        summarizing_num_ctx: int | None = None,
        request_timeout: int | None = None,
    ):
        self.project = project
        self.akashic_records = self.project.load_akashic_model()
        self.cancel_event = cancel_event or threading.Event()

        # Configurações (usa config.py como fallback)
        self.model_drafting = model_drafting or config.MODEL_DRAFTING
        self.model_refining = model_refining or config.MODEL_REFINING
        self.model_summarizing = model_summarizing or config.MODEL_SUMMARIZING
        self.drafting_temperature = drafting_temperature if drafting_temperature is not None else config.DRAFTING_TEMPERATURE
        self.refining_temperature = refining_temperature if refining_temperature is not None else config.REFINING_TEMPERATURE
        self.summarizing_temperature = summarizing_temperature if summarizing_temperature is not None else config.SUMMARIZING_TEMPERATURE
        self.drafting_num_ctx = drafting_num_ctx or config.DRAFTING_NUM_CTX
        self.refining_num_ctx = refining_num_ctx or config.REFINING_NUM_CTX
        self.summarizing_num_ctx = summarizing_num_ctx or config.SUMMARIZING_NUM_CTX
        self.request_timeout = request_timeout or config.REQUEST_TIMEOUT

        # Opções extras de GPU (num_gpu)
        self.drafting_extra = {"num_gpu": config.DRAFTING_NUM_GPU} if config.DRAFTING_NUM_GPU is not None else {}
        self.refining_extra = {"num_gpu": config.REFINING_NUM_GPU} if config.REFINING_NUM_GPU is not None else {}
        self.summarizing_extra = {"num_gpu": config.SUMMARIZING_NUM_GPU} if config.SUMMARIZING_NUM_GPU is not None else {}

    def _check_cancelled(self):
        """Verifica se o pipeline foi cancelado."""
        if self.cancel_event.is_set():
            raise GenerationInterrupted("", "Pipeline cancelado pelo usuário.")

    # ─── FASES INDIVIDUAIS ──────────────────────────────────

    def _run_drafting(
        self,
        premise: str,
        chapter_num: int,
        callbacks: PipelineCallbacks,
    ) -> str:
        """Fase 1: Geração do rascunho."""
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 1: Construção do Rascunho "
            f"(modelo: {self.model_drafting})"
        )

        user_prompt = build_drafting_prompt(
            premise=premise,
            akashic_records=self.akashic_records,
            story_so_far=self.project.story_so_far,
            recent_summaries=self.project.accumulated_summaries,
            dynamic_memory=self.project.dynamic_memory,
        )

        draft = generate_text(
            model=self.model_drafting,
            system_prompt=SYSTEM_DRAFTING,
            user_prompt=user_prompt,
            temperature=self.drafting_temperature,
            num_ctx=self.drafting_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: callbacks.on_token("drafting", token),
            cancel_event=self.cancel_event,
            extra_options=self.drafting_extra or None,
        )

        chapter_dir = self.project.chapter_dir(chapter_num)
        write_file(chapter_dir / "rascunho.md", draft)
        logger.info(f"Cap {chapter_num:02d} — Rascunho salvo ({len(draft)} chars)")

        callbacks.on_phase_complete("drafting", draft, chapter_num)
        return draft

    def _run_refining(
        self,
        draft: str,
        chapter_num: int,
        callbacks: PipelineCallbacks,
    ) -> str:
        """Fase 2: Polimento literário."""
        self._check_cancelled()

        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 2: Polimento Literário "
            f"(modelo: {self.model_refining})"
        )

        style_block = extract_style_block(self.akashic_records)
        user_prompt = build_refining_prompt(draft, style_block=style_block)

        final = generate_text(
            model=self.model_refining,
            system_prompt=SYSTEM_REFINING,
            user_prompt=user_prompt,
            temperature=self.refining_temperature,
            num_ctx=self.refining_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: callbacks.on_token("refining", token),
            cancel_event=self.cancel_event,
            extra_options=self.refining_extra or None,
        )

        chapter_dir = self.project.chapter_dir(chapter_num)
        write_file(chapter_dir / "capitulo_final.md", final)
        logger.info(f"Cap {chapter_num:02d} — Capítulo final salvo ({len(final)} chars)")

        callbacks.on_phase_complete("refining", final, chapter_num)
        return final

    def _run_summarizing(
        self,
        final_text: str,
        chapter_num: int,
        callbacks: PipelineCallbacks,
    ) -> str:
        """Fase 3: Extração de resumo para continuidade."""
        self._check_cancelled()

        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 3: Resumo de Continuidade "
            f"(modelo: {self.model_summarizing})"
        )

        user_prompt = build_summarizing_prompt(final_text, chapter_num)

        summary = generate_text(
            model=self.model_summarizing,
            system_prompt=SYSTEM_SUMMARIZING,
            user_prompt=user_prompt,
            temperature=self.summarizing_temperature,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: callbacks.on_token("summarizing", token),
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )

        chapter_dir = self.project.chapter_dir(chapter_num)
        write_file(chapter_dir / "resumo.md", summary)
        
        self.project.accumulated_summaries.append((chapter_num, summary))
        logger.info(f"Cap {chapter_num:02d} — Resumo salvo ({len(summary)} chars)")

        callbacks.on_phase_complete("summarizing", summary, chapter_num)
        return summary

    def _run_updating(
        self,
        summary: str,
        chapter_num: int,
        callbacks: PipelineCallbacks,
    ) -> str:
        """Fase 3.5: Arquivista de Lore (Memória Dinâmica)."""
        self._check_cancelled()

        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 3.5: Atualização de Lore "
            f"(modelo: {self.model_summarizing})"
        )

        user_prompt = build_updating_prompt(self.project.dynamic_memory, summary)

        updated_memory = generate_text(
            model=self.model_summarizing,
            system_prompt=SYSTEM_UPDATING,
            user_prompt=user_prompt,
            temperature=self.summarizing_temperature,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: None,  # Silencioso na UI
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )

        self.project.dynamic_memory = updated_memory
        logger.info(f"Cap {chapter_num:02d} — Memória Dinâmica atualizada ({len(updated_memory)} chars)")
        return updated_memory

    def _run_merging(
        self,
        callbacks: PipelineCallbacks,
    ):
        """Fase 4: Fusão de resumos se exceder o limite."""
        self._check_cancelled()
        
        if len(self.project.accumulated_summaries) <= config.RECENT_SUMMARIES_KEPT:
            return

        callbacks.on_status(f"Fase 4: Fundindo resumos antigos (Story So Far)...")

        # Pega os que vão sair da janela
        to_merge = self.project.accumulated_summaries[:-config.RECENT_SUMMARIES_KEPT]
        # Mantém na lista só os recentes
        self.project.accumulated_summaries = self.project.accumulated_summaries[-config.RECENT_SUMMARIES_KEPT:]

        user_prompt = build_merging_prompt(self.project.story_so_far, to_merge)

        new_story_so_far = generate_text(
            model=self.model_summarizing,
            system_prompt=SYSTEM_MERGING,
            user_prompt=user_prompt,
            temperature=self.summarizing_temperature,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: None,
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )

        self.project.story_so_far = new_story_so_far
        logger.info(f"Fusão concluída. Story So Far atualizado ({len(self.project.story_so_far)} chars)")

    # ─── EXECUÇÃO PRINCIPAL ─────────────────────────────────

    def run_single(
        self,
        premise: str,
        chapter_num: int = 1,
        callbacks: PipelineCallbacks | None = None,
    ) -> ChapterResult:
        """Processa um único capítulo pelo pipeline completo."""
        if callbacks is None:
            callbacks = PipelineCallbacks()

        result = ChapterResult(chapter_num=chapter_num, premise=premise)
        callbacks.on_chapter_start(chapter_num)
        
        # Salva a premissa no disco imediatamente
        chapter_dir = self.project.chapter_dir(chapter_num)
        write_file(chapter_dir / "premissa.md", premise)

        try:
            # Fase 1: Rascunho
            result.status = "drafting"
            result.draft = self._run_drafting(premise, chapter_num, callbacks)

            # Fase 2: Polimento
            result.status = "polishing"
            result.final = self._run_refining(result.draft, chapter_num, callbacks)

            # Fase 3: Resumo de Continuidade
            result.status = "summarizing"
            result.summary = self._run_summarizing(result.final, chapter_num, callbacks)

            # Fase 3.5: Memória Dinâmica
            self._run_updating(result.summary, chapter_num, callbacks)
            
            # Fase 4: Fusão (se necessário)
            self._run_merging(callbacks)

            # Atualiza e salva o estado do projeto
            self.project.last_chapter_num = max(self.project.last_chapter_num, chapter_num)
            self.project.save_state()

            result.status = "done"
            callbacks.on_status(
                f"✓ Capítulo {chapter_num:02d} concluído com sucesso!"
            )
            callbacks.on_chapter_complete(chapter_num, result)

        except GenerationInterrupted as e:
            result.status = "error"
            result.error = str(e)

            # Salva fragmentos em qualquer fase
            if e.fragment:
                if not result.draft:
                    save_fragment(chapter_dir / "rascunho.md", e.fragment)
                    result.draft = e.fragment
                elif not result.final:
                    save_fragment(chapter_dir / "capitulo_final.md", e.fragment)
                    result.final = e.fragment
                elif not result.summary:
                    save_fragment(chapter_dir / "resumo.md", e.fragment)
                    result.summary = e.fragment

            callbacks.on_status(f"⚠ Capítulo {chapter_num:02d} interrompido. Fragmentos salvos.")
            callbacks.on_error(str(e), chapter_num)

        except OllamaError as e:
            result.status = "error"
            result.error = str(e)
            callbacks.on_status(f"✗ Erro no capítulo {chapter_num:02d}: {e}")
            callbacks.on_error(str(e), chapter_num)

        except Exception as e:
            result.status = "error"
            result.error = str(e)
            logger.exception(f"Erro inesperado no capítulo {chapter_num:02d}")
            callbacks.on_status(f"✗ Erro inesperado: {e}")
            callbacks.on_error(str(e), chapter_num)

        return result

    def run_batch(
        self,
        premises: list[str],
        callbacks: PipelineCallbacks | None = None,
        start_from: int = 1,
    ) -> list[ChapterResult]:
        """Processa uma fila de capítulos em sequência."""
        if callbacks is None:
            callbacks = PipelineCallbacks()

        results: list[ChapterResult] = []
        total = len(premises)

        callbacks.on_status(
            f"Iniciando pipeline em lote: {total} capítulo(s) na fila."
        )

        for i, premise in enumerate(premises):
            chapter_num = start_from + i

            callbacks.on_status(
                f"═══ Capítulo {chapter_num:02d} de {start_from + total - 1:02d} ═══"
            )

            if self.cancel_event.is_set():
                callbacks.on_status("Pipeline cancelado pelo usuário.")
                break

            result = self.run_single(premise, chapter_num, callbacks)
            results.append(result)

            if result.status == "error":
                callbacks.on_status(
                    f"⚠ Pipeline interrompido no capítulo {chapter_num:02d} "
                    f"devido a erro. Capítulos processados: {len(results)}/{total}."
                )
                break

        callbacks.on_pipeline_complete(results)
        return results
