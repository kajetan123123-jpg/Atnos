#!/usr/bin/env python
"""Create a local-only Wealth Blueprint Production Order with renderer=remotion (test option).

Does not change the saved default profile permanently unless --persist-profile is passed.
Does not publish / upload to YouTube.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from atnos.core.config import get_settings
from atnos.core.logging import redact_secrets
from atnos.production.order_dispatch import ProductionOrderDispatcher, order_status
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder, load_project_profile


SHORT_SCRIPT_TOPIC = "Keep more of your raise"
SHORT_DIRECTION = (
    "20-30 second English Short. Calm expert tone. "
    "Hook: paycheck feels bigger but wealth is what you keep. "
    "Practical: save first automatically, then spend. "
    "Close with consistent investing. No hype, no guarantees."
)


def _install_remotion_profile(settings, *, persist: bool) -> Path | None:
    """Write data_dir/production-profiles/wealth_blueprint.json with renderer=remotion."""
    profile = load_project_profile(settings, "wealth_blueprint")
    production = dict(profile["production"])
    production["renderer"] = "remotion"
    # Keep canonical duration/word_budget — short [20,32]/[55,90] overrides conflicted with
    # normal Production Orders (script_repair loops against a budget the brief cannot meet).
    override_dir = Path(settings.data_dir) / "production-profiles"
    override_dir.mkdir(parents=True, exist_ok=True)
    target = override_dir / "wealth_blueprint.json"
    backup = override_dir / "wealth_blueprint.json.bak_stabilization"
    # Rebuild full channel profile JSON expected by load_project_profile
    source = API_ROOT / "config" / "channel_profiles" / "wealth_blueprint.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    if target.is_file() and not backup.is_file():
        shutil.copy2(target, backup)
    raw["production"] = production
    # load_project_profile for WB validates ProductionProfile then returns subset;
    # override file must still be the channel profile shape with production key.
    target.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    if persist:
        return None
    return backup if backup.is_file() else target


def _restore_profile(settings, backup: Path | None, installed: Path) -> None:
    if backup is None:
        return
    override = Path(settings.data_dir) / "production-profiles" / "wealth_blueprint.json"
    if backup.name.endswith(".bak_stabilization") and backup.is_file():
        shutil.copy2(backup, override)
        backup.unlink(missing_ok=True)
    elif installed.is_file() and not backup.is_file():
        # We created the override from defaults — remove temporary remotion override.
        installed.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=SHORT_SCRIPT_TOPIC)
    parser.add_argument("--direction", default=SHORT_DIRECTION)
    parser.add_argument("--notes", default="stabilization remotion e2e — local only, no publish")
    parser.add_argument("--request-key", default=None)
    parser.add_argument("--queue-only", action="store_true")
    parser.add_argument("--persist-profile", action="store_true", help="Keep remotion override after run")
    parser.add_argument("--restore-only", action="store_true", help="Restore editorial profile override backup")
    args = parser.parse_args()

    settings = get_settings()
    override = Path(settings.data_dir) / "production-profiles" / "wealth_blueprint.json"
    backup = Path(settings.data_dir) / "production-profiles" / "wealth_blueprint.json.bak_stabilization"

    if args.restore_only:
        if backup.is_file():
            shutil.copy2(backup, override)
            backup.unlink(missing_ok=True)
            print(json.dumps({"status": "restored", "profile": str(override)}))
            return 0
        if override.is_file():
            override.unlink()
            print(json.dumps({"status": "removed_temporary_override", "profile": str(override)}))
            return 0
        print(json.dumps({"status": "noop"}))
        return 0

    backup_ref = _install_remotion_profile(settings, persist=args.persist_profile)
    # Verify load sees remotion
    loaded = load_project_profile(settings, "wealth_blueprint")
    if loaded["production"]["renderer"] != "remotion":
        print(json.dumps({"status": "failed", "error": "remotion profile override not active"}))
        return 2

    store = ProductionOrderStore(settings)
    try:
        state = store.create(
            ProductionOrder(
                topic=args.topic,
                direction=args.direction,
                optional_notes=args.notes,
                delivery_mode="local_only",
            ),
            request_key=args.request_key,
        )
        # Provenance: E2E with remotion override explicitly requested remotion (not profile default).
        artifact_dir = Path(settings.data_dir) / "production" / "wealth_blueprint" / state["id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "remotion_options.json").write_text(
            json.dumps(
                {
                    "requested_renderer": "remotion",
                    "enable_sound": True,
                    "e2e_remotion_override": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        result = order_status(state) if args.queue_only else ProductionOrderDispatcher(settings).dispatch(state["id"])
    finally:
        if not args.persist_profile:
            _restore_profile(settings, backup_ref, override)

    print(json.dumps(redact_secrets(result), ensure_ascii=True), flush=True)
    status = str(result.get("status") or "")
    if status in {"blocked", "failed"}:
        return 2
    if status in {"ready", "done", "completed"}:
        return 0
    # dispatch may return in-progress / worker_requested
    return 0 if result.get("worker_requested") or status in {"running", "queued", "leased"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": redact_secrets(str(exc))}), flush=True)
        raise SystemExit(2)
