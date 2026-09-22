"""
api.py — Interação com a API REST do Ollama.

Suporta streaming de tokens (para exibição em tempo real na GUI)
e modo batch (resposta completa de uma vez).
Timeouts generosos para acomodar troca de modelo na VRAM.
"""

import json
import logging
import threading
from typing import Callable

import requests

# A configuração pode mudar pela tela ⚙ Configurações: ler config.NOME na hora de usar.
from pipeline import config

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Erro específico de comunicação com o Ollama."""
    pass


class GenerationInterrupted(Exception):
    """Geração interrompida pelo usuário. Contém o fragmento gerado."""

    def __init__(self, fragment: str, message: str = "Geração cancelada pelo usuário."):
        self.fragment = fragment
        super().__init__(message)


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
) -> str:
    """
    Envia uma requisição de geração ao Ollama com streaming.

    Args:
        model: Nome do modelo (ex: 'llama3.1:8b').
        system_prompt: Prompt de sistema que define o papel da IA.
        user_prompt: Prompt do usuário com o conteúdo a processar.
        temperature: Temperatura de amostragem.
        num_ctx: Tamanho da janela de contexto.
        timeout: Timeout em segundos (usa REQUEST_TIMEOUT se None).
        on_token: Callback chamado para cada token gerado (streaming).
        cancel_event: Evento de cancelamento (threading.Event).
        extra_options: Opções extras do Ollama (ex: {'num_gpu': 28}).

    Returns:
        Texto completo gerado.

    Raises:
        OllamaError: Falha de conexão, modelo não encontrado, etc.
        GenerationInterrupted: Geração cancelada; contém o fragmento.
    """
    if timeout is None:
        timeout = config.REQUEST_TIMEOUT

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
        "options": options,
    }

    accumulated_text = ""

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

            # Extrai o token da resposta
            token = chunk.get("response", "")
            if token:
                accumulated_text += token
                if on_token:
                    on_token(token)

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

        return accumulated_text

    except GenerationInterrupted:
        raise  # Re-lança sem embrulhar

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
