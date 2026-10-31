"""Per-order WB style routing must change pixels and never reuse generic stills."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from atnos.production.channels.wealth_blueprint import scene_production_assets as scene_assets
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style, style_asset_tags
from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.prompts import build_prompt, WB_MEDIUM
from atnos.production.order_comfy_routing import OrderAssetRequest, OrderComfyAssetFulfiller
from atnos.production.project_profile import ProductionOrder, profile_for_order


def test_four_variants_freeze_distinct_prompts_and_brands():
    base = {"project": "wealth_blueprint", "production": {}}
    snapshots = [profile_for_order(base, ProductionOrder(topic="The same money decision", visual_style_id=key))["visual_style"]
                 for key in "ABCD"]
    assert "visual_style" not in base
    assert len({s["prompt"] for s in snapshots}) == 4
    assert len({s["asset_tag"] for s in snapshots}) == 4
    assert len({s["brand"]["background"] for s in snapshots}) == 4
    prompts = [build_prompt(GenerationRequest(
        channel="wealth_blueprint", workflow="WB_ICON_OR_SYMBOL", subject="wallet",
        visual_style_id=s["id"], visual_style_prompt=s["prompt"],
        visual_style_tags=[s["asset_tag"]], visual_style_definition_hash=s["definition_hash"],
    )) for s in snapshots]
    assert len(set(prompts)) == 4
    # The sampler uses concise weighted media, not the unfiltered catalog prose.
    for style, prompt in zip(snapshots, prompts):
        medium = (style['prompt'].split(',', 1)[0] if style['prompt'].startswith('2D ')
                  else WB_MEDIUM[style['id']])
        assert f'({medium}:1.15)' in prompt
        assert '(wallet:1.25)' in prompt
        if style['prompt'].startswith('2D '):
            assert 'turquoise and mint' in prompt
    with pytest.raises(ValueError):
        ProductionOrder(topic="A story", project="children_stories", production_scope="script_preview",
                        delivery_mode="local_only", visual_style_id="A")


def test_styled_scene_ignores_staged_generic_and_requests_own_candidate(tmp_path: Path, monkeypatch):
    style = freeze_visual_style("B")
    staged = tmp_path / "generic.png"
    Image.new("RGB", (256, 256), "red").save(staged)
    queries: list[list[str]] = []
    acquisitions: list[tuple[str, list[str], bool]] = []

    monkeypatch.setattr(scene_assets, "WealthBlueprintAssetBrain", lambda settings: object())

    def select(brain, need, *, channel, style_tags=None):
        queries.append(list(style_tags or []))
        return None

    def acquire(settings, need, *, allow_generate, visual_style=None, style_tags=None):
        acquisitions.append((visual_style["id"], list(style_tags), allow_generate))
        return None, {"status": "awaiting_approval", "asset_id": "candidate-1"}

    monkeypatch.setattr(scene_assets, "_select_library_asset", select)
    monkeypatch.setattr(scene_assets, "_try_acquisition", acquire)
    plan = {"scenes": [{"scene_id": "s01", "start_sec": 0, "end_sec": 3,
                        "layout_id": "icon_explainer", "visual_focus": "icon",
                        "on_screen_text": "Save a part of it",
                        "asset_needs": [{"type": "icon", "tags": ["savings"], "required": True}]}]}
    result = scene_assets.resolve_scene_production_assets(
        plan=plan, settings=object(), artifact_dir=tmp_path,
        visuals={"assets": {"generic": {"staged_path": str(staged), "production_role": "icon"}}},
        allow_generate=True, visual_style=style,
    )
    expected = style_asset_tags(style, tmp_path.name, "s01")
    assert result.status == "waiting"
    assert not result.assignments
    assert acquisitions == [("B", expected, True)]
    assert queries and all(tags == expected for tags in queries)
    assert result.waiting[0]["acquisition"]["status"] == "awaiting_approval"


def test_order_routing_passes_frozen_style_to_comfy(monkeypatch):
    from atnos.production import order_comfy_routing as routing

    style = freeze_visual_style("C")
    tags = style_asset_tags(style, "prod-example", "s02")
    workflow = SimpleNamespace(workflow_id="wb_icon_v1", title="WB_ICON_OR_SYMBOL", asset_type="icon")
    profile = SimpleNamespace(channel_id="wealth_blueprint")
    queries = []
    brain = SimpleNamespace(select_best=lambda query: (queries.append(query) or SimpleNamespace(
        status="missing", match=None, generation_need=None)))
    generated = []

    class FakeComfy:
        def generate(self, request):
            generated.append(request)
            return SimpleNamespace(workflow_id=workflow.workflow_id, model="test", output_path="E:/candidate.png",
                                   generation_settings={"asset_brain_id": "candidate"}, cache_hit=False,
                                   fingerprint="fingerprint")

    monkeypatch.setattr(routing, "resolve_order_comfy_profile", lambda settings, channel: (
        "wealth_blueprint", profile, "FILM_STILL_ASSETS"))
    monkeypatch.setattr(routing, "find_workflow", lambda profile, key: workflow)
    result = OrderComfyAssetFulfiller(object(), channel_service=FakeComfy(), asset_brain=brain).fulfill(
        OrderAssetRequest(channel_id="wealth_blueprint", asset_type="icon", subject="wallet",
                          visual_style_id="C", visual_style_prompt=style["prompt"], visual_style_tags=tags)
    )
    assert result.status == "awaiting_approval"
    assert result.usable_in_final_film is False
    assert queries[0].tags_all == tags
    assert generated[0].visual_style_prompt == style["prompt"]
    assert generated[0].visual_style_tags == tags


def test_manifest_carries_style_and_uses_frozen_palette():
    style = freeze_visual_style("B")
    scenes = [{"scene_id": f"s{index:02d}", "start_sec": (index - 1) * 3,
               "end_sec": index * 3, "layout_id": "fullscreen_statement",
               "visual_focus": "statement", "on_screen_text": f"Beat {index}",
               "narration_excerpt": f"Beat {index}."} for index in range(1, 4)]
    manifest = scene_plan_to_project_manifest(job_id="style-test", topic="Decision", script="Beat 1. Beat 2. Beat 3.",
                                              plan={"scenes": scenes}, duration_hint=9, visual_style=style)
    assert manifest.visual_style_id == "B"
    assert manifest.visual_style_definition_hash == style["definition_hash"]
    assert manifest.brand.background == style["brand"]["background"]
    assert manifest.brand.captionStyle == "plain_phrase"
