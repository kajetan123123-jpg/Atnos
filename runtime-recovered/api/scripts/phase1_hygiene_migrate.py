"""Kontrolowana migracja artefaktow Fazy 1.

Domyslnie skrypt dziala w trybie dry-run. Nie wypisuje zawartosci sekretow.
Migracja sekretow wymaga jawnej flagi `--include-secrets`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except Exception:  # pragma: no cover - psutil jest zaleznoscia runtime, ale skrypt ma miec fallback.
    psutil = None  # type: ignore[assignment]

from phase1_hygiene_audit import Finding, scan_backend_root


@dataclass(frozen=True)
class MigrationAction:
    """Opis pojedynczej planowanej albo wykonanej migracji."""

    category: str
    source: str
    destination: str | None
    size_bytes: int
    action: str
    skipped: bool = False
    reason: str = ""


def default_secure_dir() -> Path:
    """Zwroc domyslny katalog na sekrety poza rootem backendu."""

    configured = os.environ.get("ATNOS_SECURE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return (Path(appdata) / "Atnos" / "secure").resolve()
    return (Path.home() / ".atnos" / "secure").resolve()


def build_migration_plan(
    root: Path,
    *,
    timestamp: str,
    secure_dir: Path,
    include_secrets: bool = False,
) -> list[MigrationAction]:
    """Zbuduj plan migracji bez dotykania plikow."""

    root = root.resolve()
    secure_dir = secure_dir.resolve()
    findings = scan_backend_root(root)
    actions: list[MigrationAction] = []

    for finding in findings:
        source = root / finding.path
        if finding.category == "secret_or_oauth_artifact":
            if not include_secrets:
                actions.append(
                    MigrationAction(
                        category=finding.category,
                        source=finding.path,
                        destination=None,
                        size_bytes=finding.size_bytes,
                        action="skip",
                        skipped=True,
                        reason="secret_migration_requires_include_secrets",
                    )
                )
                continue
            destination = secure_dir / timestamp / finding.path
        else:
            if _is_active_pid_file(source):
                actions.append(
                    MigrationAction(
                        category=finding.category,
                        source=finding.path,
                        destination=None,
                        size_bytes=finding.size_bytes,
                        action="skip",
                        skipped=True,
                        reason="pid_file_points_to_running_process",
                    )
                )
                continue
            destination = _runtime_destination(root, timestamp, source)

        actions.append(
            MigrationAction(
                category=finding.category,
                source=finding.path,
                destination=str(destination.relative_to(root)) if _is_relative_to(destination, root) else str(destination),
                size_bytes=finding.size_bytes,
                action="move",
            )
        )

    return actions


def apply_migration_plan(root: Path, actions: list[MigrationAction]) -> list[MigrationAction]:
    """Wykonaj plan migracji przez przenoszenie plikow."""

    root = root.resolve()
    applied: list[MigrationAction] = []
    for action in actions:
        if action.skipped or action.action != "move" or action.destination is None:
            applied.append(action)
            continue

        source = (root / action.source).resolve()
        destination = Path(action.destination)
        if not destination.is_absolute():
            destination = (root / destination).resolve()

        if not _is_relative_to(source, root):
            applied.append(_with_skip(action, "source_outside_backend_root"))
            continue
        if not source.exists() or not source.is_file():
            applied.append(_with_skip(action, "source_missing"))
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        final_destination = _dedupe_destination(destination)
        shutil.move(str(source), str(final_destination))
        applied.append(
            MigrationAction(
                category=action.category,
                source=action.source,
                destination=(
                    str(final_destination.relative_to(root))
                    if _is_relative_to(final_destination, root)
                    else str(final_destination)
                ),
                size_bytes=action.size_bytes,
                action="move",
                skipped=False,
                reason="moved",
            )
        )
    return applied


def write_manifest(root: Path, actions: list[MigrationAction], *, timestamp: str, dry_run: bool) -> Path:
    """Zapisz manifest migracji bez sekretow."""

    manifest_dir = root / "data" / "phase1_hygiene" / "migrations"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{timestamp}_{'dry_run' if dry_run else 'applied'}.json"
    payload = {
        "timestamp": timestamp,
        "dry_run": dry_run,
        "action_count": len(actions),
        "move_count": sum(1 for item in actions if item.action == "move" and not item.skipped),
        "skip_count": sum(1 for item in actions if item.skipped),
        "actions": [asdict(action) for action in actions],
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def _runtime_destination(root: Path, timestamp: str, source: Path) -> Path:
    name = source.name.lower()
    if name.endswith(".log") or name.endswith(".pid"):
        return root / "logs" / "archive" / timestamp / source.name
    return root / "outputs" / "tmp-media" / timestamp / source.name


def _is_active_pid_file(path: Path) -> bool:
    if path.suffix.lower() != ".pid" or psutil is None:
        return False
    try:
        pid_text = path.read_text(encoding="utf-8", errors="ignore").strip()
        pid = int(pid_text)
    except Exception:
        return False
    return bool(psutil.pid_exists(pid))


def _dedupe_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    parent = destination.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _with_skip(action: MigrationAction, reason: str) -> MigrationAction:
    return MigrationAction(
        category=action.category,
        source=action.source,
        destination=action.destination,
        size_bytes=action.size_bytes,
        action="skip",
        skipped=True,
        reason=reason,
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Atnos Phase 1 hygiene migration")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Backend root directory.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually move files. Default is dry-run.")
    parser.add_argument(
        "--include-secrets",
        action="store_true",
        help="Also migrate secret/OAuth artifacts to the secure directory.",
    )
    parser.add_argument(
        "--secure-dir",
        type=Path,
        default=default_secure_dir(),
        help="Destination directory for secret/OAuth artifacts.",
    )
    parser.add_argument("--timestamp", default=_timestamp(), help="Stable migration timestamp.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    args = parser.parse_args()

    root = args.root.resolve()
    plan = build_migration_plan(
        root,
        timestamp=args.timestamp,
        secure_dir=args.secure_dir,
        include_secrets=args.include_secrets,
    )
    actions = apply_migration_plan(root, plan) if args.apply else plan
    manifest = write_manifest(root, actions, timestamp=args.timestamp, dry_run=not args.apply)

    payload = {
        "root": str(root),
        "dry_run": not args.apply,
        "include_secrets": bool(args.include_secrets),
        "manifest": str(manifest),
        "action_count": len(actions),
        "move_count": sum(1 for item in actions if item.action == "move" and not item.skipped),
        "skip_count": sum(1 for item in actions if item.skipped),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        mode = "DRY-RUN" if payload["dry_run"] else "APPLIED"
        print(f"Atnos Phase 1 hygiene migration: {mode}")
        print(f"Actions: {payload['action_count']}")
        print(f"Moves: {payload['move_count']}")
        print(f"Skipped: {payload['skip_count']}")
        print(f"Manifest: {payload['manifest']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
