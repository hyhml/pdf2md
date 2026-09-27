"""Quality-gated PDF OCR router."""

from .models import OCRDocument, OCRLine, OCRPage, QualityReport, RouterConfig
from .router import PDFOCRRouter, ProcessResult

__all__ = [
    "OCRDocument",
    "OCRLine",
    "OCRPage",
    "PDFOCRRouter",
    "ProcessResult",
    "QualityReport",
    "RouterConfig",
]

__version__ = "0.2.0"
