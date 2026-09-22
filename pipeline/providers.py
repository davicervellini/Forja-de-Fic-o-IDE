"""
providers.py — Geração com modelos na nuvem: Anthropic (Claude), Google (Gemini),
OpenAI (GPT) e qualquer serviço compatível com a API da OpenAI (OpenRouter, Groq,
LM Studio, etc.).

Tudo com `requests` e streaming, sem SDKs, para o instalador continuar pequeno.
A interface é a mesma do Ollama (pipeline.api.generate_text): texto completo de volta,
`on_token` a cada pedaço, `cancel_event` para interromper (GenerationInterrupted com o
fragmento) e erros como ProviderError, que é um OllamaError para o pipeline tratar igual.

api.py importa este módulo só dentro de generate_text, para não haver import circular.
"""

import json
import logging
import threading
import time
from typing import Callable, Iterator

import requests

from pipeline import config, credentials
from pipeline.api import GenerationInterrupted, OllamaError

logger = logging.getLogger(__name__)

try:
    # Usa os certificados do Windows em vez da lista embutida no Python: antivírus e redes de
    # empresa que inspecionam HTTPS instalam o certificado deles no Windows, e sem isso a
    # conexão com a nuvem falha com CERTIFICATE_VERIFY_FAILED.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    logger.info("truststore não instalado; usando os certificados embutidos do Python.")

PROVIDERS: dict[str, dict] = {
    "ollama": {
        "label": "Ollama (local, grátis)",
        "cloud": False,
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "cloud": True,
        "key_url": "https://console.anthropic.com/settings/keys",
        "suggested": ["claude-opus-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    },
    "google": {
        "label": "Google (Gemini)",
        "cloud": True,
        "key_url": "https://aistudio.google.com/apikey",
        "suggested": ["gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite"],
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "cloud": True,
        "key_url": "https://platform.openai.com/api-keys",
        "suggested": ["gpt-5", "gpt-5-mini"],
    },
    "openai_compat": {
        "label": "Compatível com OpenAI (OpenRouter e outros)",
        "cloud": True,
        "key_url": "https://openrouter.ai/keys",
        "suggested": [],
    },
}

ANTHROPIC_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_URL = "https://api.openai.com/v1"

# Tentativas extras para limite de uso (429) e instabilidade do servidor (5xx),
# só enquanto nenhum texto chegou.
RETRY_STATUS = {429, 500, 502, 503, 504, 529}
RETRY_DELAYS = (4, 12)


class ProviderError(OllamaError):
    """
    Erro de um provedor na nuvem. É um OllamaError para o pipeline tratar do mesmo jeito.
    `unavailable` marca os erros em que vale trocar para o modelo local: limite de uso ou de
    crédito, servidor sobrecarregado ou fora do ar, conexão perdida. Chave errada, modelo
    inexistente e recusa de conteúdo não trocam: o problema continuaria depois.
    """

    def __init__(self, message: str, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable


# Palavras que, num erro 400/403, indicam falta de crédito ou cota, e não um pedido errado.
QUOTA_WORDS = ("quota", "credit", "billing", "exhausted", "rate limit", "usage limit", "insufficient")


def _unavailable(status: int, message: str) -> bool:
    if status in RETRY_STATUS or status == 402:
        return True
    return status in (400, 403) and any(w in message.lower() for w in QUOTA_WORDS)


def is_cloud(provider: str) -> bool:
    return PROVIDERS.get(provider, {}).get("cloud", False)


def label(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("label", provider)


# ── Leitura de SSE ───────────────────────────────────────────

def _sse_data(response: requests.Response) -> Iterator[str]:
    """Conteúdo das linhas `data:` de um stream Server-Sent Events."""
    for raw in response.iter_lines(decode_unicode=True):
        if raw and raw.startswith("data:"):
            yield raw[5:].strip()


def _error_message(provider: str, response: requests.Response, model: str) -> str:
    try:
        body = response.json()
        err = body.get("error", body)
        detail = err.get("message") if isinstance(err, dict) else str(err)
    except ValueError:
        detail = response.text[:300]
    status = response.status_code
    name = label(provider)
    if status in (401, 403) or (status == 400 and "api key" in str(detail).lower()):
        return f"{name} recusou a chave de API ({status}). Confira a chave em ⚙ Configurações. Detalhe: {detail}"
    if status == 404:
        return f"{name}: modelo '{model}' não encontrado ({status}). Detalhe: {detail}"
    if status == 429:
        return f"{name}: limite de uso ou de crédito atingido (429). Espere um pouco ou confira o saldo da conta. Detalhe: {detail}"
    return f"{name} retornou erro {status}: {detail}"


# ── Montagem das requisições ─────────────────────────────────

def _request(provider: str, model: str, system_prompt: str, user_prompt: str,
             temperature: float | None, max_tokens: int) -> tuple[str, dict, dict]:
    key = credentials.get_key(provider)
    if not key:
        raise ProviderError(f"Falta a chave de API de {label(provider)}. Cadastre em ⚙ Configurações.")

    if provider == "anthropic":
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "stream": True,
        }
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        return f"{ANTHROPIC_URL}/messages", headers, body

    if provider == "google":
        gen: dict = {"maxOutputTokens": max_tokens}
        if temperature is not None:
            gen["temperature"] = temperature
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": gen,
        }
        headers = {"x-goog-api-key": key, "content-type": "application/json"}
        return f"{GOOGLE_URL}/models/{model}:streamGenerateContent?alt=sse", headers, body

    # OpenAI e compatíveis
    base = OPENAI_URL if provider == "openai" else config.OPENAI_COMPAT_BASE_URL.rstrip("/")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
    }
    # A OpenAI trocou max_tokens por max_completion_tokens; os serviços compatíveis ainda usam o nome antigo.
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
    return f"{base}/chat/completions", headers, body


def _pieces(provider: str, response: requests.Response) -> Iterator[str]:
    """Pedaços de texto do stream de cada provedor. Lança erro se o stream trouxer um."""
    for data in _sse_data(response):
        if data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if provider == "anthropic":
            kind = event.get("type")
            if kind == "content_block_delta":
                text = event.get("delta", {}).get("text")
                if text:
                    yield text
            elif kind == "message_delta":
                stop = event.get("delta", {}).get("stop_reason")
                if stop == "refusal":
                    raise ProviderError("O Claude se recusou a continuar este trecho (stop_reason=refusal).")
                if stop == "max_tokens":
                    logger.info("Anthropic parou no limite de tokens do pedido.")
            elif kind == "error":
                raise ProviderError(f"Anthropic: {event.get('error', {}).get('message', event)}")
        elif provider == "google":
            block = event.get("promptFeedback", {}).get("blockReason")
            if block:
                raise ProviderError(f"O Gemini bloqueou o pedido ({block}).")
            for cand in event.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    # Partes de raciocínio (thought) não entram no texto.
                    if part.get("text") and not part.get("thought"):
                        yield part["text"]
                reason = cand.get("finishReason")
                if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"):
                    raise ProviderError(f"O Gemini interrompeu o texto (finishReason={reason}).")
        else:
            if "error" in event:
                err = event["error"]
                raise ProviderError(f"{label(provider)}: {err.get('message', err) if isinstance(err, dict) else err}")
            for choice in event.get("choices", []):
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


def max_tokens_for(provider: str, num_predict: int | None) -> int:
    """
    Teto de tokens do pedido. `num_predict` é pensado para modelos locais pequenos; os da
    nuvem ganham folga, e os que raciocinam antes de responder (GPT-5, Gemini 2.5) gastam
    parte do teto pensando, então ganham mais.
    """
    if not num_predict:
        return 8192
    if provider == "anthropic":
        return int(num_predict * 1.5) + 500
    return num_predict * 2 + 4000


# ── Geração ──────────────────────────────────────────────────

def generate_cloud(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float | None = 0.7,
    num_predict: int | None = None,
    timeout: int | None = None,
    on_token: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    timeout = timeout or config.REQUEST_TIMEOUT
    max_tokens = max_tokens_for(provider, num_predict)
    text = ""
    dropped_temperature = False
    attempt = 0
    logger.info(f"Iniciando geração em {label(provider)} com '{model}' (temp={temperature}, max_tokens={max_tokens})")
    started = time.time()

    while True:
        url, headers, body = _request(provider, model, system_prompt, user_prompt,
                                      None if dropped_temperature else temperature, max_tokens)
        try:
            response = requests.post(url, headers=headers, json=body, stream=True, timeout=(30, timeout))
        except requests.RequestException as e:
            raise ProviderError(f"Sem conexão com {label(provider)}: {e}", unavailable=True) from e

        if response.status_code != 200:
            msg = _error_message(provider, response, model)
            response.close()
            # Modelos que raciocinam (ex.: GPT-5) só aceitam a temperatura padrão.
            if response.status_code == 400 and "temperature" in msg.lower() and not dropped_temperature:
                logger.info(f"{label(provider)} não aceita temperatura para '{model}'; repetindo sem ela.")
                dropped_temperature = True
                continue
            if response.status_code in RETRY_STATUS and attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                attempt += 1
                logger.warning(f"{msg} Nova tentativa em {delay}s.")
                if cancel_event is not None and cancel_event.wait(delay):
                    raise GenerationInterrupted("")
                continue
            raise ProviderError(msg, unavailable=_unavailable(response.status_code, msg))

        try:
            for piece in _pieces(provider, response):
                if cancel_event is not None and cancel_event.is_set():
                    response.close()
                    raise GenerationInterrupted(text)
                text += piece
                if on_token:
                    on_token(piece)
        except requests.RequestException as e:
            if text:
                raise GenerationInterrupted(text, f"Conexão com {label(provider)} caiu no meio do texto.") from e
            raise ProviderError(f"Conexão com {label(provider)} caiu: {e}", unavailable=True) from e
        finally:
            response.close()
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationInterrupted(text)
        logger.info(f"Geração em {label(provider)} concluída: {len(text.split())} palavras em {time.time() - started:.1f}s")
        return text


# ── Lista de modelos (teste de conexão) ─────────────────────

def list_models(provider: str, key: str | None = None, timeout: int = 15) -> list[str]:
    """Modelos disponíveis para a chave. Lança ProviderError com mensagem legível se falhar."""
    key = (key or credentials.get_key(provider)).strip()
    if not key:
        raise ProviderError(f"Sem chave de API de {label(provider)}.")
    try:
        if provider == "anthropic":
            r = requests.get(f"{ANTHROPIC_URL}/models", params={"limit": 100}, timeout=timeout,
                             headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION})
            if r.status_code != 200:
                raise ProviderError(_error_message(provider, r, ""))
            return [m["id"] for m in r.json().get("data", [])]
        if provider == "google":
            r = requests.get(f"{GOOGLE_URL}/models", params={"pageSize": 200}, timeout=timeout,
                             headers={"x-goog-api-key": key})
            if r.status_code != 200:
                raise ProviderError(_error_message(provider, r, ""))
            return sorted(
                m["name"].removeprefix("models/") for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            )
        base = OPENAI_URL if provider == "openai" else config.OPENAI_COMPAT_BASE_URL.rstrip("/")
        r = requests.get(f"{base}/models", timeout=timeout, headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            raise ProviderError(_error_message(provider, r, ""))
        return sorted(m["id"] for m in r.json().get("data", []))
    except requests.RequestException as e:
        raise ProviderError(f"Sem conexão com {label(provider)}: {e}") from e
