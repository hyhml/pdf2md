---
name: pdf2md
description: Convert local PDFs to page-traceable Markdown with native-text extraction, lightweight Chinese OCR, and quality-gated Docling structural parsing. Use for scanned-PDF OCR, PDF-to-Markdown conversion, plain-text extraction from PDFs, and batch PDF conversion; do not use for editing an existing Markdown document.
---

# PDF to Markdown

Use the bundled `scripts/pdf2md` entrypoint. It keeps simple text PDFs and
ordinary scans on fast local paths, then escalates low-confidence or
structurally complex documents to local Docling.

## Route the request

- Use the default `auto` mode unless the user selects a mode.
- Use `--mode light` only when the user explicitly wants text-only OCR and does
  not care about layout, tables, headings, or footnotes.
- Use `--mode strong` when the user asks for reliable Markdown, structural
  recovery, page traceability, or maximum fidelity. The strong backend is
  Docling with full-page RapidOCR on CPU.
- Keep processing local. This skill does not authorize remote parsing or file
  uploads unless the user separately asks for an upload.

Before the first conversion when runtime readiness is unknown, run:

```bash
scripts/pdf2md doctor --json
```

If dependencies or models are missing, explain what is missing. Run
`scripts/setup.sh --strong` only when installation/model downloads are within
the user's request or the user approves them. Docling downloads its layout
models on first use and caches them under `.cache/pdf2md` in the current
working directory. Set `PDF2MD_CACHE_DIR` when a different writable cache
location is needed.

## Convert

For one PDF:

```bash
scripts/pdf2md process "<input.pdf>" -o "<output.md>" --mode strong --json
```

The short form is also valid:

```bash
scripts/pdf2md "<input.pdf>" -o "<output.md>" --mode strong --json
```

For a directory:

```bash
scripts/pdf2md batch "<input-directory>" -o "<output-directory>" --recursive --mode strong --json
```

Quote paths. Preserve the source PDF. Unless the user supplied an output path,
write beside the input with a `.md` suffix. Do not overwrite a materially
different existing output without checking its status with the user.

Docling strong output contains an explicit marker and heading for every source
page:

```markdown
<!-- PDF_PAGE: 1 -->

# PDF 第 1 页
```

Do not remove these markers when the user asks to retain page numbers.

## Estimate strong-mode runtime

Treat runtime estimates as ranges because scan resolution, page complexity,
OCR coverage, model warm-up, CPU, and available memory all matter. For a
strong-mode job similar to the benchmark below, use about 4.1 seconds per page
as the measured baseline and add a 25–50% planning margin. Do not promise a
five-minute completion time for a roughly 100 MB, 269-page scan on comparable
hardware; communicate an initial estimate of about 20–30 minutes instead.

Recorded local benchmark (2026-09-30):

- Input: 102,974,450-byte PDF, 269 pages, structurally complex Chinese scan.
- Mode/backend: `strong`, Docling 2.130.0 with full-page RapidOCR on CPU.
- Host: WSL2, Intel Core i7-12700H (20 logical CPUs), 7.6 GiB RAM.
- Successful conversion: 1,108.12 seconds (18 minutes 28 seconds), or 4.12
  seconds per page; peak RSS was 5,962,412 KiB.
- A preceding cache-permission failure consumed another 13.90 seconds. Keep
  model/cache setup failures separate from the successful conversion time when
  reporting elapsed time.

Use this benchmark only for comparable strong-mode documents. Prefer newer
local observations when available, and widen the estimate for larger images,
more complex layouts, model downloads, slower CPUs, or memory pressure.

## Verify and report

Each conversion writes `<output>.report.json`. Read it after processing and
report the selected stage (`native`, `light`, or `strong`), status, output path,
and page-marker count. For strong output, verify that the number of
`<!-- PDF_PAGE: n -->` markers matches the source PDF page count. For light
OCR, use its confidence and quality fields when describing uncertainty.

If automatic routing cannot use Docling, it preserves a `*.light.md` candidate
and exits with an error. Do not present that candidate as a verified final
result; explain the missing strong backend or request permission to set it up.
