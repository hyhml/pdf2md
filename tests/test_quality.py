from pdf_ocr_router.models import OCRDocument, OCRLine, OCRPage, RouterConfig
from pdf_ocr_router.quality import assess_native_text, assess_ocr, detect_complex_layout


def test_good_native_text_passes():
    pages = ["这是一个正常的中文文本层。" * 8, "第二页也有足够多的正文内容。" * 8]
    report = assess_native_text(pages, RouterConfig())
    assert report.accepted


def test_broken_native_text_fails():
    pages = ["\ufffd\ufffd\ufffd", ""]
    report = assess_native_text(pages, RouterConfig())
    assert not report.accepted
    assert report.replacement_char_ratio > 0


def test_high_confidence_ocr_passes():
    line = OCRLine("这是一行可信的中文识别结果，用来验证质量门槛。", 0.97, ((10, 10), (500, 10), (500, 40), (10, 40)))
    document = OCRDocument((OCRPage(1, 1000, 1400, (line, line)),))
    report = assess_ocr(document, RouterConfig())
    assert report.accepted
    assert report.weighted_confidence == 0.97


def test_low_confidence_ocr_fails():
    line = OCRLine("模糊不清的识别结果也不能被直接当作成功输出。", 0.61)
    document = OCRDocument((OCRPage(1, 1000, 1400, (line, line)),))
    report = assess_ocr(document, RouterConfig())
    assert not report.accepted
    assert "OCR 平均置信度不足" in report.reasons


def test_two_columns_are_detected():
    lines = []
    for row in range(6):
        y = 50 + row * 80
        lines.append(OCRLine("左栏正文内容足够长", 0.98, ((50, y), (400, y), (400, y + 30), (50, y + 30))))
        lines.append(OCRLine("右栏正文内容足够长", 0.98, ((600, y), (950, y), (950, y + 30), (600, y + 30))))
    document = OCRDocument((OCRPage(1, 1000, 1400, tuple(lines)),))
    complex_layout, reasons = detect_complex_layout(document)
    assert complex_layout
    assert "检测到疑似多栏排版" in reasons
