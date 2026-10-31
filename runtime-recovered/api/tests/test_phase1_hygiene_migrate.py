from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT_PATH = SCRIPTS_DIR / "phase1_hygiene_migrate.py"
SPEC = importlib.util.spec_from_file_location("phase1_hygiene_migrate", SCRIPT_PATH)
assert SPEC is not None
phase1_hygiene_migrate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = phase1_hygiene_migrate
SPEC.loader.exec_module(phase1_hygiene_migrate)


def test_build_migration_plan_is_dry_run_safe_and_skips_secrets_by_default(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
    (tmp_path / "client_secret.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tmp-live-api-8000.stderr.log").write_text("log", encoding="utf-8")
    (tmp_path / "abcTEMP_MPY_wvf_snd.mp4").write_bytes(b"mp4")

    plan = phase1_hygiene_migrate.build_migration_plan(
        tmp_path,
        timestamp="20260702T000000Z",
        secure_dir=tmp_path.parent / "secure",
        include_secrets=False,
    )

    by_source = {action.source: action for action in plan}
    assert by_source[".env"].skipped is True
    assert by_source[".env"].reason == "secret_migration_requires_include_secrets"
    assert by_source["client_secret.json"].skipped is True
    assert by_source["tmp-live-api-8000.stderr.log"].destination == (
        "logs\\archive\\20260702T000000Z\\tmp-live-api-8000.stderr.log"
    ) or by_source["tmp-live-api-8000.stderr.log"].destination == (
        "logs/archive/20260702T000000Z/tmp-live-api-8000.stderr.log"
    )
    assert "outputs" in str(by_source["abcTEMP_MPY_wvf_snd.mp4"].destination)
    assert (tmp_path / ".env").exists()
    assert (tmp_path / "tmp-live-api-8000.stderr.log").exists()


def test_apply_migration_plan_moves_runtime_artifacts_and_keeps_manifest_secret_free(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
    (tmp_path / "tmp-live-api-8000.stderr.log").write_text("log", encoding="utf-8")
    (tmp_path / "abcTEMP_MPY_wvf_snd.mp4").write_bytes(b"mp4")

    plan = phase1_hygiene_migrate.build_migration_plan(
        tmp_path,
        timestamp="20260702T000000Z",
        secure_dir=tmp_path.parent / "secure",
        include_secrets=False,
    )
    actions = phase1_hygiene_migrate.apply_migration_plan(tmp_path, plan)
    manifest = phase1_hygiene_migrate.write_manifest(
        tmp_path,
        actions,
        timestamp="20260702T000000Z",
        dry_run=False,
    )

    assert (tmp_path / ".env").exists()
    assert not (tmp_path / "tmp-live-api-8000.stderr.log").exists()
    assert not (tmp_path / "abcTEMP_MPY_wvf_snd.mp4").exists()
    assert (tmp_path / "logs" / "archive" / "20260702T000000Z" / "tmp-live-api-8000.stderr.log").exists()
    assert (tmp_path / "outputs" / "tmp-media" / "20260702T000000Z" / "abcTEMP_MPY_wvf_snd.mp4").exists()
    assert "SECRET=redacted" not in manifest.read_text(encoding="utf-8")


def test_apply_migration_plan_can_move_secrets_when_explicitly_included(tmp_path: Path) -> None:
    secure_dir = tmp_path.parent / "secure"
    (tmp_path / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
    (tmp_path / "client_secret.json").write_text("{}", encoding="utf-8")

    plan = phase1_hygiene_migrate.build_migration_plan(
        tmp_path,
        timestamp="20260702T000000Z",
        secure_dir=secure_dir,
        include_secrets=True,
    )
    actions = phase1_hygiene_migrate.apply_migration_plan(tmp_path, plan)

    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "client_secret.json").exists()
    assert (secure_dir / "20260702T000000Z" / ".env").exists()
    assert (secure_dir / "20260702T000000Z" / "client_secret.json").exists()
    assert all(action.reason == "moved" for action in actions if not action.skipped)
