"""
profiles.py — Perfis de hardware prontos para a tela ⚙ Configurações.

Cada perfil é um conjunto de valores de configuração (modelos e contextos) pensado para
uma faixa de memória de vídeo. Os nomes de modelo são tags da biblioteca do Ollama.
"Personalizado" não muda nada: vale o que o usuário preencher.
"""

PROFILES: dict[str, dict] = {
    "cpu": {
        "label": "Sem placa de vídeo dedicada, ou até 4 GB",
        "note": "Tudo roda na CPU ou em GPU pequena: lento, e a qualidade dos modelos de 3B é menor. Bom para testar.",
        "values": {
            "MODEL_DRAFTING": "llama3.2:3b",
            "MODEL_REFINING": "llama3.2:3b",
            "MODEL_SUMMARIZING": "llama3.2:3b",
            "DRAFTING_NUM_CTX": 8192,
            "REFINING_NUM_CTX": 8192,
            "SUMMARIZING_NUM_CTX": 8192,
        },
    },
    "vram8": {
        "label": "6 a 8 GB de VRAM (ex.: RX 580, GTX 1070, RTX 2060)",
        "note": "gemma4:12b nas três fases: um modelo só, sem troca entre as fases. Em 8 GB parte dele "
                "roda na CPU. Defina OLLAMA_NUM_PARALLEL=1 no Windows para sobrar mais memória de vídeo.",
        "values": {
            "MODEL_DRAFTING": "gemma4:12b",
            "MODEL_REFINING": "gemma4:12b",
            "MODEL_SUMMARIZING": "gemma4:12b",
            "DRAFTING_NUM_CTX": 12288,
            "REFINING_NUM_CTX": 10240,
            "SUMMARIZING_NUM_CTX": 8192,
        },
    },
    "vram12": {
        "label": "10 a 12 GB de VRAM (ex.: RTX 3060 12 GB, RX 6700 XT)",
        "note": "gemma4:12b cabe inteiro na GPU.",
        "values": {
            "MODEL_DRAFTING": "gemma4:12b",
            "MODEL_REFINING": "gemma4:12b",
            "MODEL_SUMMARIZING": "gemma4:12b",
            "DRAFTING_NUM_CTX": 16384,
            "REFINING_NUM_CTX": 12288,
            "SUMMARIZING_NUM_CTX": 8192,
        },
    },
    "vram16": {
        "label": "16 GB de VRAM (ex.: RTX 4060 Ti 16 GB, RX 7800 XT)",
        "note": "gemma4:12b com contexto maior.",
        "values": {
            "MODEL_DRAFTING": "gemma4:12b",
            "MODEL_REFINING": "gemma4:12b",
            "MODEL_SUMMARIZING": "gemma4:12b",
            "DRAFTING_NUM_CTX": 16384,
            "REFINING_NUM_CTX": 16384,
            "SUMMARIZING_NUM_CTX": 8192,
        },
    },
    "vram24": {
        "label": "24 GB de VRAM ou mais (ex.: RTX 3090, RTX 4090)",
        "note": "Rascunho e polimento com gemma4:31b, o melhor Gemma 4 em ficção longa.",
        "values": {
            "MODEL_DRAFTING": "gemma4:31b",
            "MODEL_REFINING": "gemma4:31b",
            "MODEL_SUMMARIZING": "gemma4:12b",
            "DRAFTING_NUM_CTX": 16384,
            "REFINING_NUM_CTX": 16384,
            "SUMMARIZING_NUM_CTX": 12288,
        },
    },
    # ── Nuvem: não precisa de placa de vídeo, mas cada capítulo custa créditos da conta ──
    "anthropic": {
        "label": "Nuvem: Anthropic (Claude)",
        "kind": "cloud",
        "note": "Precisa de chave de API da Anthropic, paga por uso. Não usa a placa de vídeo. "
                "O texto da história é enviado para a Anthropic.",
        "values": {
            "PROVIDER_DRAFTING": "anthropic", "MODEL_DRAFTING": "claude-opus-5",
            "PROVIDER_REFINING": "anthropic", "MODEL_REFINING": "claude-sonnet-5",
            "PROVIDER_SUMMARIZING": "anthropic", "MODEL_SUMMARIZING": "claude-haiku-4-5-20251001",
        },
    },
    "google": {
        "label": "Nuvem: Google (Gemini)",
        "kind": "cloud",
        "note": "Precisa de chave de API do Google AI Studio (tem cota grátis limitada). "
                "O texto da história é enviado para o Google.",
        "values": {
            "PROVIDER_DRAFTING": "google", "MODEL_DRAFTING": "gemini-3.8-flash",
            "PROVIDER_REFINING": "google", "MODEL_REFINING": "gemini-3.8-flash",
            "PROVIDER_SUMMARIZING": "google", "MODEL_SUMMARIZING": "gemini-3.1-flash-lite",
        },
    },
    "openai": {
        "label": "Nuvem: OpenAI (GPT)",
        "kind": "cloud",
        "note": "Precisa de chave de API da OpenAI, paga por uso. O texto da história é enviado para a OpenAI.",
        "values": {
            "PROVIDER_DRAFTING": "openai", "MODEL_DRAFTING": "gpt-5",
            "PROVIDER_REFINING": "openai", "MODEL_REFINING": "gpt-5-mini",
            "PROVIDER_SUMMARIZING": "openai", "MODEL_SUMMARIZING": "gpt-5-mini",
        },
    },
    "custom": {
        "label": "Personalizado",
        "note": "Vale o que estiver preenchido abaixo.",
        "values": {},
    },
}


# Perfis locais usam o Ollama nas três fases (trocar de um perfil de nuvem de volta desfaz o provedor).
for _p in PROFILES.values():
    if _p.get("kind", "local") == "local" and _p["values"]:
        for _phase in ("DRAFTING", "REFINING", "SUMMARIZING"):
            _p["values"].setdefault(f"PROVIDER_{_phase}", "ollama")


def profile_by_label(label: str) -> str:
    return next((k for k, p in PROFILES.items() if p["label"] == label), "custom")


def models_of(profile_key: str) -> list[str]:
    values = PROFILES.get(profile_key, {}).get("values", {})
    return sorted({v for k, v in values.items() if k.startswith("MODEL_")})


def missing_models(profile_values: dict, installed: list[str]) -> list[str]:
    """Modelos pedidos que não estão baixados. Nome sem tag casa também com a tag ':latest'."""
    names = set(installed)
    # Só modelos das fases que rodam no Ollama; os da nuvem não são baixados.
    wanted = {
        v for k, v in profile_values.items()
        if k.startswith("MODEL_") and v
        and profile_values.get(k.replace("MODEL_", "PROVIDER_"), "ollama") == "ollama"
    }
    return sorted(m for m in wanted if m not in names and f"{m}:latest" not in names)
