"""Audit existing WB output without rendering, cloud calls, or changing production data."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
from atnos.production.wb_delivery_audit import audit_order, read_json, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--editorial-review", type=Path)
    parser.add_argument("--app-root", type=Path, default=Path("E:/Atnos/App"))
    parser.add_argument("--ffmpeg", type=Path, default=Path("E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe"))
    parser.add_argument("--ffprobe", type=Path, default=Path("E:/Atnos/Tools/FFmpeg/bin/ffprobe.exe"))
    args = parser.parse_args()
    library = API_ROOT / "config" / "asset_libraries" / "wealth_blueprint"
    report = audit_order(args.order_dir, app_root=args.app_root, catalog_path=library / "catalog.json",
                         sound_catalog_path=library / "sounds" / "catalog.json", ffmpeg=args.ffmpeg,
                         ffprobe=args.ffprobe,
                         human_review=read_json(args.editorial_review) if args.editorial_review else None)
    write_report(report, args.output_dir)
    print(json.dumps({"status": report["status"], "blocker_count": len(report["blockers"]),
                      "report": str(args.output_dir / "audit.json"),
                      "review": str(args.output_dir / "review.html")}, ensure_ascii=False))
    return 0 if report["production_accepted"] else (2 if report["blockers"] else 3)


if __name__ == "__main__":
    raise SystemExit(main())
