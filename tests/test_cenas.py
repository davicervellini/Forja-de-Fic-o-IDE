"""
Testes da geração cena por cena.

O Ollama é trocado por stubs. O de rascunho devolve o número de palavras pedido no
prompt ("about N words"), o que deixa conferir metas, continuações e montagem.
Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline import config
from pipeline import prompts as P
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.project import StoryProject
from pipeline.scenes import (
    assemble_chapter,
    clean_scene,
    count_words,
    drop_new_system_lines,
    parse_premise,
    split_chapter,
)

# Premissa do jeito que o usuário escreveu o Capítulo 2 (formato "Scenes:" + "Hook:").
PREMISE_CH2 = """Chapter Premise: Chapter 2: The Empty City

Goal: Show Arthur exploring Atlantis, absorbing his new reality and younger body, and testing the ATA gene.

Scenes:
1. The Dark Corridors. Arthur wanders through the pitch-black, silent city of Atlantis.
2. The Genetic Key. He touches a dormant wall panel. It instantly lights up.
3. The Chair. He finds the Lantean control chair and senses the failing power grid.

Hook: A system diagnostic in his mind showing the ocean is about to crush the shield.
"""

# Formato do resumo detalhado, com metas por cena e texto em várias linhas.
PREMISE_BLOCKS = """=== CHAPTER 1 BRIEF ===
First line of the output: Chapter 1: Tuesday

SCENE 1 (about 350 words) - Tuesday morning.
His phone alarm, the snooze button.
He crosses the street reading his phone.

SCENE 2 (about 900 words) - The white waiting room.
Plastic chairs, a ticket display.

SCENE 3 (about 600 words) - The handover.
The System arrives as a terminal.

TARGET: 2,000 to 2,500 words.
"""


# ── Divisão da premissa ───────────────────────────────────────

def test_premissa_com_lista_de_cenas_e_gancho():
    plan = parse_premise(PREMISE_CH2)
    assert plan.title == "Chapter 2: The Empty City"
    assert [s.num for s in plan.scenes] == [1, 2, 3]
    assert plan.scenes[2].text.startswith("The Chair.")
    assert plan.hook.startswith("A system diagnostic")


def test_premissa_em_blocos_com_meta_de_palavras():
    plan = parse_premise(PREMISE_BLOCKS)
    assert plan.title == "Chapter 1: Tuesday"
    assert [s.target_words for s in plan.scenes] == [350, 900, 600]
    assert "crosses the street" in plan.scenes[0].text
    assert "TARGET" not in plan.scenes[-1].text


def test_premissa_sem_cenas_numeradas_devolve_lista_vazia():
    assert parse_premise("Arthur explores Atlantis and finds the chair.").scenes == []
    # Uma cena só também não conta como divisão.
    assert parse_premise("Scenes:\n1. Only one scene.").scenes == []


def test_limpeza_tira_titulo_cabecalho_e_separador():
    raw = "Chapter 2: The Empty City\n\nScene 1\n\nArthur walked.\n\nHe stopped.\n\n* * *\n"
    assert clean_scene(raw) == "Arthur walked.\n\nHe stopped."


def test_montar_e_separar_o_capitulo():
    text = assemble_chapter("Chapter 2: X", ["Um.", "Dois.\n\nTrês."])
    assert text == "Chapter 2: X\n\nUm.\n\n* * *\n\nDois.\n\nTrês."
    assert split_chapter(text) == ("Chapter 2: X", ["Um.", "Dois.\n\nTrês."])


def test_polimento_nao_pode_inventar_linha_de_sistema():
    original = "He typed.\n\n[System] Bound to Arthur."
    edited = "He typed fast.\n\n[System] Bound to Arthur.\n\n[System] Power grid instability detected."
    assert drop_new_system_lines(edited, original) == "He typed fast.\n\n[System] Bound to Arthur."


# ── Fluxo do orquestrador ─────────────────────────────────────

_CALLS = [0]


def _words(n: int, tag: str) -> str:
    """n palavras únicas (o filtro de repetição apagaria uma palavra repetida n vezes)."""
    _CALLS[0] += 1
    return " ".join(f"{tag}x{_CALLS[0]}x{k}" for k in range(n))


class Recorder:
    """Stub do Ollama que guarda cada chamada."""

    def __init__(self, first_scene_short=False, refine_ratio=1.0, planner_reply=None, refine_extra=""):
        self.calls = []
        self.first_scene_short = first_scene_short
        self.refine_ratio = refine_ratio
        self.planner_reply = planner_reply
        self.refine_extra = refine_extra

    def __call__(self, model, system_prompt, user_prompt, **kw):
        phase = {
            P.SYSTEM_DRAFTING: "drafting", P.SYSTEM_REFINING: "refining",
            P.SYSTEM_SUMMARIZING: "summarizing", P.SYSTEM_UPDATING: "updating",
            P.SYSTEM_COMPRESS_MEMORY: "compress", P.SYSTEM_MERGING: "merging",
            P.SYSTEM_CONSISTENCY: "consistency", P.SYSTEM_SCENE_PLANNER: "planner",
        }[system_prompt]
        self.calls.append((phase, user_prompt, kw))
        if phase == "planner":
            return self.planner_reply or ""
        if phase == "drafting":
            scene = int(re.search(r"scene (\d+) of", user_prompt).group(1))
            m = re.search(r"about (\d+) (?:more )?words", user_prompt)
            target = int(m.group(1)) if m else 100
            drafts = [c for c in self.calls if c[0] == "drafting"]
            if self.first_scene_short and scene == 1 and len(drafts) == 1:
                return f"Chapter 9: Junk\n\n{_words(target // 5, 's1')}"
            return _words(target, f"s{scene}")
        if phase == "refining":
            draft = user_prompt.split("=== DRAFT ===\n", 1)[1].split("\n\n=== TASK ===", 1)[0]
            words = draft.split()
            kept = " ".join(words[: max(1, int(len(words) * self.refine_ratio))])
            return kept + self.refine_extra
        if phase == "updating":
            return "=== DYNAMIC MEMORY ===\nmem\n=== CHARACTER ROSTER ===\nroster\n=== OPEN THREADS ===\n[Ch.02] t"
        return {"summarizing": "summary", "compress": "c", "merging": "m", "consistency": "OK"}[phase]


def _project_with_ch1(tmp_path):
    proj = StoryProject.create(tmp_path, "t")
    ch1 = proj.chapter_dir(1)
    ch1.mkdir(parents=True, exist_ok=True)
    (ch1 / "capitulo_final.md").write_text(
        "Chapter 1: Tuesday\n\nStart.\n\n* * *\n\nHe stood in the gate room. The cursor blinked. END-OF-CH1",
        encoding="utf-8",
    )
    (ch1 / "resumo.md").write_text("summary 1", encoding="utf-8")
    proj.accumulated_summaries = [(1, "summary 1")]
    proj.last_chapter_num = 1
    return proj


def _run(proj, stub, premise=PREMISE_CH2, num=2):
    with patch.object(orch_mod, "generate_text", stub):
        return PipelineOrchestrator(project=proj).run_batch([premise], chapter_nums=[num])[-1]


def test_capitulo_e_escrito_cena_por_cena_com_a_meta_certa(tmp_path):
    proj = _project_with_ch1(tmp_path)
    stub = Recorder()
    result = _run(proj, stub)
    assert result.status == "done"
    drafts = [c for c in stub.calls if c[0] == "drafting"]
    assert len(drafts) == 3
    target = max(250, config.CHAPTER_TARGET_WORDS // 3)
    for i, (_, prompt, kw) in enumerate(drafts, start=1):
        assert f"scene {i} of 3" in prompt
        assert f"about {target} words" in prompt
        assert kw["extra_options"]["num_predict"] > target
    title, scenes = split_chapter(result.draft)
    assert title == "Chapter 2: The Empty City"
    assert [count_words(s) for s in scenes] == [target] * 3


def test_primeira_cena_ve_o_final_do_capitulo_anterior_e_as_outras_o_texto_ja_escrito(tmp_path):
    proj = _project_with_ch1(tmp_path)
    stub = Recorder()
    _run(proj, stub)
    drafts = [c[1] for c in stub.calls if c[0] == "drafting"]
    assert "END-OF-CH1" in drafts[0]
    assert "Start right after the PREVIOUS CHAPTER ENDING" in drafts[0]
    assert "THE CHAPTER SO FAR" in drafts[1] and "s1x" in drafts[1]
    assert "Continue directly from where THE CHAPTER SO FAR stops" in drafts[1]
    assert "end the chapter on this hook: A system diagnostic" in drafts[2]
    assert "Do not end the chapter" in drafts[0]


def test_cena_curta_ganha_continuacao_e_o_titulo_inventado_sai(tmp_path):
    proj = _project_with_ch1(tmp_path)
    stub = Recorder(first_scene_short=True)
    result = _run(proj, stub)
    drafts = [c[1] for c in stub.calls if c[0] == "drafting"]
    assert len(drafts) == 4
    assert "THIS SCENE SO FAR" in drafts[1] and "stopped too early" in drafts[1]
    _, scenes = split_chapter(result.draft)
    assert "Junk" not in result.draft
    assert count_words(scenes[0]) >= max(250, config.CHAPTER_TARGET_WORDS // 3) * config.SCENE_MIN_RATIO


def test_polimento_que_encolhe_a_cena_e_descartado(tmp_path):
    proj = _project_with_ch1(tmp_path)
    result = _run(proj, Recorder(refine_ratio=0.5))
    assert split_chapter(result.final)[1] == split_chapter(result.draft)[1]


def test_polimento_bom_e_mantido_sem_linha_de_sistema_inventada(tmp_path):
    proj = _project_with_ch1(tmp_path)
    result = _run(proj, Recorder(refine_ratio=0.95, refine_extra="\n\n[System] Power grid instability detected."))
    _, final_scenes = split_chapter(result.final)
    _, draft_scenes = split_chapter(result.draft)
    assert len(final_scenes) == 3
    assert "[System]" not in result.final
    assert all(count_words(f) < count_words(d) for f, d in zip(final_scenes, draft_scenes))


def test_premissa_sem_cenas_e_dividida_pelo_modelo(tmp_path):
    proj = _project_with_ch1(tmp_path)
    stub = Recorder(planner_reply="1. Wake. He wakes.\n2. Walk. He walks.\n3. Chair. He sits.\nHook: The shield flickers.")
    result = _run(proj, stub, premise="Chapter 2: Loose\n\nArthur explores Atlantis and finds the chair.")
    assert result.status == "done"
    assert [c[0] for c in stub.calls].count("planner") == 1
    assert len([c for c in stub.calls if c[0] == "drafting"]) == 3
    assert (proj.chapter_dir(2) / "cenas_planejadas.md").exists()
    assert "The shield flickers" in [c[1] for c in stub.calls if c[0] == "drafting"][-1]


def test_prefixo_do_prompt_e_igual_em_todas_as_cenas(tmp_path):
    # O Ollama reaproveita o cache quando o começo do prompt não muda.
    proj = _project_with_ch1(tmp_path)
    stub = Recorder()
    _run(proj, stub)
    drafts = [c[1] for c in stub.calls if c[0] == "drafting"]
    prefix = drafts[0].split("=== PREVIOUS CHAPTER ENDING")[0]
    assert all(d.startswith(prefix) for d in drafts)


# ── Limpeza de repetição e fronteira entre cenas ──────────────

def test_frase_que_repete_oito_palavras_sai_e_fala_fica():
    from pipeline.scenes import remove_repeated_sentences

    text = ("He felt a sense of wonder and trepidation mix together in his chest. The panel lit up.\n\n"
            "Arthur felt a sense of wonder and trepidation mix together in his chest as he looked. "
            "The map changed.\n\n"
            '"I felt a sense of wonder and trepidation mix together in my chest," he said.')
    out = remove_repeated_sentences(text, set())
    assert out.count("wonder and trepidation") == 2  # a primeira e a fala entre aspas
    assert "The panel lit up." in out and "The map changed." in out


def test_comeco_da_cena_que_repete_o_final_anterior_sai():
    from pipeline.scenes import drop_overlap

    previous = "The blue-green glow grew stronger, illuminating more and more of the silent city around him."
    scene = ("As Arthur stood there, the blue-green glow grew stronger, illuminating more and more of the city.\n\n"
             "A wall panel woke under his palm.")
    assert drop_overlap(scene, previous) == "A wall panel woke under his palm."


def test_frase_cortada_pelo_limite_de_tokens_sai():
    from pipeline.scenes import last_sentence, trim_incomplete_ending

    assert trim_incomplete_ending('He sat down. "Well," he said. The chair hummed and') == 'He sat down. "Well," he said.'
    assert trim_incomplete_ending("Complete sentence.") == "Complete sentence."
    assert last_sentence("First one. Second one.\n\nThird para. Last one here.") == "Last one here."


def test_prompt_da_cena_leva_proxima_cena_ultima_frase_e_voz(tmp_path):
    from pipeline.akashic_schema import AkashicMeta, Character, write_meta

    proj = _project_with_ch1(tmp_path)
    meta = AkashicMeta(title="t", characters=[Character(name="Arthur", role="protagonist", sheet="Dry humor, IT guy.")])
    proj.akashic_path.write_text(write_meta("# Bíblia do Mundo: t\n\n## 1. Story summary (EN)\n\nx\n", meta), encoding="utf-8")
    stub = Recorder()
    _run(proj, stub)
    drafts = [c for c in stub.calls if c[0] == "drafting"]
    first, second, last = drafts[0][1], drafts[1][1], drafts[2][1]
    assert "The NEXT scene, which is NOT yours to write, is: The Genetic Key." in first
    assert 'The last sentence already written is: "The cursor blinked."' not in first  # a última é END-OF-CH1
    assert "END-OF-CH1" in first.split("The last sentence already written is:")[1]
    assert "Dry humor, IT guy." in first and "Never use these worn phrases" in first
    assert "The NEXT scene" not in last
    assert drafts[0][2]["extra_options"]["repeat_last_n"] == config.DRAFTING_REPEAT_LAST_N
    assert "The last sentence already written is:" in second
