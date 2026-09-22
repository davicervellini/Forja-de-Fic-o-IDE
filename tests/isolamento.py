"""
Isola os testes da configuração real do usuário: sem isso, um config.json com provedores na
nuvem faria os testes pedirem chave de API ou chamarem a internet.
"""

from unittest.mock import patch

from pipeline import config


def local_only():
    """Todas as fases no Ollama, sem reserva local e sem premissa automática."""
    return patch.multiple(
        config,
        PROVIDER_DRAFTING="ollama", PROVIDER_REFINING="ollama", PROVIDER_SUMMARIZING="ollama",
        CLOUD_FALLBACK=False, AUTO_NEXT_PREMISE=False,
    )
