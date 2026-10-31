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
from content.visual_recreation_asset_pack import VisualRecreationAssetPackService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ComfyUI/Runway/CapCut production assets from visual_recreation_plans.json."
    )
    parser.add_argument("--visual-plan-json", type=Path, required=True)
    parser.add_argument("--source-short-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--plan-index", type=int, default=0)
    parser.add_argument("--max-scenes", type=int, default=5)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=1344)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--prompt-only", action="store_true")
    parser.add_argument("--no-render-video", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    created_at = datetime.now(UTC)
    output_dir = args.output_dir or (
        settings.data_dir
        / "learning"
        / "faceless-ai-shorts"
        / f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_visual_recreation_asset_pack"
    )

    result = VisualRecreationAssetPackService(settings).build(
        visual_plan_json=args.visual_plan_json,
        output_dir=output_dir,
        plan_index=args.plan_index,
        source_short_dir=args.source_short_dir,
        max_scenes=args.max_scenes,
        generate_images=not args.prompt_only,
        render_video=not args.no_render_video,
        width=args.width,
        height=args.height,
        steps=args.steps,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status in {"video_rendered", "images_generated", "prompt_pack_ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
