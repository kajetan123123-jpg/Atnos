from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.creative_video_operator_service import CreativeVideoOperatorService
from atnos.services.local_video_generation_service import VideoGenerationResult
from content.short_agent import ShortAgentResult
from atnos.services.trusted_auto_service import TrustedAutoService

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class CreativeVideoOperatorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)
        bootstrap_test_app(self.app)

    def test_does_not_activate_for_regular_chat(self) -> None:
        with self.app.state.database.session_scope() as session:
            service = CreativeVideoOperatorService(
                session,
                settings=self.app.state.settings,
                provider_registry=self.app.state.providers,
                plugin_registry=self.app.state.plugins,
            )
            result = service.maybe_handle(
                content="napisz mi plan dnia",
                conversation_id=42,
                project_id=None,
                allow_local_file_write=True,
            )
        self.assertFalse(result.activated)

    def test_generic_video_request_generates_local_mp4_artifact(self) -> None:
        with self.app.state.database.session_scope() as session:
            service = CreativeVideoOperatorService(
                session,
                settings=self.app.state.settings,
                provider_registry=self.app.state.providers,
                plugin_registry=self.app.state.plugins,
            )

            def fake_generate_video(*, output_path: Path, **kwargs):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"fake-mp4")
                return VideoGenerationResult(
                    output_path=output_path.resolve(),
                    backend="ltx-video",
                    width=512,
                    height=512,
                    duration_sec=10,
                    fps=24,
                    frame_count=240,
                    vram_total_gb=16.0,
                )

            with patch(
                "atnos.services.creative_video_operator_service.LocalVideoGenerationService.generate_video",
                side_effect=fake_generate_video,
            ):
                result = service.maybe_handle(
                    content="stworz 10-sekundowe wideo promujace Atnosa",
                    conversation_id=42,
                    project_id=None,
                    allow_local_file_write=True,
                )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_provider_key, "creative-video-operator")
        self.assertEqual(result.selected_model_key, "ltx-video")
        self.assertIn("Wygenerowalem lokalne wideo MP4", result.assistant_content or "")
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0]["file_extension"], ".mp4")
        self.assertNotIn("scenariusz", (result.assistant_content or "").lower())

    def test_routes_short_request_queue_campaign_job_and_returns_generating_status(self) -> None:
        with self.app.state.database.session_scope() as session:
            service = CreativeVideoOperatorService(
                session,
                settings=self.app.state.settings,
                provider_registry=self.app.state.providers,
                plugin_registry=self.app.state.plugins,
            )
            captured: dict[str, object] = {}

            def fake_run_job_now(
                _trusted_auto,
                *,
                project_id,
                job_type,
                goal,
                connector_key,
                platform_key,
                account_id,
                trust_grant_id,
                payload_json,
                requires_trusted_auto,
                execute_now,
            ):
                captured.update(
                    {
                        "project_id": project_id,
                        "job_type": job_type,
                        "goal": goal,
                        "connector_key": connector_key,
                        "platform_key": platform_key,
                        "account_id": account_id,
                        "trust_grant_id": trust_grant_id,
                        "quality_profile": payload_json.get("quality_profile"),
                        "publish_mode": payload_json.get("publish_mode"),
                        "approved_for_publish": payload_json.get("approved_for_publish"),
                        "peer_consultation": payload_json.get("peer_consultation"),
                        "requires_trusted_auto": requires_trusted_auto,
                        "execute_now": execute_now,
                    }
                )
                return {
                    "job": {
                        "job_id": 71,
                        "status": "queued",
                        "job_type": "campaign",
                    },
                    "run": None,
                }

            with patch.object(
                TrustedAutoService,
                "run_job_now",
                fake_run_job_now,
            ):
                result = service.maybe_handle(
                    content="zrob shorta o Atnosie do YouTube Shorts z mocnym hookiem",
                    conversation_id=42,
                    project_id=None,
                    allow_local_file_write=True,
                    quality_profile="premium",
                    runtime_controls=object(),
                )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_provider_key, "creative-video-operator")
        self.assertEqual(result.selected_model_key, "campaign-youtube-short")
        self.assertTrue(result.kick_scheduler)
        self.assertIn("Uruchomilem workflow shorta", result.assistant_content or "")
        self.assertIn("Tryb jakosci: `premium`.", result.assistant_content or "")
        self.assertEqual(len(result.artifacts), 0)
        self.assertEqual(captured["job_type"], "campaign")
        self.assertEqual(captured["quality_profile"], "premium")
        self.assertEqual(captured["publish_mode"], "local_only")
        self.assertFalse(bool(captured["approved_for_publish"]))
        self.assertFalse(bool(captured["peer_consultation"]))
        self.assertFalse(bool(captured["requires_trusted_auto"]))
        self.assertFalse(bool(captured["execute_now"]))

    def test_explicit_polish_short_request_runs_local_short_agent(self) -> None:
        with self.app.state.database.session_scope() as session:
            service = CreativeVideoOperatorService(
                session,
                settings=self.app.state.settings,
                provider_registry=self.app.state.providers,
                plugin_registry=self.app.state.plugins,
            )
            output_dir = self.root / "data" / "shorts" / "run"
            output_dir.mkdir(parents=True, exist_ok=True)
            fake_result = ShortAgentResult(
                status="manual_upload_ready",
                topic="Atnos",
                output_dir=output_dir,
                final_mp4=output_dir / "final.mp4",
                quality_report={"overall_score": 8.5},
                artifacts={
                    "render_qa": str(output_dir / "render_qa.json"),
                    "production_report": str(output_dir / "production_report.md"),
                },
                repair_attempts=1,
                quality_run={
                    "status": "DONE_VERIFIED",
                    "score": 8.5,
                    "report_path": str(output_dir / "quality.md"),
                    "evidence_count": 3,
                },
            )
            with patch("atnos.services.creative_video_operator_service.ShortAgent.run", return_value=fake_result):
                result = service.maybe_handle(
                    content="Zrób mi shorta po polsku o Atnosie",
                    conversation_id=42,
                    project_id=None,
                    allow_local_file_write=True,
                    quality_profile="premium",
                )

        self.assertTrue(result.activated)
        self.assertEqual(result.selected_provider_key, "local-short-agent")
        self.assertIn("final.mp4", result.assistant_content or "")
        self.assertEqual(
            result.metadata["creative_video_operator"]["workflow_state"]["status"],
            "manual_upload_ready",
        )
        self.assertEqual(
            result.metadata["creative_video_operator"]["quality_run"]["status"],
            "DONE_VERIFIED",
        )

    def test_accepts_natural_short_request_without_action_verb(self) -> None:
        with self.app.state.database.session_scope() as session:
            service = CreativeVideoOperatorService(
                session,
                settings=self.app.state.settings,
                provider_registry=self.app.state.providers,
                plugin_registry=self.app.state.plugins,
            )
            with patch.object(
                TrustedAutoService,
                "run_job_now",
                return_value={
                    "job": {"job_id": 15, "status": "queued", "job_type": "campaign"},
                    "run": None,
                },
            ):
                result = service.maybe_handle(
                    content="chce shorta o Atnosie i local-first AI",
                    conversation_id=9,
                    project_id=None,
                    allow_local_file_write=True,
                )
        self.assertTrue(result.activated)

    def test_blocks_vague_short_request(self) -> None:
        with self.app.state.database.session_scope() as session:
            service = CreativeVideoOperatorService(
                session,
                settings=self.app.state.settings,
                provider_registry=self.app.state.providers,
                plugin_registry=self.app.state.plugins,
            )
            result = service.maybe_handle(
                content="zrob shorta",
                conversation_id=42,
                project_id=None,
                allow_local_file_write=True,
            )
        self.assertTrue(result.activated)
        self.assertEqual(result.selected_model_key, "blocked")
        self.assertIn("Brakuje konkretnego briefu", result.assistant_content or "")


if __name__ == "__main__":
    unittest.main()
