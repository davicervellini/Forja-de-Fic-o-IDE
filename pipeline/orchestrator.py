"""
orchestrator.py — Orquestração do pipeline de geração e refinamento.

Coordena as fases:
1 Drafting → 2 Refining → 3 Summarizing → 3.5 Lore/Roster/Threads
→ 3.6 Compress (se necessário) → 4 Merging → 5 Consistency (opcional)
"""

import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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
    SYSTEM_SCENE_PLANNER,
    build_scene_prompt,
    build_planner_prompt,
    scene_guidance,
)
from pipeline.akashic_schema import read_meta
from pipeline.languages import chapter_heading, notes_language, notes_rule, story_language, story_rule
from pipeline.premise import chapter_guidance
from pipeline.premise import from_text as premise_from_text
from pipeline.chapters import (
    VERSIONED_FILES,
    archive_version,
    later_done,
    load_snapshot,
    update_info,
)
from pipeline.akashic import extract_style_block
from pipeline.scenes import (
    Scene,
    assemble_chapter,
    clean_scene,
    count_words,
    drop_new_system_lines,
    drop_overlap,
    last_sentence,
    ngrams_of,
    parse_premise,
    remove_repeated_sentences,
    remove_repetition,
    split_chapter,
    split_long_paragraphs,
    tail_words,
    trim_incomplete_ending,
)

logger = logging.getLogger(__name__)

_MIDDLE_NOTE = (
    "Refeito depois de capítulos posteriores: só o resumo foi trocado. Memória, roster e "
    "threads continuam com fatos da versão anterior, porque os capítulos seguintes foram "
    "escritos em cima deles."
)


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

        # Local (Ollama) ou nuvem, por fase. Na nuvem num_ctx e num_gpu não se aplicam.
        self.provider_drafting = config.PROVIDER_DRAFTING
        self.provider_refining = config.PROVIDER_REFINING
        self.provider_summarizing = config.PROVIDER_SUMMARIZING
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
        # História no idioma do projeto; o resto (resumos, memória, cenas planejadas, checagem) no do usuário.
        self.story_rule = story_rule(story_language(project))
        self.notes_rule = notes_rule(notes_language())

        self.drafting_extra = {"num_gpu": config.DRAFTING_NUM_GPU} if config.DRAFTING_NUM_GPU is not None else {}
        self.refining_extra = {"num_gpu": config.REFINING_NUM_GPU} if config.REFINING_NUM_GPU is not None else {}
        self.summarizing_extra = {"num_gpu": config.SUMMARIZING_NUM_GPU} if config.SUMMARIZING_NUM_GPU is not None else {}

    def _check_cancelled(self):
        if self.cancel_event.is_set():
            raise GenerationInterrupted("", "Pipeline cancelado pelo usuário.")

    # ─── Rascunho cena por cena ─────────────────────────────

    def _previous_chapter_tail(self, chapter_num: int) -> str:
        path = self.project.chapter_dir(chapter_num - 1) / "capitulo_final.md"
        if chapter_num <= 1 or not path.exists():
            return ""
        _, scenes = split_chapter(path.read_text(encoding="utf-8"))
        return tail_words("\n\n".join(scenes), config.PREVIOUS_CHAPTER_TAIL_WORDS)

    def _next_chapter_opening(self, chapter_num: int) -> str:
        """
        Abertura da premissa do capítulo seguinte, se o usuário já escreveu. Sem o campo Abertura,
        vale a primeira cena. Vazio quando não há premissa: a última cena termina no gancho.
        """
        path = self.project.chapter_dir(chapter_num + 1) / "premissa.md"
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return ""
        form = premise_from_text(text)
        if form.opening.strip():
            return form.opening.strip()
        plan = parse_premise(text)
        return plan.scenes[0].text.strip() if plan.scenes else ""

    def _plan_scenes(self, premise: str, chapter_num: int, callbacks: PipelineCallbacks):
        """Cenas da premissa. Sem cenas numeradas, o próprio modelo divide a premissa em cenas."""
        plan = parse_premise(premise)
        if plan.scenes:
            return plan
        callbacks.on_status(f"Capítulo {chapter_num:02d} — Dividindo a premissa em cenas")
        raw = generate_text(
            model=self.model_drafting,
            provider=self.provider_drafting,
            system_prompt=SYSTEM_SCENE_PLANNER,
            user_prompt=f"{build_planner_prompt(premise, self.akashic_records)}\n\n{self.notes_rule}",
            temperature=0.3,
            num_ctx=self.drafting_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: None,
            cancel_event=self.cancel_event,
            extra_options=self.drafting_extra or None,
        )
        planned = parse_premise("Scenes:\n" + raw)
        planned.title = plan.title
        planned.hook = planned.hook or plan.hook
        if not planned.scenes:
            logger.warning("O modelo não devolveu cenas numeradas; a premissa vira uma cena só.")
            planned.scenes = [Scene(num=1, text=premise.strip())]
        else:
            write_file(self.project.chapter_dir(chapter_num) / "cenas_planejadas.md", raw.strip())
        return planned

    def _generate_scene_text(self, user_prompt: str, target_words: int, callbacks: PipelineCallbacks) -> str:
        options: dict[str, Any] = dict(self.drafting_extra)
        # Teto de ~1,4x a meta: acima disso o modelo pequeno costuma entrar em laço ou invadir a próxima cena.
        options["num_predict"] = int(target_words * 1.33 * 1.4) + 150
        options["repeat_penalty"] = config.DRAFTING_REPEAT_PENALTY
        # O padrão do Ollama olha só os últimos 64 tokens, curto demais para pegar frases repetidas.
        options["repeat_last_n"] = config.DRAFTING_REPEAT_LAST_N
        return generate_text(
            model=self.model_drafting,
            provider=self.provider_drafting,
            system_prompt=SYSTEM_DRAFTING,
            user_prompt=f"{user_prompt}\n\n{self.story_rule}",
            temperature=self.drafting_temperature,
            num_ctx=self.drafting_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: callbacks.on_token("drafting", token),
            cancel_event=self.cancel_event,
            extra_options=options,
        )

    def _character_sheets(self) -> dict[str, str]:
        """Nome → ficha para o modelo, dos metadados do Registro Akáshico."""
        path = self.project.akashic_path
        if not path.exists():
            return {}
        meta, _ = read_meta(path.read_text(encoding="utf-8"))
        return {c.name: c.sheet.strip() for c in meta.characters} if meta else {}

    def _location_sheets(self) -> tuple[dict[str, str], list[str]]:
        """(nome → ficha dos locais, nomes marcados "sempre no contexto"), dos metadados do Registro."""
        path = self.project.akashic_path
        if not path.exists():
            return {}, []
        meta, _ = read_meta(path.read_text(encoding="utf-8"))
        if not meta:
            return {}, []
        sheets = {loc.name: loc.model_sheet.strip() for loc in meta.locations if loc.model_sheet.strip()}
        return sheets, [loc.name for loc in meta.locations if loc.always and loc.model_sheet.strip()]

    def _protagonist_voice(self) -> str:
        """Ficha curta do(s) protagonista(s), lida dos metadados do Registro Akáshico."""
        path = self.project.akashic_path
        if not path.exists():
            return ""
        meta, _ = read_meta(path.read_text(encoding="utf-8"))
        if meta is None:
            return ""
        heroes = [c for c in meta.characters if c.role == "protagonist" and c.sheet.strip()]
        return " ".join(f"{c.name}: {c.sheet.strip()}" for c in heroes[:2])

    @staticmethod
    def _clean_generated(raw: str, seen: set, previous_text: str) -> str:
        """Limpeza determinística do que o modelo escreveu: título solto, frase cortada, laços e recomeços."""
        text = trim_incomplete_ending(clean_scene(raw))
        text = remove_repetition(text)
        if previous_text.strip():
            text = drop_overlap(text, previous_text)
        return split_long_paragraphs(remove_repeated_sentences(text, seen))

    def _run_drafting(self, premise: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        plan = self._plan_scenes(premise, chapter_num, callbacks)
        scenes = plan.scenes
        total = len(scenes)
        default_target = max(250, config.CHAPTER_TARGET_WORDS // total)
        title = chapter_heading(plan.title, chapter_num, story_language(self.project))
        prev_tail = self._previous_chapter_tail(chapter_num)
        next_opening = self._next_chapter_opening(chapter_num)
        voice = self._protagonist_voice()
        # Premissa guiada: elenco do capítulo com as fichas, abertura e o que precisa ou não pode aparecer.
        form = premise_from_text(premise)
        sheets = self._character_sheets() if form.characters else {}
        places, always_places = self._location_sheets()
        common: dict[str, Any] = dict(
            premise=premise,
            akashic_records=self.akashic_records,
            chapter_num=chapter_num,
            scene_total=total,
            hook=plan.hook,
            story_so_far=self.project.story_so_far,
            recent_summaries=self.project.accumulated_summaries,
            dynamic_memory=self.project.dynamic_memory,
            character_roster=self.project.character_roster,
            open_threads=self.project.open_threads,
            previous_chapter_tail=prev_tail,
            next_chapter_opening=next_opening,
        )
        # Sequências de palavras já usadas (inclusive no fim do capítulo anterior): frases que as
        # repetem são laço ou recomeço, e saem do texto.
        seen = ngrams_of(prev_tail)

        written: list[str] = []
        callbacks.on_token("drafting", title + "\n\n")
        for i, scene in enumerate(scenes, start=1):
            self._check_cancelled()
            target = scene.target_words or default_target
            next_text = scenes[i].text if i < total else ""
            if i > 1:
                callbacks.on_token("drafting", f"\n\n{config.SCENE_BREAK}\n\n")
            text = ""
            try:
                callbacks.on_status(
                    f"Capítulo {chapter_num:02d} — Rascunho: cena {i}/{total}, ~{target} palavras ({self.model_drafting})"
                )
                chapter_text = "\n\n".join(written)
                so_far = tail_words(chapter_text, config.CHAPTER_SO_FAR_TAIL_WORDS)
                guidance = "\n".join(filter(None, [
                    scene_guidance(next_text, last_sentence(chapter_text or prev_tail), voice),
                    chapter_guidance(form, sheets, first_scene=not chapter_text, places=places, always=always_places),
                ]))
                raw = self._generate_scene_text(
                    build_scene_prompt(scene_num=i, scene_text=scene.text, target_words=target,
                                       chapter_so_far=so_far, guidance=guidance, **common),
                    target, callbacks,
                )
                text = self._clean_generated(raw, seen, chapter_text or prev_tail)
                for _ in range(config.SCENE_MAX_CONTINUATIONS):
                    if count_words(text) >= target * config.SCENE_MIN_RATIO:
                        break
                    missing = target - count_words(text)
                    callbacks.on_status(
                        f"Capítulo {chapter_num:02d} — Cena {i}/{total} curta ({count_words(text)} palavras); continuando"
                    )
                    callbacks.on_token("drafting", "\n\n")
                    guidance = "\n".join(filter(None, [
                        scene_guidance(next_text, last_sentence(text), voice),
                        chapter_guidance(form, sheets, first_scene=False, places=places, always=always_places),
                    ]))
                    raw = self._generate_scene_text(
                        build_scene_prompt(scene_num=i, scene_text=scene.text, target_words=missing,
                                           chapter_so_far=so_far, scene_so_far=text, guidance=guidance, **common),
                        missing, callbacks,
                    )
                    more = self._clean_generated(raw, seen, f"{chapter_text}\n\n{text}")
                    if count_words(more) < 40:
                        break
                    text = f"{text}\n\n{more}"
            except GenerationInterrupted as e:
                partial = assemble_chapter(title, written + [f"{text}\n\n{e.fragment}".strip()], config.SCENE_BREAK)
                raise GenerationInterrupted(partial, str(e)) from e
            logger.info(f"Cap {chapter_num:02d} — cena {i}/{total}: {count_words(text)} palavras (meta {target})")
            written.append(text)

        draft = assemble_chapter(title, written, config.SCENE_BREAK)
        chapter_dir = self.project.chapter_dir(chapter_num)
        write_file(chapter_dir / "rascunho.md", draft)
        logger.info(f"Cap {chapter_num:02d} — Rascunho salvo ({count_words(draft)} palavras, {total} cenas)")
        callbacks.on_phase_complete("drafting", draft, chapter_num)
        return draft

    # ─── Polimento cena por cena ────────────────────────────

    def _run_refining(self, draft: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        self._check_cancelled()
        title, scenes = split_chapter(draft)
        style_block = extract_style_block(self.akashic_records)
        total = len(scenes)
        polished: list[str] = []
        rejected: list[str] = []
        if title:
            callbacks.on_token("refining", title + "\n\n")
        for i, scene in enumerate(scenes, start=1):
            self._check_cancelled()
            if i > 1:
                callbacks.on_token("refining", f"\n\n{config.SCENE_BREAK}\n\n")
            callbacks.on_status(
                f"Capítulo {chapter_num:02d} — Polimento: cena {i}/{total} ({self.model_refining})"
            )
            options: dict[str, Any] = dict(self.refining_extra)
            options["num_predict"] = int(count_words(scene) * 1.33 * 1.6) + 200
            try:
                out = generate_text(
                    model=self.model_refining,
                    provider=self.provider_refining,
                    system_prompt=SYSTEM_REFINING,
                    user_prompt=build_refining_prompt(scene, style_block=style_block,
                                                      scene_label=f"scene {i} of {total} of a chapter")
                    + f"\n\n{self.story_rule}",
                    temperature=self.refining_temperature,
                    num_ctx=self.refining_num_ctx,
                    timeout=self.request_timeout,
                    on_token=lambda token: callbacks.on_token("refining", token),
                    cancel_event=self.cancel_event,
                    extra_options=options,
                )
            except GenerationInterrupted as e:
                partial = assemble_chapter(title, polished + scenes[i - 1:], config.SCENE_BREAK)
                raise GenerationInterrupted(partial, str(e)) from e
            out = split_long_paragraphs(drop_new_system_lines(clean_scene(out), scene))
            before, after = count_words(scene), count_words(out)
            if after < before * config.REFINE_MIN_RATIO:
                logger.warning(
                    f"Cap {chapter_num:02d} — polimento da cena {i} encolheu ({before} → {after} palavras); "
                    "mantendo o rascunho da cena"
                )
                rejected.append(f"## Cena {i} ({before} → {after} palavras)\n\n{out}")
                out = scene
            polished.append(out)

        final = assemble_chapter(title, polished, config.SCENE_BREAK)
        chapter_dir = self.project.chapter_dir(chapter_num)
        if rejected:
            # Guardado para comparação: o capítulo final usa o rascunho dessas cenas.
            write_file(chapter_dir / "polimento_descartado.md", "\n\n".join(rejected))
        write_file(chapter_dir / "capitulo_final.md", final)
        logger.info(f"Cap {chapter_num:02d} — Capítulo final salvo ({count_words(final)} palavras)")
        callbacks.on_phase_complete("refining", final, chapter_num)
        return final

    def _run_summarizing(
        self, final_text: str, chapter_num: int, callbacks: PipelineCallbacks, record: bool = True
    ) -> str:
        self._check_cancelled()
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 3: Resumo ({self.model_summarizing})"
        )
        user_prompt = build_summarizing_prompt(final_text, chapter_num)
        summary = generate_text(
            model=self.model_summarizing,
            provider=self.provider_summarizing,
            system_prompt=SYSTEM_SUMMARIZING,
            user_prompt=f"{user_prompt}\n\n{self.notes_rule}",
            temperature=self.summarizing_temperature,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: callbacks.on_token("summarizing", token),
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )
        # resumo.md só é gravado em run_single, depois que o estado inteiro foi salvo:
        # sem ele o capítulo não conta como concluído se uma fase seguinte falhar.
        # Um resumo anterior do mesmo capítulo (capítulo refeito) é substituído, não duplicado.
        # record=False: só gera o texto; quem chamou decide onde ele entra.
        if record:
            others = [(n, t) for n, t in self.project.accumulated_summaries if n != chapter_num]
            self.project.accumulated_summaries = sorted(others + [(chapter_num, summary)], key=lambda s: s[0])
        logger.info(f"Cap {chapter_num:02d} — Resumo gerado ({len(summary)} chars)")
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
            provider=self.provider_summarizing,
            system_prompt=SYSTEM_UPDATING,
            user_prompt=f"{user_prompt}\n\n{self.notes_rule}",
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
            provider=self.provider_summarizing,
            system_prompt=SYSTEM_COMPRESS_MEMORY,
            user_prompt=f"{user_prompt}\n\n{self.notes_rule}",
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
        keep = self.project.accumulated_summaries[-config.RECENT_SUMMARIES_KEPT:]
        user_prompt = build_merging_prompt(self.project.story_so_far, to_merge)
        new_story = generate_text(
            model=self.model_summarizing,
            provider=self.provider_summarizing,
            system_prompt=SYSTEM_MERGING,
            user_prompt=f"{user_prompt}\n\n{self.notes_rule}",
            temperature=self.summarizing_temperature,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: None,
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )
        if not new_story.strip():
            raise OllamaError("A fusão de resumos voltou vazia; nenhum resumo foi descartado.")
        # Os resumos antigos só saem da lista depois que a fusão deu certo.
        self.project.story_so_far = new_story.strip()
        self.project.accumulated_summaries = keep
        logger.info(f"Story So Far atualizado ({len(self.project.story_so_far)} chars)")

    def _run_consistency_check(self, final_text: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        """
        Fase opcional: auditoria de continuidade.

        Roda antes de a memória ser atualizada com o próprio capítulo, para comparar o
        texto com o que já estava estabelecido. É opcional de verdade: se falhar por
        erro ou timeout, o capítulo segue; só um cancelamento do usuário interrompe.
        O resultado vai para consistencia.md na pasta do capítulo.
        """
        if not config.CONSISTENCY_CHECK_ENABLED:
            return ""
        self._check_cancelled()
        callbacks.on_status(f"Capítulo {chapter_num:02d} — Checagem de consistência")
        user_prompt = build_consistency_prompt(
            akashic=self.akashic_records,
            dynamic_memory=self.project.dynamic_memory,
            roster=self.project.character_roster,
            chapter_text=final_text,
        )
        try:
            result = generate_text(
                model=self.model_summarizing,
                provider=self.provider_summarizing,
                system_prompt=SYSTEM_CONSISTENCY,
                user_prompt=f"{user_prompt}\n\n{self.notes_rule}",
                temperature=0.1,
                num_ctx=self.summarizing_num_ctx,
                timeout=min(self.request_timeout, 180),
                on_token=lambda token: None,
                cancel_event=self.cancel_event,
                extra_options=self.summarizing_extra or None,
            )
        except (GenerationInterrupted, OllamaError) as e:
            if self.cancel_event.is_set():
                raise
            notes = f"Checagem de consistência não rodou: {e}"
            logger.warning(f"Cap {chapter_num:02d} — {notes}")
            callbacks.on_status(f"⚠ {notes[:120]}")
            write_file(self.project.chapter_dir(chapter_num) / "consistencia.md", notes)
            return notes

        notes = result.strip()
        if notes.strip(" .!\n").upper() == "OK":
            logger.info(f"Cap {chapter_num:02d} — Consistência OK")
        else:
            logger.warning(f"Cap {chapter_num:02d} — Problemas de consistência: {notes[:200]}")
            callbacks.on_status(f"⚠ Consistência: veja consistencia.md do capítulo {chapter_num:02d}")
        write_file(self.project.chapter_dir(chapter_num) / "consistencia.md", notes)
        return notes

    def _finish_chapter(self, result: ChapterResult, chapter_num: int, callbacks: PipelineCallbacks):
        """Do texto final em diante: resumo, consistência, memória, fusão, estado e resumo.md."""
        chapter_dir = self.project.chapter_dir(chapter_num)
        result.status = "summarizing"
        result.summary = self._run_summarizing(result.final, chapter_num, callbacks)

        result.consistency_notes = self._run_consistency_check(result.final, chapter_num, callbacks)

        self._run_updating(result.summary, chapter_num, callbacks)
        self._run_compress_memory(callbacks)
        self._run_merging(callbacks)

        self.project.last_chapter_num = max(self.project.last_chapter_num, chapter_num)
        self.project.save_state()
        # Por último: com resumo.md no disco o capítulo passa a contar como concluído.
        write_file(chapter_dir / "resumo.md", result.summary)
        update_info(self.project, chapter_num, memory_stale=False, memory_note="")

    def _fail(self, e: Exception, result: ChapterResult, chapter_num: int, callbacks: PipelineCallbacks):
        """Marca o resultado como erro e avisa a interface. O estado já foi restaurado por quem chamou."""
        result.status = "error"
        result.error = str(e)
        if isinstance(e, GenerationInterrupted):
            callbacks.on_status(f"⚠ Capítulo {chapter_num:02d} interrompido.")
        elif isinstance(e, OllamaError):
            callbacks.on_status(f"✗ Erro no capítulo {chapter_num:02d}: {e}")
        else:
            logger.exception(f"Erro inesperado no capítulo {chapter_num:02d}")
            callbacks.on_status(f"✗ Erro inesperado: {e}")
        callbacks.on_error(str(e), chapter_num)

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
        # Estado de antes do capítulo: volta a valer se qualquer fase falhar, e fica
        # gravado na pasta do capítulo para a exclusão poder desfazê-lo depois.
        before = self.project.snapshot_state()
        self.project.save_chapter_snapshot(chapter_num, before)
        write_file(chapter_dir / "premissa.md", premise)

        try:
            result.status = "drafting"
            result.draft = self._run_drafting(premise, chapter_num, callbacks)

            result.status = "polishing"
            result.final = self._run_refining(result.draft, chapter_num, callbacks)

            self._finish_chapter(result, chapter_num, callbacks)

            result.status = "done"
            callbacks.on_status(f"✓ Capítulo {chapter_num:02d} concluído!")
            callbacks.on_chapter_complete(chapter_num, result)

        except Exception as e:
            self.project.restore_state(before)
            if isinstance(e, GenerationInterrupted) and e.fragment:
                if not result.draft:
                    save_fragment(chapter_dir / "rascunho.md", e.fragment)
                    result.draft = e.fragment
                elif not result.final:
                    save_fragment(chapter_dir / "capitulo_final.md", e.fragment)
                    result.final = e.fragment
                elif not result.summary:
                    save_fragment(chapter_dir / "resumo.md", e.fragment)
                    result.summary = e.fragment
            self._fail(e, result, chapter_num, callbacks)

        return result

    # ─── Refazer e atualizar a memória de um capítulo pronto ─

    def _memory_base(self, chapter_num: int) -> dict | None:
        """
        Estado da história de antes do capítulo, se dá para voltar a ele com segurança:
        nenhum capítulo concluído depois dele e um snapshot gravado (ou nenhum outro
        capítulo concluído, e então o estado de antes é o vazio). None nos outros casos.
        """
        if later_done(self.project, chapter_num):
            return None
        snap = load_snapshot(self.project, chapter_num)
        if snap is not None:
            return snap
        others = [e.num for e in self.project.scan_chapters() if e.num != chapter_num and e.status == "done"]
        return {} if not others else None

    def _put_back(self, chapter_num: int, archived):
        """Depois de uma falha, põe de volta os textos que o capítulo tinha antes de refazer."""
        chapter_dir = self.project.chapter_dir(chapter_num)
        # O que a tentativa chegou a escrever fica guardado como versão, para comparação.
        archive_version(self.project, chapter_num, "parcial")
        for name in VERSIONED_FILES:
            if (chapter_dir / name).exists():
                (chapter_dir / name).unlink()
        if archived is not None:
            for f in Path(archived).iterdir():
                if f.is_file():
                    shutil.copy2(f, chapter_dir / f.name)

    def redo_chapter(
        self,
        chapter_num: int,
        premise: str | None = None,
        callbacks: PipelineCallbacks | None = None,
    ) -> ChapterResult:
        """
        Escreve de novo um capítulo, a partir da premissa (a nova, se vier, ou a gravada).

        Os textos atuais viram uma versão em `versoes/`. Se for o último capítulo concluído,
        a memória da história volta ao estado de antes dele e o capítulo roda inteiro, como
        na primeira vez. Se houver capítulos concluídos depois dele, o texto é escrito com a
        memória de antes dele como contexto, só o resumo é trocado, e memória, roster e
        threads ficam como estão, porque os capítulos seguintes foram escritos em cima deles.
        Se algo falhar, os textos e o estado anteriores voltam.
        """
        if callbacks is None:
            callbacks = PipelineCallbacks()
        chapter_dir = self.project.chapter_dir(chapter_num)
        premise_path = chapter_dir / "premissa.md"
        if premise is None:
            premise = premise_path.read_text(encoding="utf-8") if premise_path.exists() else ""
        premise = premise.strip()
        if not premise:
            raise ValueError(f"O capítulo {chapter_num:02d} não tem premissa.")

        pre = self.project.snapshot_state()
        base = self._memory_base(chapter_num)
        archived = archive_version(self.project, chapter_num, "refazer")
        for name in VERSIONED_FILES:
            if name != "premissa.md" and (chapter_dir / name).exists():
                (chapter_dir / name).unlink()

        if base is not None:
            self.project.restore_state(base)
            result = self.run_single(premise, chapter_num, callbacks)
            if result.status == "error":
                self.project.restore_state(pre)
                self.project.save_state()
                self._put_back(chapter_num, archived)
            return result

        # Capítulo do meio: contexto de antes dele, sem mexer na memória atual.
        result = ChapterResult(chapter_num=chapter_num, premise=premise)
        callbacks.on_chapter_start(chapter_num)
        snap = load_snapshot(self.project, chapter_num)
        write_file(premise_path, premise)
        try:
            if snap is not None:
                self.project.restore_state(snap)
            try:
                result.status = "drafting"
                result.draft = self._run_drafting(premise, chapter_num, callbacks)
                result.status = "polishing"
                result.final = self._run_refining(result.draft, chapter_num, callbacks)
                result.status = "summarizing"
                result.summary = self._run_summarizing(result.final, chapter_num, callbacks, record=False)
            finally:
                self.project.restore_state(pre)
            self._replace_summary(chapter_num, result.summary)
            self.project.save_state()
            write_file(chapter_dir / "resumo.md", result.summary)
            update_info(self.project, chapter_num, memory_stale=False, memory_note=_MIDDLE_NOTE)
            result.status = "done"
            callbacks.on_status(f"✓ Capítulo {chapter_num:02d} refeito.")
            callbacks.on_chapter_complete(chapter_num, result)
        except Exception as e:
            self.project.restore_state(pre)
            self._put_back(chapter_num, archived)
            self._fail(e, result, chapter_num, callbacks)
        return result

    def _replace_summary(self, chapter_num: int, summary: str):
        """Troca o resumo do capítulo na lista, se ele ainda estiver lá (e não fundido na história)."""
        self.project.accumulated_summaries = [
            (n, summary if n == chapter_num else t) for n, t in self.project.accumulated_summaries
        ]

    def refresh_memory(self, chapter_num: int, callbacks: PipelineCallbacks | None = None) -> ChapterResult:
        """
        Refaz resumo e memória a partir do texto final atual (depois de uma edição manual).

        Último capítulo concluído: a memória volta ao estado de antes dele e é atualizada com
        o texto novo. Capítulo do meio: só o resumo é refeito.
        """
        if callbacks is None:
            callbacks = PipelineCallbacks()
        chapter_dir = self.project.chapter_dir(chapter_num)
        final_path = chapter_dir / "capitulo_final.md"
        if not final_path.exists():
            raise ValueError(f"O capítulo {chapter_num:02d} ainda não tem texto final.")
        premise_path = chapter_dir / "premissa.md"
        result = ChapterResult(
            chapter_num=chapter_num,
            premise=premise_path.read_text(encoding="utf-8") if premise_path.exists() else "",
            final=final_path.read_text(encoding="utf-8"),
        )
        callbacks.on_chapter_start(chapter_num)
        pre = self.project.snapshot_state()
        base = self._memory_base(chapter_num)
        try:
            if base is not None:
                self.project.restore_state(base)
                self._finish_chapter(result, chapter_num, callbacks)
            else:
                result.status = "summarizing"
                result.summary = self._run_summarizing(result.final, chapter_num, callbacks, record=False)
                self._replace_summary(chapter_num, result.summary)
                self.project.save_state()
                write_file(chapter_dir / "resumo.md", result.summary)
                update_info(self.project, chapter_num, memory_stale=False, memory_note=_MIDDLE_NOTE)
            result.status = "done"
            callbacks.on_status(f"✓ Memória atualizada com o capítulo {chapter_num:02d}.")
            callbacks.on_chapter_complete(chapter_num, result)
        except Exception as e:
            self.project.restore_state(pre)
            self._fail(e, result, chapter_num, callbacks)
        return result

    def run_batch(
        self,
        premises: list[str],
        callbacks: PipelineCallbacks | None = None,
        start_from: int = 1,
        chapter_nums: list[int] | None = None,
    ) -> list[ChapterResult]:
        """
        Roda os capítulos em sequência e para no primeiro erro.

        `chapter_nums` traz o número real de cada premissa. Sem ele, os números são
        consecutivos a partir de `start_from`, o que só é seguro quando os capítulos
        pendentes não têm buracos nem capítulos prontos no meio.
        """
        if callbacks is None:
            callbacks = PipelineCallbacks()

        if chapter_nums is None:
            chapter_nums = [start_from + i for i in range(len(premises))]
        if len(chapter_nums) != len(premises):
            raise ValueError("chapter_nums e premises precisam ter o mesmo tamanho.")

        results: list[ChapterResult] = []
        total = len(premises)
        callbacks.on_status(f"Iniciando pipeline em lote: {total} capítulo(s).")

        for i, (chapter_num, premise) in enumerate(zip(chapter_nums, premises)):
            callbacks.on_status(f"═══ Capítulo {chapter_num:02d} ({i + 1}/{total}) ═══")
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
