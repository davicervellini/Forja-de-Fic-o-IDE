"""
canon.py — O pedaço do Registro Akáshico que cada capítulo recebe.

O registro_modelo.md tem o cânone da história inteira: fichas de personagens que só estreiam
no capítulo 200, universos que a história ainda não visitou, a lista de grafias de todos os
nomes. Mandar tudo para cada cena gasta metade do contexto e, pior, põe na cabeça do modelo
gente e lugares que não podem aparecer ainda: modelos pequenos usam o que leem.

Aqui o registro é recortado por capítulo:
- fichas (5.8) só de quem está no capítulo, do protagonista e de quem já estreou e é citado
  na premissa ou na memória; quem estreia depois não aparece nem de nome;
- universos (9.5) só os desses personagens, dos locais em cena e o cenário base;
- grafias oficiais (13.2) só dos nomes que o capítulo usa;
- seções com faixa de capítulos no título ("Arc card: Atlantis, Chapters 1-3 (EN)") só nos
  capítulos da faixa;
- a amostra de voz ("Voice sample (EN)") sai do bloco e vai para o fim do prompt da cena.

Funções puras: recebem textos e metadados, não chamam o modelo.
"""

import re
from dataclasses import dataclass, field

from pipeline.akashic_schema import AkashicMeta, Character

_HEADING = re.compile(r"^(#+)\s+(.*)$")
_BULLET = re.compile(r"^\s*-\s+\*\*(?P<label>[^*]+?)\*\*\s*(?P<body>.*)$")
_RANGE = re.compile(r"chapters?\s+(\d+)\s*(?:-|–|—|to|a|até)\s*(\d+)", re.I)
_SINCE = re.compile(r"chapters?\s+(\d+)\s*\+", re.I)


@dataclass
class Section:
    level: int
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        t = self.title.lower()
        if re.match(r"^1\.\s", t):
            return "summary"
        if re.match(r"^5\.8\s", t) or "cast" in t and "(en)" in t:
            return "cast"
        if re.match(r"^9\.5\s", t):
            return "universes"
        if "official spellings" in t:
            return "spellings"
        if "voice sample" in t:
            return "voice"
        if "glossary" in t or "glossário" in t:
            return "glossary"
        return "other"

    def text(self) -> str:
        head = f"{'#' * self.level} {self.title}" if self.level else ""
        body = "\n".join(self.lines).strip()
        return f"{head}\n{body}".strip() if head else body


def split_sections(text: str) -> list[Section]:
    """Divide o registro em seções, uma por título de qualquer nível (sem aninhar)."""
    sections = [Section(0, "")]
    for line in (text or "").splitlines():
        m = _HEADING.match(line)
        if m:
            sections.append(Section(len(m.group(1)), m.group(2).strip()))
        else:
            sections[-1].lines.append(line)
    return [s for s in sections if s.title or "\n".join(s.lines).strip()]


def chapter_range(title: str) -> tuple[int, int] | None:
    """Faixa de capítulos escrita no título da seção ("Chapters 1-3", "Chapters 6+"), ou None."""
    m = _RANGE.search(title)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _SINCE.search(title)
    if m:
        return int(m.group(1)), 10 ** 6
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .:,").lower()


def name_pattern(name: str) -> re.Pattern:
    """Nome com a grafia (e a maiúscula) do registro; "the X" aceita "The X" no começo da frase."""
    name = name.strip()
    if name.lower().startswith("the "):
        return re.compile(r"(?<!\w)[Tt]he " + re.escape(name[4:]) + r"(?!\w)")
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")


def mentions(name: str, text: str) -> bool:
    """O nome aparece no texto: inteiro, ou só o primeiro nome quando ele é longo (Arthur, Tinaia)."""
    name = re.sub(r"\s*\(.*?\)", "", name or "").strip()
    if not name:
        return False
    if name_pattern(name).search(text or ""):
        return True
    first = name.split()[0]
    return len(name.split()) > 1 and len(first) >= 4 and first.lower() not in {"the", "first", "lady", "lord"} \
        and re.search(rf"(?<!\w){re.escape(first)}(?!\w)", text or "") is not None


def _character_of(label: str, characters: list[Character]) -> Character | None:
    lab = _norm(label)
    for c in characters:
        names = [_norm(c.name)] + ([_norm(c.sheet_label)] if c.sheet_label else [])
        if any(n and (lab == n or lab.startswith(n) or re.search(rf"\b{re.escape(n)}\b", lab)) for n in names):
            return c
    return None


def _drop_sentences_naming(text: str, names: list[str]) -> str:
    """Tira as frases que citam algum dos nomes (personagens que ainda não estrearam)."""
    if not names:
        return text
    out = []
    for para in re.split(r"\n\s*\n", text):
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        kept = [s for s in sentences if not any(mentions(n, s) for n in names)]
        if kept:
            out.append(" ".join(kept))
    return "\n\n".join(out)


def filter_spellings(lines: list[str], text: str, keep_names: list[str], hidden: list[str]) -> list[str]:
    """
    Linhas "Rótulo: a; b; c." com só os nomes citados em `text` (com a maiúscula do registro: "the
    heart of the city" não puxa "the Heart") ou da lista `keep_names`, e nunca os de `hidden`.
    """
    out = []
    for line in lines:
        if ":" not in line or ";" not in line:
            out.append(line)
            continue
        label, items = line.split(":", 1)
        keep = []
        for raw in items.split(";"):
            item = raw.strip().rstrip(".")
            if not item:
                continue
            if any(_norm(item) == _norm(h) or mentions(h, item) for h in hidden):
                continue
            if name_pattern(item).search(text or "") or any(_norm(item) == _norm(k) for k in keep_names):
                keep.append(item)
        if keep:
            out.append(f"{label}: {'; '.join(keep)}.")
    return out


@dataclass
class ChapterCanon:
    text: str
    voice_sample: str = ""
    glossary: list[tuple[str, str]] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)       # fichas que entraram
    hidden: list[str] = field(default_factory=list)     # personagens fora do capítulo


def chapter_canon(records: str, meta: AkashicMeta | None, chapter_num: int, characters: list[str],
                  introduced: list[str], not_introduced: list[str], context: str = "",
                  locations: list[str] | None = None) -> ChapterCanon:
    """
    Recorte do registro_modelo para o capítulo `chapter_num`.

    `characters`: elenco da premissa. `introduced`/`not_introduced`: quem já estreou e quem não
    (cast.cast_status). `context`: premissa, roster, threads e fim do capítulo anterior, para saber
    que nomes o capítulo usa. `locations`: locais em cena da premissa.
    """
    chars = meta.characters if meta else []
    hidden = [n for n in not_introduced if not any(_norm(n) == _norm(c) for c in characters)]
    wanted = {_norm(n) for n in characters}
    wanted |= {_norm(c.name) for c in chars if c.role == "protagonist"}
    wanted |= {_norm(n) for n in introduced if mentions(n, context)}
    sections = split_sections(records)
    has_arc_card = any(chapter_range(s.title) and chapter_range(s.title)[0] <= chapter_num <= chapter_range(s.title)[1]
                       for s in sections)

    kept_chars: list[Character] = []
    out: list[str] = []
    voice = ""
    glossary: list[tuple[str, str]] = []
    for sec in sections:
        rng = chapter_range(sec.title)
        if rng and not (rng[0] <= chapter_num <= rng[1]):
            continue
        if sec.level == 1 or (not sec.title and not "".join(sec.lines).strip()):
            continue
        lines = [l for l in sec.lines if not l.startswith("Generated automatically")]
        kind = sec.kind
        if kind == "voice":
            voice = "\n".join(lines).strip()
            continue
        if kind == "glossary":
            glossary = parse_glossary("\n".join(lines))
        if kind == "summary":
            body = "\n".join(lines).strip()
            if has_arc_card:
                paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
                body = "\n\n".join(paras[:1] + [p for p in paras[1:] if "timing" in p.lower()])
            lines = _drop_sentences_naming(body, hidden).splitlines()
        elif kind == "cast":
            new = []
            for line in lines:
                m = _BULLET.match(line)
                if not m:
                    if line.strip():
                        new.append(line)
                    continue
                c = _character_of(m.group("label"), chars)
                name = c.name if c else m.group("label")
                if any(_norm(name) == _norm(h) for h in hidden):
                    continue
                if (c and _norm(c.name) in wanted) or (not c and mentions(name.strip(" ."), context)):
                    new.append(line)
                    if c:
                        kept_chars.append(c)
            lines = new
        elif kind == "universes":
            lines = _filter_universes(lines, meta, chapter_num, kept_chars or
                                      [c for c in chars if _norm(c.name) in wanted], locations or [], context)
        elif kind == "spellings":
            keep_names = [c.name for c in kept_chars] + list(characters)
            lines = filter_spellings(lines, context, keep_names, hidden)
        body = "\n".join(lines).strip()
        if body:
            out.append(f"{'#' * sec.level} {sec.title}\n{body}" if sec.title else body)
    return ChapterCanon(text="\n\n".join(out).strip(), voice_sample=voice, glossary=glossary,
                        cast=[c.name for c in kept_chars], hidden=hidden)


def _filter_universes(lines: list[str], meta: AkashicMeta | None, chapter_num: int, kept: list[Character],
                      locations: list[str], context: str) -> list[str]:
    if not meta:
        return lines
    ids = {c.universe for c in kept if c.universe}
    locs = {_norm(n) for n in locations}
    ids |= {loc.universe for loc in meta.locations if loc.universe and _norm(loc.name) in locs}
    for u in meta.universes:
        if u.role in ("base", "hub") or mentions(u.name, context):
            ids.add(u.id)
    out = []
    for line in lines:
        m = _BULLET.match(line)
        if not m:
            if line.strip():
                out.append(line)
            continue
        u = next((u for u in meta.universes if _norm(u.name) == _norm(m.group("label"))), None)
        if u is None:
            if mentions(m.group("label").strip(" ."), context):
                out.append(line)
            continue
        if u.id in ids and not (u.debut_chapter and u.debut_chapter > chapter_num):
            out.append(line)
    return out


# ── Glossário português → inglês ──────────────────────────────

_OFFICIAL = re.compile(r"grafia oficial\s*:\s*(?P<pt>[^.\n]+?)\.\s*Em inglês,\s*(?P<en>[^.\n]+)", re.I)
_PAIR = re.compile(r"^\s*[-*]?\s*(?P<pt>[^=→:\n]+?)\s*(?:=|→|->)\s*(?P<en>[^\n]+?)\s*$")


def parse_glossary(text: str, pairs_anywhere: bool = True) -> list[tuple[str, str]]:
    """
    Pares (termo em português, termo em inglês) do registro: linhas "senha = the ticket (...)" de
    uma seção de glossário e frases "Nome e grafia oficial: a Gerência. Em inglês, the Management."
    `pairs_anywhere` falso: linhas "x = y" só valem dentro de seções de glossário.
    """
    pairs: list[tuple[str, str]] = []
    for m in _OFFICIAL.finditer(text or ""):
        pairs.append((m.group("pt").strip(), m.group("en").strip()))
    lines = (text or "").splitlines() if pairs_anywhere else [
        l for sec in split_sections(text) if sec.kind == "glossary" for l in sec.lines]
    for line in lines:
        m = _PAIR.match(line)
        if m and len(m.group("pt")) <= 60:
            pairs.append((m.group("pt").strip(" *"), m.group("en").strip(" *")))
    seen, out = set(), []
    for pt, en in pairs:
        if pt.lower() not in seen and pt.lower() != en.lower():
            seen.add(pt.lower())
            out.append((pt, en))
    return out


def glossary_block(pairs: list[tuple[str, str]], text: str = "") -> str:
    """Linhas do glossário cujo termo em português aparece em `text` (todas, se `text` vazio)."""
    lines = []
    for pt, en in pairs:
        core = re.sub(r"^(?:a|o|as|os)\s+", "", pt, flags=re.I)
        if not text or re.search(rf"(?<!\w){re.escape(core)}(?!\w)", text, re.I):
            lines.append(f"- {pt} = {en}")
    return "\n".join(lines)


# ── Bloco de estilo do polimento ──────────────────────────────

# Linhas do guia de estilo que só servem para escrever o capítulo inteiro, não para editar uma cena.
_DRAFT_ONLY = re.compile(r"^\s*[-*]?\s*\**\s*(?:chapter|chapters|title|title format|reference|length|"
                         r"cap[ií]tulo|t[ií]tulo|refer[êe]ncia)\b", re.I)


def editor_brief(records: str) -> str:
    """Guia de estilo, grafias oficiais e proibições, sem repetição e sem regras de capítulo inteiro."""
    parts, seen = [], set()
    for sec in split_sections(records):
        t = sec.title.lower()
        if sec.kind == "spellings" or "style guide" in t or re.match(r"^1[04]\.\s", t) or "must not do" in t:
            key = _norm(sec.title)
            if key in seen:
                continue
            seen.add(key)
            lines = [l for l in sec.lines if not _DRAFT_ONLY.match(l)]
            body = "\n".join(lines).strip()
            if body:
                parts.append(f"{'#' * sec.level} {sec.title}\n{body}")
    return "\n\n".join(parts)
