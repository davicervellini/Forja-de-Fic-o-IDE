"""
akashic.py — Leitura de seções do Registro Akáshico e compilação do modelo curto.

O Registro Akáshico completo (registro_akashico.md) é compilado em
registro_modelo.md — versão enxuta para o LLM.
"""

import re
from pathlib import Path

from pipeline.io_utils import read_file, write_file

# Seções que formam o bloco de estilo do polimento.
STYLE_SECTION_PREFIXES = ("## 10. ", "### 13.2 ", "## 14. ")

# Prefixos extraídos para o registro_modelo (versão curta do pipeline).
MODEL_SECTION_PREFIXES = [
    "## 1. ",
    "## 2. ",
    "### 5.8 ",
    "### 9.5 ",
    "## 10. ",
    "### 13.2 ",
    "## 14. ",
]

MODEL_HEADER = [
    "# Akashic Records",
    "",
    "Generated automatically from registro_akashico.md. Do not edit by hand.",
    "",
]


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
    """
    Guia de estilo, grafias oficiais e lista de proibições, para o polimento. No registro_modelo a
    13.2 vem logo depois da seção 10, sem o '## 13.' em volta, e extract_section('## 10. ') já a
    inclui: a seção que já está dentro de uma anterior não entra de novo.
    """
    parts: list[str] = []
    for p in STYLE_SECTION_PREFIXES:
        s = extract_section(akashic_records, p)
        if s and not any(s in earlier for earlier in parts):
            parts.append(s)
    return "\n\n".join(parts)


def spellings_for(style_block: str, text: str) -> str:
    """
    Reduz a lista de grafias oficiais aos nomes que aparecem no texto. Com a lista inteira, o
    polimento de uma cena recebe dezenas de personagens e lugares da história toda, e modelos de
    narração tendem a pôr na cena quem só estava na lista. O nome conta só com a grafia do
    registro, maiúscula inclusive: "the heart of the city" não puxa "the Heart".
    """
    from pipeline.canon import filter_spellings
    out: list[str] = []
    in_spellings = False
    for line in style_block.splitlines():
        if line.startswith("#"):
            in_spellings = "official spellings" in line.lower()
        elif in_spellings:
            out.extend(filter_spellings([line], text, [], []))
            continue
        out.append(line)
    return "\n".join(out)


def build_registro_modelo(project_dir: str | Path) -> tuple[bool, str]:
    """
    Compila registro_akashico.md → registro_modelo.md.

    Extrai as seções listadas em MODEL_SECTION_PREFIXES, remove linhas
    que começam com 'Nota:' e grava o arquivo curto.

    Se nenhuma seção for encontrada (formato diferente), copia o arquivo
    inteiro como fallback para o pipeline não ficar sem cânone.

    Returns:
        (sucesso, mensagem legível)
    """
    project_dir = Path(project_dir)
    fonte = project_dir / "registro_akashico.md"
    destino = project_dir / "registro_modelo.md"

    if not fonte.exists():
        return False, f"Arquivo não encontrado: {fonte.name}"

    # As listas das seções 5.8 e 9.5 vêm dos metadados (abas do editor), não do texto.
    from pipeline.akashic_sync import sync_text
    texto_fonte = sync_text(read_file(fonte))
    linhas = texto_fonte.splitlines()

    partes = list(MODEL_HEADER)
    encontradas = 0

    for prefixo in MODEL_SECTION_PREFIXES:
        secao = extract_section(texto_fonte, prefixo)
        if not secao:
            continue
        encontradas += 1
        # Remove notas de autor
        bloco = [l for l in secao.splitlines() if not l.startswith("Nota:")]
        partes.extend(bloco)
        partes.append("")

    # Seções em inglês fora da lista fixa ("(EN)" no título): cartões de arco, amostra de voz,
    # glossário, interface do Sistema. O que é para o modelo leva "(EN)" no título, por convenção.
    incluidas = "\n".join(partes)
    for linha in linhas:
        m = re.match(r"^(#{2,4}) (.*\(EN\))\s*$", linha)
        if not m or linha in incluidas:
            continue
        secao = extract_section(texto_fonte, linha)
        if secao and secao not in incluidas:
            encontradas += 1
            bloco = [l for l in secao.splitlines() if not l.startswith("Nota:")]
            partes.extend(bloco)
            partes.append("")
            incluidas += "\n" + secao

    if encontradas == 0:
        # Fallback: usa o arquivo inteiro (sem as linhas Nota:)
        corpo = [l for l in linhas if not l.startswith("Nota:")]
        texto = "\n".join(MODEL_HEADER + corpo)
        texto = re.sub(r"\n{3,}", "\n\n", texto).rstrip() + "\n"
        write_file(destino, texto)
        return True, (
            f"Registro modelo gerado (arquivo completo, {len(texto)} chars). "
            "Nenhuma seção numerada padrão encontrada — usando o texto inteiro."
        )

    texto = "\n".join(partes)
    texto = re.sub(r"\n{3,}", "\n\n", texto).rstrip() + "\n"
    write_file(destino, texto)

    pendentes = texto.count("[A DEFINIR")
    msg = f"Registro modelo gerado: {encontradas} seções, {len(texto)} caracteres."
    if pendentes:
        msg += f" Atenção: {pendentes} marcação(ões) [A DEFINIR] ainda no texto."
    return True, msg
