#!/usr/bin/env python3
"""Local hybrid PDF-to-Markdown formula recovery.

The native PDF text layer remains the source for prose. Docling is used only
for layout/formula boxes. UniRec-0.1B is the primary local recognizer and
PP-FormulaNet_plus-L is an independent local cross-check.
No remote parsing or document upload is performed.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import difflib
import hashlib
import json
import io
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

import fitz
from PIL import Image, ImageOps
from latex2mathml.converter import convert as latex_to_mathml


SAMPLE_PAGES = [
    28, 29, 30, 31, 98, 100, 231, 232, 233, 234,
    241, 246, 247, 286, 374, 394, 421, 471, 515, 627,
]
MATH_FONTS = ("STIX", "Math", "Symbol", "Integrals")
ITALIC_FONTS = ("Italic", "It")
MATH_CHARS_RE = re.compile(
    r"^[\s\u200b\u205f\u202fA-Za-z0-9.,+\-−–=<>/()\[\]{}·×÷±∓∞∫∑∏√∆Δ∂≈≠≤≥^_'°"
    r"α-ωΑ-Ωρπμνλφθστωζχβγδ]+$"
)
FORMULA_SIGNAL_RE = re.compile(r"[=<>+\-−±∓×÷∫∑∏√∞≈≠≤≥]|[α-ωΑ-Ωρπμνλφθστωζχβγδ]")
INVISIBLE_TRANS = str.maketrans({"\u200b": "", "\u205f": " ", "\u202f": " ", "\xad": ""})
INLINE_STOPWORDS = {
    "all", "and", "as", "at", "be", "by", "for", "from", "however", "in", "is",
    "it", "no", "of", "on", "or", "the", "to", "vs", "with",
}


@dataclasses.dataclass
class Formula:
    page: int
    kind: str
    bbox: tuple[float, float, float, float]
    native_text: str
    crop: str = ""
    latex_s: str = ""
    latex_m: str = ""
    latex_unirec: str = ""
    latex_l: str = ""
    latex: str = ""
    model: str = ""
    agreement: float = 0.0
    valid: bool = False
    validation: list[str] = dataclasses.field(default_factory=list)
    review: bool = False
    line_index: int | None = None
    span_start: int | None = None
    span_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = a & b
    if inter.is_empty:
        return 0.0
    return inter.get_area() / max(1e-6, min(a.get_area(), b.get_area()))


def vertical_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    v = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    return v / max(1e-6, min(a.height, b.height))


def clean_native(text: str) -> str:
    text = text.translate(INVISIBLE_TRANS)
    text = re.sub(r"_+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def page_lines(page: fitz.Page) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for block in page.get_text("dict", sort=True).get("blocks", []):
        for line in block.get("lines", []):
            spans = []
            text_parts = []
            cursor = 0
            for span in line.get("spans", []):
                text = span.get("text", "").translate(INVISIBLE_TRANS)
                start = cursor
                text_parts.append(text)
                cursor += len(text)
                spans.append(
                    {
                        "text": text,
                        "start": start,
                        "end": cursor,
                        "bbox": tuple(span["bbox"]),
                        "font": span.get("font", ""),
                        "size": float(span.get("size", 0.0)),
                    }
                )
            text = "".join(text_parts).replace("\t", " ")
            lines.append({"bbox": tuple(line["bbox"]), "text": text, "spans": spans})
    lines.sort(key=lambda x: (round(x["bbox"][1], 1), x["bbox"][0]))
    return lines


def formula_like_span(span: dict[str, Any], base_size: float) -> bool:
    text = clean_native(span["text"])
    if not text or not MATH_CHARS_RE.match(text):
        return False
    font = span["font"]
    if FORMULA_SIGNAL_RE.search(text):
        return True
    if span["size"] and base_size and span["size"] <= base_size * 0.82:
        return True
    if any(x in font for x in MATH_FONTS) and any(x in font for x in ITALIC_FONTS):
        compact = re.sub(r"\s+", "", text)
        return len(compact) <= 5 or (
            bool(re.search(r"\d|[α-ωΑ-Ω]", compact))
            and not re.search(r"[A-Za-z]{4,}", compact)
        )
    if "Proxima" in font and any(x in font for x in ITALIC_FONTS):
        return len(re.sub(r"\s+", "", text)) <= 2
    return False


def bridge_span(span: dict[str, Any]) -> bool:
    text = clean_native(span["text"])
    if not text or len(text) > 24 or not MATH_CHARS_RE.match(text):
        return False
    if re.search(r"[,;:?!]", text):
        return False
    words = re.findall(r"[A-Za-z]{2,}", text)
    if len(words) >= 2 or any(len(word) > 3 for word in words):
        return False
    return True


def detect_inline(
    lines: list[dict[str, Any]],
    displays: list[Formula],
    page_no: int,
    exclusions: list[fitz.Rect] | None = None,
) -> list[Formula]:
    output: list[Formula] = []
    display_rects = [fitz.Rect(f.bbox) for f in displays]
    exclusions = exclusions or []
    for li, line in enumerate(lines):
        line_rect = fitz.Rect(line["bbox"])
        if any(overlap(line_rect, r) > 0.12 for r in display_rects):
            continue
        if any(overlap(line_rect, r) > 0.20 or r.contains(line_rect.tl) for r in exclusions):
            continue
        spans = line["spans"]
        if not spans:
            continue
        sizes = [s["size"] for s in spans if s["size"] >= 6]
        base_size = collections.Counter(round(s, 1) for s in sizes).most_common(1)[0][0] if sizes else 10.0
        seeds = [i for i, s in enumerate(spans) if formula_like_span(s, base_size)]
        used: set[int] = set()
        groups: list[tuple[int, int]] = []
        for seed in seeds:
            if seed in used:
                continue
            lo = hi = seed
            while lo > 0 and bridge_span(spans[lo - 1]):
                gap = fitz.Rect(spans[lo]["bbox"]).x0 - fitz.Rect(spans[lo - 1]["bbox"]).x1
                if gap > 4.0:
                    break
                lo -= 1
            while hi + 1 < len(spans) and bridge_span(spans[hi + 1]):
                gap = fitz.Rect(spans[hi + 1]["bbox"]).x0 - fitz.Rect(spans[hi]["bbox"]).x1
                if gap > 4.0:
                    break
                hi += 1
            for i in range(lo, hi + 1):
                used.add(i)
            groups.append((lo, hi))
        merged: list[tuple[int, int]] = []
        for lo, hi in sorted(groups):
            if merged and lo <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
            else:
                merged.append((lo, hi))
        for lo, hi in merged:
            raw_text = "".join(s["text"] for s in spans[lo : hi + 1]).translate(INVISIBLE_TRANS)
            left_trim = len(raw_text) - len(raw_text.lstrip(" \t,;:.!?"))
            right_trim = len(raw_text) - len(raw_text.rstrip(" \t,;:.!?"))
            text = clean_native(raw_text[left_trim:len(raw_text) - right_trim if right_trim else None])
            compact = re.sub(r"\s+", "", text)
            if not compact or len(compact) > 80:
                continue
            words = [word.lower() for word in re.findall(r"[A-Za-z]{2,}", text)]
            if any(word in INLINE_STOPWORDS for word in words):
                continue
            if any(len(word) >= 4 for word in words):
                continue
            strong_signal = bool(FORMULA_SIGNAL_RE.search(text) or re.search(r"[A-Za-z]\d|\d[A-Za-z]|/", compact))
            differential = bool(re.fullmatch(r"d[A-Za-z]", compact))
            # Isolated variables and abbreviations are already losslessly
            # represented by the native text layer; OCR only structural math.
            if not strong_signal and not differential:
                continue
            if compact.isalpha() and len(compact) > 1 and not strong_signal:
                if not differential:
                    continue
            if re.fullmatch(r"[+\-−–]?\d+(?:\.\d+)?", compact) and any(
                s["size"] < base_size * 0.9 for s in spans[lo : hi + 1]
            ):
                continue
            # Do not turn ordinary italic words/headings into equations.
            if compact.isalpha() and len(compact) > 3 and not re.search(r"[α-ωΑ-Ω]", compact):
                continue
            r = fitz.Rect(spans[lo]["bbox"])
            for span in spans[lo + 1 : hi + 1]:
                r |= fitz.Rect(span["bbox"])
            output.append(
                Formula(
                    page=page_no,
                    kind="inline",
                    bbox=tuple(r),
                    native_text=text,
                    line_index=li,
                    span_start=spans[lo]["start"] + left_trim,
                    span_end=spans[hi]["end"] - right_trim,
                )
            )
    return output


def expand_display_bbox(page: fitz.Page, bbox: fitz.Rect, lines: list[dict[str, Any]]) -> fitz.Rect:
    expanded = fitz.Rect(bbox)
    for line in lines:
        r = fitz.Rect(line["bbox"])
        if vertical_overlap(r, bbox) >= 0.35:
            text = clean_native(line["text"])
            if MATH_CHARS_RE.match(text) or re.fullmatch(r"\([A-Za-z]?\d+(?:\.\d+)*\)", text):
                expanded |= r
    return (expanded + (-4, -4, 4, 4)) & page.rect


def refine_display_bbox(page: fitz.Page, bbox: fitz.Rect, lines: list[dict[str, Any]]) -> fitz.Rect:
    """Trim prose accidentally touching a display-formula layout box."""
    seeds: list[fitz.Rect] = []
    formula_spans: list[fitz.Rect] = []
    tags: list[fitz.Rect] = []
    for line in lines:
        rect = fitz.Rect(line["bbox"])
        if vertical_overlap(rect, bbox) < 0.15 or (rect & bbox).is_empty:
            continue
        text = clean_native(line["text"])
        if re.fullmatch(r"\([A-Za-z]?\d+(?:\.\d+)+(?:[a-z])?\)", text):
            tags.append(rect)
        elif any(char in text for char in "=∫∂∑∏√"):
            seeds.append(rect)
        sizes = [span["size"] for span in line["spans"] if span["size"] >= 6]
        base_size = collections.Counter(round(size, 1) for size in sizes).most_common(1)[0][0] if sizes else 10.0
        for span in line["spans"]:
            span_rect = fitz.Rect(span["bbox"])
            span_text = clean_native(span["text"])
            if formula_like_span(span, base_size) or span_text in {"___", "--", "−", "/"}:
                formula_spans.append(span_rect)
    if not seeds:
        return bbox & page.rect
    seed_y0 = min(rect.y0 for rect in seeds)
    seed_y1 = max(rect.y1 for rect in seeds)
    connected = [
        rect for rect in formula_spans
        if rect.y1 >= seed_y0 - 18 and rect.y0 <= seed_y1 + 18
    ] or seeds
    y0 = min(rect.y0 for rect in connected) - 3.0
    y1 = max(rect.y1 for rect in connected) + 3.0
    # Keep equation numbers on the same visual rows.
    relevant_tags = [r for r in tags if r.y1 >= y0 - 3 and r.y0 <= y1 + 3]
    x0 = min([r.x0 for r in connected] + [r.x0 for r in relevant_tags]) - 4.0
    x1 = max([r.x1 for r in connected] + [r.x1 for r in relevant_tags]) + 4.0
    return fitz.Rect(x0, y0, x1, y1) & page.rect


def load_docling_formulas(docling_json: Path, page_map: list[int] | None = None) -> dict[int, list[fitz.Rect]]:
    data = json.loads(docling_json.read_text(encoding="utf-8"))
    page_heights = {int(k): float(v["size"]["height"]) for k, v in data["pages"].items()}
    out: dict[int, list[fitz.Rect]] = collections.defaultdict(list)
    for item in data.get("texts", []):
        if item.get("label") != "formula" or not item.get("prov"):
            continue
        prov = item["prov"][0]
        local_page = int(prov["page_no"])
        page_no = page_map[local_page - 1] if page_map else local_page
        b = prov["bbox"]
        h = page_heights[local_page]
        out[page_no].append(fitz.Rect(float(b["l"]), h - float(b["t"]), float(b["r"]), h - float(b["b"])))
    for rects in out.values():
        rects.sort(key=lambda r: (r.y0, r.x0))
    return out


def load_docling_exclusions(docling_json: Path, page_map: list[int] | None = None) -> dict[int, list[fitz.Rect]]:
    """Return picture/table regions where graph labels must not become inline math."""
    data = json.loads(docling_json.read_text(encoding="utf-8"))
    page_heights = {int(k): float(v["size"]["height"]) for k, v in data["pages"].items()}
    out: dict[int, list[fitz.Rect]] = collections.defaultdict(list)
    for collection in (data.get("pictures", []), data.get("tables", [])):
        for item in collection:
            if not item.get("prov"):
                continue
            prov = item["prov"][0]
            local_page = int(prov["page_no"])
            page_no = page_map[local_page - 1] if page_map else local_page
            bbox = prov["bbox"]
            height = page_heights[local_page]
            out[page_no].append(
                fitz.Rect(float(bbox["l"]), height - float(bbox["t"]), float(bbox["r"]), height - float(bbox["b"]))
            )
    return out


def render_crop(
    page: fitz.Page,
    formula: Formula,
    target: Path,
    dpi: int = 300,
    source_margin: float = 1.0,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    r = (fitz.Rect(formula.bbox) + (-source_margin, -source_margin, source_margin, source_margin)) & page.rect
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), clip=r, alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    ImageOps.expand(image, border=12, fill="white").save(target)
    formula.crop = str(target)


def normalize_formula(latex: str) -> str:
    latex = latex.strip()
    latex = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", latex, flags=re.I).strip()
    display = re.search(r"\\\[(.*?)\\\]", latex, flags=re.S)
    if display:
        tail = latex[display.end():]
        latex = display.group(1).strip() + " " + tail.strip()
    elif re.fullmatch(r"\\\(.*\\\)", latex, flags=re.S):
        latex = latex[2:-2].strip()
    latex = re.sub(r"^\$+|\$+$", "", latex).strip()
    latex = re.sub(r"^\\\[|\\\]$", "", latex).strip()
    latex = re.sub(
        r"\\eqno\s*\\?\(?\s*([A-Za-z]?\d+(?:\.\d+)+)\s*\\?\)?\s*$",
        lambda match: rf"\tag{{{match.group(1)}}}",
        latex,
    )
    # FormulaNet normally emits the printed equation number as trailing text.
    tag = re.search(r"(?:\\q?quad\s*)?\\?\(\s*([A-Za-z]?\d+(?:\.\d+)+)\s*\\?\)\s*$", latex)
    if tag:
        latex = latex[: tag.start()].rstrip() + rf" \tag{{{tag.group(1)}}}"
    latex = re.sub(r"\\eqno\s*(?=\\tag\{)", "", latex)
    # The recognizers often spell short roman words one glyph at a time.
    # Collapsing spaces inside a pure-text command is semantics preserving.
    def collapse_roman(match: re.Match[str]) -> str:
        command, content = match.group(1), match.group(2)
        if re.fullmatch(r"[A-Za-z ]+", content) and " " in content:
            content = content.replace(" ", "")
        return rf"\{command}{{{content}}}"
    latex = re.sub(r"\\(mathrm|text)\{([^{}]*)\}", collapse_roman, latex)
    latex = latex.replace("\\;\\ k", "\\;k")
    return latex.strip()


def trim_recognizer_punctuation(latex: str, native: str) -> str:
    """Keep sentence punctuation in prose rather than inside generated math."""
    native = native.rstrip()
    if native and native[-1] not in ",.;:!?":
        latex = re.sub(r"[,.;:!?]+\s*$", "", latex).rstrip()
    return latex


def canonical_formula(latex: str) -> str:
    """Canonical token stream for conservative cross-model agreement."""
    text = normalize_formula(latex)
    text = re.sub(r"\\tag\{[^{}]*\}", "", text)
    text = re.sub(r"\\operatorname\{ln\}|\\mathrm\{ln\}", r"\\ln", text)
    text = re.sub(r"\\(?:cfrac|dfrac|tfrac)", r"\\frac", text)
    text = re.sub(r"\\(?:left|right|bigg?|Bigg?)", "", text)
    text = re.sub(r"\\(?:,|;|!|quad|qquad| )", "", text)
    text = re.sub(r"\{\\rm\s+([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:mathrm|mathbf|mathit|text)\{([^{}]*)\}", r"\1", text)
    text = text.replace("~", "")
    return re.sub(r"\s+", "", text)


def agreement_score(a: str, b: str) -> float:
    ca, cb = canonical_formula(a), canonical_formula(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    return difflib.SequenceMatcher(None, ca, cb).ratio()


def comparable(text: str) -> str:
    text = clean_native(text).lower()
    replacements = {
        r"\\frac": "", r"\\mathrm": "", r"\\mathbf": "", r"\\mathit": "",
        r"\\operatorname": "", r"\\left": "", r"\\right": "", r"\\quad": "",
        r"\\cdot": "·", r"\\times": "×", r"\\rho": "ρ", r"\\pi": "π",
        r"\\delta": "δ", r"\\Delta": "Δ", r"\\mu": "μ", r"\\nu": "ν",
        r"\\lambda": "λ", r"\\theta": "θ", r"\\phi": "φ", r"\\sigma": "σ",
        r"\\omega": "ω", r"\\int": "∫", r"\\sum": "∑", r"\\sqrt": "√",
        r"\\infty": "∞", r"\\partial": "∂", r"\\tag": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\\[A-Za-z]+", "", text)
    return re.sub(r"[^a-z0-9αρπμνλφθστωζχβγδ∆Δ∂∞∫∑√=+\-×·./]", "", text)


def multiset_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ca, cb = collections.Counter(a), collections.Counter(b)
    common = sum((ca & cb).values())
    coverage = common / max(1, min(len(a), len(b)))
    return 0.55 * ratio + 0.45 * coverage


def validate_formula(latex: str, native: str, kind: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not latex:
        reasons.append("empty_latex")
        return False, reasons
    if latex.count("{") != latex.count("}"):
        reasons.append("unbalanced_braces")
    if re.search(r"\\(?:mathrm|mathbf|mathit|mathcal|operatorname)(?!\s*\{)", latex):
        reasons.append("malformed_text_command")
    if re.search(r"\\mathrm\d", latex):
        reasons.append("fragmented_text")
    if kind == "inline":
        plain_delimiters = re.sub(r"\\(?:left|right|bigg?|Bigg?)", "", latex)
        if plain_delimiters.count("(") != plain_delimiters.count(")"):
            reasons.append("unbalanced_parentheses")
        if plain_delimiters.count("[") != plain_delimiters.count("]"):
            reasons.append("unbalanced_brackets")
    if r"\cdot" in latex and not any(mark in native for mark in ("·", "⋅", "×")):
        reasons.append("unexpected_cdot")
    greek_requirements = {
        "β": (r"\beta",), "κ": (r"\kappa",), "ρ": (r"\rho",),
        "φ": (r"\phi", r"\varphi"), "ϕ": (r"\phi", r"\varphi"),
    }
    for glyph, commands in greek_requirements.items():
        if glyph in native and not any(command in latex for command in commands) and glyph not in latex:
            reasons.append(f"missing_greek:{glyph}")
    native_plain = comparable(native)
    latex_plain = comparable(latex)
    accent_marks = {
        "dot": ("˙", "̇"), "ddot": ("¨", "̈"), "bar": ("¯", "̄"),
        "hat": ("ˆ", "̂"), "vec": ("⃗", "→"), "tilde": ("˜", "̃"),
    }
    for accent, marks in accent_marks.items():
        if rf"\{accent}" in latex and accent not in native.lower() and not any(mark in native for mark in marks):
            reasons.append(f"unexpected_{accent}")
    try:
        latex_to_mathml(re.sub(r"\\tag\{[^{}]*\}\s*$", "", latex))
    except Exception as exc:  # latex2mathml has useful strictness for broken commands
        reasons.append(f"mathml:{type(exc).__name__}")
    score = multiset_score(native_plain, latex_plain)
    threshold = 0.42 if kind == "display" else 0.34
    if len(native_plain) <= 2:
        threshold = 0.25
    if score < threshold:
        reasons.append(f"native_similarity:{score:.3f}")
    native_numbers = collections.Counter(re.findall(r"\d+(?:\.\d+)?", clean_native(native)))
    latex_numbers = collections.Counter(re.findall(r"\d+(?:\.\d+)?", latex))
    missing_numbers = native_numbers - latex_numbers
    # Single digits are structural in fractions and exponents, while long
    # numbers are constants. Missing either is a strong error signal.
    if missing_numbers and kind == "display":
        reasons.append("missing_numbers:" + ",".join(sorted(missing_numbers.elements())))
    native_words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", clean_native(native))}
    missing_words = sorted(w for w in native_words if w not in latex_plain)
    if missing_words and kind == "display":
        reasons.append("missing_words:" + ",".join(missing_words[:5]))
    if len(latex) > max(300, len(clean_native(native)) * 8):
        reasons.append("excessive_output_length")
    native_tag = re.findall(r"\(([A-Za-z]?\d+(?:\.\d+)+)\)", clean_native(native))
    latex_tag = re.findall(r"\\tag\{([^{}]+)\}", latex)
    if kind == "display" and native_tag and native_tag[-1] not in latex_tag:
        reasons.append(f"missing_or_wrong_tag:{native_tag[-1]}")
    prose_words = re.findall(r"\b[A-Za-z]{4,}\b", clean_native(native))
    if kind == "display" and len(prose_words) >= 3:
        reasons.append("crop_contains_prose")
    tags = re.findall(r"\\tag\{([^{}]+)\}|\\?\((\d+(?:\.\d+)+)\)", latex)
    flat_tags = [a or b for a, b in tags]
    if len(flat_tags) >= 3 and len(set(flat_tags)) < len(flat_tags) / 2:
        reasons.append("repeated_equation_number")
    return not reasons, reasons


def select_existing_result(formula: Formula) -> None:
    """Re-run validation on checkpointed recognition without inference."""
    if formula.latex_unirec or formula.latex_l:
        resolve_ensemble(formula)
        return
    candidates = [
        (formula.latex_s, "PP-FormulaNet_plus-S"),
        (formula.latex_m, "PP-FormulaNet_plus-M"),
    ]
    last_latex = formula.latex_m or formula.latex_s
    last_model = "PP-FormulaNet_plus-M" if formula.latex_m else "PP-FormulaNet_plus-S"
    last_reasons: list[str] = ["empty_latex"]
    for raw, model in candidates:
        if not raw:
            continue
        latex = normalize_formula(raw)
        valid, reasons = validate_formula(latex, formula.native_text, formula.kind)
        last_latex, last_model, last_reasons = latex, model, reasons
        if valid:
            formula.latex, formula.model = latex, model
            formula.valid, formula.validation, formula.review = True, [], False
            return
    formula.latex, formula.model = last_latex, last_model
    formula.valid, formula.validation, formula.review = False, last_reasons, True


def resolve_ensemble(formula: Formula) -> None:
    unirec = trim_recognizer_punctuation(normalize_formula(formula.latex_unirec), formula.native_text)
    paddle_l = trim_recognizer_punctuation(normalize_formula(formula.latex_l), formula.native_text)
    formula.latex_unirec, formula.latex_l = unirec, paddle_l
    valid_u, reasons_u = validate_formula(unirec, formula.native_text, formula.kind)
    valid_l, reasons_l = validate_formula(paddle_l, formula.native_text, formula.kind)
    formula.agreement = round(agreement_score(unirec, paddle_l), 4)
    # A high edit ratio can conceal a one-symbol thermodynamic error (for
    # example p versus phi). Automatic acceptance therefore requires exact
    # equality after removing only presentation-level LaTeX differences.
    threshold = 0.9999
    reasons: list[str] = []
    if not valid_u:
        reasons.extend("unirec:" + reason for reason in reasons_u)
    if not valid_l:
        reasons.extend("paddle_l:" + reason for reason in reasons_l)
    if formula.agreement < threshold:
        reasons.append(f"model_disagreement:{formula.agreement:.3f}")
    formula.latex = unirec or paddle_l
    formula.model = "UniRec-0.1B+PP-FormulaNet_plus-L"
    formula.valid = not reasons
    formula.validation = reasons
    formula.review = bool(reasons)


class UniRecClient:
    PREFIX = "@@UNIREC_RESULT@@"

    def __init__(self, python: Path, worker: Path, model_dir: Path):
        self.process = subprocess.Popen(
            [str(python), "-u", str(worker), "--model-dir", str(model_dir), "--max-length", "2048"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._read_result(expect_ready=True)

    def _read_result(self, expect_ready: bool = False) -> dict[str, Any]:
        assert self.process.stdout is not None
        diagnostics: collections.deque[str] = collections.deque(maxlen=20)
        while True:
            line = self.process.stdout.readline()
            if not line:
                detail = "".join(diagnostics).strip()
                raise RuntimeError(f"UniRec worker exited with code {self.process.poll()}: {detail}")
            if line.startswith(self.PREFIX):
                payload = json.loads(line[len(self.PREFIX):])
                if expect_ready and not payload.get("ready"):
                    raise RuntimeError(f"UniRec worker did not become ready: {payload}")
                return payload
            diagnostics.append(line)

    def predict(self, path: str, request_id: int) -> str:
        assert self.process.stdin is not None
        request = {"id": request_id, "path": str(Path(path).resolve()), "max_length": 2048}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        payload = self._read_result()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return str(payload.get("result", ""))

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()


class Recognizer:
    def __init__(
        self,
        cache: Path,
        threads: int = 8,
        unirec_python: Path | None = None,
        unirec_model_dir: Path | None = None,
    ):
        cache.mkdir(parents=True, exist_ok=True)
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache.resolve())
        os.environ["PADDLE_PDX_MODEL_SOURCE"] = "BOS"
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        os.environ.setdefault("PADDLE_PDX_CPU_NUM_THREADS", str(threads))
        self.threads = threads
        self._l = None
        python_path = unirec_python or Path(".venv-formula-unirec/bin/python")
        if not python_path.is_absolute():
            python_path = Path.cwd() / python_path
        self.unirec = UniRecClient(
            python_path,
            Path(__file__).with_name("unirec_worker.py").resolve(),
            (unirec_model_dir or Path(".cache/openocr/unirec_0_1b_onnx")).resolve(),
        )

    def model(self, name: str):
        from paddleocr import FormulaRecognition
        value = self._l
        if value is None:
            value = FormulaRecognition(model_name=name, device="cpu", cpu_threads=self.threads)
            self._l = value
        return value

    @staticmethod
    def _predict(model: Any, formulas: list[Formula], field: str) -> None:
        if not formulas:
            return
        paths = [f.crop for f in formulas]
        for formula, result in zip(formulas, model.predict(input=paths, batch_size=1)):
            value = result.json["res"]["rec_formula"]
            setattr(formula, field, normalize_formula(value))

    def recognize(self, formulas: list[Formula]) -> None:
        for idx, formula in enumerate(formulas):
            formula.latex_unirec = normalize_formula(self.unirec.predict(formula.crop, idx))
        self._predict(self.model("PP-FormulaNet_plus-L"), formulas, "latex_l")
        for formula in formulas:
            resolve_ensemble(formula)

    def close(self) -> None:
        self.unirec.close()


def page_markdown(page_no: int, lines: list[dict[str, Any]], formulas: list[Formula], review_dir: Path) -> str:
    displays = sorted((f for f in formulas if f.kind == "display"), key=lambda f: (f.bbox[1], f.bbox[0]))
    inlines = collections.defaultdict(list)
    for f in formulas:
        if f.kind == "inline" and f.line_index is not None:
            inlines[f.line_index].append(f)

    items: list[tuple[float, float, str]] = []
    review_link_root = Path(review_dir.parent.name) / review_dir.name
    placed_inline: set[str] = set()
    display_rects = [fitz.Rect(f.bbox) for f in displays]
    for li, line in enumerate(lines):
        rect = fitz.Rect(line["bbox"])
        if any(overlap(rect, r) > 0.10 for r in display_rects):
            continue
        text = line["text"]
        replacements = sorted(inlines.get(li, []), key=lambda f: f.span_start or 0, reverse=True)
        for f in replacements:
            start, end = f.span_start or 0, f.span_end or 0
            if f.review:
                review_name = Path(f.crop).name
                repl = f"[FORMULA_REVIEW_REQUIRED]({review_link_root.as_posix()}/{review_name})"
            else:
                repl = f"${f.latex}$"
            text = text[:start] + repl + text[end:]
            placed_inline.add(Path(f.crop).name)
        text = text.strip()
        if text:
            items.append((rect.y0, rect.x0, text))
    for f in displays:
        if f.review:
            review_name = Path(f.crop).name
            rendered = f"![FORMULA_REVIEW_REQUIRED]({review_link_root.as_posix()}/{review_name})\n\n<!-- FORMULA_REVIEW_REQUIRED -->"
        else:
            rendered = f"$$\n{f.latex}\n$$"
        items.append((f.bbox[1], f.bbox[0], rendered))
    unplaced = [f for f in formulas if f.kind == "inline" and Path(f.crop).name not in placed_inline]
    for offset, f in enumerate(unplaced, 1):
        name = Path(f.crop).name
        if f.review:
            rendered = (
                f"[FORMULA_REVIEW_REQUIRED]({review_link_root.as_posix()}/{name}) "
                f"<!-- UNPLACED_INLINE_FORMULA page={page_no} -->"
            )
        else:
            rendered = f"${f.latex}$ <!-- UNPLACED_INLINE_FORMULA page={page_no} -->"
        items.append((10_000.0 + offset, 0.0, rendered))
    items.sort(key=lambda x: (round(x[0], 1), x[1]))
    body = "\n\n".join(item[2] for item in items)
    return f"<!-- PDF_PAGE: {page_no} -->\n\n# PDF 第 {page_no} 页\n\n{body.strip()}\n"


def run_docling(
    pdf: Path, output_dir: Path, cache_dir: Path, docling: Path, page_range: str | None = None
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(docling), "convert", str(pdf), "--from", "pdf", "--to", "json",
        "--pipeline", "standard", "--no-ocr", "--no-tables", "--no-enrich-formula",
        "--device", "cpu", "--num-threads", "4", "--page-batch-size", "4",
        "--output", str(output_dir), "--quiet",
    ]
    if page_range:
        command.extend(["--page-range", page_range])
    env = os.environ.copy()
    env["HF_HOME"] = str((cache_dir / "huggingface").resolve())
    env["XDG_CACHE_HOME"] = str((cache_dir / "docling").resolve())
    subprocess.run(command, check=True, env=env)
    target = output_dir / f"{pdf.stem}.json"
    if not target.exists():
        raise RuntimeError(f"Docling did not produce {target}")
    return target


def prepare_page(
    doc: fitz.Document,
    page_no: int,
    display_boxes: list[fitz.Rect],
    exclusion_boxes: list[fitz.Rect],
    crop_dir: Path,
    include_inline: bool,
) -> tuple[list[dict[str, Any]], list[Formula]]:
    page = doc[page_no - 1]
    lines = page_lines(page)
    formulas: list[Formula] = []
    for idx, box in enumerate(display_boxes, 1):
        box = refine_display_bbox(page, expand_display_bbox(page, box, lines), lines)
        native = clean_native(page.get_text("text", clip=box, sort=True))
        formula = Formula(page_no, "display", tuple(box), native)
        render_crop(page, formula, crop_dir / f"p{page_no:04d}-display-{idx:03d}.png")
        formulas.append(formula)
    if include_inline:
        inline = detect_inline(lines, formulas, page_no, exclusion_boxes)
        for idx, formula in enumerate(inline, 1):
            render_crop(page, formula, crop_dir / f"p{page_no:04d}-inline-{idx:03d}.png", source_margin=0.0)
        formulas.extend(inline)
    return lines, formulas


def copy_reviews(formulas: Iterable[Formula], review_dir: Path) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    for f in formulas:
        if f.review:
            shutil.copy2(f.crop, review_dir / Path(f.crop).name)


def process_pages(
    pdf: Path,
    page_numbers: list[int],
    display_map: dict[int, list[fitz.Rect]],
    exclusion_map: dict[int, list[fitz.Rect]],
    work_dir: Path,
    review_dir: Path,
    recognizer: Recognizer,
    include_inline: bool = True,
    retry_reviews: bool = False,
    refresh_inline: bool = False,
) -> tuple[list[str], list[Formula], dict[str, float]]:
    start = time.monotonic()
    doc = fitz.open(pdf)
    markdown: list[str] = []
    all_formulas: list[Formula] = []
    checkpoint_dir = work_dir / "checkpoints"
    crop_dir = work_dir / "crops"
    for page_no in page_numbers:
        checkpoint = checkpoint_dir / f"page-{page_no:04d}.json"
        page_md = checkpoint_dir / f"page-{page_no:04d}.md"
        if checkpoint.exists() and page_md.exists():
            data = json.loads(checkpoint.read_text(encoding="utf-8"))
            restored = [Formula(**x) for x in data["formulas"]]
            for formula in restored:
                select_existing_result(formula)
                stale_review = review_dir / Path(formula.crop).name
                if not formula.review and stale_review.exists():
                    stale_review.unlink()
            page = doc[page_no - 1]
            lines = page_lines(page)
            if include_inline and (refresh_inline or not data.get("include_inline", False)):
                displays = [formula for formula in restored if formula.kind == "display"]
                restored = displays
                inline = detect_inline(lines, displays, page_no, exclusion_map.get(page_no, []))
                for idx, formula in enumerate(inline, 1):
                    render_crop(
                        page, formula, crop_dir / f"p{page_no:04d}-inline-{idx:03d}.png", source_margin=0.0
                    )
                recognizer.recognize(inline)
                restored.extend(inline)
            if retry_reviews:
                retry = [f for f in restored if f.review and f.kind == "display"]
                for formula in retry:
                    box = refine_display_bbox(page, fitz.Rect(formula.bbox), lines)
                    formula.bbox = tuple(box)
                    formula.native_text = clean_native(page.get_text("text", clip=box, sort=True))
                    render_crop(page, formula, Path(formula.crop))
                    formula.latex_s = formula.latex_m = ""
                    formula.latex_unirec = formula.latex_l = formula.latex = ""
                    formula.agreement = 0.0
                    formula.valid = formula.review = False
                    formula.validation = []
                recognizer.recognize(retry)
            copy_reviews(restored, review_dir)
            rendered = page_markdown(page_no, lines, restored, review_dir)
            atomic_text(page_md, rendered)
            atomic_json(checkpoint, {
                "page": page_no,
                "status": "ok",
                "include_inline": include_inline or data.get("include_inline", False),
                "formulas": [f.to_dict() for f in restored],
            })
            markdown.append(rendered)
            all_formulas.extend(restored)
            continue
        lines, formulas = prepare_page(
            doc, page_no, display_map.get(page_no, []), exclusion_map.get(page_no, []), crop_dir, include_inline
        )
        recognizer.recognize(formulas)
        copy_reviews(formulas, review_dir)
        rendered = page_markdown(page_no, lines, formulas, review_dir)
        atomic_text(page_md, rendered)
        atomic_json(checkpoint, {
            "page": page_no,
            "status": "ok",
            "include_inline": include_inline,
            "formulas": [f.to_dict() for f in formulas],
        })
        markdown.append(rendered)
        all_formulas.extend(formulas)
        print(
            f"page={page_no} display={sum(f.kind == 'display' for f in formulas)} "
            f"inline={sum(f.kind == 'inline' for f in formulas)} review={sum(f.review for f in formulas)}",
            flush=True,
        )
    doc.close()
    return markdown, all_formulas, {"page_processing_seconds": round(time.monotonic() - start, 3)}


def build_report(
    *, pdf: Path, output: Path, pages: list[int], formulas: list[Formula], timings: dict[str, float], status: str
) -> dict[str, Any]:
    count = lambda pred: sum(1 for f in formulas if pred(f))
    return {
        "input": str(pdf.resolve()),
        "output": str(output.resolve()),
        "status": status,
        "backend": "native-text + docling-layout-no-ocr + UniRec-0.1B/PP-FormulaNet_plus-L-local-ensemble",
        "created_at": now_iso(),
        "pages_expected": len(pages),
        "page_numbers": pages,
        "page_markers": len(pages),
        "formulas_detected": len(formulas),
        "display_formulas": count(lambda f: f.kind == "display"),
        "inline_formulas": count(lambda f: f.kind == "inline"),
        "successful_latex": count(lambda f: f.valid),
        "unirec_predictions": count(lambda f: bool(f.latex_unirec)),
        "paddle_l_predictions": count(lambda f: bool(f.latex_l)),
        "ensemble_agreements": count(lambda f: f.valid and f.model.startswith("UniRec")),
        "model_disagreements": count(
            lambda f: any(reason.startswith("model_disagreement") for reason in f.validation)
        ),
        "review_required": count(lambda f: f.review),
        "failed_pages": sorted({f.page for f in formulas if f.review}),
        "timings": timings,
        "network_policy": "models/packages may be downloaded; PDF pages are never uploaded",
        "formula_records": [f.to_dict() for f in formulas],
    }


def apply_manual_audit(
    audit_path: Path,
    formulas: list[Formula],
    review_dir: Path,
) -> dict[str, Any]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    decisions: dict[str, str] = {}
    for section in ("incorrect_numbered_regions", "incorrect_unnumbered_or_reference_regions"):
        decisions.update(audit.get(section, {}))
    by_name = {Path(f.crop).name: f for f in formulas}
    missing = sorted(set(decisions) - set(by_name))
    if missing:
        raise RuntimeError(f"Manual audit refers to unknown crops: {missing}")
    for name, reason in decisions.items():
        formula = by_name[name]
        formula.valid = False
        formula.review = True
        marker = "manual_audit:" + reason
        if marker not in formula.validation:
            formula.validation.append(marker)
    inline_reviewed = 0
    if audit.get("review_all_inline"):
        reason = audit.get("inline_reason", "manual audit requires inline review")
        marker = "manual_audit:" + reason
        for formula in formulas:
            if formula.kind != "inline":
                continue
            formula.valid = False
            formula.review = True
            if marker not in formula.validation:
                formula.validation.append(marker)
            inline_reviewed += 1

    verified_sections = {
        "accepted_display_verified": "display",
        "accepted_inline_verified": "inline",
    }
    verified_counts: dict[str, int] = {}
    for section, expected_kind in verified_sections.items():
        names = audit.get(section, [])
        unknown = sorted(set(names) - set(by_name))
        if unknown:
            raise RuntimeError(f"Manual audit {section} refers to unknown crops: {unknown}")
        wrong_kind = sorted(name for name in names if by_name[name].kind != expected_kind)
        if wrong_kind:
            raise RuntimeError(f"Manual audit {section} has wrong-kind crops: {wrong_kind}")
        not_accepted = sorted(name for name in names if not by_name[name].valid)
        if not_accepted:
            raise RuntimeError(f"Manual audit {section} includes non-accepted crops: {not_accepted}")
        verified_counts[section] = len(names)

    copy_reviews(formulas, review_dir)
    numbered = audit.get("incorrect_numbered_regions", {})
    return {
        "path": str(audit_path.resolve()),
        "result": audit.get("result", "unknown"),
        "incorrect_numbered_regions": len(numbered),
        "incorrect_additional_regions": len(audit.get("incorrect_unnumbered_or_reference_regions", {})),
        "inline_regions_conservatively_reviewed": inline_reviewed,
        "failure_types": audit.get("failure_types", []),
        "runtime_benchmark": audit.get("runtime_benchmark"),
        "verification_method": audit.get("verification_method"),
        "accepted_display_verified": verified_counts["accepted_display_verified"],
        "accepted_inline_verified": verified_counts["accepted_inline_verified"],
        "known_old_failures_flagged": audit.get("known_old_failures_flagged"),
        "known_old_failures_corrected_and_accepted": audit.get(
            "known_old_failures_corrected_and_accepted"
        ),
        "missing_crops": missing,
    }


def rerender_pages(pdf: Path, pages: list[int], formulas: list[Formula], review_dir: Path) -> list[str]:
    by_page: dict[int, list[Formula]] = collections.defaultdict(list)
    for formula in formulas:
        by_page[formula.page].append(formula)
    doc = fitz.open(pdf)
    try:
        return [page_markdown(page, page_lines(doc[page - 1]), by_page[page], review_dir) for page in pages]
    finally:
        doc.close()


def cmd_doctor(args: argparse.Namespace) -> int:
    import paddle
    import paddleocr
    status = {
        "paddle": paddle.__version__,
        "paddleocr": paddleocr.__version__,
        "device": paddle.device.get_device(),
        "docling": Path(args.docling).exists(),
        "l_model_cached": (Path(args.model_cache) / "official_models" / "PP-FormulaNet_plus-L").exists(),
        "unirec_python": Path(args.unirec_python).exists(),
        "unirec_model_cached": all(
            (Path(args.unirec_model_dir) / name).exists()
            for name in ("unirec_encoder.onnx", "unirec_decoder.onnx", "unirec_tokenizer_mapping.json")
        ),
        "local_only": True,
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if all((status["docling"], status["l_model_cached"], status["unirec_python"], status["unirec_model_cached"])) else 1


def cmd_test(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf).resolve()
    root = Path(args.work_dir).resolve()
    docling_json = Path(args.docling_json).resolve()
    output = Path(args.output).resolve()
    review_dir = Path(args.review_dir).resolve()
    start = time.monotonic()
    display_map = load_docling_formulas(docling_json, SAMPLE_PAGES)
    exclusion_map = load_docling_exclusions(docling_json, SAMPLE_PAGES)
    layout_elapsed = 0.0
    recognizer = Recognizer(
        Path(args.model_cache), args.threads, Path(args.unirec_python), Path(args.unirec_model_dir)
    )
    try:
        md, formulas, timings = process_pages(
            pdf, SAMPLE_PAGES, display_map, exclusion_map, root, review_dir, recognizer,
            include_inline=not args.no_inline, retry_reviews=args.retry_reviews,
            refresh_inline=args.refresh_inline,
        )
    finally:
        recognizer.close()
    pre_audit_display_rate = sum(f.valid for f in formulas if f.kind == "display") / max(
        1, sum(f.kind == "display" for f in formulas)
    )
    pre_audit_inline_rate = sum(f.valid for f in formulas if f.kind == "inline") / max(
        1, sum(f.kind == "inline" for f in formulas)
    )
    manual_audit = None
    if args.manual_audit:
        manual_audit = apply_manual_audit(Path(args.manual_audit).resolve(), formulas, review_dir)
        md = rerender_pages(pdf, SAMPLE_PAGES, formulas, review_dir)
    atomic_text(output, "\n".join(md))
    timings["layout_seconds"] = layout_elapsed
    timings["total_seconds"] = round(time.monotonic() - start, 3)
    status = "sample_failed" if manual_audit and manual_audit["result"] == "fail" else "sample_complete"
    report = build_report(pdf=pdf, output=output, pages=SAMPLE_PAGES, formulas=formulas, timings=timings, status=status)
    report["sample_thresholds"] = {
        "display_min": 0.95,
        "inline_min": 0.90,
        "display_pre_audit_auto_rate": round(pre_audit_display_rate, 4),
        "inline_pre_audit_auto_rate": round(pre_audit_inline_rate, 4),
        "manual_review_pending": True,
    }
    if manual_audit:
        report["manual_audit"] = manual_audit
        report["sample_thresholds"]["manual_review_pending"] = False
        report["sample_thresholds"]["display_post_audit_rate"] = round(
            sum(f.valid for f in formulas if f.kind == "display") / max(1, sum(f.kind == "display" for f in formulas)), 4
        )
        report["sample_thresholds"]["inline_post_audit_rate"] = round(
            sum(f.valid for f in formulas if f.kind == "inline") / max(1, sum(f.kind == "inline" for f in formulas)), 4
        )
        report["sample_thresholds"]["gate_passed"] = False
        report["full_book_processing_authorized"] = False
    atomic_json(output.with_suffix(output.suffix + ".report.json"), report)
    print(json.dumps({k: v for k, v in report.items() if k != "formula_records"}, ensure_ascii=False, indent=2))
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    gate_path = Path(args.gate_report).resolve()
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if not gate.get("full_book_processing_authorized", False):
        print(
            json.dumps(
                {
                    "status": "blocked_by_sample_gate",
                    "gate_report": str(gate_path),
                    "message": (
                        "Full-book processing requires a manually audited sample report with "
                        "full_book_processing_authorized=true."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    pdf = Path(args.pdf).resolve()
    output = Path(args.output).resolve()
    root = Path(args.work_dir).resolve()
    review_dir = Path(args.review_dir).resolve()
    cache_dir = Path(args.docling_cache).resolve()
    total = fitz.open(pdf).page_count
    pages = list(range(1, total + 1))
    recognizer = Recognizer(
        Path(args.model_cache), args.threads, Path(args.unirec_python), Path(args.unirec_model_dir)
    )
    all_md: list[str] = []
    all_formulas: list[Formula] = []
    timings = {"layout_seconds": 0.0, "page_processing_seconds": 0.0}
    started = time.monotonic()
    try:
        for batch_start in range(1, total + 1, args.batch_size):
            batch_end = min(total, batch_start + args.batch_size - 1)
            batch_pages = list(range(batch_start, batch_end + 1))
            layout_dir = root / "layout" / f"pages-{batch_start:04d}-{batch_end:04d}"
            layout_json = layout_dir / f"{pdf.stem}.json"
            t0 = time.monotonic()
            if not layout_json.exists():
                layout_json = run_docling(
                    pdf, layout_dir, cache_dir, Path(args.docling), f"{batch_start}-{batch_end}"
                )
            timings["layout_seconds"] += time.monotonic() - t0
            display_map = load_docling_formulas(layout_json)
            exclusion_map = load_docling_exclusions(layout_json)
            md, formulas, page_timing = process_pages(
                pdf, batch_pages, display_map, exclusion_map, root, review_dir, recognizer,
                include_inline=not args.no_inline,
            )
            all_md.extend(md)
            all_formulas.extend(formulas)
            timings["page_processing_seconds"] += page_timing["page_processing_seconds"]
            progress = batch_end / total
            if any(abs(progress - mark) < args.batch_size / total for mark in (0.25, 0.5, 0.75, 1.0)):
                print(f"checkpoint pages={batch_end}/{total} progress={progress:.1%}", flush=True)
            partial = build_report(
                pdf=pdf, output=output, pages=list(range(1, batch_end + 1)), formulas=all_formulas,
                timings={k: round(v, 3) for k, v in timings.items()}, status="running",
            )
            atomic_json(output.with_suffix(output.suffix + ".report.json"), partial)
    finally:
        recognizer.close()
    atomic_text(output, "\n".join(all_md))
    timings["total_seconds"] = round(time.monotonic() - started, 3)
    timings = {k: round(v, 3) for k, v in timings.items()}
    report = build_report(pdf=pdf, output=output, pages=pages, formulas=all_formulas, timings=timings, status="ok")
    atomic_json(output.with_suffix(output.suffix + ".report.json"), report)
    print(json.dumps({k: v for k, v in report.items() if k != "formula_records"}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--docling", default=".venv-docling/bin/docling")
    doctor.add_argument("--model-cache", default=".cache/paddlex")
    doctor.add_argument("--unirec-python", default=".venv-formula-unirec/bin/python")
    doctor.add_argument("--unirec-model-dir", default=".cache/openocr/unirec_0_1b_onnx")
    doctor.set_defaults(func=cmd_doctor)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("pdf")
    common.add_argument("-o", "--output", required=True)
    common.add_argument("--work-dir", default="formula-hybrid/work/run")
    common.add_argument("--review-dir", default="formula-assets/review")
    common.add_argument("--model-cache", default=".cache/paddlex")
    common.add_argument("--unirec-python", default=".venv-formula-unirec/bin/python")
    common.add_argument("--unirec-model-dir", default=".cache/openocr/unirec_0_1b_onnx")
    common.add_argument("--threads", type=int, default=8)
    common.add_argument("--no-inline", action="store_true")

    test = sub.add_parser("test", parents=[common])
    test.add_argument("--docling-json", required=True)
    test.add_argument("--retry-reviews", action="store_true")
    test.add_argument("--refresh-inline", action="store_true")
    test.add_argument("--manual-audit")
    test.set_defaults(func=cmd_test)

    process = sub.add_parser("process", parents=[common])
    process.add_argument("--docling", default=".venv-docling/bin/docling")
    process.add_argument("--docling-cache", default=".cache/pdf2md")
    process.add_argument("--batch-size", type=int, default=25)
    process.add_argument(
        "--gate-report",
        required=True,
        help="manually audited sample report authorizing full-book processing",
    )
    process.set_defaults(func=cmd_process)
    return p


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
