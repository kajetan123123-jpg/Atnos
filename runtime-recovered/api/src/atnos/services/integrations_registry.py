from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.custom_integration_repository import CustomIntegrationRepository
from atnos.services.integration_config_service import IntegrationConfigService, ObsidianEffectiveConfig


@dataclass(frozen=True, slots=True)
class IntegrationDescriptor:
    key: str
    label: str
    category: str
    status: str
    capabilities: tuple[str, ...]
    permission_level: str
    is_available: bool
    is_write_capable: bool
    notes: str
    setup_mode: str
    setup_requirements: tuple[str, ...]
    can_configure_in_ui: bool
    can_connect_account: bool
    missing_requirements: tuple[str, ...]
    next_action_label: str
    origin: str
    source_kind: str
    has_adapter: bool
    configured_accounts_count: int


PLANNED_CONNECTORS: tuple[IntegrationDescriptor, ...] = tuple(
    IntegrationDescriptor(
        key=key,
        label=label,
        category=category,
        status="planned",
        capabilities=capabilities,
        permission_level="not_connected",
        is_available=False,
        is_write_capable=False,
        notes=f"Planned connector. No live {label} calls in v0.",
        setup_mode=setup_mode,
        setup_requirements=("Secure account storage v0", "Read-only account connection design", "Permission gates"),
        can_configure_in_ui=False,
        can_connect_account=False,
        missing_requirements=("account_connection",),
        next_action_label="Connection planned",
        origin="built_in",
        source_kind="system",
        has_adapter=False,
        configured_accounts_count=0,
    )
    for key, label, category, capabilities, setup_mode in (
        ("github", "GitHub", "code", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("notion", "Notion", "knowledge", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("google-drive", "Google Drive", "documents", ("read_metadata", "read_content"), "oauth_planned"),
        ("google-docs", "Google Docs", "documents", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("google-calendar", "Google Calendar", "calendar", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("linear", "Linear", "planning", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("slack", "Slack", "communication", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("youtube", "YouTube", "content", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("gmail", "Gmail", "communication", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("instagram", "Instagram", "content", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("tiktok", "TikTok", "content", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("x", "X", "communication", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("linkedin", "LinkedIn", "communication", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("facebook", "Facebook", "communication", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "oauth_planned"),
        ("canva", "Canva", "design", ("read_metadata", "draft_write"), "manual_planned"),
        ("video-editor", "Video Editor", "content", ("draft_write", "confirmed_write"), "manual_planned"),
        ("tts", "TTS", "content", ("draft_write",), "api_key_planned"),
        ("image-generator", "Image Generator", "design", ("draft_write", "confirmed_write"), "api_key_planned"),
        ("local-codegen", "Local Codegen", "code", ("read_metadata", "read_content", "draft_write", "confirmed_write"), "local_config"),
        ("ollama-local", "Ollama Local", "local_runtime", ("read_metadata", "draft_write"), "local_config"),
        ("kokoro-local", "Kokoro Local", "audio", ("draft_write", "confirmed_write"), "local_config"),
        ("comfyui", "ComfyUI", "design", ("draft_write", "confirmed_write"), "local_config"),
        ("pulid-identity", "PuLID Identity", "design", ("draft_write", "confirmed_write"), "manual_planned"),
        ("ltx-video", "LTX-Video", "content", ("draft_write", "confirmed_write"), "local_config"),
        ("wan-video", "Wan Video", "content", ("draft_write", "confirmed_write"), "manual_planned"),
        ("wav2lip", "Wav2Lip", "content", ("draft_write", "confirmed_write"), "manual_planned"),
        ("ffmpeg-amf", "FFmpeg AMF", "local_runtime", ("draft_write", "confirmed_write"), "local_config"),
        ("discord-webhook", "Discord Webhook", "communication", ("draft_write", "confirmed_write"), "manual_planned"),
        ("analytics-generic", "Analytics Generic", "analytics", ("read_metadata", "read_content"), "manual_planned"),
        ("onlyfans", "OnlyFans", "creator_monetization", ("read_metadata", "draft_write", "confirmed_write"), "oauth_planned"),
        ("creator-platform-generic", "Creator Platform Generic", "creator_monetization", ("read_metadata", "draft_write", "confirmed_write"), "oauth_planned"),
        ("tradingview", "TradingView", "market", ("read_metadata", "read_content", "draft_write"), "manual_planned"),
        ("xtb", "XTB", "market", ("read_metadata", "read_content", "confirmed_write"), "oauth_planned"),
        ("broker-generic", "Broker Generic", "market", ("read_metadata", "read_content", "confirmed_write"), "oauth_planned"),
        ("market-data-generic", "Market Data Generic", "market", ("read_metadata", "read_content"), "api_key_planned"),
    )
)


class IntegrationsRegistryService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.configs = IntegrationConfigService(session, settings)
        self.custom_integrations = CustomIntegrationRepository(session)
        self.account_credentials = AccountCredentialRepository(session)

    def list_integrations(self) -> list[IntegrationDescriptor]:
        counts = self.account_credentials.configured_counts_by_integration_key()
        custom = [
            self._custom_descriptor(item, counts.get(item.key, 0))
            for item in self.custom_integrations.list_all()
        ]
        planned = [
            self._with_account_count(descriptor, counts.get(descriptor.key, 0))
            for descriptor in PLANNED_CONNECTORS
        ]
        return [self._obsidian_descriptor(counts.get("obsidian", 0)), *planned, *custom]

    def _obsidian_descriptor(self, configured_accounts_count: int) -> IntegrationDescriptor:
        config = self.configs.get_obsidian_config()
        missing_requirements = self._obsidian_missing_requirements(config)

        if config.path_exists and config.export_enabled:
            return IntegrationDescriptor(
                key="obsidian",
                label="Obsidian",
                category="visible_memory",
                status="confirmed_write",
                capabilities=("draft_write", "confirmed_write"),
                permission_level="confirmed_write",
                is_available=True,
                is_write_capable=True,
                notes="Export ready. Local markdown export only.",
                setup_mode="local_config",
                setup_requirements=("Vault path", "Export enabled"),
                can_configure_in_ui=True,
                can_connect_account=False,
                missing_requirements=(),
                next_action_label="Configure Obsidian",
                origin="built_in",
                source_kind="system",
                has_adapter=True,
                configured_accounts_count=configured_accounts_count,
            )

        if config.path_exists:
            return IntegrationDescriptor(
                key="obsidian",
                label="Obsidian",
                category="visible_memory",
                status="configured",
                capabilities=("draft_write", "confirmed_write"),
                permission_level="draft_write",
                is_available=True,
                is_write_capable=False,
                notes="Vault path configured. Confirmed export is disabled.",
                setup_mode="local_config",
                setup_requirements=("Vault path", "Export enabled"),
                can_configure_in_ui=True,
                can_connect_account=False,
                missing_requirements=missing_requirements,
                next_action_label="Configure Obsidian",
                origin="built_in",
                source_kind="system",
                has_adapter=True,
                configured_accounts_count=configured_accounts_count,
            )

        return IntegrationDescriptor(
            key="obsidian",
            label="Obsidian",
            category="visible_memory",
            status="not_configured",
            capabilities=("draft_write", "confirmed_write"),
            permission_level="draft_write",
            is_available=False,
            is_write_capable=False,
            notes="Set an Obsidian vault path and enable export to allow confirmed local markdown export.",
            setup_mode="local_config",
            setup_requirements=("Vault path", "Export enabled"),
            can_configure_in_ui=True,
            can_connect_account=False,
            missing_requirements=missing_requirements,
            next_action_label="Configure Obsidian",
            origin="built_in",
            source_kind="system",
            has_adapter=True,
            configured_accounts_count=configured_accounts_count,
        )

    @staticmethod
    def _obsidian_missing_requirements(config: ObsidianEffectiveConfig) -> tuple[str, ...]:
        missing: list[str] = []
        if not config.path_exists:
            missing.append("vault_path")
        if not config.export_enabled:
            missing.append("export_enabled")
        return tuple(missing)

    @staticmethod
    def _custom_descriptor(item, configured_accounts_count: int) -> IntegrationDescriptor:
        capabilities = tuple(item.capabilities_json or [])
        setup_requirements = tuple(item.setup_requirements_json or [])
        missing_requirements = tuple(item.missing_requirements_json or [])
        return IntegrationDescriptor(
            key=item.key,
            label=item.label,
            category=item.category,
            status=item.status,
            capabilities=capabilities,
            permission_level=item.permission_level,
            is_available=False,
            is_write_capable=False,
            notes=item.notes,
            setup_mode=item.setup_mode,
            setup_requirements=setup_requirements,
            can_configure_in_ui=False,
            can_connect_account=False,
            missing_requirements=missing_requirements,
            next_action_label=item.next_action_label,
            origin="custom",
            source_kind=item.source_kind,
            has_adapter=item.has_adapter,
            configured_accounts_count=configured_accounts_count,
        )

    @staticmethod
    def _with_account_count(
        descriptor: IntegrationDescriptor,
        configured_accounts_count: int,
    ) -> IntegrationDescriptor:
        return IntegrationDescriptor(
            key=descriptor.key,
            label=descriptor.label,
            category=descriptor.category,
            status=descriptor.status,
            capabilities=descriptor.capabilities,
            permission_level=descriptor.permission_level,
            is_available=descriptor.is_available,
            is_write_capable=descriptor.is_write_capable,
            notes=descriptor.notes,
            setup_mode=descriptor.setup_mode,
            setup_requirements=descriptor.setup_requirements,
            can_configure_in_ui=descriptor.can_configure_in_ui,
            can_connect_account=descriptor.can_connect_account,
            missing_requirements=descriptor.missing_requirements,
            next_action_label=descriptor.next_action_label,
            origin=descriptor.origin,
            source_kind=descriptor.source_kind,
            has_adapter=descriptor.has_adapter,
            configured_accounts_count=configured_accounts_count,
        )
