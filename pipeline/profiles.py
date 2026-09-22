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
        "note": "O polimento com gemma3:12b não cabe inteiro em 8 GB e roda em parte na CPU. "
                "Defina OLLAMA_NUM_PARALLEL=1 no Windows para o rascunho caber inteiro na GPU.",
        "values": {
            "MODEL_DRAFTING": "llama3.1:8b",
            "MODEL_REFINING": "gemma3:12b",
            "MODEL_SUMMARIZING": "llama3.1:8b",
            "DRAFTING_NUM_CTX": 12288,
            "REFINING_NUM_CTX": 10240,
            "SUMMARIZING_NUM_CTX": 8192,
        },
    },
    "vram12": {
        "label": "10 a 12 GB de VRAM (ex.: RTX 3060 12 GB, RX 6700 XT)",
        "note": "Os dois modelos cabem na GPU, um de cada vez.",
        "values": {
            "MODEL_DRAFTING": "llama3.1:8b",
            "MODEL_REFINING": "gemma3:12b",
            "MODEL_SUMMARIZING": "llama3.1:8b",
            "DRAFTING_NUM_CTX": 16384,
            "REFINING_NUM_CTX": 12288,
            "SUMMARIZING_NUM_CTX": 8192,
        },
    },
    "vram16": {
        "label": "16 GB de VRAM (ex.: RTX 4060 Ti 16 GB, RX 7800 XT)",
        "note": "Rascunho com gemma3:12b, que escreve melhor que o llama3.1:8b, e contexto maior.",
        "values": {
            "MODEL_DRAFTING": "gemma3:12b",
            "MODEL_REFINING": "gemma3:12b",
            "MODEL_SUMMARIZING": "llama3.1:8b",
            "DRAFTING_NUM_CTX": 16384,
            "REFINING_NUM_CTX": 16384,
            "SUMMARIZING_NUM_CTX": 8192,
        },
    },
    "vram24": {
        "label": "24 GB de VRAM ou mais (ex.: RTX 3090, RTX 4090)",
        "note": "Polimento com gemma3:27b.",
        "values": {
            "MODEL_DRAFTING": "gemma3:12b",
            "MODEL_REFINING": "gemma3:27b",
            "MODEL_SUMMARIZING": "gemma3:12b",
            "DRAFTING_NUM_CTX": 16384,
            "REFINING_NUM_CTX": 16384,
            "SUMMARIZING_NUM_CTX": 12288,
        },
    },
    "custom": {
        "label": "Personalizado",
        "note": "Vale o que estiver preenchido abaixo.",
        "values": {},
    },
}


def profile_by_label(label: str) -> str:
    return next((k for k, p in PROFILES.items() if p["label"] == label), "custom")


def models_of(profile_key: str) -> list[str]:
    values = PROFILES.get(profile_key, {}).get("values", {})
    return sorted({v for k, v in values.items() if k.startswith("MODEL_")})


def missing_models(profile_values: dict, installed: list[str]) -> list[str]:
    """Modelos pedidos que não estão baixados. Nome sem tag casa também com a tag ':latest'."""
    names = set(installed)
    wanted = {v for k, v in profile_values.items() if k.startswith("MODEL_") and v}
    return sorted(m for m in wanted if m not in names and f"{m}:latest" not in names)
