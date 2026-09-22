"""
akashic_schema.py — Formato padrão (v2) do Registro Akáshico.

O arquivo continua sendo Markdown (`registro_akashico.md`), editável à mão. A versão 2 acrescenta, logo depois do
título, um bloco de metadados em comentário HTML, que o programa lê (universos, personagens principais, escolhas
do assistente) e o Markdown ignora:

    # Bíblia do Mundo: Título
    <!-- akashic:meta
    { ...JSON... }
    -->

Seções numeradas (mesmas de sempre, para o `akashic.py` continuar extraindo o modelo curto):
1 Story summary (EN) · 2 Inviolable rules (EN) · 3 Premissa · 4 Sistema de poder · 5 Personagens (5.8 Cast (EN))
6 Arcos · 7 Locais e organizações · 8 Cenário central · 9 Universos (9.5 Universes (EN)) · 10 Style guide (EN)
11 Estrutura da história · 12 Linha do tempo · 13 Glossário (13.2 Official spellings (EN)) · 14 What the models must not do (EN)
"""

import json
import re
from dataclasses import asdict, dataclass, field

META_VERSION = 2
_META_RE = re.compile(r"<!-- akashic:meta\s*\n(.*?)\n-->[ \t]*\n?", re.S)

STRUCTURES = {
    "single": "Universo único",
    "multiverse": "Multiverso",
    "timelines": "Linhas do tempo alternativas de um universo",
}
ROLES = {
    "base": "Base (origem da tecnologia/cenário)",
    "source": "Origem de personagens ou visitantes",
    "visited": "Visitado pela história",
    "hub": "Hub central",
    "original": "Universo original (criado para a fic)",
}


@dataclass
class Universe:
    id: str
    name: str
    role: str = "source"
    wiki: str = ""                 # subdomínio do Fandom (ex.: "stargate"); vazio = sem wiki
    active: bool = True            # False = reserva: cadastrado, mas fora da lista fechada da história
    notes: str = ""                # ficha para o autor (seção 9.2)
    allowed_characters: list[str] = field(default_factory=list)
    model_sheet: str = ""          # ficha curta em inglês que vai para o modelo (item da seção 9.5)


@dataclass
class Character:
    name: str
    role: str = "protagonist"      # protagonist | supporting | antagonist
    origin: str = ""               # nativo, reencarnado, transportado, personagem original...
    age: str = ""
    universe: str = ""             # id do universo de origem, se houver
    notes: str = ""
    sheet: str = ""                # ficha curta em inglês que vai para o modelo (item da seção 5.8)
    sheet_label: str = ""          # rótulo em negrito do item na 5.8, se diferente do nome (ex.: 'Tinaia, the central AI.')


@dataclass
class Location:
    name: str
    universe: str = ""             # id do universo
    parent: str = ""               # nome do local que contém este (ex.: "Atlantis" para "Chair room")
    always: bool = False           # True = a ficha vai em toda cena, não só quando o local está em cena
    model_sheet: str = ""          # ficha curta em inglês para o modelo: planta, o que há ali, o que nunca há
    notes: str = ""                # notas do autor (não vão para o modelo)
    wiki_page: str = ""            # título da página na wiki
    url: str = ""
    images: list[str] = field(default_factory=list)   # endereços de imagens da wiki, para o autor ver


@dataclass
class AkashicMeta:
    version: int = META_VERSION
    title: str = ""
    structure: str = "single"
    language: str = "en"
    universes: list[Universe] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    answers: dict = field(default_factory=dict)   # respostas do assistente (para reabrir/refazer)
    # True depois que as listas das seções 5.8 e 9.5 foram importadas do texto para os metadados.
    # A partir daí essas duas listas são geradas a partir dos metadados (ver akashic_sync.py).
    lists_synced: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AkashicMeta":
        return cls(
            version=data.get("version", META_VERSION),
            title=data.get("title", ""),
            structure=data.get("structure", "single"),
            language=data.get("language", "en"),
            universes=[Universe(**_known(u, Universe)) for u in data.get("universes", [])],
            characters=[Character(**_known(c, Character)) for c in data.get("characters", [])],
            locations=[Location(**_known(loc, Location)) for loc in data.get("locations", [])],
            answers=data.get("answers", {}),
            lists_synced=bool(data.get("lists_synced", False)),
        )

    def universe(self, uid: str) -> Universe | None:
        return next((u for u in self.universes if u.id == uid), None)


def _known(d: dict, cls) -> dict:
    """Ignora campos desconhecidos para o arquivo continuar abrindo se ganhar campos novos no futuro."""
    return {k: v for k, v in d.items() if k in cls.__dataclass_fields__}


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    return re.sub(r"[\s-]+", "_", slug) or "universo"


def read_meta(text: str) -> tuple[AkashicMeta | None, str]:
    """(metadados, texto sem o bloco). Sem bloco ou com JSON inválido: (None, texto original)."""
    m = _META_RE.search(text)
    if not m:
        return None, text
    try:
        meta = AkashicMeta.from_dict(json.loads(m.group(1)))
    except (ValueError, TypeError):
        return None, text
    return meta, _META_RE.sub("", text, count=1)


def write_meta(text: str, meta: AkashicMeta) -> str:
    """Insere/atualiza o bloco de metadados logo depois do título (primeira linha '# ')."""
    _, body = read_meta(text)
    block = "<!-- akashic:meta\n" + json.dumps(meta.to_dict(), ensure_ascii=False, indent=1) + "\n-->\n"
    lines = body.split("\n")
    at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("# ")), 0)
    return "\n".join(lines[:at] + [block.rstrip("\n")] + lines[at:])


def strip_meta(text: str) -> str:
    return read_meta(text)[1]
