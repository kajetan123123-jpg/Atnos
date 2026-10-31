"""Dark Chronicles visual QC — good scenes + controlled bad fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from atnos.production.comfy_channels.dark_chronicles_visual_qc import (
    DcVisualQcPolicy,
    GoreProfile,
    adjust_request_for_failures,
    resolve_gore_profile,
    run_dc_visual_qc,
    write_dc_qc_fixture,
)
from atnos.production.comfy_channels.models import GenerationRequest


GOOD = ("corridor", "forest", "motel", "basement", "house_exterior")
BAD = ("cartoon", "random_text", "overexposed", "excessive_gore")


@pytest.fixture()
def fixtures_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dc_qc"
    for kind in (*GOOD, *BAD):
        write_dc_qc_fixture(kind, root / f"{kind}.png")
    return root


def test_good_scenes_mostly_pass(fixtures_dir: Path):
    policy = DcVisualQcPolicy(gore_profile=GoreProfile.NONE, allow_ocr=True, allow_vision=False)
    results = {k: run_dc_visual_qc(fixtures_dir / f"{k}.png", policy=policy) for k in GOOD}
    passed = sum(1 for r in results.values() if r.passed)
    # Allow at most one soft false positive on synthetic plates
    assert passed >= 4, {k: r.failure_reasons for k, r in results.items()}
    for r in results.values():
        assert "CARTOON_LEAKAGE" not in r.failure_reasons or r.passed


def test_bad_fixtures_detected(fixtures_dir: Path):
    policy = DcVisualQcPolicy(gore_profile=GoreProfile.NONE, allow_ocr=True, allow_vision=False)
    cartoon = run_dc_visual_qc(fixtures_dir / "cartoon.png", policy=policy)
    assert not cartoon.passed
    assert "CARTOON_LEAKAGE" in cartoon.failure_reasons

    over = run_dc_visual_qc(fixtures_dir / "overexposed.png", policy=policy)
    assert not over.passed
    assert "extreme_exposure" in over.failure_reasons or "LIGHTING" in over.failure_reasons

    gore = run_dc_visual_qc(fixtures_dir / "excessive_gore.png", policy=policy)
    assert not gore.passed
    assert "GORE_LEVEL" in gore.failure_reasons

    text = run_dc_visual_qc(fixtures_dir / "random_text.png", policy=policy)
    assert not text.passed
    # OCR if available, else atmosphere/cinematic may still fail; TEXT preferred
    assert (
        "TEXT_ARTIFACTS" in text.failure_reasons
        or any(c.name == "TEXT_ARTIFACTS" and c.score > 0.2 for c in text.categories)
        or "ATMOSPHERE" in text.failure_reasons
    )


def test_gore_high_rejected_when_profile_none(fixtures_dir: Path):
    policy = DcVisualQcPolicy(gore_profile=GoreProfile.NONE, allow_vision=False)
    result = run_dc_visual_qc(fixtures_dir / "excessive_gore.png", policy=policy)
    assert result.verdict == "FAIL_VISUAL_QC"
    assert result.gore_detected > 0.35


def test_gore_profile_resolve_default_none():
    req = GenerationRequest(channel="dark_chronicles", workflow="DC_LOCATION", subject="abandoned corridor")
    assert resolve_gore_profile(req) == GoreProfile.NONE


def test_adjust_request_changes_seed_and_composition():
    req = GenerationRequest(
        channel="dark_chronicles",
        workflow="DC_LOCATION",
        subject="motel exterior",
        seed=100,
        composition="night",
    )
    from atnos.production.comfy_channels.dark_chronicles_visual_qc import DcVisualQcResult

    fake = DcVisualQcResult(
        passed=False,
        verdict="FAIL_VISUAL_QC",
        failure_reasons=["CARTOON_LEAKAGE", "TEXT_ARTIFACTS"],
        metrics={},
    )
    nxt = adjust_request_for_failures(req, fake, attempt=0)
    assert nxt.force_regenerate is True
    assert nxt.seed != req.seed
    assert "cartoon" in (nxt.composition or "").casefold() or "cinematic" in (nxt.composition or "").casefold()
    assert "text" in (nxt.composition or "").casefold() or "Avoid" in (nxt.composition or "")


@pytest.fixture()
def settings():
    from atnos.core.config import get_settings

    return get_settings()


def test_dc_cartoon_regen_does_not_block_isolation(settings):
    """Live failure mode: CARTOON_LEAKAGE regen used to inject 'storybook' into composition → isolation ERROR."""
    from atnos.production.comfy_channels.dark_chronicles_visual_qc import DcVisualQcResult
    from atnos.production.comfy_channels.isolation import assert_request_isolated
    from atnos.production.comfy_channels.profiles import load_profile
    from atnos.production.comfy_channels.prompts import build_prompt, negative_prompt_with_request

    req = GenerationRequest(
        channel="dark_chronicles",
        workflow="DC_LOCATION",
        subject="abandoned corridor at night",
        composition="cinematic storytelling frame",
        lighting="low-key",
        seed=1,
    )
    fake = DcVisualQcResult(
        passed=False,
        verdict="FAIL_VISUAL_QC",
        failure_reasons=["CARTOON_LEAKAGE"],
        metrics={},
    )
    nxt = adjust_request_for_failures(req, fake, attempt=0)
    assert_request_isolated(load_profile(settings, "dark_chronicles"), nxt)
    positive = (nxt.composition or "").casefold().split(" avoid:")[0]
    assert "storybook" not in positive
    assert "wonder crew" not in positive
    prompt = build_prompt(nxt)
    assert "storybook" not in prompt.casefold()
    assert "avoid:" not in prompt.casefold()
    neg = negative_prompt_with_request("dark_chronicles", nxt)
    assert "cartoon" in neg.casefold() or "illustration" in neg.casefold()


def test_categories_are_separate(fixtures_dir: Path):
    result = run_dc_visual_qc(
        fixtures_dir / "corridor.png",
        policy=DcVisualQcPolicy(allow_vision=False),
    )
    names = [c.name for c in result.categories]
    assert names == [
        "CINEMATIC_COMPOSITION",
        "LIGHTING",
        "ATMOSPHERE",
        "BELIEVABLE_GEOMETRY",
        "CARTOON_LEAKAGE",
        "TEXT_ARTIFACTS",
        "GORE_LEVEL",
        "AI_ARTIFACTS",
    ]
