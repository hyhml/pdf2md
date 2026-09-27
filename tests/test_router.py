from pathlib import Path

from pdf_ocr_router.models import OCRDocument, OCRLine, OCRPage
from pdf_ocr_router.router import PDFOCRRouter


class FakeNative:
    def __init__(self, pages):
        self.pages = pages

    def extract(self, _input_path: Path):
        return self.pages


class FakeLight:
    def __init__(self, document):
        self.document = document
        self.calls = 0

    def recognize(self, _input_path: Path, _dpi: int):
        self.calls += 1
        return self.document


class FakeStrong:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = 0

    def available(self):
        return self.ready

    def convert(self, _input_path: Path, output_path: Path, _tier: str):
        self.calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# Docling result\n", encoding="utf-8")


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-fake")
    return path


def _good_ocr() -> OCRDocument:
    line = OCRLine("高置信度中文识别结果足够长，可以通过质量门槛。", 0.98)
    return OCRDocument((OCRPage(1, 1000, 1400, (line, line)),))


def test_router_uses_native_layer_first(tmp_path):
    light = FakeLight(_good_ocr())
    router = PDFOCRRouter(
        native_backend=FakeNative(["正常中文文本层。" * 12]),
        light_backend=light,
        strong_backend=FakeStrong(),
    )
    result = router.process(_pdf(tmp_path), tmp_path / "out.md")
    assert result.selected_stage == "native"
    assert light.calls == 0


def test_router_uses_light_ocr_for_scan(tmp_path):
    light = FakeLight(_good_ocr())
    strong = FakeStrong()
    router = PDFOCRRouter(
        native_backend=FakeNative([""]),
        light_backend=light,
        strong_backend=strong,
    )
    result = router.process(_pdf(tmp_path), tmp_path / "out.md")
    assert result.selected_stage == "light"
    assert light.calls == 1
    assert strong.calls == 0


def test_router_escalates_low_confidence_to_docling(tmp_path):
    weak_line = OCRLine("低置信度识别结果虽然有文字但不可靠。", 0.40)
    weak = OCRDocument((OCRPage(1, 1000, 1400, (weak_line, weak_line)),))
    strong = FakeStrong()
    router = PDFOCRRouter(
        native_backend=FakeNative([""]),
        light_backend=FakeLight(weak),
        strong_backend=strong,
    )
    result = router.process(_pdf(tmp_path), tmp_path / "out.md")
    assert result.selected_stage == "strong"
    assert strong.calls == 1
    assert (tmp_path / "out.md").read_text(encoding="utf-8").startswith("# Docling")
