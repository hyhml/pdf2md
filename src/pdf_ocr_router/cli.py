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
    find_executable,
    mineru_environment,
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
    parser.add_argument("--no-layout-escalation", action="store_true", help="复杂版面不升级到 MinerU")
    parser.add_argument("--strong-tier", choices=("basic", "standard", "advanced"), default="standard")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-ocr",
        description="先文本层、再轻量 OCR、最后 MinerU 的分层 PDF 处理工具",
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
    executables = {name: find_executable(name) for name in ("mineru-kit", "mineru")}
    models_ready = False
    model_check = "MinerU CLI 未安装"
    if executables["mineru-kit"]:
        checked = subprocess.run(
            [executables["mineru-kit"], "models", "verify", "--tier", "standard"],
            text=True,
            capture_output=True,
            check=False,
            env=mineru_environment(),
        )
        models_ready = checked.returncode == 0
        model_check = (checked.stdout or checked.stderr).strip()
    return {
        "native_text_ready": modules["pypdf"],
        "light_ocr_ready": all(modules[name] for name in ("pypdfium2", "rapidocr", "onnxruntime")),
        "strong_cli_ready": any(executables.values()),
        "strong_models_ready": models_ready,
        "strong_parser_ready": any(executables.values()) and models_ready,
        "modules": modules,
        "executables": executables,
        "model_check": model_check,
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
