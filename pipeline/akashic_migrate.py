"""
akashic_migrate.py — Converte um Registro Akáshico antigo (v1, só Markdown) para o formato padrão v2.

O corpo do arquivo não é reescrito: ele já segue a numeração padrão. A migração acrescenta o bloco de metadados
(universos, personagens principais) extraído das seções 5 e 9 e cadastra também os universos do catálogo que ainda
não estavam na história, marcados como reserva (`active: false`) para poderem ser usados na importação da wiki sem
alterar a lista fechada da história.
"""

import re
from pathlib import Path

from pipeline.akashic import extract_section
from pipeline.akashic_catalog import CATALOG, match_universe
from pipeline.akashic_schema import (
    AkashicMeta, Character, Universe, read_meta, slugify, write_meta,
)
from pipeline.io_utils import read_file, write_file

_BULLET = re.compile(r"^- \*\*(?P<name>[^*]+?)\.\*\*\s*(?P<body>.*)$")
_ALLOWED = re.compile(r"Allowed:\s*(?P<list>.*?)(?:\.\s|\.$|$)")
_HEADING_5 = re.compile(r"^### 5\.(?P<n>\d+) (?P<name>.+)$", re.M)
_BASE_HINT = re.compile(r"(.+?) como origem da tecnologia", re.I)


def _allowed_names(body: str) -> list[str]:
    m = _ALLOWED.search(body)
    if not m:
        return []
    raw = re.sub(r"\([^)]*\)", "", m.group("list"))
    names = []
    for part in re.split(r",| and ", raw):
        name = re.split(r"\bwith\b", part)[0].strip(' ."“”')
        if name:
            names.append(name)
    return names


def _base_universe_names(text: str) -> str:
    """Trecho da seção 9.1 que diz quais universos são a origem da tecnologia (ex.: 'Stargate Atlantis e Halo')."""
    section = extract_section(text, "### 9.1 ")
    m = _BASE_HINT.search(section.replace("\n", " "))
    return m.group(1) if m else ""


def parse_v1(text: str) -> AkashicMeta:
    """Metadados de um arquivo v1: universos da seção 9.5, personagens principais da seção 5."""
    title_m = re.search(r"^# (?:Bíblia do Mundo:\s*)?(.+)$", text, re.M)
    meta = AkashicMeta(title=title_m.group(1).strip() if title_m else "", structure="multiverse", language="en",
                       answers={"migrated_from": 1})
    base_text = _base_universe_names(text).lower()
    for line in extract_section(text, "### 9.5 ").splitlines():
        m = _BULLET.match(line.strip())
        if not m:
            continue
        name, body = m.group("name").strip(), m.group("body")
        entry = match_universe(name)
        uid = entry["id"] if entry else slugify(name)
        role = "base" if name.lower().split(",")[0] in base_text else "source"
        meta.universes.append(Universe(id=uid, name=name, role=role, wiki=entry["wiki"] if entry else "",
                                       active=True, allowed_characters=_allowed_names(body)))
    for m in _HEADING_5.finditer(text):
        n = int(m.group("n"))
        if 2 <= n <= 6:  # 5.2 a 5.6: personagens principais no padrão do arquivo
            name = m.group("name").split(",")[0].strip()
            meta.characters.append(Character(name=name, role="protagonist" if n == 2 else "supporting"))
    return meta


def add_reserve_universes(meta: AkashicMeta) -> int:
    """Cadastra os universos do catálogo que faltam, como reserva. Retorna quantos foram adicionados."""
    have_wikis = {u.wiki for u in meta.universes if u.wiki}
    added = 0
    for e in CATALOG:
        if e["wiki"] in have_wikis or meta.universe(e["id"]):
            continue
        meta.universes.append(Universe(id=e["id"], name=e["name"], role="source", wiki=e["wiki"], active=False))
        added += 1
    return added


def migrate_text(text: str) -> tuple[str, str]:
    """(texto v2, relatório). Se já for v2, devolve o texto igual."""
    existing, body = read_meta(text)
    if existing is not None:
        return text, "Arquivo já está no formato v2: nada a fazer."
    meta = parse_v1(body)
    reserve = add_reserve_universes(meta)
    active = sum(u.active for u in meta.universes)
    report = (f"Migrado para v2: {active} universo(s) ativo(s) da seção 9.5, {reserve} de reserva do catálogo, "
              f"{len(meta.characters)} personagem(ns) principal(is).")
    return write_meta(body, meta), report


def migrate_file(project_dir: str | Path) -> tuple[bool, str]:
    """Migra `registro_akashico.md` do projeto no lugar. Faça backup antes (o chamador decide onde)."""
    path = Path(project_dir) / "registro_akashico.md"
    if not path.exists():
        return False, f"Arquivo não encontrado: {path.name}"
    new_text, report = migrate_text(read_file(path))
    if new_text != read_file(path):
        write_file(path, new_text)
    return True, report
