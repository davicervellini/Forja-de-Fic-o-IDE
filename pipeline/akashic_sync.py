"""
akashic_sync.py — Liga as abas Universos e Personagens do editor ao que o modelo recebe.

O modelo lê o `registro_modelo.md`, montado a partir das seções em Markdown. As abas do
editor gravam só os metadados (bloco JSON no topo do arquivo). Este módulo faz dos
metadados a fonte das duas listas que dependem deles:

- seção 9.5 (universos para o modelo): um item por universo ATIVO, com a ficha curta
  (`Universe.model_sheet`) e os personagens permitidos;
- seção 5.8 (fichas curtas do elenco): um item por personagem, com `Character.sheet`.

Na primeira vez (`lists_synced` falso), os itens que já existem no texto são importados
para os metadados, sem perder nada. Depois disso as duas listas são sempre geradas a
partir dos metadados; o resto das seções, como introdução e rodapé, continua vindo do texto.
"""

import re

from pipeline.akashic_schema import AkashicMeta, Character, Universe, read_meta, slugify, write_meta

SECTION_UNIVERSES = "### 9.5 "
SECTION_CAST = "### 5.8 "
TODO = "[A DEFINIR: {}]"
SYNC_NOTE = ("Nota: esta lista é gerada pelas abas Universos e Personagens do editor 📜. "
             "Edite lá: os itens com marcador escritos aqui no texto são substituídos ao salvar.")

_BULLET = re.compile(r"^- \*\*(?P<label>[^*]+?)\*\*\s*(?P<body>.*)$")


# ── Localização das seções ────────────────────────────────────

def _level(line: str) -> int:
    m = re.match(r"^(#+) ", line)
    return len(m.group(1)) if m else 0


def _section_range(lines: list[str], prefix: str) -> tuple[int, int] | None:
    """(índice do título, índice do fim exclusivo) da seção, ou None."""
    start = next((i for i, l in enumerate(lines) if l.startswith(prefix)), None)
    if start is None:
        return None
    lvl = _level(lines[start])
    end = next((j for j in range(start + 1, len(lines)) if 0 < _level(lines[j]) <= lvl), len(lines))
    return start, end


def _bullets(lines: list[str], rng: tuple[int, int] | None) -> list[tuple[str, str]]:
    if not rng:
        return []
    out = []
    for line in lines[rng[0] + 1:rng[1]]:
        m = _BULLET.match(line.strip())
        if m:
            out.append((m.group("label").strip(), m.group("body").strip()))
    return out


# ── "Allowed: A, B (tier 2). Resto." ─────────────────────────

def _split_top_level(text: str) -> list[str]:
    parts, buf, depth = [], [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def split_allowed(body: str) -> tuple[list[str], str]:
    """(personagens permitidos, resto do texto). A frase 'Allowed: ...' termina no primeiro ponto fora de parênteses."""
    i = body.find("Allowed:")
    if i < 0:
        return [], body.strip()
    j = i + len("Allowed:")
    depth, end = 0, len(body)
    for k in range(j, len(body)):
        ch = body[k]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "." and depth == 0 and (k + 1 == len(body) or body[k + 1] == " "):
            end = k
            break
    allowed = _split_top_level(body[j:end])
    rest = (body[:i] + body[end + 1:]).strip()
    return allowed, re.sub(r"\s{2,}", " ", rest)


# ── Importação do texto para os metadados (uma vez) ───────────

def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" .:").lower()


def _find_universe(meta: AkashicMeta, name: str) -> Universe | None:
    n = _norm(name)
    return next((u for u in meta.universes if _norm(u.name) == n), None)


def _find_character(meta: AkashicMeta, label: str, taken: set[int]) -> Character | None:
    """Personagem cujo nome começa o rótulo ou aparece nele ('Tinaia, the central AI' → 'Tinaia')."""
    lab = _norm(label)
    for idx, c in enumerate(meta.characters):
        cn = _norm(c.name)
        if idx not in taken and cn and (lab == cn or lab.startswith(cn) or re.search(rf"\b{re.escape(cn)}\b", lab)):
            taken.add(idx)
            return c
    return None


def import_lists_from_body(meta: AkashicMeta, body: str) -> str:
    """Copia para os metadados os itens das seções 9.5 e 5.8 do texto. Retorna um relatório curto."""
    lines = body.splitlines()

    listed = _bullets(lines, _section_range(lines, SECTION_UNIVERSES))
    new_u = 0
    for label, text in listed:
        name = label.strip(" .:")
        allowed, rest = split_allowed(text)
        u = _find_universe(meta, name)
        if u is None:
            u = Universe(id=slugify(name), name=name, role="source")
            meta.universes.append(u)
            new_u += 1
        u.active = True
        u.model_sheet = rest
        u.allowed_characters = allowed
    if listed:
        names = {_norm(label) for label, _ in listed}
        for u in meta.universes:
            if u.active and _norm(u.name) not in names:
                u.active = False  # a 9.5 do texto é a lista fechada que valia até agora

    cast = _bullets(lines, _section_range(lines, SECTION_CAST))
    taken: set[int] = set()
    new_c = 0
    for label, text in cast:
        c = _find_character(meta, label, taken)
        if c is None:
            c = Character(name=label.strip(" .:"), role="supporting")
            meta.characters.append(c)
            taken.add(len(meta.characters) - 1)
            new_c += 1
        c.sheet_label = label
        c.sheet = text

    meta.lists_synced = True
    return (f"Importados do texto: {len(listed)} universo(s) da 9.5 ({new_u} novo(s)) e "
            f"{len(cast)} ficha(s) da 5.8 ({new_c} personagem(ns) novo(s)).")


# ── Geração das listas a partir dos metadados ─────────────────

def universe_line(u: Universe) -> str:
    allowed = f"Allowed: {', '.join(u.allowed_characters)}. " if u.allowed_characters else ""
    sheet = u.model_sheet.strip() or TODO.format("what exists here and what never appears")
    return f"- **{u.name}.** {allowed}{sheet}".rstrip()


def character_line(c: Character) -> str:
    label = c.sheet_label.strip() or f"{c.name}."
    sheet = c.sheet.strip() or TODO.format("one paragraph: origin, look, personality, powers, how they speak")
    return f"- **{label}** {sheet}".rstrip()


def _replace_list(lines: list[str], prefix: str, new_items: list[str]) -> list[str]:
    rng = _section_range(lines, prefix)
    if not rng:
        return lines
    start, end = rng
    inner = lines[start + 1:end]
    idx = [i for i, l in enumerate(inner) if _BULLET.match(l.strip())]
    if idx:
        head, foot = inner[:idx[0]], inner[idx[-1] + 1:]
    else:
        # Sem itens ainda: a lista entra depois do primeiro parágrafo da seção.
        head, foot = inner, []
    head = [l for l in head if not l.startswith(SYNC_NOTE[:40])]
    while head and not head[0].strip():
        head.pop(0)
    new_inner = ["", SYNC_NOTE, ""] + head
    while new_inner and not new_inner[-1].strip():
        new_inner.pop()
    new_inner += [""] + new_items
    if foot and foot[0].strip():
        new_inner.append("")
    new_inner += foot if foot else [""]
    return lines[:start + 1] + new_inner + lines[end:]


def render_lists_into_body(body: str, meta: AkashicMeta) -> str:
    lines = body.split("\n")
    lines = _replace_list(lines, SECTION_UNIVERSES, [universe_line(u) for u in meta.universes if u.active])
    lines = _replace_list(lines, SECTION_CAST, [character_line(c) for c in meta.characters])
    return "\n".join(lines)


def sync_text(text: str) -> str:
    """
    Arquivo completo com as seções 9.5 e 5.8 geradas a partir dos metadados.
    Sem bloco de metadados (formato v1), devolve o texto igual.
    """
    meta, body = read_meta(text)
    if meta is None:
        return text
    if not meta.lists_synced:
        import_lists_from_body(meta, body)
    return write_meta(render_lists_into_body(body, meta), meta)
