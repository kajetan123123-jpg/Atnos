from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from urllib import error as urllib_error

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.creator_ops_service import CreatorOpsService
from atnos.services.hardware_guard_service import HardwareSnapshotView
from atnos.services.trusted_auto_service import TrustedAutoService


class TrustedAutoWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_runtime_not_ready_is_blocked_not_done_and_does_not_schedule_analytics(self):
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(autonomy_mode="trusted_auto", scheduler_enabled=True)
                with patch("atnos.services.content_campaign_service.ContentCampaignService.run_youtube_short_campaign",
                           return_value={"status": "runtime_not_ready", "publish_result": {"status": "runtime_not_ready", "message": "Piper unavailable"}, "artifacts": {}}), \
                     patch.object(service, '_schedule_analytics_followup_if_needed', side_effect=AssertionError('blocked job cannot schedule analytics')):
                    result = service.run_job_now(project_id=1, job_type='campaign', goal='Test audit',
                        connector_key=None, platform_key=None, account_id=None, trust_grant_id=None,
                        payload_json={'publish_mode': 'local_only'}, requires_trusted_auto=False, execute_now=True)
                self.assertEqual(result['run']['status'], 'blocked')
                self.assertEqual(result['job']['status'], 'blocked')
                self.assertEqual(result['job']['last_error'], 'Piper unavailable')

    def test_campaign_run_waits_for_human_approval(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                    publish_automation_enabled=True,
                )
                with patch(
                    "atnos.services.content_campaign_service.ContentCampaignService.run_youtube_short_campaign",
                    return_value={
                        "status": "awaiting_approval",
                        "approval_required": True,
                        "approval_package": {
                            "title": "Test approval package",
                            "summary": "Human review required before publish.",
                        },
                        "publish_result": {
                            "status": "awaiting_approval",
                            "reason": "human_review_required",
                        },
                        "analytics_summary": {"status": "skipped"},
                        "workflow_state": {
                            "current_node": "graph.approval_gate",
                            "status": "awaiting_human",
                        },
                        "artifacts": {},
                    },
                ) as mocked_run_campaign:
                    result = service.run_job_now(
                        project_id=1,
                        job_type="campaign",
                        goal="Test short campaign",
                        connector_key=None,
                        platform_key=None,
                        account_id=None,
                        trust_grant_id=None,
                        payload_json={"brief": "Generate a short.", "quality_profile": "premium"},
                        requires_trusted_auto=False,
                        execute_now=True,
                    )

        self.assertIsNotNone(result["run"])
        self.assertEqual(mocked_run_campaign.call_args.kwargs["job"].payload_json["quality_profile"], "premium")
        self.assertEqual(result["run"]["status"], "waiting_for_input")
        self.assertEqual(result["job"]["status"], "waiting_for_input")
        self.assertTrue(result["run"]["output_json"]["approval_required"])
        self.assertEqual(
            result["run"]["output_json"]["workflow_state"]["current_node"],
            "graph.approval_gate",
        )

    def test_campaign_hardware_snapshot_is_advisory_and_does_not_block_work(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        low_vram_snapshot = HardwareSnapshotView(
            free_vram_mb=512,
            used_vram_mb=4096,
            total_vram_mb=8192,
            free_ram_mb=4096,
            total_ram_mb=16384,
            free_disk_gb=24,
            disk_path=str(self.root),
            status="blocked",
            notes="Free VRAM below 6 GB floor.",
            used_ram_mb=12288,
            cpu_percent=18.5,
            gpu_name="AMD Radeon RX 7600 XT",
        )

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                    default_retry_budget=0,
                )
                with (
                    patch(
                        "atnos.services.trusted_auto_service.HardwareGuardService.capture",
                        return_value=low_vram_snapshot,
                    ) as mocked_capture,
                    patch(
                        "atnos.services.trusted_auto_service.HardwareGuardService.release_gpu_pressure",
                    ) as mocked_release,
                    patch(
                        "atnos.services.content_campaign_service.ContentCampaignService.run_youtube_short_campaign",
                        return_value={
                            "status": "manual_upload_ready",
                            "approval_required": False,
                            "publish_result": {
                                "status": "local_render_complete",
                                "publish_path": "manual_upload_ready",
                            },
                            "analytics_summary": {"status": "skipped"},
                            "workflow_state": {
                                "current_node": "graph.local_delivery",
                                "status": "completed",
                            },
                            "artifacts": {},
                        },
                    ),
                ):
                    result = service.run_job_now(
                        project_id=1,
                        job_type="campaign",
                        goal="Short o lokalnym AI",
                        connector_key="youtube-studio",
                        platform_key="youtube",
                        account_id=None,
                        trust_grant_id=None,
                        payload_json={
                            "brief": "short o lokalnym ai",
                            "topic": "short o lokalnym ai",
                            "quality_profile": "premium",
                        },
                        requires_trusted_auto=False,
                        execute_now=True,
                    )

        mocked_capture.assert_called_once()
        mocked_release.assert_not_called()
        self.assertEqual(result["run"]["status"], "done")
        self.assertEqual(result["job"]["status"], "done")
        artifacts = result["run"]["artifacts_json"]
        self.assertEqual(artifacts["hardware_snapshot"]["status"], "blocked")
        self.assertEqual(artifacts["hardware_gate_mode"], "advisory_only")

    def test_run_job_now_resolves_single_project_trust_grant_before_creating_job(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                account = service.account_credentials.create_account(
                    label="YouTube Studio",
                    provider_key="youtube",
                    account_identifier="owner@example.com",
                    secret_type="oauth_token_later",
                    secret_value=None,
                    risk_level="high",
                    allowed_capabilities=["trusted_login", "browser_workflow", "connector_write"],
                    linked_integration_key=None,
                    linked_project_id=1,
                    notes=None,
                )
                grant = service.upsert_trust_grant(
                    trust_grant_id=None,
                    project_id=1,
                    account_id=account.id,
                    connector_key="youtube",
                    mode="trusted_auto",
                    domain_allowlist=[],
                    action_families=["publish"],
                    file_write_scope=[],
                    login_allowed=True,
                    two_factor_allowed=False,
                    publish_allowed=True,
                    browser_automation_allowed=True,
                    connector_write_allowed=True,
                    local_render_allowed=False,
                    max_retries=2,
                    cooldown_minutes=60,
                    is_enabled=True,
                    notes=None,
                )

                result = service.run_job_now(
                    project_id=1,
                    job_type="campaign",
                    goal="Campaign with inferred grant",
                    connector_key="youtube",
                    platform_key="youtube",
                    account_id=None,
                    trust_grant_id=None,
                    payload_json={"conversation_id": 101},
                    requires_trusted_auto=True,
                    execute_now=False,
                )

        self.assertEqual(result["job"]["connector_key"], "youtube")
        self.assertEqual(result["job"]["payload_json"]["account_id"], account.id)
        self.assertEqual(result["job"]["payload_json"]["trust_grant_id"], grant["trust_grant_id"])

    def test_run_job_now_rejects_trusted_auto_run_without_account_context(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)

                with self.assertRaisesRegex(
                    RuntimeError,
                    "Connect an account and enable one trusted auto grant",
                ):
                    service.run_job_now(
                        project_id=1,
                        job_type="campaign",
                        goal="Campaign without grant",
                        connector_key="youtube",
                        platform_key="youtube",
                        account_id=None,
                        trust_grant_id=None,
                        payload_json={"conversation_id": 101},
                        requires_trusted_auto=True,
                        execute_now=False,
                    )

                self.assertEqual(service.scheduler_jobs.list_all(), [])

    def test_provider_card_flags_wrong_secret_family(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            update = client.put(
                "/settings/provider-cards/openai",
                json={
                    "api_key": "sk-ant-test-secret",
                    "enabled": True,
                },
            )
            self.assertEqual(update.status_code, 200)
            card = update.json()
            self.assertEqual(card["provider_key"], "openai")
            self.assertEqual(card["sanity_status"], "warning")
            self.assertIn("Anthropic", card["sanity_reason"])
            self.assertIn(card["readiness_status"], {"configured", "blocked"})

    def test_settings_account_accepts_legacy_secret_type_alias_and_saves_password(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            response = client.put(
                "/settings/accounts",
                json={
                    "label": "YouTube Studio glowny",
                    "provider_key": "youtube",
                    "account_identifier": "owner@example.com",
                    "secret_type": "oauth_token",
                    "secret_value": None,
                    "linked_project_id": None,
                    "linked_integration_key": None,
                    "notes": None,
                    "allowed_capabilities": ["trusted_login", "browser_workflow"],
                    "login_identifier": "owner@example.com",
                    "password_value": "tajnehaslo123",
                    "totp_value": None,
                    "backup_codes_value": None,
                    "recovery_email": None,
                    "recovery_email_secret_value": None,
                    "connector_mapping_json": {},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        account = payload["account"]
        self.assertEqual(account["secret_type"], "oauth_token_later")
        self.assertEqual(account["status"], "metadata_only")
        self.assertEqual(account["credential_profile"]["login_identifier"], "owner@example.com")
        self.assertTrue(account["credential_profile"]["has_password"])

    def test_account_feedback_probe_checks_accounts_sequentially_and_classifies_statuses(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        class _FakeResponse:
            def __init__(self, status: int) -> None:
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self) -> int:
                return self.status

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.upsert_account(
                    account_id=None,
                    label="HubSpot",
                    provider_key="hubspot",
                    account_identifier="owner@example.com",
                    secret_type="oauth_token_later",
                    secret_value=None,
                    linked_project_id=None,
                    linked_integration_key=None,
                    notes=None,
                    allowed_capabilities=["browser_workflow"],
                    login_identifier=None,
                    password_value=None,
                    totp_value=None,
                    backup_codes_value=None,
                    recovery_email=None,
                    recovery_email_secret_value=None,
                    connector_mapping_json={"primary_url": "https://app.hubspot.com/login"},
                )
                service.upsert_account(
                    account_id=None,
                    label="OnlyFans",
                    provider_key="onlyfans",
                    account_identifier="creator@example.com",
                    secret_type="session_cookie_later",
                    secret_value=None,
                    linked_project_id=None,
                    linked_integration_key=None,
                    notes=None,
                    allowed_capabilities=["browser_workflow"],
                    login_identifier=None,
                    password_value=None,
                    totp_value=None,
                    backup_codes_value=None,
                    recovery_email=None,
                    recovery_email_secret_value=None,
                    connector_mapping_json={"primary_url": "https://onlyfans.com/"},
                )
                service.upsert_account(
                    account_id=None,
                    label="Slack",
                    provider_key="slack",
                    account_identifier="workspace@example.com",
                    secret_type="oauth_token_later",
                    secret_value=None,
                    linked_project_id=None,
                    linked_integration_key=None,
                    notes=None,
                    allowed_capabilities=["browser_workflow"],
                    login_identifier=None,
                    password_value=None,
                    totp_value=None,
                    backup_codes_value=None,
                    recovery_email=None,
                    recovery_email_secret_value=None,
                    connector_mapping_json={"primary_url": "https://slack.com/signin"},
                )

                def fake_urlopen(request, timeout=0):
                    url = request.full_url
                    if "hubspot" in url:
                        return _FakeResponse(200)
                    if "onlyfans" in url:
                        raise urllib_error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)
                    if "slack" in url:
                        raise urllib_error.URLError("timed out")
                    raise AssertionError(f"Unexpected probe url: {url}")

                with patch("atnos.services.trusted_auto_service.urllib_request.urlopen", side_effect=fake_urlopen):
                    results = service.probe_account_feedback(timeout_seconds=3.0)

        self.assertEqual([item["sequence"] for item in results], [1, 2, 3])
        by_label = {item["label"]: item for item in results}
        self.assertEqual(by_label["HubSpot"]["status"], "ok")
        self.assertEqual(by_label["HubSpot"]["http_status"], 200)
        self.assertEqual(by_label["OnlyFans"]["status"], "http_restricted")
        self.assertEqual(by_label["OnlyFans"]["http_status"], 403)
        self.assertEqual(by_label["Slack"]["status"], "timeout")
        self.assertIn("timed out", by_label["Slack"]["reason"])

    def test_account_feedback_probe_route_returns_results(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client, patch.object(
            TrustedAutoService,
            "probe_account_feedback",
            return_value=[
                {
                    "sequence": 1,
                    "account_id": 11,
                    "label": "HubSpot",
                    "provider_key": "hubspot",
                    "account_identifier": "owner@example.com",
                    "url": "https://app.hubspot.com/login",
                    "has_secret": False,
                    "grant_count": 0,
                    "checked_at": "2026-05-11T10:00:00Z",
                    "status": "ok",
                    "http_status": 200,
                    "reason": "Strona odpowiada HTTP 200.",
                }
            ],
        ) as probe_mock:
            response = client.post("/settings/accounts/feedback-probe", json={"timeout_seconds": 9})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["label"], "HubSpot")
        self.assertEqual(payload["results"][0]["status"], "ok")
        probe_mock.assert_called_once_with(timeout_seconds=9.0)

    def test_recover_interrupted_runs_requeues_job_with_retry_budget(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                job = service.scheduler_jobs.create(
                    project_id=1,
                    job_type="campaign",
                    trigger_mode="manual",
                    status="running",
                    connector_key="youtube",
                    platform_key="youtube",
                    payload_json={"goal": "Recover me"},
                    retry_count=0,
                    retry_budget=2,
                    requires_trusted_auto=False,
                    cooldown_key=None,
                    last_error=None,
                    last_run_at=None,
                    next_run_at=None,
                    locked_at=service._now(),
                    cancelled_at=None,
                )
                run = service.execution_runs.create_run(
                    project_id=1,
                    scheduler_job_id=job.id,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="campaign",
                    status="running",
                    goal="Recover me",
                    connector_key="youtube",
                    platform_key="youtube",
                    risk_level="high",
                    input_json={},
                    output_json={},
                    artifacts_json={},
                    error_message=None,
                    started_at=service._now(),
                    finished_at=None,
                )
                step = service.execution_runs.create_step(
                    execution_run_id=run.id,
                    step_index=0,
                    step_type="graph.render",
                    step_name="Render",
                    status="running",
                    selector_hint=None,
                    input_json={},
                    output_json={},
                    error_message=None,
                    screenshot_path=None,
                    started_at=service._now(),
                    finished_at=None,
                )
                session.commit()

                recovery = service.recover_interrupted_runs()
                self.assertEqual(recovery["recovered_jobs"], 1)
                self.assertEqual(recovery["requeued_jobs"], 1)
                self.assertEqual(recovery["recovered_runs"], 1)
                self.assertEqual(recovery["recovered_steps"], 1)

                refreshed_job = service.scheduler_jobs.get_by_id(job.id)
                refreshed_run = service.execution_runs.get_run(run.id)
                refreshed_step = service.execution_runs.list_steps(run.id)[0]

                self.assertIsNotNone(refreshed_job)
                self.assertIsNotNone(refreshed_run)
                self.assertEqual(refreshed_job.status, "retrying")
                self.assertEqual(refreshed_job.retry_count, 1)
                self.assertEqual(refreshed_run.status, "interrupted")
                self.assertTrue(
                    bool(
                        dict(refreshed_run.output_json or {})
                        .get("recovery", {})
                        .get("retry_scheduled"),
                    )
                )
                self.assertEqual(refreshed_step.status, "interrupted")

    def test_synthetic_creator_preview_job_runs_through_trusted_auto(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            create_response = client.post(
                "/creator-projects",
                json={
                    "name": "Synthetic Trusted Auto Base",
                    "project_type": "synthetic_model",
                    "niche": "portrait-led premium synthetic creator",
                    "target_audience": "adult viewers",
                    "monetization_goal": "manual teaser validation",
                    "content_boundaries": "legal, consent-based, non-impersonating boundaries",
                    "platforms": ["youtube_short"],
                    "persona_type": "synthetic_original_persona",
                    "disclosure_policy": "platform_dependent",
                    "operating_mode": "draft_only",
                },
            )
            self.assertEqual(create_response.status_code, 201)
            canonical_project_id = create_response.json()["project_id"]

            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                )
                with patch(
                    "atnos.services.trusted_auto_service.SyntheticCreatorWorkflowService.generate_preview",
                    return_value={
                        "status": "completed",
                        "creator_project_id": 1,
                        "project_name": "Synthetic Trusted Auto Base",
                        "artifacts": {
                            "portrait": {"filename": "portrait.png"},
                            "talking_head": {"descriptor": {"filename": "preview.mp4"}},
                        },
                        "vault_note_path": "Atnos/Business/SyntheticCreator/test.md",
                        "workflow_state": {"current_node": "graph.synthetic_creator_complete", "status": "done"},
                    },
                ) as mocked_generate_preview:
                    result = service.run_job_now(
                        project_id=int(canonical_project_id),
                        job_type="synthetic_creator_preview",
                        goal="Generate one synthetic talking-head preview",
                        connector_key=None,
                        platform_key="youtube_short",
                        account_id=None,
                        trust_grant_id=None,
                        payload_json={"quality_profile": "fast-turnaround"},
                        requires_trusted_auto=False,
                        execute_now=True,
                    )

        self.assertIsNotNone(result["run"])
        self.assertEqual(mocked_generate_preview.call_args.kwargs["quality_profile"], "fast-turnaround")
        self.assertEqual(result["run"]["status"], "done")
        self.assertEqual(result["run"]["run_type"], "synthetic_creator_preview")
        self.assertIn("synthetic_creator_preview", result["run"]["artifacts_json"])

    def test_synthetic_creator_preview_batch_job_runs_through_trusted_auto(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            create_response = client.post(
                "/creator-projects",
                json={
                    "name": "Synthetic Trusted Auto Batch",
                    "project_type": "synthetic_model",
                    "niche": "portrait-led premium synthetic creator",
                    "target_audience": "adult viewers",
                    "monetization_goal": "manual teaser validation",
                    "content_boundaries": "legal, consent-based, non-impersonating boundaries",
                    "platforms": ["youtube_short"],
                    "persona_type": "synthetic_original_persona",
                    "disclosure_policy": "platform_dependent",
                    "operating_mode": "draft_only",
                },
            )
            self.assertEqual(create_response.status_code, 201)
            canonical_project_id = create_response.json()["project_id"]

            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                )
                with patch(
                    "atnos.services.trusted_auto_service.SyntheticCreatorWorkflowService.generate_preview_batch",
                    return_value={
                        "status": "completed",
                        "creator_project_id": 1,
                        "variants": [
                            {"style": {"key": "editorial_premium"}, "reel_type": {"key": "talking_hook"}},
                            {"style": {"key": "cozy_girlfriend"}, "reel_type": {"key": "micro_story"}},
                        ],
                        "release_pack": {"platform_key": "youtube", "publish_path": "manual_upload_ready"},
                        "release_pack_artifact": {"filename": "release-pack.md"},
                        "release_pack_json_artifact": {"filename": "release-pack.json"},
                        "release_pack_vault_note_path": "Atnos/Social/ReleasePacks/release-pack.md",
                        "batch_report": {"filename": "batch.md"},
                        "vault_note_path": "Atnos/Business/SyntheticCreator/test-batch.md",
                        "workflow_state": {"current_node": "graph.synthetic_creator_batch_complete", "status": "done"},
                    },
                ) as mocked_generate_preview_batch:
                    result = service.run_job_now(
                        project_id=int(canonical_project_id),
                        job_type="synthetic_creator_preview_batch",
                        goal="Generate synthetic preview batch",
                        connector_key=None,
                        platform_key="youtube_short",
                        account_id=None,
                        trust_grant_id=None,
                        payload_json={
                            "style_keys": ["editorial_premium", "cozy_girlfriend"],
                            "max_items": 2,
                            "quality_profile": "premium",
                        },
                        requires_trusted_auto=False,
                        execute_now=True,
                    )

        self.assertIsNotNone(result["run"])
        self.assertEqual(mocked_generate_preview_batch.call_args.kwargs["quality_profile"], "premium")
        self.assertEqual(result["run"]["status"], "done")
        self.assertEqual(result["run"]["run_type"], "synthetic_creator_preview_batch")
        self.assertIn("synthetic_creator_preview_batch", result["run"]["artifacts_json"])
        self.assertEqual(
            result["run"]["artifacts_json"]["synthetic_creator_preview_batch"]["release_pack"]["platform_key"],
            "youtube",
        )

    def test_approve_publish_dispatches_for_synthetic_creator_batch(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            create_response = client.post(
                "/creator-projects",
                json={
                    "name": "Synthetic Approval Batch",
                    "project_type": "synthetic_model",
                    "niche": "portrait-led premium synthetic creator",
                    "target_audience": "adult viewers",
                    "monetization_goal": "manual teaser validation",
                    "content_boundaries": "legal, consent-based, non-impersonating boundaries",
                    "platforms": ["youtube_short"],
                    "persona_type": "synthetic_original_persona",
                    "disclosure_policy": "platform_dependent",
                    "operating_mode": "draft_only",
                },
            )
            self.assertEqual(create_response.status_code, 201)
            canonical_project_id = create_response.json()["project_id"]

            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                    publish_automation_enabled=True,
                )
                job = service.scheduler_jobs.create(
                    project_id=int(canonical_project_id),
                    job_type="synthetic_creator_preview_batch",
                    trigger_mode="manual",
                    status="waiting_for_input",
                    connector_key=None,
                    platform_key="youtube",
                    payload_json={"goal": "Synthetic batch ready for approval"},
                    retry_count=0,
                    retry_budget=2,
                    requires_trusted_auto=False,
                    cooldown_key=None,
                    last_error=None,
                    last_run_at=None,
                    next_run_at=None,
                    locked_at=None,
                    cancelled_at=None,
                )
                run = service.execution_runs.create_run(
                    project_id=int(canonical_project_id),
                    scheduler_job_id=job.id,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="synthetic_creator_preview_batch",
                    status="waiting_for_input",
                    goal="Synthetic batch ready for approval",
                    connector_key=None,
                    platform_key="youtube",
                    risk_level="high",
                    input_json={},
                    output_json={
                        "status": "awaiting_approval",
                        "approval_required": True,
                        "approval_package": {
                            "title": "Synthetic winner",
                            "summary": "Synthetic release pack is waiting for approval.",
                        },
                        "release_pack": {
                            "platform_key": "youtube",
                            "connector_key": "youtube-studio",
                            "publish_path": "official_youtube_ready",
                        },
                    },
                    artifacts_json={},
                    error_message=None,
                    started_at=service._now(),
                    finished_at=None,
                )
                session.commit()

                with patch(
                    "atnos.services.trusted_auto_service.SyntheticCreatorWorkflowService.publish_approved_batch",
                    return_value={
                        "status": "completed",
                        "workflow": "synthetic_creator_publish",
                        "publish_result": {"status": "uploaded", "video_id": "abc123"},
                        "analytics_summary": {"status": "ready", "views": 100},
                        "artifacts": {"feedback_loop": {"filename": "feedback.md"}},
                    },
                ):
                    publish_result = service.approve_campaign_publish(run.id)

                refreshed_source_run = service.execution_runs.get_run(run.id)
                refreshed_job = service.scheduler_jobs.get_by_id(job.id)

        self.assertEqual(publish_result["run_type"], "synthetic_creator_publish")
        self.assertEqual(publish_result["status"], "done")
        self.assertEqual(publish_result["output_json"]["publish_result"]["status"], "uploaded")
        self.assertIsNotNone(refreshed_source_run)
        self.assertEqual(refreshed_source_run.status, "done")
        self.assertEqual(refreshed_source_run.output_json["approval_status"], "approved")
        self.assertIsNotNone(refreshed_job)
        self.assertEqual(refreshed_job.status, "done")

    def test_approve_campaign_publish_schedules_analytics_followup_when_pending(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                    publish_automation_enabled=True,
                )
                job = service.scheduler_jobs.create(
                    project_id=1,
                    job_type="campaign",
                    trigger_mode="manual",
                    status="waiting_for_input",
                    connector_key="youtube-studio",
                    platform_key="youtube",
                    payload_json={"conversation_id": 5},
                    retry_count=0,
                    retry_budget=2,
                    requires_trusted_auto=False,
                    cooldown_key=None,
                    last_error=None,
                    last_run_at=None,
                    next_run_at=None,
                    locked_at=None,
                    cancelled_at=None,
                )
                run = service.execution_runs.create_run(
                    project_id=1,
                    scheduler_job_id=job.id,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="campaign",
                    status="waiting_for_input",
                    goal="Publish short campaign",
                    connector_key="youtube-studio",
                    platform_key="youtube",
                    risk_level="high",
                    input_json={"conversation_id": 5},
                    output_json={
                        "status": "awaiting_approval",
                        "approval_required": True,
                        "approval_package": {
                            "metadata": {"title": "Campaign title"},
                        },
                        "publish_result": {"status": "awaiting_approval"},
                    },
                    artifacts_json={},
                    error_message=None,
                    started_at=service._now(),
                    finished_at=None,
                )
                session.commit()

                with patch(
                    "atnos.services.trusted_auto_service.ContentCampaignService.publish_approved_campaign",
                    return_value={
                        "status": "completed",
                        "publish_result": {"status": "uploaded", "video_id": "abc123"},
                        "analytics_summary": {"status": "pending", "message": "not ready yet"},
                        "artifacts": {},
                    },
                ), patch.object(
                    service,
                    "_resolve_trust",
                    return_value=SimpleNamespace(
                        allowed=True,
                        reason="ok",
                        grant_id=None,
                    ),
                ):
                    publish_run = service.approve_campaign_publish(run.id)

                analytics_jobs = [item for item in service.scheduler_jobs.list_all() if item.job_type == "analytics_sync"]

        self.assertEqual(publish_run["status"], "done")
        self.assertEqual(len(analytics_jobs), 1)
        self.assertEqual(analytics_jobs[0].payload_json["video_id"], "abc123")
        self.assertEqual(analytics_jobs[0].payload_json["source_run_id"], publish_run["execution_run_id"])

    def test_analytics_sync_job_updates_source_run(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = TrustedAutoService(session, app.state.settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                )
                creator_project = CreatorOpsService(session, app.state.settings).create_project(
                    name="Synthetic project",
                    project_type="synthetic_influencer",
                    niche="premium synthetic creator",
                    target_audience="adult short-form audience",
                    monetization_goal="validate local preview",
                    content_boundaries="non-explicit",
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
                source_run = service.execution_runs.create_run(
                    project_id=creator_project.project_id,
                    scheduler_job_id=None,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="synthetic_creator_publish",
                    status="done",
                    goal="Synthetic publish complete",
                    connector_key="youtube-studio",
                    platform_key="youtube",
                    risk_level="high",
                    input_json={"conversation_id": 7},
                    output_json={
                        "status": "completed",
                        "publish_result": {"status": "uploaded", "video_id": "abc123"},
                        "analytics_summary": {"status": "pending"},
                    },
                    artifacts_json={},
                    error_message=None,
                    started_at=service._now(),
                    finished_at=service._now(),
                )
                session.commit()

                with patch(
                    "atnos.services.trusted_auto_service.PublishAnalyticsFollowupService.run_followup",
                    return_value={
                        "status": "completed",
                        "workflow": "youtube_publish_analytics_followup",
                        "source_run_id": source_run.id,
                        "video_id": "abc123",
                        "analytics_summary": {"status": "ready", "views": 321, "average_view_percentage": 82.5},
                        "strategy_snapshot": {
                            "hook_strategy": "faster_hook",
                            "pacing_strategy": "tighter_intro",
                            "cutaway_strategy": "add_midbeat_cutaway",
                            "title_strategy": "test_hook_first_title",
                        },
                        "feedback_artifact": {"filename": "atnos-analytics-followup.md", "storage_path": "data/atnos-outputs/global/followup.md"},
                        "vault_note_path": "Atnos/Social/Analytics/followup.md",
                        "workflow_state": {"current_node": "graph.analytics_sync", "status": "done"},
                    },
                ):
                    result = service.run_job_now(
                        project_id=1,
                        job_type="analytics_sync",
                        goal="Analytics follow-up for synthetic publish",
                        connector_key="youtube-studio",
                        platform_key="youtube",
                        account_id=None,
                        trust_grant_id=None,
                        payload_json={
                            "source_run_id": source_run.id,
                            "conversation_id": 7,
                            "video_id": "abc123",
                            "project_name": "Synthetic project",
                            "title": "Synthetic winner",
                            "publish_result": {"status": "uploaded", "video_id": "abc123"},
                            "review_window_hours": 48,
                        },
                        requires_trusted_auto=False,
                        execute_now=True,
                    )

                refreshed_source_run = service.execution_runs.get_run(source_run.id)
                refreshed_creator_project = CreatorOpsService(session, app.state.settings).get_project(creator_project.id)

        self.assertEqual(result["run"]["status"], "done")
        self.assertEqual(result["run"]["output_json"]["analytics_summary"]["status"], "ready")
        self.assertIsNotNone(refreshed_source_run)
        self.assertIsNotNone(refreshed_creator_project)
        self.assertEqual(refreshed_source_run.output_json["analytics_summary"]["status"], "ready")
        self.assertEqual(refreshed_source_run.output_json["analytics_strategy_snapshot"]["hook_strategy"], "faster_hook")
        self.assertEqual(refreshed_source_run.output_json["analytics_followup_job_id"], result["job"]["job_id"])
        self.assertIn("analytics_followup", refreshed_source_run.artifacts_json)
        assert refreshed_creator_project is not None
        self.assertEqual(
            dict(refreshed_creator_project.strategy_profile.get("hook_template") or {}).get("strategy_key"),
            "faster_hook",
        )


if __name__ == "__main__":
    unittest.main()
