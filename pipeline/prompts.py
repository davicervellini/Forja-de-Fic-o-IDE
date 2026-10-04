"""
prompts.py — System prompts e construtores de prompt das fases do pipeline.

Os pedidos são em inglês: modelos pequenos seguem melhor instruções em inglês. O idioma de cada
resposta vai numa linha LANGUAGE no fim do pedido.

Fase 1 (Drafting):       escritor de rascunho, uma cena por vez
         (QA):           juiz da cena (itens da premissa e proibições) e juiz de pares
Fase 2 (Refining):       revisão com citação (contradições, repetições) e polimento de linha
Fase 3 (Summarizing):    analista de continuidade, ancorado no plano do capítulo
Fase 3.5 (Updating):     arquivista: só as mudanças de memória, roster, threads e callbacks
Fase 3.6 (Compress):     compressão da memória dinâmica (quando necessário)
Fase 4 (Merging):        fusão de resumos antigos
Fase 5 (Consistency):    conferência final do capítulo
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
or spellings. If the CHAPTER OUTLINE contradicts the AKASHIC RECORDS or what \
already happened in previous chapters, the AKASHIC RECORDS and the previous \
chapters win: silently drop the contradicting detail. Never invent illnesses, \
injuries, powers or characters that the records do not establish.
3. Follow the DYNAMIC LORE MEMORY and the CHARACTER ROSTER. They contain rules \
and characters established in previous chapters.
4. OPEN THREADS are for continuity: do not contradict them. Advance or close a \
thread only when the task asks for it.
5. Write only the requested scene. Do not write later scenes of the outline and \
do not jump ahead.
6. Keep continuity with THE STORY SO FAR, the PREVIOUS CHAPTER SUMMARIES, the \
PREVIOUS CHAPTER ENDING and THE CHAPTER SO FAR. Start exactly where the text \
before you stops. Never re-narrate or summarize what already happened.
7. Third person limited, past tense, anchored on the protagonist unless the \
outline says otherwise.
8. Concrete sentences. Show, don't tell. One precise detail beats \
three adjectives. Sensory description only when the scene needs it.
9. Dialogue in double quotes, one speaker per paragraph, tagged with "said" or \
nothing. Every character keeps the distinct voice described in the records or roster.
10. Light, humorous tone with serious moments. Humor comes from character \
and situation. The narrator never explains a joke or a comparison.
11. Rules and technology are shown through action and dialogue, never as a \
block of narrator explanation. When a character thinks aloud, write the words \
in quotes; never "he talked about X".
12. Length: write the number of words the task asks for. Develop the scene \
moment by moment with action, concrete detail, dialogue and the protagonist's \
reactions. Never compress the scene into a summary.
13. [System] lines: at most three in the whole chapter, only for messages the \
System shows without being asked. A command the protagonist types goes on its \
own line as "> command", and the reply follows as plain text.
14. Plain prose paragraphs of one to four sentences. No bullet points, no \
markdown headers, no metadata.\
"""

SYSTEM_SCENE_PLANNER = """\
You split a chapter premise into scenes for a web novel writer.
Output only a numbered list of scenes (as many as the premise needs, usually 2 to 6), one per line, in this exact format:
1. Short scene name. What happens, who is there, and what changes, in one or two sentences.
Keep every event of the premise, in order. Add nothing that contradicts the AKASHIC RECORDS.
After the list, write one last line: Hook: how the chapter ends.\
"""

SYSTEM_REFINING = """\
You are the line editor of a serialized web novel, in the language the LANGUAGE \
line at the end of the task asks for. You \
receive one draft scene and rewrite it for fluency, rhythm and correctness \
while preserving everything that happens.

MANDATORY RULES:
1. Keep every event, in the same order, every character and every line of \
dialogue with the same meaning. Do not add plot, characters, scenes or \
subplots. Do not cut events.
2. Keep all names and spellings exactly as in the STYLE AND SPELLING block. \
Fix any name the draft got wrong. Never capitalize an ordinary word to turn it \
into a listed name: "the heart of the city" stays lowercase.
3. Rewrite the sentences in the FIX LIST first. Elsewhere, edit lightly: fix \
what is clumsy, keep what already reads well. Do not split good sentences into \
short ones, do not start three sentences in a row with the same word, keep \
contractions and the characters' asides and jokes. Short sentences for tension, \
longer ones for calm moments.
4. Add sensory detail only where a scene needs it to be felt. Do not pad.
5. Keep each character's voice as described under VOICE. Do not homogenize.
6. Fix grammar, punctuation, unclosed quotes and tense consistency. Third \
person limited, past tense.
7. Keep the length within 10% of the draft. Never shorten the text into a summary.
8. Output only the edited text. No preamble, no comments about the \
editing, no notes, no title or heading that is not in the draft. Never add \
[System] lines that are not in the draft, and keep every [System] line and \
"> command" line of the draft.
9. Plain prose paragraphs. Dialogue in double quotes. No bullet points, no \
headers, no metadata.\
"""

SYSTEM_REVISE = """\
You are the continuity editor of a serialized web novel. You read ONE scene and \
point out only real defects, each with an exact quote and a fix:
- CONTRADICTION: the scene contradicts itself, the previous text or the scene plan.
- REPEAT: a passage repeats something already said in this scene or in the previous text.
- RENARRATION: the scene re-tells events the previous text already showed.
- MEANING: a sentence says something the plan did not mean (wrong word, bad translation, calque).
- FORBIDDEN: something from the MUST NOT APPEAR list is in the scene.
- CANON: a fact contradicts the CANON block.
Do not report style preferences. Do not invent defects. At most 6 edits.
Answer only with JSON in this shape:
{"edits": [{"kind": "REPEAT", "quote": "exact sentence copied from the scene", \
"replacement": "the corrected sentence, or an empty string to delete it", "reason": "short reason"}]}
If there is nothing to fix, answer {"edits": []}.\
"""

_SYSTEM_SUMMARIZING_TEMPLATE = """\
You are the continuity analyst of a serialized web novel. You extract a \
compact, factual summary of one chapter so later chapters stay consistent.

Write in the language the LANGUAGE line asks for. Use these numbered sections with short bullet points:
0. PLAN CHECK: for each PLANNED BEAT, write "shown", "changed" or "missing", with a short quote.
1. KEY EVENTS: what happened, in order. Facts only.
2. CHARACTER STATE: where each named character is at the end, and any \
change in them.
3. RELATIONSHIPS: alliances, tensions, trust gained or lost.
4. NEW FACTS: rules, places, technology, names or lore revealed for the \
first time.
5. OPEN THREADS: promises, debts, threats, unanswered questions. For each, \
note if it is new or continuing.
6. OFF-PLAN: named characters, places or System commands in the text that are \
not in the PLANNED BEATS and not in the ALLOWED CHARACTERS list. Write "none" if there are none.

Hard limit: {max_words} words for sections 1 to 5. Facts come only from the chapter text. \
No opinions, no adjectives. You may quote short concrete details worth calling back later \
(a number, a repeated line). Use the official spellings exactly. Output only the summary.\
"""

SYSTEM_SUMMARIZING = _SYSTEM_SUMMARIZING_TEMPLATE.format(
    max_words=config.SUMMARY_MAX_WORDS
)

SYSTEM_UPDATING = """\
You are the Lore Archivist and Continuity Keeper of a serialized web novel. You \
receive the current DYNAMIC MEMORY, CHARACTER ROSTER, OPEN THREADS and CALLBACKS, \
plus the SUMMARY OF THE NEW CHAPTER. You output ONLY the changes the new chapter \
makes, one per line, using exactly these forms:

MEMORY ADD: a new permanent fact (new rule, new recurring place, permanent change)
MEMORY REPLACE: an existing memory line, copied exactly => the corrected line
ROSTER NEW: Name | status | current location | role or relationship to the protagonist | one-line voice note
ROSTER UPDATE: Name | Field (Status, Location, Role or Voice) | new value
THREAD NEW: a new unresolved promise, debt, threat or mystery
THREAD RESOLVED: an existing thread, copied exactly | EVIDENCE: the words of the summary that resolve it
CALLBACK: a short concrete detail worth calling back later (a number, an object, a repeated line)
END STATE: where the protagonist is and what is happening at the exact end of the chapter

Rules: only what the summary states. Do not repeat what the documents already say. \
Every existing line you do not mention stays as it is. Only characters from the \
ALLOWED CHARACTERS list may enter the roster. Always output one END STATE line. \
If nothing else changed, output only the END STATE line.\
"""

SYSTEM_COMPRESS_MEMORY = """\
You are a Lore Archivist. The Dynamic Memory has grown too large.
Compress it while keeping every permanent fact that future chapters still need.
Merge redundant entries, drop anything that is no longer relevant, keep names \
and rules intact. Keep the "End of Ch." line first and unchanged. \
Output only the compressed Dynamic Memory as Markdown bullets.
Hard limit: roughly {max_words} words.
""".format(max_words=config.DYNAMIC_MEMORY_MAX_WORDS)

_SYSTEM_MERGING_TEMPLATE = """\
You are the continuity analyst of a serialized web novel. You merge an \
existing \"story so far\" with the summaries of the chapters that followed it \
into one updated \"story so far\".

Write in the language the LANGUAGE line asks for. Keep only what future chapters need: the current state of \
the world, of each named character, of relationships, and every open thread. \
Drop scene-by-scene detail. Never invent anything not present in the inputs. \
Use the official spellings exactly. \
Hard limit: {max_words} words. Output only the merged text, as short bullet \
points grouped under: WORLD STATE, CHARACTERS, RELATIONSHIPS, OPEN THREADS.\
"""

SYSTEM_MERGING = _SYSTEM_MERGING_TEMPLATE.format(
    max_words=config.STORY_SO_FAR_MAX_WORDS
)

SYSTEM_CONSISTENCY = """\
You are a continuity auditor of a serialized web novel. You receive the CANON \
for this chapter, the list of characters allowed in it, the MUST NOT APPEAR list, \
the memory of earlier chapters and ONE scene of the new chapter. Report only real \
problems: characters that are not allowed, forbidden things, contradictions with \
the canon or with established facts, names spelled differently from the canon. \
Every problem needs an exact quote from the scene.
Answer only with JSON in this shape:
{"violations": [{"rule": "short description", "quote": "exact words copied from the scene"}]}
If there are no problems, answer {"violations": []}.\
"""

SYSTEM_QA_JUDGE = """\
You check one scene of a web novel against its plan. You receive the numbered \
REQUIRED ITEMS of the scene, the MUST NOT APPEAR list and the scene text.
1. For each required item, decide if the scene shows it (in any wording). List \
the numbers of the items that are NOT shown.
2. List every forbidden thing that appears, with an exact quote from the scene.
Answer only with JSON in this shape:
{"missing": [2, 5], "violations": [{"rule": "which forbidden item", "quote": "exact words from the scene"}]}\
"""

SYSTEM_QA_PAIRWISE = """\
You compare two versions, A and B, of the same scene of a web novel. Pick the \
one that better carries out the scene plan, keeps the protagonist's voice and \
continues naturally from the last sentence before it. Ignore length.
Answer only with JSON: {"winner": "A" or "B", "reason": "one short sentence"}\
"""

SYSTEM_PREMISE_TRANSLATE = """\
You translate a chapter brief for a novel into {language}. Keep the exact structure: every \
line, the numbering, the labels (Chapter Premise, Goal, Opening, Characters in scene, \
Locations in scene, Scenes, Hook, Must include, Must not appear) and the "(about N words)" \
targets that are already there. Never add numbers, counts or notes of your own. Translate every field, including Must not appear. Use the GLOSSARY for the \
story's own terms: those words have a fixed meaning in this story (a word in the glossary \
never takes its dictionary meaning). Use the PREVIOUS CHAPTER to understand callbacks to \
earlier events and keep them recognizable. Keep commands, quotes and [System] lines as \
written. Translate meaning, not word by word. Output only the translated brief."""


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
    callbacks: str = "",
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
            "Unresolved threads, for continuity. Do not contradict them:\n"
            f"{open_threads.strip()}"
        )

    if callbacks and callbacks.strip():
        sections.append(
            "=== CALLBACKS AND MOTIFS ===\n"
            "Concrete details from earlier chapters. A callback to one of them beats a new metaphor:\n"
            f"{callbacks.strip()}"
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


def scene_title(scene_text: str) -> str:
    """Nome curto da cena: a primeira frase da descrição ("The transporter.")."""
    import re
    first = re.split(r"(?<=[.!?])\s", (scene_text or "").strip(), maxsplit=1)[0]
    return " ".join(first.split()[:12]).rstrip(".") + "."


def end_moment(scene_text: str) -> str:
    """Última frase da descrição da cena: o momento em que ela termina."""
    import re
    text = re.sub(r"\s*\((?:about|around|~|cerca de)?\s*\d[\d.,]*\s*(?:words|palavras)\)\s*\.?", "", scene_text or "")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if len(sentences) < 2:
        return ""
    return sentences[-1].strip()


def chapter_outline(goal: str, scene_texts: list[str], current: int, hook: str = "") -> str:
    """
    Roteiro compacto do capítulo: cenas anteriores e a atual por inteiro, as seguintes só pelo
    nome. Com a premissa inteira à vista, o modelo adianta o que é das cenas seguintes.
    """
    total = len(scene_texts)
    lines = []
    if goal.strip() and current == total:
        lines.append(f"Goal of the chapter: {goal.strip()}")
    for i, text in enumerate(scene_texts, start=1):
        if i < current:
            lines.append(f"Scene {i} (already written): {text.strip()}")
        elif i == current:
            lines.append(f"Scene {i} (THIS SCENE): {text.strip()}")
        else:
            lines.append(f"Scene {i}: {scene_title(text)} (later; not yet)")
    if hook.strip() and current == total:
        lines.append(f"Hook: {hook.strip()}")
    return "\n".join(lines)


def build_scene_prompt(
    akashic_records: str,
    chapter_num: int,
    scene_num: int,
    scene_total: int,
    scene_text: str,
    target_words: int,
    outline: str = "",
    premise: str = "",
    hook: str = "",
    story_so_far: str = "",
    recent_summaries: list[tuple[int, str]] | None = None,
    dynamic_memory: str = "",
    character_roster: str = "",
    open_threads: str = "",
    callbacks: str = "",
    previous_chapter_tail: str = "",
    chapter_so_far: str = "",
    scene_so_far: str = "",
    guidance: str = "",
    next_chapter_opening: str = "",
) -> str:
    """
    Prompt de UMA cena. A parte fixa (registro do capítulo, memória, resumos) vem primeiro e é
    igual em todas as cenas do capítulo, para o Ollama reaproveitar o cache do prompt; o que
    muda (roteiro, texto já escrito e tarefa) vem no fim.
    `outline`: roteiro compacto (chapter_outline); sem ele, vai a premissa inteira.
    `scene_so_far` preenchido pede a continuação de uma cena que parou cedo demais.
    `next_chapter_opening` é a abertura da premissa do capítulo seguinte, quando o usuário já
    a escreveu: a última cena termina onde o próximo capítulo começa, sem escrever nada dele.
    """
    from pipeline.scenes import similarity

    base = build_drafting_prompt(
        premise="",
        akashic_records=akashic_records,
        story_so_far=story_so_far,
        recent_summaries=recent_summaries,
        dynamic_memory=dynamic_memory,
        character_roster=character_roster,
        open_threads=open_threads,
        callbacks=callbacks,
    )
    # build_drafting_prompt termina com a premissa e a instrução de capítulo inteiro; aqui elas
    # dão lugar ao roteiro do capítulo e à tarefa da cena.
    base = base.rsplit("\n\n=== CHAPTER PREMISE ===", 1)[0]
    sections = [base]
    if outline.strip():
        sections.append(f"=== CHAPTER OUTLINE (Chapter {chapter_num}) ===\n{outline.strip()}")
    elif premise.strip():
        sections.append(f"=== CHAPTER PREMISE ===\n{premise.strip()}")

    # Com o capítulo já começado, o fim do anterior só repetiria o que THE CHAPTER SO FAR ancora.
    if previous_chapter_tail.strip() and not chapter_so_far.strip():
        sections.append(
            f"=== PREVIOUS CHAPTER ENDING (end of Chapter {chapter_num - 1}) ===\n"
            "The story continues right after this text:\n"
            f"{previous_chapter_tail.strip()}"
        )
    if chapter_so_far.strip():
        sections.append(
            f"=== THE CHAPTER SO FAR (what is already written in Chapter {chapter_num}) ===\n"
            f"{chapter_so_far.strip()}"
        )
    if scene_so_far.strip():
        sections.append(f"=== THIS SCENE SO FAR ===\n{scene_so_far.strip()}")

    is_last = scene_num == scene_total
    task = ["=== YOUR TASK ===", f"Chapter {chapter_num}, scene {scene_num} of {scene_total}:", scene_text.strip(), ""]
    if scene_so_far.strip():
        task.append(
            f"The scene above stopped too early. Continue it from its last sentence, without "
            f"repeating anything, until this scene's beat is complete: about {target_words} more words."
        )
    elif chapter_so_far.strip():
        task.append("Continue directly from where THE CHAPTER SO FAR stops. Do not repeat or summarize it.")
    elif previous_chapter_tail.strip():
        task.append("Start right after the PREVIOUS CHAPTER ENDING. Do not re-narrate it.")
    task.append("Write only this scene. Do not write any later scene of the outline.")
    if is_last:
        if hook.strip():
            task.append(f"This is the last scene: end the chapter on this hook: {hook.strip()}")
        else:
            task.append("This is the last scene: end the chapter on a hook or a decision.")
        if next_chapter_opening.strip():
            hook_end = end_moment(hook) or hook.strip()
            if hook.strip() and similarity(next_chapter_opening, hook) > 0.15:
                task.append(f'Your last paragraph is the hook\'s final image ("{hook_end}"). End there: no '
                            "reaction, no reflection, no summary after it. "
                            f"Chapter {chapter_num + 1} continues from that exact moment.")
            else:
                task.append(f"The NEXT chapter (Chapter {chapter_num + 1}), which is NOT yours to write, opens like "
                            f"this: {next_chapter_opening.strip()} End this chapter so that the next one can start "
                            "exactly there: same place, same people, same situation. Do not write any of it.")
    else:
        moment = end_moment(scene_text)
        if moment:
            task.append(f'This scene ends on this moment: "{moment}" Write that moment, then stop. '
                        "Do not end the chapter.")
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
    "little did he know", "couldn't help but", "sent shivers", "his mind raced",
]

KEY_RULES = (
    "Key rules: third person limited, past tense. Show, don't tell. Dialogue in double quotes, tagged "
    "with \"said\" or nothing; no descriptions of how a voice sounds. The narrator never explains a joke "
    "or a comparison. Thinking aloud goes in quotes. Only the characters, places and things of this "
    "chapter appear. Paragraphs of one to four sentences."
)

TERMINAL_RULE = (
    "Terminal format: a command the protagonist types goes on its own line as \"> command\", and the "
    "System's reply follows as plain text on the next line(s). A \"[System]\" line is only for a message "
    "the System shows without being asked."
)


def scene_guidance(next_scene_title: str = "", last_written_sentence: str = "", protagonist_voice: str = "",
                   voice_samples: list[str] | None = None, system_left: int | None = None,
                   terminal: bool = False) -> str:
    """
    Bloco final do prompt de cena: última frase escrita, nome da próxima cena, voz do protagonista
    (com falas de exemplo), linhas [System] que ainda cabem e as regras principais repetidas.
    """
    lines = []
    if last_written_sentence.strip():
        lines.append(f'The last sentence already written is: "{last_written_sentence.strip()}" '
                     "Your first sentence must come right after it. Never describe again anything already written.")
    if next_scene_title.strip():
        lines.append(f"The next scene ({next_scene_title.strip()}) is not yours: stop before it begins.")
    if protagonist_voice.strip():
        lines.append(f"Protagonist's voice: {protagonist_voice.strip()} "
                     "Give the protagonist at least one line of dialogue or inner remark in that voice.")
    if voice_samples:
        lines.append("Voice samples from earlier chapters (match this register; never reuse these lines):\n"
                     + "\n".join(f"- {s}" for s in voice_samples))
    lines.append("Show emotions through actions and details, not by naming them. Never use these worn phrases: "
                 + "; ".join(f'"{p}"' for p in WORN_PHRASES) + ".")
    lines.append("Never repeat a sentence or an image you already used.")
    if system_left is not None:
        lines.append(f"[System] lines left in this chapter: {max(system_left, 0)}."
                     + (" Do not write any." if system_left <= 0 else ""))
    if terminal:
        lines.append(TERMINAL_RULE)
    lines.append(KEY_RULES)
    return "\n".join(lines)


def build_planner_prompt(premise: str, akashic_records: str) -> str:
    return (
        f"=== AKASHIC RECORDS ===\n{akashic_records.strip()}\n\n"
        f"=== CHAPTER PREMISE ===\n{premise.strip()}\n\n"
        "=== TASK ===\nSplit this premise into numbered scenes (usually 2 to 6), then write the Hook line."
    )


def build_refining_prompt(draft: str, style_block: str = "", scene_label: str = "", voice: str = "",
                          fix_list: list[tuple[str, str]] | None = None) -> str:
    sections = []
    if style_block and style_block.strip():
        sections.append(f"=== STYLE AND SPELLING ===\n{style_block.strip()}")
    if voice and voice.strip():
        sections.append(f"=== VOICE ===\n{voice.strip()}")
    sections.append(f"=== DRAFT ===\n{draft.strip()}")
    if scene_label:
        task = (f"This draft is {scene_label}. Edit only this text following your editor rules. "
                "Keep every event and every line of dialogue, keep about the same length, and do not "
                "add a title or a heading. Output only the edited text.")
    else:
        task = ("Rewrite the draft following your editor rules. Keep every event and "
                "every line of dialogue. Output only the edited chapter.")
    if fix_list:
        task += ("\n\nFIX LIST — rewrite these exact passages (change nothing else around them):\n"
                 + "\n".join(f'- "{q}" ({why})' for q, why in fix_list))
    sections.append(f"=== TASK ===\n{task}")
    return "\n\n".join(sections)


def build_revise_prompt(scene: str, plan: str, must_not: str, canon: str, previous: str, following: str) -> str:
    """Revisão com citação de uma cena: plano da cena, proibições, cânone e os vizinhos."""
    parts = []
    if canon.strip():
        parts.append(f"=== CANON ===\n{canon.strip()}")
    if plan.strip():
        parts.append(f"=== SCENE PLAN ===\n{plan.strip()}")
    if must_not.strip():
        parts.append(f"=== MUST NOT APPEAR ===\n{must_not.strip()}")
    if previous.strip():
        parts.append(f"=== PREVIOUS TEXT (already final; do not edit) ===\n{previous.strip()}")
    parts.append(f"=== SCENE TO CHECK ===\n{scene.strip()}")
    if following.strip():
        parts.append(f"=== NEXT SCENE STARTS WITH (do not edit) ===\n{following.strip()}")
    parts.append("=== TASK ===\nList the defects of SCENE TO CHECK with exact quotes and fixes, as JSON.")
    return "\n\n".join(parts)


def build_judge_prompt(scene: str, items: list[str], must_not: str) -> str:
    numbered = "\n".join(f"{i}. {it}" for i, it in enumerate(items, start=1)) or "(none)"
    return (f"=== REQUIRED ITEMS ===\n{numbered}\n\n"
            f"=== MUST NOT APPEAR ===\n{must_not.strip() or '(none)'}\n\n"
            f"=== SCENE ===\n{scene.strip()}\n\n"
            "=== TASK ===\nAnswer with the JSON described in your instructions.")


def build_pairwise_prompt(plan: str, before: str, a: str, b: str) -> str:
    return (f"=== SCENE PLAN ===\n{plan.strip()}\n\n"
            f"=== TEXT BEFORE THE SCENE ===\n{before.strip() or '(start of the chapter)'}\n\n"
            f"=== VERSION A ===\n{a.strip()}\n\n=== VERSION B ===\n{b.strip()}\n\n"
            "=== TASK ===\nWhich version is better? Answer with the JSON described in your instructions.")


def build_summarizing_prompt(chapter_text: str, chapter_num: int, planned_beats: list[str] | None = None,
                             allowed: list[str] | None = None, spellings: str = "") -> str:
    parts = []
    if planned_beats:
        parts.append("=== PLANNED BEATS ===\n" + "\n".join(f"- {b}" for b in planned_beats))
    if allowed:
        parts.append("=== ALLOWED CHARACTERS ===\n" + ", ".join(allowed))
    if spellings.strip():
        parts.append(f"=== OFFICIAL SPELLINGS ===\n{spellings.strip()}")
    parts.append(f"=== CHAPTER {chapter_num:02d} ===\n\n{chapter_text.strip()}")
    parts.append(
        "=== TASK ===\n"
        "Extract the continuity summary of this chapter following your "
        "instructions. Facts only, within the word limit."
    )
    return "\n\n".join(parts)


def build_updating_prompt(
    current_memory: str,
    chapter_summary: str,
    current_roster: str = "",
    current_threads: str = "",
    chapter_num: int = 0,
    current_callbacks: str = "",
    allowed: list[str] | None = None,
) -> str:
    parts = [
        "=== CURRENT DYNAMIC MEMORY ===\n" + (current_memory.strip() or "Empty."),
        "=== CURRENT CHARACTER ROSTER ===\n" + (current_roster.strip() or "Empty."),
        "=== CURRENT OPEN THREADS ===\n" + (current_threads.strip() or "Empty."),
        "=== CURRENT CALLBACKS ===\n" + (current_callbacks.strip() or "Empty."),
    ]
    if allowed:
        parts.append("=== ALLOWED CHARACTERS ===\n" + ", ".join(allowed))
    parts.append(f"=== SUMMARY OF NEW CHAPTER (Chapter {chapter_num:02d}) ===\n{chapter_summary.strip()}")
    parts.append("=== TASK ===\nOutput only the change lines described in your instructions, one per line.")
    return "\n\n".join(parts)


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
    allowed: list[str] | None = None,
    must_not: str = "",
) -> str:
    """Conferência de UMA cena do capítulo pronto, com o cânone do capítulo inteiro (sem cortes)."""
    return (
        f"=== CANON ===\n{akashic.strip()}\n\n"
        f"=== CHARACTERS ALLOWED IN THIS CHAPTER ===\n{', '.join(allowed or []) or '(see canon)'}\n\n"
        f"=== MUST NOT APPEAR ===\n{must_not.strip() or '(none)'}\n\n"
        f"=== DYNAMIC MEMORY ===\n{dynamic_memory.strip() if dynamic_memory else 'Empty.'}\n\n"
        f"=== CHARACTER ROSTER ===\n{roster.strip() if roster else 'Empty.'}\n\n"
        f"=== SCENE ===\n{chapter_text.strip()}\n\n"
        "=== TASK ===\nAudit this scene. Answer with the JSON described in your instructions."
    )


def strip_summary_checks(summary: str) -> tuple[str, str, str]:
    """
    (resumo sem as seções 0 e 6, PLAN CHECK, OFF-PLAN). As duas seções servem à conferência do
    capítulo; nos prompts dos capítulos seguintes seriam só ruído.
    """
    import re
    plan, off = [], []
    kept: list[str] = []
    current = kept
    for line in (summary or "").splitlines():
        head = re.match(r"^\s*(?:#+\s*)?\**\s*(\d)\s*[.)]\s*\**\s*([A-Z]{3,}[A-Z -]*)\b", line)
        if head:
            num = head.group(1)
            current = plan if num == "0" else off if num == "6" else kept
        current.append(line)
    return "\n".join(kept).strip(), "\n".join(plan).strip(), "\n".join(off).strip()


def parse_updating_output(text: str) -> tuple[str, str, str]:
    """Formato antigo (três seções reescritas inteiras), lido só como reserva."""
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
