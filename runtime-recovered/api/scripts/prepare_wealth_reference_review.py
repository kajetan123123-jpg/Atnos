from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atnos.production.reference_approval import ReferenceApprovalError, prepare_wealth_reference_review_package


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a hash-bound Wealth Blueprint owner review package without accepting it."
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare_wealth_reference_review_package(
            artifact_dir=args.artifact_dir,
            review_dir=args.review_dir,
        )
    except ReferenceApprovalError as exc:
        print(
            json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
