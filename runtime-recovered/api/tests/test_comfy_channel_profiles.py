"""Comfy channel profiles — isolation, cache, prompts, health (unit + live optional)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atnos.core.config import get_settings
from atnos.production.comfy_channels import (
    ChannelComfyGenerationError,
    ChannelComfyGenerationService,
    ChannelGenerationCache,
    ChannelIsolationError,
    GenerationRequest,
    assert_request_isolated,
    build_prompt,
    check_channel_comfy,
    find_workflow,
    fingerprint_request,
    load_profile,
    negative_prompt,
    persist_builtin_profiles,
    resolve_channel_id,
    run_all_channel_comfy_health,
    validate_generation_request,
)
from atnos.production.comfy_channels.isolation import assert_output_path_isolated
from atnos.production.comfy_channels.prompts import STYLE
from atnos.production.comfy_channels.validation import validate_workflow_for_generation


@pytest.fixture(scope="module")
def settings():
    return get_settings()


def test_resolve_channel_aliases():
    assert resolve_channel_id("children_stories") == "wonder_crew"
    assert resolve_channel_id("horror_stories") == "dark_chronicles"
    assert resolve_channel_id("Wealth Blueprint") == "wealth_blueprint"


def test_persist_and_load_profiles(settings, tmp_path):
    # Persisting built-ins rewrites profile/workflow files. This unit test must
    # never replace the operator's active channel profiles or project indices.
    isolated = settings.model_copy(update={
        "app_root": tmp_path / "app",
        "data_dir": tmp_path / "data",
        "atnos_tools_dir": tmp_path / "tools",
    })
    written = persist_builtin_profiles(isolated)
    assert written
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in written)
    for cid in ("wealth_blueprint", "wonder_crew", "dark_chronicles"):
        profile = load_profile(isolated, cid)
        assert profile.channel_id == cid
        assert profile.workflows
        assert profile.output_subdir == cid
        # Shared models — no per-channel checkpoint copies in profile paths
        for rel in profile.image_models:
            assert not rel.startswith(str(profile.comfy_project_dir))


def test_workflow_lookup_rejects_foreign_title(settings):
    wb = load_profile(settings, "wealth_blueprint")
    with pytest.raises(ValueError, match="not allowed"):
        find_workflow(wb, "CHARACTER_REFERENCE")
    wc = load_profile(settings, "wonder_crew")
    find_workflow(wc, "CHARACTER_REFERENCE")


def test_prompt_isolation_no_cross_style():
    wb = build_prompt(
        GenerationRequest(channel="wealth_blueprint", workflow="WB_CHARACTER", subject="alex", emotion="explaining")
    )
    wc = build_prompt(
        GenerationRequest(channel="wonder_crew", workflow="CHARACTER_REFERENCE", subject="niko", emotion="friendly")
    )
    dc = build_prompt(
        GenerationRequest(
            channel="dark_chronicles",
            workflow="DC_LOCATION",
            subject="abandoned corridor",
            lighting="low-key",
        )
    )
    assert "editorial" in wb.casefold() or "finance" in wb.casefold()
    assert "alex" not in wc.casefold() or "niko" in wc.casefold()
    assert STYLE["wonder_crew"].split(",")[0] not in dc
    assert "horror" in dc.casefold() or "cinematic" in dc.casefold()
    assert "storybook" not in dc.casefold()
    assert negative_prompt("wealth_blueprint") != negative_prompt("dark_chronicles")


def test_cross_channel_subject_leakage_blocked(settings):
    wc = load_profile(settings, "wonder_crew")
    with pytest.raises(ChannelIsolationError):
        assert_request_isolated(
            wc,
            GenerationRequest(channel="wonder_crew", workflow="CHARACTER_REFERENCE", subject="alex"),
        )
    wb = load_profile(settings, "wealth_blueprint")
    with pytest.raises(ChannelIsolationError):
        assert_request_isolated(
            wb,
            GenerationRequest(channel="wealth_blueprint", workflow="WB_CHARACTER", subject="niko"),
        )
    dc = load_profile(settings, "dark_chronicles")
    with pytest.raises(ChannelIsolationError):
        assert_request_isolated(
            dc,
            GenerationRequest(channel="dark_chronicles", workflow="DC_LOCATION", subject="mira"),
        )


def test_output_path_isolation(settings, tmp_path):
    from atnos.production.comfy_channels.profiles import generated_root

    wb = load_profile(settings, "wealth_blueprint")
    root = generated_root(settings)
    ok = root / "wealth_blueprint" / "character" / "x" / "image.png"
    ok.parent.mkdir(parents=True, exist_ok=True)
    ok.write_bytes(b"x" * 10)
    assert_output_path_isolated(wb, ok, root)
    leak = root / "wonder_crew" / "character" / "x" / "image.png"
    leak.parent.mkdir(parents=True, exist_ok=True)
    leak.write_bytes(b"x" * 10)
    with pytest.raises(ChannelIsolationError):
        assert_output_path_isolated(wb, leak, root)


def test_cache_fingerprint_emotion_miss():
    base = {
        "channel": "wealth_blueprint",
        "workflow_id": "wealth_character_v1",
        "workflow_version": "v1",
        "models": ["a"],
        "references": [],
        "prompt": "p",
        "negative": "n",
        "width": 512,
        "height": 512,
        "seed": 1,
        "backend_preference": ["flux2_klein"],
    }
    a = fingerprint_request(base)
    b = fingerprint_request({**base, "prompt": "p emotion: explaining"})
    assert a != b
    cache_path = Path("E:/Atnos/App/data/generated/wealth_blueprint/_cache_test_unit.json")
    cache = ChannelGenerationCache(cache_path)
    cache.put(a, {"output_path": "E:/Atnos/App/data/generated/wealth_blueprint/x.png", "cache_hit": False})
    assert cache.get(a) is not None
    assert cache.get(b) is None
    cache_path.unlink(missing_ok=True)


def test_validate_request_rejects_arbitrary_path(settings):
    with pytest.raises(Exception):
        validate_generation_request(
            settings,
            {
                "channel": "wonder_crew",
                "workflow": "C:/evil/workflow.json",
                "subject": "niko",
            },
        )


def test_health_reports_three_engines(settings):
    report = run_all_channel_comfy_health(settings)
    assert set(report["channels"]) == {"wealth_blueprint", "wonder_crew", "dark_chronicles"}
    for cid, item in report["channels"].items():
        assert item["engine"] == f"{cid.upper()}_COMFY"
        assert item["status"] in {"HEALTHY", "DEGRADED", "BLOCKED_DEPENDENCY"}


def test_workflow_validation(settings):
    for cid in ("wealth_blueprint", "wonder_crew", "dark_chronicles"):
        profile = load_profile(settings, cid)
        wf = profile.workflows[0]
        result = validate_workflow_for_generation(settings, profile, wf)
        assert result["ok"] is True


@pytest.mark.live_comfy
def test_live_wealth_alex_explaining(settings):
    req = GenerationRequest(
        channel="wealth_blueprint",
        workflow="WB_CHARACTER",
        subject="alex",
        emotion="explaining",
        direction="right",
        aspect_ratio="1:1",
        width=768,
        height=768,
        seed=424242,
        references=["image-1.png"],
    )
    svc = ChannelComfyGenerationService(settings)
    meta = svc.generate(req)
    assert Path(meta.output_path).is_file()
    assert meta.width > 0 and meta.height > 0
    assert meta.reference_paths
    assert meta.workflow_id == "wealth_character_v1"
    assert meta.cache_hit is False
    # cache hit
    meta2 = svc.generate(req)
    assert meta2.cache_hit is True
    # emotion change → miss path differs
    req2 = req.model_copy(update={"emotion": "confused", "force_regenerate": True, "seed": 424243})
    meta3 = svc.generate(req2)
    assert meta3.cache_hit is False
    assert meta3.fingerprint != meta.fingerprint


@pytest.mark.live_comfy
def test_live_wonder_niko_and_environment(settings):
    svc = ChannelComfyGenerationService(settings)
    char = GenerationRequest(
        channel="wonder_crew",
        workflow="CHARACTER_REFERENCE",
        subject="niko",
        emotion="friendly",
        direction="3/4",
        aspect_ratio="1:1",
        width=768,
        height=768,
        seed=515151,
        references=["image-3.png"],
    )
    meta = svc.generate(char)
    assert Path(meta.output_path).is_file()
    assert "wonder_crew" in meta.output_path.replace("\\", "/")
    assert meta.workflow_id.startswith("wonder_")
    env = GenerationRequest(
        channel="wonder_crew",
        workflow="ENVIRONMENT_CONCEPT",
        subject="magical island of soft lanterns",
        emotion="calm",
        composition="wide concept art establishing view",
        width=896,
        height=512,
        seed=515152,
    )
    meta_env = svc.generate(env)
    assert Path(meta_env.output_path).is_file()
    assert "environment" in meta_env.output_path.replace("\\", "/")


@pytest.mark.live_comfy
def test_live_dark_corridor(settings):
    svc = ChannelComfyGenerationService(settings)
    req = GenerationRequest(
        channel="dark_chronicles",
        workflow="DC_LOCATION",
        subject="abandoned corridor at night",
        composition="cinematic storytelling frame down a long hallway",
        lighting="low-key practical lights, deep shadows, light fog",
        camera="35mm film still",
        aspect_ratio="16:9",
        width=896,
        height=512,
        seed=616161,
    )
    meta = svc.generate(req)
    assert Path(meta.output_path).is_file()
    assert "dark_chronicles" in meta.output_path.replace("\\", "/")
    payload = json.loads(Path(meta.output_path).with_name("metadata.json").read_text(encoding="utf-8"))
    settings_block = payload.get("generation_settings") or {}
    # Visual QC may regenerate with a new seed; first attempt keeps 616161.
    if settings_block.get("dc_visual_qc_attempt", 1) <= 1 and not settings_block.get("dc_visual_qc_exhausted"):
        assert payload["seed"] == 616161
    else:
        assert isinstance(payload["seed"], int)
        attempts = settings_block.get("dc_visual_qc_attempts") or []
        assert attempts, "regen without QC attempt log"
        assert attempts[0].get("seed") == 616161
    assert "gore" not in (payload.get("prompt") or "").casefold() or "no gore" in (payload.get("prompt") or "").casefold()
    # Channel isolation + QC must not poison the positive prompt with WC leakage tokens
    assert "storybook" not in (payload.get("prompt") or "").casefold()


def test_wonder_cannot_use_wb_output_dir(settings):
    wb = load_profile(settings, "wealth_blueprint")
    wc_req = GenerationRequest(channel="wonder_crew", workflow="CHARACTER_REFERENCE", subject="niko")
    # Isolationsubject already ok; ensure validate does not allow WB workflow on WC channel
    with pytest.raises(Exception):
        validate_generation_request(
            settings,
            {"channel": "wonder_crew", "workflow": "WB_CHARACTER", "subject": "niko"},
        )
    # WB style tokens blocked on WC
    with pytest.raises(ChannelIsolationError):
        assert_request_isolated(
            load_profile(settings, "wonder_crew"),
            GenerationRequest(
                channel="wonder_crew",
                workflow="CHARACTER_REFERENCE",
                subject="niko",
                composition="juggernaut corporate finance chart",
            ),
        )
