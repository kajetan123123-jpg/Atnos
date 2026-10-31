"""Unit tests for WB FAST/QUALITY tier resolution (no Flux)."""

from __future__ import annotations

from atnos.production.comfy_channels.model_profiles import resolve_generation_tier


WB = {
    "fast_model": "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
    "quality_model": "checkpoints/sd_xl_base_1.0_0.9vae.safetensors",
    "primary_model": "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
    "fallback_model": "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
    "quality_fallback_model": "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
    "quality_asset_types": ["character", "editorial_scene", "object"],
    "fast_asset_types": ["icon", "background"],
}


def test_wb_character_uses_quality_base():
    tier = resolve_generation_tier(WB, channel="wealth_blueprint", asset_type="character", workflow_title="WB_CHARACTER")
    assert tier["tier"] == "quality"
    assert "sd_xl_base" in tier["preferred_checkpoint"]
    assert tier["quality_mode"] is True
    assert "turbo" in (tier["fallback_checkpoint"] or "").casefold()


def test_wb_icon_uses_fast_turbo():
    tier = resolve_generation_tier(WB, channel="wealth_blueprint", asset_type="icon", workflow_title="WB_ICON_OR_SYMBOL")
    assert tier["tier"] == "fast"
    assert "turbo" in tier["preferred_checkpoint"].casefold()
    assert tier["quality_mode"] is False


def test_wb_background_uses_fast():
    tier = resolve_generation_tier(WB, channel="wealth_blueprint", asset_type="background")
    assert tier["tier"] == "fast"


def test_dc_unchanged_juggernaut_primary():
    dc = {
        "primary_model": "checkpoints/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
        "fallback_model": "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
    }
    tier = resolve_generation_tier(dc, channel="dark_chronicles", asset_type="location")
    assert tier["tier"] == "primary"
    assert "juggernaut" in tier["preferred_checkpoint"].casefold()
    assert tier["quality_mode"] is True
