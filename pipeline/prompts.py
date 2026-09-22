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

SYSTEM_DRAFTING = """\
You are the drafting writer of a serialized web novel, written in the language \
the LANGUAGE line at the end of each task asks for. \
You write a chapter one scene at a time: each request asks for ONE scene, and \
you write only that scene, fully developed, as narrative prose.

MANDATORY RULES:
1. Write only the prose of the requested scene. No title, no scene number, no \
headings, no preamble, no notes, no summary at the end, no \"to be continued\".
2. Follow the AKASHIC RECORDS exactly. Never contradict its rules, facts, names \
or spellings. If the CHAPTER PREMISE contradicts the AKASHIC RECORDS or what \
already happened in previous chapters, the AKASHIC RECORDS and the previous \
chapters win: silently drop the contradicting detail. Never invent illnesses, \
injuries, powers or characters that the records do not establish.
3. Follow the DYNAMIC LORE MEMORY and the CHARACTER ROSTER. They contain rules \
and characters established in previous chapters.
4. Respect OPEN THREADS: do not ignore long-standing unresolved threads without \
reason. Prefer to advance or close the oldest ones when the premise allows.
5. Follow the CHAPTER PREMISE for the requested scene only. Do not write later \
scenes of the premise and do not jump ahead.
6. Keep continuity with THE STORY SO FAR, the PREVIOUS CHAPTER SUMMARIES, the \
PREVIOUS CHAPTER ENDING and THE CHAPTER SO FAR. Start exactly where the text \
before you stops. Never re-narrate or summarize what already happened.
7. Third person limited, past tense, anchored on the protagonist unless the \
premise says otherwise.
8. Short, concrete sentences. Show, don't tell. One precise detail beats \
three adjectives. Sensory description only when the scene needs it.
9. Dialogue in double quotes, one speaker per paragraph. Every character \
keeps the distinct voice described in the akashic records or roster.
10. Light, humorous tone with serious moments. Humor comes from character \
and situation, never from explained jokes.
11. Rules and technology are shown through action and dialogue, never as a \
block of narrator explanation.
12. Length: write the number of words the task asks for. Develop the scene \
moment by moment with action, concrete detail, dialogue and the protagonist's \
reactions. Never compress the scene into a summary.
13. At most one [System] line per scene, and only when the scene needs it.
14. Plain prose paragraphs. No bullet points, no markdown headers, no metadata.\
"""

SYSTEM_SCENE_PLANNER = """\
You split a chapter premise into scenes for a web novel writer.
Output only a numbered list of 3 or 4 scenes, one per line, in this exact format:
1. Short scene name. What happens, who is there, and what changes, in one or two sentences.
Keep every event of the premise, in order. Add nothing that contradicts the AKASHIC RECORDS.
After the list, write one last line: Hook: how the chapter ends.\
"""

SYSTEM_REFINING = """\
You are the line editor of a serialized web novel, in the language the LANGUAGE \
line at the end of the task asks for. You \
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
7. Keep the length within 10% of the draft. Never shorten the text into a summary.
8. Output only the edited text. No preamble, no comments about the \
editing, no notes, no title or heading that is not in the draft. Never add \
[System] lines that are not in the draft.
9. Plain prose paragraphs. Dialogue in double quotes. No bullet points, no \
headers, no metadata.\
"""

_SYSTEM_SUMMARIZING_TEMPLATE = """\
You are the continuity analyst of a serialized web novel. You extract a \
compact, factual summary of one chapter so later chapters stay consistent.

Write in the language the LANGUAGE line asks for. Use these numbered sections with short bullet points:
1. KEY EVENTS: what happened, in order. Facts only.
2. CHARACTER STATE: where each named character is at the end, and any \
change in them.
3. RELATIONSHIPS: alliances, tensions, trust gained or lost.
4. NEW FACTS: rules, places, technology, names or lore revealed for the \
first time.
5. OPEN THREADS: promises, debts, threats, unanswered questions. For each, \
note if it is new or continuing.

Hard limit: {max_words} words in total. No opinions, no adjectives, no \
quotes from the text. Output only the summary.\
"""

SYSTEM_SUMMARIZING = _SYSTEM_SUMMARIZING_TEMPLATE.format(
    max_words=config.SUMMARY_MAX_WORDS
)

SYSTEM_UPDATING = """\
You are a Lore Archivist and Continuity Keeper for a serialized web novel.
You maintain three living documents:

A) DYNAMIC MEMORY — only permanent new facts (new recurring characters, \
new rules, permanent physical changes to the setting). Extremely concise \
Markdown bullets. Do not include passing events.

B) CHARACTER ROSTER — a living list of important characters. For each:
   - Name
   - Status (alive / dead / missing / unknown)
   - Current location or last known place
   - Brief role / relationship to protagonist
   - One-line personality or voice note
Keep it under a reasonable size. Remove or mark characters that are clearly \
irrelevant forever. Update status and location from the new summary.

C) OPEN THREADS — unresolved promises, debts, threats, mysteries.
   Format each as: [Ch.XX] description
   Keep the chapter number where it first appeared.
   Remove threads that were clearly resolved in the new summary.
   Keep the oldest and most important ones. Hard limit around 15 threads.

You will receive the CURRENT versions of all three + the SUMMARY OF THE NEW CHAPTER.
Keep the section headers and the roster field labels (Name, Status, ...) exactly in English, even when
the content is written in another language.
Output exactly three sections, nothing else:

=== DYNAMIC MEMORY ===
...updated content...

=== CHARACTER ROSTER ===
...updated content...

=== OPEN THREADS ===
...updated content...

If a section has no change, still output it with the previous content.
"""

SYSTEM_COMPRESS_MEMORY = """\
You are a Lore Archivist. The Dynamic Memory has grown too large.
Compress it while keeping every permanent fact that future chapters still need.
Merge redundant entries, drop anything that is no longer relevant, keep names \
and rules intact. Output only the compressed Dynamic Memory as Markdown bullets.
Hard limit: roughly {max_words} words.
""".format(max_words=config.DYNAMIC_MEMORY_MAX_WORDS)

_SYSTEM_MERGING_TEMPLATE = """\
You are the continuity analyst of a serialized web novel. You merge an \
existing \"story so far\" with the summaries of the chapters that followed it \
into one updated \"story so far\".

Write in the language the LANGUAGE line asks for. Keep only what future chapters need: the current state of \
the world, of each named character, of relationships, and every open thread. \
Drop scene-by-scene detail. Never invent anything not present in the inputs. \
Hard limit: {max_words} words. Output only the merged text, as short bullet \
points grouped under: WORLD STATE, CHARACTERS, RELATIONSHIPS, OPEN THREADS.\
"""

SYSTEM_MERGING = _SYSTEM_MERGING_TEMPLATE.format(
    max_words=config.STORY_SO_FAR_MAX_WORDS
)

SYSTEM_CONSISTENCY = """\
You are a continuity auditor. You receive:
- AKASHIC RECORDS (canonical rules)
- DYNAMIC MEMORY + CHARACTER ROSTER
- The newly written CHAPTER

Check for contradictions with established facts, names, rules or character \
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


def build_scene_prompt(
    premise: str,
    akashic_records: str,
    chapter_num: int,
    scene_num: int,
    scene_total: int,
    scene_text: str,
    target_words: int,
    hook: str = "",
    story_so_far: str = "",
    recent_summaries: list[tuple[int, str]] | None = None,
    dynamic_memory: str = "",
    character_roster: str = "",
    open_threads: str = "",
    previous_chapter_tail: str = "",
    chapter_so_far: str = "",
    scene_so_far: str = "",
    guidance: str = "",
    next_chapter_opening: str = "",
) -> str:
    """
    Prompt de UMA cena. A parte fixa (registro, memória, resumos, final do capítulo
    anterior, premissa) vem primeiro e é igual em todas as cenas do capítulo, para o
    Ollama reaproveitar o cache do prompt; o que muda (texto já escrito e tarefa) vem no fim.
    `scene_so_far` preenchido pede a continuação de uma cena que parou cedo demais.
    `next_chapter_opening` é a abertura da premissa do capítulo seguinte, quando o usuário já
    a escreveu: a última cena termina onde o próximo capítulo começa, sem escrever nada dele.
    """
    base = build_drafting_prompt(
        premise=premise,
        akashic_records=akashic_records,
        story_so_far=story_so_far,
        recent_summaries=recent_summaries,
        dynamic_memory=dynamic_memory,
        character_roster=character_roster,
        open_threads=open_threads,
    )
    # build_drafting_prompt termina com a instrução de capítulo inteiro; aqui ela é trocada pela de cena.
    base = base.rsplit("\n\nWrite the complete chapter now", 1)[0]
    sections = [base]

    if previous_chapter_tail.strip():
        sections.append(
            f"=== PREVIOUS CHAPTER ENDING (end of Chapter {chapter_num - 1}) ===\n"
            "The story continues right after this text:\n"
            f"{previous_chapter_tail.strip()}"
        )
    if chapter_so_far.strip():
        sections.append(
            f"=== THE CHAPTER SO FAR (end of what is already written in Chapter {chapter_num}) ===\n"
            f"{chapter_so_far.strip()}"
        )
    if scene_so_far.strip():
        sections.append(f"=== THIS SCENE SO FAR ===\n{scene_so_far.strip()}")

    is_last = scene_num == scene_total
    task = [f"=== YOUR TASK ===", f"Chapter {chapter_num}, scene {scene_num} of {scene_total}:", scene_text.strip(), ""]
    if scene_so_far.strip():
        task.append(
            f"The scene above stopped too early. Continue it from its last sentence, without "
            f"repeating anything, until this scene's beat is complete: about {target_words} more words."
        )
    elif chapter_so_far.strip():
        task.append("Continue directly from where THE CHAPTER SO FAR stops. Do not repeat or summarize it.")
    elif previous_chapter_tail.strip():
        task.append("Start right after the PREVIOUS CHAPTER ENDING. Do not re-narrate it.")
    task.append("Write only this scene. Do not write any later scene of the premise.")
    if is_last:
        task.append(f"This is the last scene: end the chapter on this hook: {hook.strip()}" if hook.strip()
                    else "This is the last scene: end the chapter on a hook or a decision.")
        if next_chapter_opening.strip():
            task.append(f"The NEXT chapter (Chapter {chapter_num + 1}), which is NOT yours to write, opens like this: "
                        f"{next_chapter_opening.strip()} End this chapter so that the next one can start exactly "
                        "there: same place, same people, same situation. Do not write any of it.")
    else:
        task.append("Stop when this scene's beat is complete. Do not end the chapter.")
    if not scene_so_far.strip():
        task.append(f"Length: about {target_words} words. Develop it moment by moment; never summarize.")
    task.append("No title, no scene number, no headings. Plain prose only.")
    if guidance.strip():
        # No fim do prompt: modelos pequenos obedecem mais ao que leem por último.
        task.append(guidance.strip())
    sections.append("\n".join(task))
    return "\n\n".join(sections)


# Frases gastas que modelos pequenos repetem sem parar. Vão no fim do prompt de cada cena.
WORN_PHRASES = [
    "a sense of", "eyes widened", "heart skipped a beat", "heart beat faster", "a mix of",
    "shiver ran down his spine", "felt a surge", "something about this felt right",
    "little did he know", "couldn't help but", "sent shivers",
]


def scene_guidance(next_scene_text: str = "", last_written_sentence: str = "", protagonist_voice: str = "") -> str:
    """Bloco final do prompt de cena: fronteira da próxima cena, última frase escrita e voz do protagonista."""
    lines = []
    if last_written_sentence.strip():
        lines.append(f'The last sentence already written is: "{last_written_sentence.strip()}" '
                     "Your first sentence must come right after it. Never describe again anything already written.")
    if next_scene_text.strip():
        lines.append(f"The NEXT scene, which is NOT yours to write, is: {next_scene_text.strip()} "
                     "Stop before anything of it happens.")
    if protagonist_voice.strip():
        lines.append(f"Protagonist's voice and personality: {protagonist_voice.strip()} "
                     "Give the protagonist at least one line of dialogue or inner remark in that voice.")
    lines.append("Show emotions through actions and details, not by naming them. Never use these worn phrases: "
                 + "; ".join(f'"{p}"' for p in WORN_PHRASES) + ".")
    lines.append("Never repeat a sentence or an image you already used.")
    return "\n".join(lines)


def build_planner_prompt(premise: str, akashic_records: str) -> str:
    return (
        f"=== AKASHIC RECORDS ===\n{akashic_records.strip()}\n\n"
        f"=== CHAPTER PREMISE ===\n{premise.strip()}\n\n"
        "=== TASK ===\nSplit this premise into 3 or 4 numbered scenes, then write the Hook line."
    )


def build_refining_prompt(draft: str, style_block: str = "", scene_label: str = "") -> str:
    sections = []
    if style_block and style_block.strip():
        sections.append(f"=== STYLE AND SPELLING ===\n{style_block.strip()}")
    sections.append(f"=== DRAFT ===\n{draft.strip()}")
    if scene_label:
        task = (f"This draft is {scene_label}. Edit only this text following your editor rules. "
                "Keep every event and every line of dialogue, keep about the same length, and do not "
                "add a title or a heading. Output only the edited text.")
    else:
        task = ("Rewrite the draft following your editor rules. Keep every event and "
                "every line of dialogue. Output only the edited chapter.")
    sections.append(f"=== TASK ===\n{task}")
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
