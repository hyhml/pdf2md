# Local formula-hybrid conversion

This pipeline keeps prose on the PDF native-text path. Docling runs without
OCR and supplies display-formula/layout boxes; STIX/font geometry supplies
inline candidates. Formula crops are rendered locally at 300 DPI.

Recognition uses two independent local models:

- OpenOCR `UniRec-0.1B` (ONNX) as the primary recognizer.
- PaddleOCR `PP-FormulaNet_plus-L` as the cross-check.

A formula is inserted automatically only when both models agree after
presentation-only normalization and the structural/context validators pass.
All other formulas are copied to the review directory and emitted as an
explicit `FORMULA_REVIEW_REQUIRED` image link. Packages and model weights may
be downloaded, but PDF pages and crops are never uploaded.

```bash
formula-hybrid/formula-hybrid doctor

formula-hybrid/formula-hybrid test \
  "sample.pdf" \
  --docling-json formula-hybrid/work/sample-docling/sample-20.json \
  --work-dir formula-hybrid/work/sample-ensemble-display \
  --review-dir formula-hybrid/work/sample-ensemble-v2-assets/review \
  --manual-audit formula-hybrid/sample-ensemble-manual-audit.json \
  -o formula-hybrid/work/sample-ensemble-v2.formula.md
```

Each page is checkpointed as a JSON record and Markdown fragment under the
run directory. A repeated `test` command resumes from checkpoints.

## Sample result and full-book gate

The audited 20-page sample does **not** pass the full-book gate:

- 126 display formulas detected: 50 initially auto-accepted; manual context
  review demoted 1, leaving 49 verified (38.89%, required 95%).
- 83 inline formulas detected after removing prose/graph-label false
  positives: 37 initially auto-accepted; manual context review demoted 3,
  leaving 34 verified (40.96%, required 90%).
- 126 formulas have an explicit review screenshot; there are no broken or
  orphaned review links.
- Of 52 failures documented for the older S/M pipeline, the new pipeline
  flags 33 for review and corrects plus accepts 19.
- The fresh ensemble sample run took 441.763 seconds. At the same formula
  density, 769 pages extrapolate to about 4.7 hours before margin, or roughly
  5.9–7.1 hours with the required 25–50% planning margin.

The remaining failures are mainly meaningful cross-model disagreements,
equivalent LaTeX written differently by the two models, complex multiline
formulas, and inline crop boundaries that split adjacent superscripts or
operators. Strict model agreement therefore reduces false acceptance but does
not provide enough automatic coverage for this book.

The `process` command requires an audited gate report and refuses to start
unless that report contains `full_book_processing_authorized: true`:

```bash
formula-hybrid/formula-hybrid process "book.pdf" \
  --gate-report audited-sample.formula.md.report.json \
  -o "book.formula.md" --batch-size 25
```

For the current sample report that flag is false, so the 769-page conversion
must remain stopped. The original PDF, existing Markdown, and its original
conversion report are not modified.
