"""Testes do formato v2, da árvore de escolhas, do gerador e da migração. Rodar: python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.akashic import build_registro_modelo  # noqa: E402
from pipeline.akashic_builder import build_akashic, catalog_universe  # noqa: E402
from pipeline.akashic_catalog import match_universe  # noqa: E402
from pipeline.akashic_migrate import migrate_text, parse_v1  # noqa: E402
from pipeline.akashic_schema import AkashicMeta, Universe, read_meta, strip_meta, write_meta  # noqa: E402
from pipeline.akashic_tree import BY_ID, is_visible, min_items, missing, prune, visible_questions  # noqa: E402


def ids(answers):
    return [q.id for q in visible_questions(answers)]


def test_meta_ida_e_volta_sem_alterar_o_corpo():
    meta = AkashicMeta(title="Meu Mundo", structure="multiverse", universes=[Universe("a", "Alfa", wiki="alfa")])
    text = write_meta("# Bíblia do Mundo: Meu Mundo\n\n## 1. Story summary (EN)\n\nTexto.\n", meta)
    back, body = read_meta(text)
    assert back is not None and back.universes[0].wiki == "alfa" and back.structure == "multiverse"
    assert body.startswith("# Bíblia do Mundo: Meu Mundo") and "## 1. Story summary (EN)\n\nTexto." in body
    assert write_meta(text, meta).count("akashic:meta") == 1  # reescrever não duplica o bloco


def test_json_invalido_nao_derruba_a_leitura():
    text = "# T\n<!-- akashic:meta\n{quebrado\n-->\n## 1. X\n"
    assert read_meta(text) == (None, text)


def test_campos_desconhecidos_no_meta_sao_ignorados():
    text = write_meta("# T\n", AkashicMeta(title="T"))
    text = text.replace('"version": 2', '"version": 2, "campo_futuro": 1')
    assert read_meta(text)[0] is not None


def test_arvore_em_cascata():
    base = {"structure": "single", "origin": "original"}
    assert "connection" not in ids(base) and "multiverse_scope" not in ids(base) and "power_balance" not in ids(base)
    multi = {"structure": "multiverse", "origin": "crossover"}
    assert {"connection", "multiverse_scope", "power_balance", "canon_policy"} <= set(ids(multi))
    assert "divergence" in ids({"structure": "timelines"})
    assert "game_rules" in ids({"power_system": "game"}) and "game_rules" not in ids({"power_system": "magic"})
    assert "romance" in ids({"tone": ["action", "romance"]}) and "romance" not in ids({"tone": ["action"]})


def test_multiverso_pede_dois_universos_e_prune_remove_ramo_abandonado():
    assert min_items(BY_ID["universes"], {"structure": "multiverse"}) == 2
    assert min_items(BY_ID["universes"], {"structure": "single"}) == 1
    answers = {"structure": "multiverse", "connection": "hub", "multiverse_scope": "closed", "power_balance": "tiers"}
    answers["structure"] = "single"  # usuário voltou e mudou o ramo
    assert set(prune(answers)) == {"structure"}


def test_missing_lista_so_obrigatorias_visiveis():
    m = {q.id for q in missing({"structure": "single"})}
    assert "title" in m and "universes" in m and "connection" not in m and "references" not in m
    assert not is_visible(BY_ID["connection"], {})


ANSWERS = {
    "title": "Mercado Entre Mundos", "origin": "crossover", "structure": "multiverse", "connection": "hub",
    "multiverse_scope": "closed",
    "universes": [catalog_universe("Stargate", "base"), catalog_universe("Halo", "base"),
                  catalog_universe("MCU"), catalog_universe("Naruto", active=False)],
    "canon_policy": "changes", "power_system": "game", "game_rules": ["levels", "respawn"],
    "power_balance": "equalized", "death_rules": "conditional",
    "protagonists": [{"name": "Ana Souza", "origin": "reencarnada", "age": "19"}],
    "supporting": [{"name": "Beto", "origin": "nativo"}], "antagonist": "episodic", "tone": ["comedy", "action"],
    "rating": "teen", "pov": "third_limited", "tense": "past", "language": "en", "chapter_length": "medium",
    "season_plan": "35",
}


def test_gerador_segue_a_numeracao_padrao_e_o_meta_bate_com_as_respostas():
    text = build_akashic(ANSWERS)
    for heading in ("## 1. Story summary (EN)", "## 2. Inviolable rules (EN)", "### 5.8 Cast", "## 9. Universos",
                    "### 9.5 Universes", "## 10. Style guide (EN)", "### 13.2 Official spellings (EN)",
                    "## 14. What the models must not do (EN)"):
        assert heading in text, heading
    meta, body = read_meta(text)
    assert meta is not None and meta.title == "Mercado Entre Mundos"
    assert [u.name for u in meta.universes if u.active] == ["Stargate", "Halo", "MCU"]
    assert [c.name for c in meta.characters] == ["Ana Souza", "Beto"]
    short = body[body.index("### 9.5"):body.index("## 10.")]
    assert "Naruto" not in short and "Stargate" in short  # reserva fica fora da lista para o modelo
    assert "Naruto" in body  # mas aparece na ficha do autor, marcada como reserva


def test_modelo_curto_e_gerado_do_arquivo_v2_sem_o_bloco_de_meta(tmp_path):
    (tmp_path / "registro_akashico.md").write_text(build_akashic(ANSWERS), encoding="utf-8")
    ok, msg = build_registro_modelo(tmp_path)
    model = (tmp_path / "registro_modelo.md").read_text(encoding="utf-8")
    assert ok and "seções" in msg and "[A DEFINIR" in msg  # avisa o que falta preencher
    assert "akashic:meta" not in model
    assert "\n## 9." not in model and "### 9.5" in model  # a seção 9 inteira não vai; só a lista curta 9.5


V1 = """# Bíblia do Mundo: Mundo Velho

## 5. Personagens

### 5.2 Ana Souza

- Nome: Ana

### 5.3 A Gerência

- Nome: X

### 5.6 Fada, a primeira

## 9. Universos visitantes

### 9.1 Regra

Lista fechada: Stargate Atlantis e Halo como origem da tecnologia, mais MCU e DC como origem dos visitantes.

### 9.5 Universes (short list for the model) (EN)

- **Stargate Atlantis.** Only the empty city. No canon characters.
- **Halo, alternate universe.** Only the Ark. Allowed: 000 Tragic Solitude.
- **MCU.** Allowed: Ned Leeds (tier 1). Others never appear.
- **DC.** Allowed: Booster Gold with his robot Skeets (tier 2 with gear), Blue Beetle (tier 2).

## 10. Style guide (EN)

- Language: English.
"""


def test_parse_v1_acha_universos_papeis_e_permitidos():
    meta = parse_v1(V1)
    by_id = {u.id: u for u in meta.universes}
    assert meta.title == "Mundo Velho" and meta.structure == "multiverse"
    assert by_id["stargate"].role == "base" and by_id["halo"].role == "base" and by_id["mcu"].role == "source"
    assert by_id["halo"].wiki == "halo" and by_id["stargate"].wiki == "stargate"
    assert by_id["halo"].allowed_characters == ["000 Tragic Solitude"]
    assert by_id["dc"].allowed_characters == ["Booster Gold", "Blue Beetle"]
    assert [c.name for c in meta.characters] == ["Ana Souza", "A Gerência", "Fada"]
    assert meta.characters[0].role == "protagonist"


def test_migracao_preserva_o_corpo_e_adiciona_reservas_do_catalogo():
    new, report = migrate_text(V1)
    meta, body = read_meta(new)
    assert meta is not None and body.strip() == V1.strip()  # corpo intacto, byte a byte (fora o bloco)
    assert strip_meta(new).strip() == V1.strip()
    active = [u for u in meta.universes if u.active]
    reserve = [u for u in meta.universes if not u.active]
    assert [u.id for u in active] == ["stargate", "halo", "mcu", "dc"]
    assert reserve and all(u.wiki for u in reserve)
    assert not {u.wiki for u in reserve} & {u.wiki for u in active}  # sem duplicar universos
    assert "de reserva" in report
    again, msg = migrate_text(new)
    assert again == new and "já está" in msg  # idempotente


def test_catalogo_reconhece_apelidos_e_prefere_o_mais_especifico():
    assert match_universe("Halo, alternate universe")["wiki"] == "halo"
    assert match_universe("Stargate Atlantis")["wiki"] == "stargate"
    assert match_universe("Gate: Jieitai")["wiki"] == "gate"
    assert match_universe("Universo Inventado") is None
