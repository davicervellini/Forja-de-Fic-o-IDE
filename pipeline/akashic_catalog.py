"""
akashic_catalog.py — Catálogo de universos conhecidos e o subdomínio da wiki (Fandom) de cada um.

Os 18 primeiros vêm do antigo `WIKI_DOMAINS` do wiki_fetcher. Os demais são atalhos comuns; se algum subdomínio
estiver errado, a importação da wiki avisa e o subdomínio pode ser corrigido na tela de edição do Akáshico.
Qualquer universo fora do catálogo pode ser cadastrado à mão, com ou sem wiki.
"""

from pipeline.akashic_schema import slugify

# (nome exibido, subdomínio do Fandom, apelidos usados para reconhecer o universo em textos)
_ENTRIES = [
    ("Stargate", "stargate", ["stargate atlantis", "stargate sg-1", "stargate"]),
    ("Halo", "halo", ["halo, alternate universe", "halo alternate universe", "halo"]),
    ("MCU", "marvelcinematicuniverse", ["mcu", "marvel cinematic universe"]),
    ("DC", "dc", ["dc", "dc comics"]),
    ("Harry Potter", "harrypotter", ["harry potter"]),
    ("My Hero Academia", "myheroacademia", ["my hero academia", "boku no hero academia"]),
    ("High School DxD", "highschooldxd", ["high school dxd", "highschool dxd"]),
    ("DanMachi", "danmachi", ["danmachi", "is it wrong to try to pick up girls in a dungeon"]),
    ("Gate: Jieitai", "gate", ["gate: jieitai", "gate jieitai", "gate"]),
    ("A Certain Magical Index", "toarumajutsunoindex", ["a certain magical index", "toaru majutsu no index"]),
    ("Cyberpunk", "cyberpunk", ["cyberpunk", "cyberpunk 2077"]),
    ("Warhammer 40k", "warhammer40k", ["warhammer 40k", "warhammer 40,000"]),
    ("Mario", "mario", ["mario", "super mario"]),
    ("Mass Effect", "masseffect", ["mass effect"]),
    ("Star Wars", "starwars", ["star wars"]),
    ("Star Trek", "memory-alpha", ["star trek"]),
    ("The Elder Scrolls", "elderscrolls", ["the elder scrolls", "elder scrolls", "skyrim"]),
    ("Fairy Tail", "fairytail", ["fairy tail"]),
    # atalhos extras (subdomínios não verificados um a um)
    ("Naruto", "naruto", ["naruto"]),
    ("One Piece", "onepiece", ["one piece"]),
    ("Dragon Ball", "dragonball", ["dragon ball"]),
    ("Bleach", "bleach", ["bleach"]),
    ("Attack on Titan", "attackontitan", ["attack on titan", "shingeki no kyojin"]),
    ("Sword Art Online", "swordartonline", ["sword art online"]),
    ("Fallout", "fallout", ["fallout"]),
    ("The Witcher", "witcher", ["the witcher", "witcher"]),
    ("O Senhor dos Anéis", "lotr", ["the lord of the rings", "lord of the rings", "o senhor dos anéis"]),
    ("Game of Thrones", "gameofthrones", ["game of thrones"]),
    ("Doctor Who", "tardis", ["doctor who"]),
    ("Avatar: A Lenda de Aang", "avatar", ["avatar: the last airbender", "avatar the last airbender"]),
]

CATALOG: list[dict] = [
    {"id": slugify(name), "name": name, "wiki": wiki, "aliases": aliases} for name, wiki, aliases in _ENTRIES
]
BY_NAME = {e["name"]: e for e in CATALOG}
LEGACY_WIKI_DOMAINS = {e["name"]: e["wiki"] for e in CATALOG[:18]}   # compatibilidade com o wiki_fetcher antigo


def match_universe(text: str) -> dict | None:
    """Acha a entrada do catálogo cujo nome ou apelido aparece em `text` (o apelido mais longo vence)."""
    low = text.lower()
    best, best_len = None, 0
    for e in CATALOG:
        for alias in e["aliases"] + [e["name"].lower()]:
            if alias in low and len(alias) > best_len:
                best, best_len = e, len(alias)
    return best
