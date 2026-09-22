"""
scenes.py — Divide a premissa em cenas e monta o capítulo a partir delas.

Modelos pequenos (7B a 12B) não escrevem 2.000 palavras de uma vez: resumem todas as
cenas em 600 palavras e param. O pipeline por isso gera e pole uma cena por vez. Este
módulo tem só funções puras, sem chamadas ao modelo, para poder ser testado sozinho.

Formatos de premissa aceitos (inglês ou português), por exemplo:

    Chapter Premise: Chapter 2: The Empty City
    Scenes:
    1. The Dark Corridors. Arthur wanders ...
    2. The Genetic Key. ...
    Hook: ...

    SCENE 1 (about 350 words) - Tuesday morning.
    texto da cena em várias linhas ...
    SCENE 2 (about 900 words) - The white waiting room.
    ...
"""

import re
from dataclasses import dataclass, field

SCENE_BREAK = "* * *"


@dataclass
class Scene:
    num: int
    text: str
    target_words: int | None = None


@dataclass
class PremisePlan:
    title: str | None = None
    hook: str = ""
    scenes: list[Scene] = field(default_factory=list)


_HEADER = re.compile(r"^\s*(?:scenes|cenas)\b", re.I)
_SCENE_WORD = re.compile(r"^\s*(?:[-*]\s*)?(?:scene|cena)\s+(\d{1,2})\b\s*[\.\):\-–—]?\s*(.*)$", re.I)
_SCENE_NUM = re.compile(r"^\s*(?:[-*]\s*)?(\d{1,2})\s*[\.\):\-–—]\s+(.*)$")
_SECTION_END = re.compile(
    r"^\s*(?:hook|gancho|termina com|ends? with|ending|must|precisa|n[ãa]o pode|tamanho|target|"
    r"length|goal|objetivo|personagens em cena|characters in scene|universos em cena|regras em foco|"
    r"forbidden|do not|n[ãa]o escrever)\b",
    re.I,
)
_HOOK = re.compile(r"^\s*(?:hook|gancho|termina com|ends? with)\s*:\s*(.+)$", re.I)
_TITLE = re.compile(r"(?:chapter|cap[ií]tulo)\s+(\d+)\s*:\s*([^\n]+)", re.I)
_WORDS = re.compile(r"(?:about|around|~|cerca de|aprox\.?)\s*(\d[\d.,]*)\s*(?:words|palavras)", re.I)
_BREAK_LINE = re.compile(r"^\s*(?:\*\s*){3}\s*$")
_LEADING_JUNK = re.compile(
    r"^\s*(?:#+\s.*|(?:chapter|cap[ií]tulo)\s+\d+\b.*|(?:scene|cena)\s+\d+\b.*|\*\*[^*]{1,60}\*\*|(?:\*\s*){3})\s*$",
    re.I,
)


def count_words(text: str) -> int:
    return len((text or "").split())


def tail_words(text: str, n: int) -> str:
    """Últimas `n` palavras do texto, começando num início de parágrafo quando possível."""
    words = (text or "").split()
    if len(words) <= n:
        return (text or "").strip()
    tail = " ".join(words[-n:])
    return "… " + tail


def parse_premise(premise: str) -> PremisePlan:
    """Extrai título, gancho e cenas numeradas. Menos de duas cenas: lista vazia."""
    plan = PremisePlan()
    text = premise or ""

    m = _TITLE.search(text)
    if m:
        plan.title = f"Chapter {int(m.group(1))}: {m.group(2).strip().rstrip('.')}"

    lines = text.splitlines()
    has_header = any(_HEADER.match(l) for l in lines)
    collecting = not has_header
    scenes: list[Scene] = []

    for line in lines:
        hook = _HOOK.match(line)
        if hook and not plan.hook:
            plan.hook = hook.group(1).strip()
        if has_header and not collecting:
            if _HEADER.match(line):
                collecting = True
            continue
        if not collecting:
            continue
        start = _SCENE_WORD.match(line) or _SCENE_NUM.match(line)
        if start and int(start.group(1)) == len(scenes) + 1:
            scenes.append(Scene(num=int(start.group(1)), text=start.group(2).strip()))
            continue
        if scenes and _SECTION_END.match(line):
            collecting = False
            continue
        if scenes and line.strip():
            scenes[-1].text = (scenes[-1].text + "\n" + line.strip()).strip()

    for s in scenes:
        w = _WORDS.search(s.text)
        if w:
            try:
                s.target_words = int(w.group(1).replace(",", "").replace(".", ""))
            except ValueError:
                s.target_words = None

    plan.scenes = scenes if len(scenes) >= 2 else []
    return plan


def clean_scene(text: str) -> str:
    """Tira título, cabeçalho de cena e separadores que o modelo às vezes põe no começo ou no fim."""
    lines = (text or "").strip().splitlines()
    while lines and (not lines[0].strip() or _LEADING_JUNK.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _BREAK_LINE.match(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


def assemble_chapter(title: str | None, scenes: list[str], scene_break: str = SCENE_BREAK) -> str:
    body = f"\n\n{scene_break}\n\n".join(s.strip() for s in scenes if s and s.strip())
    return f"{title}\n\n{body}" if title else body


def split_chapter(text: str) -> tuple[str | None, list[str]]:
    """Inverso de assemble_chapter: (título, cenas). Aceita capítulo sem título ou sem separadores."""
    lines = (text or "").strip().splitlines()
    title = None
    if lines and re.match(r"^\s*(?:chapter|cap[ií]tulo)\s+\d+\b", lines[0], re.I):
        title = lines.pop(0).strip()
    scenes, buf = [], []
    for line in lines:
        if _BREAK_LINE.match(line):
            if "\n".join(buf).strip():
                scenes.append("\n".join(buf).strip())
            buf = []
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        scenes.append("\n".join(buf).strip())
    return title, scenes


# ── Limpeza de repetição (modelos pequenos entram em laço) ────

_WORD = re.compile(r"[a-zà-ÿ0-9']+", re.I)
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*\s*$")


def _words_set(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def similarity(a: str, b: str) -> float:
    """Semelhança entre dois trechos pelas palavras em comum (Jaccard, de 0 a 1)."""
    sa, sb = _words_set(a), _words_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+(?=[\"'“A-ZÀ-Ý])", paragraph) if s.strip()]


def remove_repetition(text: str, para_threshold: float = 0.6, min_words: int = 8) -> str:
    """
    Tira parágrafos quase iguais a um anterior da mesma cena e frases repetidas palavra
    por palavra. Mantém sempre a primeira ocorrência.
    """
    kept: list[str] = []
    seen_sentences: set[str] = set()
    for para in paragraphs(text):
        if count_words(para) >= min_words and any(similarity(para, k) >= para_threshold for k in kept):
            continue
        out = []
        for s in _sentences(para):
            key = " ".join(_WORD.findall(s.lower()))
            if count_words(s) >= 6 and key in seen_sentences:
                continue
            seen_sentences.add(key)
            out.append(s.strip())
        if out:
            kept.append(" ".join(out))
    return "\n\n".join(kept)


REPEAT_NGRAM = 8


def ngrams_of(text: str, n: int = REPEAT_NGRAM) -> set[tuple[str, ...]]:
    grams: set[tuple[str, ...]] = set()
    for para in paragraphs(text):
        for s in _sentences(para):
            w = [x.lower() for x in _WORD.findall(s)]
            grams |= {tuple(w[k:k + n]) for k in range(len(w) - n + 1)}
    return grams


def remove_repeated_sentences(text: str, seen: set[tuple[str, ...]], n: int = REPEAT_NGRAM) -> str:
    """
    Tira as frases que repetem uma sequência de `n` palavras já usada antes (no capítulo
    ou mais cedo nesta cena). Falas entre aspas ficam, porque personagens repetem de propósito.
    Atualiza `seen` com as frases mantidas.
    """
    out_paras = []
    for para in paragraphs(text):
        kept = []
        for s in _sentences(para):
            w = [x.lower() for x in _WORD.findall(s)]
            grams = {tuple(w[k:k + n]) for k in range(len(w) - n + 1)}
            if grams & seen and not s.lstrip().startswith(('"', "“")):
                continue
            seen |= grams
            kept.append(s.strip())
        if kept:
            out_paras.append(" ".join(kept))
    return "\n\n".join(out_paras)


def drop_overlap(new_scene: str, previous_text: str, threshold: float = 0.45, lookback: int = 4) -> str:
    """Tira do começo da cena nova os parágrafos que repetem o final do texto anterior."""
    prev = paragraphs(previous_text)[-lookback:]
    paras = paragraphs(new_scene)
    while paras and prev and count_words(paras[0]) >= 8 and any(similarity(paras[0], p) >= threshold for p in prev):
        paras.pop(0)
    return "\n\n".join(paras)


def trim_incomplete_ending(text: str) -> str:
    """Corta a frase final pela metade (texto interrompido pelo limite de tokens)."""
    text = (text or "").rstrip()
    if not text or _SENTENCE_END.search(text):
        return text
    cut = max(text.rfind(ch) for ch in ".!?…")
    if cut <= 0:
        return text
    end = cut + 1
    while end < len(text) and text[end] in "\"'”’)]":
        end += 1
    return text[:end].rstrip()


def last_sentence(text: str) -> str:
    paras = paragraphs(text)
    if not paras:
        return ""
    sents = _sentences(paras[-1])
    return sents[-1].strip() if sents else paras[-1]


def drop_new_system_lines(edited: str, original: str) -> str:
    """Remove linhas [System] que o polimento inventou (as que não existiam no rascunho)."""
    allowed = {l.strip() for l in original.splitlines() if l.strip().startswith("[System]")}
    kept = [l for l in edited.splitlines() if not (l.strip().startswith("[System]") and l.strip() not in allowed)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
