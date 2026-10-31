"""Actual planner/acquisition/compiler chain keeps unprinted income props literal."""

import re
from types import SimpleNamespace

import pytest

from atnos.production.channels.wealth_blueprint.deterministic import _raise_illustration_for
from atnos.production.channels.wealth_blueprint import scene_production_assets
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style
from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.prompts import build_prompt, negative_prompt_with_request
from atnos.production import order_comfy_routing


@pytest.mark.parametrize("style_id", list("ABCD"))
def test_income_literal_props_reach_final_positive_without_currency_triggers(monkeypatch, style_id):
    seen = []

    class AcquisitionBoundary:
        def __init__(self, settings):
            pass

        def fulfill(self, request):
            seen.append(request)
            return SimpleNamespace(status="awaiting_approval", usable_in_final_film=False,
                                   reason="review required", asset_brain_id=None, output_path=None)

    monkeypatch.setattr(order_comfy_routing, "OrderComfyAssetFulfiller", AcquisitionBoundary)
    text = "An unexpected raise arrives, but savings stay the same. What happens to the extra money?"
    spec = _raise_illustration_for(text)
    assert spec["function"] == "extra_income"
    assert spec["concept"] == "wb_raise_extra_income"
    assert set(spec["objects"]) == {"income", "money"}
    assert "additional income" in spec["state"]
    assert "does not by itself prove" in spec["understand"]
    need = {"type": "background", "asset_subject": spec["subject"],
            "asset_composition": spec["composition"], "illustration_function": spec["function"],
            "object_roles": spec["objects"], "text": text}
    style = freeze_visual_style(style_id)
    scene_production_assets._try_acquisition(object(), need, allow_generate=True,
                                           visual_style=style, style_tags=["scope"])
    assert len(seen) == 1
    order_request = seen[0]
    assert order_request.subject.startswith("one open cream paper envelope")
    # Compiler receives the exact captured acquisition fields; the simulated
    # boundary does not replace or shorten the actual planner direction.
    generation_request = GenerationRequest(
        channel=order_request.channel_id, workflow="WEALTH_BACKGROUND",
        subject=order_request.subject, composition=order_request.text,
        asset_type=order_request.asset_type, width=order_request.width, height=order_request.height,
        visual_style_id=order_request.visual_style_id, visual_style_prompt=order_request.visual_style_prompt,
        visual_style_tags=order_request.visual_style_tags,
        visual_style_definition_hash=order_request.visual_style_definition_hash,
    )
    positive = build_prompt(generation_request)
    negative = negative_prompt_with_request("wealth_blueprint", generation_request)
    assert not re.search(r"\b(?:pay|money|banknotes?|currency|portraits?)\b", positive, re.I)
    assert positive.startswith("(one open cream paper envelope")
    assert ":1.25), (2D" in positive
    assert "unprinted teal paper cards" in positive
    assert "blank teal card faces visible inside" in positive
    assert "Open triangular flap" in positive
    assert "Paper folds, soft shadows" in positive
    assert "entire envelope centered with side margins" in positive
    assert "Quiet upper and lower text areas" in positive
    assert "portraits" in negative and "printed currency" in negative
    assert (generation_request.width, generation_request.height) == (768, 1344)
    assert text == need["text"]  # Visual conditioning does not edit the narration.


@pytest.mark.parametrize("text", [
    "A pay raise brings extra income.",
    "A higher paycheck arrives.",
    "You earn more after a salary increase.",
    "Your income grows, but does saving follow?",
])
def test_income_intro_variants_share_literal_prop_without_weakening_meaning(text):
    spec = _raise_illustration_for(text)
    assert spec["function"] == "extra_income"
    assert spec["subject"].startswith("one open cream paper envelope")
    assert "money" in spec["objects"]
    assert not re.search(r"\b(?:pay|money|banknotes?|portraits?)\b", spec["subject"], re.I)
    assert "does not by itself prove an increase in savings" in spec["understand"]
