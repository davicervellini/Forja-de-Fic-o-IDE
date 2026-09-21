"""
prompts.py — System prompts e construtores de prompt das fases do pipeline.

A história é escrita em inglês, então os prompts são em inglês.

Fase 1 (Drafting):       escritor de rascunho
Fase 2 (Refining):       editor de texto
Fase 3 (Summarizing):    analista de continuidade
Fase 3.5 (Updating):     arquivista de lore + roster + threads
Fase 3.6 (Compress):     compressão da memória dinâmica (quando necessário)
Fase 4 (Merging):        fusão de resumos antigos
Fase 5 (Consistency):    verificação opcional de continuidade
"""

from pipeline import config

SYSTEM_DRAFTING = """\\
You are the drafting writer of a serialized web novel written in English. \\
You turn a chapter premise into a complete chapter of narrative prose.

MANDATORY RULES:
1. Write only the chapter's prose. No preamble, no title unless the premise \\
gives one, no notes, no summary at the end, no \"to be continued\".
2. Follow the AKASHIC RECORDS exactly. Never contradict its rules, facts, names \\
or spellings.
3. Follow the DYNAMIC LORE MEMORY and the CHARACTER ROSTER. They contain rules \\
and characters established in previous chapters.
4. Respect OPEN THREADS: do not ignore long-standing unresolved threads without \\
reason. Prefer to advance or close the oldest ones when the premise allows.
5. Follow the CHAPTER PREMISE: write every scene listed, in order, and \\
nothing large that is not listed.
6. Keep continuity with THE STORY SO FAR and the PREVIOUS CHAPTER SUMMARIES. \\
Do not re-narrate scenes that already happened.
7. Third person limited, past tense, anchored on the protagonist unless the \\
premise says otherwise.
8. Short, concrete sentences. Show, don't tell. One precise detail beats \\
three adjectives. Sensory description only when the scene needs it.
9. Dialogue in double quotes, one speaker per paragraph. Every character \\
keeps the distinct voice described in the akashic records or roster.
10. Light, humorous tone with serious moments. Humor comes from character \\
and situation, never from explained jokes.
11. Rules and technology are shown through action and dialogue, never as a \\
block of narrator explanation.
12. Length: between 2,000 and 2,500 words. Do not stop before the last scene \\
of the premise is written.
13. Plain prose paragraphs. No bullet points, no markdown headers, no metadata.\\
"""

SYSTEM_REFINING = """\\
You are the line editor of a serialized web novel written in English. You \\
receive a draft chapter and rewrite it for fluency, rhythm and correctness \\
while preserving everything that happens.

MANDATORY RULES:
1. Keep every event, every scene in the same order, every character and \\
every line of dialogue with the same meaning. Do not add plot, characters, \\
scenes or subplots. Do not cut scenes.
2. Keep all names and spellings exactly as in the STYLE AND SPELLING block. \\
Fix any name the draft got wrong.
3. Prefer short, concrete sentences. Cut filler, repetition, clichés and \\
adverbs that add nothing. Vary rhythm: short sentences for tension, longer \\
ones for calm moments.
4. Add sensory detail only where a scene needs it to be felt. Do not pad.
5. Keep each character's distinct voice in dialogue. Do not homogenize.
6. Fix grammar, punctuation and tense consistency. Third person limited, \\
past tense.
7. Keep the length within 10% of the draft.
8. Output only the edited chapter. No preamble, no comments about the \\
editing, no notes.
9. Plain prose paragraphs. Dialogue in double quotes. No bullet points, no \\
headers, no metadata.\\
"""

_SYSTEM_SUMMARIZING_TEMPLATE = """\\
You are the continuity analyst of a serialized web novel. You extract a \\
compact, factual summary of one chapter so later chapters stay consistent.

Write in English. Use these numbered sections with short bullet points:
1. KEY EVENTS: what happened, in order. Facts only.
2. CHARACTER STATE: where each named character is at the end, and any \\
change in them.
3. RELATIONSHIPS: alliances, tensions, trust gained or lost.
4. NEW FACTS: rules, places, technology, names or lore revealed for the \\
first time.
5. OPEN THREADS: promises, debts, threats, unanswered questions. For each, \\
note if it is new or continuing.

Hard limit: {max_words} words in total. No opinions, no adjectives, no \\
quotes from the text. Output only the summary.\\
"""

SYSTEM_SUMMARIZING = _SYSTEM_SUMMARIZING_TEMPLATE.format(
    max_words=config.SUMMARY_MAX_WORDS
)

SYSTEM_UPDATING = """\\
You are a Lore Archivist and Continuity Keeper for a serialized web novel.
You maintain three living documents:

A) DYNAMIC MEMORY — only permanent new facts (new recurring characters, \\
new rules, permanent physical changes to the setting). Extremely concise \\
Markdown bullets. Do not include passing events.

B) CHARACTER ROSTER — a living list of important characters. For each:
   - Name
   - Status (alive / dead / missing / unknown)
   - Current location or last known place
   - Brief role / relationship to protagonist
   - One-line personality or voice note
Keep it under a reasonable size. Remove or mark characters that are clearly \\
irrelevant forever. Update status and location from the new summary.

C) OPEN THREADS — unresolved promises, debts, threats, mysteries.
   Format each as: [Ch.XX] description
   Keep the chapter number where it first appeared.
   Remove threads that were clearly resolved in the new summary.
   Keep the oldest and most important ones. Hard limit around 15 threads.

You will receive the CURRENT versions of all three + the SUMMARY OF THE NEW CHAPTER.
Output exactly three sections, nothing else:

=== DYNAMIC MEMORY ===
...updated content...

=== CHARACTER ROSTER ===
...updated content...

=== OPEN THREADS ===
...updated content...

If a section has no change, still output it with the previous content.
"""

SYSTEM_COMPRESS_MEMORY = """\\
You are a Lore Archivist. The Dynamic Memory has grown too large.
Compress it while keeping every permanent fact that future chapters still need.
Merge redundant entries, drop anything that is no longer relevant, keep names \\
and rules intact. Output only the compressed Dynamic Memory as Markdown bullets.
Hard limit: roughly {max_words} words.
""".format(max_words=config.DYNAMIC_MEMORY_MAX_WORDS)

_SYSTEM_MERGING_TEMPLATE = """\\
You are the continuity analyst of a serialized web novel. You merge an \\
existing \"story so far\" with the summaries of the chapters that followed it \\
into one updated \"story so far\".

Write in English. Keep only what future chapters need: the current state of \\
the world, of each named character, of relationships, and every open thread. \\
Drop scene-by-scene detail. Never invent anything not present in the inputs. \\
Hard limit: {max_words} words. Output only the merged text, as short bullet \\
points grouped under: WORLD STATE, CHARACTERS, RELATIONSHIPS, OPEN THREADS.\\
"""

SYSTEM_MERGING = _SYSTEM_MERGING_TEMPLATE.format(
    max_words=config.STORY_SO_FAR_MAX_WORDS
)

SYSTEM_CONSISTENCY = """\\
You are a continuity auditor. You receive:
- AKASHIC RECORDS (canonical rules)
- DYNAMIC MEMORY + CHARACTER ROSTER
- The newly written CHAPTER

Check for contradictions with established facts, names, rules or character \\
status. Report only real problems.

Output format:
- If everything is consistent: output exactly the single word OK
- If there are problems: list them as short bullet points (max 8). Be specific.
Do not rewrite the chapter. Do not invent issues.
"""


def format_chapter_summaries(summaries: list[tuple[int, str]]) -> str:
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
    character_roster: str = "",
    open_threads: str = "",
) -> str:
    sections = [f"=== AKASHIC RECORDS ===\n{akashic_records.strip()}"]

    if dynamic_memory and dynamic_memory.strip() and dynamic_memory.strip() != "Empty.":
        sections.append(
            "=== DYNAMIC LORE MEMORY ===\n"
            "Keep continuity with these newly established facts:\n"
            f"{dynamic_memory.strip()}"
        )

    if character_roster and character_roster.strip():
        sections.append(
            "=== CHARACTER ROSTER ===\n"
            "Current status of important characters. Respect their status and location:\n"
            f"{character_roster.strip()}"
        )

    if open_threads and open_threads.strip():
        sections.append(
            "=== OPEN THREADS ===\n"
            "Unresolved threads. Prefer advancing or closing the oldest ones when the premise allows:\n"
            f"{open_threads.strip()}"
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
    return (
        f"=== CHAPTER {chapter_num:02d} ===\n\n"
        f"{chapter_text.strip()}\n\n"
        "=== TASK ===\n"
        "Extract the continuity summary of this chapter following your "
        "instructions. Facts only, within the word limit."
    )


def build_updating_prompt(
    current_memory: str,
    chapter_summary: str,
    current_roster: str = "",
    current_threads: str = "",
    chapter_num: int = 0,
) -> str:
    return (
        "=== CURRENT DYNAMIC MEMORY ===\n"
        f"{current_memory if current_memory.strip() else 'Empty.'}\n\n"
        "=== CURRENT CHARACTER ROSTER ===\n"
        f"{current_roster if current_roster.strip() else 'Empty.'}\n\n"
        "=== CURRENT OPEN THREADS ===\n"
        f"{current_threads if current_threads.strip() else 'Empty.'}\n\n"
        f"=== SUMMARY OF NEW CHAPTER (Chapter {chapter_num:02d}) ===\n"
        f"{chapter_summary.strip()}\n\n"
        "=== TASK ===\n"
        "Produce the three updated sections following your instructions. "
        "Output exactly the three headers and their content."
    )


def build_compress_memory_prompt(current_memory: str) -> str:
    return (
        "=== CURRENT DYNAMIC MEMORY (too long) ===\n"
        f"{current_memory.strip()}\n\n"
        "=== TASK ===\n"
        "Compress it following your instructions. Output only the compressed memory."
    )


def build_merging_prompt(story_so_far: str, summaries: list[tuple[int, str]]) -> str:
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


def build_consistency_prompt(
    akashic: str,
    dynamic_memory: str,
    roster: str,
    chapter_text: str,
) -> str:
    return (
        "=== AKASHIC RECORDS ===\n"
        f"{akashic.strip()[:3000]}\n\n"
        "=== DYNAMIC MEMORY ===\n"
        f"{dynamic_memory.strip()[:1500] if dynamic_memory else 'Empty.'}\n\n"
        "=== CHARACTER ROSTER ===\n"
        f"{roster.strip()[:1500] if roster else 'Empty.'}\n\n"
        "=== NEW CHAPTER ===\n"
        f"{chapter_text.strip()[:6000]}\n\n"
        "=== TASK ===\n"
        "Audit continuity. Output OK or a short bullet list of real problems."
    )


def parse_updating_output(text: str) -> tuple[str, str, str]:
    memory, roster, threads = "", "", ""
    current = None
    lines = text.strip().splitlines()
    buf: list[str] = []

    def flush():
        nonlocal memory, roster, threads, buf
        content = "\n".join(buf).strip()
        if current == "memory":
            memory = content
        elif current == "roster":
            roster = content
        elif current == "threads":
            threads = content
        buf = []

    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("=== DYNAMIC MEMORY"):
            flush()
            current = "memory"
            continue
        if upper.startswith("=== CHARACTER ROSTER"):
            flush()
            current = "roster"
            continue
        if upper.startswith("=== OPEN THREADS"):
            flush()
            current = "threads"
            continue
        if current:
            buf.append(line)
    flush()

    return memory, roster, threads
