"""Configuração comum dos testes (pytest)."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from isolamento import basic_pipeline  # noqa: E402
from pipeline import config  # noqa: E402


@pytest.fixture(autouse=True)
def _pipeline_basico(tmp_path_factory):
    """
    Os valores do .env do usuário não mudam o que os testes esperam, e nenhum teste grava no
    config.json de verdade (salvar pela tela vai para um arquivo temporário).
    """
    settings = tmp_path_factory.mktemp("config") / "config.json"
    with basic_pipeline(), patch.object(config, "SETTINGS_PATH", settings):
        yield
