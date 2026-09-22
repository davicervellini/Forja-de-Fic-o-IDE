"""
config.py — Carregamento centralizado de configuração.

Ordem de precedência, da mais fraca para a mais forte:
1. padrões deste arquivo;
2. variáveis de ambiente e o `.env` ao lado do programa (útil no desenvolvimento);
3. `config.json` na pasta de dados, gravado pela tela ⚙ Configurações.

Pasta de dados (projetos, logs e config.json):
- `FORJA_DATA_DIR`, se definida;
- a pasta do programa, se existir `portable.txt` ao lado dele (modo portátil);
- a pasta do programa, se rodando do código-fonte e `projetos/` já existir ali;
- senão, a pasta do usuário: `%APPDATA%\\Forja de Ficcao` no Windows,
  `~/Library/Application Support/Forja de Ficcao` no macOS e
  `~/.local/share/Forja de Ficcao` no Linux. O executável instalado em
  "Arquivos de Programas" não tem permissão para gravar na própria pasta.

As constantes deste módulo podem mudar em tempo de execução (apply_settings). Por isso
os outros módulos devem ler `config.NOME` na hora de usar, não copiar o valor no import.
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

APP_NAME = "Forja de Ficcao"
FROZEN: bool = bool(getattr(sys, "frozen", False))

# ── Caminhos do programa ─────────────────────────────────────
PROJECT_ROOT: Path = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def _resolve_data_dir() -> Path:
    env = os.getenv("FORJA_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    if (PROJECT_ROOT / "portable.txt").exists():
        return PROJECT_ROOT
    if not FROZEN and (PROJECT_ROOT / "projetos").is_dir():
        return PROJECT_ROOT
    return user_data_dir()


DATA_DIR: Path = _resolve_data_dir()
PROJECTS_DIR: Path = DATA_DIR / "projetos"
LOG_DIR: Path = DATA_DIR / "logs"
SETTINGS_PATH: Path = DATA_DIR / "config.json"


def ensure_data_dirs():
    for d in (DATA_DIR, PROJECTS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ── Tabela de configurações ──────────────────────────────────
# chave: (tipo, padrão). "optint" = inteiro ou vazio (None = automático).

_SPEC: dict[str, tuple[str, object]] = {
    "OLLAMA_BASE_URL": ("str", "http://127.0.0.1:11434"),
    # Idioma da interface e de tudo o que o programa escreve para a pessoa ler (não o da história).
    "UI_LANGUAGE": ("str", "pt-BR"),
    "HARDWARE_PROFILE": ("str", "custom"),
    # Provedor de cada fase: "ollama" (local) ou um serviço na nuvem (pipeline/providers.py).
    "PROVIDER_DRAFTING": ("provider", "ollama"),
    "PROVIDER_REFINING": ("provider", "ollama"),
    "PROVIDER_SUMMARIZING": ("provider", "ollama"),
    "OPENAI_COMPAT_BASE_URL": ("str", "https://openrouter.ai/api/v1"),
    "MODEL_DRAFTING": ("str", "gemma4:12b"),
    "MODEL_REFINING": ("str", "gemma4:12b"),
    "MODEL_SUMMARIZING": ("str", "gemma4:12b"),
    "DRAFTING_TEMPERATURE": ("float", 0.7),
    "DRAFTING_NUM_CTX": ("int", 12288),
    "REFINING_TEMPERATURE": ("float", 0.4),
    "REFINING_NUM_CTX": ("int", 10240),
    "SUMMARIZING_TEMPERATURE": ("float", 0.2),
    "SUMMARIZING_NUM_CTX": ("int", 8192),
    "DRAFTING_NUM_GPU": ("optint", None),
    "REFINING_NUM_GPU": ("optint", None),
    "SUMMARIZING_NUM_GPU": ("optint", None),
    "RECENT_SUMMARIES_KEPT": ("int", 4),
    "SUMMARY_MAX_WORDS": ("int", 250),
    "STORY_SO_FAR_MAX_WORDS": ("int", 700),
    "DYNAMIC_MEMORY_MAX_WORDS": ("int", 600),
    "ROSTER_MAX_CHARS": ("int", 40),
    "OPEN_THREADS_MAX": ("int", 15),
    "CHAPTER_TARGET_WORDS": ("int", 2200),
    "SCENE_MIN_RATIO": ("float", 0.6),
    "SCENE_MAX_CONTINUATIONS": ("int", 2),
    "SCENE_BREAK": ("str", "* * *"),
    "DRAFTING_REPEAT_PENALTY": ("float", 1.15),
    "DRAFTING_REPEAT_LAST_N": ("int", 1024),
    "PREVIOUS_CHAPTER_TAIL_WORDS": ("int", 350),
    "CHAPTER_SO_FAR_TAIL_WORDS": ("int", 700),
    "REFINE_MIN_RATIO": ("float", 0.7),
    "CONSISTENCY_CHECK_ENABLED": ("bool", True),
    "REQUEST_TIMEOUT": ("int", 3600),
    "CLOUD_FALLBACK": ("bool", True),
    "CLOUD_FALLBACK_MODEL": ("str", "gemma4:12b"),
    "CLOUD_FALLBACK_MINUTES": ("int", 30),
}

# Chaves que a tela ⚙ Configurações edita e grava no config.json. As outras continuam só no .env,
# porque alguns prompts usam o valor delas na hora do import.
UI_KEYS = [
    "OLLAMA_BASE_URL", "HARDWARE_PROFILE", "UI_LANGUAGE",
    "PROVIDER_DRAFTING", "PROVIDER_REFINING", "PROVIDER_SUMMARIZING", "OPENAI_COMPAT_BASE_URL",
    "MODEL_DRAFTING", "MODEL_REFINING", "MODEL_SUMMARIZING",
    "DRAFTING_TEMPERATURE", "REFINING_TEMPERATURE", "SUMMARIZING_TEMPERATURE",
    "DRAFTING_NUM_CTX", "REFINING_NUM_CTX", "SUMMARIZING_NUM_CTX",
    "DRAFTING_NUM_GPU", "REFINING_NUM_GPU", "SUMMARIZING_NUM_GPU",
    "CHAPTER_TARGET_WORDS", "CONSISTENCY_CHECK_ENABLED", "REQUEST_TIMEOUT",
    "CLOUD_FALLBACK", "CLOUD_FALLBACK_MODEL", "CLOUD_FALLBACK_MINUTES",
]


PROVIDER_IDS = ("ollama", "anthropic", "google", "openai", "openai_compat")


def coerce(key: str, raw):
    """Converte um valor vindo de texto, JSON ou da tela para o tipo da chave. Erro se inválido."""
    kind, default = _SPEC[key]
    if raw is None:
        return default
    if kind == "str":
        text = str(raw).strip()
        return text or default
    if kind == "provider":
        text = str(raw).strip().lower() or str(default)
        if text not in PROVIDER_IDS:
            raise ValueError(f"provedor desconhecido: {text}")
        return text
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "sim", "on")
    if kind == "optint":
        text = str(raw).strip()
        return int(text) if text else None
    text = str(raw).strip()
    if text == "":
        return default
    return int(text) if kind == "int" else float(text)


def _from_env() -> dict:
    values = {}
    for key in _SPEC:
        try:
            values[key] = coerce(key, os.getenv(key))
        except ValueError:
            logger.warning("Valor inválido para %s no ambiente/.env; usando o padrão.", key)
            values[key] = _SPEC[key][1]
    return values


def load_user_settings() -> dict:
    """Configurações gravadas pela tela (config.json). Chaves inválidas são ignoradas."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("config.json ilegível; ignorando.")
        return {}
    out = {}
    for key, raw in data.items():
        if key in _SPEC:
            try:
                out[key] = coerce(key, raw)
            except ValueError:
                logger.warning("Valor inválido para %s no config.json; ignorando.", key)
    return out


_values = _from_env()
_values.update(load_user_settings())

# Declarações explícitas: os outros módulos leem estes nomes.
OLLAMA_BASE_URL: str = _values["OLLAMA_BASE_URL"]
OLLAMA_GENERATE_URL: str = f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate"
HARDWARE_PROFILE: str = _values["HARDWARE_PROFILE"]
UI_LANGUAGE: str = _values["UI_LANGUAGE"]
PROVIDER_DRAFTING: str = _values["PROVIDER_DRAFTING"]
PROVIDER_REFINING: str = _values["PROVIDER_REFINING"]
PROVIDER_SUMMARIZING: str = _values["PROVIDER_SUMMARIZING"]
OPENAI_COMPAT_BASE_URL: str = _values["OPENAI_COMPAT_BASE_URL"]
MODEL_DRAFTING: str = _values["MODEL_DRAFTING"]
MODEL_REFINING: str = _values["MODEL_REFINING"]
MODEL_SUMMARIZING: str = _values["MODEL_SUMMARIZING"]
DRAFTING_TEMPERATURE: float = _values["DRAFTING_TEMPERATURE"]
DRAFTING_NUM_CTX: int = _values["DRAFTING_NUM_CTX"]
REFINING_TEMPERATURE: float = _values["REFINING_TEMPERATURE"]
REFINING_NUM_CTX: int = _values["REFINING_NUM_CTX"]
SUMMARIZING_TEMPERATURE: float = _values["SUMMARIZING_TEMPERATURE"]
SUMMARIZING_NUM_CTX: int = _values["SUMMARIZING_NUM_CTX"]
# Camadas do modelo na GPU (opção num_gpu do Ollama). None = automático.
DRAFTING_NUM_GPU: int | None = _values["DRAFTING_NUM_GPU"]
REFINING_NUM_GPU: int | None = _values["REFINING_NUM_GPU"]
SUMMARIZING_NUM_GPU: int | None = _values["SUMMARIZING_NUM_GPU"]
RECENT_SUMMARIES_KEPT: int = _values["RECENT_SUMMARIES_KEPT"]
SUMMARY_MAX_WORDS: int = _values["SUMMARY_MAX_WORDS"]
STORY_SO_FAR_MAX_WORDS: int = _values["STORY_SO_FAR_MAX_WORDS"]
DYNAMIC_MEMORY_MAX_WORDS: int = _values["DYNAMIC_MEMORY_MAX_WORDS"]
ROSTER_MAX_CHARS: int = _values["ROSTER_MAX_CHARS"]
OPEN_THREADS_MAX: int = _values["OPEN_THREADS_MAX"]
# ── Geração cena por cena ────────────────────────────────────
# Meta do capítulo inteiro; cada cena recebe a meta dela na premissa ("about 400 words")
# ou uma parte igual desta.
CHAPTER_TARGET_WORDS: int = _values["CHAPTER_TARGET_WORDS"]
# Cena abaixo desta fração da meta ganha pedidos de continuação, até SCENE_MAX_CONTINUATIONS.
SCENE_MIN_RATIO: float = _values["SCENE_MIN_RATIO"]
SCENE_MAX_CONTINUATIONS: int = _values["SCENE_MAX_CONTINUATIONS"]
SCENE_BREAK: str = _values["SCENE_BREAK"]
DRAFTING_REPEAT_PENALTY: float = _values["DRAFTING_REPEAT_PENALTY"]
# Quantos tokens para trás a penalidade de repetição olha (o padrão do Ollama é 64).
DRAFTING_REPEAT_LAST_N: int = _values["DRAFTING_REPEAT_LAST_N"]
# Quanto do texto anterior entra no prompt de cada cena.
PREVIOUS_CHAPTER_TAIL_WORDS: int = _values["PREVIOUS_CHAPTER_TAIL_WORDS"]
CHAPTER_SO_FAR_TAIL_WORDS: int = _values["CHAPTER_SO_FAR_TAIL_WORDS"]
# Polimento que encolher a cena abaixo desta fração é descartado (fica o rascunho).
REFINE_MIN_RATIO: float = _values["REFINE_MIN_RATIO"]
CONSISTENCY_CHECK_ENABLED: bool = _values["CONSISTENCY_CHECK_ENABLED"]
REQUEST_TIMEOUT: int = _values["REQUEST_TIMEOUT"]
# Reserva local: quando um provedor na nuvem esgota o limite de uso ou para de responder, a
# geração continua no Ollama com este modelo, e o provedor fica de lado por alguns minutos.
CLOUD_FALLBACK: bool = _values["CLOUD_FALLBACK"]
CLOUD_FALLBACK_MODEL: str = _values["CLOUD_FALLBACK_MODEL"]
CLOUD_FALLBACK_MINUTES: int = _values["CLOUD_FALLBACK_MINUTES"]


def current_settings() -> dict:
    """Valores atuais das chaves editáveis pela tela."""
    module = sys.modules[__name__]
    return {key: getattr(module, key) for key in UI_KEYS}


def apply_settings(values: dict):
    """Aplica valores em tempo de execução. Vale para a próxima geração."""
    module = sys.modules[__name__]
    for key, raw in values.items():
        if key in _SPEC:
            setattr(module, key, coerce(key, raw))
    setattr(module, "OLLAMA_GENERATE_URL", f"{getattr(module, 'OLLAMA_BASE_URL').rstrip('/')}/api/generate")


def save_user_settings(values: dict):
    """Valida, grava o config.json e aplica. Lança ValueError com a chave inválida."""
    clean = {}
    for key in UI_KEYS:
        if key in values:
            try:
                clean[key] = coerce(key, values[key])
            except ValueError as e:
                raise ValueError(f"{key}: valor inválido ({values[key]!r})") from e
    stored = {}
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
    stored.update(clean)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_settings(clean)
