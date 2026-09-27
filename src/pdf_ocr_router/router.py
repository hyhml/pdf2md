from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .backends import (
    BackendUnavailable,
    LightBackend,
    DoclingBackend,
    NativeBackend,
    PyPDFTextBackend,
    RapidOCRBackend,
    StrongBackend,
)
from .models import RunAudit, RouterConfig
from .quality import assess_native_text, assess_ocr


VALID_MODES = {"auto", "native", "light", "strong"}


@dataclass(frozen=True)
class ProcessResult:
    output_path: Path
    report_path: Path
    selected_stage: str
    status: str


class PDFOCRRouter:
    def __init__(
        self,
        config: RouterConfig | None = None,
        native_backend: NativeBackend | None = None,
        light_backend: LightBackend | None = None,
        strong_backend: StrongBackend | None = None,
    ) -> None:
        self.config = config or RouterConfig()
        self.native_backend = native_backend or PyPDFTextBackend()
        self.light_backend = light_backend or RapidOCRBackend()
        self.strong_backend = strong_backend or DoclingBackend()

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _write_report(path: Path, audit: RunAudit) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(audit.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def process(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        mode: str = "auto",
    ) -> ProcessResult:
        source = Path(input_path).expanduser().resolve()
        if mode not in VALID_MODES:
            raise ValueError(f"未知模式：{mode}")
        if not source.is_file():
            raise FileNotFoundError(f"找不到输入文件：{source}")
        if source.suffix.lower() != ".pdf":
            raise ValueError("当前工具包只接受 PDF 文件")

        target = (
            Path(output_path).expanduser().resolve()
            if output_path
            else source.with_suffix(".md")
        )
        report_path = target.with_suffix(target.suffix + ".report.json")
        audit = RunAudit(str(source), str(target), mode)
        light_document = None

        try:
            if mode in {"auto", "native"}:
                pages = self.native_backend.extract(source)
                native_quality = assess_native_text(pages, self.config)
                audit.native_quality = native_quality.to_dict()
                if mode == "native" or native_quality.accepted:
                    text = "\n\n".join(page.strip() for page in pages).strip()
                    self._write_text(target, text)
                    audit.selected_stage = "native"
                    audit.status = "ok" if native_quality.accepted else "forced"
                    audit.reasons.extend(native_quality.reasons)
                    self._write_report(report_path, audit)
                    return ProcessResult(target, report_path, "native", audit.status)
                audit.reasons.extend(native_quality.reasons)

            if mode in {"auto", "light"}:
                try:
                    light_document = self.light_backend.recognize(source, self.config.render_dpi)
                except BackendUnavailable as exc:
                    if mode == "light":
                        raise
                    audit.reasons.append(str(exc))
                else:
                    light_quality = assess_ocr(light_document, self.config)
                    audit.light_quality = light_quality.to_dict()
                    if mode == "light" or light_quality.accepted:
                        self._write_text(target, light_document.text)
                        audit.selected_stage = "light"
                        audit.status = "ok" if light_quality.accepted else "forced"
                        audit.reasons.extend(light_quality.reasons)
                        self._write_report(report_path, audit)
                        return ProcessResult(target, report_path, "light", audit.status)
                    audit.reasons.extend(light_quality.reasons)

            if mode in {"auto", "strong"}:
                if not self.strong_backend.available():
                    if light_document is not None:
                        fallback = target.with_name(target.stem + ".light" + target.suffix)
                        self._write_text(fallback, light_document.text)
                        audit.fallback_output = str(fallback)
                    raise BackendUnavailable(
                        "质量门槛要求升级到 Docling，但 Docling 尚未安装；"
                        "低质量候选结果已保留（如有）"
                    )
                self.strong_backend.convert(source, target, self.config.strong_tier)
                audit.selected_stage = "strong"
                audit.status = "ok"
                self._write_report(report_path, audit)
                return ProcessResult(target, report_path, "strong", "ok")

            raise RuntimeError("路由没有产生结果")
        except Exception as exc:
            audit.status = "error"
            audit.error = str(exc)
            self._write_report(report_path, audit)
            raise
