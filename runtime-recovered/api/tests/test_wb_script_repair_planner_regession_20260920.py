"""Regression: WB script_repair word-budget compression + planner layout continuity."""

from __future__ import annotations

import pytest

from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.scene_plan_models import (
    NarrationSegment,
    ScenePlannerInput,
)
from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan
from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
from atnos.production.steps.order_script import (
    EditorialScript,
    ScriptSegment,
    compress_script_to_word_budget,
    repair_word_budget,
    shorten_spoken_text,
    script_issues,
    try_deterministic_repair,
)


def _segment(sid: str, text: str, *, kind: str = "practical") -> ScriptSegment:
    return ScriptSegment(
        id=sid,
        type=kind,  # type: ignore[arg-type]
        text=text,
        on_screen=" ".join(text.split()[:4])[:70],
        visual_intent="Show the practical money action from this beat clearly.",
        claim_ids=[],
        fact_ids=[],
    )


def test_shorten_does_not_leave_dangling_clause():
    text = (
        "Make a habit of reviewing your spending plan after any income change. "
        "This helps you stay in control of where the extra money goes each month."
    )
    shortened = shorten_spoken_text(text, 12)
    assert not shortened.rstrip(".!?").casefold().endswith(("where", "of", "the", "your", "when", "income"))
    assert shortened.endswith((".", "!", "?"))


def test_shorten_keeps_hypothetical_and_numbers():
    text = (
        "In fact you really need to remember that this hypothetical example assumes "
        "one hundred dollars each month so the transfer can actually compound."
    )
    shortened = shorten_spoken_text(text, 16)
    words = shortened.casefold()
    assert "hypothetical" in words or "example" in words or "assumes" in words
    assert "hundred" in words or "dollars" in words or "transfer" in words
    # Updated user contract: an oversized complete sentence goes to model repair;
    # chopping a prefix just to hit eighteen words is no longer an accepted repair.
    assert shortened.endswith('can actually compound.')


def test_deterministic_compression_leaves_overlong_complete_thoughts_for_model_repair():
    # Reproduces the terminal failure shape: ~105 words vs required 55-90 under a tight budget.
    long_bits = [
        "Your paycheck can feel bigger this year while your real wealth stays flat because everyday expenses quietly rise with income.",
        "Turn on automatic weekly transfers so money leaves checking on Friday and lands in savings without relying on willpower.",
        "This hypothetical example assumes one hundred dollars each month moved before you spend whatever remains afterward.",
        "Keep part of every raise so lifestyle creep does not erase the gain you just earned on paper.",
        "Name the savings goal clearly and review the automatic transfer once after the first payday lands.",
        "Open the account today and schedule that first automatic transfer before the week ends for good.",
    ]
    script = EditorialScript(
        title="Keep more of your raise",
        description="Practical raise capture habit",
        segments=[
            _segment("s01", long_bits[0], kind="hook"),
            _segment("s02", long_bits[1]),
            _segment("s03", long_bits[2]),
            _segment("s04", long_bits[3]),
            _segment("s05", long_bits[4]),
            _segment("s06", long_bits[5], kind="cta"),
        ],
        claims=[],
    )
    total = sum(len(segment.text.split()) for segment in script.segments)
    assert total >= 100
    budget = [55, 90]
    allowed = {segment.id for segment in script.segments}
    compressed, trace = compress_script_to_word_budget(script, allowed, budget)
    words = sum(len(segment.text.split()) for segment in compressed.segments)
    assert words > budget[1], (words, trace)
    assert [s.text for s in compressed.segments] == long_bits
    joined = " ".join(segment.text for segment in compressed.segments).casefold()
    assert "transfer" in joined
    assert "hypothetical" in joined or "example" in joined or "assumes" in joined
    assert "hundred" in joined or "dollars" in joined


def test_overlong_single_sentences_require_model_rewrite():
    long_bits = [
        "Opening a high-yield savings account is the first careful step toward securing your future wealth with intention.",
        "Choose a separate savings account so you avoid spending money that was set aside for important goals.",
        "Turn on automatic weekly transfers so saving happens without relying on willpower every single Friday.",
        "Name the goal for this account so you stay focused when spending pressure returns later this month.",
        "Setting up these practical steps usually takes just a few quiet minutes in your banking app today.",
        "Open your account today and schedule your first automatic transfer before the weekend starts for real.",
    ]
    script = EditorialScript(
        title="Open a high-yield account",
        description="Step by step savings setup",
        segments=[
            _segment("s01", long_bits[0], kind="hook"),
            _segment("s02", long_bits[1]),
            _segment("s03", long_bits[2]),
            _segment("s04", long_bits[3]),
            _segment("s05", long_bits[4]),
            _segment("s06", long_bits[5], kind="cta"),
        ],
        claims=[],
    )
    profile = {
        "production": {
            "language": "en",
            "word_budget": [55, 90],
            "minimum_practical_share": 0.34,
            "narrative_rules": ["Be practical"],
        }
    }

    class _Ctx:
        artifacts = {"project_profile": profile, "production_order": {"topic": "savings"}}
        artifact_dir = __import__("pathlib").Path("E:/Atnos/Temp/wb-repair-test")

    _Ctx.artifact_dir.mkdir(parents=True, exist_ok=True)
    issues = script_issues(script, profile, trusted_facts=[], request={"topic": "savings"})
    assert any(item["code"] == "word_budget" for item in issues)
    fixed, trace = try_deterministic_repair(script, issues, _Ctx())  # type: ignore[arg-type]
    assert fixed is None, trace
    assert [s.text for s in script.segments] == long_bits


def test_planner_alex_mechanism_case_validates_without_layout_repeat():
    """Reproduction from planner-reproduction.json — must plan + validate cleanly."""
    segments = [
        NarrationSegment(
            id="s01",
            text="Opening a high-yield savings account is the first step toward securing your future.",
            start=0.0,
            end=4.0,
        ),
        NarrationSegment(
            id="s02",
            text="Choose a separate savings account. This helps you avoid spending money set aside.",
            start=4.0,
            end=9.0,
        ),
        NarrationSegment(
            id="s03",
            text="Turn on automatic weekly transfers so saving happens without willpower.",
            start=9.0,
            end=14.0,
        ),
        NarrationSegment(
            id="s04",
            text="Name the goal for this account to stay focused.",
            start=14.0,
            end=18.0,
        ),
        NarrationSegment(
            id="s05",
            text="Setting up these steps takes just a few minutes.",
            start=18.0,
            end=22.0,
        ),
        NarrationSegment(
            id="s06",
            text="Open your account today and schedule your first transfer.",
            start=22.0,
            end=26.0,
        ),
    ]
    planner_input = ScenePlannerInput(
        script=" ".join(seg.text for seg in segments),
        segments=segments,
        profile={
            "channel": "wealth_blueprint",
            "topic": "Alex shows how to open a high-yield savings account step by step",
            "direction": "Alex is the on-screen host who points through the setup steps.",
        },
        mode="deterministic",
    )
    plan = plan_scenes_deterministic(planner_input)
    validation = validate_scene_plan(plan)
    assert validation.ok, [issue.model_dump() for issue in validation.issues]
    layouts = [scene.layout_id for scene in plan.scenes]
    assert "icon_explainer" in layouts
    # No run of 3+ identical layouts
    streak = 1
    for left, right in zip(layouts, layouts[1:]):
        if left == right:
            streak += 1
            assert streak <= 2, layouts
        else:
            streak = 1
    public = WealthBlueprintScenePlanner().plan(planner_input)
    assert any(scene.use_alex for scene in public.scenes)


def _raise_absorption_input():
    segments = [
        NarrationSegment(id="s01", text="Why does a pay raise leave you with no extra money to save?", start=0.0, end=4.0),
        NarrationSegment(
            id="s02",
            text="Watch as income enters a budget. New recurring expenses absorb the raise before savings.",
            start=4.0,
            end=10.0,
        ),
        NarrationSegment(
            id="s03",
            text="Choose to direct part of the increase toward savings first.",
            start=10.0,
            end=15.0,
        ),
        NarrationSegment(
            id="s04",
            text="Review your spending plan after any income change so you stay in control.",
            start=15.0,
            end=21.0,
        ),
        NarrationSegment(
            id="s05",
            text="This is about prioritizing savings when your income grows.",
            start=21.0,
            end=27.0,
        ),
        NarrationSegment(
            id="s06",
            text="Before your next raise arrives, decide where the extra money should go.",
            start=27.0,
            end=33.0,
        ),
    ]
    return ScenePlannerInput(
        script=" ".join(seg.text for seg in segments),
        segments=segments,
        profile={
            "channel": "wealth_blueprint",
            "topic": "Why higher income does not automatically build wealth",
            "direction": "Show income entering a spending plan; new expenses absorb the raise. Alex is optional.",
        },
        mode="deterministic",
    )


def test_raise_absorption_concept_and_no_decorative_alex_on_mechanism_brief():
    from atnos.production.channels.wealth_blueprint.visual_concepts import infer_visual_concept

    assert (
        infer_visual_concept(
            "Why does a pay raise sometimes leave you with no extra money to save?",
            "New recurring expenses absorb the raise before savings.",
        )
        == "raise_absorption"
    )
    planner_input = _raise_absorption_input()
    plan = plan_scenes_deterministic(planner_input)
    assert not any(scene.use_alex for scene in plan.scenes), [s.layout_id for s in plan.scenes]
    assert any(scene.visual_concept == "raise_absorption" for scene in plan.scenes)
    assert validate_scene_plan(plan).ok
    stories = [scene.visual_story for scene in plan.scenes if scene.visual_story]
    assert [story["state"] for story in stories] == ["raise", "costs", "choice"]
    assert [story["previous_state"] for story in stories] == [None, "raise", "costs"]
    assert "recurring commitment" in stories[1]["action"]
    assert "alternative route to savings" in stories[2]["action"]
    assert all(scene.layout_id == "icon_explainer" for scene in plan.scenes if scene.visual_story)
    choice = next(scene for scene in plan.scenes if (scene.visual_story or {}).get("state") == "choice")
    assert choice.narration_segment_ids == ["s03", "s04", "s05"]
    assert (choice.start_sec, choice.end_sec) == (10.0, 27.0)
    assert choice.meaning_contract["visual_story_state"] == "choice"
    assert choice.visual_intent.final_state == stories[2]["final_state"]
    assert [sid for scene in plan.scenes for sid in scene.narration_segment_ids] == [seg.id for seg in planner_input.segments]
    assert " ".join(scene.narration_excerpt for scene in plan.scenes) == planner_input.script
    assert plan.scenes[-1].visual_story is None
    assert plan.scenes[-1].narration_excerpt == planner_input.segments[-1].text


@pytest.mark.parametrize("scope", ["topic_only", "disabled", "explicit_still", "style_variant", "unsupported_beat", "functional_host", "quantified", "negated_action", "negated_cause"])
def test_raise_story_does_not_override_unrelated_or_explicit_handoffs(scope):
    source = _raise_absorption_input()
    if scope == "topic_only":
        source.profile["direction"] = "Explain saving, Alex is optional."
    elif scope == "disabled":
        source.profile["disable_visual_story"] = True
    elif scope == "explicit_still":
        source.segments[0].visual_intent = "A still illustration of a pay envelope."
    elif scope == "style_variant":
        source.design_system["visual_style_id"] = "D"
    elif scope == "functional_host":
        source.profile["direction"] += " Alex is the on-screen host who explains the mechanism."
    elif scope == "quantified":
        source.segments[2].text = "Direct $100 of the raise toward savings first."
        source.script = " ".join(seg.text for seg in source.segments)
    elif scope == "negated_action":
        source.segments[2].text = "Never direct part of the increase toward savings first."
        source.script = " ".join(seg.text for seg in source.segments)
    elif scope == "negated_cause":
        source.segments[1].text = "New recurring expenses never absorb the raise in this example."
        source.script = " ".join(seg.text for seg in source.segments)
    else:
        source.segments[3].text = "Money matters to everyone."
        source.script = " ".join(seg.text for seg in source.segments)
    plan = plan_scenes_deterministic(source)
    assert not any(scene.visual_story for scene in plan.scenes)
    if scope == "unsupported_beat":
        assert not validate_scene_plan(plan).ok  # An unsupported beat cannot be blessed by a fake state.


def test_layered_raise_story_cannot_render_without_alignment_or_approved_pack(tmp_path):
    import hashlib
    import json
    from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest

    source = _raise_absorption_input()
    plan = plan_scenes_deterministic(source)
    request = dict(job_id="raise-regression", topic=source.profile["topic"], script=source.script,
                   plan=plan.model_dump(mode="json"), duration_hint=plan.duration_sec,
                   illustration_pack_path=tmp_path / "missing-pack.json")
    with pytest.raises(ValueError, match="requires final spoken word timings"):
        scene_plan_to_project_manifest(**request)
    words = []
    for segment in source.segments:
        tokens = segment.text.split()
        step = (segment.end - segment.start) / len(tokens)
        words.extend({"text": word, "segment_id": segment.id,
                      "start_sec": segment.start + index * step,
                      "end_sec": segment.start + (index + 1) * step}
                     for index, word in enumerate(tokens))
    # Synthetic alignment is test data, never evidence of a produced voice/film.
    with pytest.raises(ValueError, match="WAITING_FOR_ASSETS"):
        scene_plan_to_project_manifest(**request, voice={"word_timestamps": words})
    # This is deliberately not an image; user approval must be checked before
    # any unapproved background can be opened/imported as a production layer.
    background = tmp_path / "background-fixture-not-an-image.bin"
    background.write_bytes(b"unapproved test data, never a production asset")
    pack = tmp_path / "unapproved-pack.json"
    pack.write_text(json.dumps({"channel": "wealth_blueprint", "version": "isolated-test",
                               "layers": {"background": {
                                   "path": str(background),
                                   "sha256": hashlib.sha256(background.read_bytes()).hexdigest(),
                                   "classification": "production_candidate", "visual_review": "technical_pass",
                                   "approved": True, "user_approval": {},
                               }}}), encoding="utf-8")
    request["illustration_pack_path"] = pack
    with pytest.raises(ValueError, match="WAITING_FOR_USER_ASSET_REVIEW"):
        scene_plan_to_project_manifest(**request, voice={"word_timestamps": words})


def test_optional_alex_wording_does_not_force_host_budget():
    from atnos.production.channels.wealth_blueprint.deterministic import _request_requires_functional_alex

    planner_input = ScenePlannerInput(
        script="Why does a pay raise leave you with no extra money to save?",
        segments=[
            NarrationSegment(id="s01", text="Why does a pay raise leave you with no extra money to save?", start=0.0, end=4.0),
        ],
        profile={
            "topic": "Why higher income does not automatically build wealth",
            "direction": "Show the raise absorption mechanism.",
            "optional_notes": "Alex is optional and should only appear if he explains a specific transition.",
        },
        mode="deterministic",
    )
    assert _request_requires_functional_alex(planner_input) is False
