"""Capability workflow: planner → runtime → brain → brief (no fake fallbacks)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atnos.integrations.capability_task_planner import plan_required_capabilities
from atnos.integrations.capability_workflow_service import CapabilityWorkflowService
from atnos.integrations.external_repositories import set_skill_enabled


WB_SCRIPT = Path(
    r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint\prod-dc76431dac84\script.json"
)


@pytest.fixture
def enabled_skills(tmp_path):
    set_skill_enabled("yt-package", True, root=tmp_path)
    set_skill_enabled("yt-script", True, root=tmp_path)
    set_skill_enabled("yt-retention", True, root=tmp_path)
    return tmp_path


def test_planner_infers_capabilities_without_repo_names():
    plan = plan_required_capabilities(
        task_type="capability_workflow",
        prompt=(
            "Review the current Wealth Blueprint short and prepare a better production brief "
            "and packaging recommendation using available capabilities."
        ),
        context={"production_id": "prod-dc76431dac84", "project_key": "wealth_blueprint"},
    )
    ids = [item.capability for item in plan]
    assert "YOUTUBE_SCRIPT_QA" in ids
    assert "YOUTUBE_PACKAGING" in ids
    blob = json.dumps([item.to_dict() for item in plan]).casefold()
    assert "awesome" not in blob
    assert "chatgpt-youtube" not in blob
    assert "opentoonz" not in blob
    assert "social-sdk" not in blob
    assert "scrapling" not in blob


def test_required_capability_failure_blocks_without_invented_output(enabled_skills):
    service = CapabilityWorkflowService(root=enabled_skills)
    result = service.run(
        prompt="Review packaging for this Wealth Blueprint video",
        context={
            "production_id": "prod-dc76431dac84",
            "project_key": "wealth_blueprint",
            "script_path": str(WB_SCRIPT) if WB_SCRIPT.is_file() else None,
            "script": "Set aside money every Friday. Build a cushion.",
        },
        inject_status={"YOUTUBE_PACKAGING": "FAILED"},
        max_retries=1,
    )
    assert result["workflow_status"] == "BLOCKED"
    packaging_exec = next(item for item in result["capabilities_executed"] if item["capability"] == "YOUTUBE_PACKAGING")
    assert packaging_exec["status"] == "FAILED"
    assert packaging_exec.get("output") is None
    assert result.get("packaging") is None


def test_optional_requires_setup_keeps_partial(enabled_skills):
    service = CapabilityWorkflowService(root=enabled_skills)
    result = service.run(
        prompt="Review the short and prepare packaging recommendation using available capabilities.",
        context={
            "production_id": "prod-dc76431dac84",
            "project_key": "wealth_blueprint",
            "script": (
                "Set aside a specific amount from your paycheck every Friday. "
                "This habit builds financial security. Start this habit today."
            ),
        },
        inject_status={"SOCIAL_READ": "REQUIRES_SETUP"},
    )
    assert result["workflow_status"] in {"PARTIAL", "COMPLETED"}
    skipped = {item["capability"]: item for item in result["skipped"]}
    assert "SOCIAL_READ" in skipped
    assert "not connected" in skipped["SOCIAL_READ"]["message"].casefold() or "unavailable" in skipped["SOCIAL_READ"]["message"].casefold()
    assert result["packaging"] is not None
    assert result["published"] is False


def test_optional_requires_data_is_not_failed(enabled_skills):
    service = CapabilityWorkflowService(root=enabled_skills)
    result = service.run(
        prompt="Review packaging and retention for this Wealth Blueprint short using available capabilities.",
        context={
            "project_key": "wealth_blueprint",
            "script": "Set aside money every Friday. Move a fixed amount into savings. Start today.",
        },
    )
    retention = next(
        (item for item in result["capabilities_executed"] if item["capability"] == "YOUTUBE_RETENTION_ANALYSIS"),
        None,
    )
    assert retention is not None
    assert retention["status"] == "REQUIRES_DATA"
    assert result["workflow_status"] != "FAILED"
    unresolved = [item for item in result["unresolved_data_requirements"] if item["capability"] == "YOUTUBE_RETENTION_ANALYSIS"]
    assert unresolved
    assert unresolved[0]["kind"] == "UNRESOLVED_DATA_REQUIREMENT"


def test_provenance_preserved_in_evidence_graph(enabled_skills):
    service = CapabilityWorkflowService(root=enabled_skills)
    result = service.run(
        prompt="Prepare a better production brief and packaging recommendation using available capabilities.",
        context={
            "project_key": "wealth_blueprint",
            "production_id": "prod-dc76431dac84",
            "script_path": str(WB_SCRIPT) if WB_SCRIPT.is_file() else None,
            "script": "Set aside money every Friday. Build security with consistent actions.",
        },
    )
    assert result["evidence_graph"]
    for edge in result["evidence_graph"]:
        assert edge.get("execution_id")
        assert edge.get("capability")
        assert "source_input" in edge
    titles = (result.get("packaging") or {}).get("title_concepts") or []
    assert titles
    assert all(item.get("status") == "CANDIDATE" for item in titles)
    assert (result.get("packaging") or {}).get("best_title_claimed") is False


def test_project_brain_write_policy_and_cross_project_isolation(enabled_skills):
    import sqlite3

    from atnos.production.project_content_brain.content_intelligence import ContentIntelligenceStore

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    store = ContentIntelligenceStore(db)
    service = CapabilityWorkflowService(root=enabled_skills, brain_store=store)
    result = service.run(
        prompt="Review the current Wealth Blueprint short and prepare a better production brief and packaging recommendation using available capabilities.",
        context={
            "project_key": "wealth_blueprint",
            "production_id": "prod-dc76431dac84",
            "script": "Set aside a specific amount from your paycheck every Friday. Start this habit today.",
        },
    )
    assert result["decisions"]
    wb_decisions = store.list_decisions("wealth_blueprint")
    assert wb_decisions
    assert store.list_decisions("wonder_crew") == []
    assert store.list_decisions("dark_chronicles") == []
    wb_insights = store.list_insights("wealth_blueprint")
    for insight in wb_insights:
        assert insight.get("performance_claimed") is not True
        assert "proven winner" not in str(insight.get("statement") or "").casefold()
    kinds = {insight.get("knowledge_type") or insight.get("kind") for insight in wb_insights}
    assert "OBSERVATION" in kinds or "RECOMMENDATION" in kinds or not wb_insights


def test_capability_execution_trace_shape(enabled_skills):
    service = CapabilityWorkflowService(root=enabled_skills)
    result = service.run(
        prompt="Review the short and prepare packaging using available capabilities.",
        context={"project_key": "wealth_blueprint", "script": "Move money to savings every Friday. Start today."},
    )
    stages = [item.get("stage") for item in result["execution_trace"]]
    assert "user_task" in stages
    assert "planner" in stages
    assert "capability_execute" in stages
    assert "quality_council" in stages
    assert "production_brief" in stages
    assert result["production_brief"]["status"] == "PRODUCTION_BRIEF_READY"
    assert result["production_started"] is False


def test_script_qa_structured_minimum(enabled_skills):
    from atnos.integrations.capability_runtime import execute_capability

    if not WB_SCRIPT.is_file():
        pytest.skip("WB pilot script missing")
    result = execute_capability(
        "YOUTUBE_SCRIPT_QA",
        parameters={"script_path": str(WB_SCRIPT)},
        project_id="wealth_blueprint",
        root=enabled_skills,
    )
    assert result["status"] == "SUCCESS"
    out = result["output"]
    for key in (
        "hook_issues",
        "clarity_issues",
        "pacing_issues",
        "actionable_value_issues",
        "unsupported_claims",
        "repetition",
        "cta_issues",
        "recommendations",
        "evidence",
    ):
        assert key in out
    assert out["original_script_preserved"] is True
    assert out["auto_rewrite"] is False


def test_task_planner_capability_plan_integration():
    from atnos.ai_workers.task_planner import TaskPlanner

    plan = TaskPlanner().build_plan(
        task_type="capability_workflow",
        prompt=(
            "Review the current Wealth Blueprint short and prepare a better production brief "
            "and packaging recommendation using available capabilities."
        ),
        context={"production_id": "prod-dc76431dac84", "project_key": "wealth_blueprint"},
        privacy_mode="strict_local",
        preferred_worker=None,
        allowed_workers=("capability_runtime", "ollama"),
        max_steps=4,
    )
    assert plan.required_capabilities
    assert any(item["capability"] == "YOUTUBE_PACKAGING" for item in plan.required_capabilities)
    assert plan.subtasks
    assert plan.subtasks[0].worker_key == "capability_runtime"
    assert "awesome" not in plan.summary.casefold()
