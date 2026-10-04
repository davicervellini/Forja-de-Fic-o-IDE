"""
wizard.py — O assistente de criação do Registro Akáshico, para a interface web.

A árvore de perguntas (akashic_tree.py) e o gerador do registro (akashic_builder.py) não mudam: este
módulo só os entrega à tela (perguntas, catálogo de universos, papéis) e confere as respostas antes
de gerar o texto.
"""

from dataclasses import asdict

from pipeline.akashic_builder import build_akashic
from pipeline.akashic_catalog import CATALOG
from pipeline.akashic_schema import ROLES
from pipeline.akashic_tree import QUESTIONS, missing, prune

CHARACTER_ROLES = {"protagonist": "Protagonista", "supporting": "Coadjuvante", "antagonist": "Antagonista"}


def definition() -> dict:
    """Perguntas (com as condições de quando aparecem), catálogo de universos e papéis."""
    questions = []
    for q in QUESTIONS:
        d = asdict(q)
        d["options"] = [list(o) for o in q.options]
        questions.append(d)
    return {
        "questions": questions,
        "catalog": [{"id": e["id"], "name": e["name"], "wiki": e["wiki"]} for e in CATALOG],
        "universe_roles": ROLES,
        "character_roles": CHARACTER_ROLES,
    }


def clean(answers: dict) -> dict:
    """Respostas só das perguntas que valem, com listas sem itens vazios."""
    a = prune(dict(answers or {}))
    for key in ("universes", "protagonists", "supporting"):
        if key in a:
            a[key] = [x for x in (a[key] or []) if isinstance(x, dict) and str(x.get("name", "")).strip()]
            for x in a[key]:
                x["name"] = str(x["name"]).strip()
                if isinstance(x.get("allowed_characters"), str):
                    x["allowed_characters"] = [c.strip() for c in x["allowed_characters"].split(",") if c.strip()]
    return a


def preview(answers: dict) -> dict:
    """{text: registro gerado, missing: [{id, title}] perguntas obrigatórias sem resposta, answers: limpas}."""
    a = clean(answers)
    return {
        "text": build_akashic(a),
        "missing": [{"id": q.id, "title": q.title} for q in missing(a)],
        "answers": a,
    }


def build_text(answers: dict) -> str:
    return build_akashic(clean(answers))
