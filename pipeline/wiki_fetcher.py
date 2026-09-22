"""
wiki_fetcher.py — Busca informações canônicas de personagens nas Wikis do Fandom.

Baixa o código-fonte (Wikitext) da introdução do personagem e usa o modelo da fase de
resumo (Ollama ou nuvem) para escrever a ficha curta em inglês. A interface nova põe a
ficha no personagem do Registro Akáshico (fetch_character_sheet); a antiga ainda usa
add_character_to_memory, que acrescenta à Memória Dinâmica.
"""

import logging
import re
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


def project_universes(project_dir: str | Path) -> list[dict]:
    """
    Universos do Registro Akáshico do projeto que têm wiki: [{name, wiki, active}].
    Universos ativos vêm primeiro; os de reserva (cadastrados, fora da história) depois.
    Lista vazia se o arquivo não existe ou ainda está no formato antigo (v1).
    """
    from pipeline.akashic_schema import read_meta

    path = Path(project_dir) / "registro_akashico.md"
    if not path.exists():
        return []
    meta, _ = read_meta(read_file(path))
    if meta is None:
        return []
    found = [{"name": u.name, "wiki": u.wiki, "active": u.active} for u in meta.universes if u.wiki]
    return sorted(found, key=lambda u: not u["active"])


def resolve_wiki_domain(franchise: str, project_dir: str | Path | None = None) -> str | None:
    """Subdomínio da wiki: primeiro o universo cadastrado no Akáshico do projeto, depois o dicionário antigo."""
    if project_dir:
        for u in project_universes(project_dir):
            if u["name"] == franchise:
                return u["wiki"]
    return WIKI_DOMAINS.get(franchise)


def search_fandom_wiki(character_name: str, franchise: str, project_dir: str | Path | None = None) -> str | None:
    """Busca o wikitexto do personagem na API do Fandom."""
    found = search_fandom_page(character_name, franchise, project_dir)
    return found[1] if found else None


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9à-ÿ]+", text.lower()) if len(w) >= 3}


def title_matches(query: str, title: str) -> bool:
    """O título da página tem pelo menos uma palavra (de 3+ letras) do nome procurado."""
    return bool(_tokens(query) & _tokens(title))


def search_fandom_page(
    character_name: str, franchise: str, project_dir: str | Path | None = None
) -> tuple[str, str, str] | None:
    """(título da página, wikitexto, endereço) do personagem na wiki do Fandom, ou None."""
    domain = resolve_wiki_domain(franchise, project_dir)
    if not domain:
        raise ValueError(f"Universo '{franchise}' sem wiki cadastrada. Informe o subdomínio na tela do Registro Akáshico.")

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

        # A busca do Fandom devolve qualquer página parecida ("Pessoa Xyz" acha "PX5-442"):
        # só vale um resultado que tenha no título alguma palavra do nome procurado.
        best_title = next((r["title"] for r in search_results if title_matches(character_name, r["title"])), None)
        if not best_title:
            return None

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
                title = page_data.get("title", best_title)
                page_url = f"https://{domain}.fandom.com/wiki/{title.replace(' ', '_')}"
                # Limita a 4000 caracteres (suficiente para infobox e introdução)
                return title, wikitext[:4000], page_url

    except Exception as e:
        logger.error(f"Erro ao buscar na wiki: {e}")
        raise e

    return None


SHEET_SYSTEM_PROMPT = (
    "You are a wiki archivist for a fanfiction writing tool. Convert the raw wikitext into a short "
    "character sheet in English: ONE paragraph, under 100 words, covering origin, appearance, "
    "personality, powers or skills, and how they speak. Canon facts only, no speculation. "
    "In-universe only: never mention actors, voice actors, episodes, seasons or production. "
    "Do not start with the character's name or a heading; output only the paragraph."
)


def extract_character_sheet_with_llm(character_name: str, wikitext: str, cancel_event=None) -> str:
    """Usa o modelo da fase de resumo (local ou nuvem) para transformar o wikitexto numa ficha curta."""
    user_prompt = f"Raw wikitext for {character_name}:\n\n{wikitext}\n\nWrite the short character sheet."
    sheet = generate_text(
        model=config.MODEL_SUMMARIZING,
        provider=config.PROVIDER_SUMMARIZING,
        system_prompt=SHEET_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.2,
        num_ctx=4096,
        timeout=180,
        cancel_event=cancel_event,
        extra_options={"num_predict": 300},
    )
    return clean_sheet(sheet)


def clean_sheet(sheet: str) -> str:
    """Tira título, nome em negrito e aspas que o modelo às vezes põe antes da ficha."""
    text = (sheet or "").strip().strip('"').strip()
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) > 1 and (lines[0].lstrip().startswith("#") or len(lines[0].split()) <= 5):
        lines = lines[1:]
    text = " ".join(l.strip() for l in lines)
    text = re.sub(r"^\*\*[^*]{1,80}\*\*[\s.:—-]*", "", text)
    return text.strip()


def fetch_character_sheet(name: str, universe: str, project_dir: str | Path, cancel_event=None) -> dict:
    """
    Busca um personagem na wiki do universo e escreve a ficha para o modelo.
    Retorna {name, page_title, sheet, url, found, error}; não grava nada. O nome fica o que o
    usuário digitou: o título da página costuma ser o nome completo ("Meredith Rodney McKay").
    """
    out = {"name": name, "page_title": "", "sheet": "", "url": "", "found": False, "error": ""}
    try:
        page = search_fandom_page(name, universe, project_dir)
    except Exception as e:  # rede, wiki fora do ar, subdomínio errado
        out["error"] = f"Falha ao consultar a wiki: {e}"
        return out
    if not page:
        out["error"] = f"Não encontrado na wiki de {universe}."
        return out
    title, wikitext, url = page
    out.update(page_title=title, url=url, found=True)
    out["sheet"] = extract_character_sheet_with_llm(title, wikitext, cancel_event=cancel_event)
    return out


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

    wikitext = search_fandom_wiki(character_name, franchise, project_dir)
    if not wikitext:
        return f"Personagem '{character_name}' não encontrado na Wiki de {franchise}."

    sheet = extract_character_sheet_with_llm(character_name, wikitext)

    addition = f"\n- **{character_name}**: {sheet}"
    new_memory = (current_memory + addition).strip()

    dyn_path.parent.mkdir(parents=True, exist_ok=True)
    write_file(dyn_path, new_memory)

    return f"Personagem '{character_name}' adicionado com sucesso.\n{sheet}"
