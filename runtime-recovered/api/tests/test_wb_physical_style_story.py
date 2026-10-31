"""The four WB trials must show literal, distinct art in the normal manifest."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image

from atnos.production.channels.wealth_blueprint import (
    NarrationSegment, ScenePlannerInput, WealthBlueprintScenePlanner, validate_scene_plan,
)
from atnos.production.channels.wealth_blueprint import scene_production_assets as scene_assets
from atnos.production.channels.wealth_blueprint.deterministic import (
    _raise_illustration_for,
    _source_physical_illustration_for,
)
from atnos.production.channels.wealth_blueprint.scene_production_assets import _needs_for_scene
from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style
from atnos.production.comfy_channels.models import GenerationRequest


def _plan(style_id: str):
    texts = [
        "A pay raise brings extra income, but does it make you richer?",
        "A new monthly subscription becomes a recurring expense that absorbs the extra income.",
        "Before spending more, set aside part of the raise in separate savings.",
        "That reserved money remains available for your future goal; saving alone does not guarantee a return.",
    ]
    segments = [NarrationSegment(id=f"n{i}", text=text, on_screen=heading,
                                 start=i * 8.0, end=(i + 1) * 8.0)
                for i, (text, heading) in enumerate(zip(texts, (
                    "More income, same savings?", "A recurring cost", "Choose what you keep", "Keep your future options",
                ), strict=True))]
    return WealthBlueprintScenePlanner().plan(ScenePlannerInput(
        script=" ".join(texts), segments=segments,
        profile={"topic": "Why a pay raise can leave savings unchanged", "disable_visual_story": True},
        design_system={"visual_style_id": style_id},
    ))


@pytest.mark.parametrize("style_id", list("ABCD"))
def test_style_art_is_used_in_all_four_states_including_the_ending(tmp_path, style_id):
    plan = _plan(style_id)
    assert plan.validation is not None and plan.validation.ok
    assert [s.meaning_contract["illustration_function"] for s in plan.scenes] == [
        "extra_income", "recurring_expense", "savings_choice", "future_goal",
    ]
    assert all(s.layout_id == "icon_explainer" for s in plan.scenes)
    assert all(s.visual_intent.scene_mode == "illustration" for s in plan.scenes)
    assert all(s.visual_intent.action == "hold" for s in plan.scenes)
    assert all(not s.use_alex and s.chart is None and s.number_value is None for s in plan.scenes)
    assert all(s.asset_needs[0].type == "background"
               for s in plan.scenes)
    assert plan.scenes[2].meaning_contract["prop_ids"]["savings"] == plan.scenes[3].meaning_contract["prop_ids"]["savings"]
    images = {}
    for i, scene in enumerate(plan.scenes):
        path = tmp_path / f"{scene.scene_id}.png"
        Image.new("RGB", (64, 64), (i * 30, 40, 60)).save(path)
        images[scene.scene_id] = path
    manifest = scene_plan_to_project_manifest(
        job_id="style-states", topic="Pay raise", script=plan.script,
        plan=plan.model_dump(mode="json"), duration_hint=32,
        scene_images=images, visual_style=freeze_visual_style(style_id),
    )
    assert len(manifest.scenes) == 4
    assert all(scene.scene_type.value == "ImageScene" for scene in manifest.scenes)
    assert all(scene.assets and scene.semantic_overlay_kind is None for scene in manifest.scenes)
    assert [scene.assets[0].path for scene in manifest.scenes] == [str(images[s.scene_id]) for s in plan.scenes]


def test_duplicate_stills_do_not_gain_layout_continuity_exception():
    plan = _plan("A")
    first = plan.scenes[0]
    repeated = [first.model_copy(update={
        "scene_id": f"s{i}", "start_sec": i * 8.0, "end_sec": (i + 1) * 8.0,
        "animations": [],
    }) for i in range(3)]
    result = validate_scene_plan(plan.model_copy(update={"scenes": repeated, "duration_sec": 24}))
    assert not result.ok
    assert "layout_repeat" in {issue.code for issue in result.issues}


def test_states_follow_meaning_not_scene_position():
    assert _raise_illustration_for("Set aside some extra income in separate savings.")["function"] == "savings_choice"
    assert _raise_illustration_for("A subscription absorbs that raise every month.")["function"] == "recurring_expense"
    assert _raise_illustration_for("A pay raise brings extra income.")["function"] == "extra_income"
    assert _raise_illustration_for("That money remains available for a future goal.")["function"] == "future_goal"
    assert _raise_illustration_for("An unrelated sentence about geography.") is None


@pytest.mark.parametrize("style_id", list("ABCD"))
@pytest.mark.parametrize(("source_intent", "function", "subject_terms", "forbidden_terms"), [
    ("A $10 bill is shown next to a small shopping bag.", "bill_and_bag",
     ("blank-faced teal", "shopping bag"), ("$10", "currency symbol")),
    ("A bill sits beside an investment account on a phone screen.", "bill_and_account_ui",
     ("blank cream screen", "blank-faced teal"), ("shows a balance of", "shows gains")),
    ("A coffee cup stays upright and uncrossed; no red X is visible. That's not the point.", "coffee_reassurance",
     ("coffee cup", "uncrossed"), ("red x crosses the cup",)),
    ("A balance scale appears: left BUY NOW, right BUILD LATER. Alex stands between them.",
     "balance_scale", ("balance scale", "unmarked pans"), ("Alex", "BUY NOW")),
])
def test_source_visual_intent_maps_only_bounded_static_props(
    style_id, source_intent, function, subject_terms, forbidden_terms,
):
    segment = NarrationSegment(
        id="segment-source-1", text="Consider the choice and what it means.",
        on_screen="A useful choice", visual_intent=source_intent, start=0, end=4,
    )
    plan = plan_scenes_deterministic(ScenePlannerInput(
        script=segment.text, segments=[segment],
        profile={"topic": "Unrelated sample topic"},
        design_system={"visual_style_id": style_id},
    ))
    assert len(plan.scenes) == 1
    scene = plan.scenes[0]
    assert scene.narration_segment_ids == ["segment-source-1"]
    assert scene.source_visual_intents == [source_intent]
    assert scene.meaning_contract["illustration_function"] == function
    assert scene.layout_id == "icon_explainer"
    assert scene.visual_intent.action == "hold"
    assert scene.visual_intent.final_state
    need = _needs_for_scene(scene.model_dump(mode="json"))[0]
    assert need["type"] == "background" and need["required"] is True
    assert all(term.casefold() in need["asset_subject"].casefold() + " " + need["asset_composition"].casefold()
               for term in subject_terms)
    combined = (need["asset_subject"] + " " + need["asset_composition"]).casefold()
    assert all(term.casefold() not in combined for term in forbidden_terms)


def test_unmapped_source_intent_stays_without_physical_subject():
    segment = NarrationSegment(
        id="unknown", text="Consider an unrelated idea.", on_screen="A title",
        visual_intent="A spaceship floats beside a flower.", start=0, end=4,
    )
    plan = plan_scenes_deterministic(ScenePlannerInput(
        script=segment.text, segments=[segment], profile={"topic": "Any topic"},
        design_system={"visual_style_id": "A"},
    ))
    scene = plan.scenes[0]
    need = _needs_for_scene(scene.model_dump(mode="json"))[0]
    assert scene.narration_segment_ids == ["unknown"]
    assert "asset_subject" not in need


def test_bill_account_uses_same_single_slip_and_compiles_exclusions_as_negative():
    from atnos.production.comfy_channels.models import GenerationRequest
    from atnos.production.comfy_channels.prompts import build_prompt, negative_prompt_with_request

    spec = _source_physical_illustration_for([
        "A bill sits beside an investment account on a phone screen.",
    ])
    bag = _source_physical_illustration_for([
        "A bill sits beside a small shopping bag.",
    ])
    assert spec is not None and bag is not None
    assert spec["objects"]["money"] == bag["objects"]["money"] == "one blank-faced teal paper slip"
    assert spec["subject"].startswith("one blank-faced teal paper slip")
    assert "exactly one" in spec["composition"].casefold() and "one simple phone" in spec["composition"].casefold()
    assert "warm-gold" in spec["composition"] and "charcoal-navy" in spec["composition"]

    request = GenerationRequest(channel="wealth_blueprint", workflow="WB_OBJECT",
        subject=spec["subject"], composition=spec["composition"], visual_style_id="D",
        visual_style_prompt=freeze_visual_style("D")["prompt"], visual_style_tags=["scope"])
    positive = build_prompt(request).casefold()
    negative = negative_prompt_with_request("wealth_blueprint", request).casefold()
    assert "avoid:" not in positive
    assert "duplicate slips" in negative and "currency portraits" in negative
    assert "blank cream screen" in positive and "charcoal-navy" in positive


def test_exact_paper_slip_synonym_maps_bounded_props_but_generic_paper_and_chart_do_not():
    bag = _source_physical_illustration_for([
        "One blank-faced teal paper slip sits beside one small unbranded shopping bag.",
    ])
    account = _source_physical_illustration_for([
        "One blank-faced teal paper slip sits beside one blank-screen phone for an investment account.",
    ])
    assert bag is not None and bag["function"] == "bill_and_bag"
    assert account is not None and account["function"] == "bill_and_account_ui"
    assert bag["objects"]["money"] == account["objects"]["money"] == "one blank-faced teal paper slip"

    assert _source_physical_illustration_for(["A sheet of paper sits beside a shopping bag."]) is None
    assert _source_physical_illustration_for(["A simple chart shows growth over time."]) is None


@pytest.mark.parametrize("source_intent", [
    "An ordinary coffee cup remains upright and uncrossed.",
    "A coffee cup is shown with no red X or hand in the image.",
    "A coffee cup, not crossed out, reminds us that this is not about banning coffee.",
])
def test_explicit_static_uncrossed_coffee_state_is_supported(source_intent):
    spec = _source_physical_illustration_for([source_intent])
    assert spec is not None
    assert spec["function"] == "coffee_reassurance"
    assert "uncrossed" in spec["state"].casefold()


@pytest.mark.parametrize("source_intent", [
    "A coffee cup remains crossed-out with a red X.",
    "A coffee cup is shown; the red X remains on it.",
    "A hand gently moves the red X away from the coffee cup.",
    "Coffee is not the point; a red X briefly crosses the cup and disappears.",
    "A coffee cup appears with a red X moving away.",
])
def test_crossed_or_moving_coffee_marks_stay_unsupported(source_intent):
    assert _source_physical_illustration_for([source_intent]) is None


def test_source_image_states_reach_normal_remotion_while_chart_and_endcard_stay_native(tmp_path):
    claim = {
        "id": "c1", "operation": "compound", "operands": [10, 0.08, 30],
        "result": 100.6265688907345, "unit": "dollars", "spoken_result": "one hundred one",
        "assumptions": "One initial deposit, annual compounding, no additional deposits.",
    }
    segments = [
        NarrationSegment(id="hook", text="A purchase costs more than its receipt.",
            on_screen="$10 ≠ $10", visual_intent="A $10 bill is next to a small shopping bag.", start=0, end=4),
        NarrationSegment(id="chart", text="If that ten dollars earned an assumed eight percent a year for thirty years, it could grow to roughly one hundred one dollars.",
            on_screen="Compound growth", visual_intent="A small graph grows upward. Text: Compound growth.", start=4, end=10),
        NarrationSegment(id="end", text="Follow Wealth Blueprint for more practical ideas.",
            on_screen="Price is not cost", visual_intent="Wealth Blueprint logo appears for the closing call to action.", start=10, end=15),
    ]
    plan = plan_scenes_deterministic(ScenePlannerInput(
        script=" ".join(segment.text for segment in segments), segments=segments,
        profile={"topic": "An unrelated neutral topic"}, design_system={"visual_style_id": "A"},
        claims=[claim], segment_claim_map={"chart": ["c1"]},
    ))
    assert [scene.narration_segment_ids for scene in plan.scenes] == [["hook"], ["chart"], ["end"]]
    assert plan.scenes[0].layout_id == "icon_explainer"
    subject = plan.scenes[0].meaning_contract["asset_subject"]
    composition = plan.scenes[0].meaning_contract["asset_composition"]
    assert subject == "one blank-faced teal paper slip beside one small unbranded shopping bag"
    assert composition.count("Avoid:") == 1
    assert "duplicate slips" in composition and "extra bags" in composition
    assert "warm-gold" in composition and "charcoal-navy" in composition
    assert plan.scenes[1].meaning_contract["native_scene_role"] == "chart"
    assert plan.scenes[1].chart and plan.scenes[1].chart.is_quantitative
    assert plan.scenes[2].meaning_contract["native_scene_role"] == "end_card"

    image = tmp_path / "hook.png"
    Image.new("RGB", (64, 64), "#163142").save(image)
    manifest = scene_plan_to_project_manifest(
        job_id="source-physical-native-scenes", topic="An unrelated neutral topic", script=plan.script,
        plan=plan.model_dump(mode="json"), duration_hint=15,
        scene_images={plan.scenes[0].scene_id: image}, visual_style=freeze_visual_style("A"),
        verified_claims=[claim],
    )
    assert [scene.scene_type.value for scene in manifest.scenes] == ["ImageScene", "ChartScene", "CTAScene"]
    assert manifest.scenes[0].assets
    assert not manifest.scenes[1].assets and not manifest.scenes[2].assets


def test_adjacent_source_objects_remain_distinct_remotion_image_scenes():
    from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type

    segments = [
        NarrationSegment(id="bill-bag", text="A purchase has an opportunity cost.",
            on_screen="$10 ≠ $10", visual_intent="A $10 bill sits beside a small shopping bag.", start=0, end=4),
        NarrationSegment(id="bill-account", text="Compare spending with investing.",
            on_screen="TODAY: $10 / FUTURE: ?", visual_intent="A $10 bill sits beside an investment account on a phone screen.", start=4, end=8),
    ]
    plan = plan_scenes_deterministic(ScenePlannerInput(
        script=" ".join(segment.text for segment in segments), segments=segments,
        profile={"topic": "Unrelated neutral topic"}, design_system={"visual_style_id": "A"},
    ))
    assert [scene.narration_segment_ids for scene in plan.scenes] == [["bill-bag"], ["bill-account"]]
    assert [scene.layout_id for scene in plan.scenes] == ["icon_explainer", "icon_explainer"]
    assert [scene.meaning_contract["illustration_function"] for scene in plan.scenes] == [
        "bill_and_bag", "bill_and_account_ui",
    ]
    assert [
        layout_to_scene_type(scene.layout_id, visual_focus=scene.visual_focus, index=index,
            total=len(plan.scenes), has_number=bool(scene.number_value), has_chart=bool(scene.chart)).value
        for index, scene in enumerate(plan.scenes)
    ] == ["ImageScene", "ImageScene"]


@pytest.mark.parametrize("style_id", ["A", "D"])
def test_acquisition_uses_literal_subject_and_keeps_the_complete_brief(monkeypatch, style_id):
    from atnos.production import order_comfy_routing as routing
    requests = []

    class FakeFulfiller:
        def __init__(self, settings):
            pass

        def fulfill(self, request):
            requests.append(request)
            return SimpleNamespace(status="awaiting_approval", usable_in_final_film=False,
                                   reason="review required", asset_brain_id=None, output_path="E:/candidate.png")

    monkeypatch.setattr(routing, "OrderComfyAssetFulfiller", FakeFulfiller)
    segment = NarrationSegment(id="bill-bag", text="A purchase has an opportunity cost.",
        on_screen="$10 ≠ $10", visual_intent="A $10 bill sits beside a small shopping bag.", start=0, end=4)
    scene = plan_scenes_deterministic(ScenePlannerInput(script=segment.text, segments=[segment],
        profile={"topic": "Unrelated neutral topic"},
        design_system={"visual_style_id": style_id})).scenes[0].model_dump(mode="json")
    need = scene_assets._needs_for_scene(scene)[0]
    scene_assets._try_acquisition(object(), need, allow_generate=True,
                                 visual_style=freeze_visual_style(style_id), style_tags=["scope"])
    request = requests[0]
    # The diffusion compiler names paper literally: "slip" can mean clothing.
    # Retain the bill's horizontal shape and the two distinct requested props.
    assert request.subject == "one unprinted thin horizontal teal paper card beside one small plain shopping bag"
    assert len(request.text) > 200
    assert "Avoid:" in request.text
    assert request.asset_type == "background"
    assert (request.width, request.height) == (768, 1344)
    assert "raise absorption" not in request.subject
    assert len(request.text) <= 500
    from atnos.production.comfy_channels.models import GenerationRequest
    from atnos.production.comfy_channels.prompts import build_prompt, negative_prompt_with_request
    compiled = GenerationRequest(channel="wealth_blueprint", workflow="WB_OBJECT",
        subject=request.subject, composition=request.text, visual_style_id=style_id,
        visual_style_prompt=freeze_visual_style(style_id)["prompt"], visual_style_tags=["scope"])
    assert "Avoid:" not in build_prompt(compiled)
    negative = negative_prompt_with_request("wealth_blueprint", compiled).casefold()
    assert "extra bags" in negative and "duplicate slips" in negative
    assert "text" in negative


def test_comfy_composition_accepts_complete_500_character_brief():
    request = GenerationRequest(channel="wealth_blueprint", workflow="WB_OBJECT", composition="x" * 500)
    assert len(request.composition) == 500
    with pytest.raises(ValueError):
        GenerationRequest(channel="wealth_blueprint", workflow="WB_OBJECT", composition="x" * 501)


def test_natural_atnos_phrases_merge_one_hold_without_losing_any_caption_word(tmp_path):
    texts = [
        "A pay raise arrives, but your savings stay the same. What happens next?",
        "A new expense might appear, like a subscription or a monthly payment. It can take up the extra money without you noticing.",
        "Before accepting the raise, decide how much you want to save for a future goal. This helps avoid spending it elsewhere.",
        "By setting aside money first, you ensure it's available for your goal instead of being used for new costs.",
        "The key is to choose where your money goes before it's spent. This creates a habit that lasts.",
        "Start by setting aside a small amount each time you receive more income. It builds a habit that works for you.",
    ]
    segments = [NarrationSegment(id=f"s{i+1:02d}", text=text, on_screen=heading,
                                 start=i * 8.0, end=(i + 1) * 8.0)
                for i, (text, heading) in enumerate(zip(texts, (
                    "More income, same savings?", "A recurring cost", "Choose what you keep",
                    "Money stays available", "Choose before spending", "Set aside before spending",
                ), strict=True))]
    plan = WealthBlueprintScenePlanner().plan(ScenePlannerInput(
        script=" ".join(texts), segments=segments,
        profile={"topic": "Why a pay raise can leave savings unchanged", "disable_visual_story": True},
        design_system={"visual_style_id": "A"},
    ))
    assert plan.validation is not None and plan.validation.ok
    assert len(plan.scenes) == 5
    assert [s.meaning_contract["illustration_function"] for s in plan.scenes] == [
        "extra_income", "recurring_expense", "savings_choice", "future_goal", "savings_choice",
    ]
    closing = plan.scenes[-1]
    assert closing.narration_segment_ids == ["s05", "s06"]
    assert (closing.start_sec, closing.end_sec) == (32, 48)
    assert closing.narration_excerpt == " ".join(texts[-2:])
    assert len(closing.animations) <= 2  # one introduction, no second object reset at s06

    images = {}
    for i, scene in enumerate(plan.scenes):
        path = tmp_path / f"merged-{scene.scene_id}.png"
        Image.new("RGB", (64, 64), (i * 30, 40, 60)).save(path)
        images[scene.scene_id] = path
    words = []
    for segment in segments:
        tokens = segment.text.split()
        step = (segment.end - segment.start) / len(tokens)
        words.extend({"text": token, "segment_id": segment.id,
                      "start_sec": segment.start + j * step + 0.02,
                      "end_sec": segment.start + (j + 1) * step - 0.02}
                     for j, token in enumerate(tokens))
    manifest = scene_plan_to_project_manifest(
        job_id="merged-literal-story", topic="Pay raise", script=plan.script,
        plan=plan.model_dump(mode="json"), duration_hint=48,
        scene_images=images, voice={"word_timestamps": words}, require_caption_words=True,
        visual_style=freeze_visual_style("A"),
    )
    assert [word.text for scene in manifest.scenes for word in scene.caption_words] == [word["text"] for word in words]
    assert {word.segment_id for word in manifest.scenes[-1].caption_words} == {"s05", "s06"}
    assert manifest.scenes[-1].caption == " ".join(texts[-2:])
    assert manifest.scenes[-1].narration == " ".join(texts[-2:])
    assert all(scene.semantic_overlay_kind is None for scene in manifest.scenes)
    assert _raise_illustration_for("Never set aside money.") is None


def test_literal_style_trial_keeps_the_complete_script_heading_and_legacy_limit():
    source = _plan("A")
    heading = "A pay raise arrives, but savings stay the same."
    assert 8 < len(heading.split()) <= 14
    segments = [NarrationSegment(
        id=scene.narration_segment_ids[0], text=scene.narration_excerpt,
        on_screen=heading if i == 0 else scene.on_screen_text,
        start=scene.start_sec, end=scene.end_sec,
    ) for i, scene in enumerate(source.scenes)]
    styled = WealthBlueprintScenePlanner().plan(ScenePlannerInput(
        script=source.script, segments=segments,
        profile={"topic": "Why a pay raise can leave savings unchanged", "disable_visual_story": True},
        design_system={"visual_style_id": "A"},
    ))
    assert styled.scenes[0].on_screen_text == heading
    assert styled.scenes[0].meaning_contract["source_on_screen_text"] == heading
    assert styled.validation is not None and styled.validation.ok

    from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
    legacy = plan_scenes_deterministic(ScenePlannerInput(script=source.script, segments=segments))
    assert len(legacy.scenes[0].on_screen_text.split()) <= 8
    assert legacy.scenes[0].on_screen_text != heading

    too_long = " ".join(["income"] * 15)
    contract = {**styled.scenes[0].meaning_contract, "source_on_screen_text": too_long}
    invalid = styled.scenes[0].model_copy(update={"on_screen_text": too_long, "meaning_contract": contract})
    report = validate_scene_plan(styled.model_copy(update={"scenes": [invalid, *styled.scenes[1:]]}))
    assert not report.ok
    assert "onscreen_too_long" in {issue.code for issue in report.issues}
