from __future__ import annotations

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.access_reality_service import AccessRealityService
from atnos.services.creator_ops_service import CreatorOpsService
from atnos.services.hardware_guard_service import HardwareGuardService
from atnos.services.trusted_auto_service import TrustedAutoService
from atnos.services.ui_adapter_service import UiAdapterService
from atnos.repositories.creator_project_repository import CreatorProjectRepository
from atnos.repositories.remote_node_repository import RemoteNodeRepository
from atnos.db.models.enums import (
    RemoteDisplayMode,
    RemoteExecutionExecutor,
    RemoteExecutionOrigin,
    RemoteExecutionStatus,
    RemoteExecutionStepStatus,
    RemoteNodeStatus,
    RemoteNodeTransport,
    RemoteNodeTrustMode,
    RemoteRoutingTarget,
)


class UiAdapterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_done_run_does_not_emit_graph_progress_frames(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                trusted_auto = TrustedAutoService(session, app.state.settings)
                run = trusted_auto.execution_runs.create_run(
                    project_id=1,
                    scheduler_job_id=None,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="campaign",
                    status="done",
                    goal="Finished short",
                    connector_key="youtube",
                    platform_key="youtube",
                    risk_level="high",
                    input_json={"conversation_id": 99},
                    output_json={"status": "completed"},
                    artifacts_json={},
                    error_message=None,
                    started_at=trusted_auto._now(),
                    finished_at=trusted_auto._now(),
                )
                trusted_auto.execution_runs.create_step(
                    execution_run_id=run.id,
                    step_index=12,
                    step_type="graph.memory_update",
                    step_name="Memory update",
                    status="done",
                    selector_hint=None,
                    input_json={},
                    output_json={},
                    error_message=None,
                    screenshot_path=None,
                    started_at=trusted_auto._now(),
                    finished_at=trusted_auto._now(),
                )
                session.commit()

                frames = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).build_agent_frames()

        frame_types = [frame["type"] for frame in frames]
        self.assertNotIn("graph.node", frame_types)
        self.assertNotIn("graph.progress", frame_types)

    def test_content_readiness_frame_is_emitted(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                frames = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).build_agent_frames()

        payload = next(
            (
                frame.get("payload")
                for frame in frames
                if frame.get("type") == "content.readiness"
            ),
            None,
        )
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertIn("summary", payload)
        self.assertIn("items", payload)

    def test_running_run_still_emits_graph_progress_frames(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                trusted_auto = TrustedAutoService(session, app.state.settings)
                run = trusted_auto.execution_runs.create_run(
                    project_id=1,
                    scheduler_job_id=None,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="campaign",
                    status="running",
                    goal="Running short",
                    connector_key="youtube",
                    platform_key="youtube",
                    risk_level="high",
                    input_json={"conversation_id": 99},
                    output_json={"status": "running"},
                    artifacts_json={},
                    error_message=None,
                    started_at=trusted_auto._now(),
                    finished_at=None,
                )
                trusted_auto.execution_runs.create_step(
                    execution_run_id=run.id,
                    step_index=12,
                    step_type="graph.memory_update",
                    step_name="Memory update",
                    status="running",
                    selector_hint=None,
                    input_json={},
                    output_json={},
                    error_message=None,
                    screenshot_path=None,
                    started_at=trusted_auto._now(),
                    finished_at=None,
                )
                session.commit()

                frames = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).build_agent_frames()

        frame_types = [frame["type"] for frame in frames]
        self.assertIn("graph.node", frame_types)
        self.assertIn("graph.progress", frame_types)

    def test_blocked_run_does_not_emit_repeating_global_alert_frame(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                trusted_auto = TrustedAutoService(session, app.state.settings)
                trusted_auto.execution_runs.create_run(
                    project_id=1,
                    scheduler_job_id=None,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="campaign",
                    status="blocked",
                    goal="Blocked short",
                    connector_key="youtube",
                    platform_key="youtube",
                    risk_level="high",
                    input_json={"conversation_id": 99},
                    output_json={},
                    artifacts_json={},
                    error_message="Trusted auto jobs require account and connector context.",
                    started_at=trusted_auto._now(),
                    finished_at=trusted_auto._now(),
                )
                session.commit()

                frames = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).build_agent_frames()

        alert_titles = [
            str(frame["payload"].get("title"))
            for frame in frames
            if frame["type"] == "alert" and isinstance(frame.get("payload"), dict)
        ]
        self.assertNotIn("Run blocked", alert_titles)

    def test_remote_worker_frames_are_emitted_for_connected_nodes(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                repo = RemoteNodeRepository(session)
                node = repo.create_node(
                    label="Ubuntu Worker",
                    node_type="ubuntu-worker",
                    platform="linux",
                    hostname="ubuntu-lab",
                    host="198.51.100.229",
                    ssh_port=22,
                    ssh_user="atnos",
                    status=RemoteNodeStatus.CONNECTED,
                    trust_mode=RemoteNodeTrustMode.TRUSTED_AUTO,
                    transport=RemoteNodeTransport.WEBSOCKET,
                    display_mode=RemoteDisplayMode.X11,
                    browser_default_target="personal_profile",
                    public_capabilities_json={},
                    metadata_json={},
                    pause_requested=False,
                    last_error=None,
                )
                repo.create_capability_snapshot(
                    node_id=node.id,
                    cpu_percent=10.0,
                    load_avg_json=[0.1, 0.2, 0.3],
                    ram_total_mb=16000,
                    ram_free_mb=12000,
                    disk_free_gb=180.0,
                    network_online=True,
                    sudo_available=True,
                    ollama_ready=True,
                    browser_ready=True,
                    desktop_bridge_ready=True,
                    ffmpeg_ready=True,
                    docker_ready=True,
                    display_mode=RemoteDisplayMode.X11,
                    browser_mode="personal_profile",
                    python_version="3.12.3",
                    ffmpeg_version="6.1",
                    raw_payload_json={},
                    captured_at=TrustedAutoService(session, app.state.settings)._now(),
                )
                run = repo.create_run(
                    origin=RemoteExecutionOrigin.MANUAL,
                    requested_by="owner",
                    task_kind="shell",
                    routing_target=RemoteRoutingTarget.REMOTE,
                    assigned_node_id=node.id,
                    status=RemoteExecutionStatus.RUNNING,
                    label="Remote whoami",
                    request_payload_json={"steps": [{"action": "shell", "command": "whoami"}]},
                    result_json={},
                    error_json={},
                    fallback_reason=None,
                    started_at=TrustedAutoService(session, app.state.settings)._now(),
                    finished_at=None,
                )
                repo.create_step(
                    run_id=run.id,
                    step_index=0,
                    executor=RemoteExecutionExecutor.WORKER_CORE,
                    action_kind="shell",
                    command="whoami",
                    browser_target=None,
                    sudo=False,
                    status=RemoteExecutionStepStatus.RUNNING,
                    stdout=None,
                    stderr=None,
                    exit_code=None,
                    result_json={},
                    started_at=TrustedAutoService(session, app.state.settings)._now(),
                    finished_at=None,
                )
                session.commit()

                frames = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).build_agent_frames()

        frame_types = [frame["type"] for frame in frames]
        self.assertIn("remote.node", frame_types)
        self.assertIn("remote.execution.run", frame_types)

    def test_current_pipeline_surfaces_latest_video_artifact(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                creator_project = CreatorOpsService(session, app.state.settings).create_project(
                    name="YouTube Ops",
                    project_type="synthetic_influencer",
                    niche="ai",
                    target_audience="operators",
                    monetization_goal="validate workflow",
                    content_boundaries="safe",
                    platforms=["youtube"],
                    persona_type="synthetic_original_persona",
                    disclosure_policy="platform_dependent",
                    operating_mode="supervised_actions",
                    linked_account_ids=[],
                    notes=None,
                    safety_boundaries="no impersonation",
                    status="active",
                    approval_policy="always_require_approval",
                )
                CreatorProjectRepository(session).create_work_item(
                    creator_project_id=creator_project.id,
                    title="Upload draft",
                    work_type="youtube_upload",
                    status="drafted",
                    risk_level="medium",
                    approval_required=True,
                    platform="youtube",
                    draft_payload_json={
                        "title": "ATNOS upload draft",
                        "video_storage_path": "uploads/final-video.mp4",
                        "youtube_url": "https://youtu.be/demo123",
                    },
                    review_notes=None,
                    next_action=None,
                    safety_flags_json=[],
                )
                session.commit()

                pipeline = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).get_current_pipeline()

        artifact = pipeline.get("latest_video_artifact")
        self.assertIsInstance(artifact, dict)
        assert isinstance(artifact, dict)
        self.assertEqual(artifact["title"], "ATNOS upload draft")
        self.assertEqual(artifact["storage_path"], "uploads/final-video.mp4")
        self.assertEqual(artifact["external_url"], "https://youtu.be/demo123")

    def test_preview_only_pipeline_does_not_force_global_executing_stage(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                frames = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).build_agent_frames()

        stage_payload = next(
            frame["payload"]
            for frame in frames
            if frame["type"] == "stage"
        )
        self.assertEqual(stage_payload["stage"], "idle")

    def test_build_agent_frames_uses_hardware_guard_fallback_metrics(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        fallback_snapshot = SimpleNamespace(
            to_dict=lambda: {
                "free_vram_mb": None,
                "used_vram_mb": 6144,
                "total_vram_mb": 32768,
                "free_ram_mb": 24576,
                "total_ram_mb": 32768,
                "free_disk_gb": 512,
                "disk_path": str(self.root),
                "status": "ready",
                "notes": None,
            }
        )

        with patch.object(
            HardwareGuardService,
            "capture",
            return_value=fallback_snapshot,
        ):
            with TestClient(app):
                with app.state.database.session_scope() as session:
                    frames = UiAdapterService(
                        session,
                        settings=app.state.settings,
                        provider_registry=app.state.providers,
                    ).build_agent_frames()

        metrics_payload = next(
            frame["payload"]
            for frame in frames
            if frame["type"] == "metrics"
        )
        self.assertEqual(metrics_payload["vramUsedMB"], 6144)
        self.assertEqual(metrics_payload["vramTotalMB"], 32768)

    def test_bootstrap_uses_cached_hardware_snapshot_for_trusted_auto_summary(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        cached_snapshot = SimpleNamespace(
            to_dict=lambda: {
                "free_vram_mb": 11264,
                "used_vram_mb": 5120,
                "total_vram_mb": 16384,
                "free_ram_mb": 24576,
                "total_ram_mb": 32768,
                "used_ram_mb": 8192,
                "cpu_percent": 18.5,
                "gpu_name": "AMD Radeon RX 7600 XT",
                "free_disk_gb": 512,
                "disk_path": str(self.root),
                "status": "ready",
                "notes": None,
            }
        )

        with patch.object(
            HardwareGuardService,
            "capture",
            side_effect=AssertionError("bootstrap should not capture live hardware"),
        ), patch.object(HardwareGuardService, "latest", return_value=cached_snapshot):
            with TestClient(app):
                with app.state.database.session_scope() as session:
                    payload = UiAdapterService(
                        session,
                        settings=app.state.settings,
                        provider_registry=app.state.providers,
                    ).get_bootstrap()

        hardware_snapshot = payload["trusted_auto_summary"]["hardware_snapshot"]
        self.assertEqual(hardware_snapshot["used_ram_mb"], 8192)
        self.assertEqual(hardware_snapshot["cpu_percent"], 18.5)
        self.assertEqual(hardware_snapshot["gpu_name"], "AMD Radeon RX 7600 XT")

    def test_bootstrap_avoids_live_health_probes(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        runtime_calls: list[bool] = []
        access_calls: list[bool] = []

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                original_runtime_snapshot = service.runtime.snapshot
                original_access_snapshot = AccessRealityService.build_snapshot

                def runtime_spy(*, include_live_health: bool = False):
                    runtime_calls.append(include_live_health)
                    return original_runtime_snapshot(
                        include_live_health=include_live_health
                    )

                def access_spy(access_service, *, include_live_health: bool = True):
                    access_calls.append(include_live_health)
                    return original_access_snapshot(
                        access_service,
                        include_live_health=include_live_health,
                    )

                service.runtime.snapshot = runtime_spy  # type: ignore[method-assign]

                with patch(
                    "atnos.services.ui_adapter_service.PCAutomationService.list_adapters",
                    side_effect=AssertionError("bootstrap should not build every PC adapter"),
                ), patch(
                    "atnos.services.ui_adapter_service.AccessRealityService.build_snapshot",
                    autospec=True,
                    side_effect=access_spy,
                ):
                    service.get_bootstrap()

        self.assertEqual(runtime_calls, [False])
        self.assertEqual(access_calls, [False])

    def test_build_agent_frames_fast_mode_avoids_live_health_and_hardware_capture(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        cached_snapshot = SimpleNamespace(
            to_dict=lambda: {
                "free_vram_mb": 11264,
                "used_vram_mb": 5120,
                "total_vram_mb": 16384,
                "free_ram_mb": 24576,
                "total_ram_mb": 32768,
                "used_ram_mb": 8192,
                "cpu_percent": 18.5,
                "gpu_name": "AMD Radeon RX 7600 XT",
                "free_disk_gb": 512,
                "disk_path": str(self.root),
                "status": "ready",
                "notes": None,
            }
        )

        runtime_calls: list[bool] = []

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                original_runtime_snapshot = service.runtime.snapshot

                def runtime_spy(*, include_live_health: bool = False):
                    runtime_calls.append(include_live_health)
                    return original_runtime_snapshot(
                        include_live_health=include_live_health
                    )

                service.runtime.snapshot = runtime_spy  # type: ignore[method-assign]

                with patch.object(
                    HardwareGuardService,
                    "capture",
                    side_effect=AssertionError(
                        "agent websocket frames should not capture live hardware in fast mode"
                    ),
                ), patch.object(
                    HardwareGuardService,
                    "latest",
                    return_value=cached_snapshot,
                ):
                    frames = service.build_agent_frames(
                        include_live_health=False,
                        fast_mode=True,
                    )

        self.assertEqual(runtime_calls, [False])
        metrics_payload = next(
            frame["payload"]
            for frame in frames
            if frame["type"] == "metrics"
        )
        self.assertEqual(metrics_payload["vramUsedMB"], 5120)
        self.assertEqual(metrics_payload["vramTotalMB"], 16384)

    def test_provider_cards_detect_gemini_runtime_env_secret(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        app.state.settings.gemini_api_key = "AIzaSyTestRuntimeGeminiKey1234567890"

        with TestClient(app):
            with app.state.database.session_scope() as session:
                cards = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                ).list_provider_cards(runtime_snapshot=SimpleNamespace(providers=[]))

        gemini = next(card for card in cards if card["provider_key"] == "gemini")
        self.assertTrue(gemini["has_secret"])
        self.assertEqual(gemini["status"], "configured")
        self.assertEqual(gemini["readiness_status"], "configured")
        self.assertEqual(gemini["masked_secret_state"], "Runtime environment secret configured")

    def test_provider_card_probe_uses_gemini_runtime_env_secret(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        app.state.settings.gemini_api_key = "AIzaSyTestRuntimeGeminiKey1234567890"

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = UiAdapterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                with patch.object(
                    UiAdapterService,
                    "_probe_gemini",
                    return_value={"provider_key": "gemini", "status": "connected"},
                ) as probe:
                    result = service.test_provider_card("gemini")

        self.assertEqual(result["status"], "connected")
        self.assertTrue(probe.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
