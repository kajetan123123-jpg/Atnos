from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from atnos.services.account_credentials_service import AccountCredentialView
from atnos.services.content_access_reality_service import ContentAccessRealityService
from atnos.services.github_connector_probe_service import GitHubConnectorStatusView
from atnos.services.google_calendar_connector_service import GoogleCalendarConnectorStatusView
from atnos.services.oauth_service import OAuthConnectionView, OAuthConnectorView
from atnos.services.youtube_connector_service import YouTubeConnectionStatus


def _account_view(
    *,
    account_id: int,
    label: str,
    provider_key: str,
    has_secret: bool,
    linked_integration_key: str | None = None,
) -> AccountCredentialView:
    now = datetime.now(timezone.utc)
    return AccountCredentialView(
        id=account_id,
        label=label,
        provider_key=provider_key,
        account_identifier=f"{provider_key}:{account_id}",
        secret_type="oauth_token_later",
        storage_backend="os_keyring" if has_secret else "metadata_only",
        status="configured" if has_secret else "metadata_only",
        has_secret=has_secret,
        risk_level="medium",
        allowed_capabilities=("read_metadata",),
        linked_integration_key=linked_integration_key,
        linked_project_id=None,
        notes=None,
        masked_secret_state="Configured (masked)" if has_secret else "Metadata only",
        created_at=now,
        updated_at=now,
        last_rotated_at=now if has_secret else None,
        last_used_at=None,
    )


class ContentAccessRealityServiceTest(unittest.TestCase):
    def test_build_snapshot_classifies_content_ops_readiness(self) -> None:
        now = datetime.now(timezone.utc)
        service = ContentAccessRealityService.__new__(ContentAccessRealityService)
        service.session = object()
        service.settings = object()
        service.runtime_settings = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                oauth_enabled=True,
                publish_automation_enabled=True,
                browser_control_enabled=True,
                login_automation_enabled=True,
            )
        )
        service.oauth = SimpleNamespace(
            list_connectors=lambda: [
                OAuthConnectorView(
                    connector_key="youtube-studio",
                    display_name="YouTube Studio",
                    status="connected",
                    scopes=("openid",),
                    supports_pkce=True,
                    enabled=True,
                    configured=True,
                    setup_warnings=(),
                    connection_id=101,
                    connected_at=now,
                    disconnected_at=None,
                    last_error=None,
                ),
                OAuthConnectorView(
                    connector_key="google-calendar",
                    display_name="Google Calendar",
                    status="connected",
                    scopes=("openid",),
                    supports_pkce=True,
                    enabled=True,
                    configured=True,
                    setup_warnings=(),
                    connection_id=102,
                    connected_at=now,
                    disconnected_at=None,
                    last_error=None,
                ),
                OAuthConnectorView(
                    connector_key="gmail-readonly",
                    display_name="Gmail",
                    status="connected",
                    scopes=("openid",),
                    supports_pkce=True,
                    enabled=True,
                    configured=True,
                    setup_warnings=(),
                    connection_id=301,
                    connected_at=now,
                    disconnected_at=None,
                    last_error=None,
                ),
            ],
            list_connections=lambda: [
                OAuthConnectionView(
                    id=301,
                    connector_key="gmail-readonly",
                    display_name="Gmail",
                    status="connected",
                    scopes=("https://www.googleapis.com/auth/gmail.readonly",),
                    account_id=401,
                    connected_at=now,
                    disconnected_at=None,
                    last_error=None,
                    metadata={},
                    audit_log=(),
                    created_at=now,
                    updated_at=now,
                )
            ],
        )
        service.accounts = SimpleNamespace(
            list_accounts=lambda: [
                _account_view(
                    account_id=201,
                    label="YouTube Studio glowny",
                    provider_key="youtube",
                    has_secret=True,
                    linked_integration_key="youtube",
                ),
                _account_view(
                    account_id=202,
                    label="Calendar owner",
                    provider_key="google-calendar",
                    has_secret=True,
                    linked_integration_key="google-calendar",
                ),
                _account_view(
                    account_id=401,
                    label="Bootstrap mailbox",
                    provider_key="bootstrap-mailbox",
                    has_secret=True,
                    linked_integration_key="bootstrap-mailbox",
                ),
            ]
        )
        service.trust_grants = SimpleNamespace(
            list_all=lambda: [
                SimpleNamespace(
                    account_id=201,
                    connector_key="youtube",
                    is_enabled=True,
                    publish_allowed=True,
                    connector_write_allowed=True,
                )
            ]
        )
        service.creator_ops = SimpleNamespace(
            list_projects=lambda: [
                SimpleNamespace(name="Projekt bazowy", linked_account_ids=[201, 401]),
            ]
        )
        service.browser_runtime = SimpleNamespace(describe=lambda: SimpleNamespace(ready=True))

        with (
            patch(
                "atnos.services.content_access_reality_service.YouTubeConnectorService"
            ) as youtube_cls,
            patch(
                "atnos.services.content_access_reality_service.GoogleCalendarConnectorService"
            ) as calendar_cls,
            patch(
                "atnos.services.content_access_reality_service.GitHubConnectorProbeService"
            ) as github_cls,
        ):
            youtube_cls.return_value.get_status.return_value = YouTubeConnectionStatus(
                connector_key="youtube-studio",
                display_name="YouTube Studio",
                configured=True,
                connected=True,
                ready=True,
                warnings=(),
                account_id=201,
                channel_title="Main Channel",
                granted_scopes=("https://www.googleapis.com/auth/youtube.upload",),
            )
            calendar_cls.return_value.get_status.return_value = GoogleCalendarConnectorStatusView(
                connector_key="google-calendar",
                display_name="Google Calendar",
                integration_key="google-calendar",
                oauth_status="connected",
                connection_id=102,
                account_id=202,
                provider_enabled=True,
                provider_configured=True,
                token_present=True,
                required_scopes=("https://www.googleapis.com/auth/calendar",),
                granted_scopes=("https://www.googleapis.com/auth/calendar.readonly",),
                warnings=(),
                checked_at=now,
                live_read_enabled=True,
                live_write_enabled=False,
                connected=True,
            )
            github_cls.return_value.get_status.return_value = GitHubConnectorStatusView(
                connector_key="github",
                display_name="GitHub",
                integration_key="github",
                oauth_status="available",
                connection_id=None,
                account_id=None,
                provider_enabled=True,
                provider_configured=True,
                token_present=False,
                required_scopes=("read:user", "repo"),
                granted_scopes=(),
                warnings=(),
                connected_at=None,
                disconnected_at=None,
                checked_at=now,
                live_read_enabled=False,
                live_write_enabled=False,
            )

            snapshot = service.build_snapshot()
            chat_summary = service.build_chat_summary()

        items = {item.platform_key: item for item in snapshot.items}
        self.assertEqual(items["youtube"].status, "supervised_ready")
        self.assertIn("publish_supervised", items["youtube"].available_actions)
        self.assertEqual(items["google-calendar"].status, "read_ready")
        self.assertEqual(items["github"].status, "metadata_only")
        self.assertEqual(items["bootstrap-mailbox"].status, "supervised_ready")
        self.assertEqual(items["bootstrap-mailbox"].connection_id, 301)
        self.assertEqual(snapshot.summary.publish_mode, "supervised_publish")
        self.assertEqual(snapshot.summary.primary_youtube_status, "supervised_ready")
        self.assertIn("youtube: status=supervised_ready", chat_summary)
        self.assertIn("google-calendar: status=read_ready", chat_summary)

    def test_enabled_youtube_configs_are_reported_as_separate_channel_cards(self) -> None:
        now = datetime.now(timezone.utc)
        service = ContentAccessRealityService.__new__(ContentAccessRealityService)
        service.session = object()
        service.settings = object()
        runtime_controls = SimpleNamespace(oauth_enabled=True, publish_automation_enabled=False)
        status_by_account = {
            201: YouTubeConnectionStatus(
                connector_key="youtube-studio",
                display_name="YouTube Studio",
                configured=True,
                connected=True,
                ready=True,
                warnings=(),
                account_id=201,
                granted_scopes=("https://www.googleapis.com/auth/youtube.upload",),
            ),
            202: YouTubeConnectionStatus(
                connector_key="youtube-studio",
                display_name="YouTube Studio",
                configured=True,
                connected=True,
                ready=True,
                warnings=(),
                account_id=202,
                granted_scopes=("https://www.googleapis.com/auth/youtube.upload",),
            ),
        }

        def youtube_service(_session, _settings, *, account_id=None):
            return SimpleNamespace(get_status=lambda: status_by_account[account_id])

        connections = [
            OAuthConnectionView(
                id=501,
                connector_key="youtube-studio",
                display_name="YouTube Studio",
                status="connected",
                scopes=("https://www.googleapis.com/auth/youtube.upload",),
                account_id=201,
                connected_at=now,
                disconnected_at=None,
                last_error=None,
                metadata={},
                audit_log=(),
                created_at=now,
                updated_at=now,
            ),
            OAuthConnectionView(
                id=502,
                connector_key="youtube-studio",
                display_name="YouTube Studio",
                status="connected",
                scopes=("https://www.googleapis.com/auth/youtube.upload",),
                account_id=202,
                connected_at=now,
                disconnected_at=None,
                last_error=None,
                metadata={},
                audit_log=(),
                created_at=now,
                updated_at=now,
            ),
        ]

        with (
            patch.object(
                ContentAccessRealityService,
                "_active_youtube_content_accounts",
                return_value=[
                    {"account_id": "youtube:finance", "channel_title": "Wealth Blueprint", "youtube_oauth_account_id": 201},
                    {"account_id": "youtube:history", "channel_title": "Dark Chronicles", "youtube_oauth_account_id": 202},
                ],
            ),
            patch("atnos.services.content_access_reality_service.YouTubeConnectorService", side_effect=youtube_service),
        ):
            items = service._build_youtube_items(
                checked_at=now,
                runtime_controls=runtime_controls,
                oauth_connectors={},
                oauth_connections=connections,
                account_by_id={
                    201: _account_view(account_id=201, label="Finance OAuth", provider_key="youtube", has_secret=True),
                    202: _account_view(account_id=202, label="History OAuth", provider_key="youtube", has_secret=True),
                },
                grants=[],
                linked_projects_by_account={},
            )

        by_key = {item.platform_key: item for item in items}
        self.assertEqual(set(by_key), {"youtube:youtube:finance", "youtube:youtube:history"})
        self.assertEqual(by_key["youtube:youtube:finance"].display_name, "Wealth Blueprint")
        self.assertEqual(by_key["youtube:youtube:history"].connection_id, 502)
        self.assertEqual(by_key["youtube:youtube:history"].status, "draft_ready")


if __name__ == "__main__":
    unittest.main()
