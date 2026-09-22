"""
akashic_tree.py — Árvore de escolhas em cascata para montar o Registro Akáshico de um projeto novo.

A árvore foi desenhada a partir dos eixos que as grandes séries usam para organizar o próprio cânone:
  1. estrutura do mundo (um universo, um multiverso com universos numerados ou linhas do tempo alternativas);
  2. como os mundos se conectam (portal, hub, viagem dimensional, reencarnação, simulação...);
  3. quais universos entram, com que papel e com que regras (lista fechada ou aberta);
  4. sistema de poder e o que acontece quando poderes de mundos diferentes se encontram;
  5. regras de vida e morte;
  6. elenco (protagonistas, apoio, antagonismo);
  7. tom, classificação, ponto de vista, idioma;
  8. estrutura da história (temporadas, tamanho dos capítulos).
Cada pergunta só aparece quando as respostas anteriores a tornam relevante (campo `when`). Isto é dados + funções
puras: a interface (gui_akashic.py) e os testes usam as mesmas funções.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    kind: str                                   # single | multi | text | longtext | universes | characters
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()   # (valor, rótulo)
    when: dict = field(default_factory=dict)    # {id_pergunta: [valores]}: aparece se a resposta estiver na lista
    required: bool = True
    min_items: int = 0                          # para universes/characters (também aceita callable via min_for)


def _o(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(pairs)


QUESTIONS: tuple[Question, ...] = (
    Question("title", "Qual é o título da história?", "text"),
    Question("origin", "De onde vem o mundo da história?", "single", options=_o(
        ("original", "Mundo original, criado para esta fic"),
        ("fanfic", "Fanfic de uma franquia existente"),
        ("crossover", "Crossover de várias franquias"))),
    Question("structure", "Como o mundo é organizado?", "single",
             "Um universo só, vários universos conectados ou um mesmo universo em linhas do tempo diferentes.",
             _o(("single", "Um universo só"),
                ("multiverse", "Multiverso: vários universos"),
                ("timelines", "Um universo com linhas do tempo alternativas"))),
    Question("connection", "Como os universos se conectam?", "single", options=_o(
        ("hub", "Hub central (estação, cidade, mercado entre mundos)"),
        ("portals", "Rede de portais"),
        ("travel", "Viagem dimensional do protagonista"),
        ("reincarnation", "Reencarnação ou transmigração"),
        ("simulation", "Simulação ou jogo em que os mundos são fases"),
        ("organization", "Organização que administra o multiverso"),
        ("other", "Outro (descrever depois)")), when={"structure": ["multiverse"]}),
    Question("multiverse_scope", "A lista de universos é fechada?", "single",
             "Fechada: só os universos listados existem, e nenhum personagem de fora pode aparecer.",
             _o(("closed", "Fechada: só os universos listados"),
                ("open", "Aberta: novos universos podem entrar durante a história")),
             when={"structure": ["multiverse"]}),
    Question("universes", "Quais universos entram na história?", "universes",
             "Escolha do catálogo ou cadastre um universo novo. Cada um tem papel, wiki e personagens permitidos. "
             "Multiverso pede pelo menos 2; as outras estruturas, 1.", min_items=1),
    Question("divergence", "Qual é o ponto de divergência das linhas do tempo?", "longtext",
             "O evento a partir do qual as linhas se separam.", when={"structure": ["timelines"]}),
    Question("canon_policy", "Quanto do cânone original a história respeita?", "single", options=_o(
        ("strict", "Cânone estrito: nada contradiz as obras"),
        ("changes", "Cânone com mudanças declaradas (linha do tempo alternativa)"),
        ("loose", "Homenagem livre: só nomes e ideias")), when={"origin": ["fanfic", "crossover"]}),
    Question("time_anchor", "Em que época do cânone a história começa?", "text",
             "Ex.: 'dez anos antes da série', 'ano 1804', 'presente'.", required=False,
             when={"origin": ["fanfic", "crossover"]}),
    Question("power_system", "Qual é o sistema de poder dominante?", "single", options=_o(
        ("none", "Sem poderes: mundo realista"),
        ("tech", "Tecnologia avançada"),
        ("magic", "Magia"),
        ("hybrid", "Tecnologia e magia juntas"),
        ("game", "Sistema de jogo (níveis, habilidades, interface)"),
        ("superpowers", "Superpoderes ou individualidades"))),
    Question("game_rules", "Que regras de jogo o sistema tem?", "multi", required=False, options=_o(
        ("levels", "Níveis e experiência"), ("skills", "Habilidades e classes"),
        ("respawn", "Morte e retorno (respawn)"), ("shop", "Loja e moeda"),
        ("guilds", "Guildas e grupos"), ("quests", "Missões")), when={"power_system": ["game"]}),
    Question("power_balance", "Como poderes de universos diferentes convivem?", "single",
             "Só aparece quando há mais de um mundo ou franquia.", options=_o(
                 ("native", "Cada um mantém os próprios poderes"),
                 ("equalized", "Todos igualados por uma regra do mundo (ex.: nível base)"),
                 ("tiers", "Faixas de poder separadas (filas, ligas, categorias)")),
             when={"structure": ["multiverse"]}),
    Question("death_rules", "Como funciona a morte?", "single", options=_o(
        ("permanent", "Permanente"),
        ("reversible", "Reversível (retorno, ressurreição, respawn)"),
        ("conditional", "Depende do lugar ou do modo (ex.: só em jogos, só com consentimento)"))),
    Question("protagonists", "Quem são os protagonistas?", "characters",
             "Nome, origem e idade. Pelo menos um.", min_items=1),
    Question("supporting", "Quem são os personagens de apoio principais?", "characters", required=False),
    Question("antagonist", "Que tipo de antagonismo a história tem?", "single", options=_o(
        ("none", "Sem vilão fixo"),
        ("fixed", "Um vilão principal"),
        ("episodic", "Antagonistas por episódio ou arco"),
        ("force", "Uma força impessoal (sistema, sociedade, natureza)"))),
    Question("tone", "Qual é o tom?", "multi", options=_o(
        ("comedy", "Comédia"), ("drama", "Drama"), ("action", "Ação"), ("adventure", "Aventura"),
        ("mystery", "Mistério"), ("slice", "Cotidiano"), ("romance", "Romance"), ("horror", "Terror"))),
    Question("romance", "Qual o peso do romance?", "single", options=_o(
        ("subplot", "Subtrama"), ("main", "Trama principal")), when={"tone": ["romance"]}),
    Question("rating", "Qual a classificação indicativa?", "single", options=_o(
        ("kids", "Livre"), ("teen", "Adolescente"), ("mature", "Adulto"))),
    Question("pov", "Qual o ponto de vista?", "single", options=_o(
        ("first", "Primeira pessoa"), ("third_limited", "Terceira pessoa limitada a um personagem"),
        ("omniscient", "Narrador onisciente"))),
    Question("tense", "Qual o tempo verbal?", "single", options=_o(("past", "Passado"), ("present", "Presente"))),
    Question("language", "Em que idioma a história é escrita?", "single", options=_o(
        ("en", "Inglês"), ("pt-BR", "Português do Brasil"), ("es", "Espanhol"), ("other", "Outro"))),
    Question("season_plan", "Quantos capítulos tem a primeira temporada?", "text", required=False),
    Question("chapter_length", "Qual o tamanho de cada capítulo?", "single", options=_o(
        ("short", "Curto: 1.000 a 1.500 palavras"), ("medium", "Médio: 2.000 a 2.500 palavras"),
        ("long", "Longo: 3.000 palavras ou mais"))),
    Question("references", "Há obras de referência de estilo?", "longtext",
             "Uma obra e o que imitar dela (ritmo, leveza, tom). Não copie trechos.", required=False),
)

BY_ID = {q.id: q for q in QUESTIONS}


def min_items(q: Question, answers: dict) -> int:
    """Multiverso pede pelo menos 2 universos."""
    if q.id == "universes" and answers.get("structure") == "multiverse":
        return 2
    return q.min_items


def is_visible(q: Question, answers: dict) -> bool:
    for key, allowed in q.when.items():
        value = answers.get(key)
        values = value if isinstance(value, (list, tuple, set)) else [value]
        if not any(v in allowed for v in values):
            return False
    return True


def visible_questions(answers: dict) -> list[Question]:
    """Perguntas que valem para as respostas atuais, na ordem em que aparecem."""
    return [q for q in QUESTIONS if is_visible(q, answers)]


def is_answered(q: Question, answers: dict) -> bool:
    value = answers.get(q.id)
    if q.kind in ("universes", "characters"):
        return len(value or []) >= min_items(q, answers)
    if q.kind == "multi":
        return bool(value)
    return bool(str(value or "").strip())


def missing(answers: dict) -> list[Question]:
    """Perguntas obrigatórias visíveis ainda sem resposta."""
    return [q for q in visible_questions(answers) if q.required and not is_answered(q, answers)]


def prune(answers: dict) -> dict:
    """Remove respostas de perguntas que deixaram de ser relevantes (o usuário voltou e mudou um ramo)."""
    keep = {q.id for q in visible_questions(answers)}
    return {k: v for k, v in answers.items() if k in keep}
