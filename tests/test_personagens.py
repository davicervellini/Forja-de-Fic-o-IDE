"""
Testes da gestão de personagens e universos (pipeline/cast.py e a API /cast).

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

from unittest.mock import patch

from pipeline import cast, config
from pipeline.akashic_schema import AkashicMeta, Character, Universe, read_meta, write_meta
from pipeline.project import StoryProject

BODY = """# Bíblia do Mundo: Teste

## 1. Story summary (EN)

A man builds a hub.

## 5. Personagens

### 5.8 Cast (EN)

Short sheets.

- **Arthur Galhardo.** Old sheet.

## 9. Universos

### 9.5 Universes (EN)

Closed list.

- **Stargate Atlantis.** Allowed: Rodney McKay. The city.
"""
from tests.isolamento import local_only


def _project(tmp_path, with_meta=True) -> StoryProject:
    proj = StoryProject.create(tmp_path, "teste")
    text = BODY
    if with_meta:
        meta = AkashicMeta(
            title="Teste",
            universes=[Universe(id="stargate_atlantis", name="Stargate Atlantis", role="base")],
            characters=[Character(name="Arthur Galhardo", role="protagonist")],
        )
        text = write_meta(BODY, meta)
    proj.akashic_path.write_text(text, encoding="utf-8")
    return proj


def test_primeira_leitura_importa_as_fichas_do_texto(tmp_path):
    proj = _project(tmp_path)
    fmt, meta = cast.load(proj)
    assert fmt == "v2"
    assert meta.characters[0].sheet == "Old sheet."
    assert meta.universes[0].allowed_characters == ["Rodney McKay"]


def test_salvar_gera_as_listas_e_o_registro_do_modelo(tmp_path):
    proj = _project(tmp_path)
    data = cast.overview(proj)
    chars = data["characters"]
    chars[0]["sheet"] = "Deadpan IT guy, 19-year-old body."
    chars.append({"name": "Veronica", "role": "supporting", "sheet": "A fairy hologram.", "universe": "stargate_atlantis"})
    unis = data["universes"]
    unis.append({"name": "Halo, alternate universe", "role": "base", "model_sheet": "The Ark.", "active": True})
    cast.save(proj, chars, unis)

    text = proj.akashic_path.read_text(encoding="utf-8")
    meta, body = read_meta(text)
    assert [c.name for c in meta.characters] == ["Arthur Galhardo", "Veronica"]
    assert meta.universes[1].id == "halo_alternate_universe"
    assert "- **Arthur Galhardo.** Deadpan IT guy, 19-year-old body." in body
    assert "- **Veronica.** A fairy hologram." in body
    assert "- **Halo, alternate universe.** The Ark." in body
    assert "Old sheet." not in body
    model = proj.akashic_model_path.read_text(encoding="utf-8")
    assert "A fairy hologram." in model
    # A versão anterior do registro inteiro fica guardada.
    assert "Old sheet." in (proj.project_dir / cast.BACKUP_NAME).read_text(encoding="utf-8")


def test_universo_de_reserva_sai_da_lista_do_modelo(tmp_path):
    proj = _project(tmp_path)
    data = cast.overview(proj)
    data["universes"].append({"name": "Star Wars", "role": "source", "model_sheet": "A galaxy.", "active": False})
    cast.save(proj, data["characters"], data["universes"])
    _, body = read_meta(proj.akashic_path.read_text(encoding="utf-8"))
    assert "Star Wars" not in body
    assert any(u.name == "Star Wars" for u in cast.load(proj)[1].universes)


def test_validacao_recusa_nome_vazio_repetido_e_universo_inexistente(tmp_path):
    proj = _project(tmp_path)
    data = cast.overview(proj)
    bad = data["characters"] + [{"name": "Arthur Galhardo"}, {"name": " "},
                                {"name": "Zed", "universe": "narnia"}]
    try:
        cast.save(proj, bad, data["universes"])
    except ValueError as e:
        msg = str(e)
        assert "precisa de nome" in msg and "repetido" in msg and "narnia" in msg
    else:
        raise AssertionError("devia recusar")


def test_registro_v1_pede_conversao(tmp_path):
    proj = _project(tmp_path, with_meta=False)
    assert cast.load(proj)[0] == "v1"
    try:
        cast.save(proj, [], [])
    except ValueError as e:
        assert "v2" in str(e)
    else:
        raise AssertionError("devia recusar")
    cast.migrate(proj)
    assert cast.load(proj)[0] == "v2"


def test_roster_e_aparicoes(tmp_path):
    proj = _project(tmp_path)
    proj.character_roster = (
        "ARTHUR GALHARDO\n* Name: Arthur Galhardo\n* Status: alive\n\n"
        "RODNEY MCKAY\n* Name: Rodney McKay\n* Status: alive\n* Note: scientist"
    )
    proj.save_state()
    for n, text in ((1, "Arthur woke up."), (2, "Rodney argued. Arthur Galhardo sighed.")):
        d = proj.chapter_dir(n)
        d.mkdir(parents=True)
        (d / "capitulo_final.md").write_text(text, encoding="utf-8")
    entries = cast.roster_entries(proj.character_roster)
    assert [e["name"] for e in entries] == ["Arthur Galhardo", "Rodney McKay"]
    assert entries[0]["text"].endswith("* Status: alive") and "RODNEY" not in entries[0]["text"]
    data = cast.overview(StoryProject.load(proj.project_dir))
    assert [r["name"] for r in data["roster_missing"]] == ["Rodney McKay"]
    assert data["appearances"]["Arthur Galhardo"] == [1, 2]


def test_api_de_personagens(tmp_path):
    from fastapi.testclient import TestClient
    from webapp.server import create_app

    root = tmp_path / "projetos"
    root.mkdir()
    proj = _project(root)
    with patch.object(config, "PROJECTS_DIR", root), patch.object(config, "DATA_DIR", tmp_path):
        c = TestClient(create_app())
        slug = proj.project_dir.name
        data = c.get(f"/api/projects/{slug}/cast").json()
        assert data["format"] == "v2" and data["characters"][0]["name"] == "Arthur Galhardo"
        data["characters"][0]["sheet"] = "New sheet."
        r = c.put(f"/api/projects/{slug}/cast", json={"characters": data["characters"], "universes": data["universes"]})
        assert r.status_code == 200, r.text
        assert r.json()["characters"][0]["sheet"] == "New sheet."
        r = c.put(f"/api/projects/{slug}/cast", json={"characters": [{"name": ""}], "universes": []})
        assert r.status_code == 400 and "nome" in r.json()["detail"]


def test_importacao_da_wiki_gera_resultados_sem_gravar(tmp_path):
    from fastapi.testclient import TestClient
    import pipeline.wiki_fetcher as wf
    import webapp.server as srv

    root = tmp_path / "projetos"
    root.mkdir()
    proj = _project(root)
    data = cast.overview(proj)
    data["universes"][0]["wiki"] = "stargate"
    cast.save(proj, data["characters"], data["universes"])
    before = proj.akashic_path.read_text(encoding="utf-8")

    def fake_fetch(name, universe, project_dir, cancel_event=None):
        if name == "Nobody":
            return {"name": name, "page_title": "", "sheet": "", "url": "", "found": False, "error": "Não encontrado."}
        return {"name": name, "page_title": f"{name} (page)", "sheet": f"Sheet of {name}.",
                "url": "https://stargate.fandom.com/wiki/X", "found": True, "error": ""}

    with local_only(), patch.object(config, "PROJECTS_DIR", root), patch.object(config, "DATA_DIR", tmp_path), \
            patch.object(srv, "check_ollama_health", lambda **kw: True), \
            patch.object(wf, "fetch_character_sheet", fake_fetch):
        c = TestClient(srv.create_app())
        jobs = c.app.state.jobs
        slug = proj.project_dir.name
        r = c.post(f"/api/projects/{slug}/wiki/import", json={"names": ["DC"], "universe": "Narnia"})
        assert r.status_code == 400 and "wiki" in r.json()["detail"]
        q, _ = jobs.subscribe()
        r = c.post(f"/api/projects/{slug}/wiki/import",
                   json={"names": ["Rodney McKay", "Nobody", "Rodney McKay"], "universe": "Stargate Atlantis"})
        assert r.status_code == 200, r.text
        assert jobs.wait(10)
        results = []
        while not q.empty():
            e = q.get_nowait()
            if e["type"] == "wiki_result":
                results.append(e["result"])
        assert [x["name"] for x in results] == ["Rodney McKay", "Nobody"]
        assert results[0]["sheet"] == "Sheet of Rodney McKay." and results[1]["found"] is False
    # A importação só sugere: o registro não muda até o usuário confirmar.
    assert proj.akashic_path.read_text(encoding="utf-8") == before


def test_ficha_da_wiki_sai_limpa():
    from pipeline.wiki_fetcher import clean_sheet
    assert clean_sheet("**Rodney McKay**: A brilliant astrophysicist.") == "A brilliant astrophysicist."
    assert clean_sheet("# Rodney McKay\n\nA brilliant astrophysicist.") == "A brilliant astrophysicist."
    assert clean_sheet('"A brilliant astrophysicist."') == "A brilliant astrophysicist."


def test_busca_da_wiki_so_aceita_titulo_parecido():
    from pipeline.wiki_fetcher import title_matches
    assert title_matches("Rodney McKay", "Meredith Rodney McKay")
    assert title_matches("teyla", "Teyla Emmagan")
    assert not title_matches("Pessoa Inexistente Xyz", "PX5-442")
