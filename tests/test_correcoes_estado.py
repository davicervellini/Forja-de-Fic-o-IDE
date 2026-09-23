"""
Testes das correções de perda de dados e de estado do pipeline.

O Ollama é trocado por um stub: cada fase devolve um texto que carrega o número da
premissa ("PREMISE-4" vira "draft 4", "final 4", "summary 4"), o que permite conferir
em que pasta cada capítulo foi parar.

Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline.api import OllamaError
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.project import SNAPSHOT_FILENAME, TRASH_DIRNAME, StoryProject
from pipeline import prompts as P


def _stub(fail_on: set[str] | None = None, consistency_reply: str = "OK."):
    """Gerador falso. `fail_on` lista as fases que devem falhar com OllamaError."""
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
        # Cada fase acha o número do capítulo no texto que ela recebe da fase anterior.
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
            "consistency": consistency_reply,
            # Sem lista numerada: a premissa vira uma cena só (o fluxo de cenas tem testes próprios).
            "planner": "no scenes",
        }[phase]

    return gen


def _project(tmp_path: Path) -> StoryProject:
    return StoryProject.create(tmp_path, "teste")


def _run(proj, nums, **stub_kwargs):
    with patch.object(orch_mod, "generate_text", _stub(**stub_kwargs)):
        orch = PipelineOrchestrator(project=proj)
        return orch.run_batch([f"PREMISE-{n}" for n in nums], chapter_nums=nums)


def _read(proj, n, name):
    path = proj.chapter_dir(n) / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def _final(proj, n):
    """Corpo do capítulo final sem a linha de título que o fluxo de cenas acrescenta."""
    text = _read(proj, n, "capitulo_final.md")
    return None if text is None else text.split(chr(10) * 2, 1)[-1]


# ── 1. Numeração da fila ──────────────────────────────────────

def test_fila_usa_o_numero_real_e_nao_sobrescreve_capitulo_pronto(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 3])
    assert _final(proj, 3) == "final 3"

    # Pendentes 2 e 4 com o 3 pronto no meio: antes, o 4 ia para capitulo_03.
    _run(proj, [2, 4])
    assert _final(proj, 2) == "final 2"
    assert _final(proj, 3) == "final 3"
    assert _final(proj, 4) == "final 4"
    assert [n for n, _ in proj.accumulated_summaries] == [1, 2, 3, 4]


def test_fila_com_buraco_na_numeracao(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 4])
    assert _final(proj, 3) is None
    assert _final(proj, 4) == "final 4"


# ── 2. Fusão de resumos ───────────────────────────────────────

def test_fusao_que_falha_nao_perde_resumos(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 3, 4])
    assert len(proj.accumulated_summaries) == 4
    results = _run(proj, [5], fail_on={"merging"})
    assert results[-1].status == "error"
    assert [n for n, _ in proj.accumulated_summaries] == [1, 2, 3, 4]
    assert proj.story_so_far == ""


def test_fusao_que_da_certo_descarta_so_os_antigos(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 3, 4, 5])
    assert [n for n, _ in proj.accumulated_summaries] == [2, 3, 4, 5]
    assert proj.story_so_far == "merged story"


# ── 3. Rollback e resumo duplicado ────────────────────────────

def test_falha_depois_do_resumo_volta_o_estado_e_nao_marca_concluido(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1])
    before = proj.snapshot_state()
    results = _run(proj, [2], fail_on={"updating"})
    assert results[-1].status == "error"
    assert proj.snapshot_state() == before
    assert _read(proj, 2, "resumo.md") is None
    status = {e.num: e.status for e in proj.scan_chapters()}
    assert status[2] != "done"


def test_refazer_capitulo_nao_duplica_o_resumo(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1])
    _run(proj, [2], fail_on={"updating"})
    _run(proj, [2])
    assert [n for n, _ in proj.accumulated_summaries] == [1, 2]


def test_checagem_de_consistencia_que_falha_nao_derruba_o_capitulo(tmp_path):
    proj = _project(tmp_path)
    results = _run(proj, [1], fail_on={"consistency"})
    assert results[-1].status == "done"
    assert "não rodou" in (_read(proj, 1, "consistencia.md") or "")


def test_consistencia_ok_com_ponto_e_salva_no_arquivo(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1], consistency_reply="OK.")
    assert _read(proj, 1, "consistencia.md") == "OK."


def test_checagem_roda_antes_da_memoria_mudar(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1])
    seen = {}
    base = _stub()

    def spy(model, system_prompt, user_prompt, **kw):
        if system_prompt == P.SYSTEM_CONSISTENCY:
            seen["prompt"] = user_prompt
        return base(model, system_prompt, user_prompt, **kw)

    with patch.object(orch_mod, "generate_text", spy):
        PipelineOrchestrator(project=proj).run_batch(["PREMISE-2"], chapter_nums=[2])
    assert "mem after 1" in seen["prompt"]
    assert "mem after 2" not in seen["prompt"]


# ── 4. Exclusão de capítulo ───────────────────────────────────

def test_excluir_o_ultimo_capitulo_volta_a_memoria_de_antes_dele(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1])
    after_1 = proj.snapshot_state()
    _run(proj, [2])
    assert (proj.chapter_dir(2) / SNAPSHOT_FILENAME).exists()

    assert proj.dynamic_memory == "mem after 2"

    msg = proj.delete_chapter(2)
    assert "voltou ao estado de antes" in msg
    assert proj.snapshot_state() == after_1
    assert proj.dynamic_memory == "mem after 1"
    assert not proj.chapter_dir(2).exists()
    assert any(d.name.startswith("capitulo_02_") for d in (proj.project_dir / TRASH_DIRNAME).iterdir())

    saved = json.loads(proj.state_path.read_text(encoding="utf-8"))
    assert saved["last_chapter_num"] == 1
    assert [n for n, _ in saved["accumulated_summaries"]] == [1]


def test_excluir_o_unico_capitulo_sem_snapshot_zera_a_memoria(tmp_path):
    # Capítulos gerados antes desta correção não têm snapshot.
    proj = _project(tmp_path)
    _run(proj, [1])
    (proj.chapter_dir(1) / SNAPSHOT_FILENAME).unlink()
    msg = proj.delete_chapter(1)
    assert "zerada" in msg
    assert proj.snapshot_state() == StoryProject(tmp_path / "vazio").snapshot_state()


def test_excluir_capitulo_do_meio_tira_so_o_resumo(tmp_path):
    proj = _project(tmp_path)
    _run(proj, [1, 2, 3])
    memory = proj.dynamic_memory
    assert memory == "mem after 3"
    msg = proj.delete_chapter(2)
    assert "ainda têm fatos dele" in msg
    assert [n for n, _ in proj.accumulated_summaries] == [1, 3]
    assert proj.dynamic_memory == memory
    assert proj.last_chapter_num == 3


# ── 5. Importação da Wiki ─────────────────────────────────────

def test_importacao_da_wiki_fica_no_estado_ao_reabrir(tmp_path):
    from gui import WikiImportDialog

    proj = _project(tmp_path)
    proj.dynamic_memory_path.write_text("- Ned Leeds: sheet", encoding="utf-8")
    WikiImportDialog._persist_memory(SimpleNamespace(project=proj))  # type: ignore[arg-type]
    reopened = StoryProject.load(proj.project_dir)
    assert reopened.dynamic_memory == "- Ned Leeds: sheet"


# ── 6. Prompts ────────────────────────────────────────────────

def test_prompts_de_sistema_sem_barra_invertida_solta():
    for name in dir(P):
        if name.startswith("SYSTEM_"):
            assert "\\" not in getattr(P, name), name


def test_capitulos_em_ordem_numerica(tmp_path):
    """capitulo_100 vem depois de capitulo_99, não logo depois de capitulo_10."""
    from pipeline.project import StoryProject
    proj = StoryProject.create(tmp_path, "p")
    for n in (1, 2, 10, 11, 99, 100, 101):
        proj.chapter_dir(n).mkdir(parents=True, exist_ok=True)
        (proj.chapter_dir(n) / "premissa.md").write_text(f"P{n}", encoding="utf-8")
    assert [e.num for e in proj.scan_chapters()] == [1, 2, 10, 11, 99, 100, 101]


def test_saida_so_com_tokens_especiais_descarrega_e_tenta_de_novo():
    """O backend Vulkan às vezes devolve só <unused50>: o modelo é descarregado e a geração repete uma vez."""
    import json as _json
    from unittest.mock import patch as _patch
    import requests as _requests
    from pipeline import api, config as _config

    calls = {"gen": 0, "unload": 0}

    class Stream:
        status_code = 200

        def __init__(self, lines):
            self.lines = lines

        def iter_lines(self, decode_unicode=True):
            yield from self.lines

        def close(self):
            pass

    def fake_post(url, json=None, **kw):
        if json.get("keep_alive") == 0:
            calls["unload"] += 1
            return Stream([])
        calls["gen"] += 1
        if calls["gen"] == 1:
            return Stream([_json.dumps({"response": "<unused50>"}) for _ in range(40)])
        return Stream([_json.dumps({"response": "Real text here, finally.", "done": True})])

    seen = []
    with _patch.object(_requests, "post", fake_post), _patch.object(_config, "OLLAMA_GENERATE_URL", "http://x/api/generate"):
        out = api.generate_text("gemma4:12b", "s", "u", on_token=seen.append)
    assert out == "Real text here, finally."
    assert calls == {"gen": 2, "unload": 1}
    assert all("<unused" not in s for s in seen)
