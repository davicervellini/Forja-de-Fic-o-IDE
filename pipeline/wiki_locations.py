"""
wiki_locations.py — Locais canônicos a partir das wikis do Fandom.

Modelos locais não conhecem a planta de Atlantis ou da Arca e inventam salas, distâncias e
até inimigos que nunca estiveram ali. Este módulo busca a página do local na wiki do
universo, limpa o wikitexto, e o modelo da fase de resumo escreve uma ficha curta em inglês:
planta e posição relativa das áreas, o que existe ali, estado na época da história e uma
linha "Never:" com o que não existe no lugar. Também sugere os sublocais listados na página
(seções "Areas", "Locations", "Levels"...) e guarda endereços de imagens para o autor ver.
Nada é gravado aqui: a tela revisa e confirma.
"""

import logging
import re
from pathlib import Path

import requests

from pipeline import config
from pipeline.api import generate_text
from pipeline.languages import notes_language, notes_rule
from pipeline.wiki_fetcher import _tokens, resolve_wiki_domain

logger = logging.getLogger(__name__)

SOURCE_MAX_CHARS = 7000
SUBLOCATION_SECTIONS = re.compile(
    r"^(areas?|locations?|sections?|rooms?|levels?|decks?|districts?|places?|facilities|layout|structure|"
    r"sub-?locations?|notable locations|áreas|locais|setores)$", re.I)
# Palavras que aparecem no nome de quase todo local e não ajudam a achar a página certa.
_GENERIC_PLACE = {"room", "rooms", "area", "hall", "the", "level", "deck", "section", "city", "station", "chamber"}
_BAD_IMAGE = re.compile(r"(icon|logo|symbol|flag|glyph|\.svg$|\.gif$|sprite|button)", re.I)

SYSTEM_LOCATION_SHEET = (
    "You are a canon archivist for a fanfiction writing tool. From the raw wiki text, write a location "
    "sheet for a small language model that will set scenes there. ONE paragraph, under 150 words: "
    "layout and geography (where the main areas are relative to each other, levels, how you get in and out), "
    "what is physically there (rooms, technology, objects), and its state at the story's time. Canon facts only. "
    "End with a sentence that starts with 'Never:' listing things that are not in this place in canon "
    "(creatures, people or features a writer might wrongly invent). In-universe only: no actors, episodes or "
    "production. Do not start with the place's name or a heading; output only the paragraph."
)


def _api(domain: str, **params) -> dict:
    params.setdefault("format", "json")
    params.setdefault("utf8", 1)
    r = requests.get(f"https://{domain}.fandom.com/api.php", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def score_title(query: str, title: str) -> int:
    """Quantas palavras próprias (não genéricas) do nome procurado estão no título da página."""
    q = _tokens(query) - _GENERIC_PLACE
    return len(q & (_tokens(title) - _GENERIC_PLACE))


def search_location(name: str, domain: str) -> tuple[str | None, list[str]]:
    """(melhor título, outros candidatos). O melhor precisa ter uma palavra própria do nome em comum."""
    data = _api(domain, action="query", list="search", srsearch=name, srlimit=10)
    titles = [r["title"] for r in data.get("query", {}).get("search", []) if "/" not in r["title"]]
    # Título igual ao nome vem primeiro; páginas de variante ("Atlantis (Before I Sleep)") vão para o fim.
    ranked = sorted(titles, key=lambda t: (t.lower() != name.lower(), -score_title(name, t), "(" in t, titles.index(t)))
    best = ranked[0] if ranked and score_title(name, ranked[0]) > 0 else None
    return best, [t for t in ranked if t != best][:6]


def fetch_page(title: str, domain: str) -> dict | None:
    """Wikitexto (seguindo redirecionamentos), imagem principal e imagens da página."""
    data = _api(domain, action="query", prop="revisions|images|pageimages", rvprop="content", rvslots="main",
                titles=title, redirects=1, imlimit=40, pithumbsize=800)
    pages = data.get("query", {}).get("pages", {})
    page = next((p for pid, p in pages.items() if pid != "-1"), None)
    if not page or not page.get("revisions"):
        return None
    wikitext = page["revisions"][0].get("slots", {}).get("main", {}).get("*", "")
    real_title = page.get("title", title)
    images = []
    thumb = (page.get("thumbnail") or {}).get("source")
    if thumb:
        images.append(thumb)
    files = [i["title"] for i in page.get("images", []) if not _BAD_IMAGE.search(i["title"])][:8]
    if files:
        info = _api(domain, action="query", prop="imageinfo", iiprop="url", iiurlwidth=800, titles="|".join(files))
        for p in info.get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            url = ii.get("thumburl") or ii.get("url")
            if url and url not in images:
                images.append(url)
    return {
        "title": real_title,
        "wikitext": wikitext,
        "url": f"https://{domain}.fandom.com/wiki/{real_title.replace(' ', '_')}",
        "images": images[:6],
    }


# ── Limpeza do wikitexto ─────────────────────────────────────

def _strip_templates(text: str) -> str:
    """Tira {{...}} aninhados, guardando os campos de infobox (|campo=valor) como linhas."""
    out, depth, buf = [], 0, []
    i = 0
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if text.startswith("}}", i) and depth:
            depth -= 1
            i += 2
            if depth == 0:
                inner = "".join(buf)
                buf = []
                fields = re.findall(r"^\s*\|\s*([a-z_ ]{3,20})\s*=\s*([^|\n]{2,160})", inner, re.M)
                keep = [f"{k.strip()}: {v.strip()}" for k, v in fields
                        if k.strip() not in ("image", "imagebg", "caption", "appearances", "name")]
                if keep:
                    out.append("\n" + "\n".join(keep) + "\n")
            continue
        (buf if depth else out).append(text[i])
        i += 1
    return "".join(out)


def clean_wikitext(wikitext: str, max_chars: int = SOURCE_MAX_CHARS) -> str:
    text = re.sub(r"<ref[^>/]*/>|<ref.*?</ref>", "", wikitext or "", flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = _strip_templates(text)
    text = re.sub(r"\[\[(?:File|Image|Category):[^\]]*(?:\[\[[^\]]*\]\][^\]]*)*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    # Seções que não descrevem o lugar.
    text = re.split(r"^==\s*(?:Appearances|Notes|Trivia|References|Links and navigation|Behind the scenes|"
                    r"External links|See also|Gallery)\s*==", text, flags=re.M | re.I)[0]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def sublocations(wikitext: str) -> list[str]:
    """Títulos de páginas listados nas seções de áreas/locais da página."""
    found: list[str] = []
    current = ""
    for line in (wikitext or "").splitlines():
        head = re.match(r"^=+\s*(.*?)\s*=+\s*$", line)
        if head:
            current = head.group(1)
            continue
        if not SUBLOCATION_SECTIONS.match(current.strip()) or not line.lstrip().startswith("*"):
            continue
        for target in re.findall(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]", line):
            target = target.strip()
            if target.lower().startswith(("file:", "image:", "category:")) or target in found:
                continue
            found.append(target)
    return found[:60]


# ── Ficha ────────────────────────────────────────────────────

def write_location_sheet(name: str, source: str, era: str = "", cancel_event=None) -> str:
    from pipeline.wiki_fetcher import clean_sheet
    era_line = f"The story's time and situation: {era.strip()}\n\n" if era.strip() else ""
    user = (f"{era_line}Raw wiki text for the place '{name}':\n\n{source}\n\nWrite the location sheet. "
            f"Keep the word 'Never:' in English.\n\n{notes_rule(notes_language())}")
    sheet = generate_text(
        model=config.MODEL_SUMMARIZING,
        provider=config.PROVIDER_SUMMARIZING,
        system_prompt=SYSTEM_LOCATION_SHEET,
        user_prompt=user,
        temperature=0.2,
        num_ctx=max(config.SUMMARIZING_NUM_CTX, 6144),
        timeout=240,
        cancel_event=cancel_event,
        extra_options={"num_predict": 400},
    )
    return clean_sheet(sheet)


def fetch_location_sheet(name: str, universe: str, project_dir: str | Path, era: str = "",
                         exact_title: str | None = None, cancel_event=None) -> dict:
    """
    Busca o local na wiki do universo e escreve a ficha. Com `exact_title`, usa essa página
    sem buscar (o usuário escolheu outra página na revisão). Não grava nada.
    """
    out = {"kind": "location", "name": name, "page_title": "", "sheet": "", "url": "", "found": False,
           "error": "", "candidates": [], "sublocations": [], "images": []}
    domain = resolve_wiki_domain(universe, project_dir)
    if not domain:
        out["error"] = f"O universo '{universe}' não tem wiki cadastrada."
        return out
    try:
        page, candidates = None, []
        # Primeiro a página com o próprio nome (a wiki segue redirecionamentos, ex.: "Gate room").
        page = fetch_page(exact_title or name, domain)
        if page is None and not exact_title:
            title, candidates = search_location(name, domain)
            page = fetch_page(title, domain) if title else None
        elif not exact_title:
            _, candidates = search_location(name, domain)
            candidates = [c for c in candidates if c != page["title"]]
        out["candidates"] = candidates
    except requests.RequestException as e:
        out["error"] = f"Falha ao consultar a wiki: {e}"
        return out
    if not page:
        out["error"] = f"Não encontrado na wiki de {universe}." + (" Escolha uma das páginas sugeridas." if candidates else "")
        return out
    source = clean_wikitext(page["wikitext"])
    out.update(page_title=page["title"], url=page["url"], images=page["images"], found=True,
               sublocations=sublocations(page["wikitext"]))
    out["sheet"] = write_location_sheet(page["title"], source, era, cancel_event)
    return out
