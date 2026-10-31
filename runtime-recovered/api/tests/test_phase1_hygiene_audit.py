from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase1_hygiene_audit.py"
SPEC = importlib.util.spec_from_file_location("phase1_hygiene_audit", SCRIPT_PATH)
assert SPEC is not None
phase1_hygiene_audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = phase1_hygiene_audit
SPEC.loader.exec_module(phase1_hygiene_audit)


def test_scan_backend_root_detects_secrets_and_runtime_artifacts(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    (tmp_path / "client_secret.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tmp-live-api-8000.stderr.log").write_text("log", encoding="utf-8")
    (tmp_path / "abcTEMP_MPY_wvf_snd.mp4").write_bytes(b"mp4")
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")

    findings = phase1_hygiene_audit.scan_backend_root(tmp_path)

    by_path = {finding.path: finding for finding in findings}
    assert ".env" in by_path
    assert "client_secret.json" in by_path
    assert "tmp-live-api-8000.stderr.log" in by_path
    assert "abcTEMP_MPY_wvf_snd.mp4" in by_path
    assert ".env.example" not in by_path
    assert "README.md" not in by_path
    assert by_path[".env"].category == "secret_or_oauth_artifact"
    assert by_path["tmp-live-api-8000.stderr.log"].category == "runtime_artifact"


def test_scan_backend_root_passes_when_root_is_clean(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.log").write_text("not root artifact", encoding="utf-8")

    findings = phase1_hygiene_audit.scan_backend_root(tmp_path)

    assert findings == []
