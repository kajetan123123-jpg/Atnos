"""Frozen production constraints must not summon a decorative Alex scene."""

from types import SimpleNamespace

import pytest

from atnos.production.channels.wealth_blueprint import (
    NarrationSegment, ScenePlannerInput, WealthBlueprintScenePlan, validate_scene_plan,
)
from atnos.production.channels.wealth_blueprint.deterministic import (
    _request_requires_functional_alex,
    _raise_illustration_for,
    plan_scenes_deterministic,
)
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style
from atnos.production.wb_image_api import image_selection_plan


# Public editorial text from the real A/B frozen orders, not a live store fixture.
DIRECTION = (
    "Create an English 35-50 second Wealth Blueprint Short explaining why a pay raise can leave savings unchanged. "
    "Four clear thoughts: extra income arrives; a new recurring expense can absorb it; "
    "the alternative is deciding first to set aside part for a future goal; "
    "the consequence is having that part available instead of committed to another recurring cost. "
    "Write natural complete sentences and concrete practical actions. "
    "No amounts, percentages, financial returns, forecasts, invented facts or quantitative chart. "
    "Use an illustrative situation without numerical values. Atnos writes the final script and produces all media."
)
NOTES = (
    "Supervised visual comparison, style {style}. Use the frozen style v2, show real illustrated physical objects "
    "that correspond to each thought, and keep the same narrative progression. "
    "No decorative substitute diagrams, clipart, fake written marks in images or Alex without a useful action. "
    "Full stable plain-phrase English captions without highlighting. "
    "Roger-v3 is a local voice candidate, not channel default approval. Everything on E:. No YouTube publication."
)
TEXTS = (
    "A pay raise brings extra income, but savings may stay unchanged.",
    "A new recurring subscription can absorb that extra income each month.",
    "Before spending more, set aside part of the raise in separate savings.",
    "That reserved money remains available for your future goal.",
)


def _planner_input(direction="", optional_notes="", topic="Why a pay raise can leave savings unchanged", style="A"):
    segments = [NarrationSegment(id=f"s{index + 1:02d}", text=text,
                                start=index * 8.0, end=(index + 1) * 8.0)
                for index, text in enumerate(TEXTS)]
    return ScenePlannerInput(
        script=" ".join(TEXTS), segments=segments,
        profile={"channel": "wealth_blueprint", "topic": topic, "direction": direction,
                 "optional_notes": optional_notes, "disable_visual_story": True},
        design_system={"visual_style_id": style},
    )


@pytest.mark.parametrize("style", list("ABCD"))
def test_frozen_trial_constraints_keep_the_four_real_physical_states_without_alex(style):
    request = {"topic": "Why a pay raise can leave savings unchanged", "direction": DIRECTION,
               "optional_notes": NOTES.format(style=style)}
    context = SimpleNamespace(artifacts={
        "production_order": request,
        "project_profile": {"visual_style": freeze_visual_style(style)},
        "script": {"segments": [{"id": f"s{index + 1:02d}", "text": text,
                                    "on_screen": heading, "claim_ids": []}
                                   for index, (text, heading) in enumerate(zip(TEXTS, (
                                       "More income, same savings?", "A recurring cost", "Choose what you keep",
                                       "Keep your future options"), strict=True))], "claims": []},
    })
    plan = image_selection_plan(context)
    assert validate_scene_plan(WealthBlueprintScenePlan.model_validate(plan)).ok
    assert [scene["meaning_contract"]["illustration_function"] for scene in plan["scenes"]] == [
        "extra_income", "recurring_expense", "savings_choice", "future_goal",
    ]
    assert all(not scene["use_alex"] for scene in plan["scenes"])
    assert all(scene["asset_needs"][0]["type"] == "background" for scene in plan["scenes"])
    assert all(scene["meaning_contract"]["asset_subject"] for scene in plan["scenes"])


@pytest.mark.parametrize("direction,notes", [
    ("Use short on-screen headlines to explain each step.", "Do not force Alex into the film."),
    ("The on-screen text explains the savings mechanism step by step.", "Alex is optional."),
    ("Use an illustrated guide to the budget.", "Avoid Alex as the host."),
    ("Show the physical mechanism.", "Alex explains only if he adds a necessary action."),
    ("The narrator describes the cost.", "Alex is not the on-screen host."),
    ("Do not have Alex explain this mechanism.", "Use plain on-screen captions."),
    ("Use Alex as a guide only when necessary.", "Keep the object sequence dominant."),
    ("Alex leads a host-led story.", "No Alex."),
])
def test_optional_or_negated_character_mentions_do_not_require_a_host(direction, notes):
    assert not _request_requires_functional_alex(_planner_input(direction, notes))


@pytest.mark.parametrize("direction", [
    "Alex is the on-screen host who points through the setup steps.",
    "Alex leads the viewer through a choice before spending.",
    "Alex shows how to set aside part of a raise.",
    "Use Alex as the on-screen guide.",
    "Have Alex explain the choice using a physical envelope.",
    "Make a host-led story with Alex comparing the two decisions.",
    "Create an Alex-led short about setting aside part of a raise.",
    "The on-screen host is Alex.",
])
def test_explicit_functional_host_requests_are_preserved(direction):
    data = _planner_input(direction, "Do not force Alex into every scene. Keep plain on-screen captions.")
    assert _request_requires_functional_alex(data)
    plan = plan_scenes_deterministic(data)
    assert any(scene.use_alex for scene in plan.scenes)


@pytest.mark.parametrize("style", list("ABCD"))
def test_accepted_atnos_assignment_and_later_consequence_keep_all_four_states(style):
    # Actual accepted A revision phrases. No fabricated financial amounts,
    # no rewriting the production script, and no live database/test dependence.
    texts = (
        "An unexpected raise arrives, but savings stay the same. What happens to the extra money?",
        "A new recurring expense might appear, like a subscription or a class. It can use up the extra income without notice.",
        "Before you agree to the expense, choose where the extra money will go. Assign it to a specific purpose or savings container.",
        "By making a choice before spending, you protect some money. It stays separate and can be used for a future need or goal.",
        "Next time you receive extra income, decide where it goes before you spend it. This step helps keep some money available for later.",
    )
    context = SimpleNamespace(artifacts={
        "production_order": {"topic": "Why a pay raise can leave savings unchanged", "direction": DIRECTION,
                             "optional_notes": NOTES.format(style=style)},
        "project_profile": {"visual_style": freeze_visual_style(style)},
        "script": {"segments": [{"id": f"s{index + 1:02d}", "text": text,
                                    "on_screen": "", "claim_ids": []}
                                   for index, text in enumerate(texts)], "claims": []},
    })
    plan = image_selection_plan(context)
    assert [scene["meaning_contract"]["illustration_function"] for scene in plan["scenes"]] == [
        "extra_income", "recurring_expense", "savings_choice", "future_goal",
    ]
    assert all(not scene["use_alex"] for scene in plan["scenes"])
    assert all(scene["asset_needs"][0]["type"] == "background" for scene in plan["scenes"])
    assert plan["scenes"][-1]["narration_segment_ids"] == ["s04", "s05"]
    assert plan["scenes"][-1]["narration_excerpt"] == " ".join(texts[-2:])
    assert validate_scene_plan(WealthBlueprintScenePlan.model_validate(plan)).ok


@pytest.mark.parametrize("text", [
    "Don't assign the extra money to a savings container.",
    "Don’t assign the extra money to a savings container.",
    "You cannot assign that extra income to a savings goal.",
    "This money cannot be assigned to a savings container.",
    "Never allocate this income to a future goal.",
])
def test_negated_assignment_never_turns_into_a_positive_savings_picture(text):
    assert _raise_illustration_for(text) is None


def test_assignment_to_recurring_cost_remains_a_cost_instead_of_savings():
    result = _raise_illustration_for("Assign the extra income to a new monthly subscription payment.")
    assert result["function"] == "recurring_expense"
    assert _raise_illustration_for("Assign a license to a computer.") is None
