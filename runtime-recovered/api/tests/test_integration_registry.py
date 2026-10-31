from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from integrations.integration_registry import load_default_registry  # noqa: E402
from integrations.provider_router import choose_provider  # noqa: E402
from integrations.rendi_provider import RendiProvider  # noqa: E402


def test_registry_contains_rendi_ffmpeg_mcp() -> None:
    registry = load_default_registry()

    rendi = registry.get("rendi_ffmpeg_mcp")

    assert rendi.type.value == "mcp"
    assert rendi.enabled is False
    assert rendi.requires_api_key is True
    assert rendi.risk_level == "high"
    assert "RENDI_API_KEY" in rendi.env_vars
    assert "cloud FFmpeg rendering" in rendi.use_cases


def test_provider_router_selects_rendi_for_explicit_cloud_ffmpeg_task() -> None:
    decision = choose_provider("wyrenderuj finalny short przez Rendi FFmpeg API")

    assert decision.provider_id == "rendi_ffmpeg_mcp"
    assert decision.task_category == "cloud_ffmpeg_rendering"
    assert decision.requires_approval is True
    assert decision.fallback_provider_ids == ["local_short_pipeline"]


def test_rendi_provider_is_blocked_without_enabled_api_key(monkeypatch) -> None:
    monkeypatch.delenv("RENDI_API_KEY", raising=False)
    monkeypatch.setenv("RENDI_ENABLED", "false")

    result = RendiProvider().build_ffmpeg_job_preview(
        input_url="https://example.com/input.mp4",
        ffmpeg_args=["-vf", "scale=1080:1920"],
    )

    assert result.status == "blocked"
    assert result.requires_approval is True
    assert "RENDI_API_KEY" in result.message
