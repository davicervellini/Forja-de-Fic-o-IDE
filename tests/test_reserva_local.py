"""
Testes da reserva local: quando a nuvem esgota o limite ou para de responder, a geração
continua no Ollama, e o provedor fica de lado por alguns minutos.

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

from unittest.mock import patch

import requests

from pipeline import api, config
from pipeline import providers as prov


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body

    def close(self):
        pass


class FakeStream:
    status_code = 200

    def iter_lines(self, decode_unicode=True):
        yield '{"response": "texto local", "done": true}'

    def close(self):
        pass


def _run(**kw):
    return api.generate_text("claude-opus-5", "sys", "user", provider="anthropic", **kw)


def test_limite_da_nuvem_troca_para_o_local_e_pausa_o_provedor():
    api.resume_cloud()
    cloud, local, notes = [], [], []

    def fake_post(url, **kw):
        if url == config.OLLAMA_GENERATE_URL:
            local.append(kw["json"]["model"])
            return FakeStream()
        cloud.append(url)
        return FakeResponse(429, {"error": {"message": "rate limited"}})

    api.fallback_listeners.append(notes.append)
    try:
        with patch.object(config, "CLOUD_FALLBACK", True), patch.object(config, "CLOUD_FALLBACK_MODEL", "gemma4:12b"), \
                patch.object(config, "CLOUD_FALLBACK_MINUTES", 30), patch.object(prov, "RETRY_DELAYS", ()), \
                patch.object(prov.credentials, "get_key", lambda p: "k"), patch.object(requests, "post", fake_post):
            assert "texto local" in _run()
            assert "anthropic" in api.cloud_paused()
            # Na segunda chamada a nuvem nem é tentada.
            assert "texto local" in _run()
        assert len(cloud) == 1
        assert local == ["gemma4:12b", "gemma4:12b"]
        assert len(notes) == 1 and "gemma4:12b" in notes[0] and "30 min" in notes[0]
    finally:
        api.fallback_listeners.remove(notes.append)
        api.resume_cloud()


def test_chave_errada_nao_troca():
    api.resume_cloud()
    def fake_post(url, **kw):
        return FakeResponse(401, {"error": {"message": "invalid x-api-key"}})
    with patch.object(config, "CLOUD_FALLBACK", True), patch.object(prov.credentials, "get_key", lambda p: "k"), \
            patch.object(prov.requests, "post", fake_post):
        try:
            _run()
            raise AssertionError("devia falhar")
        except prov.ProviderError as e:
            assert not e.unavailable
    assert not api.cloud_paused()


def test_reserva_desligada_mantem_o_erro():
    api.resume_cloud()
    def fake_post(url, **kw):
        return FakeResponse(529, {"error": {"message": "overloaded"}})
    with patch.object(config, "CLOUD_FALLBACK", False), patch.object(prov, "RETRY_DELAYS", ()), \
            patch.object(prov.credentials, "get_key", lambda p: "k"), patch.object(prov.requests, "post", fake_post):
        try:
            _run()
            raise AssertionError("devia falhar")
        except prov.ProviderError as e:
            assert e.unavailable
    assert not api.cloud_paused()


def test_quais_erros_contam_como_indisponivel():
    assert prov._unavailable(429, "x")
    assert prov._unavailable(503, "x")
    assert prov._unavailable(402, "x")
    assert prov._unavailable(400, "Your credit balance is too low to access the Anthropic API.")
    assert prov._unavailable(403, "Quota exceeded for quota metric")
    assert not prov._unavailable(400, "max_tokens: field required")
    assert not prov._unavailable(401, "invalid key")
    assert not prov._unavailable(404, "model not found")


def test_sem_conexao_troca_para_o_local():
    api.resume_cloud()
    def fake_post(url, **kw):
        raise requests.ConnectionError("down")
    ollama_calls = []

    def fake_ollama_post(url, **kw):
        if url == config.OLLAMA_GENERATE_URL:
            ollama_calls.append(kw["json"]["model"])
            # Sem isso, modelos que raciocinam (gemma4) gastam o teto pensando e devolvem texto vazio.
            assert kw["json"]["think"] is False
            return FakeStream()
        return fake_post(url, **kw)

    with patch.object(config, "CLOUD_FALLBACK", True), patch.object(config, "CLOUD_FALLBACK_MODEL", "gemma4:12b"), \
            patch.object(prov.credentials, "get_key", lambda p: "k"), patch.object(requests, "post", fake_ollama_post):
        out = _run()
    api.resume_cloud()
    assert ollama_calls == ["gemma4:12b"]
    assert "texto local" in out
