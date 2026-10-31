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
from atnos.services.local_short_runtime_doctor_service import LocalShortRuntimeDoctorService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose the local short production runtime.")
    parser.add_argument("--output", type=Path, default=None, help="Optional report path. Use .json for JSON output.")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when the runtime is not ready.")
    parser.add_argument(
        "--skip-production-readiness",
        action="store_true",
        help="Only check legacy local runtime components, not the full Shorts production readiness.",
    )
    args = parser.parse_args()

    settings = Settings()
    service = LocalShortRuntimeDoctorService(
        settings,
        include_production_readiness=not args.skip_production_readiness,
    )
    report = service.build_report()
    report_path = service.write_report(args.output)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Local short runtime status: {report.status}")
        print(f"Ready: {report.ready}")
        print(f"Reason: {report.reason}")
        if report.missing_components:
            print(f"Missing: {', '.join(report.missing_components)}")
        print(f"Report: {report_path}")
        if report.recommended_actions:
            print("\nNext actions:")
            for action in report.recommended_actions:
                print(f"- {action.priority} {action.component}: {action.title}")

    if args.strict and not report.ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
