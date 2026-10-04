"""
Testes das melhorias de qualidade do pipeline (docs/pente_fino_capitulos.md): cânone recortado por
capítulo, conferência da premissa, polimento com travas, memória por mudanças e o limite de contexto.

O Ollama é trocado por stubs. Rodam com pytest.
"""

import json
import re
from unittest.mock import patch

import pipeline.orchestrator as orch_mod
from pipeline import api, canon, config, memory_ops, qa
from pipeline import prompts as P
from pipeline.akashic import build_registro_modelo, extract_style_block
from pipeline.akashic_schema import AkashicMeta, Character, Universe, write_meta
from pipeline.cast import cast_status, debut_of
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.project import StoryProject
from pipeline.scenes import (
    fix_capitalized_names,
    last_prose_sentence,
    lost_system_spans,
    refine_problems,
    revert_paragraphs,
    tail_words,
    trim_after_hook,
    trim_next_scene_leak,
    voice_samples,
)

RECORDS = """# Akashic Records

## 1. Story summary (EN)

Arthur wakes in Atlantis. Tinaia and Veronica are created in Chapter 5 and run the Ark.

Timing: the station opens in Chapter 6.

## 2. Inviolable rules

1. Nobody else is alive in Atlantis.

### 5.8 Cast (EN)

- **Arthur Galhardo.** Dry humor, IT guy.
- **Tinaia, the central AI.** Calm voice. Created in Chapter 5.
- **Roy Mustang.** Visitor from Chapter 208.

### 9.5 Universes (EN)

Closed list of universes.

- **Stargate Atlantis.** The city on Lantea.
- **Fullmetal Alchemist.** Alchemy.

## 10. Style guide (EN)

- Chapter: 2,000 to 2,500 words.
- Tone: light.

### 13.2 Official spellings (EN)

People and AIs: Arthur Galhardo; Arthur; Tinaia; Roy Mustang.
Places: Atlantis; the Heart; the Ark.

### 6.9 Arc card: Atlantis, Chapters 1-3 (EN)

- The ocean outside is Lantea's, never the Atlantic.

### 10.1 Voice sample (EN)

"Sole user," he said. "Very secure. Terrible bus factor."

## 14. What the models must not do (EN)

1. No new characters.
"""


def _meta() -> AkashicMeta:
    return AkashicMeta(
        title="T",
        universes=[Universe(id="sga", name="Stargate Atlantis", role="base"),
                   Universe(id="fma", name="Fullmetal Alchemist", role="source")],
        characters=[Character(name="Arthur Galhardo", role="protagonist", universe="sga", sheet="Dry humor, IT guy."),
                    Character(name="Tinaia", role="supporting", sheet="Calm voice. Created in Chapter 5.",
                              sheet_label="Tinaia, the central AI."),
                    Character(name="Roy Mustang", role="supporting", universe="fma",
                              sheet="Visitor from Chapter 208.")],
    )


# ── Cânone por capítulo ───────────────────────────────────────

def test_canone_do_capitulo_esconde_quem_ainda_nao_estreou():
    meta = _meta()
    c = canon.chapter_canon(RECORDS, meta, 2, ["Arthur Galhardo"], ["Arthur Galhardo"], ["Tinaia", "Roy Mustang"],
                            context="Arthur walks to the heart of the city.")
    assert "Arthur Galhardo.** Dry humor" in c.text
    assert "Tinaia" not in c.text and "Roy Mustang" not in c.text and "Mustang" not in c.text
    assert "Fullmetal" not in c.text and "Stargate Atlantis" in c.text
    # Grafia com maiúscula: "the heart of the city" não puxa "the Heart".
    assert "the Heart" not in c.text
    # Cartão de arco só na faixa; amostra de voz sai do bloco e vem à parte.
    assert "Lantea's, never the Atlantic" in c.text
    assert "Terrible bus factor" not in c.text and "Terrible bus factor" in c.voice_sample
    later = canon.chapter_canon(RECORDS, meta, 7, ["Arthur Galhardo", "Tinaia"], ["Arthur Galhardo", "Tinaia"],
                                ["Roy Mustang"], context="")
    assert "Tinaia, the central AI" in later.text and "never the Atlantic" not in later.text


def test_estreia_vem_do_campo_ou_da_ficha_e_capitulo_ruim_nao_apresenta_ninguem(tmp_path):
    meta = _meta()
    assert debut_of(meta.characters[1]) == 5 and debut_of(meta.characters[2]) == 208
    proj = StoryProject.create(tmp_path, "p")
    meta.characters.append(Character(name="Lelei la Lelena", role="supporting", sheet="A mage."))
    proj.akashic_path.write_text(write_meta("# T\n\n## 1. Story summary (EN)\n\nx\n", meta), encoding="utf-8")
    ch2 = proj.chapter_dir(2)
    ch2.mkdir(parents=True)
    # O capítulo 2 pôs Lelei em cena sem estar no elenco planejado: não conta como estreia.
    (ch2 / "premissa.md").write_text("Characters in scene: Arthur Galhardo\nMust not appear: Lelei\n", encoding="utf-8")
    (ch2 / "capitulo_final.md").write_text("Chapter 2\n\nLelei la Lelena waved at Arthur.", encoding="utf-8")
    (ch2 / "resumo.md").write_text("s", encoding="utf-8")
    introduced, hidden = cast_status(proj, 3)
    assert introduced == ["Arthur Galhardo"]
    assert set(hidden) == {"Tinaia", "Roy Mustang", "Lelei la Lelena"}
    assert "Tinaia" in cast_status(proj, 5)[0]


def test_registro_modelo_leva_secoes_em_ingles_extras_e_estilo_sem_repeticao(tmp_path):
    proj = StoryProject.create(tmp_path, "p")
    body = RECORDS.replace("# Akashic Records", "# Bíblia").replace("### 13.2", "## 13. Glossário\n\n### 13.2")
    proj.akashic_path.write_text(body + "\n## 6. Arcos\n\nNota interna.\n", encoding="utf-8")
    ok, _ = build_registro_modelo(proj.project_dir)
    model = proj.akashic_model_path.read_text(encoding="utf-8")
    assert ok and "Arc card: Atlantis" in model and "Voice sample" in model and "Nota interna" not in model
    style = extract_style_block(model)
    assert style.count("Official spellings") == 1


def test_glossario_do_registro():
    text = "Nome e grafia oficial: a Gerência. Em inglês, the Management.\n- senha = the ticket (queue number)\n"
    pairs = canon.parse_glossary(text)
    assert ("a Gerência", "the Management") in pairs and ("senha", "the ticket (queue number)") in pairs
    assert canon.glossary_block(pairs, "recebeu uma senha") == "- senha = the ticket (queue number)"
    assert qa.leak_terms(pairs, "en") == ["Gerência", "senha"] and qa.leak_terms(pairs, "pt-BR") == []


# ── Itens da premissa e conferência ───────────────────────────

def test_itens_obrigatorios_vao_para_a_cena_certa_e_nomes_proibidos_viram_trava():
    scenes = ["The gate room. He counts the stairs.", "The console shows a graph of the shield.",
              "The transporter. A closet-sized cabin with a map."]
    cl = qa.build_checklist("The stairs are off by two; the graph with the shield; the closet-sized cabin. "
                            "At most two [System] lines.",
                            "Tinaia, Veronica, fairies", scenes, ["Tinaia", "Veronica", "Arthur"], ["Roy Mustang"],
                            ["Arthur"])
    assert cl.scene_of[:3] == [1, 2, 3]
    assert cl.forbidden_names == ["Tinaia", "Veronica", "Roy Mustang"]
    assert cl.max_system_lines == 2
    assert cl.before_scene(3) == ["The stairs are off by two", "the graph with the shield"]


def test_conferencia_sem_modelo_acha_proibido_conversa_e_vazamento():
    cl = qa.Checklist(forbidden_names=["Tinaia"], max_system_lines=3)
    text = "Arthur walked.\n\nTinaia spoke from the wall.\n\nPlease provide the draft text for the scene."
    kinds = {i.kind for i in qa.scene_issues(text, "", cl, "en", ["senha"], None)}
    assert {"proibido", "conversa de assistente"} <= kinds
    leak = qa.scene_issues("He had received a senha and died.", "", qa.Checklist(), "en", ["senha"], None)
    assert any(i.kind == "idioma" for i in leak)
    over = qa.scene_issues("[System] A.\n\n[System] B.\n\n[System] C.", "", qa.Checklist(), "en", [], 1)
    assert any(i.kind == "[System]" and i.hard for i in over)


def test_juiz_so_vale_com_citacao_que_esta_no_texto():
    text = "Arthur sat. The chair hummed under him."
    raw = json.dumps({"missing": [2, 9], "violations": [
        {"rule": "forbidden", "quote": "The chair hummed"}, {"rule": "made up", "quote": "Tinaia waved"}]})
    missing, issues = qa.parse_judge(raw, text, 3)
    assert missing == [1] and [i.quote for i in issues] == ["The chair hummed"]
    assert qa.parse_pairwise('noise {"winner": "B", "reason": "x"}') == "B"


def test_revisao_troca_so_o_trecho_citado():
    scene = 'He found two fewer stairs. He climbed.\n\n"Fifty-nine steps instead of fifty-seven," he said.'
    raw = json.dumps({"edits": [
        {"kind": "CONTRADICTION", "quote": "He found two fewer stairs.", "replacement": "He found two extra stairs.",
         "reason": "contradicts the count"},
        {"kind": "REPEAT", "quote": "Not in the text at all.", "replacement": "", "reason": "x"}]})
    edits = qa.parse_edits(raw, scene)
    out, applied = qa.apply_edits(scene, edits)
    assert len(applied) == 1 and out.startswith("He found two extra stairs. He climbed.")
    assert "Fifty-nine steps" in out


def test_lista_do_polimento_aponta_frase_gasta_repeticao_e_aspas_abertas():
    earlier = '"Great," he muttered, and kept his hands deep in his pockets as he walked.'
    scene = ('His mind raced as the lights came on.\n\n'
             'He kept his hands deep in his pockets as he walked on.\n\n'
             '"Great," he muttered.\n\n"One, two, three...')
    targets = dict((why, q) for q, why in qa.polish_targets(scene, earlier))
    assert any("frase gasta" in w for w in targets)
    assert "repete uma cena anterior" in targets
    assert "aspas abertas sem fechar" in targets
    assert any("tique" in w for w in targets)
    # Motivo pedido pela premissa (mãos nos bolsos) só conta a partir da terceira vez.
    exempt = [w for _, w in qa.polish_targets(scene, earlier, exempt=["hands in his pockets"])]
    assert "repete uma cena anterior" not in exempt


# ── Polimento ─────────────────────────────────────────────────

DRAFT = ('He reached the heart of the city. The panel lit up.\n\n'
         'He typed:\n\n> status\n\n[System] Build progress: 0%.\n\n"Great," he said.')


def test_polimento_nao_vira_nome_proprio_por_maiuscula():
    polished = DRAFT.replace("heart of the city", "Heart of the city")
    fixed, names = fix_capitalized_names(polished, DRAFT, ["the Heart", "Atlantis"])
    assert fixed == DRAFT and names == ["the Heart"]
    start = "Heart beats fast. " + DRAFT
    assert fix_capitalized_names(start, "heart beats fast. " + DRAFT, ["the Heart"])[0].startswith("Heart beats")


def test_polimento_que_perde_linha_de_sistema_e_recusado_e_conserto_por_paragrafo():
    lost = DRAFT.replace("\n\n[System] Build progress: 0%.", "")
    assert lost_system_spans(lost, DRAFT) == ["[system] build progress: 0%"]
    assert any("perdeu" in p for p in refine_problems(lost, DRAFT))
    fixed, changed = revert_paragraphs(lost, DRAFT, lambda p: False)
    assert changed == 1 and "[System] Build progress: 0%." in fixed
    guest = DRAFT.replace('"Great," he said.', '"Great," he said. Tinaia smiled from the wall.')
    fixed, changed = revert_paragraphs(guest, DRAFT, lambda p: "Tinaia" in p)
    assert changed == 1 and "Tinaia" not in fixed


def test_nome_da_lista_de_proibidos_nao_conta_como_permitido():
    # O texto da premissa cita Tinaia (no "não pode aparecer"); o polimento que a traz é recusado.
    problems = refine_problems(DRAFT + " Tinaia waved.", DRAFT, premise="Arthur alone.",
                               names=["Tinaia"], max_ratio=2, forbidden=["Tinaia"])
    assert any("proíbe: Tinaia" in p for p in problems)


# ── Fronteiras de cena e texto de contexto ────────────────────

def test_fim_do_texto_mantem_paragrafos():
    text = "\n\n".join(f"Paragraph {i} has some words in it." for i in range(30))
    tail = tail_words(text, 20)
    assert "\n\n" in tail and tail.endswith("Paragraph 29 has some words in it.")
    assert tail.split("\n\n")[0].startswith(("…", "Paragraph"))


def test_ultima_frase_pula_linha_de_sistema():
    assert last_prose_sentence('He read the line:\n\n[System] Build progress: 0%.') == "He read the line:"
    assert last_prose_sentence('He sat. He read the line:\n\n[System] Build progress: 0%.') == "He sat."
    assert last_prose_sentence('He stopped. He read the line: "[System] Build progress: 0%."') == "He stopped."


def test_cena_que_invade_a_seguinte_e_cortada_e_coda_do_gancho_sai():
    base = "\n\n".join(f"Arthur counted step {i} in the cold dark stairwell slowly." for i in range(8))
    leak = base + "\n\nWalking meant hundreds of meters of corridors; he needed the transporter cabin near the infirmary."
    brief = "The transporter. Walking means hundreds of meters of corridors; he takes the transporter cabin near the infirmary."
    assert trim_next_scene_leak(leak, brief, 20) == base
    assert trim_next_scene_leak(leak, brief, 10_000) == leak  # não corta abaixo do mínimo
    hook = "The city floods into his head and a red light keeps screaming."
    ending = base + "\n\nThe city floods into his head, and a red light keeps screaming.\n\nToday he had learned a lot."
    assert trim_after_hook(ending, hook).endswith("keeps screaming.")


def test_falas_de_exemplo_do_protagonista():
    ch1 = ('"Sole user," Arthur said. "Very secure. Terrible bus factor."\n\n'
           '"Next item," the clerk said.\n\n'
           'Arthur stared. "You gave me a compiler, and now I have to go mine the silicon."')
    samples = voice_samples([ch1], "Arthur Galhardo")
    assert "Next item," not in samples
    assert any("compiler" in s for s in samples)


def test_roteiro_do_capitulo_mostra_so_o_nome_das_cenas_seguintes():
    out = P.chapter_outline("Reach the chair.", ["The stairs. He climbs.", "The console. A graph.", "The chair. He sits."], 1)
    assert "Scene 1 (THIS SCENE): The stairs. He climbs." in out
    assert "Scene 3: The chair. (later; not yet)" in out and "He sits" not in out
    assert "Reach the chair" not in out  # o objetivo final só aparece na última cena
    assert P.end_moment("The stairs. He climbs two flights. He stops at the door. (about 400 words)") == \
        "He stops at the door."


def test_resumo_sem_as_secoes_de_conferencia():
    raw = ("0. PLAN CHECK:\n- stairs: shown\n1. KEY EVENTS:\n- Arthur climbs.\n2. CHARACTER STATE:\n"
           "- Arthur at the door.\n6. OFF-PLAN:\n- Tinaia")
    summary, plan, off = P.strip_summary_checks(raw)
    assert "PLAN CHECK" not in summary and "OFF-PLAN" not in summary and "Arthur climbs" in summary
    assert "stairs: shown" in plan and "Tinaia" in off


# ── Memória por mudanças ──────────────────────────────────────

ROSTER = "**ARTHUR GALHARDO**\n- Name: Arthur Galhardo\n- Status: alive\n- Location: gate room"
THREADS = "[Ch.01] The Alteran database is somewhere in the city.\n[Ch.01] What the ATA gene does."


def test_memoria_por_mudancas_nao_perde_threads_nem_aceita_gente_proibida():
    raw = """THREAD RESOLVED: [Ch.01] What the ATA gene does. | EVIDENCE: Arthur learns the ATA gene wakes the panels
THREAD RESOLVED: [Ch.01] The Alteran database is somewhere in the city. | EVIDENCE: nothing like it
ROSTER NEW: Present Mic | alive | the chair room | singer | loud
ROSTER UPDATE: Arthur Galhardo | Location | the chair room
MEMORY ADD: The transporter cabins show a map of the city.
CALLBACK: ticket 847, the counter flipped to 848
END STATE: Arthur sits in the control chair as the red light screams."""
    summary = "1. KEY EVENTS:\n- Arthur learns the ATA gene wakes the panels of the city."
    res = memory_ops.apply_ops(memory_ops.parse_ops(raw), "- The city is underwater.", ROSTER, THREADS, "",
                               summary, 2, ["Arthur Galhardo"], ["Present Mic"])
    assert "What the ATA gene does" not in res.threads and "Alteran database" in res.threads
    assert "Present Mic" not in res.roster and "- Location: the chair room" in res.roster
    assert res.memory.splitlines()[0].startswith("- End of Ch.02: Arthur sits")
    assert "The city is underwater." in res.memory and "transporter cabins" in res.memory
    assert "ticket 847" in res.callbacks
    assert any("sem evidência" in r for r in res.rejected) and any("Present Mic" in r for r in res.rejected)
    report = memory_ops.diff_report(2, res)
    assert "## Recusadas" in report


def test_roster_do_prompt_so_com_quem_esta_no_capitulo():
    roster = ROSTER + "\n\n**TINAIA**\n- Name: Tinaia\n- Status: offline\n- Role: AI"
    out = memory_ops.roster_for_chapter(roster, ["Arthur Galhardo"])
    assert "- Location: gate room" in out and "- Tinaia (offline)" in out and "Role: AI" not in out


# ── Limite de contexto ────────────────────────────────────────

class _Resp:
    def __init__(self, status, body="", lines=()):
        self.status_code = status
        self.text = body
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        pass


def test_prompt_maior_que_o_contexto_aumenta_o_contexto_em_vez_de_cortar():
    calls = []
    done = json.dumps({"response": "ok", "done": True, "prompt_eval_count": 9000, "eval_count": 5})

    def post(url, json=None, stream=False, timeout=None):
        calls.append(json)
        if len(calls) == 1:
            return _Resp(400, '{"error":"request (9000 tokens) exceeds the available context size (4096 tokens)",'
                              '"type":"exceed_context_size_error","n_prompt_tokens":9000,"n_ctx":4096}')
        return _Resp(200, lines=[done])

    with patch.object(api.requests, "post", post):
        out = api.generate_text("m", "sys", "short prompt", num_ctx=4096, extra_options={"num_predict": 500})
    assert out == "ok"
    assert calls[0]["truncate"] is False and calls[0]["shift"] is False
    assert calls[1]["options"]["num_ctx"] >= 9000 + 500


def test_contexto_cheio_nao_vira_descarregar_e_tentar_de_novo():
    # Contexto cheio: o Ollama devolve um token especial e para (prompt + resposta = num_ctx).
    full = [json.dumps({"response": "<unused1>", "done": False}),
            json.dumps({"response": "", "done": True, "prompt_eval_count": 4095, "eval_count": 1})]
    calls = []

    def post(url, json=None, stream=False, timeout=None):
        calls.append(json)
        return _Resp(200, lines=full)

    with patch.object(api.requests, "post", post), patch.object(api, "unload_model", lambda m: calls.append("unload")):
        try:
            api.generate_text("m", "s", "p", num_ctx=4096, extra_options={"num_predict": 100})
        except api.OllamaError as e:
            assert "contexto" in str(e)
        else:
            raise AssertionError("devia falhar")
    assert "unload" not in calls


# ── Fluxo completo com as etapas novas ────────────────────────

PREMISE = """Chapter Premise: Chapter 2: The Chair

Goal: Arthur reaches the control chair.
Characters in scene: Arthur Galhardo

Scenes:
1. The stairs. He climbs the stairs of the gate room and counts them. (about 300 words)
2. The chair. He finds the control chair and sits in it. (about 300 words)

Hook: The chair lights up.
Must include: the stairs are counted; the chair lights up under him.
Must not appear: Tinaia
"""


class Stub:
    """Ollama falso que sabe as fases novas. `bad_first`: a primeira versão da cena 1 traz Tinaia."""

    def __init__(self, bad_first=False, update_reply=None, consistency_reply='{"violations": []}'):
        self.calls = []
        self.bad_first = bad_first
        self.update_reply = update_reply
        self.consistency_reply = consistency_reply
        self.n = 0

    def __call__(self, model, system_prompt, user_prompt, **kw):
        self.n += 1
        phase = {P.SYSTEM_DRAFTING: "drafting", P.SYSTEM_REFINING: "refining", P.SYSTEM_REVISE: "revise",
                 P.SYSTEM_SUMMARIZING: "summarizing", P.SYSTEM_UPDATING: "updating",
                 P.SYSTEM_COMPRESS_MEMORY: "compress", P.SYSTEM_MERGING: "merging",
                 P.SYSTEM_CONSISTENCY: "consistency", P.SYSTEM_QA_JUDGE: "judge",
                 P.SYSTEM_QA_PAIRWISE: "pairwise"}.get(system_prompt, "other")
        self.calls.append((phase, user_prompt, kw))
        words = lambda k: " ".join(f"w{self.n}x{i}." for i in range(k))
        if phase == "drafting":
            scene = int(re.search(r"scene (\d+) of", user_prompt).group(1))
            body = ("Arthur counted the stairs, one by one." if scene == 1 else "The chair lights up under him.")
            drafts = [c for c in self.calls if c[0] == "drafting"]
            if self.bad_first and scene == 1 and len(drafts) == 1:
                body += " Tinaia spoke from the wall."
            return f"{body} {words(310)}"
        if phase == "refining":
            return user_prompt.split("=== DRAFT ===\n", 1)[1].split("\n\n=== TASK ===", 1)[0]
        if phase == "revise":
            return '{"edits": []}'
        if phase == "judge":
            return '{"missing": [], "violations": []}'
        if phase == "pairwise":
            return '{"winner": "A"}'
        if phase == "summarizing":
            return "1. KEY EVENTS:\n- Arthur sits in the chair.\n2. CHARACTER STATE:\n- Arthur Galhardo: in the chair."
        if phase == "updating":
            return self.update_reply or "MEMORY ADD: The chair answers the ATA gene."
        if phase == "consistency":
            return self.consistency_reply
        return "ok"


def _project(tmp_path):
    proj = StoryProject.create(tmp_path, "q")
    meta = _meta()
    proj.akashic_path.write_text(write_meta(RECORDS.replace("# Akashic Records", "# T"), meta), encoding="utf-8")
    build_registro_modelo(proj.project_dir)
    proj.dynamic_memory = "- The city is underwater."
    proj.open_threads = THREADS
    return proj


def _quality(**over):
    values = dict(DRAFT_CANDIDATES=1, REFINE_CANDIDATES=1, QA_SCENE_RETRIES=2, QA_JUDGE_ENABLED=True,
                  REVISE_PASS_ENABLED=True, CONSISTENCY_CHECK_ENABLED=True, CHAPTER_TARGET_WORDS=600)
    values.update(over)
    return patch.multiple(config, **values)


def test_cena_com_personagem_proibido_e_reescrita_com_correcao(tmp_path):
    proj = _project(tmp_path)
    stub = Stub(bad_first=True)
    with _quality(), patch.object(orch_mod, "generate_text", stub):
        result = PipelineOrchestrator(project=proj).run_single(PREMISE, 2)
    assert result.status == "done", result.error
    drafts = [c for c in stub.calls if c[0] == "drafting"]
    assert len(drafts) == 3  # cena 1 duas vezes, cena 2 uma
    assert "Your previous version of this scene had problems" in drafts[1][1]
    assert "Only these characters appear: Arthur Galhardo" in drafts[1][1]
    assert "Tinaia" not in result.final
    # O cânone do capítulo não fala de quem ainda não estreou, e o checklist ficou salvo.
    assert "Tinaia" not in drafts[0][1].split("=== CHAPTER OUTLINE")[0]
    saved = json.loads((proj.chapter_dir(2) / "checklist.json").read_text(encoding="utf-8"))
    assert saved["scene_of"] == [1, 2] and saved["forbidden_names"] == ["Tinaia", "Roy Mustang"]


def test_versoes_da_cena_e_juiz_de_pares(tmp_path):
    proj = _project(tmp_path)
    stub = Stub()
    with _quality(DRAFT_CANDIDATES=2), patch.object(orch_mod, "generate_text", stub):
        result = PipelineOrchestrator(project=proj).run_single(PREMISE, 2)
    assert result.status == "done", result.error
    drafts = [c for c in stub.calls if c[0] == "drafting"]
    assert len(drafts) == 4
    assert "seed" in drafts[1][2]["extra_options"] and "seed" not in drafts[0][2]["extra_options"]
    assert len([c for c in stub.calls if c[0] == "pairwise"]) == 4  # duas ordens por cena


def test_capitulo_que_falha_na_conferencia_final_nao_entra_na_memoria(tmp_path):
    proj = _project(tmp_path)
    before = proj.snapshot_state()

    # O polimento põe em cena quem a premissa proíbe.
    stub = Stub()
    with _quality(QA_JUDGE_ENABLED=False, QA_SCENE_RETRIES=0, REVISE_PASS_ENABLED=False), \
            patch.object(orch_mod, "generate_text", stub), \
            patch.object(orch_mod.PipelineOrchestrator, "_run_refining",
                         lambda self, draft, n, cb: draft + "\n\nTinaia waved from the wall."):
        orch = PipelineOrchestrator(project=proj)
        results = orch.run_batch([PREMISE, PREMISE.replace("Chapter 2", "Chapter 3")], chapter_nums=[2, 3])
    assert [r.status for r in results] == ["qa_failed"]  # o lote para no capítulo com problema
    assert proj.snapshot_state()["dynamic_memory"] == before["dynamic_memory"]
    info = json.loads((proj.chapter_dir(2) / "info.json").read_text(encoding="utf-8"))
    assert info["memory_stale"] and info["qa_failed"]
    report = (proj.chapter_dir(2) / "consistencia.md").read_text(encoding="utf-8")
    assert "Problemas graves" in report and "Tinaia" in report
    # O usuário revisa e pede para atualizar a memória: agora entra.
    (proj.chapter_dir(2) / "capitulo_final.md").write_text(results[0].draft, encoding="utf-8")
    with _quality(QA_JUDGE_ENABLED=False), patch.object(orch_mod, "generate_text", Stub()):
        r = PipelineOrchestrator(project=proj).refresh_memory(2)
    assert r.status == "done" and "The chair answers the ATA gene." in proj.dynamic_memory


def test_memoria_atualizada_por_mudancas_no_fluxo(tmp_path):
    proj = _project(tmp_path)
    reply = ("THREAD NEW: Who built the chair?\n"
             "ROSTER NEW: Tinaia | online | the wall | AI | calm")
    stub = Stub(update_reply=reply)
    with _quality(QA_JUDGE_ENABLED=False, REVISE_PASS_ENABLED=False), patch.object(orch_mod, "generate_text", stub):
        result = PipelineOrchestrator(project=proj).run_single(PREMISE, 2)
    assert result.status == "done", result.error
    assert "Alteran database" in proj.open_threads and "[Ch.02] Who built the chair?" in proj.open_threads
    assert "Tinaia" not in proj.character_roster
    assert proj.dynamic_memory.startswith("- End of Ch.02: Arthur Galhardo: in the chair.")
    diff = (proj.chapter_dir(2) / "memoria_diff.md").read_text(encoding="utf-8")
    assert "Recusadas" in diff and "Tinaia" in diff
    updating = [c[1] for c in stub.calls if c[0] == "updating"][0]
    assert "ALLOWED CHARACTERS" in updating and "Tinaia" not in updating.split("=== ALLOWED CHARACTERS ===")[1].split("===")[0]


def test_premissa_em_portugues_e_traduzida_com_glossario_e_guardada(tmp_path):
    proj = _project(tmp_path)
    proj.akashic_path.write_text(proj.akashic_path.read_text(encoding="utf-8")
                                 + "\n## 13. Glossário\n\n- senha = the ticket (queue number)\n", encoding="utf-8")
    pt = ("Chapter Premise: Chapter 2: A cadeira\n\nGoal: Arthur chega até a cadeira de controle e lembra que da "
          "última vez que sentou numa cadeira oficial ele recebeu uma senha e morreu.\n"
          "Characters in scene: Arthur Galhardo\n\nScenes:\n1. A escada. Ele sobe a escada e conta os degraus. "
          "(about 300 words)\n2. A cadeira. Ele senta na cadeira e ela acende. (about 300 words)\n\n"
          "Hook: A cadeira acende.\nMust not appear: Tinaia\n")
    seen = []

    def gen(model, system_prompt, user_prompt, **kw):
        if "translate a chapter brief" in system_prompt:
            seen.append(user_prompt)
            return PREMISE
        return Stub()(model, system_prompt, user_prompt, **kw)

    with _quality(QA_JUDGE_ENABLED=False, REVISE_PASS_ENABLED=False), patch.object(orch_mod, "generate_text", gen):
        result = PipelineOrchestrator(project=proj).run_single(pt, 2)
    assert result.status == "done", result.error
    assert len(seen) == 1 and "senha = the ticket (queue number)" in seen[0]
    cached = (proj.chapter_dir(2) / "premissa_modelo.md").read_text(encoding="utf-8")
    assert cached.startswith("<!-- ") and "The stairs." in cached


def test_traducao_nao_fica_com_contagem_inventada():
    from pipeline.orchestrator import _drop_invented_counts
    out = "Chapter Premise: Chapter 2: The Pulsing Stone (24)\n\nGoal: Introduce the Core. (15)\n1. He climbs. (about 300 words)"
    clean = _drop_invented_counts(out, "Chapter Premise: Chapter 2: A Pedra\nGoal: x\n1. Sobe. (about 300 words)")
    assert clean.splitlines()[0] == "Chapter Premise: Chapter 2: The Pulsing Stone"
    assert "(15)" not in clean and "(about 300 words)" in clean


def test_nomes_em_portugues_viram_os_oficiais_depois_da_traducao():
    from pipeline.orchestrator import _official_names
    gl = [('Barnaby "Tambor"', 'Barnaby "Thumper"'), ("Tambor", "Thumper"), ("Estação Ponto Cego", "Blind Spot Station"),
          ("Créditos", "Credits (CR)"), ("catador", "scavenger")]
    out = _official_names('Characters in scene: Alexei Ivanov, Barnaby "Tambor"\nLocations in scene: Estação Ponto Cego\nHe is a catador.', gl)
    assert 'Barnaby "Thumper"' in out and "Blind Spot Station" in out and "catador" in out


def test_quem_esta_no_elenco_nao_vira_proibido_pelo_primeiro_nome():
    cl = qa.build_checklist("x", "Kargen understanding the runes; Tinaia", ["a", "b"], ["Kargen", "Tinaia"], [],
                            ["Kargen Ironbreaker", "Alexei Ivanov"])
    assert cl.forbidden_names == ["Tinaia"]


def test_cena_atrasada_e_continuada_ate_o_minimo_do_capitulo(tmp_path):
    proj = _project(tmp_path)
    calls = []

    def gen(model, system_prompt, user_prompt, **kw):
        if system_prompt == P.SYSTEM_DRAFTING:
            calls.append(user_prompt)
            return " ".join(f"w{len(calls)}x{i}." for i in range(150))
        return Stub()(model, system_prompt, user_prompt, **kw)

    with _quality(QA_JUDGE_ENABLED=False, REVISE_PASS_ENABLED=False, QA_SCENE_RETRIES=0, SCENE_MAX_CONTINUATIONS=3,
                  CHAPTER_TARGET_WORDS=900), patch.object(orch_mod, "generate_text", gen):
        result = PipelineOrchestrator(project=proj).run_single(PREMISE, 2)
    # Metas de 300 por cena: 150 palavras ficariam acima de 60% só com continuação; o mínimo de 900 exige mais.
    assert len(result.draft.split()) >= 900



def test_minimo_de_palavras_muda_pela_configuracao(tmp_path):
    from fastapi.testclient import TestClient
    import webapp.server as srv
    from pipeline import premise as PM

    (tmp_path / "projetos").mkdir()
    with patch.object(config, "PROJECTS_DIR", tmp_path / "projetos"), patch.object(config, "DATA_DIR", tmp_path), \
            patch.object(config, "CHAPTER_TARGET_WORDS", 2200):
        c = TestClient(srv.create_app())
        assert c.put("/api/settings", json={"values": {"CHAPTER_TARGET_WORDS": 3000}}).status_code == 200
        assert config.CHAPTER_TARGET_WORDS == 3000
        assert c.get("/api/settings").json()["values"]["CHAPTER_TARGET_WORDS"] == 3000
        prompt = PM.build_suggest_prompt(2, config.CHAPTER_TARGET_WORDS, akashic="A", outline=None, previous_tail="",
                                         story_so_far="", summaries=[], open_threads="", roster="")
        assert "Minimum length: 3000 words." in prompt


def test_corte_por_repeticao_do_ollama_aproveita_o_texto():
    lines = [json.dumps({"response": "He walked. ", "done": False}),
             json.dumps({"response": "He walked. ", "done": False}),
             json.dumps({"error": "prediction aborted, token repeat limit reached"})]

    with patch.object(api.requests, "post", lambda url, json=None, stream=False, timeout=None: _Resp(200, lines=lines)):
        out = api.generate_text("m", "s", "p", num_ctx=4096, extra_options={"num_predict": 100})
    assert out == "He walked. He walked. "



def test_conferencia_acha_palavras_coladas_com_sublinhado():
    cl = qa.Checklist()
    text = "He emerged into the twilight of the junk_fields.\n\n[System] a_b status: ok\n\n> run_command"
    issues = qa.scene_issues(text, "", cl, "en", [], None)
    kinds = {i.kind: i for i in issues}
    assert "formatação" in kinds
    assert "junk_fields" in kinds["formatação"].text
    # Linhas de sistema/comando não entram na checagem: nomes técnicos não são prosa quebrada.
    assert "a_b" not in kinds["formatação"].text and "run_command" not in kinds["formatação"].text
    assert kinds["formatação"].hard
    assert qa.scene_issues("He walked through the junkyard quietly.", "", cl, "en", [], None) == []


def test_garbled_names_acha_nome_corrompido_em_qualquer_posicao():
    from pipeline.scenes import garbled_names

    names = ["Alexei", "Maeve", "Kargen"]
    draft = "Alexei sighed. He walked away."
    # Corrompido no começo da frase (onde new_proper_nouns não procura) e novo em relação ao rascunho.
    assert garbled_names("Alexelli sighed. He walked away.", names, draft) == [("Alexelli", "Alexei")]
    # Já estava assim no rascunho: não é uma corrupção nova do polimento.
    assert garbled_names("Alexelli sighed.", names, "Alexelli sighed.") == []
    # O próprio nome oficial, ou uma palavra comum qualquer, não acusa nada.
    assert garbled_names("Alexei sighed. Then he left.", names, draft) == []
    assert garbled_names("Kargen roared once.", names, draft) == []


def test_polimento_com_nome_corrompido_e_consertado_por_paragrafo():
    scene = "Alexei sighed. \"Maybe you just need a better perspective.\""
    polished = "Alexelli sighed. \"Maybe you just need a better perspective, da?\""
    names = ["Alexei", "Maeve", "Kargen"]
    problems = refine_problems(polished, scene, names=names, max_ratio=2)
    assert any("Alexelli" in p and "Alexei" in p for p in problems)


def test_drop_overlap_pega_fala_repetida_mesmo_dentro_de_paragrafo_maior():
    from pipeline.scenes import drop_overlap

    # A cena anterior termina com um parágrafo que mistura narração e a fala; a semelhança
    # por Jaccard do parágrafo inteiro cai abaixo do threshold, mas a fala em si é idêntica
    # (achado no capítulo 4: a cena seguinte reabriu repetindo a última fala ao pé da letra).
    previous = (
        'He walked away, exhausted.\n\n'
        '"There," he gasped, leaning his forehead against the cool metal of the wall. '
        '"Now that is what I call a successful negotiation."'
    )
    new_scene = (
        '"That is what I call a successful negotiation."\n\n'
        'Alexei exhaled, his breath hitching in his chest.'
    )
    out = drop_overlap(new_scene, previous)
    assert "successful negotiation" not in out
    assert out.startswith("Alexei exhaled")
