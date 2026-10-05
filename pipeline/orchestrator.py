"""
orchestrator.py — Orquestração do pipeline de geração e refinamento.

Coordena as fases:
0 Premissa no idioma da história, cânone do capítulo e itens por cena
→ 1 Drafting (versões por cena, conferência, reescrita da cena que falha)
→ 2 Refining (revisão com citação, polimento de linha)
→ 3 Summarizing → 5 Conferência final (trava a memória se o capítulo saiu do plano)
→ 3.5 Memória por mudanças → 3.6 Compress (se necessário) → 4 Merging
"""

import hashlib
import json
import logging
import random
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pipeline import config
from pipeline import qa
from pipeline.project import StoryProject
from pipeline.api import (generate_text, GenerationInterrupted, OllamaError, clear_last, last_stopped_at_context,
                          estimate_tokens)
from pipeline.io_utils import write_file, save_fragment
from pipeline.prompts import (
    SYSTEM_DRAFTING,
    SYSTEM_REFINING,
    SYSTEM_REVISE,
    SYSTEM_SUMMARIZING,
    SYSTEM_UPDATING,
    SYSTEM_COMPRESS_MEMORY,
    SYSTEM_MERGING,
    SYSTEM_CONSISTENCY,
    SYSTEM_QA_JUDGE,
    SYSTEM_QA_PAIRWISE,
    SYSTEM_PREMISE_TRANSLATE,
    SYSTEM_SCENE_PLANNER,
    build_refining_prompt,
    build_revise_prompt,
    build_summarizing_prompt,
    build_updating_prompt,
    build_compress_memory_prompt,
    build_merging_prompt,
    build_consistency_prompt,
    build_judge_prompt,
    build_pairwise_prompt,
    build_scene_prompt,
    build_planner_prompt,
    chapter_outline,
    end_moment,
    parse_updating_output,
    scene_guidance,
    scene_title,
    strip_summary_checks,
)
from pipeline.akashic_schema import read_meta
from pipeline.canon import chapter_canon, editor_brief, glossary_block, parse_glossary, mentions
from pipeline.languages import (english_name, guess_language, chapter_heading, notes_language, notes_rule,
                                state_rule, story_language, story_rule)
from pipeline.memory_ops import apply_ops, diff_report, parse_ops, roster_blocks, roster_for_chapter
from pipeline.premise import chapter_guidance
from pipeline.premise import from_text as premise_from_text
from pipeline.chapters import (
    VERSIONED_FILES,
    archive_version,
    later_done,
    load_snapshot,
    update_info,
)
from pipeline.akashic import extract_style_block, spellings_for
from pipeline.scenes import (
    Scene,
    assemble_chapter,
    clean_scene,
    count_words,
    drop_meta_lines,
    drop_new_system_lines,
    drop_overlap,
    fix_capitalized_names,
    garbled_acronym,
    garbled_names,
    last_prose_sentence,
    new_proper_nouns,
    ngrams_of,
    official_names,
    paragraphs,
    parse_premise,
    refine_problems,
    remove_repeated_sentences,
    remove_repetition,
    revert_paragraphs,
    similarity,
    split_chapter,
    split_long_paragraphs,
    tail_words,
    trim_after_hook,
    trim_incomplete_ending,
    trim_next_scene_leak,
    voice_samples,
)

logger = logging.getLogger(__name__)

_MIDDLE_NOTE = (
    "Refeito depois de capítulos posteriores: só o resumo foi trocado. Memória, roster e "
    "threads continuam com fatos da versão anterior, porque os capítulos seguintes foram "
    "escritos em cima deles."
)
_QA_NOTE = (
    "A conferência final achou problemas (veja a aba Verificações). A memória da história não foi "
    "atualizada com este capítulo: corrija o texto ou refaça o capítulo e use 'Atualizar memória'."
)
# Pedidos de comando de terminal na descrição da cena ("> status", "digita o comando").
_TERMINAL = re.compile(r"(?:^|\s)>\s?\w|\bcommand|\bcomando|\bterminal|\bprompt\b", re.I)


@dataclass
class ChapterResult:
    chapter_num: int
    premise: str
    draft: str = ""
    final: str = ""
    summary: str = ""
    status: str = "pending"  # pending, drafting, polishing, summarizing, done, qa_failed, error
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


@dataclass
class ChapterContext:
    """O que o capítulo inteiro usa: premissa no idioma da história, cânone recortado, itens por cena."""
    premise: str
    form: Any
    canon: Any
    checklist: qa.Checklist
    introduced: list[str]
    not_introduced: list[str]
    names: list[str]
    leak: list[str]
    allowed_text: str


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
        # A história e tudo o que o modelo lê de novo (resumos, memória, roster) vão no idioma da
        # história; o que só a pessoa lê (relatórios) vai no idioma dela.
        self.story_code = story_language(project)
        self.story_rule = story_rule(self.story_code)
        self.state_rule = state_rule(self.story_code)
        self.notes_rule = notes_rule(notes_language())

        self.drafting_extra = {"num_gpu": config.DRAFTING_NUM_GPU} if config.DRAFTING_NUM_GPU is not None else {}
        self.refining_extra = {"num_gpu": config.REFINING_NUM_GPU} if config.REFINING_NUM_GPU is not None else {}
        self.summarizing_extra = {"num_gpu": config.SUMMARIZING_NUM_GPU} if config.SUMMARIZING_NUM_GPU is not None else {}
        self._ctx: dict[int, ChapterContext] = {}
        self._qa_notes: dict[int, list[str]] = {}
        # (PLAN CHECK, OFF-PLAN) do último resumo: vão para a conferência final.
        self._summary_checks: tuple[str, str] = ("", "")

    def _check_cancelled(self):
        if self.cancel_event.is_set():
            raise GenerationInterrupted("", "Pipeline cancelado pelo usuário.")

    # ─── Dados do registro ──────────────────────────────────

    def _meta(self):
        path = self.project.akashic_path
        if not path.exists():
            return None
        meta, _ = read_meta(path.read_text(encoding="utf-8"))
        return meta

    def _character_sheets(self) -> dict[str, str]:
        """Nome → ficha para o modelo, dos metadados do Registro Akáshico."""
        meta = self._meta()
        return {c.name: c.sheet.strip() for c in meta.characters} if meta else {}

    def _location_sheets(self) -> tuple[dict[str, str], list[str], dict[str, str]]:
        """(nome → ficha dos locais, nomes "sempre no contexto", nome → local que o contém)."""
        meta = self._meta()
        if not meta:
            return {}, [], {}
        sheets = {loc.name: loc.model_sheet.strip() for loc in meta.locations if loc.model_sheet.strip()}
        always = [loc.name for loc in meta.locations if loc.always and loc.model_sheet.strip()]
        parents = {loc.name: loc.parent for loc in meta.locations if loc.parent}
        return sheets, always, parents

    def _protagonists(self) -> list:
        meta = self._meta()
        return [c for c in meta.characters if c.role == "protagonist"] if meta else []

    def _protagonist_voice(self) -> str:
        """
        Voz do(s) protagonista(s): o campo Voz do registro; sem ele, a nota de voz do roster (que
        vem do que ele fez nos capítulos); sem ela, a ficha.
        """
        heroes = self._protagonists()[:2]
        if not heroes:
            return ""
        roster = {b["name"].lower(): b for b in roster_blocks(self.project.character_roster)}
        out = []
        for c in heroes:
            block = roster.get(c.name.lower()) or next(
                (b for k, b in roster.items() if k.split()[0] == c.name.lower().split()[0]), None)
            note = block["fields"].get("voice", "") if block else ""
            voice = c.voice.strip() or note.strip() or c.sheet.strip()
            if voice:
                out.append(f"{c.name}: {voice}")
        return " ".join(out)

    def _voice_samples(self, chapter_num: int) -> list[str]:
        heroes = self._protagonists()
        if not heroes:
            return []
        texts = [e.final for e in sorted(self.project.scan_chapters(), key=lambda e: -e.num)
                 if e.num < chapter_num and e.final]
        return voice_samples(texts, heroes[0].name)

    def _glossary(self) -> list[tuple[str, str]]:
        full = self.project.akashic_path.read_text(encoding="utf-8") if self.project.akashic_path.exists() else ""
        return parse_glossary(full, pairs_anywhere=False) + parse_glossary(self.akashic_records, pairs_anywhere=False)

    # ─── Contexto do capítulo ───────────────────────────────

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

    def _premise_in_story_language(self, premise: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        """
        A premissa é escrita no idioma do usuário; o modelo que escreve em outro idioma erra
        tradução no meio da cena ("senha" vira "password"). Traduz uma vez por capítulo, com o
        glossário da história e o capítulo anterior como contexto, e guarda em premissa_modelo.md,
        que o usuário pode corrigir à mão (a correção vale enquanto a premissa não mudar).
        """
        guess = guess_language(premise)
        if guess is None or guess == self.story_code:
            return premise
        cache = self.project.chapter_dir(chapter_num) / "premissa_modelo.md"
        key = hashlib.sha1(premise.encode("utf-8")).hexdigest()[:12]
        if cache.exists():
            cached = cache.read_text(encoding="utf-8")
            if cached.startswith(f"<!-- {key} -->\n"):
                return cached.split("\n", 1)[1]
        callbacks.on_status(f"Capítulo {chapter_num:02d} — Traduzindo a premissa para o idioma da história")
        parts = []
        gloss = glossary_block(self._glossary(), premise)
        if gloss:
            parts.append(f"=== GLOSSARY (this story's own terms) ===\n{gloss}")
        prev = [t for n, t in self.project.accumulated_summaries if n == chapter_num - 1]
        tail = self._previous_chapter_tail(chapter_num)
        if prev or tail:
            parts.append("=== PREVIOUS CHAPTER (context only; do not translate) ===\n"
                         + "\n\n".join(filter(None, [prev[0] if prev else "", tail])))
        parts.append(f"=== BRIEF TO TRANSLATE ===\n{premise.strip()}")
        out = generate_text(
            model=self.model_refining, provider=self.provider_refining,
            system_prompt=SYSTEM_PREMISE_TRANSLATE.format(language=english_name(self.story_code)),
            user_prompt="\n\n".join(parts), temperature=0.1, num_ctx=self.refining_num_ctx,
            timeout=self.request_timeout, cancel_event=self.cancel_event,
            extra_options=dict(self.refining_extra, num_predict=int(count_words(premise) * 2.5) + 300),
        ).strip()
        out = _official_names(_drop_invented_counts(out, premise), self._glossary())
        if not out or len(parse_premise(out).scenes) != len(parse_premise(premise).scenes):
            logger.warning(f"Cap {chapter_num:02d} — tradução da premissa perdeu as cenas; usando a original")
            return premise
        write_file(cache, f"<!-- {key} -->\n{out}")
        return out

    def _chapter_context(self, premise: str, chapter_num: int, callbacks: PipelineCallbacks) -> ChapterContext:
        """Prepara (uma vez por capítulo) a premissa traduzida, o cânone recortado e os itens por cena."""
        if chapter_num in self._ctx and self._ctx[chapter_num].premise:
            return self._ctx[chapter_num]
        from pipeline.cast import cast_status
        premise_en = self._premise_in_story_language(premise, chapter_num, callbacks)
        form = premise_from_text(premise_en)
        meta = self._meta()
        introduced, not_introduced = cast_status(self.project, chapter_num, meta)
        context = "\n".join([premise_en, self.project.character_roster, self.project.open_threads,
                             self._previous_chapter_tail(chapter_num)])
        canon = chapter_canon(self.akashic_records, meta, chapter_num, form.characters, introduced,
                              not_introduced, context, form.locations)
        write_file(self.project.chapter_dir(chapter_num) / "canon_modelo.md", canon.text)
        names = official_names(self.akashic_records)
        plan = parse_premise(premise_en)
        checklist = self._checklist(premise_en, form, [s.text for s in plan.scenes], names, canon.hidden, chapter_num)
        glossary = canon.glossary + self._glossary()
        # O que a premissa permite (sem o "não pode aparecer"): vale para o guarda do polimento.
        allowed_text = "\n".join([form.goal, form.opening, form.hook, form.must_include,
                                  ", ".join(form.characters), ", ".join(form.locations)]
                                 + [s["text"] for s in form.scenes])
        ctx = ChapterContext(premise_en, form, canon, checklist, introduced, not_introduced, names,
                             qa.leak_terms(glossary, self.story_code), allowed_text)
        self._ctx[chapter_num] = ctx
        return ctx

    def _checklist(self, premise_en: str, form, scene_texts: list[str], names: list[str], hidden: list[str],
                   chapter_num: int) -> qa.Checklist:
        """
        Itens obrigatórios por cena e nomes proibidos, guardados em checklist.json. O usuário pode
        corrigir o arquivo; a versão dele vale enquanto a premissa não mudar.
        """
        path = self.project.chapter_dir(chapter_num) / "checklist.json"
        key = hashlib.sha1(premise_en.encode("utf-8")).hexdigest()[:12]
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("premise_hash") == key:
                    return qa.Checklist.from_dict(data)
            except (ValueError, OSError):
                pass
        cl = qa.build_checklist(form.must_include, form.must_not, scene_texts, names, hidden, form.characters)
        write_file(path, json.dumps({"premise_hash": key, **cl.to_dict()}, ensure_ascii=False, indent=2))
        return cl

    def _plan_scenes(self, premise: str, chapter_num: int, callbacks: PipelineCallbacks, canon_text: str = ""):
        """Cenas da premissa. Sem cenas numeradas, o próprio modelo divide a premissa em cenas."""
        plan = parse_premise(premise)
        if plan.scenes:
            return plan
        callbacks.on_status(f"Capítulo {chapter_num:02d} — Dividindo a premissa em cenas")
        raw = generate_text(
            model=self.model_drafting,
            provider=self.provider_drafting,
            system_prompt=SYSTEM_SCENE_PLANNER,
            user_prompt=f"{build_planner_prompt(premise, canon_text or self.akashic_records)}\n\n{self.story_rule}",
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

    def _memory_for_prompt(self, ctx: ChapterContext, fixed_tokens: int) -> dict:
        """
        Memória, roster, threads e resumos para o prompt de cena, dentro do orçamento de tokens.
        Roster: ficha inteira só de quem o capítulo usa. Resumos: os dois últimos inteiros, os
        anteriores só com os eventos. Acima do orçamento, sai primeiro o que importa menos.
        """
        keep = list(ctx.form.characters) + [ctx.premise, self.project.open_threads]
        keep += [c.name for c in self._protagonists()]
        mem = {
            "dynamic_memory": self.project.dynamic_memory,
            "character_roster": roster_for_chapter(self.project.character_roster, keep),
            "open_threads": self.project.open_threads,
            "callbacks": self.project.callbacks,
            "story_so_far": self.project.story_so_far,
            "recent_summaries": _shorten_old_summaries(self.project.accumulated_summaries),
        }
        budget = config.PROMPT_BUDGET_TOKENS - fixed_tokens

        def size() -> int:
            return estimate_tokens("\n".join(
                str(v) if not isinstance(v, list) else "\n".join(t for _, t in v) for v in mem.values()))

        trims = [
            ("recent_summaries", lambda: mem.update(recent_summaries=mem["recent_summaries"][-2:])),
            ("story_so_far", lambda: mem.update(story_so_far=tail_words(mem["story_so_far"], 400))),
            ("callbacks", lambda: mem.update(callbacks="\n".join(mem["callbacks"].splitlines()[-8:]))),
            ("open_threads", lambda: mem.update(open_threads="\n".join(mem["open_threads"].splitlines()[:8]))),
            ("character_roster", lambda: mem.update(character_roster=mem["character_roster"].split(
                "\n\nOthers (not in this chapter):")[0])),
            ("dynamic_memory", lambda: mem.update(dynamic_memory="\n".join(mem["dynamic_memory"].splitlines()[:20]))),
            ("recent_summaries", lambda: mem.update(recent_summaries=mem["recent_summaries"][-1:])),
        ]
        cut = []
        for name, trim in trims:
            if size() <= budget:
                break
            trim()
            cut.append(name)
        if cut:
            logger.warning(f"Prompt acima do orçamento ({config.PROMPT_BUDGET_TOKENS} tokens): reduzido {', '.join(cut)}")
        return mem

    # ─── Geração de uma cena ────────────────────────────────

    def _generate_scene_text(self, user_prompt: str, target_words: int, callbacks: PipelineCallbacks,
                             stream: bool = True, seed: int | None = None) -> str:
        options: dict[str, Any] = dict(self.drafting_extra)
        # Teto de ~1,4x a meta: acima disso o modelo pequeno costuma entrar em laço ou invadir a próxima cena.
        options["num_predict"] = int(target_words * 1.33 * 1.4) + 150
        options["repeat_penalty"] = config.DRAFTING_REPEAT_PENALTY
        # O padrão do Ollama olha só os últimos 64 tokens, curto demais para pegar frases repetidas.
        options["repeat_last_n"] = config.DRAFTING_REPEAT_LAST_N
        if seed is not None:
            options["seed"] = seed
        clear_last()
        return generate_text(
            model=self.model_drafting,
            provider=self.provider_drafting,
            system_prompt=SYSTEM_DRAFTING,
            user_prompt=f"{user_prompt}\n\n{self.story_rule}",
            temperature=self.drafting_temperature,
            num_ctx=self.drafting_num_ctx,
            timeout=self.request_timeout,
            on_token=(lambda token: callbacks.on_token("drafting", token)) if stream else (lambda token: None),
            cancel_event=self.cancel_event,
            extra_options=options,
        )

    @staticmethod
    def _clean_generated(raw: str, seen: set, previous_text: str) -> str:
        """Limpeza determinística do que o modelo escreveu: título solto, frase cortada, laços e recomeços."""
        text = trim_incomplete_ending(clean_scene(raw))
        text = remove_repetition(text)
        if previous_text.strip():
            text = drop_overlap(text, previous_text)
        return split_long_paragraphs(remove_repeated_sentences(text, seen))

    def _draft_once(self, prompt_for, target: int, seen: set, previous: str, callbacks: PipelineCallbacks,
                    stream: bool, seed: int | None, label: str, min_ratio: float | None = None) -> str:
        """
        Uma versão da cena, com pedidos de continuação se parar curta (mas não se o contexto encheu).
        `min_ratio`: fração da meta abaixo da qual a cena é continuada (mais alta quando o capítulo
        está atrás do mínimo de palavras).
        """
        min_ratio = config.SCENE_MIN_RATIO if min_ratio is None else min_ratio
        raw = self._generate_scene_text(prompt_for(None, target), target, callbacks, stream, seed)
        text = self._clean_generated(raw, seen, previous)
        goal = int(target * min_ratio)
        for _ in range(config.SCENE_MAX_CONTINUATIONS):
            if count_words(text) >= goal:
                break
            if last_stopped_at_context():
                logger.warning(f"{label}: parou no limite do contexto; sem pedido de continuação")
                break
            missing = max(target, goal) - count_words(text)
            callbacks.on_status(f"{label} curta ({count_words(text)} palavras); continuando")
            if stream:
                callbacks.on_token("drafting", "\n\n")
            raw = self._generate_scene_text(prompt_for(text, missing), missing, callbacks, stream, seed)
            more = self._clean_generated(raw, seen, f"{previous}\n\n{text}")
            if count_words(more) < 40:
                break
            text = f"{text}\n\n{more}"
        return text

    def _judge_scene(self, text: str, items: list[tuple[int, str]], must_not: str) -> tuple[list[int], list[qa.Issue]]:
        """Juiz da cena: (índices do checklist que faltam, violações com citação conferida)."""
        if not items and not must_not.strip():
            return [], []
        raw = generate_text(
            model=self.model_refining, provider=self.provider_refining, system_prompt=SYSTEM_QA_JUDGE,
            user_prompt=build_judge_prompt(text, [it for _, it in items], must_not),
            temperature=0.0, num_ctx=self.refining_num_ctx, timeout=self.request_timeout,
            cancel_event=self.cancel_event, extra_options=dict(self.refining_extra, num_predict=400),
            json_output=True,
        )
        missing, violations = qa.parse_judge(raw, text, len(items))
        return [items[k][0] for k in missing], violations

    def _pairwise(self, plan: str, before: str, a: str, b: str) -> str:
        """'A' ou 'B' quando o juiz escolhe a mesma versão nas duas ordens; '' se ele se contradiz."""
        votes = []
        for first, second in ((a, b), (b, a)):
            raw = generate_text(
                model=self.model_refining, provider=self.provider_refining, system_prompt=SYSTEM_QA_PAIRWISE,
                user_prompt=build_pairwise_prompt(plan, tail_words(before, 150), first, second),
                temperature=0.0, num_ctx=self.refining_num_ctx, timeout=self.request_timeout,
                cancel_event=self.cancel_event, extra_options=dict(self.refining_extra, num_predict=120),
                json_output=True,
            )
            votes.append(qa.parse_pairwise(raw))
        flipped = {"A": "B", "B": "A"}.get(votes[1], "")
        return votes[0] if votes[0] and votes[0] == flipped else ""

    # ─── Rascunho cena por cena ─────────────────────────────

    def _run_drafting(self, premise: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        ctx = self._chapter_context(premise, chapter_num, callbacks)
        premise = ctx.premise
        form = ctx.form
        plan = self._plan_scenes(premise, chapter_num, callbacks, ctx.canon.text)
        scenes = plan.scenes
        total = len(scenes)
        scene_texts = [s.text for s in scenes]
        if not parse_premise(premise).scenes and ctx.checklist.items:
            # Cenas vieram do planejador: os itens são distribuídos pelas cenas dele.
            ctx.checklist = qa.build_checklist(form.must_include, form.must_not, scene_texts, ctx.names,
                                               ctx.canon.hidden, form.characters)
        default_target = max(250, -(-config.CHAPTER_TARGET_WORDS // total))
        title = chapter_heading(plan.title, chapter_num, self.story_code)
        prev_tail = self._previous_chapter_tail(chapter_num)
        next_opening = self._next_chapter_opening(chapter_num)
        voice = self._protagonist_voice()
        samples = self._voice_samples(chapter_num)
        # Fichas no fim do prompt só de quem não tem ficha no cânone do capítulo.
        sheets = {n: s for n, s in self._character_sheets().items() if n not in ctx.canon.cast}
        places, always_places, parents = self._location_sheets()
        fixed = estimate_tokens(ctx.canon.text) + int(config.CHAPTER_SO_FAR_TAIL_WORDS * 1.4) + 2500
        memory = self._memory_for_prompt(ctx, fixed)
        common: dict[str, Any] = dict(
            akashic_records=ctx.canon.text or self.akashic_records,
            chapter_num=chapter_num,
            scene_total=total,
            hook=plan.hook,
            previous_chapter_tail=prev_tail,
            next_chapter_opening=next_opening,
            **memory,
        )
        # Sequências de palavras já usadas (inclusive no fim do capítulo anterior e nas falas de
        # exemplo): frases que as repetem são laço, recomeço ou cópia, e saem do texto.
        seen = ngrams_of(prev_tail) | ngrams_of("\n\n".join(samples))
        hook_end = end_moment(plan.hook) or plan.hook.strip()
        qa_notes: list[str] = []

        written: list[str] = []
        callbacks.on_token("drafting", title + "\n\n")
        for i, scene in enumerate(scenes, start=1):
            self._check_cancelled()
            target = scene.target_words or default_target
            next_brief = scenes[i].text if i < total else ""
            if i > 1:
                callbacks.on_token("drafting", f"\n\n{config.SCENE_BREAK}\n\n")
            chapter_text = "\n\n".join(written)
            so_far = tail_words(chapter_text, config.CHAPTER_SO_FAR_TAIL_WORDS)
            items_here = ctx.checklist.for_scene(i)
            if i == total:
                # Itens do capítulo sem cena certa: vão para a última, se ainda não apareceram.
                loose = [(k, it) for k, (it, s) in enumerate(zip(ctx.checklist.items, ctx.checklist.scene_of))
                         if s == 0 and qa.coverage(it, chapter_text) < 0.5]
                items_here = items_here + loose
            system_left = ctx.checklist.max_system_lines - qa.system_lines(chapter_text)

            def guidance(scene_so_far: str, scene=scene, chapter_text=chapter_text, items_here=items_here,
                         system_left=system_left, next_brief=next_brief, i=i) -> str:
                """Fim do prompt; na continuação, a última frase é a da própria cena e a abertura já passou."""
                written_here = qa.system_lines(scene_so_far)
                return "\n".join(filter(None, [
                    scene_guidance(scene_title(next_brief) if next_brief else "",
                                   last_prose_sentence(scene_so_far or chapter_text or prev_tail), voice, samples,
                                   system_left - written_here, bool(_TERMINAL.search(scene.text))),
                    chapter_guidance(form, sheets, first_scene=not (chapter_text or scene_so_far), places=places,
                                     always=always_places, scene_text=scene.text, has_previous=bool(prev_tail),
                                     must_here=[it for _, it in items_here],
                                     done_before=ctx.checklist.before_scene(i), parents=parents),
                ]))
            outline = chapter_outline(form.goal, scene_texts, i, plan.hook)
            label = f"Capítulo {chapter_num:02d} — Cena {i}/{total}"
            # CHAPTER_TARGET_WORDS é o mínimo do capítulo: se as cenas anteriores ficaram curtas, esta
            # é continuada até o capítulo poder chegar lá (as já escritas não são mexidas).
            remaining_min = config.CHAPTER_TARGET_WORDS - count_words(chapter_text)
            remaining_targets = sum(s.target_words or default_target for s in scenes[i - 1:])
            # Até o dobro da meta da cena: a última cena não vira o capítulo inteiro.
            min_ratio = max(config.SCENE_MIN_RATIO, min(2.0, remaining_min / max(remaining_targets, 1)))
            text = ""
            try:
                callbacks.on_status(f"{label}: rascunho, ~{target} palavras ({self.model_drafting})")
                text, seen, notes = self._draft_scene(
                    common, outline, scene.text, i, target, so_far, guidance, items_here, ctx, seen,
                    chapter_text or prev_tail, system_left, callbacks, label, min_ratio,
                )
                qa_notes += [f"Cena {i}: {n}" for n in notes]
            except GenerationInterrupted as e:
                partial = assemble_chapter(title, written + [f"{text}\n\n{e.fragment}".strip()], config.SCENE_BREAK)
                raise GenerationInterrupted(partial, str(e)) from e
            if next_brief:
                text = trim_next_scene_leak(text, next_brief, int(target * config.SCENE_MIN_RATIO))
            elif next_opening and plan.hook:
                text = trim_after_hook(text, hook_end)
            logger.info(f"Cap {chapter_num:02d} — cena {i}/{total}: {count_words(text)} palavras (meta {target})")
            written.append(text)

        total_words = sum(count_words(w) for w in written)
        if total_words < config.CHAPTER_TARGET_WORDS:
            qa_notes.append(f"Capítulo com {total_words} palavras, abaixo do mínimo de {config.CHAPTER_TARGET_WORDS}")
        self._qa_notes[chapter_num] = qa_notes
        draft = assemble_chapter(title, written, config.SCENE_BREAK)
        chapter_dir = self.project.chapter_dir(chapter_num)
        write_file(chapter_dir / "rascunho.md", draft)
        logger.info(f"Cap {chapter_num:02d} — Rascunho salvo ({count_words(draft)} palavras, {total} cenas)")
        callbacks.on_phase_complete("drafting", draft, chapter_num)
        return draft

    def _draft_scene(self, common: dict, outline: str, scene_text: str, num: int, target: int, so_far: str,
                     guidance: Callable[[str], str], items: list[tuple[int, str]], ctx: ChapterContext, seen: set,
                     previous: str, system_left: int, callbacks: PipelineCallbacks,
                     label: str, min_ratio: float | None = None) -> tuple[str, set, list[str]]:
        """
        Escreve a cena: N versões com o mesmo prompt (o Ollama reaproveita o prompt em cache),
        nota automática, juiz de pares entre as duas melhores e conferência da premissa. A cena
        que falha é escrita de novo com a correção no fim do prompt, até QA_SCENE_RETRIES vezes.
        Retorna (texto, n-gramas vistos atualizados, problemas que sobraram).
        """
        must_not = ctx.form.must_not
        best: tuple | None = None
        corrective = ""
        attempts = 1 + max(0, config.QA_SCENE_RETRIES)
        for attempt in range(attempts):
            def prompt_for(scene_so_far, words, extra=corrective):
                return build_scene_prompt(scene_num=num, scene_text=scene_text, target_words=words, outline=outline,
                                          chapter_so_far=so_far, scene_so_far=scene_so_far or "",
                                          guidance=f"{guidance(scene_so_far or '')}\n{extra}".strip(), **common)
            candidates = []
            n = max(1, config.DRAFT_CANDIDATES)
            for k in range(n):
                if k or attempt:
                    callbacks.on_status(f"{label}: versão {k + 1}/{n}" + (f", tentativa {attempt + 1}" if attempt else ""))
                local_seen = set(seen)
                text = self._draft_once(prompt_for, target, local_seen, previous, callbacks,
                                        stream=(attempt == 0 and k == 0),
                                        seed=None if k == 0 else random.randint(1, 2 ** 31 - 1), label=label,
                                        min_ratio=min_ratio)
                issues = qa.scene_issues(text, previous, ctx.checklist, self.story_code, ctx.leak, system_left)
                candidates.append([qa.score(text, target, items, issues, seen), text, issues, local_seen])
            candidates.sort(key=lambda c: -c[0])
            if config.QA_JUDGE_ENABLED and len(candidates) >= 2 and not any(
                    i.hard for c in candidates[:2] for i in c[2]):
                winner = self._pairwise(f"{scene_text}\n" + "\n".join(f"- {it}" for _, it in items),
                                        previous, candidates[0][1], candidates[1][1])
                if winner == "B":
                    candidates[0], candidates[1] = candidates[1], candidates[0]
            score, text, issues, local_seen = candidates[0]
            # Sem o juiz, os itens que faltam vêm das palavras-chave: servem para a nota, não para
            # reescrever a cena (a mesma ideia com outras palavras daria falso negativo).
            missing = qa.missing_items(text, items)
            judged = False
            if config.QA_JUDGE_ENABLED and not any(i.hard for i in issues):
                missing, violations = self._judge_scene(text, items, must_not)
                issues = issues + violations
                judged = True
            hard = [i for i in issues if i.hard]
            rank = (len(hard), len(missing), -score)
            if best is None or rank < best[0]:
                best = (rank, text, local_seen, hard, missing)
            if not hard and (not judged or len(missing) <= (0 if len(items) <= 2 else 1)):
                break
            if attempt + 1 < attempts:
                callbacks.on_status(f"{label}: a conferência achou problemas; escrevendo de novo")
                corrective = _corrective(hard, [ctx.checklist.items[k] for k in missing], ctx.form.characters,
                                         english_name(self.story_code))
        _, text, local_seen, hard, missing = best
        seen.clear()
        seen |= local_seen
        notes = [str(i) for i in hard] + [f"faltou: {ctx.checklist.items[k]}" for k in missing]
        return text, seen, notes

    # ─── Polimento cena por cena ────────────────────────────

    def _run_refining(self, draft: str, chapter_num: int, callbacks: PipelineCallbacks) -> str:
        self._check_cancelled()
        title, scenes = split_chapter(draft)
        scenes = [drop_meta_lines(s) for s in scenes]
        premise_file = self.project.chapter_dir(chapter_num) / "premissa.md"
        premise = premise_file.read_text(encoding="utf-8") if premise_file.exists() else ""
        ctx = self._chapter_context(premise, chapter_num, callbacks) if premise.strip() else None
        canon_text = ctx.canon.text if ctx else self.akashic_records
        brief = editor_brief(canon_text) or extract_style_block(self.akashic_records)
        total = len(scenes)
        polished: list[str] = []
        rejected: list[str] = []
        critique: list[str] = []
        # O polimento só melhora a prosa: quem aparece na cena é o que está no rascunho e na premissa
        # (sem o "não pode aparecer"). Nomes da lista de proibidos nunca contam como permitidos.
        names = official_names(self.akashic_records)
        allowed = ctx.allowed_text if ctx else ""
        forbidden = ctx.checklist.forbidden_names if ctx else []
        plan_scenes = parse_premise(ctx.premise).scenes if ctx else []
        prev_tail = self._previous_chapter_tail(chapter_num)
        if title:
            callbacks.on_token("refining", title + "\n\n")
        for i, scene in enumerate(scenes, start=1):
            self._check_cancelled()
            if i > 1:
                callbacks.on_token("refining", f"\n\n{config.SCENE_BREAK}\n\n")
            plan_text = plan_scenes[i - 1].text if i <= len(plan_scenes) else ""
            items = ctx.checklist.for_scene(i) if ctx else []
            earlier = "\n\n".join([prev_tail] + polished)
            try:
                base = scene
                if config.REVISE_PASS_ENABLED:
                    callbacks.on_status(f"Capítulo {chapter_num:02d} — Revisão: cena {i}/{total} ({self.model_refining})")
                    base, log = self._revise_scene(scene, plan_text, items, ctx, tail_words(earlier, 120),
                                                   " ".join((scenes[i].split() if i < total else [])[:60]),
                                                   names, allowed, forbidden)
                    if log:
                        critique.append(f"## Cena {i}\n\n" + "\n".join(log))
                callbacks.on_status(f"Capítulo {chapter_num:02d} — Polimento: cena {i}/{total} ({self.model_refining})")
                out, reason = self._polish_scene(base, i, total, brief, earlier, items, names, allowed, forbidden,
                                                 callbacks)
            except GenerationInterrupted as e:
                partial = assemble_chapter(title, polished + scenes[i - 1:], config.SCENE_BREAK)
                raise GenerationInterrupted(partial, str(e)) from e
            if reason:
                before, after = count_words(base), count_words(out)
                logger.warning(
                    f"Cap {chapter_num:02d} — polimento da cena {i} descartado: {reason}; mantendo o texto revisado"
                )
                callbacks.on_status(f"Capítulo {chapter_num:02d} — cena {i}: polimento descartado ({reason})")
                rejected.append(f"## Cena {i} ({before} → {after} palavras)\n\nMotivo: {reason}\n\n{out}")
                out = base
            polished.append(out)

        final = assemble_chapter(title, polished, config.SCENE_BREAK)
        chapter_dir = self.project.chapter_dir(chapter_num)
        if rejected:
            # Guardado para comparação: o capítulo final usa o rascunho (revisado) dessas cenas.
            write_file(chapter_dir / "polimento_descartado.md", "\n\n".join(rejected))
        if critique:
            write_file(chapter_dir / "critica.md", "# Revisão antes do polimento\n\n" + "\n\n".join(critique))
        write_file(chapter_dir / "capitulo_final.md", final)
        logger.info(f"Cap {chapter_num:02d} — Capítulo final salvo ({count_words(final)} palavras)")
        callbacks.on_phase_complete("refining", final, chapter_num)
        return final

    def _revise_scene(self, scene: str, plan_text: str, items: list[tuple[int, str]], ctx: ChapterContext | None,
                      previous: str, following: str, names: list[str], allowed: str,
                      forbidden: list[str]) -> tuple[str, list[str]]:
        """
        Revisão com citação: o modelo aponta defeitos (contradição, repetição, recontagem, erro de
        sentido, proibido, cânone) com o trecho exato e a correção; o código troca só esses
        trechos. Se o resultado não passar nas travas, fica a cena como estava.
        """
        plan = "\n".join(filter(None, [plan_text] + [f"- {it}" for _, it in items]))
        raw = generate_text(
            model=self.model_refining, provider=self.provider_refining, system_prompt=SYSTEM_REVISE,
            user_prompt=build_revise_prompt(scene, plan, ctx.form.must_not if ctx else "",
                                            _canon_core(ctx.canon.text) if ctx else "", previous, following)
            + f"\n\n{self.story_rule}",
            temperature=0.2, num_ctx=self.refining_num_ctx, timeout=self.request_timeout,
            cancel_event=self.cancel_event, extra_options=dict(self.refining_extra, num_predict=900),
            json_output=True,
        )
        edits = qa.parse_edits(raw, scene)
        if not edits:
            return scene, []
        revised, applied = qa.apply_edits(scene, edits)
        problems = refine_problems(revised, scene, allowed, names, 1.15, forbidden)
        if count_words(revised) < count_words(scene) * 0.8:
            problems.append("cortou demais")
        log = [f"- **{e['kind']}** \"{e['quote']}\" → \"{e['replacement']}\" ({e['reason']})" for e in applied]
        if problems:
            log.append(f"Revisão descartada: {'; '.join(problems)}")
            return scene, log
        return revised, log

    def _polish_scene(self, scene: str, num: int, total: int, brief: str, earlier: str,
                      items: list[tuple[int, str]], names: list[str], allowed: str, forbidden: list[str],
                      callbacks: PipelineCallbacks) -> tuple[str, str]:
        """
        Polimento de linha com a lista de trechos a reescrever e a voz de quem está na cena.
        Problema localizado (nome proibido, gente inventada, [System] perdido) volta só o parágrafo
        do rascunho; problema geral descarta o polimento. Retorna (texto, motivo do descarte).
        """
        fix_list = qa.polish_targets(scene, earlier, [it for _, it in items])
        voice = self._scene_voices(scene)
        best: tuple | None = None
        tries = max(1, config.REFINE_CANDIDATES)
        temperature = self.refining_temperature
        k = 0
        retried_noop = False
        while k < tries:
            options: dict[str, Any] = dict(self.refining_extra)
            options["num_predict"] = int(count_words(scene) * 1.33 * 1.6) + 200
            if k:
                options["seed"] = random.randint(1, 2 ** 31 - 1)
            out = generate_text(
                model=self.model_refining,
                provider=self.provider_refining,
                system_prompt=SYSTEM_REFINING,
                user_prompt=build_refining_prompt(scene, style_block=spellings_for(brief, scene),
                                                  scene_label=f"scene {num} of {total} of a chapter",
                                                  voice=voice, fix_list=fix_list)
                + f"\n\n{self.story_rule}",
                temperature=temperature,
                num_ctx=self.refining_num_ctx,
                timeout=self.request_timeout,
                on_token=(lambda token: callbacks.on_token("refining", token)) if k == 0 else (lambda token: None),
                cancel_event=self.cancel_event,
                extra_options=options,
            )
            out = split_long_paragraphs(drop_new_system_lines(clean_scene(out), scene))
            out, fixed = fix_capitalized_names(out, scene, names)
            if fixed:
                logger.info(f"Cena {num}: mudou maiúscula de volta ({', '.join(fixed)})")
            out, problems = self._guard_polish(out, scene, allowed, names, forbidden)
            # Polimento que não mudou quase nada (e havia o que corrigir): mais uma tentativa, mais solta.
            if not problems and fix_list and similarity(out, scene) > 0.97 and not retried_noop:
                retried_noop = True
                temperature = min(1.0, temperature + 0.2)
                logger.info(f"Cena {num}: polimento quase igual ao rascunho; tentando de novo")
                continue
            left = len(qa.polish_targets(out, earlier, [it for _, it in items]))
            rank = (len(problems), qa.cliche_count(out) + left, -similarity(out, scene))
            if best is None or rank < best[0]:
                best = (rank, out, problems)
            k += 1
        _, out, problems = best
        return out, "; ".join(problems)

    def _guard_polish(self, out: str, scene: str, allowed: str, names: list[str],
                      forbidden: list[str]) -> tuple[str, list[str]]:
        """Travas do polimento, com conserto parágrafo a parágrafo do que é localizado."""
        problems = refine_problems(out, scene, allowed, names, config.REFINE_MAX_RATIO, forbidden)
        before, after = count_words(scene), count_words(out)
        if after < before * config.REFINE_MIN_RATIO:
            problems.insert(0, f"encolheu ({before} → {after} palavras)")
        local = ("trouxe", "inventou", "perdeu")
        if problems and all(p.startswith(local) for p in problems):
            source = f"{scene}\n{allowed}"

            def bad(p: str) -> bool:
                return any(mentions(n, p) and not mentions(n, scene) for n in forbidden) \
                    or any(mentions(n, p) and not mentions(n, source) for n in names) \
                    or len(new_proper_nouns(p, source)) >= 2 \
                    or bool(garbled_names(p, names, source)) \
                    or bool(garbled_acronym(p, names))

            fixed, changed = revert_paragraphs(out, scene, bad)
            if changed > 0:
                again = refine_problems(fixed, scene, allowed, names, config.REFINE_MAX_RATIO, forbidden)
                if not again:
                    logger.info(f"Polimento consertado: {changed} parágrafo(s) voltaram ao rascunho ({'; '.join(problems)})")
                    return fixed, []
        return out, problems

    def _scene_voices(self, scene: str) -> str:
        """Voz (ou ficha) de cada personagem com ficha que aparece na cena."""
        meta = self._meta()
        if not meta:
            return ""
        out = []
        for c in meta.characters:
            if mentions(c.name, scene):
                voice = c.voice.strip() or c.sheet.strip()
                if voice:
                    out.append(f"{c.name}: {voice}")
        return "\n".join(out[:4])

    # ─── Resumo, conferência e memória ──────────────────────

    def _run_summarizing(
        self, final_text: str, chapter_num: int, callbacks: PipelineCallbacks, record: bool = True
    ) -> str:
        self._check_cancelled()
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 3: Resumo ({self.model_summarizing})"
        )
        ctx = self._ctx.get(chapter_num)
        premise_file = self.project.chapter_dir(chapter_num) / "premissa.md"
        if ctx is None and premise_file.exists() and premise_file.read_text(encoding="utf-8").strip():
            # Atualizar a memória de um capítulo pronto: o plano vem da premissa gravada.
            ctx = self._chapter_context(premise_file.read_text(encoding="utf-8"), chapter_num, callbacks)
        beats, allowed, spellings = [], [], ""
        if ctx:
            beats = [s.text for s in parse_premise(ctx.premise).scenes] + ctx.checklist.items
            allowed = list(dict.fromkeys(ctx.form.characters + ctx.introduced))
            spellings = "\n".join(l for l in ctx.canon.text.splitlines() if ";" in l and ":" in l)
        user_prompt = build_summarizing_prompt(final_text, chapter_num, beats, allowed, spellings)
        raw = ""
        for attempt in range(2):
            raw = generate_text(
                model=self.model_summarizing,
                provider=self.provider_summarizing,
                system_prompt=SYSTEM_SUMMARIZING,
                user_prompt=f"{user_prompt}\n\n{self.state_rule}",
                temperature=self.summarizing_temperature,
                num_ctx=self.summarizing_num_ctx,
                timeout=self.request_timeout,
                on_token=lambda token: callbacks.on_token("summarizing", token),
                cancel_event=self.cancel_event,
                extra_options=self.summarizing_extra or None,
            )
            summary, _, _ = strip_summary_checks(raw)
            if count_words(summary) <= config.SUMMARY_MAX_WORDS * 1.3 or attempt:
                break
            logger.info(f"Cap {chapter_num:02d} — resumo longo ({count_words(summary)} palavras); pedindo de novo")
            user_prompt += f"\n\nYour last answer was too long. Stay under {config.SUMMARY_MAX_WORDS} words."
        summary, plan_check, off_plan = strip_summary_checks(raw)
        summary = summary or raw.strip()
        if ctx:
            # Nomes fora do elenco permitido não entram como fato no estado dos personagens.
            summary = _drop_lines_naming(summary, ctx.checklist.forbidden_names)
        self._summary_checks = (plan_check, off_plan)
        # resumo.md só é gravado em run_single, depois que o estado inteiro foi salvo:
        # sem ele o capítulo não conta como concluído se uma fase seguinte falhar.
        # Um resumo anterior do mesmo capítulo (capítulo refeito) é substituído, não duplicado.
        # record=False: só gera o texto; quem chamou decide onde ele entra.
        if record:
            self._record_summary(chapter_num, summary)
        logger.info(f"Cap {chapter_num:02d} — Resumo gerado ({len(summary)} chars)")
        callbacks.on_phase_complete("summarizing", summary, chapter_num)
        return summary

    def _record_summary(self, chapter_num: int, summary: str):
        others = [(n, t) for n, t in self.project.accumulated_summaries if n != chapter_num]
        self.project.accumulated_summaries = sorted(others + [(chapter_num, summary)], key=lambda s: s[0])

    def _allowed_and_forbidden(self, chapter_num: int) -> tuple[list[str], list[str]]:
        ctx = self._ctx.get(chapter_num)
        roster_names = [b["name"] for b in roster_blocks(self.project.character_roster)]
        if ctx:
            allowed = list(dict.fromkeys(ctx.form.characters + ctx.introduced + roster_names))
            forbidden = [n for n in ctx.checklist.forbidden_names + ctx.not_introduced if n not in allowed]
            return allowed, list(dict.fromkeys(forbidden))
        from pipeline.cast import cast_status
        introduced, not_introduced = cast_status(self.project, chapter_num)
        allowed = list(dict.fromkeys(introduced + roster_names))
        return allowed, [n for n in not_introduced if n not in allowed]

    def _run_updating(self, summary: str, chapter_num: int, callbacks: PipelineCallbacks):
        """
        Fase 3.5: memória, roster, threads e callbacks, por mudanças (memory_ops). O que o modelo
        não menciona fica como está; o que cita quem não pode estar na história é recusado.
        """
        self._check_cancelled()
        callbacks.on_status(
            f"Capítulo {chapter_num:02d} — Fase 3.5: Lore + Roster + Threads"
        )
        allowed, forbidden = self._allowed_and_forbidden(chapter_num)
        user_prompt = build_updating_prompt(
            current_memory=self.project.dynamic_memory,
            chapter_summary=summary,
            current_roster=self.project.character_roster,
            current_threads=self.project.open_threads,
            chapter_num=chapter_num,
            current_callbacks=self.project.callbacks,
            allowed=allowed,
        )
        ops = []
        raw = ""
        for attempt in range(2):
            raw = generate_text(
                model=self.model_summarizing,
                provider=self.provider_summarizing,
                system_prompt=SYSTEM_UPDATING,
                user_prompt=f"{user_prompt}\n\n{self.state_rule}",
                temperature=self.summarizing_temperature,
                num_ctx=self.summarizing_num_ctx,
                timeout=self.request_timeout,
                on_token=lambda token: None,
                cancel_event=self.cancel_event,
                extra_options=self.summarizing_extra or None,
            )
            ops = parse_ops(raw)
            if ops or "=== DYNAMIC MEMORY" in raw.upper():
                break
            user_prompt += ("\n\nYour last answer had no change lines. Every line must start with MEMORY ADD, "
                            "MEMORY REPLACE, ROSTER NEW, ROSTER UPDATE, THREAD NEW, THREAD RESOLVED, CALLBACK "
                            "or END STATE.")
        chapter_dir = self.project.chapter_dir(chapter_num)
        if not ops:
            self._legacy_update(raw, chapter_num, forbidden, allowed)
            return
        if not any(op.kind == "END STATE" for op in ops):
            end = _end_state_from_summary(summary, [c.name for c in self._protagonists()])
            if end:
                ops += parse_ops(f"END STATE: {end}")
        res = apply_ops(ops, self.project.dynamic_memory, self.project.character_roster, self.project.open_threads,
                        self.project.callbacks, summary, chapter_num, allowed, forbidden,
                        config.OPEN_THREADS_MAX, config.ROSTER_MAX_CHARS)
        self.project.dynamic_memory = res.memory
        self.project.character_roster = res.roster
        self.project.open_threads = res.threads
        self.project.callbacks = res.callbacks
        write_file(chapter_dir / "memoria_diff.md", diff_report(chapter_num, res))
        logger.info(
            f"Cap {chapter_num:02d} — Memória atualizada: {len(res.applied)} mudança(s), "
            f"{len(res.rejected)} recusada(s)"
        )

    def _legacy_update(self, raw: str, chapter_num: int, forbidden: list[str], allowed: list[str]):
        """
        Reserva para modelos que devolvem as três seções inteiras (formato antigo): vale só se não
        trouxer nomes proibidos e não perder threads antigos sem explicação.
        """
        memory, roster, threads = parse_updating_output(raw)
        text = "\n".join([memory, roster, threads])
        bad = [n for n in forbidden if mentions(n, text) and n not in allowed]
        note = ""
        if bad:
            note = f"Atualização recusada: cita quem não pode estar na história ({', '.join(bad)})."
        elif not (memory or roster or threads):
            note = "O modelo não devolveu mudanças reconhecíveis; a memória ficou como estava."
        if note:
            logger.warning(f"Cap {chapter_num:02d} — {note}")
            write_file(self.project.chapter_dir(chapter_num) / "memoria_diff.md", f"# Memória — capítulo {chapter_num:02d}\n\n{note}\n")
            return
        if memory:
            self.project.dynamic_memory = memory
        if roster:
            self.project.character_roster = roster
        if threads:
            self.project.open_threads = threads
        logger.info(f"Cap {chapter_num:02d} — Memória/Roster/Threads reescritos (formato antigo)")

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
            user_prompt=f"{user_prompt}\n\n{self.state_rule}",
            temperature=0.2,
            num_ctx=self.summarizing_num_ctx,
            timeout=self.request_timeout,
            on_token=lambda token: None,
            cancel_event=self.cancel_event,
            extra_options=self.summarizing_extra or None,
        )
        end = [l for l in mem.splitlines() if re.match(r"^\s*-?\s*End of Ch\.?\s*\d+", l, re.I)]
        compressed = compressed.strip()
        if end and end[0].strip() not in compressed:
            compressed = f"{end[0].strip()}\n{compressed}"
        self.project.dynamic_memory = compressed
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
            user_prompt=f"{user_prompt}\n\n{self.state_rule}",
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

    def _run_consistency_check(self, final_text: str, chapter_num: int, callbacks: PipelineCallbacks) -> list[str]:
        """
        Conferência final do capítulo, antes de a memória ser atualizada com ele.

        Camada 1, sem modelo: nomes proibidos ou que ainda não estrearam, conversa de assistente,
        roteiro, laço, idioma, linhas [System] além da conta e o que o resumo apontou como fora do
        plano. Camada 2 (CONSISTENCY_CHECK_ENABLED): o modelo confere cada cena com o cânone do
        capítulo inteiro (sem cortes) e cada problema precisa de citação exata.

        Retorna os problemas graves; o relatório vai para consistencia.md.
        """
        self._check_cancelled()
        callbacks.on_status(f"Capítulo {chapter_num:02d} — Checagem de consistência")
        ctx = self._ctx.get(chapter_num)
        if ctx is None:
            premise_file = self.project.chapter_dir(chapter_num) / "premissa.md"
            premise = premise_file.read_text(encoding="utf-8") if premise_file.exists() else ""
            ctx = self._chapter_context(premise, chapter_num, callbacks) if premise.strip() else None
        _, scenes = split_chapter(final_text)
        sections: list[tuple[str, list]] = []
        hard: list[str] = []
        soft: list[str] = []
        used = 0
        for i, scene in enumerate(scenes, start=1):
            problems: list[qa.Issue] = []
            if ctx:
                problems += qa.scene_issues(scene, "", ctx.checklist, self.story_code, ctx.leak, None)
            if config.CONSISTENCY_CHECK_ENABLED and ctx:
                try:
                    raw = generate_text(
                        model=self.model_refining, provider=self.provider_refining,
                        system_prompt=SYSTEM_CONSISTENCY,
                        user_prompt=build_consistency_prompt(
                            ctx.canon.text, self.project.dynamic_memory,
                            roster_for_chapter(self.project.character_roster, ctx.form.characters + [scene]),
                            scene, list(dict.fromkeys(ctx.form.characters + ctx.introduced)), ctx.form.must_not)
                        + f"\n\n{self.notes_rule}",
                        temperature=0.0, num_ctx=self.refining_num_ctx, timeout=self.request_timeout,
                        cancel_event=self.cancel_event, extra_options=dict(self.refining_extra, num_predict=500),
                        json_output=True,
                    )
                    _, violations = qa.parse_judge(raw, scene, 0)
                    problems += [qa.Issue("cânone", v.text, hard=False, quote=v.quote) for v in violations]
                except (GenerationInterrupted, OllamaError) as e:
                    if self.cancel_event.is_set():
                        raise
                    soft.append(f"Cena {i}: conferência com o modelo não rodou ({e})")
            used += qa.system_lines(scene)
            sections.append((f"Cena {i}", [str(p) for p in problems]))
            hard += [f"Cena {i}: {p}" for p in problems if p.hard]
        if ctx and used > ctx.checklist.max_system_lines:
            soft.append(f"{used} linhas [System] no capítulo (limite {ctx.checklist.max_system_lines})")
        plan_check, off_plan = self._summary_checks
        if off_plan and ctx:
            off_names = [n for n in ctx.checklist.forbidden_names if mentions(n, off_plan)]
            hard += [f"O resumo aponta fora do plano: {n}" for n in off_names]
        drafting_notes = self._qa_notes.get(chapter_num, [])
        found = bool(hard or soft or drafting_notes or any(p for _, p in sections))
        sections = [("Problemas graves", hard), ("Avisos", soft), ("Rascunho", drafting_notes)] + sections
        extra = []
        if plan_check:
            extra.append(("Conferência do plano (resumo)", _bullets(plan_check)))
        if off_plan:
            extra.append(("Fora do plano (resumo)", _bullets(off_plan)))
        notes = qa.report(sections) if found else "OK"
        if extra:
            notes = f"{notes}\n\n{qa.report(extra)}".strip()
        write_file(self.project.chapter_dir(chapter_num) / "consistencia.md", notes)
        if hard:
            logger.warning(f"Cap {chapter_num:02d} — Conferência final: {len(hard)} problema(s) grave(s)")
            callbacks.on_status(f"⚠ Consistência: veja consistencia.md do capítulo {chapter_num:02d}")
        else:
            logger.info(f"Cap {chapter_num:02d} — Consistência OK")
        return hard

    def _finish_chapter(self, result: ChapterResult, chapter_num: int, callbacks: PipelineCallbacks,
                        gate: bool = True) -> bool:
        """
        Do texto final em diante: resumo, conferência, memória, fusão, estado e resumo.md.

        Com `gate`, um capítulo que falha na conferência final não entra na memória: o resumo é
        gravado (o capítulo aparece como escrito), mas memória, roster, threads e resumos ficam
        como estavam e o capítulo fica marcado para revisão. Retorna False nesse caso.
        """
        chapter_dir = self.project.chapter_dir(chapter_num)
        result.status = "summarizing"
        result.summary = self._run_summarizing(result.final, chapter_num, callbacks, record=False)

        hard = self._run_consistency_check(result.final, chapter_num, callbacks)
        result.consistency_notes = "\n".join(hard) or "OK"
        if hard and gate:
            # A memória fica a de antes do capítulo (num refazer, sem a versão antiga dele).
            self.project.save_state()
            write_file(chapter_dir / "resumo.md", result.summary)
            update_info(self.project, chapter_num, memory_stale=True, memory_note=_QA_NOTE, qa_failed=True)
            return False

        self._record_summary(chapter_num, result.summary)
        self._run_updating(result.summary, chapter_num, callbacks)
        self._run_compress_memory(callbacks)
        self._run_merging(callbacks)

        self.project.last_chapter_num = max(self.project.last_chapter_num, chapter_num)
        self.project.save_state()
        # Por último: com resumo.md no disco o capítulo passa a contar como concluído.
        write_file(chapter_dir / "resumo.md", result.summary)
        update_info(self.project, chapter_num, memory_stale=False, memory_note="", qa_failed=False)
        return True

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

    def _summary_hash(self, chapter_num: int) -> str:
        path = self.project.chapter_dir(chapter_num) / "resumo.md"
        if chapter_num < 1 or not path.exists():
            return ""
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]

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
        self._ctx.pop(chapter_num, None)

        chapter_dir = self.project.chapter_dir(chapter_num)
        # Estado de antes do capítulo: volta a valer se qualquer fase falhar, e fica
        # gravado na pasta do capítulo para a exclusão poder desfazê-lo depois. Leva a marca
        # do resumo do capítulo anterior: se ele mudar, o snapshot deixa de valer.
        before = self.project.snapshot_state()
        self.project.save_chapter_snapshot(chapter_num, {**before, "prev_summary_hash": self._summary_hash(chapter_num - 1)})
        write_file(chapter_dir / "premissa.md", premise)

        try:
            result.status = "drafting"
            result.draft = self._run_drafting(premise, chapter_num, callbacks)

            result.status = "polishing"
            result.final = self._run_refining(result.draft, chapter_num, callbacks)

            if self._finish_chapter(result, chapter_num, callbacks):
                result.status = "done"
                callbacks.on_status(f"✓ Capítulo {chapter_num:02d} concluído!")
            else:
                result.status = "qa_failed"
                callbacks.on_status(f"⚠ Capítulo {chapter_num:02d} escrito, mas a conferência final achou problemas: "
                                    "a memória não foi atualizada. Veja a aba Verificações.")
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
        capítulo concluído, e então o estado de antes é o vazio). None nos outros casos,
        e também quando o capítulo anterior mudou depois do snapshot.
        """
        if later_done(self.project, chapter_num):
            return None
        snap = load_snapshot(self.project, chapter_num)
        if snap is not None:
            mark = snap.get("prev_summary_hash")
            if mark and mark != self._summary_hash(chapter_num - 1):
                logger.warning(f"Cap {chapter_num:02d} — o capítulo anterior mudou depois do snapshot; "
                               "a memória atual fica como base")
                return None
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
        self._ctx.pop(chapter_num, None)
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
                self._run_consistency_check(result.final, chapter_num, callbacks)
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
        o texto novo. Capítulo do meio: só o resumo é refeito. O pedido vem do usuário, que já
        revisou o texto: a conferência final roda e gera o relatório, mas não trava a memória.
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
        self._ctx.pop(chapter_num, None)
        pre = self.project.snapshot_state()
        base = self._memory_base(chapter_num)
        try:
            if base is not None:
                self.project.restore_state(base)
                self._finish_chapter(result, chapter_num, callbacks, gate=False)
            else:
                result.status = "summarizing"
                result.summary = self._run_summarizing(result.final, chapter_num, callbacks, record=False)
                self._replace_summary(chapter_num, result.summary)
                self.project.save_state()
                write_file(chapter_dir / "resumo.md", result.summary)
                update_info(self.project, chapter_num, memory_stale=False, memory_note=_MIDDLE_NOTE, qa_failed=False)
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
        Roda os capítulos em sequência e para no primeiro erro ou no primeiro capítulo que falhar
        na conferência final (o seguinte seria escrito sem a memória dele).

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
            if result.status in ("error", "qa_failed"):
                callbacks.on_status(
                    f"⚠ Pipeline interrompido no capítulo {chapter_num:02d}. "
                    f"Processados: {len(results)}/{total}."
                )
                break

        callbacks.on_pipeline_complete(results)
        return results


# ─── Funções auxiliares ─────────────────────────────────────

def _shorten_old_summaries(summaries: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Os dois resumos mais recentes inteiros; os anteriores só com a seção de eventos."""
    out = []
    for k, (n, text) in enumerate(summaries):
        if k >= len(summaries) - 2:
            out.append((n, text))
            continue
        m = re.search(r"(?is)(1\s*[.)]\s*\**\s*KEY EVENTS.*?)(?=\n\s*\**\s*2\s*[.)]|\Z)", text)
        out.append((n, m.group(1).strip() if m else text))
    return out


def _canon_core(canon: str) -> str:
    """O essencial do cânone para a revisão: resumo, regras e proibições (sem fichas e listas longas)."""
    from pipeline.canon import split_sections
    keep = []
    for sec in split_sections(canon):
        t = sec.title.lower()
        if sec.kind in ("summary",) or re.match(r"^(2|14)\.\s", t) or chapter_range_title(t):
            keep.append(sec.text())
    return "\n\n".join(keep)


def chapter_range_title(title: str) -> bool:
    from pipeline.canon import chapter_range
    return chapter_range(title) is not None


def _corrective(hard: list, missing: list[str], cast: list[str], language: str) -> str:
    """
    Correção para a nova tentativa da cena, dita em positivo: o que mostrar e quem pode estar em
    cena. Repetir os nomes proibidos só reforçaria a ideia no modelo.
    """
    lines = ["Your previous version of this scene had problems. In this new version:"]
    for item in missing:
        lines.append(f"- Show this on the page: {item}")
    kinds = {i.kind for i in hard}
    if "proibido" in kinds or "juiz" in kinds:
        who = ", ".join(cast) if cast else "only the characters already in the scene"
        lines.append(f"- Only these characters appear: {who}. Keep to the scene plan and the canon.")
    if "idioma" in kinds:
        lines.append(f"- Write every sentence in {language}; translate any word from the brief.")
    if "conversa de assistente" in kinds or "comentário" in kinds:
        lines.append("- Output only story prose, with no message to the reader or the editor.")
    if "roteiro" in kinds:
        lines.append("- Write prose paragraphs; dialogue goes in quotes inside the narration.")
    if "laço" in kinds:
        lines.append("- Every paragraph moves the scene forward; never repeat a passage.")
    if "[System]" in kinds:
        lines.append("- Use no more [System] lines in this scene.")
    return "\n".join(lines) if len(lines) > 1 else ""


def _official_names(text: str, glossary: list[tuple[str, str]]) -> str:
    """
    Nomes próprios que a tradução deixou em português viram a forma oficial do glossário
    ('Barnaby "Tambor"' → 'Barnaby "Thumper"'). Só termos com maiúscula: palavras comuns ficam com o tradutor.
    """
    for pt, en in sorted(glossary, key=lambda p: -len(p[0])):
        if pt[:1].isupper() and pt != en:
            en_clean = re.sub(r"\s*\(.*?\)$", "", en).strip()
            text = re.sub(rf"(?<!\w){re.escape(pt)}(?!\w)", en_clean, text)
    return text


def _drop_invented_counts(translated: str, original: str) -> str:
    """Tira os "(24)" que o tradutor às vezes inventa no fim das linhas (contagem de palavras do próprio modelo)."""
    if re.search(r"\(\d+\)\s*$", original, re.M):
        return translated
    return re.sub(r"[ \t]*\(\d+\)[ \t]*$", "", translated, flags=re.M)


def _bullets(section: str) -> list[str]:
    """Itens de uma seção do resumo, sem o título, sem linhas vazias e sem o marcador do modelo."""
    lines = section.splitlines()[1:]
    return [re.sub(r"^\s*[-*•]\s*", "", l).strip() for l in lines if re.sub(r"^\s*[-*•]\s*", "", l).strip()]


def _drop_lines_naming(summary: str, names: list[str]) -> str:
    """Tira do resumo as linhas que tratam como fato quem não pode estar no capítulo."""
    if not names:
        return summary
    kept = [l for l in summary.splitlines() if not any(mentions(n, l) for n in names)]
    return "\n".join(kept)


def _end_state_from_summary(summary: str, heroes: list[str]) -> str:
    """Estado do protagonista no fim do capítulo, da seção CHARACTER STATE do resumo."""
    m = re.search(r"(?is)2\s*[.)]\s*\**\s*CHARACTER STATE\**\s*:?(.*?)(?=\n\s*\**\s*3\s*[.)]|\Z)", summary or "")
    if not m:
        return ""
    lines = [re.sub(r"^\s*[-*]\s*", "", l).strip() for l in m.group(1).splitlines() if l.strip()]
    for l in lines:
        if any(mentions(h, l) for h in heroes):
            return l
    return lines[0] if lines else ""
