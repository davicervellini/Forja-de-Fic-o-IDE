"""
premise_flow.py — Sugestão de premissa fora da tela: o contexto que o modelo recebe, a
sugestão com as travas de premise.py e a premissa automática do próximo capítulo.

Depois que um capítulo termina, o programa já escreve a premissa do seguinte, para a
continuidade ficar fresca. Essa premissa fica marcada como `premise_pending` no info.json
do capítulo: a geração em lote pula o capítulo até o usuário revisar e aprovar (gerar
aquele capítulo pelo botão dele conta como aprovação).
"""

import logging
import threading
from typing import Callable

from pipeline import cast, config
from pipeline import chapters as ch
from pipeline import premise as premise_mod
from pipeline.api import generate_text
from pipeline.io_utils import read_file
from pipeline.project import StoryProject

logger = logging.getLogger(__name__)


def cast_so_far(project: StoryProject, num: int) -> tuple[list[str], list[str]]:
    """(personagens que já apareceram antes deste capítulo ou são protagonistas, os que ainda não)."""
    _, meta = cast.load(project)
    if not meta:
        return [], []
    seen = cast.appearances(project, [c.name for c in meta.characters])
    introduced = [c.name for c in meta.characters
                  if c.role == "protagonist" or any(n < num for n in seen.get(c.name, []))]
    return introduced, [c.name for c in meta.characters if c.name not in introduced]


def premise_context(project: StoryProject, num: int) -> dict:
    """Argumentos de build_suggest_prompt/build_check_prompt para o capítulo `num`."""
    from pipeline.scenes import split_chapter, tail_words
    akashic_full = read_file(project.akashic_path) if project.akashic_path.exists() else ""
    prev = project.chapter_dir(num - 1) / "capitulo_final.md"
    tail = ""
    if num > 1 and prev.exists():
        _, scenes = split_chapter(read_file(prev))
        tail = tail_words("\n\n".join(scenes), 400)
    # Com capítulos depois deste já prontos, o contexto é o de antes dele (snapshot), se houver.
    snap = ch.load_snapshot(project, num) if ch.later_done(project, num) else None
    state = snap or project.snapshot_state()
    introduced, not_introduced = cast_so_far(project, num)
    _, meta = cast.load(project)
    return dict(
        akashic=project.load_akashic_model(),
        outline=premise_mod.outline_row(akashic_full, num),
        next_outline=premise_mod.outline_row(akashic_full, num + 1),
        introduced=introduced,
        not_introduced=not_introduced,
        places=[loc.name for loc in meta.locations] if meta else [],
        previous_tail=tail,
        story_so_far=state.get("story_so_far", ""),
        summaries=[(int(n), t) for n, t in state.get("accumulated_summaries", []) if int(n) < num],
        open_threads=state.get("open_threads", ""),
        roster=state.get("character_roster", ""),
    )


def ask_model(system: str, user: str, suggest: bool, cancel_event: threading.Event | None = None,
              on_token: Callable[[str], None] | None = None) -> str:
    """Planejar a premissa pede o modelo mais capaz: o do polimento."""
    return generate_text(
        model=config.MODEL_REFINING, provider=config.PROVIDER_REFINING,
        system_prompt=system, user_prompt=user,
        temperature=0.5 if suggest else 0.1,
        num_ctx=config.REFINING_NUM_CTX, cancel_event=cancel_event,
        on_token=on_token or (lambda tok: None),
        # Premissa tem ~500 palavras; o teto baixo corta o modelo que tenta escrever o capítulo.
        extra_options={"num_predict": 900, "stop": ["\n---"]},
    )


def clean_suggestion(raw: str, ctx: dict) -> tuple[premise_mod.PremiseForm, str, list[str]]:
    """Aplica as travas à resposta do modelo: (formulário, resposta cortada, quem saiu do elenco)."""
    raw = premise_mod.cut_after_premise(raw)
    form = premise_mod.from_text(raw)
    premise_mod.drop_template_echo(form)
    premise_mod.prune_must_not(form, ctx["outline"])
    removed = premise_mod.enforce_cast(form, ctx["not_introduced"], ctx["outline"])
    return form, raw, removed


def auto_next_premise(project: StoryProject, done_num: int, cancel_event: threading.Event | None = None,
                      on_status: Callable[[str], None] | None = None,
                      on_token: Callable[[str], None] | None = None) -> dict | None:
    """
    Escreve a premissa do capítulo seguinte a `done_num` e marca como esperando aprovação.
    Não faz nada se o capítulo seguinte já tem premissa. Retorna {chapter, ok, removed} ou None.
    """
    num = done_num + 1
    path = project.chapter_dir(num) / "premissa.md"
    if path.exists() and read_file(path).strip():
        return None
    if on_status:
        on_status(f"Capítulo {num:02d} — Escrevendo a premissa para você revisar")
    ctx = premise_context(project, num)
    user = premise_mod.build_suggest_prompt(num, config.CHAPTER_TARGET_WORDS, "", **ctx)
    raw = ask_model(premise_mod.SYSTEM_PREMISE_WRITER, user, True, cancel_event, on_token)
    form, raw, removed = clean_suggestion(raw, ctx)
    ok = bool(form.scenes)
    # Fora do formato, a resposta vai como texto: o usuário revisa do mesmo jeito.
    ch.save_premise(project, num, premise_mod.to_text(form, num) if ok else raw)
    ch.update_info(project, num, premise_pending=True, premise_auto=True)
    logger.info(f"Premissa do capítulo {num:02d} sugerida e esperando aprovação.")
    return {"chapter": num, "ok": ok, "removed": removed}


def approve(project: StoryProject, num: int):
    if ch.read_info(project, num).get("premise_pending"):
        ch.update_info(project, num, premise_pending=False)


def pending_approval(project: StoryProject, num: int) -> bool:
    return bool(ch.read_info(project, num).get("premise_pending"))
