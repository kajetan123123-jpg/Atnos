from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atnos.core.config import Settings
from atnos.skills.router import route_task
from content.hook_variants import build_hook_variants
from content.script_generator import ShortScript
from content.short_asset_plan import build_short_asset_plan, render_pre_render_quality_report, render_visual_direction_markdown
from monetization import MonetizationInitializationService, MonetizationStateMachine, validate_short_pre_render_contract
from monetization.learning_handoff import MonetizationLearningHandoff
from monetization.models import ExecutionPhase


def make_settings(root: Path) -> Settings:
    return Settings(app_env="test", app_root=root, data_dir=root / "data")


class MonetizationCoreTest(unittest.TestCase):
    def test_initialization_writes_status_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            status = MonetizationInitializationService(settings).initialize()
            status_path = settings.data_dir / "monetization" / "initialization_status.json"

            self.assertEqual(status.revenue_vector.value, "automated_shorts_factory")
            self.assertTrue(status.state_machine_ready)
            self.assertTrue(status.channel_ops_ready)
            self.assertTrue(status.shorts_contract_ready)
            self.assertTrue(status.private_upload_only)
            self.assertTrue(status_path.exists())
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["revenue_vector"], "automated_shorts_factory")

    def test_state_machine_records_safe_plan_execute_verify_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            machine = MonetizationStateMachine(settings)

            run = machine.run(
                task="prepare one local short package",
                execute=lambda: {"package": "ok"},
                verify=lambda result: result == {"package": "ok"},
            )

            self.assertEqual(run.status, "committed")
            self.assertEqual([event.phase for event in run.events], [
                ExecutionPhase.PLAN,
                ExecutionPhase.EXECUTE,
                ExecutionPhase.VERIFY,
                ExecutionPhase.COMMIT,
            ])
            self.assertEqual(run.artifacts["package"], "ok")

    def test_state_machine_recovers_from_timeout_without_unhandled_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            machine = MonetizationStateMachine(settings)

            def fail():
                raise TimeoutError("provider timeout")

            run = machine.run(task="generate script with provider fallback", execute=fail)

            self.assertEqual(run.status, "recovered")
            self.assertEqual(run.fallback_provider, "anthropic-cloud")
            self.assertIn(ExecutionPhase.RECOVER, [event.phase for event in run.events])

    def test_high_risk_action_creates_approval_payload_and_does_not_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            executed = {"value": False}

            def execute():
                executed["value"] = True
                return {"should_not": "run"}

            run = MonetizationStateMachine(settings).run(
                task="public upload and payment setup",
                execute=execute,
                target="youtube_publication",
                risk_level="high",
            )

            self.assertEqual(run.status, "awaiting_approval")
            self.assertFalse(executed["value"])
            self.assertIsNotNone(run.approval_id)
            self.assertTrue((settings.data_dir / "monetization" / "approval_queue.jsonl").exists())

    def test_short_pre_render_validator_fails_when_artifacts_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_short_pre_render_contract(Path(tmp))

            self.assertFalse(result.valid)
            self.assertTrue(any("hook_variants.json is missing" == issue for issue in result.issues))

    def test_short_pre_render_validator_accepts_generated_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            script = _script()
            hooks = build_hook_variants(script=script, topic="local AI")
            plan = build_short_asset_plan(script=script, topic="local AI", hook_variants=hooks)
            (output_dir / "hook_variants.json").write_text(json.dumps(hooks.to_dict()), encoding="utf-8")
            (output_dir / "short_asset_plan.json").write_text(json.dumps(plan.to_dict()), encoding="utf-8")
            (output_dir / "visual_direction.md").write_text(
                render_visual_direction_markdown(topic="local AI", hook_variants=hooks, asset_plan=plan),
                encoding="utf-8",
            )
            (output_dir / "quality_report.md").write_text(
                render_pre_render_quality_report(hook_variants=hooks, asset_plan=plan, script_score=8.1),
                encoding="utf-8",
            )

            result = validate_short_pre_render_contract(output_dir)

            self.assertTrue(result.valid, result.issues)
            self.assertEqual(result.scene_count, len(plan.scenes))
            self.assertEqual(result.selected_hook_id, hooks.selected_hook_id)

    def test_learning_handoff_creates_codex_review_required_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            result = MonetizationLearningHandoff(settings).create(
                batch_summary={"processed_items": 1},
                qa_failures=["hook too generic"],
            )

            self.assertEqual(result.status, "codex_review_required")
            self.assertTrue(result.handoff_path.exists())
            self.assertTrue(result.report_path.exists())
            self.assertIn("must not self-modify", result.handoff_path.read_text(encoding="utf-8"))

    def test_canonical_skill_registry_routes_required_domains(self) -> None:
        cases = {
            "deploy sanity schema": "sanity:",
            "apply brand voice to this copy": "brand-voice:",
            "prepare onboarding checklist for new hire": "human-resources:",
            "design critique of this UI": "design:",
            "prepare three shorty na kanal lokalne-ai": "atnos:channel-ops",
            "youtube private upload analytics": "atnos:youtube-automation",
            "open browser and read page": "atnos:browser-operator",
            "sprawdz laptop ubuntu przez ssh": "atnos:ubuntu-full-control",
        }

        for task, expected_prefix in cases.items():
            selected = route_task(task).selected_skills
            self.assertTrue(
                any(skill == expected_prefix or skill.startswith(expected_prefix) for skill in selected),
                f"{task!r} selected {selected}, expected {expected_prefix}",
            )


def _script() -> ShortScript:
    hook = "This local AI workflow saves time before you render anything."
    body = (
        "Start with one repeatable topic format.",
        "Write a visual plan before creating frames.",
        "Block upload until QA passes.",
    )
    cta = "Save this checklist before the next render."
    return ShortScript(
        title="Local AI workflow",
        hook=hook,
        body=body,
        cta=cta,
        voiceover=" ".join((hook, *body, cta)),
    )


if __name__ == "__main__":
    unittest.main()
