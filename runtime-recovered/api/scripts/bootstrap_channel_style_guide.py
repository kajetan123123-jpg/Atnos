from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings, load_runtime_env_files  # noqa: E402
from atnos.memory.obsidian_style_guide import ensure_channel_style_guide, read_channel_style_guide  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a default Obsidian channel style guide for Atnos Shorts.")
    parser.add_argument("account_id", help="Production account_id used by topic_queue/production jobs.")
    parser.add_argument("--channel-name", default=None)
    parser.add_argument("--niche", default="finanse osobiste, edukacja finansowa, YouTube Shorts")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    load_runtime_env_files()
    settings = Settings()
    result = ensure_channel_style_guide(
        settings,
        args.account_id,
        channel_name=args.channel_name,
        niche=args.niche,
        overwrite=bool(args.overwrite),
    )
    readback = read_channel_style_guide(settings, args.account_id)
    payload = {
        "account_id": args.account_id,
        "status": result.status,
        "created": result.created,
        "path": str(result.path) if result.path else None,
        "message": result.message,
        "readback_status": readback.status,
        "readback_source": readback.source,
        "readback_used": bool(readback.content),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if readback.content else 2


if __name__ == "__main__":
    raise SystemExit(main())
