from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atnos.production import wb_image_api as lane
from atnos.production.channels.wealth_blueprint.deterministic import _source_physical_illustration_for
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style
from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.prompts import build_prompt
from atnos.production.order_store import ProductionBlocked


def test_bill_and_bag_prompt_names_each_prop_once_and_preserves_wb_art_direction():
    scene = _source_physical_illustration_for([
        "A blank bill prop sits beside a small shopping bag."
    ])
    assert scene is not None
    style = freeze_visual_style("D")
    positive_composition = scene["composition"].split(" Avoid:", 1)[0]
    request = GenerationRequest(
        channel="wealth_blueprint", workflow="WB_BACKGROUND", subject=scene["subject"],
        composition=positive_composition, visual_style_id="D", visual_style_prompt=style["prompt"],
        visual_style_tags=[style["asset_tag"]], visual_style_definition_hash=style["definition_hash"],
    )
    prompt = build_prompt(request)
    assert prompt.count("paper card") == 1
    assert prompt.count("small unbranded shopping bag") == 1
    assert "paper slip" not in prompt
    assert "thin horizontal" in prompt
    assert "money prop" not in prompt.casefold()
    assert "cinematic directional warm-gold practical light" in prompt.casefold()
    assert "deep charcoal-navy shadows" in prompt.casefold()
    assert "soft painted 2.5d depth" in prompt.casefold()
    assert "Avoid: banknotes" not in prompt  # excluded content stays in negative conditioning


def test_bill_and_bag_acquisition_uses_a_compact_two_prop_prompt(monkeypatch):
    from atnos.production import order_comfy_routing
    from atnos.production.channels.wealth_blueprint import scene_production_assets

    captured = []

    class CaptureFulfiller:
        def __init__(self, settings):
            pass

        def fulfill(self, request):
            captured.append(request)
            return SimpleNamespace(
                status="awaiting_approval", usable_in_final_film=False,
                reason="review required", asset_brain_id=None, output_path=None,
            )

    monkeypatch.setattr(order_comfy_routing, "OrderComfyAssetFulfiller", CaptureFulfiller)
    style = freeze_visual_style("D")
    need = {
        "type": "background",
        "illustration_function": "bill_and_bag",
        "asset_subject": "one blank-faced teal paper slip beside one small unbranded shopping bag",
        "asset_composition": (
            "Keep both requested props fully visible with a clear gap; leave the paper face blank for a later overlay. "
            "Place the pair on a home-office desk with quiet caption areas. Cinematic directional warm-gold practical light, "
            "deep charcoal-navy shadows, cream-paper surfaces, restrained teal, soft painted 2.5D depth and paper texture. "
            "Avoid: banknotes, bills, currency portraits, stacks or piles of cash, duplicate slips, extra bags, "
            "unrelated desk props, flat gray line art, monochrome shading."
        ),
        "object_roles": {"money": "one blank-faced teal paper slip", "bag": "one small unbranded shopping bag"},
    }

    scene_production_assets._try_acquisition(
        object(), need, allow_generate=True, visual_style=style, style_tags=[style["asset_tag"]]
    )

    assert len(captured) == 1
    request = captured[0]
    assert request.subject == "one unprinted thin horizontal teal paper card beside one small plain shopping bag"
    positive_text = request.text.split(" Avoid:", 1)[0]
    assert len(positive_text) < 200
    assert len(request.text) < 400
    assert "home-office" not in request.text
    assert "quiet caption areas" not in request.text
    assert "Avoid: banknotes" in request.text  # Exclusions remain available to negative conditioning.

    compiled = build_prompt(GenerationRequest(
        channel="wealth_blueprint", workflow="WEALTH_BACKGROUND", subject=request.subject,
        composition=request.text, visual_style_id=request.visual_style_id,
        visual_style_prompt=request.visual_style_prompt, visual_style_tags=request.visual_style_tags,
        visual_style_definition_hash=request.visual_style_definition_hash,
    ))
    assert len(compiled) < 500
    assert compiled.count("unprinted thin horizontal teal paper card") == 1
    assert compiled.count("small plain shopping bag") == 1
    assert "home-office" not in compiled
    assert "A complete" not in compiled


def test_image_brief_uses_frozen_medium_without_reintroducing_plural_paper_props():
    scene = _source_physical_illustration_for([
        "A blank bill prop sits beside a small shopping bag."
    ])
    assert scene is not None
    style = freeze_visual_style("D")
    brief = lane.build_image_brief(
        {"asset_subject": scene["subject"], "asset_composition": scene["composition"]}, style
    )
    assert style["prompt"] in brief
    assert "no duplicates, piles or additional paper props" in brief
    assert "cinematic depth" in brief
    assert "plain teal paper slips" not in brief


def test_whole_plan_preflight_blocks_later_unsupported_scene_before_any_candidate_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    scenes = [{"scene_id": "s01"}, {"scene_id": "native-chart"}, {"scene_id": "s04"}]
    monkeypatch.setattr(lane, "load_image_lane", lambda *_: {
        "provider": "openai-cloud", "digest": "test-auth", "max_requests": 20,
        "reservation_usd_per_request": 1.0,
    })
    monkeypatch.setattr(lane, "image_selection_plan", lambda _: {"scenes": scenes, "claims": []})
    monkeypatch.setattr(lane, "_native_scene_for_image_trial",
                        lambda scene, **_: "chart" if scene["scene_id"] == "native-chart" else None)
    needs_calls = []

    def need_for_scene(scene):
        needs_calls.append(scene["scene_id"])
        if scene["scene_id"] == "s01":
            return [{"type": "background", "asset_subject": "one blank teal slip", "asset_composition": ""}]
        if scene["scene_id"] == "s04":
            return [{"type": "icon", "asset_subject": "unsupported moving X"}]
        pytest.fail("native scene must retain its existing exemption")

    monkeypatch.setattr("atnos.production.channels.wealth_blueprint.scene_production_assets._needs_for_scene",
                        need_for_scene)
    monkeypatch.setattr(lane, "_generate_candidate", lambda *_: pytest.fail("image model call before whole-plan validation"))
    monkeypatch.setattr(lane, "_generate_local_candidate", lambda *_: pytest.fail("local model call before whole-plan validation"))
    run = AsyncMock(side_effect=AssertionError("stage executor reached before whole-plan validation"))
    context = SimpleNamespace(
        job_id="prod-preflight", artifact_dir=tmp_path,
        artifacts={"project_profile": {"visual_style": freeze_visual_style("D")}},
    )
    executor = SimpleNamespace(store=SimpleNamespace(settings=object()), context=context, run=run)

    with pytest.raises(ProductionBlocked, match="supported physical illustration for every scene"):
        asyncio.run(lane.produce_image_candidates(executor))

    assert needs_calls == ["s01", "s04"]
    run.assert_not_awaited()


def test_order_preflight_freezes_the_validated_plan_for_the_same_script_and_lane(monkeypatch):
    scenes = [{"scene_id": "s01", "layout_id": "fullscreen_statement"}]
    plan = {"scenes": scenes, "claims": []}
    needs = [(True, None)]
    monkeypatch.setattr(lane, "image_selection_plan", lambda _: plan)
    monkeypatch.setattr(lane, "_preflight_image_plan", lambda supplied: needs if supplied is plan else pytest.fail("plan changed"))
    context = SimpleNamespace(artifacts={
        "script": {"segments": [{"id": "s01", "text": "A clear thought."}]},
        "project_profile": {"visual_style": {"id": "D"}},
    })
    image_lane = {"provider": "comfyui-local", "digest": "lane-v1"}

    result = lane.preflight_image_plan_for_order(context, image_lane)

    assert result == {"scene_count": 1, "status": "passed"}
    assert context.artifacts["_image_plan_preflight"]["plan"] is plan
    assert context.artifacts["_image_plan_preflight"]["scene_needs"] is needs
    assert context.artifacts["_image_plan_preflight"]["lane_digest"] == "lane-v1"


def test_goal_choice_storyboard_plans_supported_physical_assets_for_every_scene():
    from atnos.production.wb_image_api import image_selection_plan

    script = {
        "qa": {"status": "passed"}, "claims": [],
        "segments": [
            {"id": "s01", "text": "You cannot spend the same money on two goals at once.",
             "on_screen": "Same money, two goals?", "visual_intent": "footage of a person looking at two different budgeting apps on a phone, with a split screen showing two separate goals", "claim_ids": []},
            {"id": "s02", "text": "Compare the vacation savings plan with the debt payoff timeline.",
             "on_screen": "Compare deadlines", "visual_intent": "flow diagram showing a single savings pot splitting into two paths: one labeled Vacation and the other Debt Payoff", "claim_ids": []},
            {"id": "s03", "text": "Confirm the goal you selected and monitor your plan.",
             "on_screen": "Choose one goal", "visual_intent": "person writing in a notebook, with the words Vacation and Debt Payoff crossed out and Focus on one written above", "claim_ids": []},
            {"id": "s04", "text": "Ask yourself: what is your most important goal right now?",
             "on_screen": "What's your priority?", "visual_intent": "person looking directly at the camera, with a text card on screen saying What's your priority?", "claim_ids": []},
        ],
    }
    context = SimpleNamespace(artifacts={
        "script": script,
        "production_order": {"topic": "Choosing between savings goals", "direction": "", "optional_notes": ""},
        "project_profile": {"visual_style": freeze_visual_style("D")},
    })

    plan = image_selection_plan(context)
    needs = lane._preflight_image_plan(plan)

    assert [scene["scene_id"] for scene in plan["scenes"]] == ["s01", "s02", "s03", "s04"]
    assert [need["illustration_function"] for _, need in needs] == [
        "two_goal_phone", "one_pot_two_goals", "goal_notebook", "priority_prompt_card",
    ]
    assert all(is_native or need["type"] == "background" for is_native, need in needs)


def test_real_planner_chart_uses_validated_script_claims_and_unverified_chart_still_blocks():
    from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic

    claim = {
        "id": "c1", "operation": "compound", "operands": [10.0, 0.08, 30.0],
        "result": 100.6265688907345, "unit": "dollars", "spoken_result": "one hundred one",
        "spoken_rounding": 1,
        "assumptions": "One initial deposit of 10 dollars, no recurring deposits, 30 years at an assumed 8 percent annual rate.",
    }
    script = {
        "qa": {"status": "passed"}, "claims": [claim],
        "segments": [
            {"id": "0", "type": "hook", "text": "A purchase has another possible use.",
             "on_screen": "$10 ≠ $10", "visual_intent": "A $10 bill sits beside a small shopping bag.",
             "claim_ids": []},
            {"id": "1", "type": "practical", "text": "The same money could go into an investment account.",
             "on_screen": "TODAY / FUTURE", "visual_intent": "A bill sits beside an investment account on a phone screen.",
             "claim_ids": []},
            {"id": "2", "type": "explanation",
             "text": "If that ten dollars earned an assumed eight percent a year for thirty years, it could grow to roughly one hundred one dollars.",
             "on_screen": "Compound growth", "visual_intent": "$10 → $22 → $47 → $101. A small graph grows upward.",
             "claim_ids": ["c1"]},
            {"id": "3", "type": "practical", "text": "Coffee can still be a worthwhile choice.",
             "on_screen": "Enjoy what matters", "visual_intent": "An ordinary coffee cup stays upright and uncrossed, with no X or hand, as a calm reassurance.",
             "claim_ids": []},
            {"id": "4", "type": "practical", "text": "Weigh the choice before spending.",
             "on_screen": "BUY NOW / BUILD LATER", "visual_intent": "A balance scale appears: left BUY NOW, right BUILD LATER. Alex stands between them.",
             "claim_ids": []},
            {"id": "5", "type": "cta", "text": "Follow Wealth Blueprint for more.",
             "on_screen": "PRICE ≠ COST", "visual_intent": "Wealth Blueprint logo appears for the closing call to action.",
             "claim_ids": []},
        ],
    }
    context = SimpleNamespace(artifacts={
        "script": script,
        "production_order": {"topic": "A hypothetical compound example", "direction": "", "intake": {"kind": "user_hypothetical"}},
        "script_calculations": {"claims": {"c1": claim}},
        "project_profile": {"visual_style": freeze_visual_style("D")},
    })

    planner_input, _ = lane._image_scene_planner_input(context)
    serialized = plan_scenes_deterministic(planner_input).model_dump(mode="json")
    assert "claims" not in serialized  # ScenePlan serializes chart data without its claim ledger.

    plan = lane.image_selection_plan(context)
    assert plan["claims"] == [claim]
    chart = next(scene for scene in plan["scenes"] if scene.get("chart"))
    chart_index = plan["scenes"].index(chart)
    assert lane._native_scene_for_image_trial(
        chart, index=chart_index, total=len(plan["scenes"]), claims=plan["claims"]
    ) == "chart"
    assert lane._preflight_image_plan(plan)[chart_index] == (True, None)

    invalid = copy.deepcopy(plan)
    invalid_chart = next(scene for scene in invalid["scenes"] if scene.get("chart"))
    invalid_chart["chart"]["claim_ids"] = ["unverified-claim"]
    with pytest.raises(ProductionBlocked, match="supported physical illustration for every scene"):
        lane._preflight_image_plan(invalid)
