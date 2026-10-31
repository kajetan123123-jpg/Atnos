from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.faceless_shorts_watch_session import FacelessShortsWatchSessionService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run recurring faceless Shorts watch/learn/recreate session.")
    parser.add_argument("--config-json", type=Path, default=None)
    parser.add_argument("--write-default-config", action="store_true")
    parser.add_argument("--overwrite-config", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--benchmark-json", type=Path, default=None)
    parser.add_argument("--reference-observation-json", type=Path, default=None)
    parser.add_argument("--visual-plan-json", type=Path, default=None)
    parser.add_argument("--source-short-dir", type=Path, default=None)
    parser.add_argument("--prompt-only", action="store_true")
    parser.add_argument("--no-render-video", action="store_true")
    args = parser.parse_args()

    service = FacelessShortsWatchSessionService(Settings())
    if args.write_default_config:
        path = service.write_default_config(args.config_json, overwrite=args.overwrite_config)
        print(json.dumps({"config_path": str(path)}, ensure_ascii=True, indent=2))
        return 0

    result = service.run_once(
        config_path=args.config_json,
        output_dir=args.output_dir,
        benchmark_json=args.benchmark_json,
        reference_observation_json=args.reference_observation_json,
        visual_plan_json=args.visual_plan_json,
        source_short_dir=args.source_short_dir,
        generate_images=False if args.prompt_only else None,
        render_video=False if args.no_render_video else None,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))
    return 0 if result.ready_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
