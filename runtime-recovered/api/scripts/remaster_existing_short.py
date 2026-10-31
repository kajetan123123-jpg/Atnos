from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.existing_short_remaster import remaster_existing_short


def main() -> None:
    parser = argparse.ArgumentParser(description="Remaster accepted Atnos scenes against a new narration timeline.")
    parser.add_argument("source_job_dir", type=Path)
    parser.add_argument("voice_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    result = remaster_existing_short(
        source_job_dir=args.source_job_dir.resolve(),
        voice_dir=args.voice_dir.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
