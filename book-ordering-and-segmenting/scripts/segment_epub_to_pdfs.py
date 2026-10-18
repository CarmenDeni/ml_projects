#!/usr/bin/env python3
"""Segmenta un EPUB en PDFs por capítulo e inserta las notas al pie en el texto."""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning
import warnings
from weasyprint import HTML

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

NS = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}


@dataclass
class Chapter:
    index: int
    title: str
    files: list[str]  # relative to OEBPS/


PDF_CSS = """
@page {
  size: A4;
  margin: 2.2cm 2cm 2.4cm 2cm;
  @bottom-center {
    content: counter(page);
    font-family: "Liberation Serif", "DejaVu Serif", Georgia, serif;
    font-size: 9pt;
    color: #444;
  }
}

html, body {
  font-family: "Liberation Serif", "DejaVu Serif", Georgia, serif;
  font-size: 11.5pt;
  line-height: 1.45;
  color: #1a1a1a;
  background: #fff;
}

body { margin: 0; }

p {
  margin: 0 0 0.65em 0;
  text-align: justify;
  text-indent: 1.4em;
  hyphens: auto;
}

a { color: inherit; text-decoration: none; }

h1, h2, h3, h4 {
  font-family: "Liberation Serif", "DejaVu Serif", Georgia, serif;
  font-weight: 700;
  text-align: center;
  text-indent: 0;
  margin: 1.4em 0 0.8em;
  page-break-after: avoid;
  hyphens: none;
}

h1 { font-size: 1.55em; margin-top: 0.4em; }
h2 { font-size: 1.25em; }
h3 { font-size: 1.12em; }

.subtitulo {
  text-align: center;
  text-indent: 0;
  font-style: italic;
  margin: 0 0 1.4em;
}

.asangre, .asangre p, .sinopsis p, .info p,
.dedicatoria p, .cita p, .cita2 p, .centrado p,
.izquierda p, .derecha p {
  text-indent: 0;
}

.cita, .cita2 {
  margin: 0.9em 1.2em;
  font-size: 0.95em;
}

.dedicatoria {
  margin: 1.5em 0 1.8em;
  text-align: right;
}

.ilustra, .ilustra p, .cubierta, .autorimg {
  text-align: center;
  text-indent: 0;
  margin: 1.1em 0;
}

img {
  max-width: 100%;
  height: auto;
  display: inline-block;
}

.versalita {
  font-variant: small-caps;
  font-size: 0.92em;
  letter-spacing: 0.02em;
}

.inline-note {
  font-size: 0.88em;
  color: #2c2c2c;
  font-style: italic;
  text-indent: 0;
}

.inline-note em { font-style: normal; }

.chapter-meta {
  text-align: center;
  text-indent: 0;
  color: #555;
  font-size: 0.95em;
  margin: 0 0 1.6em;
}

.page-break { page-break-before: always; }

/* Neutralizar saltos pensados para ePub / notas finales */
.nota { page-break-before: auto; padding-top: 0; }
.saltoinicio { margin-top: 1.5em; }
"""


def slugify(title: str) -> str:
    text = title.strip().lower()
    text = (
        text.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
        .replace("«", "")
        .replace("»", "")
    )
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:70] or "capitulo"


def extract_epub(epub_path: Path, out_dir: Path) -> Path:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub_path, "r") as zf:
        zf.extractall(out_dir)
    return out_dir / "OEBPS"


def parse_notes(notes_path: Path) -> dict[str, str]:
    soup = BeautifulSoup(notes_path.read_text(encoding="utf-8"), "lxml")
    notes: dict[str, str] = {}
    for div in soup.select("div.nota"):
        anchor = div.find("a", id=True)
        if not anchor or not anchor.get("id"):
            continue
        note_id = anchor["id"]
        # Quitar enlaces de retorno
        for back in div.select('a[href]'):
            if back.get_text(strip=True) in {"<<", "«", "‹‹"} or "<<" in back.get_text():
                back.decompose()
        # Quitar el marcador [N] del inicio
        for sup in div.find_all("sup"):
            if re.fullmatch(r"\[\d+\]", sup.get_text(strip=True) or ""):
                sup.decompose()
        # Ancla vacía
        if anchor.parent is not None and not anchor.get_text(strip=True):
            anchor.decompose()

        parts: list[str] = []
        for p in div.find_all("p"):
            # Conservar markup interno básico
            html = "".join(str(c) for c in p.contents).strip()
            html = re.sub(r"\s+", " ", html)
            if html:
                parts.append(html)
        text = " ".join(parts).strip()
        text = re.sub(r"^\s*\[\d+\]\s*", "", text)
        notes[note_id] = text
    return notes


def inline_notes(soup: BeautifulSoup, notes: dict[str, str]) -> int:
    inserted = 0
    for a in list(soup.find_all("a", href=True)):
        href = a["href"]
        if "notas.xhtml#" not in href and not href.startswith("#nota"):
            continue
        fragment = urlparse(href).fragment or href.split("#", 1)[-1]
        fragment = unquote(fragment)
        note_html = notes.get(fragment)
        if not note_html:
            # Mantener el marcador si no hay texto
            continue
        span = soup.new_tag("span")
        span["class"] = "inline-note"
        # Insertar como parte del texto entre corchetes
        span.append(NavigableString(" ["))
        inner = BeautifulSoup(f"<fragment>{note_html}</fragment>", "lxml")
        frag = inner.find("fragment")
        if frag:
            for child in list(frag.contents):
                span.append(child)
        span.append(NavigableString("]"))
        a.replace_with(span)
        inserted += 1
    return inserted


def fix_image_sources(soup: BeautifulSoup, oebps: Path, file_dir: Path) -> None:
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        # Resolver ruta relativa al archivo xhtml
        resolved = (file_dir / src).resolve()
        if resolved.exists():
            img["src"] = resolved.as_uri()
        else:
            # Intentar desde OEBPS
            alt = (oebps / src.lstrip("./")).resolve()
            if alt.exists():
                img["src"] = alt.as_uri()


def body_inner_html(xhtml_path: Path, notes: dict[str, str], oebps: Path) -> tuple[str, int]:
    soup = BeautifulSoup(xhtml_path.read_text(encoding="utf-8"), "lxml")
    body = soup.body
    if body is None:
        return "", 0
    inserted = inline_notes(soup, notes)
    fix_image_sources(soup, oebps, xhtml_path.parent)
    # Devolver solo contenido del body
    return "".join(str(c) for c in body.contents), inserted


def discover_chapters(oebps: Path) -> list[Chapter]:
    """Capítulos lógicos del libro según el TOC + emparejamiento título/cuerpo."""
    chapters = [
        Chapter(1, "Material preliminar", ["Text/cubierta.xhtml", "Text/sinopsis.xhtml", "Text/titulo.xhtml", "Text/info.xhtml"]),
        Chapter(2, "Agradecimientos", ["Text/CYLB_0001.xhtml"]),
        Chapter(3, "Prefacio", ["Text/CYLB_0002.xhtml"]),
        Chapter(4, "Introducción", ["Text/CYLB_0003.xhtml", "Text/CYLB_0004.xhtml"]),
        Chapter(5, "El mundo entero necesita una sacudida", ["Text/CYLB_0005.xhtml", "Text/CYLB_0006.xhtml"]),
        Chapter(6, "La acumulación de trabajo y la degradación de las mujeres", ["Text/CYLB_0007.xhtml", "Text/CYLB_0008.xhtml"]),
        Chapter(7, "El Gran Calibán", ["Text/CYLB_0009.xhtml", "Text/CYLB_0010.xhtml"]),
        Chapter(8, "La gran caza de brujas en Europa", ["Text/CYLB_0011.xhtml", "Text/CYLB_0012.xhtml"]),
        Chapter(9, "Colonización y cristianización", ["Text/CYLB_0013.xhtml", "Text/CYLB_0014.xhtml"]),
        Chapter(10, "Bibliografía", ["Text/CYLB_0015.xhtml"]),
        Chapter(11, "Créditos de las imágenes", ["Text/CYLB_0016.xhtml"]),
        Chapter(12, "Entrevista a Silvia Federici", ["Text/CYLB_0017.xhtml"]),
        Chapter(13, "Creative Commons", ["Text/CYLB_0018.xhtml"]),
        Chapter(14, "Autor", ["Text/autor.xhtml"]),
    ]
    # Filtrar archivos inexistentes
    valid: list[Chapter] = []
    for ch in chapters:
        files = [f for f in ch.files if (oebps / f).exists()]
        if files:
            valid.append(Chapter(ch.index, ch.title, files))
    return valid


def build_chapter_html(chapter: Chapter, oebps: Path, notes: dict[str, str]) -> tuple[str, int]:
    parts: list[str] = []
    total_notes = 0
    for i, rel in enumerate(chapter.files):
        path = oebps / rel
        inner, count = body_inner_html(path, notes, oebps)
        total_notes += count
        if i > 0:
            parts.append('<div class="page-break"></div>')
        parts.append(inner)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <title>{chapter.title}</title>
  <style>{PDF_CSS}</style>
</head>
<body>
  <p class="chapter-meta">Silvia Federici — Calibán y la bruja</p>
  {"".join(parts)}
</body>
</html>
"""
    return html, total_notes


def write_pdf(html: str, out_path: Path, base_url: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=base_url).write_pdf(str(out_path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--epub",
        type=Path,
        default=root / "source" / "Caliban_y_la_bruja_-_Silvia_Federici_caf8.epub",
    )
    parser.add_argument("--out", type=Path, default=root / "chapters")
    parser.add_argument(
        "--work",
        type=Path,
        default=root / ".work" / "epub_extracted",
    )
    args = parser.parse_args()

    if not args.epub.exists():
        raise SystemExit(f"No se encontró el EPUB: {args.epub}")

    print(f"Extrayendo {args.epub} …")
    oebps = extract_epub(args.epub, args.work)
    notes_path = oebps / "Text" / "notas.xhtml"
    notes = parse_notes(notes_path)
    print(f"Notas cargadas: {len(notes)}")

    chapters = discover_chapters(oebps)
    args.out.mkdir(parents=True, exist_ok=True)

    # Limpiar PDFs previos
    for old in args.out.glob("*.pdf"):
        old.unlink()

    manifest_lines = [
        "# Capítulos generados",
        "",
        f"Fuente: `{args.epub.name}`",
        f"Notas integradas en el texto entre corchetes: {len(notes)} disponibles",
        "",
        "| # | Archivo | Título | Notas insertadas |",
        "|---|---------|--------|------------------|",
    ]

    for chapter in chapters:
        html, inserted = build_chapter_html(chapter, oebps, notes)
        filename = f"{chapter.index:02d}_{slugify(chapter.title)}.pdf"
        out_pdf = args.out / filename
        print(f"Generando {filename} ({inserted} notas) …")
        write_pdf(html, out_pdf, base_url=oebps.as_uri() + "/")
        size_kb = out_pdf.stat().st_size / 1024
        print(f"  → {out_pdf} ({size_kb:.0f} KB)")
        manifest_lines.append(
            f"| {chapter.index} | `{filename}` | {chapter.title} | {inserted} |"
        )

    (args.out / "MANIFEST.md").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    print("Listo.")


if __name__ == "__main__":
    main()
