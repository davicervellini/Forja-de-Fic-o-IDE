"""
cast.py — Personagens e universos do Registro Akáshico, para a tela de gestão.

A fonte é o bloco de metadados (formato v2) no topo de `registro_akashico.md`. Ao salvar,
as listas das seções 5.8 (fichas do elenco) e 9.5 (universos) são geradas de novo a
partir dos metadados e o `registro_modelo.md`, que vai para o modelo, é recompilado.

Também cruza o registro com a história já escrita: personagens que o roster da memória
conhece mas o registro não tem, e em que capítulos cada personagem aparece.
"""

import re
from dataclasses import asdict

from pipeline.akashic import build_registro_modelo
from pipeline.akashic_migrate import migrate_text
from pipeline.akashic_schema import (ROLES, STRUCTURES, AkashicMeta, Character, Location, Universe, read_meta,
                                     slugify, write_meta)
from pipeline.akashic_sync import import_lists_from_body, render_lists_into_body
from pipeline.io_utils import read_file, write_file
from pipeline.project import StoryProject

CHARACTER_ROLES = {
    "protagonist": "Protagonista",
    "supporting": "Coadjuvante",
    "antagonist": "Antagonista",
}
BACKUP_NAME = "registro_akashico.anterior.md"


def _text(project: StoryProject) -> str:
    return read_file(project.akashic_path) if project.akashic_path.exists() else ""


def load(project: StoryProject) -> tuple[str, AkashicMeta | None]:
    """
    ("v2" | "v1" | "none", metadados). No v2 as listas 5.8 e 9.5 do texto são importadas
    na primeira leitura, como o editor antigo fazia.
    """
    text = _text(project)
    if not text.strip():
        return "none", None
    meta, body = read_meta(text)
    if meta is None:
        return "v1", None
    if not meta.lists_synced:
        import_lists_from_body(meta, body)
    return "v2", meta


def migrate(project: StoryProject) -> str:
    """Converte um registro v1 para v2 (lê universos e personagens do texto)."""
    text = _text(project)
    new_text, report = migrate_text(text)
    if new_text != text:
        write_file(project.project_dir / BACKUP_NAME, text)
        write_file(project.akashic_path, new_text)
    return report


def validate(characters: list[Character], universes: list[Universe],
             locations: list[Location] | None = None) -> list[str]:
    problems = []
    names = [c.name.strip() for c in characters]
    if any(not n for n in names):
        problems.append("Todo personagem precisa de nome.")
    dup = sorted({n for n in names if n and names.count(n) > 1})
    if dup:
        problems.append(f"Personagem repetido: {', '.join(dup)}.")
    for c in characters:
        if c.role not in CHARACTER_ROLES:
            problems.append(f"{c.name}: papel desconhecido ({c.role}).")
    unames = [u.name.strip() for u in universes]
    if any(not n for n in unames):
        problems.append("Todo universo precisa de nome.")
    ids = [u.id for u in universes]
    dup_u = sorted({i for i in ids if ids.count(i) > 1})
    if dup_u:
        problems.append(f"Universo repetido: {', '.join(dup_u)}.")
    for u in universes:
        if u.role not in ROLES:
            problems.append(f"{u.name}: papel de universo desconhecido ({u.role}).")
    known = set(ids)
    for c in characters:
        if c.universe and c.universe not in known:
            problems.append(f"{c.name}: universo de origem '{c.universe}' não existe.")
    locations = locations or []
    lnames = [loc.name.strip() for loc in locations]
    if any(not n for n in lnames):
        problems.append("Todo local precisa de nome.")
    dup_l = sorted({n for n in lnames if n and lnames.count(n) > 1})
    if dup_l:
        problems.append(f"Local repetido: {', '.join(dup_l)}.")
    for loc in locations:
        if loc.universe and loc.universe not in known:
            problems.append(f"{loc.name}: universo '{loc.universe}' não existe.")
        if loc.parent and loc.parent not in lnames:
            problems.append(f"{loc.name}: fica dentro de '{loc.parent}', que não está na lista de locais.")
        if loc.parent and loc.parent == loc.name:
            problems.append(f"{loc.name}: um local não pode ficar dentro dele mesmo.")
    return problems


def _chapter_number(value) -> int:
    """Capítulo de estreia vindo da tela (texto, número ou vazio); 0 quando não informado."""
    try:
        return max(0, int(str(value).strip() or 0))
    except (TypeError, ValueError):
        return 0


def save(project: StoryProject, characters: list[dict], universes: list[dict], structure: str | None = None,
         locations: list[dict] | None = None) -> str:
    """
    Grava personagens, universos e locais no registro, gera as listas 5.8/9.5 e recompila o
    modelo. `locations` None mantém os locais que já estavam.
    """
    fmt, meta = load(project)
    if fmt != "v2" or meta is None:
        raise ValueError("O Registro Akáshico precisa estar no formato v2. Converta primeiro.")
    chars = [Character(**{k: v for k, v in c.items() if k in Character.__dataclass_fields__}) for c in characters]
    unis = []
    for u in universes:
        data = {k: v for k, v in u.items() if k in Universe.__dataclass_fields__}
        data["id"] = (data.get("id") or slugify(data.get("name", ""))).strip()
        unis.append(Universe(**data))
    for c in chars:
        c.name = c.name.strip()
        c.debut_chapter = _chapter_number(c.debut_chapter)
        c.voice = (c.voice or "").strip()
    for u in unis:
        u.name = u.name.strip()
        u.debut_chapter = _chapter_number(u.debut_chapter)
        u.allowed_characters = [a.strip() for a in u.allowed_characters if a.strip()]
    if locations is None:
        locs = list(meta.locations)
    else:
        locs = [Location(**{k: v for k, v in loc.items() if k in Location.__dataclass_fields__}) for loc in locations]
        for loc in locs:
            loc.name = loc.name.strip()
            loc.debut_chapter = _chapter_number(loc.debut_chapter)
            loc.parent = loc.parent.strip()
    problems = validate(chars, unis, locs)
    if problems:
        raise ValueError(" ".join(problems))

    text = _text(project)
    _, body = read_meta(text)
    meta.characters = chars
    meta.universes = unis
    meta.locations = locs
    if structure in STRUCTURES:
        meta.structure = structure
    meta.lists_synced = True
    body = render_lists_into_body(body, meta)
    # projetos/ não tem histórico no git: guarda a versão anterior do registro inteiro.
    write_file(project.project_dir / BACKUP_NAME, text)
    write_file(project.akashic_path, write_meta(body, meta))
    ok, msg = build_registro_modelo(project.project_dir)
    if not ok:
        raise ValueError(f"Salvo, mas o registro do modelo falhou: {msg}")
    return msg


# ── Cruzamento com a história escrita ────────────────────────

_ROSTER_NAME = re.compile(r"^\s*[*\-]\s*(?:Name|Nome|Nombre)\s*:\s*(.+?)\s*$", re.I | re.M)


def roster_entries(roster: str) -> list[dict]:
    """Personagens do roster da memória: [{name, text}] com o bloco de cada um."""
    out = []
    matches = list(_ROSTER_NAME.finditer(roster or ""))
    for i, m in enumerate(matches):
        start = roster.rfind("\n\n", 0, m.start())
        end = matches[i + 1].start() if i + 1 < len(matches) else len(roster)
        block = roster[start + 1 if start >= 0 else 0:end].strip()
        # O cabeçalho em maiúsculas do próximo bloco não pertence a este.
        lines = block.splitlines()
        while lines and lines[-1].strip() and not lines[-1].lstrip().startswith(("*", "-")):
            lines.pop()
        out.append({"name": m.group(1).strip(), "text": "\n".join(lines).strip()})
    return out


def _mentions(name: str, text: str) -> bool:
    first = name.split()[0] if name.split() else name
    patterns = {name}
    # Nome composto: o primeiro nome sozinho também conta ("Arthur" em "Arthur Galhardo").
    if len(first) >= 4 and first.lower() not in {"the", "first", "later"}:
        patterns.add(first)
    return any(re.search(rf"\b{re.escape(p)}\b", text, re.I) for p in patterns)


def appearances(project: StoryProject, names: list[str]) -> dict[str, list[int]]:
    """Capítulos (com texto final) em que cada nome aparece."""
    out: dict[str, list[int]] = {n: [] for n in names}
    for e in project.scan_chapters():
        text = e.final or e.draft
        if not text:
            continue
        for n in names:
            if _mentions(n, text):
                out[n].append(e.num)
    return out


def _same(a: str, b: str) -> bool:
    na, nb = a.lower().strip(), b.lower().strip()
    return na == nb or na.startswith(nb + " ") or nb.startswith(na + " ")


def overview(project: StoryProject) -> dict:
    fmt, meta = load(project)
    chars = meta.characters if meta else []
    unis = meta.universes if meta else []
    roster = roster_entries(project.character_roster)
    missing = [r for r in roster if not any(_same(r["name"], c.name) for c in chars)]
    return {
        "format": fmt,
        "structure": meta.structure if meta else "single",
        "characters": [asdict(c) for c in chars],
        "universes": [asdict(u) for u in unis],
        "locations": [asdict(loc) for loc in (meta.locations if meta else [])],
        "character_roles": CHARACTER_ROLES,
        "universe_roles": ROLES,
        "structures": STRUCTURES,
        "roster_missing": missing,
        "appearances": appearances(project, [c.name for c in chars]),
    }


# ── Quem já está na história ─────────────────────────────────

# "visitor from Chapter 205", "created in Chapter 5", "arrives in Chapter 7".
_DEBUT = re.compile(
    r"\b(?:from|since|starting (?:in|from)|created in|introduced in|debuts? in|arrives? in|"
    r"(?:first )?appears? (?:first )?in)\s+(?:the\s+)?chapters?\s+(\d+)",
    re.I,
)


def debut_of(character: Character) -> int:
    """Capítulo de estreia: o campo do registro, senão o que a ficha disser. 0 = desconhecido."""
    if character.debut_chapter:
        return character.debut_chapter
    m = _DEBUT.search(character.sheet or "")
    return int(m.group(1)) if m else 0


def _planned_appearances(project: StoryProject, names: list[str], before: int) -> dict[str, list[int]]:
    """
    Capítulos concluídos antes de `before` em que o nome aparece no texto E está no elenco da
    premissa (e não no "não pode aparecer"). Um capítulo ruim, que pôs gente fora da hora, não
    apresenta ninguém: só conta quem o autor planejou.
    """
    from pipeline.premise import from_text as premise_from_text
    out: dict[str, list[int]] = {n: [] for n in names}
    for e in project.scan_chapters():
        if e.num >= before or e.status != "done" or not e.final:
            continue
        form = premise_from_text(e.premise)
        planned = " ".join(form.characters).lower()
        forbidden = form.must_not.lower()
        for n in names:
            first = n.split()[0].lower() if n.split() else n.lower()
            in_cast = n.lower() in planned or (len(first) >= 4 and first in planned)
            if in_cast and n.lower() not in forbidden and _mentions(n, e.final):
                out[n].append(e.num)
    return out


def cast_status(project: StoryProject, num: int, meta: AkashicMeta | None = None) -> tuple[list[str], list[str]]:
    """
    (personagens que podem estar no capítulo `num`, os que ainda não). Vale o capítulo de estreia
    do registro; sem ele, quem já esteve no elenco planejado de um capítulo concluído antes.
    Protagonistas estão sempre na história.
    """
    if meta is None:
        _, meta = load(project)
    if not meta:
        return [], []
    unknown = [c.name for c in meta.characters if c.role != "protagonist" and not debut_of(c)]
    seen = _planned_appearances(project, unknown, num) if unknown else {}
    introduced = []
    for c in meta.characters:
        debut = debut_of(c)
        if c.role == "protagonist" or (debut and debut <= num) or (not debut and seen.get(c.name)):
            introduced.append(c.name)
    return introduced, [c.name for c in meta.characters if c.name not in introduced]

