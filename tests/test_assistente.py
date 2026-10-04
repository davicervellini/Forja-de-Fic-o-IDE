"""Testes do assistente de criação do Registro Akáshico na interface web."""

from unittest.mock import patch

from pipeline import config, wizard
from pipeline.akashic_schema import read_meta

ANSWERS = {
    "title": "A Cidade Afundada", "origin": "fanfic", "structure": "single",
    "universes": [{"id": "stargate", "name": "Stargate", "wiki": "stargate", "role": "base", "catalog": True},
                  {"name": "  "}],
    "canon_policy": "changes", "power_system": "tech", "death_rules": "permanent",
    "protagonists": [{"name": "Arthur Galhardo", "origin": "transportado", "age": "28", "universe": "stargate"}],
    "antagonist": "force", "tone": ["comedy", "adventure"], "rating": "teen", "pov": "third_limited",
    "tense": "past", "language": "en", "chapter_length": "medium",
    # Resposta de um ramo que não vale (multiverso) sai.
    "connection": "hub",
}


def test_definicao_tem_perguntas_condicoes_e_catalogo():
    d = wizard.definition()
    ids = [q["id"] for q in d["questions"]]
    assert ids[0] == "title" and "universes" in ids
    conn = next(q for q in d["questions"] if q["id"] == "connection")
    assert conn["when"] == {"structure": ["multiverse"]} and conn["options"][0][0] == "hub"
    assert any(c["name"] == "Stargate" for c in d["catalog"])


def test_previa_aponta_o_que_falta_e_limpa_as_respostas():
    p = wizard.preview({"title": "X", "structure": "multiverse",
                        "universes": [{"name": "Stargate"}]})
    ids = [m["id"] for m in p["missing"]]
    assert "universes" in ids and "connection" in ids and "protagonists" in ids
    full = wizard.preview(ANSWERS)
    assert full["missing"] == []
    assert "connection" not in full["answers"] and len(full["answers"]["universes"]) == 1
    assert full["text"].startswith("# Bíblia do Mundo: A Cidade Afundada")


def test_api_cria_projeto_pelo_assistente_e_refaz_o_registro(tmp_path):
    from fastapi.testclient import TestClient
    import webapp.server as srv

    (tmp_path / "projetos").mkdir()
    with patch.object(config, "PROJECTS_DIR", tmp_path / "projetos"), patch.object(config, "DATA_DIR", tmp_path):
        c = TestClient(srv.create_app())
        assert len(c.get("/api/wizard").json()["questions"]) > 10
        slug = c.post("/api/projects", json={"name": "Afundada", "language": "en", "wizard_answers": ANSWERS}).json()["slug"]
        proj_dir = tmp_path / "projetos" / slug
        meta, _ = read_meta((proj_dir / "registro_akashico.md").read_text(encoding="utf-8"))
        assert meta.title == "A Cidade Afundada" and [c_.name for c_ in meta.characters] == ["Arthur Galhardo"]
        assert (proj_dir / "registro_modelo.md").exists()
        got = c.get(f"/api/projects/{slug}/akashic").json()
        assert got["answers"]["title"] == "A Cidade Afundada"

        bad = c.put(f"/api/projects/{slug}/akashic/wizard", json={"answers": {"title": "Y"}})
        assert bad.status_code == 400 and "Falta responder" in bad.json()["detail"]
        ok = c.put(f"/api/projects/{slug}/akashic/wizard", json={"answers": {**ANSWERS, "title": "Outro Título"}})
        assert ok.status_code == 200
        assert "Outro Título" in (proj_dir / "registro_akashico.md").read_text(encoding="utf-8")
        assert "A Cidade Afundada" in (proj_dir / "registro_akashico.anterior.md").read_text(encoding="utf-8")
