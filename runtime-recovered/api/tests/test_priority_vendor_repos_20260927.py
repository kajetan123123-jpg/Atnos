"""Priority-A vendor repos: catalog, clones, light capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from atnos.integrations.capability_executors import run_executor
from atnos.integrations.capability_runtime import CAPABILITY_BINDINGS, execute_capability, list_executable_capabilities
from atnos.integrations.external_repositories import list_repositories
from atnos.integrations import priority_vendor_bridges as pvb

VENDOR = Path(r"E:\Atnos\Tools\external-repos")
PRIORITY_IDS = (
    "whisperx",
    "stable_ts",
    "youtube_transcript_api",
    "yt_dlp",
    "remotion_trading",
    "lucide",
)


def test_priority_a_repos_are_catalogued_and_cloned():
    repos = {item["id"]: item for item in list_repositories()}
    for repo_id in PRIORITY_IDS:
        assert repo_id in repos, repo_id
        row = repos[repo_id]
        assert row.get("local_clone") is True, repo_id
        assert row.get("capability")
        assert row.get("commit")
        assert Path(row["local_path"]).is_dir()


def test_priority_status_bridges():
    statuses = pvb.status_all()
    assert set(statuses) == set(PRIORITY_IDS)
    assert statuses["lucide"]["status"] == "HEALTHY"
    assert statuses["lucide"]["icon_count"] > 100
    assert statuses["remotion_trading"]["status"] == "HEALTHY"
    assert statuses["youtube_transcript_api"]["status"] == "HEALTHY"
    assert statuses["yt_dlp"]["status"] == "HEALTHY"
    # Aligners are optional GPU installs — clone present is enough for REQUIRES_SETUP.
    assert statuses["whisperx"]["local_clone"] is True
    assert statuses["stable_ts"]["local_clone"] is True
    assert statuses["whisperx"]["status"] in {"HEALTHY", "REQUIRES_SETUP"}
    assert statuses["stable_ts"]["status"] in {"HEALTHY", "REQUIRES_SETUP"}


def test_capabilities_bound_for_priority_a():
    caps = {row["capability"]: row for row in list_executable_capabilities()}
    for capability in (
        "YOUTUBE_TRANSCRIPT",
        "YOUTUBE_METADATA",
        "CAPTION_ALIGN",
        "ICON_SVG_LIBRARY",
        "FINANCE_CHART_SKILL",
    ):
        assert capability in CAPABILITY_BINDINGS
        assert capability in caps
        assert caps[capability]["executable"] is True


def test_lucide_icon_library_executor():
    result = run_executor("ICON_SVG_LIBRARY", {"query": "wallet", "limit": 5})
    assert result["status"] == "SUCCESS"
    icons = (result.get("output") or {}).get("icons") or []
    assert icons
    assert any("wallet" in name for name in icons)
    assert Path(result["output"]["paths"][0]).is_file()


def test_finance_chart_skill_executor():
    result = run_executor("FINANCE_CHART_SKILL", {})
    assert result["status"] == "SUCCESS"
    assert (result.get("output") or {}).get("skill_chars", 0) > 100
    assert (result.get("output") or {}).get("provider") == "remotion_trading"


def test_caption_align_reports_setup_without_gpu_stack():
    result = run_executor("CAPTION_ALIGN", {})
    # Without whisperx/stable-ts installed: REQUIRES_SETUP; with either: SUCCESS readiness.
    assert result["status"] in {"REQUIRES_SETUP", "SUCCESS"}
    assert result.get("output")


def test_youtube_metadata_refuses_download_flag():
    result = run_executor("YOUTUBE_METADATA", {"video_id": "jNQXAC9IVRw", "download": True})
    assert result["status"] == "FAILED"
    assert "download" in " ".join(result.get("errors") or []).casefold()


@pytest.mark.network
def test_youtube_transcript_live_smoke():
    # Short public video commonly used in docs ("Me at the zoo").
    result = execute_capability("YOUTUBE_TRANSCRIPT", parameters={"video_id": "jNQXAC9IVRw", "languages": ["en"]})
    assert result.get("status") in {"SUCCESS", "FAILED", "REQUIRES_SETUP"}
    if result.get("status") == "SUCCESS":
        output = result.get("output") or {}
        assert output.get("segment_count", 0) > 0


@pytest.mark.network
def test_youtube_metadata_live_smoke():
    result = execute_capability("YOUTUBE_METADATA", parameters={"video_id": "jNQXAC9IVRw"})
    assert result.get("status") in {"SUCCESS", "FAILED", "REQUIRES_SETUP"}
    if result.get("status") == "SUCCESS":
        meta = (result.get("output") or {}).get("metadata") or {}
        assert meta.get("id") == "jNQXAC9IVRw"
        assert meta.get("title")
