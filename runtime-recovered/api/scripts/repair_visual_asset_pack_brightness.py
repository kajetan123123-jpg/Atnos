from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.visual_asset_pack_repair import VisualAssetPackBrightnessRepairService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Brighten and rerender a visual recreation asset pack.")
    parser.add_argument("--asset-pack-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--brightness", type=float, default=1.28)
    parser.add_argument("--contrast", type=float, default=1.08)
    parser.add_argument("--sharpness", type=float, default=1.04)
    parser.add_argument("--no-render-video", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    output_dir = args.output_dir or (
        settings.data_dir
        / "learning"
        / "faceless-ai-shorts"
        / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_visual_asset_pack_brightness_repair"
    )
    result = VisualAssetPackBrightnessRepairService(settings).run(
        asset_pack_dir=args.asset_pack_dir,
        output_dir=output_dir,
        brightness_factor=args.brightness,
        contrast_factor=args.contrast,
        sharpness_factor=args.sharpness,
        render_video=not args.no_render_video,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status in {"video_rendered", "images_repaired"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
