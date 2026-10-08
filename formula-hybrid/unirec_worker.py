#!/usr/bin/env python3
"""Persistent JSON-lines worker for local UniRec-0.1B ONNX inference."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PREFIX = "@@UNIREC_RESULT@@"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--max-length", type=int, default=2048)
    args = parser.parse_args()

    from openocr import OpenOCR

    root = Path(args.model_dir).resolve()
    model = OpenOCR(
        task="unirec",
        unirec_encoder_path=str(root / "unirec_encoder.onnx"),
        unirec_decoder_path=str(root / "unirec_decoder.onnx"),
        tokenizer_mapping_path=str(root / "unirec_tokenizer_mapping.json"),
        use_gpu=False,
        auto_download=False,
        max_length=args.max_length,
    )
    print(PREFIX + json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(line)
            result, _ = model(
                image_path=str(request["path"]),
                max_length=int(request.get("max_length", args.max_length)),
            )
            payload = {"id": request.get("id"), "result": result}
        except Exception as exc:
            payload = {
                "id": request.get("id"),
                "error": f"{type(exc).__name__}: {exc}",
            }
        print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
