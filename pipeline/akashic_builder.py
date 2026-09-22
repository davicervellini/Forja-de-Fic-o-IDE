"""
akashic_builder.py — Gera o Registro Akáshico (v2) a partir das respostas da árvore de escolhas.

O resultado segue a numeração padrão de seções (ver akashic_schema.py). Onde a resposta não basta, o texto fica com
a marca `[A DEFINIR: ...]`, que o `build_registro_modelo` já conta e avisa.
"""

from pipeline.akashic_catalog import BY_NAME
from pipeline.akashic_schema import (
    ROLES, STRUCTURES, AkashicMeta, Character, Universe, slugify, write_meta,
)
from pipeline.akashic_tree import BY_ID

TODO = "[A DEFINIR: {}]"

_TONE_EN = {"comedy": "comedy", "drama": "drama", "action": "action", "adventure": "adventure",
            "mystery": "mystery", "slice": "slice of life", "romance": "romance", "horror": "horror"}
_POV_EN = {"first": "first person", "third_limited": "third person limited", "omniscient": "omniscient narrator"}
_TENSE_EN = {"past": "past tense", "present": "present tense"}
_LANG_EN = {"en": "English", "pt-BR": "Brazilian Portuguese", "es": "Spanish", "other": "the story language"}
_RATING_EN = {"kids": "all ages", "teen": "teen-rated", "mature": "mature audiences"}
_LENGTH_EN = {"short": "1,000 to 1,500 words", "medium": "2,000 to 2,500 words", "long": "3,000 words or more"}
_DEATH_EN = {
    "permanent": "Death is permanent.",
    "reversible": "Death is reversible: the character returns to the state they had before.",
    "conditional": "Death is reversible only where the world says so; anywhere else it is permanent.",
}
_BALANCE_EN = {
    "native": "Each character keeps their own powers everywhere.",
    "equalized": "A world rule equalizes everyone to the same baseline power outside special areas.",
    "tiers": "Powers are grouped into tiers (queues, leagues) so unequal characters never face each other unfairly.",
}
_POWER_EN = {"none": "No superhuman powers exist.", "tech": "Advanced technology is the dominant power system.",
             "magic": "Magic is the dominant power system.", "hybrid": "Technology and magic work together.",
             "game": "A game-like system (interface, levels, skills) governs power.",
             "superpowers": "Superpowers or quirks are the dominant power system."}
_CONNECTION_PT = {
    "hub": "Os universos se conectam por um hub central.", "portals": "Os universos se conectam por uma rede de portais.",
    "travel": "O protagonista viaja entre universos.", "reincarnation": "A conexão é reencarnação ou transmigração.",
    "simulation": "Os universos são fases de uma simulação ou jogo.",
    "organization": "Uma organização administra o multiverso.", "other": TODO.format("como os universos se conectam"),
}


def _labels(qid: str, values) -> str:
    """Rótulos em português das opções escolhidas em uma pergunta."""
    opts = dict(BY_ID[qid].options)
    return ", ".join(str(opts.get(v, v)) for v in (values if isinstance(values, list) else [values]))


def meta_from_answers(answers: dict) -> AkashicMeta:
    universes = []
    for u in answers.get("universes", []):
        if isinstance(u, Universe):
            universes.append(u)
        else:
            universes.append(Universe(id=u.get("id") or slugify(u["name"]), name=u["name"], role=u.get("role", "source"),
                                      wiki=u.get("wiki", ""), active=u.get("active", True), notes=u.get("notes", ""),
                                      allowed_characters=list(u.get("allowed_characters", []))))
    characters = []
    for key, default_role in (("protagonists", "protagonist"), ("supporting", "supporting")):
        for c in answers.get(key, []):
            if isinstance(c, Character):
                characters.append(c)
            else:
                characters.append(Character(name=c["name"], role=c.get("role", default_role), origin=c.get("origin", ""),
                                            age=c.get("age", ""), universe=c.get("universe", ""), notes=c.get("notes", "")))
    return AkashicMeta(title=answers.get("title", "").strip(), structure=answers.get("structure", "single"),
                       language=answers.get("language", "en"), universes=universes, characters=characters,
                       answers=answers)


def _summary(meta: AkashicMeta, a: dict) -> str:
    tones = ", ".join(str(_TONE_EN.get(t, t)) for t in a.get("tone", [])) or "story"
    heroes = ", ".join(c.name for c in meta.characters if c.role == "protagonist") or TODO.format("protagonists")
    worlds = ", ".join(u.name for u in meta.universes if u.active) or TODO.format("universes")
    return (f"{meta.title} is a {tones} story set in {STRUCTURES.get(meta.structure, meta.structure).lower()}: {worlds}. "
            f"Main character(s): {heroes}. {TODO.format('story summary, about 350 words: who the protagonist is, what changes, the premise')}")


def _rules(meta: AkashicMeta, a: dict) -> list[str]:
    rules = []
    if meta.structure == "multiverse" and a.get("multiverse_scope") == "closed":
        rules.append("Only the universes listed in section 9.5 exist in the story. No other universe and no character from "
                     "outside that list may appear on stage.")
    if a.get("power_system"):
        rules.append(_POWER_EN[a["power_system"]])
    if a.get("power_balance"):
        rules.append(_BALANCE_EN[a["power_balance"]])
    if a.get("death_rules"):
        rules.append(_DEATH_EN[a["death_rules"]])
    if a.get("canon_policy") == "strict":
        rules.append("Nothing may contradict the canon of the source works.")
    elif a.get("canon_policy") == "changes":
        rules.append("Canon changes are allowed only where this document declares them (alternate timeline).")
    if a.get("divergence"):
        rules.append(f"Divergence point: {a['divergence'].strip()}")
    rules.append(TODO.format("add the rules that never change, one per line, numbered so a chapter premise can cite them"))
    return rules


def _character_block(c: Character, number: str) -> str:
    return (f"### {number} {c.name}\n\n"
            f"- Nome e grafia oficial: {c.name}\n"
            f"- Origem: {c.origin or TODO.format('origem')}\n"
            f"- Idade: {c.age or TODO.format('idade')}\n"
            f"- Aparência, três detalhes fixos: {TODO.format('aparência')}\n"
            f"- Personalidade, três qualidades e dois defeitos: {TODO.format('personalidade')}\n"
            f"- Como fala, duas ou três frases de exemplo: {TODO.format('voz')}\n"
            f"- O que quer: {TODO.format('desejo')}\n"
            f"- O que teme: {TODO.format('medo')}\n"
            f"- Relações: {TODO.format('relações')}\n"
            f"- O que sabe e o que não sabe: {TODO.format('conhecimento')}\n"
            f"- Fatos fixos que nunca mudam: {TODO.format('fatos fixos')}\n")


def _universe_sheet(u: Universe) -> str:
    wiki = f" Wiki: {u.wiki}.fandom.com." if u.wiki else ""
    status = "" if u.active else " (reserva: fora da lista fechada da história)"
    allowed = f" Personagens permitidos: {', '.join(u.allowed_characters)}." if u.allowed_characters else ""
    return (f"**{u.name}.** Papel: {ROLES.get(u.role, u.role)}{status}.{wiki}{allowed} "
            f"{u.notes or TODO.format('o que existe neste universo, o que não pode acontecer')}")


def _universe_short(u: Universe) -> str:
    allowed = f" Allowed: {', '.join(u.allowed_characters)}." if u.allowed_characters else " No canon characters."
    return f"- **{u.name}.** {TODO.format('what exists here and what never appears')}{allowed}"


def build_akashic(answers: dict) -> str:
    """Markdown completo do Registro Akáshico v2 para as respostas dadas."""
    meta = meta_from_answers(answers)
    a = answers
    heroes = [c for c in meta.characters if c.role == "protagonist"]
    others = [c for c in meta.characters if c.role != "protagonist"]
    active = [u for u in meta.universes if u.active]
    out: list[str] = [
        f"# Bíblia do Mundo: {meta.title}", "",
        "Documento vivo do projeto e fonte única de verdade da história. Os modelos recebem a versão curta deste "
        "documento em cada capítulo.", "",
        "Convenções:",
        "- As seções que vão para os modelos estão em inglês e marcadas com \"(EN)\". O resto é para o autor.",
        "- `registro_modelo.md` é a versão curta que o pipeline carrega, gerada das seções 1, 2, 5.8, 9.5, 10, 13.2 e 14.",
        "- Linhas que começam com \"Nota:\" são recados para o autor e não vão para os modelos.",
        "- `[A DEFINIR: ...]` marca o que o assistente não conseguiu preencher. Resolva antes de gerar capítulos.", "",
        "---", "",
        "## 1. Story summary (EN)", "", "Nota: entra em todo prompt de rascunho. Manter perto de 350 palavras.", "",
        _summary(meta, a), "",
        "## 2. Inviolable rules (EN)", "", "Nota: numeradas para a premissa de cada capítulo poder citar.", "",
    ]
    out += [f"{i}. {r}" for i, r in enumerate(_rules(meta, a), 1)]
    out += ["", "## 3. Premissa", "",
            f"- Estrutura: {_labels('structure', meta.structure)}.",
            f"- Origem do mundo: {_labels('origin', a.get('origin', 'original'))}.",
            f"- {_CONNECTION_PT[a['connection']]}" if a.get("connection") else "- Mundo único, sem conexão entre universos.",
            f"- Época de início: {a['time_anchor']}." if a.get("time_anchor") else f"- Época de início: {TODO.format('época')}",
            f"- {TODO.format('premissa em 5 a 8 linhas: como a história começa e o que o protagonista quer')}", "",
            "## 4. Sistema de poder", "",
            f"- Sistema dominante: {_labels('power_system', a.get('power_system', 'none'))}.",
            f"- Morte: {_labels('death_rules', a.get('death_rules', 'permanent'))}."]
    if a.get("power_balance"):
        out.append(f"- Convivência de poderes: {_labels('power_balance', a['power_balance'])}.")
    if a.get("game_rules"):
        out.append(f"- Regras de jogo: {_labels('game_rules', a['game_rules'])}.")
    out += [f"- {TODO.format('como o sistema funciona, o que está definido e o que é mistério')}", "",
            "## 5. Personagens", "", "### 5.1 Ficha padrão", "",
            "Toda personagem fixa usa estes campos. Os modelos recebem a versão curta da seção 5.8.", ""]
    for i, c in enumerate(heroes, 2):
        out += [_character_block(c, f"5.{i}")]
    out += ["### 5.7 Elenco recorrente", ""]
    out += [f"- {c.name} ({c.origin or c.role})." for c in others] or ["- Nenhum cadastrado ainda."]
    out += ["", "### 5.8 Cast (short sheets for the model) (EN)", ""]
    for c in meta.characters:
        out.append(f"- **{c.name}.** {TODO.format('one paragraph: origin, look, personality, powers, how they speak')}")
    out += ["", "## 6. Arcos", "", f"- {TODO.format('arcos da temporada 1, um por linha')}", "",
            "## 7. Locais e organizações", "", f"- {TODO.format('locais e organizações que aparecem')}", "",
            "## 8. Cenário central", "", f"- {TODO.format('o lugar principal da história e suas regras')}", "",
            "## 9. Universos", "", "### 9.1 Regra", ""]
    scope = {"closed": "Lista fechada: só estes universos existem.", "open": "Lista aberta: universos novos podem entrar."}
    out += [scope.get(a.get("multiverse_scope", ""), "Universo único."), "", "### 9.2 Fichas para o autor", ""]
    out += [_universe_sheet(u) + "\n" for u in meta.universes]
    out += ["### 9.5 Universes (short list for the model) (EN)", ""]
    if meta.structure == "multiverse" and a.get("multiverse_scope") == "closed":
        out += ["Only these universes exist in the story. No other universe and no character from outside this list may appear on stage.", ""]
    out += [_universe_short(u) for u in active] or ["- " + TODO.format("universes")]
    out += ["", "## 10. Style guide (EN)", "", "Nota: entra em todo prompt de rascunho e de polimento.", "",
            f"- Language: {_LANG_EN.get(meta.language, meta.language)}. All prose and dialogue in that language.",
            f"- Point of view and tense: {_POV_EN.get(a.get('pov', ''), TODO.format('point of view'))}, "
            f"{_TENSE_EN.get(a.get('tense', ''), TODO.format('tense'))}.",
            f"- Tone: {', '.join(str(_TONE_EN.get(t, t)) for t in a.get('tone', [])) or TODO.format('tone')}.",
            f"- Content: {_RATING_EN.get(a.get('rating', ''), TODO.format('rating'))}."]
    if a.get("romance"):
        out.append(f"- Romance: {'a subplot' if a['romance'] == 'subplot' else 'the main plot'}.")
    out += [f"- Chapter: {_LENGTH_EN.get(a.get('chapter_length', ''), TODO.format('length'))}. Title format: \"Chapter N: Title\".",
            "- Sentences: short and concrete. Show, don't tell.",
            "- Dialogue: one speaker per paragraph. Each character keeps the voice from their sheet.",
            "- Names: use the official spellings list. Never invent new names for existing characters or places."]
    if a.get("references", "").strip():
        out.append(f"- Reference: {a['references'].strip()}")
    out += ["", "## 11. Estrutura da história", "", f"- Título: {meta.title}.",
            f"- Antagonismo: {_labels('antagonist', a.get('antagonist', 'none'))}.",
            f"- Temporada 1: {a['season_plan'].strip()} capítulos." if a.get("season_plan", "").strip()
            else f"- Temporada 1: {TODO.format('número de capítulos e onde termina')}",
            "", "### 11.1 Plano dos capítulos", "", f"- {TODO.format('um capítulo por linha')}", "",
            "## 12. Linha do tempo", "", f"- {TODO.format('eventos em ordem')}", "",
            "## 13. Glossário e grafias", "", "### 13.1 Glossário para o autor", "",
            f"- {TODO.format('termos próprios do mundo')}", "", "### 13.2 Official spellings (EN)", ""]
    out += [f"- {c.name}" for c in meta.characters] + [f"- {u.name}" for u in meta.universes if u.active]
    out += ["", "## 14. What the models must not do (EN)", "",
            "- Do not contradict section 2 or bring in anything outside section 9.5.",
            "- Do not invent names, powers or places that the sheets do not define.",
            "- Do not explain how a mystery works if section 4 keeps it a mystery.", ""]
    return write_meta("\n".join(out), meta)


def catalog_universe(name: str, role: str = "source", active: bool = True) -> Universe:
    """Universo pronto a partir do catálogo (com o subdomínio da wiki)."""
    entry = BY_NAME[name]
    return Universe(id=entry["id"], name=entry["name"], role=role, wiki=entry["wiki"], active=active)
