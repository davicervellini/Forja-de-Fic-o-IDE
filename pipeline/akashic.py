"""
akashic.py — Leitura de seções do Registro Akáshico pelo prefixo do título.

O Registro Akáshico compilado (registro_modelo.md) é markdown com títulos
numerados. Estas funções extraem seções inteiras pelo começo do título,
para montar blocos menores de prompt, como o bloco de estilo e grafias
que vai para a fase de polimento.
"""

import re

# Seções que formam o bloco de estilo do polimento.
STYLE_SECTION_PREFIXES = ("## 10. ", "### 13.2 ", "## 14. ")


def _heading_level(line: str) -> int:
    """Nível do título markdown (1 para '#', 2 para '##'...) ou 0 se não for título."""
    m = re.match(r"^(#+) ", line)
    return len(m.group(1)) if m else 0


def extract_section(text: str, prefix: str) -> str:
    """
    Retorna a seção cujo título começa com `prefix`, do título até o
    próximo título de nível igual ou superior. String vazia se não existir.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
    if start is None:
        return ""
    level = _heading_level(lines[start])
    end = len(lines)
    for j in range(start + 1, len(lines)):
        lj = _heading_level(lines[j])
        if lj and lj <= level:
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def extract_style_block(akashic_records: str) -> str:
    """Guia de estilo, grafias oficiais e lista de proibições, para o polimento."""
    parts = [s for s in (extract_section(akashic_records, p) for p in STYLE_SECTION_PREFIXES) if s]
    return "\n\n".join(parts)
