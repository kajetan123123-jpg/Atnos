"""Prepare, inspect or serially produce the four finance orders. Never publishes or sends mail."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atnos.core.config import get_settings
from atnos.core.logging import redact_secrets
from atnos.production.finance_four_intake import BATCH_ID, prepare_batch, probe_held_workers, refresh_batch, run_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "status", "probe-held-workers", "run", "prepare-scripts"])
    parser.add_argument("--attachment", type=Path)
    parser.add_argument("--factcheck", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "prepare":
        if not args.attachment or not args.factcheck:
            parser.error("prepare requires --attachment and --factcheck")
        result = prepare_batch(settings, args.attachment, args.factcheck)
    elif args.command == "probe-held-workers":
        result = probe_held_workers(settings, settings.data_dir / "production-batches" / BATCH_ID / "manifest.json")
    elif args.command in {"run", "prepare-scripts"}:
        result = asyncio.run(run_batch(settings, settings.data_dir / "production-batches" / BATCH_ID / "manifest.json",
                                      script_only=args.command == "prepare-scripts"))
    else:
        result = refresh_batch(settings, settings.data_dir / "production-batches" / BATCH_ID / "manifest.json")
    print(json.dumps(redact_secrets(result), ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
