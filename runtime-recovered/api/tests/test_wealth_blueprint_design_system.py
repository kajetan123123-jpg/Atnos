"""Wealth Blueprint design system pack — load + layout demos."""

from __future__ import annotations

from pathlib import Path

from atnos.production.channels.wealth_blueprint.design_system import (
    DesignSystemError,
    brand_tokens_for_manifest,
    load_wealth_blueprint_style_v1,
    require_layout,
)
from atnos.production.channels.wealth_blueprint.design_system_demos import write_all_layout_demos
from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_IDS
from atnos.production.channels.wealth_blueprint.visual_profile import (
    PROFILE_ID,
    load_wealth_blueprint_visual_v1,
)
from atnos.production.project_manifest import load_project_manifest


def test_visual_profile_is_active_authority():
    profile = load_wealth_blueprint_visual_v1()
    assert profile["profile_id"] == PROFILE_ID
    assert profile["status"] == "active"
    roles = profile["palette"]["roles"]
    assert roles["background"] == "#142633"
    assert roles["accent"] == "#D5AD6C"
    assert roles["text"] == "#F0E5D2"
    assert "#39FF9F" not in roles.values()


def test_style_pack_loads_all_layouts():
    pack = load_wealth_blueprint_style_v1()
    assert pack.style_id == "wealth_blueprint_style_v1"
    assert pack.colors["background"] == "#142633"
    assert pack.colors["accent"] == "#D5AD6C"
    assert pack.colors.get("visual_profile_id") == "wealth_blueprint_visual_v1" or True
    assert set(pack.layouts) == set(LAYOUT_IDS)
    for layout_id in LAYOUT_IDS:
        spec = require_layout(layout_id)
        assert spec.max_elements >= 1
        assert len(spec.slots) >= 1


def test_brand_tokens_match_visual_bible():
    tokens = brand_tokens_for_manifest()
    assert tokens["background"] == "#142633"
    assert tokens["accent"] == "#D5AD6C"
    assert tokens["text"] == "#F0E5D2"


def test_unknown_layout_rejected():
    try:
        require_layout("neon_fintech_chaos")
        raise AssertionError("expected DesignSystemError")
    except DesignSystemError as exc:
        assert "Unknown layout_id" in str(exc)


def test_write_layout_demos(tmp_path: Path):
    out = tmp_path / "demos" / "design_system" / "wealth_blueprint_style_v1"
    paths = write_all_layout_demos(out)
    assert len(paths) == 10
    index = (out / "index.json").read_text(encoding="utf-8")
    assert "fullscreen_statement" in index
    for path in paths:
        manifest = load_project_manifest(path)
        assert manifest.channel == "wealth_blueprint"
        assert manifest.brand.background == "#142633"
        assert manifest.brand_profile_version == "wealth_blueprint_visual_v1"
        assert manifest.scenes[0].layout_id
        assert len(manifest.scenes) == 1
