#!/usr/bin/env python3
"""Build page-labelled comparison copies without editing any OCR text."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
TITLE = "尼共毛（汪亭友）测试"


def page_heading(page: int) -> str:
    return f"<!-- PDF_PAGE: {page} -->\n\n# PDF 第 {page} 页\n\n"


def build_pp_structure() -> None:
    raw_dir = ROOT / "pp-structurev3" / "raw"
    files = sorted(
        raw_dir.glob(f"{TITLE}_*.md"),
        key=lambda path: int(re.search(r"_(\d+)\.md$", path.name).group(1)),
    )
    text = "".join(
        page_heading(page) + path.read_text(encoding="utf-8").strip() + "\n\n"
        for page, path in enumerate(files, 1)
    )
    (ROOT / "pp-structurev3" / f"{TITLE}.paginated.md").write_text(
        text, encoding="utf-8"
    )


def build_ocrmypdf() -> None:
    source = ROOT / "ocrmypdf" / f"{TITLE}.ocrmypdf.md"
    pages = source.read_text(encoding="utf-8").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    text = "".join(
        page_heading(page) + content.strip() + "\n\n"
        for page, content in enumerate(pages, 1)
    )
    (ROOT / "ocrmypdf" / f"{TITLE}.paginated.md").write_text(
        text, encoding="utf-8"
    )


def build_docling() -> None:
    from docling_core.types.doc import DoclingDocument

    source = ROOT / "docling" / "json" / f"{TITLE}.json"
    document = DoclingDocument.load_from_json(source)
    marker = "<<<PDF_PAGE_BREAK>>>"
    pages = document.export_to_markdown(
        image_placeholder="<!-- image -->", page_break_placeholder=marker
    ).split(marker)
    text = "".join(
        page_heading(page) + content.strip() + "\n\n"
        for page, content in enumerate(pages, 1)
    )
    (ROOT / "docling" / f"{TITLE}.paginated.md").write_text(
        text, encoding="utf-8"
    )


def build_marker() -> None:
    matches = sorted((ROOT / "marker2" / "raw").rglob("*.md"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Marker Markdown file, found: {matches}")
    source = matches[0].read_text(encoding="utf-8")
    parts = re.split(r"(?m)^\{\d+\}-+\s*$", source)
    if parts and not parts[0].strip():
        parts.pop(0)
    text = "".join(
        page_heading(page) + content.strip() + "\n\n"
        for page, content in enumerate(parts, 1)
    )
    (ROOT / "marker2" / f"{TITLE}.paginated.md").write_text(
        text, encoding="utf-8"
    )


if __name__ == "__main__":
    build_pp_structure()
    build_ocrmypdf()
    build_docling()
    build_marker()
