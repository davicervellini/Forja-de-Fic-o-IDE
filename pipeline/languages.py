"""
languages.py — Os dois idiomas do programa.

- Idioma do usuário (config.UI_LANGUAGE): a interface e tudo o que o programa escreve para
  a pessoa ler — resumos, memória, roster, pontas soltas, fichas importadas da wiki,
  sugestões e conferências de premissa, checagem de consistência.
- Idioma da história (metadado "language" do projeto): só o texto dos capítulos e a
  exportação.

Os pedidos ao modelo continuam em inglês (modelos pequenos seguem melhor instruções em
inglês); o idioma de cada resposta vai numa linha no fim do pedido, que é o que o modelo
lê por último.
"""

from pipeline import config

# Nome em inglês (para os pedidos ao modelo) e no próprio idioma (para a tela).
LANGUAGES: dict[str, tuple[str, str]] = {
    "pt-BR": ("Brazilian Portuguese", "Português (Brasil)"),
    "en": ("English", "English"),
    "es": ("Spanish", "Español"),
    "fr": ("French", "Français"),
    "de": ("German", "Deutsch"),
    "it": ("Italian", "Italiano"),
    "ja": ("Japanese", "日本語"),
}

# Idiomas com tradução da interface pronta (webapp/static/i18n/<código>.json; pt-BR é o original).
UI_TRANSLATED = ("pt-BR", "en")


def english_name(code: str) -> str:
    return LANGUAGES.get(code, (code, code))[0]


def notes_language() -> str:
    return getattr(config, "UI_LANGUAGE", "pt-BR") or "pt-BR"


def story_language(project) -> str:
    """Idioma da história do projeto: metadado do projeto, senão o do Registro, senão inglês."""
    code = (getattr(project, "metadata", {}) or {}).get("language", "")
    if code:
        return code
    try:
        from pipeline.akashic_schema import read_meta
        path = project.akashic_path
        if path.exists():
            meta, _ = read_meta(path.read_text(encoding="utf-8"))
            if meta and meta.language and meta.language != "other":
                return meta.language
    except Exception:
        pass
    return "en"


def story_rule(code: str) -> str:
    """Última linha dos pedidos que escrevem a história."""
    name = english_name(code)
    return (f"LANGUAGE: write all narration and dialogue in {name}. The records, notes, memory and premise "
            f"above may be written in another language: never copy their language into the story, and translate "
            f"any idea you take from them. Keep proper names and [System] lines as they are.")


CHAPTER_WORD = {"en": "Chapter", "pt-BR": "Capítulo", "es": "Capítulo", "fr": "Chapitre", "de": "Kapitel",
                "it": "Capitolo", "ja": "Chapter"}


def chapter_heading(title: str | None, num: int, code: str) -> str:
    """'Chapter 2: X' com a palavra de capítulo do idioma da história ('Capítulo 2: X')."""
    import re
    word = CHAPTER_WORD.get(code, "Chapter")
    if not title:
        return f"{word} {num}"
    return re.sub(r"^(?:chapter|cap[ií]tulo|chapitre|kapitel|capitolo)\s+(\d+)", lambda m: f"{word} {m.group(1)}",
                  title.strip(), count=1, flags=re.I)


def notes_rule(code: str) -> str:
    """Última linha dos pedidos cujo resultado a pessoa lê (resumos, memória, fichas, conferências)."""
    name = english_name(code)
    return (f"LANGUAGE: write your answer in {name}. Keep proper names, field labels and section headers "
            f"exactly as shown in English, and quote story text in its original language.")
