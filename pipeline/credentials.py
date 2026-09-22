"""
credentials.py — Chaves de API dos provedores na nuvem (Anthropic, Google, OpenAI...).

As chaves ficam em `credenciais.json` na pasta de dados do usuário, separadas do
config.json, e nunca vão para o log nem voltam inteiras para a interface: a tela só
recebe se a chave existe e os 4 últimos caracteres. Uma variável de ambiente com o nome
padrão do provedor (ex.: ANTHROPIC_API_KEY) vale mais que o arquivo.

No Windows o arquivo fica dentro de %APPDATA%, que só o próprio usuário lê.
"""

import json
import logging
import os
from pathlib import Path

from pipeline import config

logger = logging.getLogger(__name__)

FILENAME = "credenciais.json"

# Variáveis de ambiente aceitas por provedor, na ordem de preferência.
ENV_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "openai_compat": ("OPENAI_COMPAT_API_KEY", "OPENROUTER_API_KEY"),
}


def _path() -> Path:
    return Path(config.DATA_DIR) / FILENAME


def _load() -> dict[str, str]:
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}
    except (OSError, ValueError):
        logger.error("credenciais.json ilegível; ignorando.")
        return {}


def get_key(provider: str) -> str:
    """Chave do provedor, ou "" se não houver."""
    for var in ENV_VARS.get(provider, ()):
        value = os.getenv(var, "").strip()
        if value:
            return value
    return _load().get(provider, "").strip()


def key_source(provider: str) -> str:
    for var in ENV_VARS.get(provider, ()):
        if os.getenv(var, "").strip():
            return f"variável {var}"
    return "arquivo" if _load().get(provider) else ""


def set_key(provider: str, key: str):
    """Grava (ou apaga, com key vazia) a chave de um provedor."""
    if provider not in ENV_VARS:
        raise ValueError(f"Provedor desconhecido: {provider}")
    data = _load()
    key = (key or "").strip()
    if key:
        data[provider] = key
    else:
        data.pop(provider, None)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def masked() -> dict[str, dict]:
    """O que a interface pode ver: se há chave, de onde vem e o final dela."""
    out = {}
    for provider in ENV_VARS:
        key = get_key(provider)
        out[provider] = {
            "configured": bool(key),
            "hint": f"…{key[-4:]}" if len(key) >= 8 else ("…" if key else ""),
            "source": key_source(provider),
        }
    return out
