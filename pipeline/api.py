"""
api.py — Interação com a API REST do Ollama. Os provedores na nuvem ficam em
providers.py; generate_text escolhe pelo parâmetro `provider`.

Suporta streaming de tokens (para exibição em tempo real na GUI)
e modo batch (resposta completa de uma vez).
Timeouts generosos para acomodar troca de modelo na VRAM.
"""

import json
import logging
import re
import threading
import time
from typing import Callable

import requests

# A configuração pode mudar pela tela ⚙ Configurações: ler config.NOME na hora de usar.
from pipeline import config

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Erro específico de comunicação com o Ollama."""
    pass


# Tokens especiais que o modelo nunca deveria escrever. O backend Vulkan com placas AMD antigas
# às vezes entra num estado em que o gemma só devolve "<unused50>" sem parar.
_SPECIAL_TOKENS = re.compile(r"<unused\d+>|<pad>|<mask>|<start_of_image>|<end_of_image>")
# Tokens especiais seguidos, sem texto de verdade, antes de desistir da resposta.
_JUNK_LIMIT = 25


class _GarbageOutput(Exception):
    """A resposta veio só com tokens especiais: o modelo carregado está num estado ruim."""


def unload_model(model: str):
    """Pede ao Ollama para descarregar o modelo; a próxima geração carrega de novo, limpo."""
    try:
        requests.post(config.OLLAMA_GENERATE_URL, json={"model": model, "keep_alive": 0}, timeout=60)
    except requests.RequestException as e:
        logger.warning(f"Não foi possível descarregar o modelo '{model}': {e}")


class GenerationInterrupted(Exception):
    """Geração interrompida pelo usuário. Contém o fragmento gerado."""

    def __init__(self, fragment: str, message: str = "Geração cancelada pelo usuário."):
        self.fragment = fragment
        super().__init__(message)


# ── Reserva local ────────────────────────────────────────────
# Provedor → horário até quando ele fica de lado depois de esgotar o limite ou parar de responder.
_cloud_paused: dict[str, float] = {}
# Funções chamadas com uma mensagem legível sempre que a geração troca para o modelo local.
fallback_listeners: list[Callable[[str], None]] = []


def cloud_paused() -> dict[str, float]:
    """Provedores de lado agora, com o horário (epoch) em que voltam a ser usados."""
    now = time.time()
    for p in [p for p, until in _cloud_paused.items() if until <= now]:
        del _cloud_paused[p]
    return dict(_cloud_paused)


def resume_cloud(provider: str | None = None):
    """Volta a usar a nuvem antes do prazo (todos os provedores, se `provider` for None)."""
    if provider is None:
        _cloud_paused.clear()
    else:
        _cloud_paused.pop(provider, None)


def _notify_fallback(message: str):
    logger.warning(message)
    for fn in list(fallback_listeners):
        try:
            fn(message)
        except Exception:
            logger.exception("Falha ao avisar a troca para o modelo local.")


def check_ollama_health(timeout: int = 10, base_url: str | None = None) -> bool:
    """True se o servidor Ollama responder. `base_url` testa outro endereço sem mudar a configuração."""
    try:
        resp = requests.get(base_url or config.OLLAMA_BASE_URL, timeout=timeout)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def list_installed_models(timeout: int = 10, base_url: str | None = None) -> list[str]:
    """Nomes dos modelos baixados no Ollama (ex.: 'llama3.1:8b'). Lista vazia se o Ollama não responder."""
    try:
        resp = requests.get(f"{(base_url or config.OLLAMA_BASE_URL).rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
        return sorted(m.get("name", "") for m in resp.json().get("models", []) if m.get("name"))
    except (requests.RequestException, ValueError):
        return []


def generate_text(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    num_ctx: int = 8192,
    timeout: int | None = None,
    on_token: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    extra_options: dict | None = None,
    provider: str = "ollama",
    _retry: bool = True,
) -> str:
    """
    Envia uma requisição de geração ao Ollama (ou a um provedor na nuvem) com streaming.

    Args:
        model: Nome do modelo (ex: 'llama3.1:8b').
        system_prompt: Prompt de sistema que define o papel da IA.
        user_prompt: Prompt do usuário com o conteúdo a processar.
        temperature: Temperatura de amostragem.
        num_ctx: Tamanho da janela de contexto.
        timeout: Timeout em segundos (usa REQUEST_TIMEOUT se None).
        on_token: Callback chamado para cada token gerado (streaming).
        cancel_event: Evento de cancelamento (threading.Event).
        extra_options: Opções extras do Ollama (ex: {'num_gpu': 28}). Na nuvem só
            `num_predict` vale (vira o teto de tokens); as outras são do Ollama.
        provider: "ollama" ou um provedor de pipeline/providers.py ("anthropic", "google"...).

    Returns:
        Texto completo gerado.

    Raises:
        OllamaError: Falha de conexão, modelo não encontrado, etc.
        GenerationInterrupted: Geração cancelada; contém o fragmento.
    """
    if timeout is None:
        timeout = config.REQUEST_TIMEOUT

    if provider and provider != "ollama":
        from pipeline.providers import ProviderError, generate_cloud, label
        fallback = config.CLOUD_FALLBACK_MODEL.strip() if config.CLOUD_FALLBACK else ""
        if not (fallback and provider in cloud_paused()):
            try:
                return generate_cloud(
                    provider, model, system_prompt, user_prompt,
                    temperature=temperature,
                    num_predict=(extra_options or {}).get("num_predict"),
                    timeout=timeout, on_token=on_token, cancel_event=cancel_event,
                )
            except ProviderError as e:
                if not (fallback and e.unavailable):
                    raise
                minutes = max(config.CLOUD_FALLBACK_MINUTES, 0)
                _cloud_paused[provider] = time.time() + minutes * 60
                _notify_fallback(
                    f"{label(provider)} indisponível ({e}). Continuando no modelo local {fallback}; "
                    f"a nuvem volta a ser tentada em {minutes} min."
                )
        model, provider = fallback, "ollama"

    options = {
        "temperature": temperature,
        "num_ctx": num_ctx,
    }
    if extra_options:
        options.update({k: v for k, v in extra_options.items() if v is not None})

    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": True,
        # Modelos que raciocinam antes de responder (gemma4, qwen3...) gastariam o teto de tokens
        # pensando e devolveriam o texto vazio. Os outros modelos ignoram o campo.
        "think": False,
        "options": options,
    }

    accumulated_text = ""
    junk = 0

    try:
        logger.info(
            f"Iniciando geração com modelo '{model}' "
            f"(temp={temperature}, ctx={num_ctx}, extras={extra_options or {}})"
        )

        # stream=True no requests habilita leitura incremental
        # timeout é uma tupla (connect_timeout, read_timeout)
        response = requests.post(
            config.OLLAMA_GENERATE_URL,
            json=payload,
            stream=True,
            timeout=(30, timeout),  # 30s para conectar, timeout para ler
        )

        # Verifica status HTTP
        if response.status_code != 200:
            error_body = response.text
            if "not found" in error_body.lower():
                raise OllamaError(
                    f"Modelo '{model}' não encontrado. "
                    f"Execute 'ollama pull {model}' no terminal."
                )
            raise OllamaError(
                f"Ollama retornou status {response.status_code}: {error_body}"
            )

        # Lê tokens incrementalmente
        for line in response.iter_lines(decode_unicode=True):
            # Verifica cancelamento
            if cancel_event and cancel_event.is_set():
                response.close()
                logger.warning(f"Geração cancelada. Fragmento: {len(accumulated_text)} chars.")
                raise GenerationInterrupted(accumulated_text)

            if not line:
                continue

            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Linha JSON inválida ignorada: {line[:100]}")
                continue

            # Erro reportado dentro do stream (ex.: falta de memória)
            if chunk.get("error"):
                raise OllamaError(f"Ollama reportou erro durante a geração: {chunk['error']}")

            # Extrai o token da resposta, sem tokens especiais.
            token = chunk.get("response", "")
            if token:
                clean = _SPECIAL_TOKENS.sub("", token)
                if clean != token:
                    junk += 1
                    if junk >= _JUNK_LIMIT and len(accumulated_text.split()) < 10:
                        response.close()
                        raise _GarbageOutput()
                if clean:
                    accumulated_text += clean
                    if on_token:
                        on_token(clean)

            # Verifica se a geração terminou
            if chunk.get("done", False):
                # Log de métricas se disponíveis
                total_duration = chunk.get("total_duration")
                eval_count = chunk.get("eval_count")
                prompt_eval_count = chunk.get("prompt_eval_count")
                if total_duration and eval_count:
                    duration_sec = total_duration / 1e9
                    tokens_per_sec = eval_count / duration_sec if duration_sec > 0 else 0
                    logger.info(
                        f"Geração concluída: {eval_count} tokens em "
                        f"{duration_sec:.1f}s ({tokens_per_sec:.1f} tok/s); "
                        f"prompt: {prompt_eval_count} tokens"
                    )
                break

        if junk and len(accumulated_text.split()) < 10:
            raise _GarbageOutput()
        return accumulated_text

    except GenerationInterrupted:
        raise  # Re-lança sem embrulhar

    except _GarbageOutput:
        if not _retry:
            raise OllamaError(
                f"O modelo '{model}' devolveu só tokens especiais (<unused…>) duas vezes seguidas. "
                "É uma falha do Ollama com esta placa de vídeo: feche o Ollama pela bandeja, abra de novo "
                "e gere outra vez."
            )
        logger.warning(f"O modelo '{model}' devolveu só tokens especiais. Descarregando e tentando de novo.")
        unload_model(model)
        return generate_text(model, system_prompt, user_prompt, temperature=temperature, num_ctx=num_ctx,
                             timeout=timeout, on_token=on_token, cancel_event=cancel_event,
                             extra_options=extra_options, provider=provider, _retry=False)

    except requests.ConnectionError as e:
        raise OllamaError(
            "Não foi possível conectar ao Ollama. "
            f"Verifique se o servidor está rodando em {config.OLLAMA_BASE_URL}.\n"
            f"Detalhes: {e}"
        ) from e

    except requests.Timeout as e:
        # Se temos fragmento, salva antes de reportar o erro
        if accumulated_text:
            raise GenerationInterrupted(
                accumulated_text,
                f"Timeout atingido após {timeout}s. "
                f"Fragmento de {len(accumulated_text)} caracteres recuperado."
            ) from e
        raise OllamaError(
            f"Timeout de {timeout}s atingido esperando resposta do Ollama. "
            "Aumente o timeout em ⚙ Configurações."
        ) from e

    except requests.RequestException as e:
        if accumulated_text:
            raise GenerationInterrupted(
                accumulated_text,
                f"Erro de rede durante geração. "
                f"Fragmento de {len(accumulated_text)} caracteres recuperado."
            ) from e
        raise OllamaError(f"Erro de comunicação com o Ollama: {e}") from e
