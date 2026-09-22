"""
Testes da premissa automática: quando um capítulo termina, o programa escreve a premissa do
seguinte e ela espera a aprovação do usuário antes de entrar na geração.

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline import config
from pipeline import premise as P
from pipeline import premise_flow
from pipeline import prompts

SUGGESTION = """Chapter Premise: Chapter 2: The Next Day

Goal: Arthur explores the city.
Opening: He wakes where chapter 1 ended.
Characters in scene: Arthur Galhardo
Locations in scene:

Scenes:
1. He wakes up. (about 300 words)
2. He walks out. (about 300 words)

Hook: A door opens.
Must include: the cold.
Must not appear: nobody else."""


def _stub(model, system_prompt, user_prompt, **kw):
    if system_prompt.startswith(P.SYSTEM_PREMISE_WRITER):
        return SUGGESTION
    if system_prompt == prompts.SYSTEM_DRAFTING:
        return " ".join(f"W{i}." for i in range(300))
    if system_prompt == prompts.SYSTEM_UPDATING:
        return "=== DYNAMIC MEMORY ===\nm\n=== CHARACTER ROSTER ===\nr\n=== OPEN THREADS ===\nt"
    return "OK." if system_prompt == prompts.SYSTEM_CONSISTENCY else "text"


def test_capitulo_pronto_gera_premissa_do_seguinte_esperando_aprovacao(tmp_path):
    from fastapi.testclient import TestClient
    import webapp.server as srv

    (tmp_path / "projetos").mkdir()
    with patch.object(config, "PROJECTS_DIR", tmp_path / "projetos"), patch.object(config, "DATA_DIR", tmp_path), \
            patch.object(config, "AUTO_NEXT_PREMISE", True), patch.object(config, "UI_LANGUAGE", "en"), \
            patch.object(config, "PROVIDER_DRAFTING", "ollama"), patch.object(config, "PROVIDER_REFINING", "ollama"), \
            patch.object(config, "PROVIDER_SUMMARIZING", "ollama"), \
            patch.object(srv, "check_ollama_health", lambda **kw: True), \
            patch.object(orch_mod, "generate_text", _stub), patch.object(premise_flow, "generate_text", _stub):
        c = TestClient(srv.create_app())
        jobs = c.app.state.jobs
        slug = c.post("/api/projects", json={"name": "Auto"}).json()["slug"]
        c.post(f"/api/projects/{slug}/chapters",
               json={"premise": "Chapter Premise: Chapter 1: Dark\n\nScenes:\n1. Wakes. (about 300 words)"})
        q, _ = jobs.subscribe()
        assert c.post(f"/api/projects/{slug}/generate", json={}).status_code == 200
        assert jobs.wait(10)

        chapters = c.get(f"/api/projects/{slug}").json()["chapters"]
        assert [ch["num"] for ch in chapters] == [1, 2]
        assert chapters[0]["status"] == "done"
        assert chapters[1]["premise_pending"] is True
        chap2 = c.get(f"/api/projects/{slug}/chapters/2").json()
        assert "The Next Day" in chap2["premise"] and "He walks out." in chap2["premise"]
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        auto = [e for e in events if e["type"] == "premise_auto"]
        assert auto and auto[0]["chapter"] == 2 and auto[0]["ok"]

        # "Gerar tudo" não gera premissa sem aprovação.
        r = c.post(f"/api/projects/{slug}/generate", json={})
        assert r.status_code == 400 and "aprovação" in r.json()["detail"]

        # Com o capítulo 2 pronto e aprovado, a próxima premissa não sobrescreve uma que já existe.
        c.post(f"/api/projects/{slug}/chapters/2/premise/approve")
        assert c.get(f"/api/projects/{slug}").json()["chapters"][1]["premise_pending"] is False


def test_premissa_existente_nao_e_sobrescrita(tmp_path):
    from pipeline.project import StoryProject
    proj = StoryProject.create(tmp_path, "p")
    (proj.chapter_dir(2)).mkdir(parents=True, exist_ok=True)
    (proj.chapter_dir(2) / "premissa.md").write_text("MINHA PREMISSA", encoding="utf-8")
    with patch.object(premise_flow, "generate_text", _stub):
        assert premise_flow.auto_next_premise(proj, 1) is None
    assert (proj.chapter_dir(2) / "premissa.md").read_text(encoding="utf-8") == "MINHA PREMISSA"
    assert not premise_flow.pending_approval(proj, 2)


def test_gerar_um_capitulo_pelo_numero_aprova_a_premissa(tmp_path):
    from pipeline.project import StoryProject
    from pipeline import chapters as ch
    proj = StoryProject.create(tmp_path, "p")
    with patch.object(premise_flow, "generate_text", _stub), patch.object(config, "UI_LANGUAGE", "en"):
        done = premise_flow.auto_next_premise(proj, 1)
    assert done == {"chapter": 2, "ok": True, "removed": []}
    assert premise_flow.pending_approval(proj, 2)
    premise_flow.approve(proj, 2)
    assert not premise_flow.pending_approval(proj, 2)
    assert ch.read_info(proj, 2)["premise_auto"] is True


def test_premissa_em_ingles_e_traduzida_para_o_idioma_do_usuario(tmp_path):
    """Modelo pequeno segue o idioma do registro: a premissa volta traduzida campo por campo, com o formato intacto."""
    from pipeline.project import StoryProject
    proj = StoryProject.create(tmp_path, "p")
    seen = []
    pieces = {
        "Arthur explores the city.": "Arthur explora a cidade.",
        "He wakes where chapter 1 ended.": "Ele acorda onde o capítulo 1 terminou.",
        "He wakes up.": "Ele acorda.",
        "He walks out.": "Ele sai para o corredor.",
        "A door opens.": "Uma porta se abre.",
        "the cold.": "o frio.",
    }

    def stub(model, system_prompt, user_prompt, **kw):
        seen.append(user_prompt)
        if system_prompt.startswith(P.SYSTEM_PREMISE_WRITER):
            assert "Brazilian Portuguese" in system_prompt
            return SUGGESTION
        assert system_prompt.startswith("You translate") and "Brazilian Portuguese" in system_prompt
        return pieces[user_prompt.strip()]

    with patch.object(premise_flow, "generate_text", stub), patch.object(config, "UI_LANGUAGE", "pt-BR"):
        done = premise_flow.auto_next_premise(proj, 1)
    # Um pedido para a premissa e um por campo de texto corrido: objetivo, abertura, gancho, "precisa aparecer" e 2 cenas.
    assert done["ok"] and len(seen) == 7
    text = (proj.chapter_dir(2) / "premissa.md").read_text(encoding="utf-8")
    assert "Ele sai para o corredor. (about 300 words)" in text
    assert "Goal: Arthur explora a cidade." in text and "Chapter 2: The Next Day" in text
    # Nomes não passam pela tradução.
    assert "Characters in scene: Arthur Galhardo" in text
