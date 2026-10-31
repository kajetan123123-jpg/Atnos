from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one evidence-backed ComfyUI image.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--quality", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    service = ComfyUiImageGenerationService(get_settings())
    try:
        result = service.generate_background(
            prompt=args.prompt,
            output_path=args.output.expanduser().resolve(),
            width=args.width,
            height=args.height,
            seed=args.seed,
            quality_mode=bool(args.quality),
        )
    except ComfyUiImageGenerationError as exc:
        failure_path = args.output.with_suffix(f"{args.output.suffix}.failure.json").resolve()
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure = {
            "status": "blocked_technical",
            "failed_at": datetime.now(UTC).isoformat(),
            "error": str(exc),
            "backend_health": service.backend_health_snapshot(),
        }
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": False, "failure_evidence": str(failure_path)}, ensure_ascii=False))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "output": str(result.output_path),
                "backend": result.backend,
                "prompt_id": result.prompt_id,
                "evidence": str(result.evidence_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
