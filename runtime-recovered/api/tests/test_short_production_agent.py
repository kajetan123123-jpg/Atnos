from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.agents.short_production import ApprovedHook, PlanRequest, ProductionFiles, ShortProductionAgent
from atnos.agents.short_production.planner import ShortProductionPlanner
from atnos.agents.short_production.qa import MediaProbe, QualityReviewService
from atnos.services.channel_ops_chat_service import ChannelOpsChatService
from channel_ops import ChannelOpsService
from test_support import build_test_settings, make_temp_dir


class OfflinePlanner(ShortProductionPlanner):
    def _llm_script(self, *, topic_title: str, hook: ApprovedHook):
        return None


class GoodProbeQa(QualityReviewService):
    def _probe_media(self, path: Path) -> MediaProbe:
        if path.suffix.casefold() == ".mp4":
            return MediaProbe(exists=True, duration_s=45.0, has_audio=True, has_video=True, width=1080, height=1920)
        return MediaProbe(exists=True, duration_s=45.0, has_audio=True, has_video=False)


class BadProbeQa(QualityReviewService):
    def _probe_media(self, path: Path) -> MediaProbe:
        return MediaProbe(exists=path.exists(), duration_s=75.0, has_audio=False, has_video=False, error="bad_asset")


class ShortProductionAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.channel_id = "lokalne-ai"
        self.topic_id = "topic-001"
        self.hook = ApprovedHook(
            variant="curiosity",
            text="Tego o lokalnym AI nikt ci nie mowi",
            opening_line="Najwieksza przewaga lokalnego AI jest prostsza niz myslisz.",
        )
        self._write_topic_queue()

    def test_create_plan_writes_complete_short_asset_plan(self) -> None:
        agent = self._agent()

        plan = agent.create_plan(
            PlanRequest(channel_id=self.channel_id, topic_id=self.topic_id, approved_hook=self.hook)
        )

        self.assertEqual(plan.channel_id, self.channel_id)
        self.assertEqual(plan.topic_id, self.topic_id)
        self.assertEqual(plan.script.hook, self.hook.text)
        self.assertGreaterEqual(plan.script.total_words, 80)
        self.assertLessEqual(plan.script.total_words, 120)
        self.assertTrue(all(len(line.split()) <= 20 for line in plan.script.body))
        self.assertEqual(plan.narration.tool, "elevenlabs")
        self.assertIn("capcut", plan.tool_sequence)
        self.assertTrue((agent.plan_dir(self.channel_id, plan.plan_id) / "plan.json").exists())
        self.assertTrue((agent.plan_dir(self.channel_id, plan.plan_id) / "production.log").exists())

    def test_run_qa_approves_good_assets(self) -> None:
        agent = self._agent(qa=GoodProbeQa(self.settings))
        plan = agent.create_plan(
            PlanRequest(channel_id=self.channel_id, topic_id=self.topic_id, approved_hook=self.hook)
        )
        files = self._write_assets(plan.plan_id)

        qa = agent.run_qa(plan.plan_id, files)

        self.assertEqual(qa.recommendation, "approve")
        self.assertTrue(qa.checks.all_pass)
        self.assertGreaterEqual(qa.scores.overall, 0.80)
        self.assertTrue((agent.plan_dir(self.channel_id, plan.plan_id) / "qa.json").exists())

    def test_run_qa_rejects_bad_assets(self) -> None:
        agent = self._agent(qa=BadProbeQa(self.settings))
        plan = agent.create_plan(
            PlanRequest(channel_id=self.channel_id, topic_id=self.topic_id, approved_hook=self.hook)
        )
        files = self._write_assets(plan.plan_id)

        qa = agent.run_qa(plan.plan_id, files)

        self.assertEqual(qa.recommendation, "reject")
        self.assertGreater(qa.checks.failed_count, 2)
        self.assertTrue(qa.fix_instructions)

    def test_review_package_is_private_and_approval_does_not_make_public(self) -> None:
        agent = self._agent(qa=GoodProbeQa(self.settings))
        plan = agent.create_plan(
            PlanRequest(channel_id=self.channel_id, topic_id=self.topic_id, approved_hook=self.hook)
        )
        files = self._write_assets(plan.plan_id)
        agent.run_qa(plan.plan_id, files)

        package = agent.build_review_package(plan.plan_id)
        approved = agent.approve_for_upload(package.review_id)

        self.assertEqual(package.upload_metadata.visibility, "private")
        self.assertEqual(approved.status, "approved_for_upload")
        self.assertEqual(approved.upload_metadata.visibility, "private")
        self.assertTrue((agent.plan_dir(self.channel_id, plan.plan_id) / "review_package.json").exists())

    def test_chat_command_creates_plan_artifact(self) -> None:
        ops = ChannelOpsService(self.settings)
        ops.register_channel(
            channel_id=self.channel_id,
            display_name="Lokalne AI",
            niche="lokalne AI i automatyzacja",
            language="pl",
            audience="tworcy solo",
            daily_target_count=1,
        )
        agent = self._agent()
        service = ChannelOpsChatService(self.settings, channel_ops=ops, short_production_agent=agent)

        operation = service.maybe_handle(f"Zaplanuj short dla tematu {self.topic_id} na kanal {self.channel_id}")

        self.assertTrue(operation.activated)
        self.assertEqual(operation.selected_provider_key, "short-production-agent")
        self.assertEqual(operation.metadata["short_production"]["status"], "planned")
        self.assertIn("Artefakt planu", operation.assistant_content or "")

    def _agent(self, *, qa: QualityReviewService | None = None) -> ShortProductionAgent:
        return ShortProductionAgent(
            self.settings,
            planner=OfflinePlanner(self.settings),
            qa_service=qa or GoodProbeQa(self.settings),
        )

    def _write_topic_queue(self) -> None:
        channel_dir = self.settings.data_dir / "channels" / self.channel_id
        channel_dir.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "id": self.topic_id,
                "package_id": "package-001",
                "channel_id": self.channel_id,
                "title": "Dlaczego lokalne AI na laptopie ma sens",
                "viral_score": 0.82,
                "rationale": "Temat laczy ciekawosc, praktyczny benefit i prosty format shorta.",
                "hooks": [self.hook.model_dump(mode="json")],
                "status": "approved",
                "approved_at": "2026-05-27T00:00:00+00:00",
            }
        ]
        (channel_dir / "topic_queue.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_assets(self, plan_id: str) -> ProductionFiles:
        plan_dir = self.settings.data_dir / "channels" / self.channel_id / "production" / plan_id
        assets = plan_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        short = assets / "final.mp4"
        narration = assets / "narration.mp3"
        thumbnail = assets / "thumbnail.png"
        short.write_bytes(b"fake-video")
        narration.write_bytes(b"fake-audio")
        thumbnail.write_bytes(b"fake-png")
        return ProductionFiles(
            short_file_path=str(short),
            narration_file_path=str(narration),
            thumbnail_file_path=str(thumbnail),
        )


if __name__ == "__main__":
    unittest.main()
