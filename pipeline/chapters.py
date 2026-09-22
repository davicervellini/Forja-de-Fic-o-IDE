"""
chapters.py — Edição, versões e informações de um capítulo já escrito.

Toda mudança que substitui um texto de capítulo (edição manual, refazer, restaurar uma
versão) guarda antes o que havia em `capitulo_NN/versoes/<data>_<motivo>/`. Nada do
que o usuário ou o modelo escreveu se perde.

O arquivo `capitulo_NN/info.json` guarda marcas do capítulo, por exemplo
`memory_stale`: o texto final mudou depois que a memória da história foi atualizada
com ele, e o resumo, a memória, o roster e os threads podem estar desatualizados.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from pipeline.io_utils import read_file, write_file
from pipeline.project import SNAPSHOT_FILENAME, StoryProject

VERSIONS_DIRNAME = "versoes"
INFO_FILENAME = "info.json"

# Arquivos de texto de um capítulo que entram numa versão.
VERSIONED_FILES = (
    "premissa.md",
    "rascunho.md",
    "capitulo_final.md",
    "resumo.md",
    "cenas_planejadas.md",
    "polimento_descartado.md",
    "consistencia.md",
)

REASON_LABELS = {
    "edicao": "Edição manual",
    "refazer": "Antes de refazer",
    "restaurar": "Antes de restaurar outra versão",
    "memoria": "Antes de atualizar a memória",
    "parcial": "Tentativa interrompida",
}


# ── Marcas do capítulo ───────────────────────────────────────

def read_info(project: StoryProject, num: int) -> dict:
    path = project.chapter_dir(num) / INFO_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(read_file(path))
    except (json.JSONDecodeError, OSError):
        return {}


def update_info(project: StoryProject, num: int, **changes) -> dict:
    info = read_info(project, num)
    info.update(changes)
    write_file(project.chapter_dir(num) / INFO_FILENAME, json.dumps(info, ensure_ascii=False, indent=2))
    return info


# ── Posição do capítulo na história ─────────────────────────

def later_done(project: StoryProject, num: int) -> list[int]:
    """Capítulos concluídos depois deste. Eles foram escritos com este na memória."""
    return [e.num for e in project.scan_chapters() if e.num > num and e.status == "done"]


def has_snapshot(project: StoryProject, num: int) -> bool:
    return (project.chapter_dir(num) / SNAPSHOT_FILENAME).exists()


def load_snapshot(project: StoryProject, num: int) -> dict | None:
    path = project.chapter_dir(num) / SNAPSHOT_FILENAME
    if not path.exists():
        return None
    return json.loads(read_file(path))


# ── Versões ──────────────────────────────────────────────────

def versions_dir(project: StoryProject, num: int) -> Path:
    return project.chapter_dir(num) / VERSIONS_DIRNAME


def archive_version(project: StoryProject, num: int, reason: str) -> Path | None:
    """
    Copia os textos atuais do capítulo para uma pasta de versão nova.
    Retorna a pasta criada, ou None se o capítulo ainda não tem nenhum texto.
    """
    ch_dir = project.chapter_dir(num)
    present = [name for name in VERSIONED_FILES if (ch_dir / name).exists()]
    if not present:
        return None
    base = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{reason}"
    dest = versions_dir(project, num) / base
    suffix = 2
    while dest.exists():
        dest = versions_dir(project, num) / f"{base}_{suffix}"
        suffix += 1
    dest.mkdir(parents=True)
    for name in present:
        shutil.copy2(ch_dir / name, dest / name)
    return dest


def list_versions(project: StoryProject, num: int) -> list[dict]:
    """Versões guardadas do capítulo, da mais nova para a mais antiga."""
    root = versions_dir(project, num)
    if not root.is_dir():
        return []
    out = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        parts = d.name.split("_")
        try:
            created = datetime.strptime("_".join(parts[:2]), "%Y%m%d_%H%M%S").isoformat(timespec="seconds")
        except ValueError:
            created = ""
        reason = parts[2] if len(parts) > 2 else ""
        final = d / "capitulo_final.md"
        draft = d / "rascunho.md"
        text_path = final if final.exists() else draft if draft.exists() else None
        out.append({
            "id": d.name,
            "created": created,
            "reason": reason,
            "reason_label": REASON_LABELS.get(reason, reason),
            "words": len(read_file(text_path).split()) if text_path else 0,
            "files": sorted(p.name for p in d.iterdir() if p.is_file()),
        })
    return out


def read_version(project: StoryProject, num: int, version_id: str) -> dict[str, str]:
    d = _version_path(project, num, version_id)
    return {p.name: read_file(p) for p in d.iterdir() if p.is_file()}


def restore_version(project: StoryProject, num: int, version_id: str) -> str:
    """
    Põe os textos de uma versão de volta no capítulo. O que estava lá antes vira uma
    versão nova. A memória da história não é recalculada: o capítulo fica marcado como
    `memory_stale` se estava concluído.
    """
    src = _version_path(project, num, version_id)
    ch_dir = project.chapter_dir(num)
    archive_version(project, num, "restaurar")
    for name in VERSIONED_FILES:
        if (ch_dir / name).exists() and not (src / name).exists():
            (ch_dir / name).unlink()
    for name in VERSIONED_FILES:
        if (src / name).exists():
            shutil.copy2(src / name, ch_dir / name)
    if (ch_dir / "resumo.md").exists():
        update_info(project, num, memory_stale=True)
    return f"Capítulo {num:02d}: versão {version_id} restaurada."


def _version_path(project: StoryProject, num: int, version_id: str) -> Path:
    root = versions_dir(project, num).resolve()
    d = (root / version_id).resolve()
    # O id vem da interface: nunca pode sair da pasta de versões.
    if d.parent != root or not d.is_dir():
        raise FileNotFoundError(f"Versão não encontrada: {version_id}")
    return d


# ── Edição manual ────────────────────────────────────────────

def save_premise(project: StoryProject, num: int, text: str):
    write_file(project.chapter_dir(num) / "premissa.md", text.strip())


def save_final_text(project: StoryProject, num: int, text: str) -> str:
    """
    Grava o texto final editado pelo usuário, guardando antes a versão anterior.
    Se o capítulo já estava concluído, a memória da história fica marcada como
    desatualizada até o usuário pedir "Atualizar memória".
    """
    ch_dir = project.chapter_dir(num)
    path = ch_dir / "capitulo_final.md"
    old = read_file(path) if path.exists() else ""
    if old.strip() == text.strip():
        return "Nada mudou."
    archive_version(project, num, "edicao")
    write_file(path, text.strip() + "\n")
    msg = f"Capítulo {num:02d} salvo."
    if (ch_dir / "resumo.md").exists():
        update_info(project, num, memory_stale=True, edited=True)
        msg += " O resumo e a memória ainda são do texto antigo: use \"Atualizar memória\"."
    else:
        update_info(project, num, edited=True)
    return msg


def chapter_title(text: str, num: int) -> str:
    lines = (text or "").strip().splitlines()
    first = lines[0].strip().lstrip("#").strip() if lines else ""
    if first.lower().startswith(("chapter", "capítulo", "capitulo")):
        return first
    return f"Capítulo {num:02d}"
