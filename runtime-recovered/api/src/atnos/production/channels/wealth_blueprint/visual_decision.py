"""Meaning → visual intent → layout family.

The planner asks what the viewer must understand, then which relationship
shows it. Layout ids stay inside the existing registry.
"""

from __future__ import annotations

import re

from atnos.production.channels.wealth_blueprint.scene_plan_models import DominantIntent, LayoutFamily
from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation

_MONEY = re.compile(
    r"(?:\$\s?\d|\b\d+(?:\.\d+)?\s*%|\b(?:percent|dollars?|pounds?)\b)",
    re.I,
)


def decide_visual(
    text: str,
    *,
    concept: str = "generic_finance",
    use_alex: bool = False,
    has_number: bool = False,
    has_chart: bool = False,
    index: int = 0,
    total: int = 1,
) -> dict[str, str]:
    """Return one dominant intent, one layout family, motion, and relation fields."""
    hay = f"{text} {concept}".casefold()
    numberish = has_number or bool(_MONEY.search(text or ""))
    relation = parse_scene_relation(text, concept=concept)
    kind = str(relation.get("kind") or "")

    if use_alex and any(token in hay for token in ("point", "explain", "look", "notice", "alex")):
        intent: DominantIntent = "CHARACTER_EXPLAINER"
        family: LayoutFamily = "CHARACTER_EXPLAINER"
        primary, secondary, motion = "Alex", "the diagram Alex indicates", "gesture"
    elif kind == "reserve_readiness":
        intent, family = "RELATIONSHIP", "MECHANISM"
        primary, secondary, motion = "reserve readiness", str(relation.get('stage')), "reveal_then_hold"
    elif kind == "transfer":
        intent, family = "TRANSFER", "MECHANISM"
        primary = "money moving"
        secondary = str(relation.get("secondary") or relation.get("direction") or "source to destination")
        motion = "movement"
    elif kind == "transfer_forbidden":
        intent, family = "TRANSFER", "MECHANISM"
        primary, secondary, motion = "blocked transfer", "money stays put", "hold"
    elif kind == "raise_split" or concept == "raise_absorption":
        intent, family = "SPLIT", "MECHANISM"
        primary, secondary, motion = "the raise split", "spend versus keep", "division"
    elif kind == "notebook_log":
        intent, family = "NOTE_TRACK", "MECHANISM"
        primary, secondary, motion = "notebook entry", "transfer logged", "write"
    elif kind == "habit_repeat" or concept == "habit":
        intent = "REPEAT"
        family = "MECHANISM"
        primary, secondary, motion = "the repeating step", "growing cushion", "repetition"
    elif concept == "comparison" or has_chart or any(token in hay for token in ("versus", "compared", "instead of")):
        intent, family = "COMPARISON", "DATA_STORY"
        primary, secondary, motion = "the difference", "the two sides", "alignment"
    elif concept == "compound" or "compound" in hay:
        intent, family = "CHANGE_OVER_TIME", "DATA_STORY"
        primary, secondary, motion = "growth stacking", "the base", "scale"
    elif numberish and (index == 0 or "percent" in hay or "$" in text):
        intent, family = "NUMBER_HERO", "DATA_STORY"
        primary, secondary, motion = "the number", "what it measures", "scale"
    elif any(token in hay for token in ("because", "so that", "leaves you", "result")):
        intent, family = "CAUSE_EFFECT", "EDITORIAL_HERO"
        primary, secondary, motion = "the result", "the cause", "hold"
    elif index == 0 or index == max(0, total - 1):
        intent, family = "ACTION", "EDITORIAL_HERO"
        primary, secondary, motion = "the claim", "one supporting line", "hold"
    else:
        intent, family = "VISUAL_METAPHOR", "VISUAL_METAPHOR"
        primary, secondary, motion = "the idea", "one concrete stand-in", "hold"

    return {
        "dominant_intent": intent,
        "layout_family": family,
        "primary": primary,
        "secondary": secondary,
        "motion": motion,
        "relation": relation,
    }


def family_layout(family: LayoutFamily, *, use_alex: bool, has_number: bool, has_chart: bool) -> str:
    if family == "CHARACTER_EXPLAINER" or use_alex:
        return "character_left_text_right"
    if family == "DATA_STORY":
        if has_chart:
            return "comparison"
        if has_number:
            return "big_number"
        return "chart_focus"
    if family == "MECHANISM":
        return "icon_explainer"
    if family == "VISUAL_METAPHOR":
        return "quote_or_fact"
    return "fullscreen_statement"


PROCESS_INTENTS = {
    "TRANSFER",
    "SPLIT",
    "REPEAT",
    "COMPARISON",
    "CHANGE_OVER_TIME",
    "LOOP",
    "NOTE_TRACK",
    "RELATIONSHIP",
    "CONSEQUENCE",
    "CAUSE_EFFECT",
}
"""Intents where an icon alone is not an explanation."""

_CARD_LAYOUTS = {"fullscreen_statement", "quote_or_fact", "three_points"}
_NAVY_CARD_TEMPLATE = {"fullscreen_statement", "quote_or_fact"}


def composition_flags(plan: dict) -> list[dict[str, str]]:
    """Structured checks. Warnings, not a percentage hard-fail."""
    scenes = plan.get("scenes") or []
    flags: list[dict[str, str]] = []
    families: list[str] = []
    card_streak = 0
    for scene in scenes:
        intent = scene.get("visual_intent") if isinstance(scene.get("visual_intent"), dict) else {}
        family = str(intent.get("layout_family") or "")
        families.append(family)
        intent_id = str(intent.get("dominant_intent") or "")
        layout_id = str(scene.get("layout_id") or "")
        concept = str(scene.get("visual_concept") or "")
        relation = intent.get("relation") if isinstance(intent.get("relation"), dict) else {}
        contract = scene.get("meaning_contract") if isinstance(scene.get("meaning_contract"), dict) else {}
        if relation.get("unsupported_gap") or contract.get("unsupported_gap"):
            flags.append(
                {
                    "code": "VISUAL_INTENT_UNSUPPORTED",
                    "scene_id": str(scene.get("scene_id") or ""),
                    "detail": str(relation.get("unsupported_gap") or contract.get("unsupported_gap")),
                }
            )
        if intent_id in PROCESS_INTENTS and layout_id in _CARD_LAYOUTS:
            flags.append(
                {
                    "code": "ICON_IS_NOT_EXPLANATION",
                    "scene_id": str(scene.get("scene_id") or ""),
                    "detail": f"{intent_id} beat uses a statement card instead of a relationship",
                }
            )
        if family == "MECHANISM" and layout_id in _CARD_LAYOUTS:
            flags.append(
                {
                    "code": "VISUAL_INTENT_UNSUPPORTED",
                    "scene_id": str(scene.get("scene_id") or ""),
                    "detail": "MECHANISM intent collapsed to navy/headline card — refusing fake fallback",
                }
            )
            flags.append(
                {
                    "code": "UNINTENTIONAL_DEAD_SPACE",
                    "scene_id": str(scene.get("scene_id") or ""),
                    "detail": "Mechanism intent collapsed to a headline card, leaving the frame unexplained",
                }
            )
        if layout_id in _NAVY_CARD_TEMPLATE and str(scene.get("visual_focus") or "") in {"statement", "icon", ""}:
            card_streak += 1
            if card_streak >= 3:
                flags.append(
                    {
                        "code": "VISUAL_TEMPLATE_OVERUSE",
                        "scene_id": str(scene.get("scene_id") or ""),
                        "detail": "Three+ consecutive navy-card / centered-title scenes",
                    }
                )
        else:
            card_streak = 0
        # Direction / role integrity from meaning contract.
        if contract.get("direction") and "→" in str(contract["direction"]):
            direction = str(contract["direction"])
            roles = contract.get("object_roles") if isinstance(contract.get("object_roles"), dict) else {}
            source = str(roles.get("source") or "")
            dest = str(roles.get("destination") or "")
            if source and dest and direction == f"{dest}→{source}":
                flags.append(
                    {
                        "code": "DIRECTION_REVERSED",
                        "scene_id": str(scene.get("scene_id") or ""),
                        "detail": f"Contract direction {direction} contradicts source/destination roles",
                    }
                )
        _ = concept  # kept for readability of prior checks
    if scenes and families.count("EDITORIAL_HERO") >= max(3, len(scenes) - 1):
        flags.append(
            {
                "code": "VISUAL_TEMPLATE_OVERUSE",
                "scene_id": "",
                "detail": "Most scenes share EDITORIAL_HERO",
            }
        )
    return flags


def storyboard_blocks_render(plan: dict) -> str | None:
    """Stop a render whose every scene is the same statement card or unsupported mechanism."""
    scenes = plan.get("scenes") or []
    layouts = [str(scene.get("layout_id") or "") for scene in scenes]
    if len(scenes) >= 3 and all(layout in _CARD_LAYOUTS for layout in layouts):
        return "storyboard gate: every scene is a statement card. Fix the visual plan before rendering."
    for scene in scenes:
        intent = scene.get("visual_intent") if isinstance(scene.get("visual_intent"), dict) else {}
        relation = intent.get("relation") if isinstance(intent.get("relation"), dict) else {}
        contract = scene.get("meaning_contract") if isinstance(scene.get("meaning_contract"), dict) else {}
        gap = relation.get("unsupported_gap") or contract.get("unsupported_gap")
        if gap and str(intent.get("layout_family") or "") == "MECHANISM":
            return f"storyboard gate: VISUAL_INTENT_UNSUPPORTED — {gap}"
        intent_id = str(intent.get("dominant_intent") or "")
        layout_id = str(scene.get("layout_id") or "")
        if intent_id in PROCESS_INTENTS and layout_id in _CARD_LAYOUTS:
            return (
                f"storyboard gate: VISUAL_INTENT_UNSUPPORTED — {scene.get('scene_id')} "
                f"intent={intent_id} collapsed to {layout_id}"
            )
        if str(intent.get("layout_family") or "") == "MECHANISM" and layout_id in _CARD_LAYOUTS:
            return (
                f"storyboard gate: VISUAL_INTENT_UNSUPPORTED — refusing navy-card fallback for "
                f"{scene.get('scene_id')}"
            )
    return None
