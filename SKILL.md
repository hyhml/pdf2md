---
name: pdf2md
description: Convert local PDFs to reliable Markdown with native-text extraction, lightweight Chinese OCR, and quality-gated MinerU escalation. Use for scanned-PDF OCR, PDF-to-Markdown conversion, plain-text extraction from PDFs, and batch PDF conversion; do not use for editing an existing Markdown document.
---

# PDF to Markdown

Use the bundled `scripts/pdf2md` entrypoint. It keeps ordinary scans on the
lightweight RapidOCR path and escalates low-confidence or structurally complex
documents to local MinerU.

## Route the request

- Use the default `auto` mode unless the user selects a mode.
- Use `--mode light` when the user explicitly wants text only and does not care
  about layout, images, tables, or formulas.
- Use `--mode strong` when the user explicitly asks for maximum fidelity or
  structural recovery. The default strong tier is `standard`.
- Keep processing local. This skill does not authorize remote parsing or file
  uploads.

Before the first conversion when runtime readiness is unknown, run:

```bash
scripts/pdf2md doctor --json
```

If dependencies or models are missing, explain what is missing. Run
`scripts/setup.sh` only when installation/model downloads are within the user's
request or the user approves them; the standard MinerU setup requires several
gigabytes.

## Convert

For one PDF:

```bash
scripts/pdf2md process "<input.pdf>" -o "<output.md>" --json
```

The short form is also valid:

```bash
scripts/pdf2md "<input.pdf>" -o "<output.md>" --json
```

For a directory:

```bash
scripts/pdf2md batch "<input-directory>" -o "<output-directory>" --recursive --json
```

Quote paths. Preserve the source PDF. Unless the user supplied an output path,
write beside the input with a `.md` suffix. Do not overwrite a materially
different existing output without checking its status with the user.

## Verify and report

Each conversion writes `<output>.report.json`. Read it after processing and
report the selected stage (`native`, `light`, or `strong`) and the output path.
For light OCR, use its confidence and quality fields when describing possible
uncertainty. If the command reports `forced`, state that the selected mode did
not pass the normal automatic quality gate.

If automatic routing cannot use MinerU, it preserves a `*.light.md` candidate
and exits with an error. Do not present that candidate as a verified final
result; explain the missing strong backend or request permission to set it up.
