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
    # Se estiver rodando como executável compilado (.exe)
    PROJECT_ROOT = Path(sys.executable).parent
else:
    # Se estiver rodando como script Python normal
    PROJECT_ROOT = Path(__file__).resolve().parent.parent

ENV_PATH = PROJECT_ROOT / ".env"

# Carrega o .env (silencioso se não existir)
load_dotenv(ENV_PATH)


def _optional_int(name: str) -> int | None:
    """Lê um inteiro opcional do .env. Vazio ou ausente vira None."""
    value = os.getenv(name, "").strip()
    return int(value) if value else None


# ── Diretório de projetos ────────────────────────────────────
PROJECTS_DIR: Path = PROJECT_ROOT / "projetos"

# ── Servidor Ollama ──────────────────────────────────────────
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_GENERATE_URL: str = f"{OLLAMA_BASE_URL}/api/generate"

# ── Modelos ──────────────────────────────────────────────────
MODEL_DRAFTING: str = os.getenv("MODEL_DRAFTING", "llama3.1:8b")
MODEL_REFINING: str = os.getenv("MODEL_REFINING", "gemma3:12b")
MODEL_SUMMARIZING: str = os.getenv("MODEL_SUMMARIZING", "llama3.1:8b")

# ── Hiperparâmetros ──────────────────────────────────────────
DRAFTING_TEMPERATURE: float = float(os.getenv("DRAFTING_TEMPERATURE", "0.7"))
DRAFTING_NUM_CTX: int = int(os.getenv("DRAFTING_NUM_CTX", "12288"))

REFINING_TEMPERATURE: float = float(os.getenv("REFINING_TEMPERATURE", "0.4"))
REFINING_NUM_CTX: int = int(os.getenv("REFINING_NUM_CTX", "10240"))

SUMMARIZING_TEMPERATURE: float = float(os.getenv("SUMMARIZING_TEMPERATURE", "0.2"))
SUMMARIZING_NUM_CTX: int = int(os.getenv("SUMMARIZING_NUM_CTX", "8192"))

# Camadas do modelo na GPU (opção num_gpu do Ollama). None = automático.
DRAFTING_NUM_GPU: int | None = _optional_int("DRAFTING_NUM_GPU")
REFINING_NUM_GPU: int | None = _optional_int("REFINING_NUM_GPU")
SUMMARIZING_NUM_GPU: int | None = _optional_int("SUMMARIZING_NUM_GPU")

# ── Continuidade ─────────────────────────────────────────────
RECENT_SUMMARIES_KEPT: int = int(os.getenv("RECENT_SUMMARIES_KEPT", "4"))
SUMMARY_MAX_WORDS: int = int(os.getenv("SUMMARY_MAX_WORDS", "250"))
STORY_SO_FAR_MAX_WORDS: int = int(os.getenv("STORY_SO_FAR_MAX_WORDS", "500"))

# ── Timeout (segundos) ──────────────────────────────────────
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "3600"))
