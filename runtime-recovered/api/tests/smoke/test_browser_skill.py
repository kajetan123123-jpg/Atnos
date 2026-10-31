from __future__ import annotations

from atnos.browser_operator.planner import BrowserPlanner
from atnos.browser_operator.policies import classify_browser_risk


def test_browser_skill_creates_read_page_plan_without_real_ui() -> None:
    recipe = BrowserPlanner().plan_extract_text(url="https://example.com/")

    assert recipe.task == "extract_text"
    assert [step.action_type for step in recipe.steps] == ["goto", "observe"]
    assert all(classify_browser_risk(step) in {"low", "medium"} for step in recipe.steps)


def test_browser_skill_chatgpt_paste_plan_does_not_auto_submit_by_default() -> None:
    recipe = BrowserPlanner().plan_chatgpt_paste_prompt(prompt="Smoke prompt", auto_send=False)

    assert recipe.site == "chatgpt.com"
    assert recipe.task == "paste_prompt"
    assert "review_before_send" in recipe.human_required
    assert all(step.action_type != "press" for step in recipe.steps)
