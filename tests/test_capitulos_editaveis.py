"""
Testes de capítulos editáveis e refazíveis, da exportação e da API da interface web.

O Ollama é trocado por um stub: cada fase devolve um texto com o número da premissa
("PREMISE-7" vira "draft 7", "final 7", "summary 7", "mem after 7"). Refazer o capítulo 2
com a premissa "PREMISE-7" permite ver exatamente que texto e que memória ficaram.

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline import chapters as ch
from pipeline import config
from pipeline import prompts as P
from pipeline.api import OllamaError
from pipeline.export import export_project
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.project import StoryProject


def _stub(fail_on: set[str] | None = None):
    fail_on = fail_on or set()

    def gen(model, system_prompt, user_prompt, **kwargs):
        phase = {
            P.SYSTEM_DRAFTING: "drafting",
            P.SYSTEM_REFINING: "refining",
            P.SYSTEM_SUMMARIZING: "summarizing",
            P.SYSTEM_UPDATING: "updating",
            P.SYSTEM_COMPRESS_MEMORY: "compress",
            P.SYSTEM_MERGING: "merging",
            P.SYSTEM_CONSISTENCY: "consistency",
            P.SYSTEM_SCENE_PLANNER: "planner",
        }[system_prompt]
        if phase in fail_on:
            raise OllamaError(f"falha simulada em {phase}")
        marker = {"drafting": r"PREMISE-(\d+)", "refining": r"draft (\d+)",
                  "summarizing": r"final (\d+)", "updating": r"summary (\d+)"}.get(phase)
        m = re.search(marker, user_prompt) if marker else None
        n = m.group(1) if m else "?"
        return {
            "drafting": f"draft {n}",
            "refining": f"final {n}",
            "summarizing": f"summary {n}",
            "updating": (f"=== DYNAMIC MEMORY ===\nmem after {n}\n"
                         f"=== CHARACTER ROSTER ===\nroster after {n}\n"
                         f"=== OPEN THREADS ===\n[Ch.{int(n):02d}] thread {n}" if n != "?" else ""),
            "compress": "compressed",
            "merging": "merged story",
            "consistency": "OK.",
            "planner": "no scenes",
        }[phase]

    return gen


def _project(tmp_path: Path) -> StoryProject:
    return StoryProject.create(tmp_path, "teste")


def _orch(proj):
    return PipelineOrchestrator(project=proj)


def _run(proj, nums):
    with patch.object(orch_mod, "generate_text", _stub()):
        return _orch(proj).run_batch([f"PREMISE-{n}" for n in nums], chapter_nums=nums)


def _read(proj, n, name):
    path = proj.chapter_dir(n) / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def _summary_of(proj, n):
    return dict(proj.accumulated_summaries).get(n)


# ── Edição e versões ─────────────────────────────────────────

def test_editar_texto_final_guarda_versao_e_marca_memoria(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1])
    original = _read(proj, 1, "capitulo_final.md")

    msg = ch.save_final_text(proj, 1, "Chapter 1: Novo\n\nTexto editado.")
    assert "Atualizar memória" in msg
    assert "Texto editado." in _read(proj, 1, "capitulo_final.md")
    versions = ch.list_versions(proj, 1)
    assert len(versions) == 1 and versions[0]["reason"] == "edicao"
    assert ch.read_version(proj, 1, versions[0]["id"])["capitulo_final.md"] == original
    assert ch.read_info(proj, 1)["memory_stale"] is True
    # Salvar o mesmo texto de novo não cria versão.
    ch.save_final_text(proj, 1, "Chapter 1: Novo\n\nTexto editado.")
    assert len(ch.list_versions(proj, 1)) == 1


def test_restaurar_versao_devolve_o_texto_e_guarda_o_atual(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1])
    original = _read(proj, 1, "capitulo_final.md")
    ch.save_final_text(proj, 1, "editado")
    vid = ch.list_versions(proj, 1)[0]["id"]

    ch.restore_version(proj, 1, vid)
    assert _read(proj, 1, "capitulo_final.md") == original
    reasons = sorted(v["reason"] for v in ch.list_versions(proj, 1))
    assert reasons == ["edicao", "restaurar"]


def test_versao_com_id_fora_da_pasta_e_recusada(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1])
    for bad in ("../..", "..", "../capitulo_01"):
        try:
            ch.read_version(proj, 1, bad)
        except FileNotFoundError:
            continue
        raise AssertionError(f"id aceito: {bad}")


# ── Refazer ──────────────────────────────────────────────────

def test_refazer_o_ultimo_capitulo_refaz_a_memoria(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2])
    with patch.object(orch_mod, "generate_text", _stub()):
        result = _orch(proj).redo_chapter(2, "PREMISE-7")

    assert result.status == "done"
    assert "final 7" in _read(proj, 2, "capitulo_final.md")
    assert _read(proj, 2, "resumo.md") == "summary 7"
    assert _read(proj, 2, "premissa.md") == "PREMISE-7"
    assert _summary_of(proj, 2) == "summary 7" and _summary_of(proj, 1) == "summary 1"
    # A memória foi refeita a partir do estado de antes do capítulo 2, não empilhada.
    assert proj.dynamic_memory == "mem after 7"
    old = [v for v in ch.list_versions(proj, 2) if v["reason"] == "refazer"]
    assert len(old) == 1
    assert "final 2" in ch.read_version(proj, 2, old[0]["id"])["capitulo_final.md"]
    # O estado gravado no disco é o mesmo da memória.
    saved = StoryProject.load(proj.project_dir)
    assert saved.dynamic_memory == "mem after 7"


def test_refazer_capitulo_do_meio_nao_mexe_na_memoria(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 3])
    memory_before = proj.dynamic_memory
    with patch.object(orch_mod, "generate_text", _stub()):
        result = _orch(proj).redo_chapter(2, "PREMISE-7")

    assert result.status == "done"
    assert "final 7" in _read(proj, 2, "capitulo_final.md")
    assert _summary_of(proj, 2) == "summary 7"
    assert proj.dynamic_memory == memory_before == "mem after 3"
    assert "capítulos posteriores" in ch.read_info(proj, 2)["memory_note"]
    # O snapshot do capítulo 2 continua sendo o estado de antes dele.
    snap = json.loads(_read(proj, 2, "estado_anterior.json"))
    assert snap["dynamic_memory"] == "mem after 1"


def test_refazer_capitulo_do_meio_usa_a_memoria_de_antes_dele(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 3])
    seen = []
    base = _stub()

    def spy(model, system_prompt, user_prompt, **kw):
        if system_prompt == P.SYSTEM_DRAFTING:
            seen.append(user_prompt)
        return base(model, system_prompt, user_prompt, **kw)

    with patch.object(orch_mod, "generate_text", spy):
        _orch(proj).redo_chapter(2, "PREMISE-7")
    assert seen and all("mem after 3" not in p for p in seen)
    assert any("mem after 1" in p for p in seen)


def test_refazer_que_falha_devolve_textos_e_estado(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2])
    before_state = proj.snapshot_state()
    before_final = _read(proj, 2, "capitulo_final.md")
    with patch.object(orch_mod, "generate_text", _stub(fail_on={"refining"})):
        result = _orch(proj).redo_chapter(2, "PREMISE-7")

    assert result.status == "error"
    assert _read(proj, 2, "capitulo_final.md") == before_final
    assert _read(proj, 2, "resumo.md") == "summary 2"
    assert _read(proj, 2, "premissa.md") == "PREMISE-2"
    assert proj.snapshot_state() == before_state
    assert StoryProject.load(proj.project_dir).snapshot_state() == before_state
    assert {e.num: e.status for e in proj.scan_chapters()} == {1: "done", 2: "done"}
    # O que a tentativa escreveu fica guardado para comparação.
    reasons = {v["reason"] for v in ch.list_versions(proj, 2)}
    assert {"refazer", "parcial"} <= reasons


def test_refazer_capitulo_do_meio_que_falha_devolve_tudo(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 3])
    before_state = proj.snapshot_state()
    with patch.object(orch_mod, "generate_text", _stub(fail_on={"summarizing"})):
        result = _orch(proj).redo_chapter(2, "PREMISE-7")
    assert result.status == "error"
    assert "final 2" in _read(proj, 2, "capitulo_final.md")
    assert proj.snapshot_state() == before_state


# ── Atualizar memória depois de editar ───────────────────────

def test_atualizar_memoria_do_ultimo_capitulo_editado(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2])
    ch.save_final_text(proj, 2, "Chapter 2: X\n\nfinal 9 edited")
    with patch.object(orch_mod, "generate_text", _stub()):
        result = _orch(proj).refresh_memory(2)

    assert result.status == "done"
    assert _read(proj, 2, "resumo.md") == "summary 9"
    assert _summary_of(proj, 2) == "summary 9"
    assert proj.dynamic_memory == "mem after 9"
    assert ch.read_info(proj, 2)["memory_stale"] is False
    assert "final 9 edited" in _read(proj, 2, "capitulo_final.md")


def test_atualizar_memoria_do_capitulo_do_meio_so_troca_o_resumo(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 3])
    ch.save_final_text(proj, 2, "final 9 edited")
    with patch.object(orch_mod, "generate_text", _stub()):
        _orch(proj).refresh_memory(2)
    assert _summary_of(proj, 2) == "summary 9"
    assert proj.dynamic_memory == "mem after 3"
    assert ch.read_info(proj, 2)["memory_stale"] is False


# ── Exportação ───────────────────────────────────────────────

def _two_chapters(tmp_path) -> StoryProject:
    proj = _project(tmp_path)
    for n, body in ((1, "The *gate* opened.\n\n[System] Welcome.\n\n* * *\n\nHe laughed & left."),
                    (2, "Second <chapter> text.")):
        d = proj.chapter_dir(n)
        d.mkdir(parents=True)
        (d / "capitulo_final.md").write_text(f"Chapter {n}: Title {n}\n\n{body}", encoding="utf-8")
    return proj


def test_exportar_markdown_e_texto(tmp_path):
    proj = _two_chapters(tmp_path)
    path, count = export_project(proj, "md", tmp_path / "out" / "livro.md")
    md = path.read_text(encoding="utf-8")
    assert count == 2
    assert md.startswith("# teste")
    assert "## Chapter 1: Title 1" in md and "## Chapter 2: Title 2" in md
    txt, _ = export_project(proj, "txt", tmp_path / "out" / "livro.txt", first=2)
    body = txt.read_text(encoding="utf-8")
    assert "Title 2" in body and "Title 1" not in body


def test_exportar_epub_valido(tmp_path):
    proj = _two_chapters(tmp_path)
    path, count = export_project(proj, "epub", tmp_path / "livro.epub")
    assert count == 2
    with zipfile.ZipFile(path) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        names = z.namelist()
        assert {"META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml",
                "OEBPS/cap001.xhtml", "OEBPS/cap002.xhtml"} <= set(names)
        for name in names:
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(z.read(name))  # XML bem formado, com & e < escapados
        cap1 = z.read("OEBPS/cap001.xhtml").decode("utf-8")
        assert "<em>gate</em>" in cap1 and 'class="system"' in cap1 and "&amp;" in cap1


def test_exportar_sem_capitulos_da_erro_claro(tmp_path):
    proj = _project(tmp_path)
    try:
        export_project(proj, "md", tmp_path / "x.md")
    except ValueError as e:
        assert "Nenhum capítulo" in str(e)
    else:
        raise AssertionError("devia falhar")


# ── API da interface web ─────────────────────────────────────

def _client(tmp_path):
    from fastapi.testclient import TestClient
    from webapp.server import create_app
    return TestClient(create_app())


def _with_data_dir(tmp_path):
    return (patch.object(config, "PROJECTS_DIR", tmp_path / "projetos"),
            patch.object(config, "DATA_DIR", tmp_path))


def test_api_projeto_capitulo_edicao_e_exportacao(tmp_path):
    (tmp_path / "projetos").mkdir()
    p1, p2 = _with_data_dir(tmp_path)
    with p1, p2:
        c = _client(tmp_path)
        r = c.post("/api/projects", json={"name": "Minha História", "akashic_text": ""})
        assert r.status_code == 200, r.text
        slug = r.json()["slug"]
        assert [p["slug"] for p in c.get("/api/projects").json()] == [slug]

        num = c.post(f"/api/projects/{slug}/chapters", json={"premise": "PREMISE-1"}).json()["num"]
        assert num == 1
        proj = c.get(f"/api/projects/{slug}").json()
        assert proj["chapters"][0]["status"] == "pending"

        r = c.put(f"/api/projects/{slug}/chapters/1/final", json={"text": "Chapter 1: A\n\nTexto."})
        assert r.status_code == 200
        chap = c.get(f"/api/projects/{slug}/chapters/1").json()
        assert "Texto." in chap["final"] and chap["premise"] == "PREMISE-1"

        r = c.post(f"/api/projects/{slug}/export", json={"format": "md"})
        assert r.status_code == 200, r.text
        assert Path(r.json()["path"]).read_text(encoding="utf-8").startswith("# Minha História")
        r = c.get(f"/api/projects/{slug}/export/download", params={"format": "epub"})
        assert r.status_code == 200 and r.content[:2] == b"PK"

        # Nome de projeto vindo da URL não sai da pasta de projetos.
        assert c.get("/api/projects/..").status_code == 404
        assert c.get("/api/projects/%2E%2E").status_code == 404

        msg = c.delete(f"/api/projects/{slug}/chapters/1").json()["message"]
        assert "_lixeira" in msg
        assert c.get(f"/api/projects/{slug}").json()["chapters"] == []


def test_api_gera_capitulo_em_segundo_plano_e_bloqueia_edicao(tmp_path):
    import threading
    import webapp.server as srv

    (tmp_path / "projetos").mkdir()
    p1, p2 = _with_data_dir(tmp_path)
    release = threading.Event()
    base = _stub()

    def slow(model, system_prompt, user_prompt, **kw):
        if system_prompt == P.SYSTEM_DRAFTING:
            release.wait(5)
        return base(model, system_prompt, user_prompt, **kw)

    with p1, p2, patch.object(srv, "check_ollama_health", lambda **kw: True), \
            patch.object(orch_mod, "generate_text", slow):
        c = _client(tmp_path)
        jobs = c.app.state.jobs
        slug = c.post("/api/projects", json={"name": "Gen"}).json()["slug"]
        c.post(f"/api/projects/{slug}/chapters", json={"premise": "PREMISE-1"})
        q, _ = jobs.subscribe()

        r = c.post(f"/api/projects/{slug}/generate", json={})
        assert r.status_code == 200, r.text
        # Enquanto gera: edição do projeto e segunda geração são recusadas.
        assert c.put(f"/api/projects/{slug}/chapters/1/premise", json={"text": "x"}).status_code == 409
        assert c.post(f"/api/projects/{slug}/generate", json={}).status_code == 409
        release.set()
        assert jobs.wait(10)

        chap = c.get(f"/api/projects/{slug}").json()["chapters"][0]
        assert chap["status"] == "done"
        types = []
        while not q.empty():
            types.append(q.get_nowait()["type"])
        assert types[0] == "job_start" and types[-1] == "job_end"
        assert "chapter_complete" in types

        # Refazer pela API
        r = c.post(f"/api/projects/{slug}/chapters/1/redo", json={"premise": "PREMISE-5"})
        assert r.status_code == 200, r.text
        assert jobs.wait(10)
        chap = c.get(f"/api/projects/{slug}/chapters/1").json()
        assert "final 5" in chap["final"]
        assert any(v["reason"] == "refazer" for v in chap["versions"])


def test_api_sem_ollama_nao_comeca_geracao(tmp_path):
    import webapp.server as srv
    (tmp_path / "projetos").mkdir()
    p1, p2 = _with_data_dir(tmp_path)
    with p1, p2, patch.object(srv, "check_ollama_health", lambda **kw: False):
        c = _client(tmp_path)
        slug = c.post("/api/projects", json={"name": "Off"}).json()["slug"]
        c.post(f"/api/projects/{slug}/chapters", json={"premise": "PREMISE-1"})
        r = c.post(f"/api/projects/{slug}/generate", json={})
        assert r.status_code == 503 and "Ollama" in r.json()["detail"]


def test_interface_web_e_servida(tmp_path):
    c = _client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200 and "Forja de Ficção" in r.text
    assert c.get("/app.js").status_code == 200
