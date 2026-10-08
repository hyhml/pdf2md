from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from .models import OCRDocument, OCRLine, OCRPage
from .quality import reading_order


class BackendUnavailable(RuntimeError):
    pass


class BackendFailure(RuntimeError):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def docling_environment() -> dict[str, str]:
    env = os.environ.copy()
    configured_cache = env.get("PDF2MD_CACHE_DIR")
    cache_root = (
        Path(configured_cache).expanduser()
        if configured_cache
        else Path.cwd() / ".cache" / "pdf2md"
    ).resolve()
    env.setdefault("HF_HOME", str(cache_root / "huggingface"))
    env.setdefault("XDG_CACHE_HOME", str(cache_root / "docling"))
    return env


def find_executable(name: str) -> str | None:
    """Find a backend CLI on PATH or in the project's isolated Docling env."""
    resolved = shutil.which(name)
    if resolved:
        return resolved
    configured = os.environ.get("PDF_OCR_DOCLING_BIN")
    if configured:
        candidate = Path(configured).expanduser()
        candidate = candidate / name if candidate.is_dir() else candidate
        if candidate.is_file():
            return str(candidate.resolve())
    for candidate in (
        project_root() / ".venv-docling" / "bin" / name,
        project_root() / ".venv-docling" / "Scripts" / f"{name}.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


class NativeBackend(Protocol):
    def extract(self, input_path: Path) -> list[str]: ...


class LightBackend(Protocol):
    def recognize(self, input_path: Path, dpi: int) -> OCRDocument: ...


class StrongBackend(Protocol):
    def convert(self, input_path: Path, output_path: Path, tier: str) -> None: ...

    def available(self) -> bool: ...


class PyPDFTextBackend:
    def extract(self, input_path: Path) -> list[str]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise BackendUnavailable("缺少 pypdf；请安装本工具包的基础依赖") from exc

        reader = PdfReader(str(input_path))
        pages: list[str] = []
        for page in reader.pages:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                text = page.extract_text() or ""
            pages.append(text)
        return pages


class RapidOCRBackend:
    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise BackendUnavailable(
                "缺少轻量 OCR 依赖；请执行 pip install -e '.[light]'"
            ) from exc
        self._engine = RapidOCR()
        return self._engine

    def recognize(self, input_path: Path, dpi: int) -> OCRDocument:
        try:
            import numpy as np
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise BackendUnavailable(
                "缺少 PDF 渲染依赖；请执行 pip install -e '.[light]'"
            ) from exc

        engine = self._get_engine()
        pdf = pdfium.PdfDocument(str(input_path))
        pages: list[OCRPage] = []
        scale = dpi / 72.0
        try:
            for index in range(len(pdf)):
                page = pdf[index]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil().convert("RGB")
                rgb = np.asarray(image)
                bgr = np.ascontiguousarray(rgb[:, :, ::-1])
                result = engine(bgr, use_det=True, use_cls=True, use_rec=True)
                texts = tuple(getattr(result, "txts", ()) or ())
                scores = tuple(getattr(result, "scores", ()) or ())
                boxes = getattr(result, "boxes", None)
                box_list = boxes.tolist() if boxes is not None else []
                lines = []
                for position, text in enumerate(texts):
                    score = float(scores[position]) if position < len(scores) else 0.0
                    raw_box = box_list[position] if position < len(box_list) else []
                    box = tuple((float(point[0]), float(point[1])) for point in raw_box)
                    if str(text).strip():
                        lines.append(OCRLine(str(text).strip(), score, box))
                pages.append(
                    OCRPage(
                        number=index + 1,
                        width=int(rgb.shape[1]),
                        height=int(rgb.shape[0]),
                        lines=reading_order(lines),
                    )
                )
                page.close()
        finally:
            pdf.close()
        return OCRDocument(tuple(pages), backend="rapidocr-ppocrv6-small")


class DoclingBackend:
    def _docling(self) -> str:
        executable = find_executable("docling")
        if not executable:
            raise BackendUnavailable(
                "未找到 Docling；请执行 scripts/setup.sh --strong，"
                "或用 PDF_OCR_DOCLING_BIN 指定 docling 可执行文件"
            )
        return executable

    @staticmethod
    def _python_for(executable: str) -> str:
        binary = Path(executable).resolve()
        name = "python.exe" if binary.suffix.lower() == ".exe" else "python"
        candidate = binary.with_name(name)
        if candidate.is_file():
            return str(candidate)
        raise BackendUnavailable(f"Docling 环境中未找到 Python：{candidate}")

    def _command(self, input_path: Path, output_dir: Path) -> list[str]:
        return [
            self._docling(),
            "convert",
            str(input_path),
            "--from",
            "pdf",
            "--to",
            "json",
            "--pipeline",
            "standard",
            "--ocr",
            "--ocr-mode",
            "full_page",
            "--ocr-engine",
            "rapidocr",
            "--ocr-lang",
            "ch",
            "--tables",
            "--device",
            "cpu",
            "--image-export-mode",
            "placeholder",
            "--output",
            str(output_dir),
        ]

    def available(self) -> bool:
        executable = find_executable("docling")
        if not executable:
            return False
        try:
            self._python_for(executable)
        except BackendUnavailable:
            return False
        return True

    def convert(self, input_path: Path, output_path: Path, _tier: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        executable = self._docling()
        env = docling_environment()
        with tempfile.TemporaryDirectory(
            prefix=".pdf2md-docling-", dir=output_path.parent
        ) as temporary:
            temporary_dir = Path(temporary)
            completed = subprocess.run(
                self._command(input_path, temporary_dir),
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "未知错误").strip()
                raise BackendFailure(f"Docling 处理失败：{detail}")
            json_path = temporary_dir / f"{input_path.stem}.json"
            if not json_path.is_file():
                candidates = sorted(temporary_dir.glob("*.json"))
                if len(candidates) == 1:
                    json_path = candidates[0]
                else:
                    raise BackendFailure(
                        f"Docling 已退出，但没有生成唯一 JSON：{temporary_dir}"
                    )
            helper = project_root() / "scripts" / "docling_export.py"
            exported = subprocess.run(
                [
                    self._python_for(executable),
                    str(helper),
                    str(json_path),
                    str(output_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            if exported.returncode != 0:
                detail = (exported.stderr or exported.stdout or "未知错误").strip()
                raise BackendFailure(f"Docling Markdown 导出失败：{detail}")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise BackendFailure(f"Docling 没有生成有效 Markdown：{output_path}")
