from __future__ import annotations

import os
import shutil
import subprocess
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


def mineru_environment() -> dict[str, str]:
    env = os.environ.copy()
    root = project_root()
    local_home = root / ".mineru"
    if "MINERU_HOME" not in env and local_home.is_dir():
        env["MINERU_HOME"] = str(local_home)
    if env.get("MINERU_HOME") == str(local_home):
        env.setdefault("MODELSCOPE_CACHE", str(root / ".cache" / "modelscope"))
        env.setdefault("HF_HOME", str(root / ".cache" / "huggingface"))
    return env


def find_executable(name: str) -> str | None:
    """Find a backend CLI on PATH or in the project's isolated strong env."""
    resolved = shutil.which(name)
    if resolved:
        return resolved
    configured = os.environ.get("PDF_OCR_MINERU_BIN")
    if configured:
        candidate = Path(configured).expanduser()
        candidate = candidate / name if candidate.is_dir() else candidate
        if candidate.is_file():
            return str(candidate.resolve())
    for candidate in (
        project_root() / ".venv-mineru" / "bin" / name,
        project_root() / ".venv-mineru" / "Scripts" / f"{name}.exe",
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


class MinerUBackend:
    def _command(self, input_path: Path, output_path: Path, tier: str) -> list[str]:
        kit = find_executable("mineru-kit")
        if kit:
            return [kit, "parse", str(input_path), "-o", str(output_path), "--tier", tier]
        mineru = find_executable("mineru")
        if mineru:
            return [
                mineru,
                "parse",
                str(input_path),
                "--pages",
                "all",
                "-o",
                str(output_path),
                "--tier",
                tier,
            ]
        raise BackendUnavailable("未找到 MinerU 4.x；请先安装并确保 mineru-kit 或 mineru 在 PATH 中")

    def available(self) -> bool:
        return bool(find_executable("mineru-kit") or find_executable("mineru"))

    def convert(self, input_path: Path, output_path: Path, tier: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._command(input_path, output_path, tier)
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=mineru_environment(),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "未知错误").strip()
            raise BackendFailure(f"MinerU 处理失败：{detail}")
        if not output_path.exists():
            raise BackendFailure(f"MinerU 已退出，但没有生成预期文件：{output_path}")
