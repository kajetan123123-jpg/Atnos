"""Register or run a bounded, restart-safe batch email delivery through Atnos."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atnos.core.config import get_settings
from atnos.core.logging import redact_secrets
from atnos.db.session import DatabaseManager
from atnos.production.batch_result_delivery import BatchResultDelivery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id")
    parser.add_argument("--recipient")
    parser.add_argument("--jobs", nargs="+")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    if not 0 < args.hours <= 72:
        parser.error("The watcher must be bounded to at most 72 hours")
    if bool(args.recipient) != bool(args.jobs):
        parser.error("Registration needs both --recipient and --jobs")
    settings = get_settings()
    database = DatabaseManager(settings)
    service = BatchResultDelivery(settings, database)
    try:
        if args.jobs:
            service.register(args.batch_id, args.recipient, args.jobs, approved=args.approve)
        deadline = time.monotonic() + args.hours * 3600
        previous = None
        while True:
            result = service.status(args.batch_id) if args.status_only else service.process(args.batch_id)
            encoded = json.dumps(redact_secrets(result), ensure_ascii=True)
            fingerprint = (result["status"], result["error"], result["message_id"])
            if previous != fingerprint:
                print(encoded, flush=True)
                previous = fingerprint
            if args.status_only or not args.watch or result["status"] in {"sent", "insufficient_evidence", "cancelled", "blocked_technical"}:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(json.dumps({"status": "watch_deadline_reached", "batch_id": args.batch_id, "delivery_status": result["status"]}), flush=True)
                break
            time.sleep(min(900 if result["status"] == "blocked_user_action" else 60, remaining))
        return 0 if args.status_only or result["status"] == "sent" else 2
    finally:
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
