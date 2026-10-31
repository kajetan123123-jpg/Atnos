"""Media Production observability — status model + failure simulations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atnos.core.config import Settings
from atnos.production.media_observability import ObservabilitySimulate, build_media_observability


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    return Settings(app_env="test", app_root=tmp_path, data_dir=data)


def _write_meta(root: Path, channel: str, *, passed: bool, gen_ms: int = 1200, dc: bool = False) -> None:
    folder = root / channel / "job1"
    folder.mkdir(parents=True, exist_ok=True)
    settings_block: dict = {
        "validation_verdict": "VALIDATED" if passed else "FAIL",
        "quality_gate": {"passed": passed, "checks": [{"name": "sharpness", "ok": passed}]},
        "generation_time_ms": gen_ms,
    }
    if dc:
        settings_block["dc_visual_qc"] = {
            "passed": passed,
            "failure_reasons": [] if passed else ["cartoon_leakage"],
        }
        settings_block["dc_visual_qc_attempt"] = 1 if passed else 3
    (folder / "metadata.json").write_text(
        json.dumps({"generation_settings": settings_block}),
        encoding="utf-8",
    )


def test_simulate_comfy_down_blocks_fleet(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "atnos.production.media_observability.generated_root",
        lambda _s: settings.data_dir / "generated",
    )
    payload = build_media_observability(
        settings,
        live=False,
        simulate=ObservabilitySimulate(comfy_down=True),
    )
    assert payload["comfy"]["alert"] == "ERROR"
    assert payload["comfy"]["reachable"] is False
    assert payload["overall_alert"] in {"BLOCKED", "ERROR"}
    assert any(e["error_code"] == "COMFY_DOWN" for e in payload["errors"])


def test_simulate_gpu_locked_degraded(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "atnos.production.media_observability.generated_root",
        lambda _s: settings.data_dir / "generated",
    )
    payload = build_media_observability(
        settings,
        live=False,
        simulate=ObservabilitySimulate(gpu_locked=True, comfy_down=False),
    )
    # Without live Comfy, fleet may already be BLOCKED; GPU itself must show busy
    assert payload["gpu"]["locked"] is True
    assert payload["gpu"]["alert"] == "DEGRADED"
    assert payload["gpu"]["current_owner"] == "sim:locked"


def test_simulate_cache_miss_and_asset_reject(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    root = settings.data_dir / "generated"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("atnos.production.media_observability.generated_root", lambda _s: root)
    payload = build_media_observability(
        settings,
        live=False,
        simulate=ObservabilitySimulate(cache_miss_only=True, asset_rejected=True),
    )
    assert payload["cache"]["hit_rate"] == 0.0
    assert payload["cache"]["alert"] == "DEGRADED"
    assert payload["assets"]["alert"] == "DEGRADED"
    assert payload["assets"]["approval_rate"] == 0.25


def test_simulate_quality_fail_and_model_fallback(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    root = settings.data_dir / "generated"
    monkeypatch.setattr("atnos.production.media_observability.generated_root", lambda _s: root)
    _write_meta(root, "dark_chronicles", passed=False, dc=True)
    _write_meta(root, "wealth_blueprint", passed=True, gen_ms=2500)

    payload = build_media_observability(
        settings,
        live=False,
        simulate=ObservabilitySimulate(quality_gate_failure=True, model_fallback=True),
    )
    assert payload["quality"]["alert"] == "ERROR"
    assert payload["quality"]["fail"] >= 5
    assert payload["models"]["alert"] == "DEGRADED"
    fail = payload["models"]["channels"]["wealth_blueprint"]["last_model_failure"]
    assert fail["error_code"] == "CHECKPOINT_FALLBACK"
    assert "api_key" not in json.dumps(payload).casefold()
    assert payload["performance"]["average_generation_seconds"] is not None
    assert "character_approval_rate" in payload["performance"]
    assert "wb_asset_approval_rate" in payload["performance"]
    assert "dc_visual_qc_pass_rate" in payload["performance"]


def test_orders_missing_events_table_is_not_fleet_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REPRO: intelligence.db with pipeline_jobs but no pipeline_job_events used to set overall ERROR."""
    data = tmp_path / "data"
    data.mkdir()
    db = data / "intelligence.db"
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE pipeline_jobs (
              id TEXT, status TEXT, created_at TEXT, updated_at TEXT,
              topic_id TEXT, current_step TEXT, error TEXT
            )
            """
        )
        conn.commit()
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=data)
    monkeypatch.setattr(
        "atnos.production.media_observability.generated_root",
        lambda _s: data / "generated",
    )
    monkeypatch.setattr(
        "atnos.production.media_observability.ComfyUiRuntimeService",
        lambda _s: type("R", (), {"_server_ready": lambda self: True, "describe_install": lambda self: type("S", (), {})()})(),
    )
    payload = build_media_observability(settings, live=True)
    assert payload["orders"]["alert"] in {"HEALTHY", "DEGRADED"}
    assert payload["orders"]["alert"] != "ERROR"
    assert "pipeline_job_events" in str(payload["orders"].get("detail") or "")
    assert not any(e.get("error_code") == "DB_ERROR" for e in payload.get("errors") or [])
    # Missing events must not force fleet ERROR when Comfy is up
    assert payload["overall_alert"] != "ERROR"

def test_channel_views_present(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "atnos.production.media_observability.generated_root",
        lambda _s: settings.data_dir / "generated",
    )
    payload = build_media_observability(settings, live=False, simulate=ObservabilitySimulate(comfy_down=True))
    assert "wealth_blueprint" in payload["channels"]
    assert "wonder_crew" in payload["channels"]
    assert "dark_chronicles" in payload["channels"]
    for err in payload["errors"]:
        assert set(err) >= {"component", "timestamp", "error_code", "short_message", "job_request_id", "retry_count"}


def test_healthy_requires_comfy_capability(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP-only readiness is not enough — unreachable Comfy cannot be HEALTHY overall."""
    monkeypatch.setattr(
        "atnos.production.media_observability.generated_root",
        lambda _s: settings.data_dir / "generated",
    )

    class FakeRuntime:
        def _server_ready(self) -> bool:
            return False

        def describe_install(self):
            return type("S", (), {"instance_marker_path": None})()

    monkeypatch.setattr(
        "atnos.production.media_observability.ComfyUiRuntimeService",
        lambda _settings: FakeRuntime(),
    )
    payload = build_media_observability(settings, live=True)
    assert payload["comfy"]["reachable"] is False
    assert payload["overall_alert"] != "HEALTHY"
