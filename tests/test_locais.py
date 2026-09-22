"""
Testes dos locais: cadastro no Registro, leitura das páginas da wiki (sem rede), uso das
fichas no prompt de cena e importação pela API.

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline import cast, config
from pipeline import premise as P
from pipeline import prompts
from pipeline import wiki_locations as W
from pipeline.akashic_schema import AkashicMeta, Character, Location, Universe, read_meta, write_meta
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.project import StoryProject

BODY = "# T\n\n## 1. Story summary (EN)\n\nA hub.\n\n### 5.8 Cast (EN)\n\n- **Arthur.** Guy.\n\n### 9.5 Universes (EN)\n\n- **Stargate Atlantis.** City.\n"


def _project(tmp_path) -> StoryProject:
    proj = StoryProject.create(tmp_path, "teste")
    meta = AkashicMeta(
        title="T",
        universes=[Universe(id="sga", name="Stargate Atlantis", role="base", wiki="stargate",
                            model_sheet="The empty city in 1904, submerged.")],
        characters=[Character(name="Arthur", role="protagonist")],
    )
    proj.akashic_path.write_text(write_meta(BODY, meta), encoding="utf-8")
    return proj


def test_locais_sao_gravados_e_validados(tmp_path):
    proj = _project(tmp_path)
    data = cast.overview(proj)
    assert data["locations"] == []
    locs = [
        {"name": "Atlantis", "universe": "sga", "model_sheet": "A city-ship. Never: Wraith inside.", "always": True},
        {"name": "Chair room", "universe": "sga", "parent": "Atlantis", "model_sheet": "Top of the east tower.",
         "images": ["https://img/x.png"], "url": "https://stargate.fandom.com/wiki/Chair_room"},
    ]
    cast.save(proj, data["characters"], data["universes"], locations=locs)
    meta, _ = read_meta(proj.akashic_path.read_text(encoding="utf-8"))
    assert [l.name for l in meta.locations] == ["Atlantis", "Chair room"]
    assert meta.locations[1].parent == "Atlantis" and meta.locations[1].images == ["https://img/x.png"]
    # Salvar só personagens (locations=None) mantém os locais.
    cast.save(proj, data["characters"], data["universes"])
    assert len(cast.load(proj)[1].locations) == 2

    bad = locs + [{"name": "Atlantis"}, {"name": "Lab", "parent": "Nowhere"}, {"name": "X", "universe": "narnia"}]
    try:
        cast.save(proj, data["characters"], data["universes"], locations=bad)
    except ValueError as e:
        msg = str(e)
        assert "Local repetido: Atlantis" in msg and "Nowhere" in msg and "narnia" in msg
    else:
        raise AssertionError("devia recusar")


WIKITEXT = """{{Location
|image=[[File:Chair.png|250px]]
|name=Chair room
|location=[[Atlantis]]
|status=Active
|appearances=many
}}
The '''chair room''' is a room at the top of the [[Atlantis control tower|eastern tower]] in the [[Inner City]].<ref>ep</ref>

==Overview==
[[File:Chair room 2.png|thumb|The chair]]
The [[control chair]] controls the [[drone weapon]]s.

==Areas==
*[[East Pier]]
*[[Atlantis armory|Armory]]
**[[Plant Room]]

==Appearances==
*Rising
"""


def test_limpeza_do_wikitexto_e_sublocais():
    text = W.clean_wikitext(WIKITEXT)
    assert "location: Atlantis" in text and "status: Active" in text
    assert "top of the eastern tower in the Inner City" in text
    assert "controls the drone weapons" in text
    assert "File:" not in text and "<ref>" not in text and "Rising" not in text and "'''" not in text
    assert W.sublocations(WIKITEXT) == ["East Pier", "Atlantis armory", "Plant Room"]


def test_ranking_da_busca_prefere_nome_exato_e_ignora_palavras_genericas():
    assert W.score_title("Atlantis gate room", "Embarkation room") == 0
    assert W.score_title("Atlantis gate room", "Atlantis control tower") == 1

    def fake_api(domain, **params):
        return {"query": {"search": [{"title": "Atlantis (Before I Sleep)"}, {"title": "Battle of Atlantis"},
                                     {"title": "Atlantis"}, {"title": "Area 51 chair room"}]}}

    with patch.object(W, "_api", fake_api):
        best, others = W.search_location("Atlantis", "stargate")
    assert best == "Atlantis" and "Atlantis (Before I Sleep)" in others


def test_ficha_do_local_usa_a_pagina_exata_e_a_epoca(tmp_path):
    proj = _project(tmp_path)
    page = {"title": "Chair room", "wikitext": WIKITEXT, "url": "https://stargate.fandom.com/wiki/Chair_room",
            "images": ["https://img/a.png"]}
    seen = {}

    def fake_generate(**kw):
        seen.update(kw)
        return "**Chair room**: Top of the east tower. Never: Wraith."

    with patch.object(W, "fetch_page", lambda title, domain: page if title == "Chair room" else None), \
            patch.object(W, "search_location", lambda name, domain: ("Chair room", ["Destiny chair room"])), \
            patch.object(W, "generate_text", fake_generate):
        out = W.fetch_location_sheet("Chair room", "Stargate Atlantis", proj.project_dir, era="Empty city, 1904.")
    assert out["found"] and out["sheet"] == "Top of the east tower. Never: Wraith."
    assert out["sublocations"] == ["East Pier", "Atlantis armory", "Plant Room"]
    assert out["images"] == ["https://img/a.png"] and out["candidates"] == ["Destiny chair room"]
    assert "Empty city, 1904." in seen["user_prompt"] and seen["system_prompt"] == W.SYSTEM_LOCATION_SHEET


def test_premissa_e_prompt_de_cena_levam_os_locais(tmp_path):
    form = P.PremiseForm(title="X", locations=["Chair room"], scenes=[{"text": "He sits.", "words": 400},
                                                                    {"text": "He stands.", "words": 400}])
    text = P.to_text(form, 2)
    assert "Locations in scene: Chair room" in text and P.from_text(text).locations == ["Chair room"]
    g = P.chapter_guidance(form, {}, first_scene=True,
                           places={"Chair room": "Top of the east tower.", "Atlantis": "City. Never: Wraith."},
                           always=["Atlantis"])
    assert "Atlantis: City. Never: Wraith." in g and "Chair room: Top of the east tower." in g
    assert "never add what they say is not there" in g

    proj = _project(tmp_path)
    data = cast.overview(proj)
    cast.save(proj, data["characters"], data["universes"], locations=[
        {"name": "Atlantis", "universe": "sga", "model_sheet": "City. Never: Wraith.", "always": True},
        {"name": "Chair room", "universe": "sga", "parent": "Atlantis", "model_sheet": "Top of the east tower."},
        {"name": "Jumper bay", "universe": "sga", "model_sheet": "Upper floor of the tower."},
    ])
    seen = []

    def stub(model, system_prompt, user_prompt, **kw):
        if system_prompt == prompts.SYSTEM_DRAFTING:
            seen.append(user_prompt)
            return " ".join(f"W{len(seen)}x{i}." for i in range(300))
        if system_prompt == prompts.SYSTEM_UPDATING:
            return "=== DYNAMIC MEMORY ===\nm\n=== CHARACTER ROSTER ===\nr\n=== OPEN THREADS ===\nt"
        return "OK." if system_prompt == prompts.SYSTEM_CONSISTENCY else "text"

    with patch.object(orch_mod, "generate_text", stub):
        assert PipelineOrchestrator(project=proj).run_single(text, 1).status == "done"
    assert seen and all("Chair room: Top of the east tower." in p and "Atlantis: City. Never: Wraith." in p for p in seen)
    assert all("Jumper bay" not in p for p in seen)


def test_api_importa_local_com_pagina_escolhida(tmp_path):
    from fastapi.testclient import TestClient
    import webapp.server as srv

    root = tmp_path / "projetos"
    root.mkdir()
    proj = _project(root)
    calls = []

    def fake_fetch(name, universe, project_dir, era="", exact_title=None, cancel_event=None):
        calls.append((name, exact_title, era))
        return {"kind": "location", "name": name, "page_title": exact_title or name, "sheet": f"Sheet {name}.",
                "url": "u", "found": True, "error": "", "candidates": [], "sublocations": ["East Pier"], "images": []}

    with patch.object(config, "PROJECTS_DIR", root), patch.object(config, "DATA_DIR", tmp_path), \
            patch.object(srv, "check_ollama_health", lambda **kw: True), \
            patch.object(W, "fetch_location_sheet", fake_fetch):
        c = TestClient(srv.create_app())
        jobs = c.app.state.jobs
        slug = proj.project_dir.name
        q, _ = jobs.subscribe()
        r = c.post(f"/api/projects/{slug}/wiki/import",
                   json={"kind": "location", "names": ["Gate room"], "universe": "Stargate Atlantis", "exact_title": "Gate Room"})
        assert r.status_code == 200, r.text
        assert jobs.wait(10)
        results = [e for e in iter(lambda: q.get_nowait() if not q.empty() else None, None) if e["type"] == "wiki_result"]
        assert results[0]["kind"] == "location" and results[0]["result"]["sublocations"] == ["East Pier"]
        # A época vem da ficha do universo (aqui, importada da seção 9.5 do texto na primeira leitura).
        assert calls == [("Gate room", "Gate Room", "City.")]
        assert c.post(f"/api/projects/{slug}/wiki/import",
                      json={"kind": "planet", "names": ["X"], "universe": "Stargate Atlantis"}).status_code == 400
        form = c.get(f"/api/projects/{slug}/chapters/1/premise-form")
        assert form.status_code == 404  # capítulo não existe ainda
