from __future__ import annotations

import unittest

from agent.execution_director import AgentExecutionDirector
from agent.failure_recovery import FailureRecoveryPlanner
from agent.models import (
    AgentExecutionMode,
    AgentPlanStep,
    AgentTaskType,
    RiskLevel,
    ToolCallResult,
)
from agent.verification import AgentVerifier


class AgentExecutionRuntimeTest(unittest.TestCase):
    def test_director_executes_safe_ubuntu_tool_plan(self) -> None:
        decision = AgentExecutionDirector().decide(
            task_type=AgentTaskType.UBUNTU_CONTROL,
            risk_level=RiskLevel.LOW,
            approval_required=False,
            plan=(
                AgentPlanStep(
                    name="check_ubuntu_connection",
                    tool_name="ubuntu_check",
                    expected_result="Ubuntu checks return status.",
                ),
            ),
        )

        self.assertEqual(decision.mode, AgentExecutionMode.EXECUTE)
        self.assertTrue(decision.should_execute)
        self.assertFalse(decision.requires_approval)

    def test_director_requires_approval_for_high_risk_task(self) -> None:
        decision = AgentExecutionDirector().decide(
            task_type=AgentTaskType.YOUTUBE,
            risk_level=RiskLevel.HIGH,
            approval_required=True,
            plan=(AgentPlanStep(name="upload", tool_name=None),),
        )

        self.assertEqual(decision.mode, AgentExecutionMode.REQUIRE_APPROVAL)
        self.assertFalse(decision.should_execute)
        self.assertTrue(decision.requires_approval)

    def test_director_marks_short_as_artifact_task(self) -> None:
        decision = AgentExecutionDirector().decide(
            task_type=AgentTaskType.CONTENT_SHORT,
            risk_level=RiskLevel.MEDIUM,
            approval_required=False,
            plan=(AgentPlanStep(name="render", tool_name="content_short"),),
        )

        self.assertEqual(decision.mode, AgentExecutionMode.CREATE_ARTIFACT)
        self.assertTrue(decision.creates_artifact)
        self.assertTrue(decision.should_execute)

    def test_failure_recovery_gives_specific_action_for_content_short_context_gap(self) -> None:
        actions = FailureRecoveryPlanner().plan_recovery(
            (
                ToolCallResult(
                    tool_name="content_short",
                    success=False,
                    status="failed",
                    result={"status": "runtime_context_required"},
                ),
            )
        )

        self.assertTrue(any("session + provider_registry" in action for action in actions))

    def test_verifier_reports_failed_tool_details(self) -> None:
        result = AgentVerifier().verify(
            (
                ToolCallResult(
                    tool_name="browser_open",
                    success=False,
                    status="tool_error",
                    error="Playwright missing",
                ),
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "tool_failed")
        self.assertIn("browser_open", result.message)
        self.assertIn("Playwright missing", result.message)


if __name__ == "__main__":
    unittest.main()
