"""
wiki_fetcher.py — Busca informações canônicas de personagens nas Wikis do Fandom.

Baixa o código-fonte (Wikitext) da introdução do personagem e usa o Ollama
para converter em uma "short sheet" compatível com o Registro Akáshico,
adicionando automaticamente à Memória Dinâmica.
"""

import logging
from pathlib import Path

import requests

from pipeline.api import generate_text
from pipeline import config
from pipeline.io_utils import read_file, write_file

logger = logging.getLogger(__name__)

# Mapeamento das franquias para os subdomínios do Fandom
WIKI_DOMAINS = {
    "Stargate": "stargate",
    "Halo": "halo",
    "MCU": "marvelcinematicuniverse",
    "DC": "dc",
    "Harry Potter": "harrypotter",
    "My Hero Academia": "myheroacademia",
    "High School DxD": "highschooldxd",
    "DanMachi": "danmachi",
    "Gate: Jieitai": "gate",
    "A Certain Magical Index": "toarumajutsunoindex",
    "Cyberpunk": "cyberpunk",
    "Warhammer 40k": "warhammer40k",
    "Mario": "mario",
    "Mass Effect": "masseffect",
    "Star Wars": "starwars",
    "Star Trek": "memory-alpha",
    "The Elder Scrolls": "elderscrolls",
    "Fairy Tail": "fairytail",
}


def search_fandom_wiki(character_name: str, franchise: str) -> str | None:
    """Busca o wikitexto do personagem na API do Fandom."""
    domain = WIKI_DOMAINS.get(franchise)
    if not domain:
        raise ValueError(f"Franquia '{franchise}' não suportada para busca Wiki.")

    url = f"https://{domain}.fandom.com/api.php"

    # Passo 1: Buscar o nome correto da página (Search)
    search_params = {
        "action": "query",
        "list": "search",
        "srsearch": character_name,
        "format": "json",
        "utf8": 1,
    }

    try:
        r_search = requests.get(url, params=search_params, timeout=15)
        r_search.raise_for_status()
        data = r_search.json()
        search_results = data.get("query", {}).get("search", [])

        if not search_results:
            return None

        # Pega a página mais relevante
        best_title = search_results[0]["title"]

        # Passo 2: Pegar o wikitexto
        content_params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": best_title,
            "format": "json",
            "utf8": 1,
        }

        r_content = requests.get(url, params=content_params, timeout=15)
        r_content.raise_for_status()
        pages = r_content.json().get("query", {}).get("pages", {})

        for page_id, page_data in pages.items():
            if page_id == "-1":
                continue
            revisions = page_data.get("revisions", [])
            if revisions:
                wikitext = revisions[0].get("slots", {}).get("main", {}).get("*", "")
                # Limita a 4000 caracteres (suficiente para infobox e introdução)
                return wikitext[:4000]

    except Exception as e:
        logger.error(f"Erro ao buscar na wiki: {e}")
        raise e

    return None


def extract_character_sheet_with_llm(character_name: str, wikitext: str) -> str:
    """Usa o Ollama para transformar wikitexto confuso em uma short sheet limpa."""
    system_prompt = (
        "You are a wiki archivist. Convert the provided raw wikitext into a short "
        "character sheet. Extract ONLY: Name, Origin, Appearance, Personality, and Powers. "
        "Keep it under 100 words. Format as a single paragraph starting with the character's name in bold."
    )

    user_prompt = f"Raw Wikitext for {character_name}:\n\n{wikitext}\n\nCreate the short character sheet."

    sheet = generate_text(
        model=config.MODEL_SUMMARIZING,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.2,
        num_ctx=4096,
        timeout=120,
    )
    return sheet.strip()


def add_character_to_memory(character_name: str, franchise: str, project_dir: str | Path) -> str:
    """
    Fluxo completo: busca na wiki, extrai a ficha e adiciona à memória dinâmica.
    Evita duplicatas óbvias (mesmo nome já presente).
    """
    dyn_path = Path(project_dir) / "memoria_dinamica.md"
    current_memory = read_file(dyn_path) if dyn_path.exists() else ""

    # Evita adicionar se o nome já aparece de forma clara na memória
    if character_name.lower() in current_memory.lower():
        return f"Personagem '{character_name}' já parece estar na Memória Dinâmica (ignorado)."

    wikitext = search_fandom_wiki(character_name, franchise)
    if not wikitext:
        return f"Personagem '{character_name}' não encontrado na Wiki de {franchise}."

    sheet = extract_character_sheet_with_llm(character_name, wikitext)

    addition = f"\n- {sheet}"
    new_memory = (current_memory + addition).strip()

    dyn_path.parent.mkdir(parents=True, exist_ok=True)
    write_file(dyn_path, new_memory)

    return f"Personagem '{character_name}' adicionado com sucesso.\n{sheet}"
