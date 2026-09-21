"""
orchestrator.py — Orquestração do pipeline de geração e refinamento.

Coordena as fases:
1 Drafting → 2 Refining → 3 Summarizing → 3.5 Lore/Roster/Threads
→ 3.6 Compress (se necessário) → 4 Merging → 5 Consistency (opcional)
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from pipeline import config
from pipeline.project import StoryProject
from pipeline.api import generate_text, GenerationInterrupted, OllamaError
from pipeline.io_utils import write_file, save_fragment
from pipeline.prompts import (
    SYSTEM_DRAFTING,
    SYSTEM_REFINING,
    SYSTEM_SUMMARIZING,
    SYSTEM_UPDATING,
    SYSTEM_COMPRESS_MEMORY,
    SYSTEM_MERGING,
    SYSTEM_CONSISTENCY,
    build_drafting_prompt,
    build_refining_prompt,
    build_summarizing_prompt,
    build_updating_prompt,
    build_compress_memory_prompt,
    build_merging_prompt,
    build_consistency_prompt,
    parse_updating_output,
)
from pipeline.akashic import extract_style_block

logger = logging.getLogger(__name__)


@dataclass
class ChapterResult:
    chapter_num: int
    premise: str
    draft: str = ""
    final: str = ""
    summary: str = ""
    status: str = "pending"
    error: str = ""
    consistency_notes: str = ""


@dataclass
class PipelineCallbacks:
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


class PipelineOrchestrator:
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

        self.drafting_extra = {"num_gpu": config.DRAFTING_NUM_GPU} if config.DRAFTING_NUM_GPU is not None else {}
        self.refining_extra = {"num_gpu": config.REFINING_NUM_GPU} if config.REFINING_NUM_GPU is not None else {}
        self.summarizing_extra = {"num_gpu": config.SUMMARIZING_NUM_GPU} if config.SUMMARIZING_NUM_GPU is not None else {}

    def _check_cancelled(self):
        if self.cancel_event.is_set():
            raise GenerationInterrupted("", "Pipeline cancelado pelo usuário.")

    def _run_drafting(self, premise: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 1: Rascunho ({self.model_drafting})"
        )
        user_prompt = build_drafting_prompt(
            premise=premise,
            akashic_records=self.akashic_records,
            story_so_far=self.project.story_so_far,
            recent_summaries=self.project.accumulated_summaries,
            dynamic_memory=self.project.dynamic_memory,
            character_roster=self.project.character_roster,
            open_threads=self.project.open_threads,
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

    def _run_refining(self, draft: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        self._check_cancelled()
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 2: Polimento ({self.model_refining})"
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

    def _run_summarizing(self, final_text: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        self._check_cancelled()
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 3: Resumo ({self.model_summarizing})"
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

    def _run_updating(self, summary: str, chapter_num: int, callbacks: PipelineCallbacks):
        """Fase 3.5: atualiza Memória Dinâmica + Roster + Open Threads."""
        self._check_cancelled()
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 3.5: Lore + Roster + Threads"
        )
        user_prompt = build_updating_prompt(
            current_memory=self.project.dynamic_memory,
            chapter_summary=summary,
            current_roster=self.project.character_roster,
            current_threads=self.project.open_threads,
            chapter_num=chapter_num,
        )
        raw = generate_text(
            model=self.model_summarizing,
            system_prompt=SYSTEM_UPDATING,
            user_prompt=user_prompt,
            temperature=self.summarizing_temperature,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: None,
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )
        memory, roster, threads = parse_updating_output(raw)
        if memory:
            self.project.dynamic_memory = memory
        if roster:
            self.project.character_roster = roster
        if threads:
            self.project.open_threads = threads
        logger.info(
            f"Cap {chapter_num:02d} — Memória/Roster/Threads atualizados "
            f"(mem={len(self.project.dynamic_memory)}, "
            f"roster={len(self.project.character_roster)}, "
            f"threads={len(self.project.open_threads)})"
        )

    def _run_compress_memory(self, callbacks: PipelineCallbacks):
        """Fase 3.6: comprime a memória dinâmica se passou do limite."""
        mem = self.project.dynamic_memory or ""
        words = len(mem.split())
        if words <= config.DYNAMIC_MEMORY_MAX_WORDS:
            return
        self._check_cancelled()
        callbacks.on_status(
            f"Fase 3.6: Comprimindo Memória Dinâmica ({words} → ~{config.DYNAMIC_MEMORY_MAX_WORDS} palavras)"
        )
        user_prompt = build_compress_memory_prompt(mem)
        compressed = generate_text(
            model=self.model_summarizing,
            system_prompt=SYSTEM_COMPRESS_MEMORY,
            user_prompt=user_prompt,
            temperature=0.2,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: None,
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )
        self.project.dynamic_memory = compressed.strip()
        logger.info(f"Memória Dinâmica comprimida: {words} → {len(compressed.split())} palavras")

    def _run_merging(self, callbacks: PipelineCallbacks):
        self._check_cancelled()
        if len(self.project.accumulated_summaries) <= config.RECENT_SUMMARIES_KEPT:
            return
        callbacks.on_status("Fase 4: Fundindo resumos antigos (Story So Far)...")
        to_merge = self.project.accumulated_summaries[:-config.RECENT_SUMMARIES_KEPT]
        self.project.accumulated_summaries = self.project.accumulated_summaries[-config.RECENT_SUMMARIES_KEPT:]
        user_prompt = build_merging_prompt(self.project.story_so_far, to_merge)
        new_story = generate_text(
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
        self.project.story_so_far = new_story
        logger.info(f"Story So Far atualizado ({len(self.project.story_so_far)} chars)")

    def _run_consistency_check(self, final_text: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        """Fase 5 opcional: auditoria de continuidade."""
        if not config.CONSISTENCY_CHECK_ENABLED:
            return ""
        self._check_cancelled()
        callbacks.on_status(f"Capítulo {chapter_num:02d} — Fase 5: Consistency Check")
        user_prompt = build_consistency_prompt(
            akashic=self.akashic_records,
            dynamic_memory=self.project.dynamic_memory,
            roster=self.project.character_roster,
            chapter_text=final_text,
        )
        result = generate_text(
            model=self.model_summarizing,
            system_prompt=SYSTEM_CONSISTENCY,
            user_prompt=user_prompt,
            temperature=0.1,
            num_ctx=self.summarizing_num_ctx,
            timeout=min(self.request_timeout, 180),
            on_token=lambda token: None,
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )
        notes = result.strip()
        if notes.upper() != "OK":
            logger.warning(f"Cap {chapter_num:02d} — Consistency issues: {notes[:200]}")
            callbacks.on_status(f"⚠ Consistency: {notes[:120]}...")
        else:
            logger.info(f"Cap {chapter_num:02d} — Consistency OK")
        return notes

    def run_single(
        self,
        premise: str,
        chapter_num: int = 1,
        callbacks: PipelineCallbacks | None = None,
    ) -> ChapterResult:
        if callbacks is None:
            callbacks = PipelineCallbacks()

        result = ChapterResult(chapter_num=chapter_num, premise=premise)
        callbacks.on_chapter_start(chapter_num)

        chapter_dir = self.project.chapter_dir(chapter_num)
        write_file(chapter_dir / "premissa.md", premise)

        try:
            result.status = "drafting"
            result.draft = self._run_drafting(premise, chapter_num, callbacks)

            result.status = "polishing"
            result.final = self._run_refining(result.draft, chapter_num, callbacks)

            result.status = "summarizing"
            result.summary = self._run_summarizing(result.final, chapter_num, callbacks)

            self._run_updating(result.summary, chapter_num, callbacks)
            self._run_compress_memory(callbacks)
            self._run_merging(callbacks)

            result.consistency_notes = self._run_consistency_check(
                result.final, chapter_num, callbacks
            )

            self.project.last_chapter_num = max(self.project.last_chapter_num, chapter_num)
            self.project.save_state()

            result.status = "done"
            callbacks.on_status(f"✓ Capítulo {chapter_num:02d} concluído!")
            callbacks.on_chapter_complete(chapter_num, result)

        except GenerationInterrupted as e:
            result.status = "error"
            result.error = str(e)
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
            callbacks.on_status(f"⚠ Capítulo {chapter_num:02d} interrompido.")
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
        if callbacks is None:
            callbacks = PipelineCallbacks()

        results: list[ChapterResult] = []
        total = len(premises)
        callbacks.on_status(f"Iniciando pipeline em lote: {total} capítulo(s).")

        for i, premise in enumerate(premises):
            chapter_num = start_from + i
            callbacks.on_status(f"═══ Capítulo {chapter_num:02d} de {start_from + total - 1:02d} ═══")
            if self.cancel_event.is_set():
                callbacks.on_status("Pipeline cancelado pelo usuário.")
                break
            result = self.run_single(premise, chapter_num, callbacks)
            results.append(result)
            if result.status == "error":
                callbacks.on_status(
                    f"⚠ Pipeline interrompido no capítulo {chapter_num:02d}. "
                    f"Processados: {len(results)}/{total}."
                )
                break

        callbacks.on_pipeline_complete(results)
        return results
