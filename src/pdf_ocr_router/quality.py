from __future__ import annotations

import math
import statistics
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence

from .models import OCRDocument, OCRLine, OCRPage, QualityReport, RouterConfig


def _visible_chars(text: str) -> list[str]:
    return [char for char in text if not char.isspace()]


def _valid_char_ratio(text: str) -> float:
    chars = _visible_chars(text)
    if not chars:
        return 0.0
    valid = 0
    for char in chars:
        category = unicodedata.category(char)
        if char != "\ufffd" and category not in {"Cc", "Cs", "Co", "Cn"}:
            valid += 1
    return valid / len(chars)


def _replacement_ratio(text: str) -> float:
    chars = _visible_chars(text)
    return text.count("\ufffd") / len(chars) if chars else 0.0


def _median(values: Sequence[int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def assess_native_text(pages: Sequence[str], config: RouterConfig) -> QualityReport:
    counts = [len(_visible_chars(page)) for page in pages]
    combined = "\n".join(pages)
    coverage = sum(count >= config.native_min_chars_per_page for count in counts) / max(1, len(counts))
    valid_ratio = _valid_char_ratio(combined)
    replacement_ratio = _replacement_ratio(combined)

    reasons: list[str] = []
    if coverage < config.native_min_coverage:
        reasons.append("文本层覆盖不足")
    if _median(counts) < config.native_min_chars_per_page:
        reasons.append("每页有效文字过少")
    if valid_ratio < config.native_min_valid_char_ratio:
        reasons.append("文本层包含过多异常字符")
    if replacement_ratio > config.native_max_replacement_ratio:
        reasons.append("文本层含有过多替换字符")

    return QualityReport(
        accepted=not reasons,
        reasons=tuple(reasons),
        pages=len(pages),
        text_coverage=coverage,
        median_chars_per_page=_median(counts),
        valid_char_ratio=valid_ratio,
        replacement_char_ratio=replacement_ratio,
    )


def _box_bounds(line: OCRLine) -> tuple[float, float, float, float] | None:
    if not line.box:
        return None
    xs = [point[0] for point in line.box]
    ys = [point[1] for point in line.box]
    return min(xs), min(ys), max(xs), max(ys)


def _is_multicolumn(page: OCRPage) -> bool:
    bounded = [(line, _box_bounds(line)) for line in page.lines]
    bounded = [(line, bounds) for line, bounds in bounded if bounds is not None]
    if len(bounded) < 8 or page.width <= 0:
        return False

    left = right = crossing = 0
    for _line, (x1, _y1, x2, _y2) in bounded:
        center = (x1 + x2) / 2 / page.width
        crosses_middle = x1 / page.width < 0.45 and x2 / page.width > 0.55
        if crosses_middle:
            crossing += 1
        elif center < 0.45:
            left += 1
        elif center > 0.55:
            right += 1
    total = len(bounded)
    return left >= 4 and right >= 4 and crossing / total <= 0.25


def _is_table_like(page: OCRPage) -> bool:
    bounded = [(line, _box_bounds(line)) for line in page.lines]
    bounded = [(line, bounds) for line, bounds in bounded if bounds is not None]
    if len(bounded) < 12:
        return False

    heights = [max(1.0, bounds[3] - bounds[1]) for _line, bounds in bounded]
    row_height = max(8.0, statistics.median(heights) * 1.25)
    rows: Counter[int] = Counter()
    short = 0
    for line, bounds in bounded:
        y_center = (bounds[1] + bounds[3]) / 2
        rows[round(y_center / row_height)] += 1
        if len(_visible_chars(line.text)) <= 12:
            short += 1
    dense_rows = sum(count >= 3 for count in rows.values())
    return dense_rows >= 3 and short / len(bounded) >= 0.55


def detect_complex_layout(document: OCRDocument) -> tuple[bool, tuple[str, ...]]:
    reasons: set[str] = set()
    for page in document.pages:
        if _is_multicolumn(page):
            reasons.add("检测到疑似多栏排版")
        if _is_table_like(page):
            reasons.add("检测到疑似表格或表单")
    return bool(reasons), tuple(sorted(reasons))


def assess_ocr(document: OCRDocument, config: RouterConfig) -> QualityReport:
    counts = [len(_visible_chars(page.text)) for page in document.pages]
    combined = "\n".join(page.text for page in document.pages)
    coverage = sum(count >= config.ocr_min_chars_per_page for count in counts) / max(1, len(counts))

    weighted_score = 0.0
    total_weight = 0
    low_weight = 0
    for page in document.pages:
        for line in page.lines:
            weight = max(1, len(_visible_chars(line.text)))
            score = line.confidence if math.isfinite(line.confidence) else 0.0
            weighted_score += score * weight
            total_weight += weight
            if score < config.ocr_low_confidence_cutoff:
                low_weight += weight
    confidence = weighted_score / total_weight if total_weight else 0.0
    low_ratio = low_weight / total_weight if total_weight else 1.0
    valid_ratio = _valid_char_ratio(combined)
    replacement_ratio = _replacement_ratio(combined)
    complex_layout, layout_reasons = detect_complex_layout(document)

    reasons: list[str] = []
    if coverage < config.ocr_min_coverage:
        reasons.append("OCR 文字覆盖不足")
    if _median(counts) < config.ocr_min_chars_per_page:
        reasons.append("OCR 每页文字过少")
    if confidence < config.ocr_min_confidence:
        reasons.append("OCR 平均置信度不足")
    if low_ratio > config.ocr_max_low_confidence_char_ratio:
        reasons.append("低置信文字占比过高")
    if valid_ratio < config.ocr_min_valid_char_ratio:
        reasons.append("OCR 结果包含过多异常字符")
    if config.route_complex_layout and complex_layout:
        reasons.extend(layout_reasons)

    return QualityReport(
        accepted=not reasons,
        reasons=tuple(reasons),
        pages=len(document.pages),
        text_coverage=coverage,
        median_chars_per_page=_median(counts),
        valid_char_ratio=valid_ratio,
        replacement_char_ratio=replacement_ratio,
        weighted_confidence=confidence,
        low_confidence_char_ratio=low_ratio,
        complex_layout=complex_layout,
        layout_reasons=layout_reasons,
    )


def reading_order(lines: Iterable[OCRLine]) -> tuple[OCRLine, ...]:
    items = list(lines)
    heights = []
    for line in items:
        bounds = _box_bounds(line)
        if bounds:
            heights.append(max(1.0, bounds[3] - bounds[1]))
    band = max(8.0, (statistics.median(heights) if heights else 16.0) * 0.65)

    def key(line: OCRLine) -> tuple[int, float]:
        bounds = _box_bounds(line)
        if not bounds:
            return (10**9, 0.0)
        x1, y1, x2, y2 = bounds
        return round(((y1 + y2) / 2) / band), (x1 + x2) / 2

    return tuple(sorted(items, key=key))
