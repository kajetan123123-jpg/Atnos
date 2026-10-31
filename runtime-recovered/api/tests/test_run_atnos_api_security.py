from __future__ import annotations

from pathlib import Path


def test_api_runner_disables_query_string_access_logging() -> None:
    runner = Path(__file__).resolve().parents[1] / "scripts" / "run_atnos_api.py"
    source = runner.read_text(encoding="utf-8")

    assert "access_log=False" in source
