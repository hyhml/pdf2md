#!/usr/bin/env python3
"""Export Docling JSON as page-labelled Markdown."""

from pathlib import Path
import sys

from docling_core.types.doc import DoclingDocument


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: docling_export.py INPUT.json OUTPUT.md", file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    document = DoclingDocument.load_from_json(source)
    separator = "<<<PDF_PAGE_BREAK>>>"
    pages = document.export_to_markdown(
        image_placeholder="<!-- image -->",
        page_break_placeholder=separator,
    ).split(separator)
    chunks = []
    for number, page in enumerate(pages, 1):
        chunks.append(
            f"<!-- PDF_PAGE: {number} -->\n\n"
            f"# PDF 第 {number} 页\n\n{page.strip()}\n"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(chunks), encoding="utf-8")
    print(f"pages={len(pages)} output={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
