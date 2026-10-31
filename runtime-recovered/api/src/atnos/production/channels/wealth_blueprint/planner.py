from __future__ import annotations

from typing import Any

from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.scene_plan_models import (
    ScenePlannerInput,
    WealthBlueprintScenePlan,
)
from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan


class ScenePlannerError(RuntimeError):
    pass


class WealthBlueprintScenePlanner:
    """Decision layer between script/narration and the Remotion renderer.

    Produces a WealthBlueprintScenePlan JSON. Never executes render.
    """

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings

    def plan(self, payload: ScenePlannerInput | dict[str, Any]) -> WealthBlueprintScenePlan:
        data = payload if isinstance(payload, ScenePlannerInput) else ScenePlannerInput.model_validate(payload)
        if data.mode == "llm_assisted" and self.settings is not None:
            plan = self._plan_with_optional_llm(data)
        else:
            plan = plan_scenes_deterministic(data)

        validation = validate_scene_plan(plan)
        plan.validation = validation
        if not validation.ok:
            # Deterministic repair pass: rebuild deterministically and re-validate
            if plan.planner_mode != "deterministic":
                plan = plan_scenes_deterministic(data.model_copy(update={"mode": "deterministic"}))
                validation = validate_scene_plan(plan)
                plan.validation = validation
                plan.planner_mode = "deterministic"
                plan.notes = [*plan.notes, "Fell back to deterministic plan after LLM validation failure"]
            if not validation.ok:
                details = "; ".join(issue.message for issue in validation.issues if issue.severity == "error")
                raise ScenePlannerError(f"Scene plan failed deterministic validation: {details}")
        return plan

    async def plan_async(self, payload: ScenePlannerInput | dict[str, Any], *, context: Any | None = None) -> WealthBlueprintScenePlan:
        """Optional LLM-assisted path using existing ask_model orchestration."""
        data = payload if isinstance(payload, ScenePlannerInput) else ScenePlannerInput.model_validate(payload)
        if data.mode != "llm_assisted" or self.settings is None or context is None:
            return self.plan(data)

        draft = plan_scenes_deterministic(data)
        try:
            from atnos.production.steps.order_script import ask_model

            prompt = (
                "You are the Wealth Blueprint Scene Planner. Refine the draft scene plan. "
                "Do not render video. Keep English finance Shorts tone. "
                "Rules: min scene duration 2.2s; max 4 elements; short on-screen text "
                "(not full VO); visualize numbers; Alex sparingly; no consecutive identical layouts; "
                "sparse animations/SFX. Return JSON matching WealthBlueprintScenePlan.\n\n"
                f"SCRIPT:\n{data.script}\n\nDRAFT_PLAN:\n{draft.model_dump_json()}"
            )
            response = await ask_model(self.settings, context, prompt, WealthBlueprintScenePlan)
            plan = WealthBlueprintScenePlan.model_validate(response)
            plan.planner_mode = "llm_assisted"
        except Exception:
            plan = draft

        validation = validate_scene_plan(plan)
        plan.validation = validation
        if not validation.ok:
            plan = draft
            plan.validation = validate_scene_plan(plan)
            plan.notes = [*plan.notes, "LLM plan rejected; deterministic plan retained"]
            if not plan.validation.ok:
                details = "; ".join(i.message for i in plan.validation.issues if i.severity == "error")
                raise ScenePlannerError(f"Scene plan failed deterministic validation: {details}")
        return plan

    def _plan_with_optional_llm(self, data: ScenePlannerInput) -> WealthBlueprintScenePlan:
        # Sync entrypoint stays deterministic; async path uses ask_model.
        return plan_scenes_deterministic(data)
