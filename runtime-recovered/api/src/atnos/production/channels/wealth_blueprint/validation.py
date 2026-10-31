from __future__ import annotations

from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_META
from atnos.production.channels.wealth_blueprint.rules import (
    CHANNEL,
    MAX_ALEX_SCENE_RATIO,
    MAX_ANIMATIONS_PER_SCENE,
    MAX_CONSECUTIVE_SAME_LAYOUT,
    MAX_ELEMENTS_PER_SCENE,
    MAX_ON_SCREEN_RATIO,
    MAX_ON_SCREEN_WORDS,
    MAX_SFX_PER_SCENE,
    MIN_SCENE_DURATION_SEC,
)
from atnos.production.channels.wealth_blueprint.scene_plan_models import (
    ScenePlanValidation,
    ValidationIssue,
    WealthBlueprintScenePlan,
)


def _count_elements(scene) -> int:
    count = 1  # on-screen text always present
    if scene.use_alex:
        count += 1
    if scene.chart is not None:
        count += 1
    if scene.icon_tags:
        count += 1
    if scene.number_value and scene.visual_focus == "number":
        count += 0  # number is the text focus, not extra
    if scene.simplify:
        count = min(count, 2)
    return count


def validate_scene_plan(plan: WealthBlueprintScenePlan) -> ScenePlanValidation:
    """Deterministic validation of Wealth Blueprint planning rules. Never renders."""
    issues: list[ValidationIssue] = []

    if plan.channel != CHANNEL:
        issues.append(
            ValidationIssue(code="channel", severity="error", message=f"channel must be {CHANNEL}")
        )

    if plan.schema_version != "wealth_blueprint_scene_plan_1":
        issues.append(
            ValidationIssue(code="schema", severity="error", message="Unsupported scene plan schema_version")
        )

    # Contiguous, monotonic timeline
    cursor = 0.0
    for scene in plan.scenes:
        duration = scene.end_sec - scene.start_sec
        if duration + 1e-6 < MIN_SCENE_DURATION_SEC:
            issues.append(
                ValidationIssue(
                    code="scene_too_short",
                    severity="error",
                    scene_id=scene.scene_id,
                    message=f"Scene shorter than {MIN_SCENE_DURATION_SEC}s (rule 1)",
                )
            )
        if scene.start_sec + 1e-6 < cursor:
            issues.append(
                ValidationIssue(
                    code="overlap",
                    severity="error",
                    scene_id=scene.scene_id,
                    message="Scenes overlap or go backwards",
                )
            )
        cursor = scene.end_sec

        # Elements
        elements = _count_elements(scene)
        layout_cap = int(LAYOUT_META.get(scene.layout_id, {}).get("max_elements", MAX_ELEMENTS_PER_SCENE))
        if elements > min(MAX_ELEMENTS_PER_SCENE, layout_cap):
            issues.append(
                ValidationIssue(
                    code="too_many_elements",
                    severity="error",
                    scene_id=scene.scene_id,
                    message=f"Too many simultaneous elements ({elements}) — rule 2",
                )
            )

        # Dominant focus
        if not scene.visual_focus or not scene.on_screen_text.strip():
            issues.append(
                ValidationIssue(
                    code="no_dominant",
                    severity="error",
                    scene_id=scene.scene_id,
                    message="Missing dominant visual focus/text — rule 3",
                )
            )

        # On-screen vs VO
        on_words = [w for w in scene.on_screen_text.split() if w]
        narr_words = [w for w in scene.narration_excerpt.split() if w]
        from atnos.production.channels.wealth_blueprint.shot_language import is_literal_illustration_hold

        preserved_literal_heading = bool(
            is_literal_illustration_hold(scene)
            and (scene.meaning_contract or {}).get("on_screen_origin") == "script"
            and (scene.meaning_contract or {}).get("source_on_screen_text") == scene.on_screen_text
        )
        heading_limit = 14 if preserved_literal_heading else MAX_ON_SCREEN_WORDS
        if len(on_words) > heading_limit:
            issues.append(
                ValidationIssue(
                    code="onscreen_too_long",
                    severity="error",
                    scene_id=scene.scene_id,
                    message=f"On-screen text exceeds {heading_limit} words — rule 4",
                )
            )
        if (not preserved_literal_heading and narr_words and len(narr_words) >= 8
                and len(on_words) / len(narr_words) > MAX_ON_SCREEN_RATIO + 1e-6):
            issues.append(
                ValidationIssue(
                    code="onscreen_copies_vo",
                    severity="error",
                    scene_id=scene.scene_id,
                    message="On-screen text copies too much of the voice-over — rule 4",
                )
            )

        # Numbers visualized
        has_numeric_vo = any(ch.isdigit() or ch in "$%" for ch in scene.narration_excerpt)
        if has_numeric_vo and scene.visual_focus not in {"number", "chart", "comparison"} and not scene.number_value:
            issues.append(
                ValidationIssue(
                    code="number_not_visualized",
                    severity="warning",
                    scene_id=scene.scene_id,
                    message="Numeric narration without number/chart focus — rule 5",
                )
            )

        # Alex layout consistency
        if scene.use_alex and "character" not in scene.layout_id and scene.layout_id != "split_screen":
            issues.append(
                ValidationIssue(
                    code="alex_layout",
                    severity="error",
                    scene_id=scene.scene_id,
                    message="Alex requires a character-capable layout — rule 6",
                )
            )

        # Motion chaos
        if len(scene.animations) > MAX_ANIMATIONS_PER_SCENE:
            issues.append(
                ValidationIssue(
                    code="too_many_animations",
                    severity="error",
                    scene_id=scene.scene_id,
                    message="Too many animations in one scene — rule 8",
                )
            )
        for cue in scene.animations:
            if cue.at_sec < scene.start_sec - 0.05 or cue.at_sec > scene.end_sec + 0.05:
                issues.append(
                    ValidationIssue(
                        code="animation_outside_scene",
                        severity="error",
                        scene_id=scene.scene_id,
                        message="Animation timing outside scene — rule 9",
                    )
                )

        if len(scene.sound_events) > MAX_SFX_PER_SCENE:
            issues.append(
                ValidationIssue(
                    code="too_many_sfx",
                    severity="warning",
                    scene_id=scene.scene_id,
                    message="Too many SFX cues in one scene",
                )
            )

        # No hardcoded asset file paths
        for need in scene.asset_needs:
            if "/" in need.type or "\\" in need.type or need.type.endswith(".png"):
                issues.append(
                    ValidationIssue(
                        code="hardcoded_asset_path",
                        severity="error",
                        scene_id=scene.scene_id,
                        message="Asset needs must be semantic, not file paths",
                    )
                )

    # Alex ratio
    if plan.scenes:
        alex_ratio = sum(1 for scene in plan.scenes if scene.use_alex) / len(plan.scenes)
        if alex_ratio > MAX_ALEX_SCENE_RATIO + 1e-6:
            issues.append(
                ValidationIssue(
                    code="alex_overuse",
                    severity="error",
                    message=f"Alex used in {alex_ratio:.0%} of scenes (max {MAX_ALEX_SCENE_RATIO:.0%}) — rule 6",
                )
            )

    # Consecutive layouts (rule 7). Explicit exception: one consecutive icon_explainer
    # pair when both scenes are mechanism/icon focus (continuity of the same explanation).
    streak = 1
    for left, right in zip(plan.scenes, plan.scenes[1:], strict=False):
        # A supported causal action advances the same diagram; changing its layout would
        # break object continuity. A repeated state is still caught by the ordinary rule.
        if (left.visual_story and right.visual_story
                and left.visual_story.get('sequence') == right.visual_story.get('sequence') == 'raise-pilot-v1'
                and right.visual_story.get('previous_state') == left.visual_story.get('state')
                and right.visual_story.get('state') != left.visual_story.get('state')):
            streak = 1
            continue
        if left.layout_id == right.layout_id:
            # A different explanatory action may use the same readable layout.
            # Mere rewording of narration is not evidence of a changed visual state.
            li, ri = left.visual_intent, right.visual_intent
            if (li and ri and li.scene_mode in {'diagram', 'comparison'}
                    and ri.scene_mode in {'diagram', 'comparison'}
                    and li.visible_change.strip() and ri.visible_change.strip()
                    and li.visible_change.casefold().strip() != ri.visible_change.casefold().strip()):
                streak = 1
                continue
            from atnos.production.channels.wealth_blueprint.shot_language import different_literal_illustration_states

            if different_literal_illustration_states(left, right):
                # An envelope, expense wallet, separate jar and kept-goal state
                # may share a stable image layout. Repeated objects/state cannot.
                streak = 1
                continue
            streak += 1
            max_allowed = MAX_CONSECUTIVE_SAME_LAYOUT
            if (
                left.layout_id == "icon_explainer"
                and right.layout_id == "icon_explainer"
                and left.visual_focus == "icon"
                and right.visual_focus == "icon"
            ):
                max_allowed = 2
            if streak > max_allowed:
                issues.append(
                    ValidationIssue(
                        code="layout_repeat",
                        severity="error",
                        scene_id=right.scene_id,
                        message="Same layout repeated consecutively — rule 7",
                    )
                )
        else:
            streak = 1

    from atnos.production.channels.wealth_blueprint.shot_language import (
        has_practical_action,
        headline_is_dangling,
        slop_hits,
    )

    for scene in plan.scenes:
        if headline_is_dangling(scene.on_screen_text):
            issues.append(
                ValidationIssue(
                    code="headline_dangling",
                    severity="warning",
                    scene_id=scene.scene_id,
                    message="On-screen headline ends on a dangling word",
                )
            )
        if scene.shot_class is None or scene.visual_density is None:
            issues.append(
                ValidationIssue(
                    code="missing_shot_language",
                    severity="warning",
                    scene_id=scene.scene_id,
                    message="Scene has no shot class or visual density",
                )
            )
    if not has_practical_action(plan):
        issues.append(
            ValidationIssue(
                code="missing_practical_action",
                severity="warning",
                message="Narration has no concrete viewer action (set aside, automate, review, pause, choose)",
            )
        )
    for token in slop_hits(plan):
        issues.append(
            ValidationIssue(
                code="ai_slop_risk",
                severity="warning",
                message=f"Luxury/hype token in plan: {token}",
            )
        )
    from atnos.production.channels.wealth_blueprint.visual_decision import composition_flags

    for flag in composition_flags(plan.model_dump(mode="json")):
        issues.append(
            ValidationIssue(
                code=flag["code"].lower(),
                severity="warning",
                scene_id=flag["scene_id"] or None,
                message=flag["detail"],
            )
        )

    errors = [issue for issue in issues if issue.severity == "error"]
    return ScenePlanValidation(ok=not errors, issues=issues)
