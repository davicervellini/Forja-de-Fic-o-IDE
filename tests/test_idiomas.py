"""
Testes dos dois idiomas: o do usuário (interface, resumos, memória, fichas, conferências) e
o da história (só o texto dos capítulos e a exportação).

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline import config
from pipeline import languages as L
from pipeline import premise as P
from pipeline import prompts
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.project import StoryProject

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"


def test_regras_de_idioma():
    assert "write all narration and dialogue in English" in L.story_rule("en")
    assert "never copy their language" in L.story_rule("en")
    assert "write your answer in Brazilian Portuguese" in L.notes_rule("pt-BR")
    assert L.chapter_heading("Chapter 2: The City", 2, "pt-BR") == "Capítulo 2: The City"
    assert L.chapter_heading("Capítulo 4: X", 4, "en") == "Chapter 4: X"
    assert L.chapter_heading(None, 3, "fr") == "Chapitre 3"


def test_idioma_da_historia_vem_do_projeto(tmp_path):
    proj = StoryProject.create(tmp_path, "p")
    assert L.story_language(proj) == "en"
    proj.metadata["language"] = "es"
    assert L.story_language(proj) == "es"


def test_cada_fase_recebe_o_idioma_certo(tmp_path):
    proj = StoryProject.create(tmp_path, "p")
    proj.metadata["language"] = "en"
    seen: dict[str, list[str]] = {}

    def stub(model, system_prompt, user_prompt, **kw):
        seen.setdefault(system_prompt, []).append(user_prompt)
        if system_prompt == prompts.SYSTEM_DRAFTING:
            return " ".join(f"W{i}." for i in range(300))
        if system_prompt == prompts.SYSTEM_UPDATING:
            return "=== DYNAMIC MEMORY ===\nm\n=== CHARACTER ROSTER ===\nr\n=== OPEN THREADS ===\nt"
        return "OK." if system_prompt == prompts.SYSTEM_CONSISTENCY else "text"

    premise = "Chapter Premise: Chapter 1: Terça\n\nScenes:\n1. Ele acorda. (about 300 words)\n2. Ele sai. (about 300 words)"
    with patch.object(config, "UI_LANGUAGE", "pt-BR"), patch.object(orch_mod, "generate_text", stub):
        result = PipelineOrchestrator(project=proj).run_single(premise, 1)
    assert result.status == "done", result.error
    story, notes = L.story_rule("en"), L.notes_rule("pt-BR")
    for system in (prompts.SYSTEM_DRAFTING, prompts.SYSTEM_REFINING):
        assert all(p.rstrip().endswith(story) for p in seen[system]), system[:40]
    for system in (prompts.SYSTEM_SUMMARIZING, prompts.SYSTEM_UPDATING, prompts.SYSTEM_CONSISTENCY):
        assert all(p.rstrip().endswith(notes) for p in seen[system]), system[:40]
    # O título segue o idioma da história, não o da premissa.
    final = (proj.chapter_dir(1) / "capitulo_final.md").read_text(encoding="utf-8")
    assert final.startswith("Chapter 1: Terça")
    for system in (prompts.SYSTEM_DRAFTING, prompts.SYSTEM_REFINING, prompts.SYSTEM_SUMMARIZING, prompts.SYSTEM_MERGING):
        assert "in English" not in system


def test_premissa_e_conferencia_no_idioma_do_usuario():
    with patch.object(config, "UI_LANGUAGE", "es"):
        s = P.build_suggest_prompt(2, 2000, akashic="A", outline=None, previous_tail="", story_so_far="",
                                   summaries=[], open_threads="", roster="")
        c = P.build_check_prompt(2, "Goal: x", akashic="A", outline=None, previous_tail="", story_so_far="",
                                 summaries=[], open_threads="", roster="")
    assert s.rstrip().endswith(L.notes_rule("es")) and c.rstrip().endswith(L.notes_rule("es"))
    assert "português" not in P.SYSTEM_PREMISE_CHECKER.lower()


def test_idioma_da_interface_na_configuracao_e_na_api(tmp_path):
    from fastapi.testclient import TestClient
    from webapp.server import create_app

    assert config.coerce("UI_LANGUAGE", "") == "pt-BR"
    assert "UI_LANGUAGE" in config.UI_KEYS
    root = tmp_path / "projetos"
    root.mkdir()
    with patch.object(config, "PROJECTS_DIR", root), patch.object(config, "DATA_DIR", tmp_path), \
            patch.object(config, "SETTINGS_PATH", tmp_path / "config.json"):
        c = TestClient(create_app())
        langs = c.get("/api/languages").json()
        assert langs["all"]["en"] == "English" and "en" in langs["ui"]
        slug = c.post("/api/projects", json={"name": "Livro", "language": "es"}).json()["slug"]
        assert c.get(f"/api/projects/{slug}").json()["story_language"] == "es"
        c.put(f"/api/projects/{slug}/meta", json={"language": "en"})
        assert c.get(f"/api/projects/{slug}").json()["story_language"] == "en"
        old = config.UI_LANGUAGE
        try:
            assert c.put("/api/settings", json={"values": {"UI_LANGUAGE": "en"}}).status_code == 200
            assert c.get("/api/status").json()["ui_language"] == "en"
        finally:
            config.apply_settings({"UI_LANGUAGE": old})


def test_textos_fixos_da_tela_tem_traducao_em_ingles():
    """Todo texto fixo do index.html precisa estar no dicionário de inglês."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    # <code> (nomes de arquivo) não é traduzido; fica uma fronteira no lugar para não juntar os textos vizinhos.
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    html = re.sub(r"<code>.*?</code>", "<i></i>", html, flags=re.S)
    dictionary = json.loads((STATIC / "i18n" / "en.json").read_text(encoding="utf-8"))
    keys = {re.sub(r"\s+", " ", k).strip() for k in dictionary}
    missing = []
    for m in re.finditer(r">([^<>]+)<", html):
        text = re.sub(r"\s+", " ", m.group(1)).strip()
        if text and re.search(r"[A-Za-zÀ-ÿ]{2}", text) and text not in keys:
            missing.append(text)
    for m in re.finditer(r'(?:placeholder|title)="([^"]+)"', html):
        if m.group(1) not in keys:
            missing.append(m.group(1))
    assert not missing, f"Sem tradução em en.json: {missing}"


def test_dicionarios_sao_validos():
    for f in (STATIC / "i18n").glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for k, v in data.items():
            if k.startswith("_"):
                continue
            assert isinstance(v, str) and v.strip(), f"{f.name}: {k}"
            assert k.count("{x}") == v.count("{x}"), f"{f.name}: {{x}} diferente em {k!r}"
