# 6-page Chinese scan comparison (2026-09-27)

This directory contains the reproducible outputs and analysis from a six-page
Chinese scanned-book sample. The sample includes three dense table-of-contents
pages and three body pages with headings, headers/footers, long Chinese
footnotes, and an English citation.

The source PDF is intentionally not committed. Its recorded SHA-256 is:

```text
62aa011553c4e36999e14d7c50f6ba1e3578b7ce29c1cd3817a93b1e80c3b246
```

Compared tools:

- PP-StructureV3 / PaddleOCR 3.5.0
- Docling 2.130.0 with RapidOCR
- Marker 2.0.0 / Surya 0.22.1
- OCRmyPDF 16.13.0 / Tesseract 4.1.1

Start with [REPORT.md](REPORT.md). Direct tool outputs are under each tool's
`raw/` directory; `*.paginated.md` files add consistent 1-based PDF page
markers without correcting OCR text. [phrase-check.md](phrase-check.md)
contains the 12-phrase mechanical spot check.
[LONG_DOCUMENT_VALIDATION.md](LONG_DOCUMENT_VALIDATION.md) records the
successful 116-page Docling validation without publishing the source or full
book text.

Virtual environments, model caches, downloaded runtimes, rendered source pages,
and the searchable derivative PDF are excluded. They occupied about 8.4 GiB
locally but are reproducible and are not needed to inspect the comparison.
