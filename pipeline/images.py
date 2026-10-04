"""
images.py — Imagens do projeto: retratos dos personagens, imagens dos locais e a capa.

Ficam dentro da pasta do projeto, em `imagens/`, e são citadas pelo caminho relativo
("imagens/alexei_ivanov.jpeg") nos metadados do registro (Character.images, Location.images)
e no projeto.json ("cover"). Endereços http(s) (imagens da wiki) continuam valendo como estão.
Os modelos de texto não leem imagens: elas servem para o autor e para a exportação.
"""

import re
import shutil
import unicodedata
from pathlib import Path

IMAGES_DIRNAME = "imagens"
EXTENSIONS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
              ".gif": "image/gif"}
MAX_BYTES = 15 * 1024 * 1024


def images_dir(project_dir: Path) -> Path:
    return Path(project_dir) / IMAGES_DIRNAME


def safe_name(filename: str) -> str:
    """Nome de arquivo sem acentos, espaços nem caminho ("R.A.S.P.U.T.I.N..jpeg" → "r_a_s_p_u_t_i_n.jpeg")."""
    p = Path(filename or "imagem")
    ext = p.suffix.lower()
    if ext not in EXTENSIONS:
        raise ValueError(f"Formato de imagem não aceito: {ext or 'sem extensão'} (use JPG, PNG, WEBP ou GIF).")
    stem = unicodedata.normalize("NFKD", p.stem).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower() or "imagem"
    return f"{stem}{ext}"


def _unique(folder: Path, name: str) -> Path:
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return path


def save_image(project_dir: Path, filename: str, data: bytes) -> str:
    """Grava a imagem em imagens/ e devolve o caminho relativo para os metadados."""
    if len(data) > MAX_BYTES:
        raise ValueError("Imagem grande demais (máximo de 15 MB).")
    folder = images_dir(project_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique(folder, safe_name(filename))
    path.write_bytes(data)
    return f"{IMAGES_DIRNAME}/{path.name}"


def import_file(project_dir: Path, source: Path, name: str | None = None) -> str:
    """Copia um arquivo de imagem para imagens/ (sem duplicar se já estiver lá com o mesmo conteúdo)."""
    source = Path(source)
    folder = images_dir(project_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe_name(name or source.name)
    if target.exists() and target.read_bytes() == source.read_bytes():
        return f"{IMAGES_DIRNAME}/{target.name}"
    target = _unique(folder, target.name)
    shutil.copy2(source, target)
    return f"{IMAGES_DIRNAME}/{target.name}"


def resolve(project_dir: Path, rel: str) -> Path | None:
    """Caminho no disco de uma imagem do projeto, só dentro de imagens/ (nada de ../)."""
    if not rel or re.match(r"^https?://", rel):
        return None
    folder = images_dir(project_dir).resolve()
    name = rel.split("/", 1)[1] if rel.startswith(f"{IMAGES_DIRNAME}/") else rel
    path = (folder / name).resolve()
    if folder not in path.parents or not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        return None
    return path


def media_type(path: Path) -> str:
    return EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
