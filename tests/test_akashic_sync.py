"""
Testes da ligação entre as abas Universos/Personagens do editor e o modelo.

Antes desta correção, editar essas abas mudava só o bloco JSON de metadados, e o
`registro_modelo.md` saía idêntico. Rodam com pytest ou com qualquer runner que passe `tmp_path`.
"""

from pathlib import Path

from pipeline.akashic import build_registro_modelo
from pipeline.akashic_schema import AkashicMeta, Character, Universe, read_meta, write_meta
from pipeline.akashic_sync import SYNC_NOTE, split_allowed, sync_text

BODY = """# Bíblia do Mundo: Teste

## 1. Story summary (EN)

A story.

## 2. Inviolable rules (EN)

1. Rule one.

### 5.8 Cast (short sheets for the model) (EN)

Nota: versão curta.

- **Ana, the hero.** Brave and loud.
- **Group of guards:** Tom (tier 1): quiet.

### 9.5 Universes (short list for the model) (EN)

Only these universes exist in the story.

- **MCU.** Allowed: Ned Leeds (tier 1), Mei (tier 1, 2 with gear). Heroes never appear.
- **Halo.** Only the Ark. No canon characters.

Origin mode power tiers: 1 trained human.

## 10. Style guide (EN)

- English.
"""


def _meta() -> AkashicMeta:
    return AkashicMeta(
        title="Teste",
        universes=[Universe(id="mcu", name="MCU"), Universe(id="halo", name="Halo"),
                   Universe(id="dc", name="DC", active=False)],
        characters=[Character(name="Ana", role="protagonist")],
    )


def _project(tmp_path: Path, meta: AkashicMeta | None = None, body: str = BODY) -> Path:
    text = write_meta(body, meta or _meta())
    (tmp_path / "registro_akashico.md").write_text(text, encoding="utf-8")
    return tmp_path


def _model(project: Path) -> str:
    ok, _ = build_registro_modelo(project)
    assert ok
    return (project / "registro_modelo.md").read_text(encoding="utf-8")


def _edit_meta(project: Path, fn):
    path = project / "registro_akashico.md"
    meta, body = read_meta(sync_text(path.read_text(encoding="utf-8")))
    fn(meta)
    path.write_text(write_meta(body, meta), encoding="utf-8")


def test_frase_allowed_respeita_parenteses():
    allowed, rest = split_allowed("Allowed: Ned Leeds (tier 1), Mei (tier 1, 2 with gear). Heroes never appear.")
    assert allowed == ["Ned Leeds (tier 1)", "Mei (tier 1, 2 with gear)"]
    assert rest == "Heroes never appear."


def test_primeira_sincronizacao_nao_muda_o_modelo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "registro_akashico.md").write_text(BODY, encoding="utf-8")  # v1: sem metadados
    synced = tmp_path / "synced"
    synced.mkdir()
    _project(synced)
    assert _model(synced) == _model(plain)


def test_importacao_preenche_fichas_e_cria_o_que_faltava(tmp_path):
    meta, _ = read_meta(sync_text(write_meta(BODY, _meta())))
    assert meta.lists_synced
    ana = meta.characters[0]
    assert ana.sheet == "Brave and loud." and ana.sheet_label == "Ana, the hero."
    assert [c.name for c in meta.characters] == ["Ana", "Group of guards"]
    mcu = next(u for u in meta.universes if u.id == "mcu")
    assert mcu.allowed_characters == ["Ned Leeds (tier 1)", "Mei (tier 1, 2 with gear)"]
    assert mcu.model_sheet == "Heroes never appear."


def test_sincronizar_de_novo_nao_muda_nada(tmp_path):
    once = sync_text(write_meta(BODY, _meta()))
    assert sync_text(once) == once
    assert once.count(SYNC_NOTE) == 2


def test_desativar_universo_tira_ele_do_modelo(tmp_path):
    project = _project(tmp_path)
    assert "**Halo.**" in _model(project)
    _edit_meta(project, lambda m: setattr(next(u for u in m.universes if u.id == "halo"), "active", False))
    model = _model(project)
    assert "**Halo.**" not in model and "**MCU.**" in model


def test_ativar_universo_de_reserva_poe_ele_no_modelo(tmp_path):
    project = _project(tmp_path)

    def activate(m):
        dc = next(u for u in m.universes if u.id == "dc")
        dc.active, dc.model_sheet, dc.allowed_characters = True, "Only Gotham.", ["Booster Gold"]

    _edit_meta(project, activate)
    assert "- **DC.** Allowed: Booster Gold. Only Gotham." in _model(project)


def test_editar_ficha_de_personagem_muda_o_modelo(tmp_path):
    project = _project(tmp_path)
    _edit_meta(project, lambda m: setattr(m.characters[0], "sheet", "Calm and precise."))
    model = _model(project)
    assert "- **Ana, the hero.** Calm and precise." in model
    assert "Brave and loud." not in model


def test_personagem_novo_e_removido(tmp_path):
    project = _project(tmp_path)
    _edit_meta(project, lambda m: m.characters.append(Character(name="Zed", sheet="A rival.")))
    assert "- **Zed.** A rival." in _model(project)
    _edit_meta(project, lambda m: m.characters.pop())
    assert "Zed" not in _model(project)


def test_personagem_sem_ficha_aparece_como_pendente(tmp_path):
    project = _project(tmp_path)
    _edit_meta(project, lambda m: m.characters.append(Character(name="Novo")))
    ok, msg = build_registro_modelo(project)
    assert "- **Novo.** [A DEFINIR:" in (project / "registro_modelo.md").read_text(encoding="utf-8")
    assert "[A DEFINIR]" in msg or "A DEFINIR" in msg


def test_introducao_e_rodape_da_secao_continuam(tmp_path):
    model = _model(_project(tmp_path))
    assert "Only these universes exist in the story." in model
    assert "Origin mode power tiers: 1 trained human." in model
    assert SYNC_NOTE not in model  # linha "Nota:" não vai para o modelo


def test_arquivo_v1_fica_igual():
    assert sync_text(BODY) == BODY
