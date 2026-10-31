from __future__ import annotations

import argparse
import json
from pathlib import Path

from atnos.core.config import get_settings, load_runtime_env_files
from atnos.db.session import DatabaseManager
from atnos.production.reference_approval import approve_wealth_reference
from atnos.services.runtime_settings_service import RuntimeSettingsService


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze an explicitly owner-approved Wealth Blueprint reference.")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--confirm-reference-id", required=True)
    parser.add_argument("--owner-confirmation", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    load_runtime_env_files()
    base_settings = get_settings()
    database = DatabaseManager(base_settings)
    database.initialize()
    with database.session_scope() as session:
        settings = RuntimeSettingsService(session, base_settings).build_effective_settings()
    result = approve_wealth_reference(
        settings,
        artifact_dir=args.artifact_dir,
        review_dir=args.review_dir,
        reviewer=args.reviewer,
        approval_note=args.note,
        confirm_reference_id=args.confirm_reference_id,
        owner_confirmation=args.owner_confirmation,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
