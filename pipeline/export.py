"""
export.py — Exporta os capítulos de um projeto para leitura ou publicação.

Formatos: Markdown (.md), texto puro (.txt), HTML de página única (.html) e EPUB 3
(.epub). O EPUB é montado à mão com zipfile, sem dependências. Usa o texto final de
cada capítulo; capítulos sem texto final ficam de fora.
"""

import html
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipeline.chapters import chapter_title
from pipeline.project import StoryProject
from pipeline.scenes import split_chapter

FORMATS = {
    "md": "Markdown",
    "txt": "Texto",
    "html": "HTML",
    "epub": "EPUB",
}


@dataclass
class ExportChapter:
    num: int
    title: str
    scenes: list[str]


def collect_chapters(project: StoryProject, first: int | None = None, last: int | None = None) -> list[ExportChapter]:
    out = []
    for entry in project.scan_chapters():
        if first is not None and entry.num < first:
            continue
        if last is not None and entry.num > last:
            continue
        if not entry.final.strip():
            continue
        title, scenes = split_chapter(entry.final)
        out.append(ExportChapter(entry.num, title or chapter_title(entry.final, entry.num), scenes))
    return out


def book_title(project: StoryProject) -> str:
    return project.metadata.get("book_title") or project.name


# ── Texto ────────────────────────────────────────────────────

def to_markdown(title: str, chapters: list[ExportChapter], scene_break: str = "* * *") -> str:
    parts = [f"# {title}"]
    for ch in chapters:
        body = f"\n\n{scene_break}\n\n".join(s.strip() for s in ch.scenes)
        parts.append(f"## {ch.title}\n\n{body}")
    return "\n\n".join(parts).strip() + "\n"


_MD_EMPH = re.compile(r"(\*\*|__)(.+?)\1|(\*|_)(?!\s)(.+?)(?<!\s)\3")


def _plain(text: str) -> str:
    return _MD_EMPH.sub(lambda m: m.group(2) or m.group(4), text)


def to_text(title: str, chapters: list[ExportChapter], scene_break: str = "* * *") -> str:
    parts = [title.upper(), ""]
    for ch in chapters:
        parts += ["", ch.title, "=" * len(ch.title), ""]
        body = f"\n\n{scene_break}\n\n".join(_plain(s.strip()) for s in ch.scenes)
        parts.append(body)
    return "\n".join(parts).strip() + "\n"


# ── HTML e EPUB ──────────────────────────────────────────────

def _inline(text: str) -> str:
    """Escapa o texto e converte **negrito** e *itálico* do Markdown."""
    escaped = html.escape(text, quote=False)

    def repl(m: re.Match) -> str:
        if m.group(2) is not None:
            return f"<strong>{m.group(2)}</strong>"
        return f"<em>{m.group(4)}</em>"

    return _MD_EMPH.sub(repl, escaped)


def _paragraphs_html(scene: str) -> str:
    out = []
    for para in re.split(r"\n\s*\n", scene.strip()):
        para = para.strip()
        if not para:
            continue
        lines = [l.strip() for l in para.splitlines() if l.strip()]
        if all(l.startswith("[") for l in lines):
            out.extend(f'<p class="system">{_inline(l)}</p>' for l in lines)
        else:
            out.append(f"<p>{'<br/>'.join(_inline(l) for l in lines)}</p>")
    return "\n".join(out)


def chapter_body_html(ch: ExportChapter) -> str:
    scenes = '\n<p class="break">* * *</p>\n'.join(_paragraphs_html(s) for s in ch.scenes)
    return f'<h2>{html.escape(ch.title)}</h2>\n{scenes}'


CSS = """
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.6; margin: 0 auto; max-width: 42em; padding: 1em; }
h1 { text-align: center; margin: 2em 0; }
h2 { margin-top: 2.5em; }
p { margin: 0 0 0.9em; text-indent: 0; }
p.break { text-align: center; margin: 1.5em 0; }
p.system { font-family: "Consolas", "Courier New", monospace; font-size: 0.92em; }
""".strip()


def to_html(title: str, chapters: list[ExportChapter]) -> str:
    toc = "\n".join(f'<li><a href="#cap{c.num}">{html.escape(c.title)}</a></li>' for c in chapters)
    body = "\n".join(f'<section id="cap{c.num}">\n{chapter_body_html(c)}\n</section>' for c in chapters)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8"/>\n'
        f"<title>{html.escape(title)}</title>\n<style>\n{CSS}\n</style>\n</head>\n<body>\n"
        f"<h1>{html.escape(title)}</h1>\n<nav><ol>\n{toc}\n</ol></nav>\n{body}\n</body>\n</html>\n"
    )


def _xhtml(title: str, body: str, lang: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">\n'
        f'<head><meta charset="utf-8"/><title>{html.escape(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def write_epub(path: Path, title: str, chapters: list[ExportChapter], author: str = "", lang: str = "en") -> Path:
    path = Path(path)
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = [(f"cap{c.num:03d}.xhtml", c) for c in chapters]

    manifest = "\n".join(
        f'    <item id="c{c.num}" href="{name}" media-type="application/xhtml+xml"/>' for name, c in files
    )
    spine = "\n".join(f'    <itemref idref="c{c.num}"/>' for _, c in files)
    creator = f"    <dc:creator>{html.escape(author)}</dc:creator>\n" if author else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{book_id}</dc:identifier>\n'
        f"    <dc:title>{html.escape(title)}</dc:title>\n{creator}"
        f"    <dc:language>{lang}</dc:language>\n"
        f'    <meta property="dcterms:modified">{modified}</meta>\n'
        "  </metadata>\n  <manifest>\n"
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
        '    <item id="css" href="style.css" media-type="text/css"/>\n'
        '    <item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>\n'
        f"{manifest}\n  </manifest>\n"
        '  <spine toc="ncx">\n    <itemref idref="title"/>\n'
        f"{spine}\n  </spine>\n</package>\n"
    )
    nav_items = "\n".join(f'<li><a href="{name}">{html.escape(c.title)}</a></li>' for name, c in files)
    nav = _xhtml(title, f'<nav epub:type="toc" id="toc"><h1>{html.escape(title)}</h1><ol>\n{nav_items}\n</ol></nav>', lang)
    points = "\n".join(
        f'    <navPoint id="p{i}" playOrder="{i}"><navLabel><text>{html.escape(c.title)}</text></navLabel>'
        f'<content src="{name}"/></navPoint>'
        for i, (name, c) in enumerate(files, start=1)
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'  <head><meta name="dtb:uid" content="{book_id}"/></head>\n'
        f"  <docTitle><text>{html.escape(title)}</text></docTitle>\n"
        f"  <navMap>\n{points}\n  </navMap>\n</ncx>\n"
    )
    title_page = _xhtml(title, f"<h1>{html.escape(title)}</h1>" + (f"<p style=\"text-align:center\">{html.escape(author)}</p>" if author else ""), lang)
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
        "</container>\n"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        # O mimetype tem de ser o primeiro arquivo e sem compressão.
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/title.xhtml", title_page, compress_type=zipfile.ZIP_DEFLATED)
        for name, c in files:
            z.writestr(f"OEBPS/{name}", _xhtml(c.title, chapter_body_html(c), lang), compress_type=zipfile.ZIP_DEFLATED)
    return path


# ── Entrada única ────────────────────────────────────────────

def export_project(
    project: StoryProject,
    fmt: str,
    dest: Path,
    first: int | None = None,
    last: int | None = None,
    scene_break: str = "* * *",
) -> tuple[Path, int]:
    """Grava o arquivo exportado em `dest`. Retorna (caminho, número de capítulos)."""
    if fmt not in FORMATS:
        raise ValueError(f"Formato desconhecido: {fmt}")
    chapters = collect_chapters(project, first, last)
    if not chapters:
        raise ValueError("Nenhum capítulo com texto final nesse intervalo.")
    title = book_title(project)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "epub":
        lang = project.metadata.get("language", "en")
        write_epub(dest, title, chapters, author=project.metadata.get("author", ""), lang=lang)
    elif fmt == "md":
        dest.write_text(to_markdown(title, chapters, scene_break), encoding="utf-8")
    elif fmt == "txt":
        dest.write_text(to_text(title, chapters, scene_break), encoding="utf-8")
    else:
        dest.write_text(to_html(title, chapters), encoding="utf-8")
    return dest, len(chapters)


def default_filename(project: StoryProject, fmt: str) -> str:
    slug = project.metadata.get("slug") or project.project_dir.name
    return f"{slug}.{fmt}"

