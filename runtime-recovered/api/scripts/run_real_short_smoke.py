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
from content.short_agent import ShortAgent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real local short render smoke.")
    parser.add_argument(
        "topic",
        nargs="?",
        default="Dlaczego lokalne AI na laptopie ma sens?",
    )
    parser.add_argument("--force-repair", action="store_true")
    args = parser.parse_args()

    result = ShortAgent(Settings()).run(args.topic, force_repair=bool(args.force_repair))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0 if result.status == "manual_upload_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
