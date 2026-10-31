"""Job 3's recorded annual-bill script: calendar and listing are distinct beats.

The source sentences and directing intents below are frozen from
prod-b20c73ef1dd3/script.json. This tests a deterministic contract, not media.
"""

import pytest

from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan


JOB3_TOPIC = "An annual bill is a surprise only if you forgot it"
JOB3_DIRECTION = (
    "English WB Short distinguishing a predictable annual expense from an unexpected emergency. "
    "Hook on a known due date mistaken for a surprise. Ask viewers to check their calendar and "
    "existing commitments, then distinguish planned-goal money from an emergency reserve and "
    "check the relevant access conditions. Use native reserve_readiness expense sheet and "
    "separate/review stages, no house, car or invented app. No prices, mandatory percentages or "
    "guarantees. CTA: Follow Wealth Blueprint. Build wealth on purpose."
)
JOB3_SEGMENTS = (
    ("s01", "Your calendar shows the due date, yet you call it a surprise. That is not an "
     "emergency; that is just poor planning for known obligations.",
     "Check your calendar first", "identify (actual need and reserve)"),
    ("s02", "Open the native expense sheet. List every annual bill you owe before it arrives "
     "so you can plan for them now.",
     "List known bills here", "identify (actual need and reserve)"),
    ("s03", "Money set aside for these scheduled costs belongs to the future goal notebook, "
     "not your emergency reserve.",
     "Separate planned from saved", "separate (reserve versus future goal)"),
    ("s04", "Ask yourself if the account holds funds for immediate access or only after a "
     "waiting period. Verify your actual conditions.",
     "Verify access rules now", "access (reserve and questions about access, timing and conditions)"),
    ("s05", "If the money is strictly for a future date, it cannot be your safety net when an "
     "unexpected event occurs.",
     "Review reserve vs goal role", "review (review both roles)"),
    ("s06", "Follow Wealth Blueprint. Build wealth on purpose.",
     "Build wealth on purpose", "Wealth Blueprint logo only, no host or props"),
)


@pytest.mark.parametrize(
    ("index", "focus", "action", "visible_result"),
    [
        (0, "calendar", "show_known_due_date", "due date is highlighted on the calendar"),
        (1, "list", "record_known_bills", "recorded on a planning list"),
    ],
)
def test_spoken_planned_bill_action_selects_distinct_identify_focus(index, focus, action, visible_result):
    _, narration, _, direction = JOB3_SEGMENTS[index]
    relation = parse_scene_relation(
        narration, visual_context=direction, reserve_context=True, planned_bill_context=True,
    )
    assert relation["kind"] == "reserve_readiness"
    assert relation["stage"] == "identify"
    assert relation["bill_focus"] == focus
    assert relation["action"] == action
    assert visible_result in relation["final_state"]
    assert relation["final_state"] == relation["viewer_should_understand"]
    assert {item["role"]: item["label"] for item in relation["objects"]} == {
        "expense": "ANNUAL BILL", "reserve": "READY RESERVE",
    }


def test_recorded_job3_keeps_both_actions_distinct_through_plan_and_manifest():
    cursor = 0.0
    segments = []
    for segment_id, narration, on_screen, direction in JOB3_SEGMENTS:
        duration = max(3.0, len(narration.split()) / 2.5)
        segments.append(NarrationSegment(
            id=segment_id, text=narration, on_screen=on_screen, visual_intent=direction,
            start=cursor, end=cursor + duration,
        ))
        cursor += duration
    script = " ".join(item.text for item in segments)
    plan = plan_scenes_deterministic(ScenePlannerInput(
        script=script, segments=segments,
        profile={"channel": "wealth_blueprint", "topic": JOB3_TOPIC,
                 "direction": JOB3_DIRECTION, "optional_notes": "", "disable_visual_story": True},
        claims=[], segment_claim_map={item.id: [] for item in segments},
    ))
    validation = validate_scene_plan(plan)
    assert validation.ok, [item.model_dump(mode="json") for item in validation.issues]
    assert [scene.scene_id for scene in plan.scenes] == [item[0] for item in JOB3_SEGMENTS]
    assert [scene.visual_intent.relation.get("bill_focus") for scene in plan.scenes[:2]] == [
        "calendar", "list",
    ]
    assert [scene.meaning_contract["action"] for scene in plan.scenes[:2]] == [
        "show_known_due_date", "record_known_bills",
    ]
    assert plan.scenes[0].visual_intent.visible_change != plan.scenes[1].visual_intent.visible_change

    manifest = scene_plan_to_project_manifest(
        job_id="job3-planned-bill-regression", topic=JOB3_TOPIC, script=script,
        plan=plan.model_dump(mode="json"), duration_hint=cursor, verified_claims=[],
    ).model_dump(mode="json")
    scenes = manifest["scenes"]
    assert [scene["visual_relation"]["bill_focus"] for scene in scenes[:2]] == [
        "calendar", "list",
    ]
    assert [scene["visual_relation"]["action"] for scene in scenes[:2]] == [
        "show_known_due_date", "record_known_bills",
    ]
    assert all(scene["semantic_overlay_kind"] == "reserve_readiness" for scene in scenes[:2])
    assert scenes[2]["visual_relation"]["objects"][-1]["label"] == "PLANNED BILL FUND"


def test_focus_is_absent_without_spoken_action_and_legacy_relations_stay_bounded():
    identify = "identify (actual need and reserve)"
    generic = parse_scene_relation(
        "Check a known annual bill against your reserve.", visual_context=identify,
        reserve_context=True, planned_bill_context=True,
    )
    assert generic["stage"] == "identify" and "bill_focus" not in generic
    assert generic["action"] == "identify_need"

    old = parse_scene_relation(
        JOB3_SEGMENTS[0][1], visual_context=identify, reserve_context=True,
    )
    assert old["action"] == "identify_need" and "bill_focus" not in old
    assert old["objects"][0]["label"] == "UNEXPECTED NEED"
    repair = parse_scene_relation(
        "Name the car repair and check your reserve.", visual_context=identify,
        reserve_context=True, planned_bill_context=True,
    )
    assert repair["objects"][0]["label"] == "CAR REPAIR"
    assert "bill_focus" not in repair and "annual bill" not in repair["final_state"].casefold()
    assert parse_scene_relation(
        "Transfer from checking to savings for the annual bill.",
        visual_context=identify, reserve_context=True, planned_bill_context=True,
    )["kind"] == "transfer"
    assert parse_scene_relation(
        "Save $500 for the annual bill.", visual_context="",
        reserve_context=True, planned_bill_context=True,
    )["kind"] != "reserve_readiness"
