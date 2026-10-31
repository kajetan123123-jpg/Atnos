"""Wealth Blueprint shot language, density, and anti-slop checks.

Does not add Remotion scene types. Annotates an existing ScenePlan so the
renderer keeps receiving layout_id, while the planner records editorial intent.
"""

from __future__ import annotations

import re
from typing import Literal

from atnos.production.channels.wealth_blueprint.scene_plan_models import (
    PlannedScene,
    WealthBlueprintScenePlan,
)

ShotClass = Literal[
    "HERO",
    "EXPLAINER",
    "DATA",
    "COMPARISON",
    "TIMELINE",
    "PERSON",
    "OBJECT",
    "ENVIRONMENT",
    "UI",
    "QUOTE",
    "ACTION_STEP",
    "TRANSITION",
]
Density = Literal["low", "medium", "high"]

_SLOP = (
    "lamborghini",
    "mansion",
    "private jet",
    "hologram",
    "floating coins",
    "get rich quick",
    "millionaire mindset",
    "crypto bro",
    "lambo",
)

_ACTION = (
    "set aside",
    "automate",
    "transfer",
    "review",
    "pause",
    "choose",
    "write down",
    "decide",
    "open a",
    "schedule",
    "pay yourself",
    "split",
    "label",
    "close the",
)


def classify_shot(scene: PlannedScene, *, index: int, total: int) -> ShotClass:
    text = f"{scene.narration_excerpt} {scene.on_screen_text}".casefold()
    if index == 0:
        return "HERO"
    if index == total - 1 or any(token in text for token in _ACTION):
        if index == total - 1:
            return "ACTION_STEP"
    if scene.visual_focus in {"chart", "comparison", "number"} or scene.chart is not None:
        return "COMPARISON" if "compar" in text or scene.visual_focus == "comparison" else "DATA"
    if scene.use_alex:
        return "PERSON"
    if scene.visual_focus == "icon" or scene.visual_concept not in {None, "generic_finance"}:
        return "EXPLAINER"
    if any(token in text for token in ("month", "week", "year", "timeline", "after six")):
        return "TIMELINE"
    return "QUOTE"


def density_for(shot: ShotClass, *, index: int) -> Density:
    """Vary density so the cut is not a constant medium slideshow."""
    if shot in {"HERO", "QUOTE"}:
        return "low"
    if shot in {"DATA", "COMPARISON", "EXPLAINER"}:
        return "high" if index % 2 == 0 else "medium"
    if shot == "ACTION_STEP":
        return "medium"
    return "low" if index % 3 == 0 else "medium"


def slop_hits(plan: WealthBlueprintScenePlan) -> list[str]:
    hay = " ".join(
        f"{scene.narration_excerpt} {scene.on_screen_text} {scene.rationale}" for scene in plan.scenes
    ).casefold()
    return [token for token in _SLOP if token in hay]


def has_practical_action(plan: WealthBlueprintScenePlan) -> bool:
    hay = " ".join(scene.narration_excerpt for scene in plan.scenes).casefold()
    return any(token in hay for token in _ACTION)


def is_literal_illustration_hold(scene: PlannedScene) -> bool:
    """An explicit physical still, not a relabeled missing animation capability."""
    intent = scene.visual_intent
    contract = scene.meaning_contract or {}
    relation = intent.relation if intent and isinstance(intent.relation, dict) else {}
    return bool(
        intent and intent.scene_mode == "illustration" and intent.action == "hold"
        and intent.visible_objects and all(str(obj).strip() for obj in intent.visible_objects)
        and intent.final_state.strip()
        and relation.get("kind") == "unsupported" and not relation.get("unsupported_gap")
        and contract.get("motion_capability") == "reveal_then_hold_still"
        and contract.get("asset_subject") and contract.get("object_roles")
        and contract.get("illustration_function") in {
            "extra_income", "recurring_expense", "savings_choice", "future_goal",
            "bill_and_bag", "bill_and_account_ui", "coffee_reassurance", "balance_scale",
        }
    )


def different_literal_illustration_states(left: PlannedScene, right: PlannedScene) -> bool:
    """Continuity may keep a layout only when the physical proposition changes."""
    if not (is_literal_illustration_hold(left) and is_literal_illustration_hold(right)):
        return False
    assert left.visual_intent is not None and right.visual_intent is not None
    left_objects = {obj.casefold().strip() for obj in left.visual_intent.visible_objects}
    right_objects = {obj.casefold().strip() for obj in right.visual_intent.visible_objects}
    return bool(
        left_objects != right_objects
        and left.visual_intent.final_state.casefold().strip() != right.visual_intent.final_state.casefold().strip()
        and (left.meaning_contract or {}).get("illustration_function")
        != (right.meaning_contract or {}).get("illustration_function")
    )


def apply_shot_language(plan: WealthBlueprintScenePlan) -> WealthBlueprintScenePlan:
    """Stamp shot class + density and soften identical motion on low-density beats."""
    total = len(plan.scenes)
    updated: list[PlannedScene] = []
    diagram_used = False
    for index, scene in enumerate(plan.scenes):
        shot = classify_shot(scene, index=index, total=total)
        density = density_for(shot, index=index)
        layout_id = scene.layout_id
        visual_focus = scene.visual_focus
        # Repeat the same mechanism only once. A different intent keeps its diagram.
        family = scene.visual_intent.layout_family if scene.visual_intent else ""
        concept = scene.visual_concept or ""
        semantic_action = bool(scene.visual_story or is_literal_illustration_hold(scene)
                               or (scene.visual_intent and scene.visual_intent.scene_mode in {'diagram', 'comparison'}))
        if layout_id == "icon_explainer" and not semantic_action:
            same_mechanism = diagram_used and concept == getattr(updated[-1], "visual_concept", None)
            if same_mechanism and family == "MECHANISM":
                layout_id = "split_screen" if updated[-1].layout_id != "split_screen" else "icon_explainer"
            elif diagram_used and family != "MECHANISM":
                layout_id = "fullscreen_statement" if updated[-1].layout_id != "fullscreen_statement" else "quote_or_fact"
                visual_focus = "statement"
                shot = "ACTION_STEP" if index == total - 1 else "QUOTE"
                density = density_for(shot, index=index)
            else:
                diagram_used = True
        animations = list(scene.animations)
        if density == "low" and animations:
            # One purposeful fade; drop stacked slides that read as default AI motion.
            animations = [animations[0].model_copy(update={"preset_id": "TEXT_FADE", "duration_sec": 0.7})]
        elif density == "high" and animations:
            animations = [
                cue.model_copy(update={"duration_sec": min(0.55, cue.duration_sec)}) for cue in animations[:2]
            ]
        rationale = scene.rationale
        stamp = f"shot={shot}; density={density}"
        if stamp not in rationale:
            rationale = f"{rationale}; {stamp}".strip("; ")
        updated.append(
            scene.model_copy(
                update={
                    "shot_class": shot,
                    "visual_density": density,
                    "layout_id": layout_id,
                    "visual_focus": visual_focus,
                    "animations": animations,
                    "rationale": rationale[:400],
                }
            )
        )
    for index in range(1, len(updated)):
        if updated[index].layout_id != updated[index - 1].layout_id:
            continue
        intent = updated[index].visual_intent
        if updated[index].visual_story or (intent and intent.scene_mode in {'diagram', 'comparison'}):
            # Changing composition to meet a variety quota can delete the action.
            # Keep the planned relationship; validation rejects duplicate states.
            continue
        if different_literal_illustration_states(updated[index - 1], updated[index]):
            # The image and physical meaning changed; don't delete it to satisfy
            # a composition quota. Duplicate still states keep ordinary rules.
            continue
        if updated[index].layout_id == "icon_explainer":
            run = 1
            cursor = index - 1
            while cursor >= 0 and updated[cursor].layout_id == "icon_explainer":
                run += 1
                cursor -= 1
            if run > 2:
                updated[index] = updated[index].model_copy(
                    update={"layout_id": "fullscreen_statement", "visual_focus": "statement"}
                )
            continue
        swap = "quote_or_fact" if updated[index].layout_id == "fullscreen_statement" else "fullscreen_statement"
        updated[index] = updated[index].model_copy(update={"layout_id": swap, "visual_focus": "statement"})
    note = "Shot language applied: HERO/EXPLAINER/DATA/ACTION_STEP with low-medium-high density"
    notes = list(plan.notes)
    if note not in notes:
        notes.append(note)
    return plan.model_copy(update={"scenes": updated, "notes": notes[:32]})


def headline_is_dangling(text: str) -> bool:
    token = re.sub(r"[^A-Za-z]+$", "", (text or "").strip().split()[-1] if text.strip() else "")
    return token.casefold() in {
        "to",
        "of",
        "for",
        "and",
        "or",
        "when",
        "where",
        "your",
        "with",
        "before",
        "after",
        "from",
        "into",
        "the",
        "a",
    }
