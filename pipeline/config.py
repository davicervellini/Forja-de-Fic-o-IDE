"""
config.py — Carregamento centralizado de configuração.

Lê o arquivo .env na raiz do projeto e expõe constantes tipadas
para todos os outros módulos do pipeline.

Os paths de arquivos de cada história agora vivem em StoryProject.
Aqui ficam apenas paths globais e configurações de modelos.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ── Caminhos do projeto ─────────────────────────────────────
if getattr(sys, 'frozen', False):
    PROJECT_ROOT = Path(sys.executable).parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent

ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else None

PROJECTS_DIR: Path = PROJECT_ROOT / "projetos"
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_GENERATE_URL: str = f"{OLLAMA_BASE_URL}/api/generate"
MODEL_DRAFTING: str = os.getenv("MODEL_DRAFTING", "llama3.1:8b")
MODEL_REFINING: str = os.getenv("MODEL_REFINING", "gemma3:12b")
MODEL_SUMMARIZING: str = os.getenv("MODEL_SUMMARIZING", "llama3.1:8b")
DRAFTING_TEMPERATURE: float = float(os.getenv("DRAFTING_TEMPERATURE", "0.7"))
DRAFTING_NUM_CTX: int = int(os.getenv("DRAFTING_NUM_CTX", "12288"))
REFINING_TEMPERATURE: float = float(os.getenv("REFINING_TEMPERATURE", "0.4"))
REFINING_NUM_CTX: int = int(os.getenv("REFINING_NUM_CTX", "10240"))
SUMMARIZING_TEMPERATURE: float = float(os.getenv("SUMMARIZING_TEMPERATURE", "0.2"))
SUMMARIZING_NUM_CTX: int = int(os.getenv("SUMMARIZING_NUM_CTX", "8192"))
DRAFTING_NUM_GPU: int | None = _optional_int("DRAFTING_NUM_GPU")
REFINING_NUM_GPU: int | None = _optional_int("REFINING_NUM_GPU")
SUMMARIZING_NUM_GPU: int | None = _optional_int("SUMMARIZING_NUM_GPU")
RECENT_SUMMARIES_KEPT: int = int(os.getenv("RECENT_SUMMARIES_KEPT", "4"))
SUMMARY_MAX_WORDS: int = int(os.getenv("SUMMARY_MAX_WORDS", "250"))
STORY_SO_FAR_MAX_WORDS: int = int(os.getenv("STORY_SO_FAR_MAX_WORDS", "700"))
DYNAMIC_MEMORY_MAX_WORDS: int = int(os.getenv("DYNAMIC_MEMORY_MAX_WORDS", "600"))
ROSTER_MAX_CHARS: int = int(os.getenv("ROSTER_MAX_CHARS", "40"))
OPEN_THREADS_MAX: int = int(os.getenv("OPEN_THREADS_MAX", "15"))
CONSISTENCY_CHECK_ENABLED: bool = os.getenv("CONSISTENCY_CHECK_ENABLED", "true").lower() in ("1", "true", "yes")
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "3600"))
