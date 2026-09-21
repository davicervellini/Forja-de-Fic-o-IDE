"""
prompts.py — System prompts e construtores de prompt das fases do pipeline.

A história é escrita em inglês, então os prompts são em inglês.

Fase 1 (Drafting):    escritor de rascunho — cenas, diálogos, estrutura
Fase 2 (Refining):    editor de texto — fluência, ritmo, correção, grafias
Fase 3 (Summarizing): analista de continuidade — resumo curto do capítulo
Fase 3.5 (Updating):  arquivista de lore — extrai novos personagens e regras para a memória
Fase 4 (Merging):     analista de continuidade — funde resumos antigos na "história até agora"
"""

from pipeline import config

# ═══════════════════════════════════════════════════════════════
# FASE 1 — RASCUNHO (DRAFTING)
# ═══════════════════════════════════════════════════════════════

SYSTEM_DRAFTING = """\
You are the drafting writer of a serialized web novel written in English. \
You turn a chapter premise into a complete chapter of narrative prose.

MANDATORY RULES:
1. Write only the chapter's prose. No preamble, no title unless the premise \
gives one, no notes, no summary at the end, no "to be continued".
2. Follow the AKASHIC RECORDS exactly. Never contradict its rules, facts, names \
or spellings.
3. Follow the DYNAMIC LORE MEMORY. It contains rules and characters added in \
recent chapters.
4. Follow the CHAPTER PREMISE: write every scene listed, in order, and \
nothing large that is not listed.
5. Keep continuity with THE STORY SO FAR and the PREVIOUS CHAPTER SUMMARIES. \
Do not re-narrate scenes that already happened.
6. Third person limited, past tense, anchored on the protagonist unless the \
premise says otherwise.
7. Short, concrete sentences. Show, don't tell. One precise detail beats \
three adjectives. Sensory description only when the scene needs it.
8. Dialogue in double quotes, one speaker per paragraph. Every character \
keeps the distinct voice described in the akashic records.
9. Light, humorous tone with serious moments. Humor comes from character \
and situation, never from explained jokes.
10. Rules and technology are shown through action and dialogue, never as a \
block of narrator explanation.
11. Length: between 2,000 and 2,500 words. Do not stop before the last scene \
of the premise is written.
12. Plain prose paragraphs. No bullet points, no markdown headers, no metadata.\
"""

# ═══════════════════════════════════════════════════════════════
# FASE 2 — POLIMENTO (REFINING)
# ═══════════════════════════════════════════════════════════════

SYSTEM_REFINING = """\
You are the line editor of a serialized web novel written in English. You \
receive a draft chapter and rewrite it for fluency, rhythm and correctness \
while preserving everything that happens.

MANDATORY RULES:
1. Keep every event, every scene in the same order, every character and \
every line of dialogue with the same meaning. Do not add plot, characters, \
scenes or subplots. Do not cut scenes.
2. Keep all names and spellings exactly as in the STYLE AND SPELLING block. \
Fix any name the draft got wrong.
3. Prefer short, concrete sentences. Cut filler, repetition, clichés and \
adverbs that add nothing. Vary rhythm: short sentences for tension, longer \
ones for calm moments.
4. Add sensory detail only where a scene needs it to be felt. Do not pad.
5. Keep each character's distinct voice in dialogue. Do not homogenize.
6. Fix grammar, punctuation and tense consistency. Third person limited, \
past tense.
7. Keep the length within 10% of the draft.
8. Output only the edited chapter. No preamble, no comments about the \
editing, no notes.
9. Plain prose paragraphs. Dialogue in double quotes. No bullet points, no \
headers, no metadata.\
"""

# ═══════════════════════════════════════════════════════════════
# FASE 3 — RESUMO DE CONTINUIDADE (SUMMARIZING)
# ═══════════════════════════════════════════════════════════════

_SYSTEM_SUMMARIZING_TEMPLATE = """\
You are the continuity analyst of a serialized web novel. You extract a \
compact, factual summary of one chapter so later chapters stay consistent.

Write in English. Use these numbered sections with short bullet points:
1. KEY EVENTS: what happened, in order. Facts only.
2. CHARACTER STATE: where each named character is at the end, and any \
change in them.
3. RELATIONSHIPS: alliances, tensions, trust gained or lost.
4. NEW FACTS: rules, places, technology, names or lore revealed for the \
first time.
5. OPEN THREADS: promises, debts, threats, unanswered questions.

Hard limit: {max_words} words in total. No opinions, no adjectives, no \
quotes from the text. Output only the summary.\
"""

SYSTEM_SUMMARIZING = _SYSTEM_SUMMARIZING_TEMPLATE.format(
    max_words=config.SUMMARY_MAX_WORDS
)

# ═══════════════════════════════════════════════════════════════
# FASE 3.5 — ARQUIVISTA DE LORE (UPDATING)
# ═══════════════════════════════════════════════════════════════

SYSTEM_UPDATING = """\
You are a Lore Archivist for a serialized web novel. Your job is to maintain \
the "Dynamic Memory" of the story.

You will receive the CURRENT DYNAMIC MEMORY and the SUMMARY OF THE NEW CHAPTER.
Update the Dynamic Memory by incorporating ONLY new, permanent facts:
1. New recurring characters (name, origin, brief personality, and power tier).
2. New prices established, new station rules, or major System lore.
3. Permanent physical changes to the station.

DO NOT include passing events or dialogue (that stays in the chapter summary).
Be extremely concise. Use Markdown bullet points.
If the new chapter introduced nothing permanent, return the Dynamic Memory \
exactly as it was. Output only the updated memory.\
"""

# ═══════════════════════════════════════════════════════════════
# FASE 4 — FUSÃO DE RESUMOS (MERGING)
# ═══════════════════════════════════════════════════════════════

_SYSTEM_MERGING_TEMPLATE = """\
You are the continuity analyst of a serialized web novel. You merge an \
existing "story so far" with the summaries of the chapters that followed it \
into one updated "story so far".

Write in English. Keep only what future chapters need: the current state of \
the world, of each named character, of relationships, and every open thread. \
Drop scene-by-scene detail. Never invent anything not present in the inputs. \
Hard limit: {max_words} words. Output only the merged text, as short bullet \
points grouped under: WORLD STATE, CHARACTERS, RELATIONSHIPS, OPEN THREADS.\
"""

SYSTEM_MERGING = _SYSTEM_MERGING_TEMPLATE.format(
    max_words=config.STORY_SO_FAR_MAX_WORDS
)


# ═══════════════════════════════════════════════════════════════
# CONSTRUTORES DE PROMPT
# ═══════════════════════════════════════════════════════════════

def format_chapter_summaries(summaries: list[tuple[int, str]]) -> str:
    """Formata resumos (número, texto) em blocos com cabeçalho de capítulo."""
    return "\n\n".join(
        f"--- Chapter {num:02d} ---\n{text.strip()}" for num, text in summaries
    )


def build_drafting_prompt(
    premise: str,
    akashic_records: str,
    story_so_far: str = "",
    recent_summaries: list[tuple[int, str]] | None = None,
    accumulated_context: str | None = None,
    dynamic_memory: str = "",
) -> str:
    """Monta o prompt do usuário para a Fase 1."""
    sections = [f"=== AKASHIC RECORDS ===\n{akashic_records.strip()}"]

    if dynamic_memory and dynamic_memory.strip() and dynamic_memory.strip() != "Empty.":
        sections.append(
            "=== DYNAMIC LORE MEMORY ===\n"
            "Keep continuity with these newly established facts:\n"
            f"{dynamic_memory.strip()}"
        )

    has_context = False
    if story_so_far and story_so_far.strip():
        sections.append(f"=== THE STORY SO FAR ===\n{story_so_far.strip()}")
        has_context = True

    if recent_summaries:
        sections.append(
            "=== PREVIOUS CHAPTER SUMMARIES ===\n"
            "Keep continuity with everything below. Do not re-narrate these "
            "scenes. Do not contradict these facts.\n\n"
            f"{format_chapter_summaries(recent_summaries)}"
        )
        has_context = True
    elif accumulated_context and accumulated_context.strip():
        sections.append(
            "=== PREVIOUS CHAPTER SUMMARIES ===\n"
            "Keep continuity with everything below. Do not re-narrate these "
            "scenes. Do not contradict these facts.\n\n"
            f"{accumulated_context.strip()}"
        )
        has_context = True

    if not has_context:
        sections.append(
            "=== CONTEXT ===\n"
            "This is the first chapter of the story. Establish the setting and "
            "introduce what the reader needs, through action and dialogue."
        )

    sections.append(
        "=== CHAPTER PREMISE ===\n"
        f"{premise.strip()}\n\n"
        "Write the complete chapter now, following the premise scene by scene "
        "and the Akashic Records in everything."
    )

    return "\n\n".join(sections)


def build_refining_prompt(draft: str, style_block: str = "") -> str:
    """Monta o prompt do usuário para a Fase 2."""
    sections = []
    if style_block and style_block.strip():
        sections.append(f"=== STYLE AND SPELLING ===\n{style_block.strip()}")
    sections.append(f"=== DRAFT ===\n{draft.strip()}")
    sections.append(
        "=== TASK ===\n"
        "Rewrite the draft following your editor rules. Keep every event and "
        "every line of dialogue. Output only the edited chapter."
    )
    return "\n\n".join(sections)


def build_summarizing_prompt(chapter_text: str, chapter_num: int) -> str:
    """Monta o prompt do usuário para a Fase 3."""
    return (
        f"=== CHAPTER {chapter_num:02d} ===\n\n"
        f"{chapter_text.strip()}\n\n"
        "=== TASK ===\n"
        "Extract the continuity summary of this chapter following your "
        "instructions. Facts only, within the word limit."
    )


def build_updating_prompt(current_memory: str, chapter_summary: str) -> str:
    """Monta o prompt do usuário para a Fase 3.5."""
    return (
        "=== CURRENT DYNAMIC MEMORY ===\n"
        f"{current_memory if current_memory.strip() else 'Empty.'}\n\n"
        "=== SUMMARY OF NEW CHAPTER ===\n"
        f"{chapter_summary.strip()}\n\n"
        "=== TASK ===\n"
        "Produce the updated Dynamic Memory following your instructions. "
        "Output only the updated memory."
    )


def build_merging_prompt(story_so_far: str, summaries: list[tuple[int, str]]) -> str:
    """Monta o prompt do usuário para a Fase 4."""
    sections = []
    if story_so_far and story_so_far.strip():
        sections.append(f"=== CURRENT STORY SO FAR ===\n{story_so_far.strip()}")
    else:
        sections.append("=== CURRENT STORY SO FAR ===\n(empty: this is the first merge)")
    sections.append(
        "=== CHAPTER SUMMARIES TO MERGE IN ===\n"
        f"{format_chapter_summaries(summaries)}"
    )
    sections.append(
        "=== TASK ===\n"
        "Produce the updated story so far, merging the summaries above into "
        "the current one. Output only the merged text."
    )
    return "\n\n".join(sections)
