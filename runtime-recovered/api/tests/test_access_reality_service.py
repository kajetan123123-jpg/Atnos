from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from atnos.services.access_reality_service import (
    AccessRealityService,
    AccessRealitySnapshot,
    AccessRealitySummary,
    AccessSubjectStatus,
)
from atnos.services.oauth_service import OAuthConnectorView


def _snapshot(
    *,
    snapshot_key: str,
    checked_at: str,
    subjects: list[AccessSubjectStatus],
) -> AccessRealitySnapshot:
    counts = {
        "none": 0,
        "identity_only": 0,
        "metadata_only": 0,
        "configured": 0,
        "live_read": 0,
        "live_write": 0,
        "blocked": 0,
        "unavailable": 0,
    }
    for item in subjects:
        counts[item.access_level] += 1
    return AccessRealitySnapshot(
        snapshot_key=snapshot_key,
        checked_at=datetime.fromisoformat(checked_at.replace("Z", "+00:00")),
        summary=AccessRealitySummary(total_subjects=len(subjects), **counts),
        subjects=tuple(subjects),
        operational_context={"app_env": "test"},
    )


class AccessRealityServiceTest(unittest.TestCase):
    def test_snapshot_maps_identity_policy_runtime_and_live_sources(self) -> None:
        now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
        service = AccessRealityService.__new__(AccessRealityService)
        service.session = object()
        service.settings = SimpleNamespace(app_env="test")
        service.provider_registry = object()
        service.users = SimpleNamespace(
            get_owner=lambda: SimpleNamespace(id=1, email="owner@example.com")
        )
        service.execution_policies = SimpleNamespace(
            get_for_user=lambda _user_id: SimpleNamespace(
                allow_local_file_read=True,
                allow_local_file_write=False,
                allow_web_read=True,
                allow_external_fallback=True,
            )
        )
        service.runtime_settings = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                browser_control_enabled=True,
                external_actions_enabled=False,
                oauth_enabled=True,
                scheduler_enabled=False,
                voice_replies_enabled=False,
            )
        )
        service.oauth = SimpleNamespace(
            list_connectors=lambda: [
                OAuthConnectorView(
                    connector_key="github",
                    display_name="GitHub",
                    status="available",
                    scopes=("read:user", "repo"),
                    supports_pkce=True,
                    enabled=True,
                    configured=True,
                    setup_warnings=(),
                    connection_id=None,
                    connected_at=None,
                    disconnected_at=None,
                    last_error=None,
                )
            ]
        )
        service.accounts = object()
        service.trusted_auto = SimpleNamespace(
            list_accounts=lambda: [
                {
                    "account_id": 77,
                    "label": "Bootstrap mailbox",
                    "provider_key": "bootstrap-mailbox",
                    "status": "configured",
                    "has_secret": True,
                    "linked_integration_key": "bootstrap-mailbox",
                    "allowed_capabilities": ["read_metadata"],
                    "trust_grants": [],
                }
            ]
        )
        service._utcnow = lambda: now

        with (
            patch(
                "atnos.services.access_reality_service.RuntimeInventoryService"
            ) as runtime_cls,
            patch(
                "atnos.services.access_reality_service.ContentAccessRealityService"
            ) as content_cls,
            patch(
                "atnos.services.access_reality_service.LocalHostControlService"
            ) as local_host_cls,
            patch(
                "atnos.services.access_reality_service.GmailConnectorService"
            ) as gmail_cls,
            patch(
                "atnos.services.access_reality_service.GoogleCalendarConnectorService"
            ) as calendar_cls,
            patch(
                "atnos.services.access_reality_service.GitHubConnectorProbeService"
            ) as github_cls,
            patch(
                "atnos.services.access_reality_service.YouTubeConnectorService"
            ) as youtube_cls,
        ):
            runtime_cls.return_value.snapshot.return_value = SimpleNamespace(
                providers=(
                    SimpleNamespace(
                        provider_key="ollama-local",
                        label="Ollama Local",
                        status="ready",
                        health_status="healthy",
                        status_reason="adapter ready",
                        health_reason="runtime ok",
                        installed_models=("qwen3:8b",),
                    ),
                )
            )
            content_cls.return_value.build_snapshot.return_value = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        platform_key="youtube",
                        display_name="YouTube",
                        status="supervised_ready",
                        available_actions=("publish_supervised",),
                        blocking_reasons=(),
                        account_label="YouTube Studio glowny",
                        connector_key="youtube-studio",
                        connected=True,
                        needs_auth=False,
                        missing_grant=False,
                        granted_scopes=("scope:upload",),
                        linked_projects=("Projekt bazowy",),
                    )
                ]
            )
            local_host_cls.return_value.get_capability_snapshot.return_value = SimpleNamespace(
                platform="windows",
                browser_recipe_available=False,
                playwright_adapter_available=True,
                browser_open_available=True,
                execution_enabled=True,
                powershell_available=True,
                desktop_input_available=True,
                elevated=False,
                current_workdir="F:\\Apps\\Atnos",
            )
            gmail_cls.return_value.get_status.return_value = SimpleNamespace(
                connector_key="gmail",
                display_name="Gmail",
                provider_configured=True,
                connected=False,
                live_write_enabled=False,
                granted_scopes=(),
                warnings=(),
            )
            calendar_cls.return_value.get_status.return_value = SimpleNamespace(
                connector_key="google-calendar",
                display_name="Google Calendar",
                provider_configured=True,
                connected=True,
                live_write_enabled=False,
                granted_scopes=("calendar.readonly",),
                warnings=(),
            )
            github_cls.return_value.get_status.return_value = SimpleNamespace(
                connector_key="github",
                display_name="GitHub",
                provider_configured=True,
                live_read_enabled=False,
                live_write_enabled=False,
                granted_scopes=(),
                warnings=(),
            )
            youtube_cls.return_value.get_status.return_value = SimpleNamespace(
                connector_key="youtube-studio",
                display_name="YouTube Studio",
                configured=True,
                connected=True,
                granted_scopes=("youtube.upload",),
                warnings=(),
            )

            snapshot = service.build_snapshot()

        subjects = {item.subject_key: item for item in snapshot.subjects}
        self.assertEqual(subjects["identity.owner_email"].access_level, "identity_only")
        self.assertEqual(subjects["policy.local_file_read"].access_level, "live_read")
        self.assertEqual(subjects["policy.local_file_write"].access_level, "blocked")
        self.assertEqual(subjects["runtime.browser_control"].access_level, "live_write")
        self.assertEqual(subjects["provider.ollama-local"].access_level, "live_read")
        self.assertEqual(subjects["connector.gmail"].access_level, "configured")
        self.assertEqual(subjects["connector.google-calendar"].access_level, "live_read")
        self.assertEqual(subjects["platform.youtube"].access_level, "live_write")
        self.assertEqual(subjects["trusted_account.77"].access_level, "configured")
        self.assertEqual(subjects["local_host.browser_recipe"].access_level, "configured")
        self.assertIn(subjects["local_host.screen_capture"].access_level, {"live_read", "unavailable"})

    def test_diff_ignores_timestamp_only_noise(self) -> None:
        service = AccessRealityService.__new__(AccessRealityService)
        subject_v1 = AccessSubjectStatus(
            subject_key="provider.ollama-local",
            subject_type="provider",
            display_name="Ollama Local",
            access_level="live_read",
            summary="Provider is reachable and can enter routing.",
            last_checked_at=datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
        )
        subject_v2 = AccessSubjectStatus(
            subject_key="provider.ollama-local",
            subject_type="provider",
            display_name="Ollama Local",
            access_level="live_read",
            summary="Provider is reachable and can enter routing.",
            last_checked_at=datetime(2026, 5, 13, 10, 5, tzinfo=timezone.utc),
        )
        previous = _snapshot(
            snapshot_key="same-1",
            checked_at="2026-05-13T10:00:00Z",
            subjects=[subject_v1],
        )
        current = _snapshot(
            snapshot_key="same-1",
            checked_at="2026-05-13T10:05:00Z",
            subjects=[subject_v2],
        )

        changes = service.diff(previous, current)
        self.assertEqual(changes, ())

    def test_diff_detects_material_change(self) -> None:
        service = AccessRealityService.__new__(AccessRealityService)
        previous = _snapshot(
            snapshot_key="before",
            checked_at="2026-05-13T10:00:00Z",
            subjects=[
                AccessSubjectStatus(
                    subject_key="provider.ollama-local",
                    subject_type="provider",
                    display_name="Ollama Local",
                    access_level="live_read",
                    summary="Provider is reachable and can enter routing.",
                )
            ],
        )
        current = _snapshot(
            snapshot_key="after",
            checked_at="2026-05-13T10:05:00Z",
            subjects=[
                AccessSubjectStatus(
                    subject_key="provider.ollama-local",
                    subject_type="provider",
                    display_name="Ollama Local",
                    access_level="unavailable",
                    summary="Runtime is unreachable.",
                    reasons=("Health check failed.",),
                )
            ],
        )

        changes = service.diff(previous, current)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].subject_key, "provider.ollama-local")
        self.assertEqual(changes[0].from_access_level, "live_read")
        self.assertEqual(changes[0].to_access_level, "unavailable")
        self.assertIn("niedostepne", changes[0].title.lower())

    def test_trusted_account_change_is_not_exposed_as_user_toast(self) -> None:
        service = AccessRealityService.__new__(AccessRealityService)
        previous = _snapshot(
            snapshot_key="trusted-account-before",
            checked_at="2026-05-13T10:00:00Z",
            subjects=[
                AccessSubjectStatus(
                    subject_key="trusted_account.77",
                    subject_type="trusted_account",
                    display_name="YouTube Studio",
                    access_level="configured",
                    summary="Trusted account is configured.",
                )
            ],
        )
        current = _snapshot(
            snapshot_key="trusted-account",
            checked_at="2026-05-13T10:05:00Z",
            subjects=[
                AccessSubjectStatus(
                    subject_key="trusted_account.77",
                    subject_type="trusted_account",
                    display_name="YouTube Studio",
                    access_level="metadata_only",
                    summary="Trusted account metadata exists, but no secret is stored.",
                )
            ],
        )

        changes = service.diff(previous, current)

        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0].user_visible)


if __name__ == "__main__":
    unittest.main()
