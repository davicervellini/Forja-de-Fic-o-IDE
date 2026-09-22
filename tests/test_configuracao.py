"""
Testes da configuração, dos perfis de hardware e do log em arquivo.

Nenhum teste grava no config.json de verdade: o caminho é trocado por um arquivo temporário.
Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

import json
import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import patch

from pipeline import config
from pipeline.profiles import PROFILES, missing_models, profile_by_label


def test_conversao_de_tipos():
    assert config.coerce("DRAFTING_NUM_CTX", "16384") == 16384
    assert config.coerce("REFINING_TEMPERATURE", "0.35") == 0.35
    assert config.coerce("DRAFTING_NUM_GPU", "") is None
    assert config.coerce("DRAFTING_NUM_GPU", "20") == 20
    assert config.coerce("CONSISTENCY_CHECK_ENABLED", "false") is False
    assert config.coerce("CONSISTENCY_CHECK_ENABLED", True) is True
    assert config.coerce("MODEL_DRAFTING", "  ") == "gemma4:12b"  # vazio volta ao padrão
    try:
        config.coerce("DRAFTING_NUM_CTX", "muito")
        raise AssertionError("deveria recusar texto em campo numérico")
    except ValueError:
        pass


def test_salvar_grava_json_e_aplica_na_hora(tmp_path):
    before = config.current_settings()
    settings = tmp_path / "config.json"
    try:
        with patch.object(config, "SETTINGS_PATH", settings):
            config.save_user_settings({"MODEL_DRAFTING": "gemma3:12b", "DRAFTING_NUM_CTX": "16384",
                                       "DRAFTING_NUM_GPU": "", "OLLAMA_BASE_URL": "http://127.0.0.1:9999/"})
            data = json.loads(settings.read_text(encoding="utf-8"))
            assert data["MODEL_DRAFTING"] == "gemma3:12b" and data["DRAFTING_NUM_CTX"] == 16384
            assert config.MODEL_DRAFTING == "gemma3:12b"
            assert config.DRAFTING_NUM_CTX == 16384
            assert config.DRAFTING_NUM_GPU is None
            assert config.OLLAMA_GENERATE_URL == "http://127.0.0.1:9999/api/generate"
            assert config.load_user_settings()["MODEL_DRAFTING"] == "gemma3:12b"
    finally:
        config.apply_settings(before)


def test_salvar_valor_invalido_nao_grava_nada(tmp_path):
    settings = tmp_path / "config.json"
    with patch.object(config, "SETTINGS_PATH", settings):
        try:
            config.save_user_settings({"DRAFTING_NUM_CTX": "abc"})
            raise AssertionError("deveria recusar")
        except ValueError as e:
            assert "DRAFTING_NUM_CTX" in str(e)
    assert not settings.exists()


def test_config_json_ilegivel_e_ignorado(tmp_path):
    settings = tmp_path / "config.json"
    settings.write_text("{ quebrado", encoding="utf-8")
    with patch.object(config, "SETTINGS_PATH", settings):
        assert config.load_user_settings() == {}


def test_pasta_de_dados(tmp_path):
    with patch.dict("os.environ", {"FORJA_DATA_DIR": str(tmp_path / "dados")}):
        assert config._resolve_data_dir() == tmp_path / "dados"
    root = tmp_path / "app"
    root.mkdir()
    with patch.object(config, "PROJECT_ROOT", root), patch.dict("os.environ", {"FORJA_DATA_DIR": ""}):
        # Sem projetos/ e sem portable.txt: pasta do usuário.
        assert config._resolve_data_dir() == config.user_data_dir()
        (root / "portable.txt").write_text("", encoding="utf-8")
        assert config._resolve_data_dir() == root
    frozen_root = tmp_path / "instalado"
    (frozen_root / "projetos").mkdir(parents=True)
    with patch.object(config, "PROJECT_ROOT", frozen_root), patch.object(config, "FROZEN", True), \
            patch.dict("os.environ", {"FORJA_DATA_DIR": ""}):
        # Executável instalado: mesmo com projetos/ ao lado, usa a pasta do usuário.
        assert config._resolve_data_dir() == config.user_data_dir()


def test_perfis_tem_so_chaves_validas():
    for key, profile in PROFILES.items():
        assert profile["label"] and profile["note"]
        for k in profile["values"]:
            assert k in config.UI_KEYS, (key, k)
    assert profile_by_label(PROFILES["vram8"]["label"]) == "vram8"
    assert profile_by_label("não existe") == "custom"


def test_modelos_faltando():
    values = {"MODEL_DRAFTING": "llama3.1:8b", "MODEL_REFINING": "gemma3:27b", "MODEL_SUMMARIZING": "qwen"}
    assert missing_models(values, ["llama3.1:8b", "qwen:latest"]) == ["gemma3:27b"]


def test_log_vai_para_arquivo(tmp_path):
    from pipeline import logsetup

    root = logging.getLogger()
    old_handlers = list(root.handlers)
    try:
        for h in list(root.handlers):
            if isinstance(h, RotatingFileHandler):
                root.removeHandler(h)
        with patch.object(config, "LOG_DIR", tmp_path / "logs"):
            path = logsetup.setup_logging()
            logging.getLogger("teste").warning("mensagem de teste do log")
            for h in root.handlers:
                h.flush()
            assert path is not None and path.exists()
            assert "mensagem de teste do log" in path.read_text(encoding="utf-8")
    finally:
        for h in list(root.handlers):
            if h not in old_handlers:
                root.removeHandler(h)
                h.close()
