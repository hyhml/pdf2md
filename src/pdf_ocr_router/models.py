from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


Box = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    box: Box = ()


@dataclass(frozen=True)
class OCRPage:
    number: int
    width: int
    height: int
    lines: tuple[OCRLine, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(line.text.strip() for line in self.lines if line.text.strip())


@dataclass(frozen=True)
class OCRDocument:
    pages: tuple[OCRPage, ...]
    backend: str = "rapidocr"

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages).strip() + "\n"


@dataclass(frozen=True)
class QualityReport:
    accepted: bool
    reasons: tuple[str, ...]
    pages: int
    text_coverage: float
    median_chars_per_page: float
    valid_char_ratio: float
    replacement_char_ratio: float
    weighted_confidence: float | None = None
    low_confidence_char_ratio: float | None = None
    complex_layout: bool = False
    layout_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class RouterConfig:
    native_min_chars_per_page: int = 30
    native_min_coverage: float = 0.80
    native_min_valid_char_ratio: float = 0.90
    native_max_replacement_ratio: float = 0.01
    ocr_min_chars_per_page: int = 20
    ocr_min_coverage: float = 0.75
    ocr_min_confidence: float = 0.86
    ocr_low_confidence_cutoff: float = 0.75
    ocr_max_low_confidence_char_ratio: float = 0.20
    ocr_min_valid_char_ratio: float = 0.88
    route_complex_layout: bool = True
    render_dpi: int = 220
    strong_tier: str = "standard"


@dataclass
class RunAudit:
    input: str
    output: str
    requested_mode: str
    selected_stage: str | None = None
    status: str = "running"
    reasons: list[str] = field(default_factory=list)
    native_quality: dict[str, Any] | None = None
    light_quality: dict[str, Any] | None = None
    fallback_output: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
