"""
Testes dos provedores na nuvem (Anthropic, Google, OpenAI e compatíveis) sem rede:
requests.post é trocado por respostas falsas com o formato de stream de cada serviço.

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

import json
import threading
from unittest.mock import patch

import pipeline.providers as prov
from pipeline import config, credentials
from pipeline.api import GenerationInterrupted, OllamaError, generate_text


class FakeResponse:
    def __init__(self, status=200, lines=(), body=None):
        self.status_code = status
        self._lines = list(lines)
        self._body = body or {}
        self.text = json.dumps(self._body)
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        yield from self._lines

    def json(self):
        return self._body

    def close(self):
        self.closed = True


def sse(*events):
    out = []
    for e in events:
        out += [f"data: {e if isinstance(e, str) else json.dumps(e)}", ""]
    return out


ANTHROPIC_STREAM = sse(
    {"type": "message_start"},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "The gate "}},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "opened."}},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
    {"type": "message_stop"},
)
GOOGLE_STREAM = sse(
    {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}]}}]},
    {"candidates": [{"content": {"parts": [{"text": "The gate "}]}}]},
    {"candidates": [{"content": {"parts": [{"text": "opened."}]}, "finishReason": "STOP"}]},
)
OPENAI_STREAM = sse(
    {"choices": [{"delta": {"role": "assistant"}}]},
    {"choices": [{"delta": {"content": "The gate "}}]},
    {"choices": [{"delta": {"content": "opened."}}]},
    "[DONE]",
)


def _keys(tmp_path):
    return patch.object(config, "DATA_DIR", tmp_path)


def _clear_env():
    return patch.dict("os.environ", {v: "" for vs in credentials.ENV_VARS.values() for v in vs})


def _gen(provider, responses, **kw):
    calls = []
    it = iter(responses)

    def fake_post(url, headers=None, json=None, stream=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return next(it)

    with patch.object(prov.requests, "post", fake_post), patch.object(prov, "RETRY_DELAYS", (0, 0)):
        tokens = []
        text = generate_text(model="m1", system_prompt="SYS", user_prompt="USER", temperature=0.5,
                             provider=provider, on_token=tokens.append, **kw)
    return text, tokens, calls


def test_cada_provedor_monta_o_pedido_e_le_o_stream(tmp_path):
    with _keys(tmp_path), _clear_env():
        for p in ("anthropic", "google", "openai", "openai_compat"):
            credentials.set_key(p, f"key-{p}-123456")
        cases = {"anthropic": ANTHROPIC_STREAM, "google": GOOGLE_STREAM,
                 "openai": OPENAI_STREAM, "openai_compat": OPENAI_STREAM}
        for p, stream in cases.items():
            text, tokens, calls = _gen(p, [FakeResponse(lines=stream)], extra_options={"num_predict": 1000})
            assert text == "The gate opened.", p
            assert "".join(tokens) == text
            body, headers, url = calls[0]["json"], calls[0]["headers"], calls[0]["url"]
            if p == "anthropic":
                assert url.endswith("/v1/messages") and headers["x-api-key"] == "key-anthropic-123456"
                assert body["system"] == "SYS" and body["messages"][0]["content"] == "USER"
                assert body["max_tokens"] == 2000 and body["temperature"] == 0.5
            elif p == "google":
                assert "m1:streamGenerateContent?alt=sse" in url and headers["x-goog-api-key"] == "key-google-123456"
                assert body["systemInstruction"]["parts"][0]["text"] == "SYS"
                assert body["generationConfig"]["maxOutputTokens"] == 6000
            elif p == "openai":
                assert url == "https://api.openai.com/v1/chat/completions"
                assert body["max_completion_tokens"] == 6000 and "max_tokens" not in body
                assert body["messages"][0] == {"role": "system", "content": "SYS"}
            else:
                assert url.startswith(config.OPENAI_COMPAT_BASE_URL.rstrip("/"))
                assert body["max_tokens"] == 6000
                assert headers["Authorization"] == "Bearer key-openai_compat-123456"


def test_sem_chave_da_erro_claro_sem_chamar_a_rede(tmp_path):
    with _keys(tmp_path), _clear_env():
        try:
            _gen("anthropic", [])
        except OllamaError as e:
            assert "chave de API" in str(e)
        else:
            raise AssertionError("devia falhar")


def test_limite_de_uso_tenta_de_novo(tmp_path):
    with _keys(tmp_path), _clear_env():
        credentials.set_key("anthropic", "sk-ant-xxxxxxxx")
        text, _, calls = _gen("anthropic", [
            FakeResponse(429, body={"error": {"message": "rate limited"}}),
            FakeResponse(lines=ANTHROPIC_STREAM),
        ])
        assert text == "The gate opened." and len(calls) == 2


def test_chave_recusada_nao_tenta_de_novo(tmp_path):
    with _keys(tmp_path), _clear_env():
        credentials.set_key("openai", "sk-xxxxxxxx")
        try:
            _gen("openai", [FakeResponse(401, body={"error": {"message": "bad key"}})])
        except OllamaError as e:
            assert "recusou a chave" in str(e) and "bad key" in str(e)
        else:
            raise AssertionError("devia falhar")


def test_modelo_que_nao_aceita_temperatura_repete_sem_ela(tmp_path):
    with _keys(tmp_path), _clear_env():
        credentials.set_key("openai", "sk-xxxxxxxx")
        text, _, calls = _gen("openai", [
            FakeResponse(400, body={"error": {"message": "Unsupported value: 'temperature' does not support 0.5"}}),
            FakeResponse(lines=OPENAI_STREAM),
        ])
        assert text == "The gate opened."
        assert "temperature" in calls[0]["json"] and "temperature" not in calls[1]["json"]


def test_cancelar_devolve_o_fragmento(tmp_path):
    with _keys(tmp_path), _clear_env():
        credentials.set_key("anthropic", "sk-ant-xxxxxxxx")
        cancel = threading.Event()

        class Cancelling(FakeResponse):
            def iter_lines(self, decode_unicode=True):
                for i, line in enumerate(ANTHROPIC_STREAM):
                    if i == 4:  # depois do primeiro pedaço de texto
                        cancel.set()
                    yield line

        try:
            _gen("anthropic", [Cancelling(lines=ANTHROPIC_STREAM)], cancel_event=cancel)
        except GenerationInterrupted as e:
            assert e.fragment == "The gate "
        else:
            raise AssertionError("devia interromper")


def test_recusa_e_bloqueio_viram_erro(tmp_path):
    with _keys(tmp_path), _clear_env():
        credentials.set_key("anthropic", "sk-ant-xxxxxxxx")
        credentials.set_key("google", "AIzaxxxxxxxx")
        refusal = sse({"type": "content_block_delta", "delta": {"text": "No"}},
                      {"type": "message_delta", "delta": {"stop_reason": "refusal"}})
        blocked = sse({"promptFeedback": {"blockReason": "SAFETY"}})
        for p, stream, word in (("anthropic", refusal, "recusou"), ("google", blocked, "bloqueou")):
            try:
                _gen(p, [FakeResponse(lines=stream)])
            except OllamaError as e:
                assert word in str(e)
            else:
                raise AssertionError(p)


def test_credenciais_mascaradas_e_variavel_de_ambiente(tmp_path):
    with _keys(tmp_path), _clear_env():
        credentials.set_key("google", "AIza-secret-9876")
        view = credentials.masked()
        assert view["google"] == {"configured": True, "hint": "…9876", "source": "arquivo"}
        assert "secret" not in json.dumps(view)
        assert view["anthropic"]["configured"] is False
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-from-env-1111"}):
            assert credentials.get_key("anthropic") == "sk-ant-from-env-1111"
            assert credentials.masked()["anthropic"]["source"] == "variável ANTHROPIC_API_KEY"
        credentials.set_key("google", "")
        assert credentials.get_key("google") == ""


def test_provedor_invalido_na_configuracao_e_recusado():
    assert config.coerce("PROVIDER_DRAFTING", "Anthropic") == "anthropic"
    try:
        config.coerce("PROVIDER_DRAFTING", "skynet")
    except ValueError:
        pass
    else:
        raise AssertionError("devia recusar")


def test_orquestrador_manda_o_provedor_de_cada_fase(tmp_path):
    import pipeline.orchestrator as orch_mod
    from pipeline.orchestrator import PipelineOrchestrator
    from pipeline.project import StoryProject
    from pipeline import prompts as P

    seen = {}

    def stub(model, system_prompt, user_prompt, provider="ollama", **kw):
        seen.setdefault(system_prompt, set()).add((provider, model))
        if system_prompt == P.SYSTEM_UPDATING:
            return "=== DYNAMIC MEMORY ===\nm\n=== CHARACTER ROSTER ===\nr\n=== OPEN THREADS ===\nt"
        return "text 1" if system_prompt != P.SYSTEM_CONSISTENCY else "OK."

    proj = StoryProject.create(tmp_path, "nuvem")
    with patch.object(config, "PROVIDER_DRAFTING", "anthropic"), \
            patch.object(config, "PROVIDER_REFINING", "google"), \
            patch.object(config, "PROVIDER_SUMMARIZING", "ollama"), \
            patch.object(orch_mod, "generate_text", stub):
        result = PipelineOrchestrator(project=proj, model_drafting="claude-x", model_refining="gem-y",
                                      model_summarizing="llama").run_single("PREMISE", 1)
    assert result.status == "done", result.error
    assert seen[P.SYSTEM_DRAFTING] == {("anthropic", "claude-x")}
    assert seen[P.SYSTEM_REFINING] == {("google", "gem-y")}
    assert seen[P.SYSTEM_SUMMARIZING] == {("ollama", "llama")}


def test_api_nao_gera_sem_a_chave_do_provedor_escolhido(tmp_path):
    from fastapi.testclient import TestClient
    import webapp.server as srv

    (tmp_path / "projetos").mkdir()
    with _keys(tmp_path), _clear_env(), patch.object(config, "PROJECTS_DIR", tmp_path / "projetos"), \
            patch.object(config, "PROVIDER_DRAFTING", "anthropic"), \
            patch.object(srv, "check_ollama_health", lambda **kw: True):
        c = TestClient(srv.create_app())
        slug = c.post("/api/projects", json={"name": "Nuvem"}).json()["slug"]
        c.post(f"/api/projects/{slug}/chapters", json={"premise": "PREMISE-1"})
        r = c.post(f"/api/projects/{slug}/generate", json={})
        assert r.status_code == 400 and "Anthropic" in r.json()["detail"]

        r = c.put("/api/credentials/anthropic", json={"key": "sk-ant-abcdefgh"})
        assert r.json()["credentials"]["anthropic"]["hint"] == "…efgh"
        settings = c.get("/api/settings").json()
        assert "sk-ant-abcdefgh" not in json.dumps(settings)
        assert settings["credentials"]["anthropic"]["configured"] is True
        assert c.put("/api/credentials/skynet", json={"key": "x"}).status_code == 400
