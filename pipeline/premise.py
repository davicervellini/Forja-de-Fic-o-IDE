"""
premise.py — Premissa guiada de um capítulo.

A qualidade do capítulo depende quase toda da premissa. Este módulo dá a ela campos fixos
(título, objetivo, abertura, cenas com meta de palavras, personagens em cena, o que precisa
e o que não pode aparecer, gancho), converte o formulário no texto que o pipeline lê
(parse_premise continua funcionando) e monta os pedidos para o modelo sugerir ou conferir
uma premissa antes de gerar.

Formato do texto:

    Chapter Premise: Chapter 2: The City Under the Sea

    Goal: ...
    Opening: ...
    Characters in scene: Arthur Galhardo

    Scenes:
    1. ... (about 700 words)
    2. ... (about 800 words)

    Hook: ...
    Must include: ...
    Must not appear: ...
"""

import re
from dataclasses import asdict, dataclass, field

from pipeline.scenes import parse_premise

LABELS = {
    "goal": ("goal", "objetivo"),
    "opening": ("opening", "abertura", "starts", "começa"),
    "characters": ("characters in scene", "personagens em cena", "characters"),
    "hook": ("hook", "gancho"),
    "must_include": ("must include", "must appear", "precisa aparecer", "precisa"),
    "must_not": ("must not appear", "must not", "forbidden", "não pode aparecer", "nao pode aparecer"),
}
_WORDS_SUFFIX = re.compile(r"\s*\((?:about|around|~|cerca de)?\s*(\d[\d.,]*)\s*(?:words|palavras)\)\s*\.?\s*$", re.I)
_WORDS_INLINE = re.compile(r"\s*\((?:about|around|~|cerca de)?\s*(\d[\d.,]*)\s*(?:words|palavras)\)", re.I)


@dataclass
class PremiseForm:
    title: str = ""
    goal: str = ""
    opening: str = ""
    characters: list[str] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)   # {"text": str, "words": int | None}
    hook: str = ""
    must_include: str = ""
    must_not: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PremiseForm":
        scenes = []
        for s in d.get("scenes") or []:
            text = str(s.get("text", "")).strip()
            if not text:
                continue
            try:
                words = int(s.get("words")) if s.get("words") not in (None, "") else None
            except (TypeError, ValueError):
                words = None
            scenes.append({"text": text, "words": words})
        chars = d.get("characters") or []
        if isinstance(chars, str):
            chars = [c.strip() for c in chars.split(",")]
        return cls(
            title=str(d.get("title", "")).strip(),
            goal=str(d.get("goal", "")).strip(),
            opening=str(d.get("opening", "")).strip(),
            characters=[c.strip() for c in chars if str(c).strip()],
            scenes=scenes,
            hook=str(d.get("hook", "")).strip(),
            must_include=str(d.get("must_include", "")).strip(),
            must_not=str(d.get("must_not", "")).strip(),
        )


def _one_line(text: str) -> str:
    return re.sub(r"\s*\n\s*", " ", text.strip())


def to_text(form: PremiseForm, chapter_num: int) -> str:
    title = form.title.strip()
    head = f"Chapter Premise: Chapter {chapter_num}: {title}" if title else f"Chapter Premise: Chapter {chapter_num}"
    parts = [head, ""]
    for key, label in (("goal", "Goal"), ("opening", "Opening")):
        value = getattr(form, key).strip()
        if value:
            parts.append(f"{label}: {_one_line(value)}")
    if form.characters:
        parts.append(f"Characters in scene: {', '.join(form.characters)}")
    if form.scenes:
        parts += ["", "Scenes:"]
        for i, s in enumerate(form.scenes, start=1):
            words = f" (about {s['words']} words)" if s.get("words") else ""
            parts.append(f"{i}. {_one_line(s['text'])}{words}")
    tail = [(label, getattr(form, key).strip()) for key, label in
            (("hook", "Hook"), ("must_include", "Must include"), ("must_not", "Must not appear"))]
    if any(v for _, v in tail):
        parts.append("")
        parts += [f"{label}: {_one_line(v)}" for label, v in tail if v]
    return "\n".join(parts).strip() + "\n"


def _label_of(line: str) -> tuple[str, str] | None:
    m = re.match(r"^\s*([A-Za-zÀ-ÿ' ]{3,30})\s*:\s*(.*)$", line)
    if not m:
        return None
    name = m.group(1).strip().lower()
    for key, names in LABELS.items():
        if name in names:
            return key, m.group(2).strip()
    return None


def from_text(text: str) -> PremiseForm:
    """Lê uma premissa (escrita pelo formulário, pelo modelo ou à mão) de volta nos campos."""
    form = PremiseForm()
    text = text or ""
    plan = parse_premise(text)
    if plan.title:
        form.title = re.sub(r"^Chapter\s+\d+\s*:\s*", "", plan.title).strip()
    for line in text.splitlines():
        found = _label_of(line)
        if not found:
            continue
        key, value = found
        if key == "characters":
            form.characters = [c.strip() for c in re.split(r"[,;]", value) if c.strip()]
        elif not getattr(form, key):
            setattr(form, key, value)
    for s in plan.scenes:
        body = _one_line(s.text)
        m = _WORDS_SUFFIX.search(body) or _WORDS_INLINE.search(body)
        words = s.target_words
        if m:
            body = (body[:m.start()] + body[m.end():]).strip()
        form.scenes.append({"text": body.strip(), "words": words})
    return form


# ── Plano da história (seção 11 do Registro) ─────────────────

def outline_row(akashic_text: str, chapter_num: int) -> dict | None:
    """
    Linha do plano de capítulos (tabela "| Cap. | Título | Conteúdo | ... |" do Registro) para
    o capítulo pedido: {title, content, extra}. None se não houver.
    """
    for line in (akashic_text or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0] == str(chapter_num) and line.strip().startswith("|"):
            return {"title": cells[1], "content": cells[2], "extra": " · ".join(cells[3:])}
    return None


# ── Pedidos ao modelo ───────────────────────────────────────

SYSTEM_PREMISE_WRITER = """\
You plan chapters of a serialized web novel for a small language model that will write them.
Write the premise of the requested chapter in EXACTLY this format, in English, and nothing else:

Chapter Premise: Chapter N: Title

Goal: what the chapter must achieve, in one sentence.
Opening: where and how the chapter starts. It must continue exactly from the PREVIOUS CHAPTER ENDING: same place, same light, same situation.
Characters in scene: comma-separated names, only characters that the records allow at this point of the story.

Scenes:
1. Scene name. What happens, concretely, in two to four sentences. (about N words)
2. ...

Hook: how the chapter ends.
Must include: concrete things that must appear (for example, one [System] line).
Must not appear: characters, places, objects or facts that must not appear yet, and anything that would contradict the records or previous chapters.

Rules: 3 or 4 scenes. The scene word counts add up to the requested length. Follow the story plan \
for this chapter when there is one. Never contradict the AKASHIC RECORDS or what already happened. \
Invent nothing the records forbid.\
"""

SYSTEM_PREMISE_CHECKER = """\
You are a continuity editor. Compare a chapter premise with the story records, the story so far and \
the end of the previous chapter. List only real problems, one per line, starting with "- ": \
contradictions with the previous chapter ending (place, light, time, who is present), characters or \
things that must not appear yet, facts that contradict the records, missing scene word counts, \
a hook that does not follow from the scenes. Be concrete and short. Write in Brazilian Portuguese. \
If there are no problems, answer exactly: OK.\
"""


def _context(akashic: str, outline: dict | None, previous_tail: str, story_so_far: str,
             summaries: list[tuple[int, str]], open_threads: str, roster: str,
             next_outline: dict | None = None, introduced: list[str] | None = None,
             not_introduced: list[str] | None = None) -> list[str]:
    parts = [f"=== AKASHIC RECORDS ===\n{akashic.strip()}"]
    if story_so_far.strip():
        parts.append(f"=== THE STORY SO FAR ===\n{story_so_far.strip()}")
    if summaries:
        parts.append("=== PREVIOUS CHAPTER SUMMARIES ===\n" + "\n\n".join(f"Chapter {n}: {t.strip()}" for n, t in summaries))
    if roster.strip():
        parts.append(f"=== CHARACTER ROSTER ===\n{roster.strip()}")
    if open_threads.strip():
        parts.append(f"=== OPEN THREADS ===\n{open_threads.strip()}")
    if previous_tail.strip():
        parts.append(f"=== PREVIOUS CHAPTER ENDING ===\n{previous_tail.strip()}")
    if introduced is not None:
        cast = [f"Characters already in the story: {', '.join(introduced) or 'only the protagonist'}."]
        if not_introduced:
            cast.append(f"Characters NOT introduced yet (they must not appear, not even as a voice, "
                        f"unless the story plan for this chapter introduces them): {', '.join(not_introduced)}.")
        parts.append("=== CAST SO FAR ===\n" + "\n".join(cast))
    if outline:
        plan = [f"This chapter must be about exactly this. Title: {outline['title']}. Content: {outline['content']}"
                + (f" Focus: {outline['extra']}." if outline['extra'] else "")]
        if next_outline:
            plan.append(f"The NEXT chapter covers this, so none of it happens yet: {next_outline['title']}: {next_outline['content']}")
        parts.append("=== STORY PLAN ===\n" + "\n".join(plan))
    return parts


def build_suggest_prompt(chapter_num: int, target_words: int, notes: str = "", **ctx) -> str:
    parts = _context(**ctx)
    task = [f"=== TASK ===", f"Write the premise of Chapter {chapter_num}. Total length: about {target_words} words."]
    if chapter_num == 1:
        task.append("This is the first chapter.")
    if notes.strip():
        task.append(f"The author's ideas for this chapter (follow them): {notes.strip()}")
    task.append("The Opening continues the PREVIOUS CHAPTER ENDING exactly: same place, same light, same situation.")
    task.append("Stop right after the 'Must not appear' line. Do not write the chapter itself.")
    parts.append("\n".join(task))
    return "\n\n".join(parts)


def build_check_prompt(chapter_num: int, premise_text: str, **ctx) -> str:
    parts = _context(**ctx)
    parts.append(f"=== PREMISE OF CHAPTER {chapter_num} TO CHECK ===\n{premise_text.strip()}")
    parts.append(
        "=== TASK ===\nList the problems of this premise, or answer OK. Check first whether the Opening "
        "and the first scene continue the PREVIOUS CHAPTER ENDING (place, light, time, who is present), then "
        "whether any character NOT introduced yet appears, then whether the premise follows the STORY PLAN "
        "and does not jump into the next chapter.\nEscreva a lista em português do Brasil."
    )
    return "\n\n".join(parts)


def cut_after_premise(raw: str) -> str:
    """O modelo às vezes continua e escreve o capítulo: fica só até a linha 'Must not appear'."""
    lines = (raw or "").strip().splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*(must not appear|não pode aparecer)\s*:", line, re.I):
            return "\n".join(lines[:i + 1]).strip()
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---" or re.match(r"^\s*#*\s*Chapter\s+\d+\s*:", line):
            return "\n".join(lines[:i]).strip()
    return "\n".join(lines).strip()


# Começos das explicações do formato no SYSTEM_PREMISE_WRITER: o modelo às vezes as copia como resposta.
_TEMPLATE_ECHOES = (
    "what the chapter must achieve", "where and how the chapter starts", "comma-separated names",
    "how the chapter ends", "concrete things that must appear", "characters, places, objects or facts",
)


def drop_template_echo(form: PremiseForm) -> list[str]:
    """Esvazia campos em que o modelo copiou a instrução do formato. Retorna os campos esvaziados."""
    cleared = []
    for key in ("goal", "opening", "hook", "must_include", "must_not"):
        value = getattr(form, key).strip().lower()
        if value and any(value.startswith(e) for e in _TEMPLATE_ECHOES):
            setattr(form, key, "")
            cleared.append(key)
    return cleared


_GENERIC = {"first", "later", "other", "second", "third", "small", "large", "great", "visitor", "visitors",
             "people", "person", "place", "thing", "things", "system", "chapter", "story", "their", "there"}


def prune_must_not(form: PremiseForm, outline: dict | None) -> list[str]:
    """
    Tira do "Não pode aparecer" o que a própria premissa ou o plano do capítulo usam (o modelo
    às vezes cola o glossário inteiro e proíbe até o cenário do capítulo). Retorna o que saiu.
    """
    used = " ".join([form.goal, form.opening, form.hook, form.must_include]
                    + [s["text"] for s in form.scenes]
                    + ([outline["title"], outline["content"]] if outline else [])).lower()
    items = [i.strip() for i in re.split(r"[;,]", form.must_not) if i.strip()]

    def in_use(item: str) -> bool:
        core = re.sub(r"^(the|a|an|any)\s+", "", item.lower()).strip(" .")
        core = re.sub(r"s$", "", core)  # "ZPMs" também casa com "ZPM"
        if core and re.search(rf"\b{re.escape(core)}", used):
            return True
        # "Atlantis gate room" sai se a premissa usa "Atlantis": vale qualquer palavra própria do item.
        words = [w for w in re.findall(r"[a-zà-ÿ0-9']+", core) if len(w) >= 5 and w not in _GENERIC]
        return any(re.search(rf"\b{re.escape(w)}", used) for w in words)

    kept = [i for i in items if not in_use(i)]
    removed = [i for i in items if in_use(i)]
    form.must_not = ", ".join(kept)
    return removed


def enforce_cast(form: PremiseForm, not_introduced: list[str], outline: dict | None) -> list[str]:
    """
    Tira da lista de personagens em cena quem ainda não foi apresentado na história, a menos que
    o plano deste capítulo o apresente, e acrescenta essas pessoas ao "Não pode aparecer".
    Retorna os nomes retirados. Trava fixa: o modelo de 12B põe no capítulo 2 quem só nasce no 5.
    """
    plan = f"{outline['title']} {outline['content']}".lower() if outline else ""

    def planned(name: str) -> bool:
        first = name.split()[0].lower() if name.split() else ""
        return name.lower() in plan or (len(first) >= 4 and first in plan)

    blocked = [n for n in not_introduced if not planned(n)]
    def base(name: str) -> str:
        # "Tinaia (voice only)" → "tinaia"
        return re.sub(r"\s*\(.*?\)", "", name).strip().lower()

    removed = [c for c in form.characters
               if any(base(c) in (b.lower(), b.split()[0].lower()) for b in blocked)]
    form.characters = [c for c in form.characters if c not in removed]
    missing = [b for b in blocked if b.lower() not in form.must_not.lower()]
    if missing:
        form.must_not = "; ".join(filter(None, [form.must_not.rstrip(". "), ", ".join(missing)]))
    return removed


# ── Uso no pipeline ─────────────────────────────────────────

def chapter_guidance(form: PremiseForm, sheets: dict[str, str], first_scene: bool) -> str:
    """
    Linhas extras para o fim do prompt de cada cena, tiradas da premissa guiada: quem está em
    cena (com a ficha de cada um), a abertura (só na primeira cena) e o que precisa e o que não
    pode aparecer. Modelos pequenos obedecem mais ao que leem por último.
    """
    lines = []
    if form.characters:
        cast = []
        for name in form.characters:
            sheet = next((s for n, s in sheets.items() if n.lower() == name.lower()), "")
            cast.append(f"{name}: {sheet}" if sheet else name)
        lines.append("Characters in this chapter (no other named character appears): " + " | ".join(cast))
    if first_scene and form.opening:
        lines.append(f"The chapter opens like this: {form.opening}")
    if form.must_include:
        lines.append(f"Somewhere in the chapter, include: {form.must_include}")
    if form.must_not:
        lines.append(f"Must NOT appear anywhere: {form.must_not}")
    return "\n".join(lines)
