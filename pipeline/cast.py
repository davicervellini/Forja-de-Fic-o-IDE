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
from pipeline.akashic_schema import ROLES, STRUCTURES, AkashicMeta, Character, Universe, read_meta, slugify, write_meta
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


def validate(characters: list[Character], universes: list[Universe]) -> list[str]:
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
    return problems


def save(project: StoryProject, characters: list[dict], universes: list[dict], structure: str | None = None) -> str:
    """Grava personagens e universos no registro, gera as listas 5.8/9.5 e recompila o modelo."""
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
    for u in unis:
        u.name = u.name.strip()
        u.allowed_characters = [a.strip() for a in u.allowed_characters if a.strip()]
    problems = validate(chars, unis)
    if problems:
        raise ValueError(" ".join(problems))

    text = _text(project)
    _, body = read_meta(text)
    meta.characters = chars
    meta.universes = unis
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

_ROSTER_NAME = re.compile(r"^\s*[*\-]\s*Name\s*:\s*(.+?)\s*$", re.I | re.M)


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
        "character_roles": CHARACTER_ROLES,
        "universe_roles": ROLES,
        "structures": STRUCTURES,
        "roster_missing": missing,
        "appearances": appearances(project, [c.name for c in chars]),
    }
