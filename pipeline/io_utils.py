"""
io_utils.py — Utilitários de leitura e gravação de arquivos.

Todas as operações usam UTF-8 e criam diretórios pai automaticamente.
Inclui função de salvamento de fragmento para recuperação em caso de erro.
"""

from pathlib import Path
from datetime import datetime


def read_file(path: Path) -> str:
    """Lê um arquivo de texto em UTF-8 e retorna seu conteúdo."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    return path.read_text(encoding="utf-8")


def write_file(path: Path, content: str) -> Path:
    """
    Grava conteúdo em um arquivo UTF-8.
    Cria diretórios pai se necessário. Retorna o caminho gravado.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def read_premise(path: Path) -> str | None:
    """
    Tenta ler o arquivo de premissa.
    Retorna None se o arquivo não existir ou estiver vazio.
    """
    path = Path(path)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text if text else None


def save_fragment(path: Path, content: str) -> Path | None:
    """
    Salva um fragmento parcial de texto em caso de erro.
    Adiciona sufixo '_fragmento' ao nome do arquivo.
    Retorna o caminho salvo ou None se não houver conteúdo.
    """
    if not content or not content.strip():
        return None

    path = Path(path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fragment_name = f"{path.stem}_fragmento_{timestamp}{path.suffix}"
    fragment_path = path.parent / fragment_name

    return write_file(fragment_path, content)


def ensure_chapter_dir(output_dir: Path, chapter_num: int) -> Path:
    """
    Cria e retorna o diretório de saída para um capítulo específico.
    Exemplo: output/capitulo_01/
    """
    chapter_dir = Path(output_dir) / f"capitulo_{chapter_num:02d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    return chapter_dir
