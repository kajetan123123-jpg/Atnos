from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy.orm import Session

from atnos.repositories.custom_integration_repository import CustomIntegrationRepository

BUILT_IN_INTEGRATION_KEYS = frozenset(
    {
        "obsidian",
        "github",
        "notion",
        "google-drive",
        "google-docs",
        "google-calendar",
        "linear",
        "slack",
        "youtube",
        "instagram",
        "tiktok",
        "canva",
        "video-editor",
        "tts",
        "image-generator",
        "analytics-generic",
        "onlyfans",
        "creator-platform-generic",
        "tradingview",
        "xtb",
        "broker-generic",
        "market-data-generic",
    }
)
ALLOWED_CAPABILITIES = ("read_metadata", "read_content", "draft_write", "confirmed_write")
ALLOWED_SETUP_MODES = (
    "oauth_planned",
    "local_config",
    "manual_planned",
    "api_key_planned",
    "folder_planned",
    "script_planned",
)
ALLOWED_SOURCE_KINDS = ("user_defined", "atnos_suggested")
ALLOWED_CUSTOM_STATUSES = ("planned", "not_configured", "disabled")


@dataclass(frozen=True, slots=True)
class CustomIntegrationDraftDescriptor:
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


@dataclass(frozen=True, slots=True)
class CustomIntegrationDraftPreview:
    descriptor: CustomIntegrationDraftDescriptor
    manifest: str
    setup_plan: str
    warnings: tuple[str, ...]
    summary: str | None


class IntegrationBuilderService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.custom_integrations = CustomIntegrationRepository(session)

    def build_draft(
        self,
        *,
        label: str,
        category: str,
        summary: str | None,
        setup_mode: str,
        requested_capabilities: list[str] | tuple[str, ...] | None,
        source_kind: str,
    ) -> CustomIntegrationDraftPreview:
        normalized_label = self._normalize_label(label)
        normalized_category = self._normalize_category(category)
        normalized_summary = self._normalize_summary(summary)
        normalized_setup_mode = self._normalize_setup_mode(setup_mode)
        normalized_source_kind = self._normalize_source_kind(source_kind)
        capabilities = self._normalize_capabilities(requested_capabilities)
        key = self._build_unique_key(normalized_label)
        setup_requirements = self._setup_requirements_for_mode(normalized_setup_mode)
        missing_requirements = self._missing_requirements_for_mode(normalized_setup_mode)
        next_action_label = (
            "Review suggested integration"
            if normalized_source_kind == "atnos_suggested"
            else "Review custom integration"
        )
        notes = self._build_notes(
            summary=normalized_summary,
            source_kind=normalized_source_kind,
            setup_mode=normalized_setup_mode,
        )
        warnings = self._build_warnings(normalized_setup_mode, capabilities, normalized_source_kind)
        descriptor = CustomIntegrationDraftDescriptor(
            key=key,
            label=normalized_label,
            category=normalized_category,
            status="planned",
            capabilities=capabilities,
            permission_level="not_connected",
            is_available=False,
            is_write_capable=False,
            notes=notes,
            setup_mode=normalized_setup_mode,
            setup_requirements=setup_requirements,
            can_configure_in_ui=False,
            can_connect_account=False,
            missing_requirements=missing_requirements,
            next_action_label=next_action_label,
            origin="custom",
            source_kind=normalized_source_kind,
            has_adapter=False,
        )
        return CustomIntegrationDraftPreview(
            descriptor=descriptor,
            manifest=self._build_manifest(descriptor, normalized_summary),
            setup_plan=self._build_setup_plan(descriptor),
            warnings=warnings,
            summary=normalized_summary,
        )

    def create_custom_integration(
        self,
        *,
        label: str,
        category: str,
        summary: str | None,
        setup_mode: str,
        requested_capabilities: list[str] | tuple[str, ...] | None,
        source_kind: str,
    ):
        draft = self.build_draft(
            label=label,
            category=category,
            summary=summary,
            setup_mode=setup_mode,
            requested_capabilities=requested_capabilities,
            source_kind=source_kind,
        )
        return self.custom_integrations.create(
            key=draft.descriptor.key,
            label=draft.descriptor.label,
            category=draft.descriptor.category,
            status=draft.descriptor.status,
            summary=draft.summary,
            capabilities_json=list(draft.descriptor.capabilities),
            permission_level=draft.descriptor.permission_level,
            notes=draft.descriptor.notes,
            setup_mode=draft.descriptor.setup_mode,
            setup_requirements_json=list(draft.descriptor.setup_requirements),
            missing_requirements_json=list(draft.descriptor.missing_requirements),
            next_action_label=draft.descriptor.next_action_label,
            source_kind=draft.descriptor.source_kind,
            has_adapter=draft.descriptor.has_adapter,
        )

    def update_custom_integration(
        self,
        *,
        integration_id: int,
        label: str | None,
        category: str | None,
        summary: str | None,
        setup_mode: str | None,
        requested_capabilities: list[str] | tuple[str, ...] | None,
        source_kind: str | None,
        status: str | None,
    ):
        existing = self.custom_integrations.get_by_id(integration_id)
        if existing is None:
            return None

        resolved_label = label if label is not None else existing.label
        resolved_category = category if category is not None else existing.category
        resolved_summary = summary if summary is not None else existing.summary
        resolved_setup_mode = setup_mode if setup_mode is not None else existing.setup_mode
        resolved_capabilities = (
            requested_capabilities
            if requested_capabilities is not None
            else list(existing.capabilities_json or [])
        )
        resolved_source_kind = source_kind if source_kind is not None else existing.source_kind
        resolved_status = status if status is not None else existing.status
        if resolved_status not in ALLOWED_CUSTOM_STATUSES:
            raise ValueError(f"Unsupported custom integration status '{resolved_status}'.")

        draft = self.build_draft(
            label=resolved_label,
            category=resolved_category,
            summary=resolved_summary,
            setup_mode=resolved_setup_mode,
            requested_capabilities=resolved_capabilities,
            source_kind=resolved_source_kind,
        )

        existing.label = draft.descriptor.label
        existing.category = draft.descriptor.category
        existing.status = resolved_status
        existing.summary = draft.summary
        existing.capabilities_json = list(draft.descriptor.capabilities)
        existing.permission_level = "not_connected"
        existing.notes = draft.descriptor.notes
        existing.setup_mode = draft.descriptor.setup_mode
        existing.setup_requirements_json = list(draft.descriptor.setup_requirements)
        existing.missing_requirements_json = list(draft.descriptor.missing_requirements)
        existing.next_action_label = draft.descriptor.next_action_label
        existing.source_kind = draft.descriptor.source_kind
        existing.has_adapter = False
        return self.custom_integrations.save(existing)

    @staticmethod
    def _normalize_label(label: str) -> str:
        value = " ".join(label.split()).strip()
        if not value:
            raise ValueError("Integration label is required.")
        return value[:120]

    @staticmethod
    def _normalize_category(category: str) -> str:
        value = " ".join(category.split()).strip().lower()
        if not value:
            raise ValueError("Integration category is required.")
        normalized = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
        return normalized or "custom"

    @staticmethod
    def _normalize_summary(summary: str | None) -> str | None:
        if summary is None:
            return None
        value = " ".join(summary.split()).strip()
        return value[:400] if value else None

    @staticmethod
    def _normalize_setup_mode(setup_mode: str) -> str:
        value = setup_mode.strip().lower()
        if value not in ALLOWED_SETUP_MODES:
            raise ValueError(f"Unsupported setup_mode '{setup_mode}'.")
        return value

    @staticmethod
    def _normalize_source_kind(source_kind: str) -> str:
        value = source_kind.strip().lower()
        if value not in ALLOWED_SOURCE_KINDS:
            raise ValueError(f"Unsupported source_kind '{source_kind}'.")
        return value

    @staticmethod
    def _normalize_capabilities(
        requested_capabilities: list[str] | tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        items = requested_capabilities or ["read_metadata"]
        normalized = {
            capability.strip().lower()
            for capability in items
            if capability and capability.strip()
        }
        invalid = sorted(capability for capability in normalized if capability not in ALLOWED_CAPABILITIES)
        if invalid:
            raise ValueError(f"Unsupported capabilities: {', '.join(invalid)}.")
        ordered = tuple(capability for capability in ALLOWED_CAPABILITIES if capability in normalized)
        return ordered or ("read_metadata",)

    def _build_unique_key(self, label: str) -> str:
        ascii_name = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
        base_key = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "custom-app"
        key = base_key
        suffix = 2
        while key in BUILT_IN_INTEGRATION_KEYS or self.custom_integrations.get_by_key(key) is not None:
            key = f"{base_key}-{suffix}"
            suffix += 1
        return key

    @staticmethod
    def _setup_requirements_for_mode(setup_mode: str) -> tuple[str, ...]:
        mapping = {
            "oauth_planned": (
                "Secure account storage v0",
                "Read-only account connection design",
                "Permission gates",
            ),
            "local_config": ("Local config model", "Permission gates", "Adapter design"),
            "manual_planned": ("Adapter design", "Permission gates"),
            "api_key_planned": ("Secret storage v0", "Permission gates", "Adapter design"),
            "folder_planned": ("Path review", "Permission gates", "Adapter design"),
            "script_planned": ("Execution policy", "Permission gates", "Adapter design"),
        }
        return mapping[setup_mode]

    @staticmethod
    def _missing_requirements_for_mode(setup_mode: str) -> tuple[str, ...]:
        mapping = {
            "oauth_planned": ("account_connection", "secret_storage", "adapter"),
            "local_config": ("local_configuration", "adapter"),
            "manual_planned": ("adapter",),
            "api_key_planned": ("secret_storage", "adapter"),
            "folder_planned": ("path_review", "adapter"),
            "script_planned": ("execution_policy", "adapter"),
        }
        return mapping[setup_mode]

    @staticmethod
    def _build_notes(*, summary: str | None, source_kind: str, setup_mode: str) -> str:
        prefix = "Suggested by Atnos." if source_kind == "atnos_suggested" else "Added by user."
        summary_text = summary or "Custom integration descriptor only. No live adapter in v0."
        return f"{prefix} {summary_text} Setup mode: {setup_mode}. No live calls until an adapter exists."

    @staticmethod
    def _build_warnings(
        setup_mode: str,
        capabilities: tuple[str, ...],
        source_kind: str,
    ) -> tuple[str, ...]:
        warnings: list[str] = [
            "This draft only registers the app in Atnos. It does not create an adapter or connect an account.",
        ]
        if "confirmed_write" in capabilities:
            warnings.append("Confirmed write is planned only. No write action is available until an adapter and permission gate exist.")
        if setup_mode == "api_key_planned":
            warnings.append("API key setup is blocked in v0 because secret storage is not implemented.")
        if setup_mode == "oauth_planned":
            warnings.append("OAuth/account connection is planned only in v0.")
        if setup_mode == "script_planned":
            warnings.append("Local scripts remain planning-only in v0. No execution path is created.")
        if source_kind == "atnos_suggested":
            warnings.append("Atnos suggestion still requires explicit user approval before the app is added to the registry.")
        return tuple(warnings)

    @staticmethod
    def _build_manifest(
        descriptor: CustomIntegrationDraftDescriptor,
        summary: str | None,
    ) -> str:
        return "\n".join(
            [
                "# Integration Manifest Draft",
                "",
                f"- key: {descriptor.key}",
                f"- label: {descriptor.label}",
                f"- origin: {descriptor.origin}",
                f"- source_kind: {descriptor.source_kind}",
                f"- category: {descriptor.category}",
                f"- status: {descriptor.status}",
                f"- setup_mode: {descriptor.setup_mode}",
                f"- permission_level: {descriptor.permission_level}",
                f"- has_adapter: {'yes' if descriptor.has_adapter else 'no'}",
                f"- capabilities: {', '.join(descriptor.capabilities)}",
                f"- summary: {summary or 'Brak'}",
                "",
                "## Current boundary",
                "",
                "This registry entry is planning-only in v0.",
                "Atnos can reference it in plans and prompts, but cannot perform live calls, OAuth, secret storage, or write actions for it.",
            ]
        )

    @staticmethod
    def _build_setup_plan(descriptor: CustomIntegrationDraftDescriptor) -> str:
        requirements = "\n".join(f"- {item}" for item in descriptor.setup_requirements)
        missing = "\n".join(f"- {item}" for item in descriptor.missing_requirements)
        return "\n".join(
            [
                "# Setup Plan Draft",
                "",
                f"1. Keep `{descriptor.label}` in `{descriptor.status}` state until a real adapter exists.",
                "2. Review the planned capability scope and confirm that no live action is needed in v0.",
                f"3. Prepare the next foundation slice for `{descriptor.setup_mode}`.",
                "4. Add permission gates before any runtime connection or write path.",
                "",
                "## Setup requirements",
                requirements,
                "",
                "## Missing requirements",
                missing,
            ]
        )
