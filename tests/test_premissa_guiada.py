"""
Testes da premissa guiada (pipeline/premise.py), da quebra de parágrafos longos e da API
de sugestão e conferência de premissa.

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline import config
from pipeline import premise as P
from pipeline import premise_flow
from pipeline import prompts
from pipeline.akashic_schema import AkashicMeta, Character, write_meta
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.project import StoryProject
from pipeline.scenes import count_words, paragraphs, parse_premise, split_long_paragraphs

FORM = P.PremiseForm(
    title="The City Under the Sea",
    goal="Atlantis answers the ATA gene.",
    opening="Right where chapter 1 ended: the gate room, lights at half strength.",
    characters=["Arthur Galhardo"],
    scenes=[{"text": "The lit corridors. He walks.", "words": 700},
            {"text": "The chair. He sits.", "words": 800}],
    hook="The terminal prints: power 3%.",
    must_include="one [System] line",
    must_not="other people, darkness at the start",
)
from tests.isolamento import local_only


def test_formulario_vira_texto_que_o_pipeline_le_e_volta_igual():
    text = P.to_text(FORM, 2)
    assert text.startswith("Chapter Premise: Chapter 2: The City Under the Sea")
    plan = parse_premise(text)
    assert plan.title == "Chapter 2: The City Under the Sea"
    assert [s.target_words for s in plan.scenes] == [700, 800]
    assert plan.hook == "The terminal prints: power 3%."
    assert P.from_text(text) == FORM


def test_premissa_escrita_a_mao_e_lida_nos_campos():
    text = """Chapter Premise: Chapter 1: Tuesday

Goal: Kill Arthur.

Scenes:
1. Tuesday morning (about 350 words). Alarm, snooze.
2. The waiting room (about 900 words). The Management.

Hook: build progress 0%.

Must not appear: fairies, Credits."""
    f = P.from_text(text)
    assert f.title == "Tuesday" and f.goal == "Kill Arthur."
    assert f.scenes == [{"text": "Tuesday morning. Alarm, snooze.", "words": 350},
                        {"text": "The waiting room. The Management.", "words": 900}]
    assert f.must_not == "fairies, Credits." and f.hook == "build progress 0%."


def test_linha_do_plano_da_historia():
    akashic = "### 11.1 Plano\n\n| Cap. | Título | Conteúdo | Sistema |\n|---|---|---|---|\n| 1 | Tuesday | Morre. | O Sistema |\n| 2 | The City | Gene ATA. | Gene |\n"
    assert P.outline_row(akashic, 2) == {"title": "The City", "content": "Gene ATA.", "extra": "Gene"}
    assert P.outline_row(akashic, 9) is None


def test_orientacao_da_premissa_no_prompt():
    g1 = P.chapter_guidance(FORM, {"Arthur Galhardo": "Deadpan IT guy."}, first_scene=True)
    assert "Arthur Galhardo: Deadpan IT guy." in g1 and "no other named character" in g1
    assert "The chapter opens like this" in g1 and "[System]" in g1 and "Must NOT appear" in g1
    g2 = P.chapter_guidance(FORM, {}, first_scene=False)
    assert "opens like this" not in g2


def test_paragrafos_longos_sao_quebrados_no_fim_da_frase():
    long_para = " ".join(f"He counted door number {i} and kept walking down the hall." for i in range(30))
    dialogue = '"' + " ".join(["word"] * 200) + '."'
    out = split_long_paragraphs(f"{long_para}\n\n{dialogue}")
    paras = paragraphs(out)
    assert len(paras) > 3
    assert all(p.endswith(".") or p.endswith('."') for p in paras)
    assert paras[-1] == dialogue  # fala não é quebrada
    assert count_words(out) == count_words(long_para) + count_words(dialogue)


def _project(tmp_path) -> StoryProject:
    proj = StoryProject.create(tmp_path, "teste")
    meta = AkashicMeta(title="T", characters=[Character(name="Arthur Galhardo", sheet="Deadpan IT guy.")])
    proj.akashic_path.write_text(write_meta("# T\n\n## 1. Story summary (EN)\n\nA hub.\n", meta), encoding="utf-8")
    return proj


def test_rascunho_leva_elenco_e_abertura_da_premissa(tmp_path):
    proj = _project(tmp_path)
    prompts_seen = []

    def stub(model, system_prompt, user_prompt, **kw):
        if system_prompt == prompts.SYSTEM_DRAFTING:
            prompts_seen.append(user_prompt)
            return " ".join(f"Word{len(prompts_seen)}x{i}." for i in range(400))
        if system_prompt == prompts.SYSTEM_UPDATING:
            return "=== DYNAMIC MEMORY ===\nm\n=== CHARACTER ROSTER ===\nr\n=== OPEN THREADS ===\nt"
        return "OK." if system_prompt == prompts.SYSTEM_CONSISTENCY else "text"

    with patch.object(orch_mod, "generate_text", stub):
        result = PipelineOrchestrator(project=proj).run_single(P.to_text(FORM, 1), 1)
    assert result.status == "done", result.error
    assert len(prompts_seen) >= 2
    assert all("Arthur Galhardo: Deadpan IT guy." in p for p in prompts_seen)
    assert "The chapter opens like this" in prompts_seen[0]
    assert all("The chapter opens like this" not in p for p in prompts_seen[1:])
    assert all("Must NOT appear anywhere: other people" in p for p in prompts_seen)


def test_api_formulario_sugestao_e_conferencia(tmp_path):
    from fastapi.testclient import TestClient
    import webapp.server as srv

    root = tmp_path / "projetos"
    root.mkdir()
    proj = _project(root)
    suggestion = P.to_text(FORM, 1)

    def fake_generate(model, system_prompt, user_prompt, **kw):
        if system_prompt.startswith(P.SYSTEM_PREMISE_WRITER):
            assert "Write the premise of Chapter 1" in user_prompt and "more action" in user_prompt
            return suggestion
        assert system_prompt.startswith(P.SYSTEM_PREMISE_CHECKER)
        return "- A abertura contradiz o capítulo anterior."

    with local_only(), patch.object(config, "PROJECTS_DIR", root), patch.object(config, "DATA_DIR", tmp_path), \
            patch.object(srv, "check_ollama_health", lambda **kw: True), \
            patch.object(premise_flow, "generate_text", fake_generate):
        c = TestClient(srv.create_app())
        jobs = c.app.state.jobs
        slug = proj.project_dir.name
        num = c.post(f"/api/projects/{slug}/chapters", json={}).json()["num"]
        assert c.get(f"/api/projects/{slug}").json()["chapters"][0]["status"] == "pending"
        data = c.get(f"/api/projects/{slug}/chapters/{num}/premise-form").json()
        assert data["characters"] == ["Arthur Galhardo"] and data["form"]["scenes"] == []

        r = c.put(f"/api/projects/{slug}/chapters/{num}/premise-form", json={"form": FORM.to_dict()})
        assert r.status_code == 200 and "Characters in scene: Arthur Galhardo" in r.json()["text"]
        assert (proj.chapter_dir(num) / "premissa.md").read_text(encoding="utf-8").strip() == r.json()["text"].strip()

        q, _ = jobs.subscribe()
        assert c.post(f"/api/projects/{slug}/chapters/{num}/premise/suggest", json={"notes": "more action"}).status_code == 200
        assert jobs.wait(10)
        assert c.post(f"/api/projects/{slug}/chapters/{num}/premise/check", json={"text": suggestion}).status_code == 200
        assert jobs.wait(10)
        events = {}
        while not q.empty():
            e = q.get_nowait()
            events[e["type"]] = e
        assert events["premise_result"]["ok"] is True
        assert events["premise_result"]["form"]["scenes"][1]["words"] == 800
        assert events["premise_check"]["ok"] is False and "contradiz" in events["premise_check"]["notes"]


def test_sugestao_cortada_depois_da_premissa_e_sem_quem_nao_estreou():
    raw = P.to_text(FORM, 2).replace("Characters in scene: Arthur Galhardo",
                                     "Characters in scene: Arthur, Veronica, Tinaia (voice only)")
    raw += "\n---\n\nChapter 2: The City Under the Sea\n\nThe cursor blinked."
    cut = P.cut_after_premise(raw)
    assert cut.endswith("Must not appear: other people, darkness at the start")
    form = P.from_text(cut)
    removed = P.enforce_cast(form, ["Veronica", "Tinaia"], {"title": "The City", "content": "Gene ATA."})
    assert removed == ["Veronica", "Tinaia (voice only)"]
    assert form.characters == ["Arthur"]
    assert "Veronica" not in form.characters and "Veronica" in form.must_not and "Tinaia" in form.must_not
    # Personagem que o plano do capítulo apresenta continua liberado.
    form2 = P.from_text(cut)
    P.enforce_cast(form2, ["Veronica", "Tinaia"], {"title": "Tinaia", "content": "Birth of Tinaia and Veronica."})
    assert "Veronica" in form2.characters


def test_limpeza_da_sugestao():
    raw = """Chapter Premise: Chapter 2: The City

Goal: Atlantis answers the ATA gene.
Opening: where and how the chapter starts. It must continue exactly from the PREVIOUS CHAPTER ENDING.

Scenes:
1. The gate room. He touches the Stargate. (600 words)
2. The chair. ZPM power is low. (700 words)

Hook: power 3%.
Must include: concrete things that must appear (for example, one [System] line).
Must not appear: Veronica, Atlantis gate room, the Stargate, ZPMs, Wraith, First visitors"""
    f = P.from_text(raw)
    assert [s["words"] for s in f.scenes] == [600, 700]
    assert [s.target_words for s in parse_premise(raw).scenes] == [600, 700]
    assert P.drop_template_echo(f) == ["opening", "must_include"]
    removed = P.prune_must_not(f, {"title": "The City", "content": "Atlantis e o gene ATA."})
    assert removed == ["Atlantis gate room", "the Stargate", "ZPMs"]
    assert f.must_not == "Veronica, Wraith, First visitors"
