from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .backends import (
    BackendFailure,
    BackendUnavailable,
    docling_environment,
    find_executable,
)
from .models import RouterConfig
from .router import PDFOCRRouter


def _config_from_args(args: argparse.Namespace) -> RouterConfig:
    config = RouterConfig()
    return replace(
        config,
        render_dpi=args.dpi,
        ocr_min_confidence=args.min_confidence,
        ocr_max_low_confidence_char_ratio=args.max_low_confidence,
        route_complex_layout=not args.no_layout_escalation,
        strong_tier=args.strong_tier,
    )


def _add_routing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("auto", "native", "light", "strong"), default="auto")
    parser.add_argument("--dpi", type=int, default=220, help="轻量 OCR 渲染 DPI（默认 220）")
    parser.add_argument("--min-confidence", type=float, default=0.86, help="OCR 加权平均置信度门槛")
    parser.add_argument("--max-low-confidence", type=float, default=0.20, help="低置信文字最大占比")
    parser.add_argument("--no-layout-escalation", action="store_true", help="复杂版面不升级到 Docling")
    parser.add_argument(
        "--strong-tier",
        choices=("basic", "standard", "advanced"),
        default="standard",
        help="兼容旧版的保留参数；Docling 后端当前忽略该值",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-ocr",
        description="先文本层、再轻量 OCR、最后 Docling 的分层 PDF 处理工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="处理一个 PDF")
    process.add_argument("input", type=Path)
    process.add_argument("-o", "--output", type=Path)
    process.add_argument("--json", action="store_true", help="以 JSON 输出处理结果")
    _add_routing_options(process)

    batch = subparsers.add_parser("batch", help="批量处理目录内的 PDF")
    batch.add_argument("input_dir", type=Path)
    batch.add_argument("-o", "--output-dir", type=Path, required=True)
    batch.add_argument("--glob", default="*.pdf")
    batch.add_argument("--recursive", action="store_true")
    batch.add_argument("--json", action="store_true")
    _add_routing_options(batch)

    doctor = subparsers.add_parser("doctor", help="检查依赖是否齐全")
    doctor.add_argument("--json", action="store_true")
    return parser


def _doctor() -> dict[str, object]:
    modules = {
        name: importlib.util.find_spec(name) is not None
        for name in ("pypdf", "pypdfium2", "rapidocr", "onnxruntime")
    }
    executable = find_executable("docling")
    executables = {"docling": executable}
    version = "Docling CLI 未安装"
    if executable:
        checked = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            check=False,
            env=docling_environment(),
        )
        version = (checked.stdout or checked.stderr).strip()
    return {
        "native_text_ready": modules["pypdf"],
        "light_ocr_ready": all(modules[name] for name in ("pypdfium2", "rapidocr", "onnxruntime")),
        "strong_backend": "docling",
        "strong_cli_ready": bool(executable),
        "strong_models_ready": bool(executable),
        "strong_parser_ready": bool(executable),
        "modules": modules,
        "executables": executables,
        "model_check": "Docling 模型会在首次转换时下载并缓存",
        "docling_version": version,
    }


def _print_result(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] not in {"process", "batch", "doctor", "-h", "--help"}:
        raw.insert(0, "process")
    args = build_parser().parse_args(raw)

    if args.command == "doctor":
        status = _doctor()
        _print_result(status, args.json)
        return 0 if status["native_text_ready"] else 1

    router = PDFOCRRouter(config=_config_from_args(args))
    try:
        if args.command == "process":
            result = router.process(args.input, args.output, args.mode)
            _print_result(
                {
                    "status": result.status,
                    "stage": result.selected_stage,
                    "output": str(result.output_path),
                    "report": str(result.report_path),
                },
                args.json,
            )
            return 0

        source_dir = args.input_dir.expanduser().resolve()
        output_dir = args.output_dir.expanduser().resolve()
        iterator = source_dir.rglob(args.glob) if args.recursive else source_dir.glob(args.glob)
        files = sorted(path for path in iterator if path.is_file())
        results: list[dict[str, object]] = []
        failures = 0
        for source in files:
            relative = source.relative_to(source_dir).with_suffix(".md")
            target = output_dir / relative
            try:
                result = router.process(source, target, args.mode)
                results.append({"input": str(source), "status": result.status, "stage": result.selected_stage, "output": str(target)})
            except Exception as exc:  # continue the batch and report every failed file
                failures += 1
                results.append({"input": str(source), "status": "error", "error": str(exc)})
        summary = {"total": len(files), "failed": failures, "results": results}
        _print_result(summary, args.json)
        return 1 if failures else 0
    except (BackendUnavailable, BackendFailure, FileNotFoundError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
