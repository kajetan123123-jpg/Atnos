from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.account_credential import AccountCredential
from atnos.db.models.project import Project
from atnos.db.models.project_app_link import ProjectAppLink
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.creator_project_repository import CreatorProjectRepository
from atnos.repositories.custom_integration_repository import CustomIntegrationRepository
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.repositories.project_app_link_repository import ProjectAppLinkRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.integrations_registry import IntegrationsRegistryService
from atnos.services.oauth_connection_runtime_service import OAuthConnectionRuntimeService

ALLOWED_APP_LINK_STATUSES: Final = {"planned", "configured", "disabled", "needs_review"}
ALLOWED_RESOURCE_TYPES: Final = {"account", "channel", "mailbox", "broker_account", "workspace", "profile"}
ALLOWED_ENVIRONMENTS: Final = {"demo", "test", "production"}
ALLOWED_CONFIRMATION_POLICIES: Final = {"none", "supervised", "always_confirm"}
ALLOWED_DELEGATION_POLICIES: Final = {"disabled", "project_only", "approved_workers"}
ALLOWED_PERMISSION_PROFILES: Final = {
    "metadata_only",
    "read_only",
    "draft_write",
    "supervised_write_later",
    "publish_preview_later",
    "financial_read_later",
    "trading_preview_later",
}
ALLOWED_APP_LINK_CAPABILITIES: Final = {
    "read_metadata",
    "read_content",
    "draft_write",
    "confirmed_write_later",
    "browser_login_later",
    "publish_later",
    "financial_read_later",
    "trading_later",
}
SECRET_LIKE_FIELDS: Final = {
    "secret",
    "secret_ref",
    "secret_value",
    "password",
    "token",
    "api_key",
    "authorization",
    "credential",
    "credentials",
}
PROFILE_REQUIRED_CAPABILITIES: Final[dict[str, str]] = {
    "publish_preview_later": "publish_later",
    "financial_read_later": "financial_read_later",
    "trading_preview_later": "trading_later",
}
DEFAULT_ROLE_FIELDS: Final[dict[str, str]] = {
    "project": "is_default_for_project",
    "content": "is_default_for_content",
    "analytics": "is_default_for_analytics",
    "storage": "is_default_for_storage",
    "publish_preview": "is_default_for_publish_preview",
}
DEFAULT_ROLE_REQUIRED_CAPABILITIES: Final[dict[str, set[str]]] = {
    "content": {"read_content", "draft_write", "publish_later"},
    "analytics": {"read_metadata", "read_content", "financial_read_later"},
    "storage": {"draft_write"},
    "publish_preview": {"publish_later"},
}
# These integrations have a narrow business context. Keeping the boundary here
# prevents a new project from accidentally wiring a YouTube identity into an
# investment workspace, or a broker credential into a content workspace.
MARKET_ONLY_INTEGRATIONS: Final[frozenset[str]] = frozenset(
    {
        "xtb",
        "broker-generic",
        "market-data-generic",
        "tradingview",
    }
)
CONTENT_ONLY_INTEGRATIONS: Final[frozenset[str]] = frozenset(
    {
        "youtube",
        "instagram",
        "tiktok",
        "onlyfans",
        "creator-platform-generic",
        "video-editor",
        "image-generator",
        "kokoro-local",
        "pulid-identity",
        "ltx-video",
        "wan-video",
        "wav2lip",
    }
)
_UNSET = object()


@dataclass(frozen=True, slots=True)
class ProjectAppLinkView:
    id: int
    project_id: int
    integration_key: str
    custom_integration_id: int | None
    account_id: int | None
    resource_type: str
    resource_external_id: str | None
    resource_label: str | None
    binding_role: str
    environment: str
    confirmation_policy: str
    delegation_policy: str
    allow_shared_account: bool
    permission_scopes: tuple[str, ...]
    connection_status: str
    connection_detail: str | None
    last_verified_at: str | None
    verification_evidence: dict[str, object]
    label: str
    purpose: str | None
    status: str
    permission_profile: str
    allowed_capabilities: tuple[str, ...]
    safety_notes: str | None
    is_default_for_project: bool
    is_default_for_content: bool
    is_default_for_analytics: bool
    is_default_for_storage: bool
    is_default_for_publish_preview: bool
    notes: str | None
    integration_label: str
    account_summary: dict[str, object] | None
    created_at: str
    updated_at: str


class ProjectAppLinksService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.projects = ProjectRepository(session)
        self.links = ProjectAppLinkRepository(session)
        self.accounts = AccountCredentialRepository(session)
        self.custom_integrations = CustomIntegrationRepository(session)
        self.creator_projects = CreatorProjectRepository(session)
        self.integrations = IntegrationsRegistryService(session, settings)
        self.oauth_connections = OAuthConnectionRepository(session)
        self.oauth_runtime = OAuthConnectionRuntimeService(session, settings)

    def list_links(self, project_id: int) -> list[ProjectAppLinkView] | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        return [self._build_view(item) for item in self.links.list_for_project(project_id)]

    def get_link(self, project_id: int, link_id: int) -> ProjectAppLinkView | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        item = self.links.get_for_project(project_id, link_id)
        if item is None:
            return None
        return self._build_view(item)

    def create_link(
        self,
        project_id: int,
        *,
        integration_key: str,
        custom_integration_id: int | None,
        account_id: int | None,
        label: str,
        purpose: str | None,
        status: str = "configured",
        permission_profile: str,
        allowed_capabilities: list[str],
        safety_notes: str | None,
        is_default_for_project: bool = False,
        is_default_for_content: bool = False,
        is_default_for_analytics: bool = False,
        is_default_for_storage: bool = False,
        is_default_for_publish_preview: bool = False,
        notes: str | None = None,
        resource_type: str | None = None,
        resource_external_id: str | None = None,
        resource_label: str | None = None,
        binding_role: str = "primary",
        environment: str = "production",
        confirmation_policy: str = "supervised",
        delegation_policy: str = "project_only",
        allow_shared_account: bool = False,
        permission_scopes: list[str] | None = None,
    ) -> ProjectAppLinkView:
        project = self._require_project(project_id)
        normalized = self._normalize_payload(
            project=project,
            integration_key=integration_key,
            custom_integration_id=custom_integration_id,
            account_id=account_id,
            label=label,
            purpose=purpose,
            status=status,
            permission_profile=permission_profile,
            allowed_capabilities=allowed_capabilities,
            safety_notes=safety_notes,
            is_default_for_project=is_default_for_project,
            is_default_for_content=is_default_for_content,
            is_default_for_analytics=is_default_for_analytics,
            is_default_for_storage=is_default_for_storage,
            is_default_for_publish_preview=is_default_for_publish_preview,
            notes=notes,
            resource_type=resource_type,
            resource_external_id=resource_external_id,
            resource_label=resource_label,
            binding_role=binding_role,
            environment=environment,
            confirmation_policy=confirmation_policy,
            delegation_policy=delegation_policy,
            allow_shared_account=allow_shared_account,
            permission_scopes=permission_scopes or [],
        )
        self._validate_account_sharing(
            project_id=project_id,
            account_id=normalized["account_id"],
            allow_shared_account=bool(normalized["allow_shared_account"]),
        )
        item = self.links.create(project_id=project_id, **normalized)
        self._apply_default_arbitration(project_id, item)
        return self._build_view(item)

    def update_link(
        self,
        project_id: int,
        link_id: int,
        *,
        account_id: int | None | object = _UNSET,
        label: str | None | object = _UNSET,
        purpose: str | None | object = _UNSET,
        status: str | None | object = _UNSET,
        permission_profile: str | None | object = _UNSET,
        allowed_capabilities: list[str] | None | object = _UNSET,
        safety_notes: str | None | object = _UNSET,
        is_default_for_project: bool | object = _UNSET,
        is_default_for_content: bool | object = _UNSET,
        is_default_for_analytics: bool | object = _UNSET,
        is_default_for_storage: bool | object = _UNSET,
        is_default_for_publish_preview: bool | object = _UNSET,
        notes: str | None | object = _UNSET,
        resource_type: str | None | object = _UNSET,
        resource_external_id: str | None | object = _UNSET,
        resource_label: str | None | object = _UNSET,
        binding_role: str | object = _UNSET,
        environment: str | object = _UNSET,
        confirmation_policy: str | object = _UNSET,
        delegation_policy: str | object = _UNSET,
        allow_shared_account: bool | object = _UNSET,
        permission_scopes: list[str] | object = _UNSET,
    ) -> ProjectAppLinkView | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        item = self.links.get_for_project(project_id, link_id)
        if item is None:
            return None
        normalized = self._normalize_payload(
            project=project,
            integration_key=item.integration_key,
            custom_integration_id=item.custom_integration_id,
            account_id=item.account_id if account_id is _UNSET else account_id,
            label=item.label if label is _UNSET else label,
            purpose=item.purpose if purpose is _UNSET else purpose,
            status=item.status if status is _UNSET else status,
            permission_profile=item.permission_profile if permission_profile is _UNSET else permission_profile,
            allowed_capabilities=list(item.allowed_capabilities_json or [])
            if allowed_capabilities is _UNSET
            else (allowed_capabilities or []),
            safety_notes=item.safety_notes if safety_notes is _UNSET else safety_notes,
            is_default_for_project=item.is_default_for_project
            if is_default_for_project is _UNSET
            else is_default_for_project,
            is_default_for_content=item.is_default_for_content
            if is_default_for_content is _UNSET
            else is_default_for_content,
            is_default_for_analytics=item.is_default_for_analytics
            if is_default_for_analytics is _UNSET
            else is_default_for_analytics,
            is_default_for_storage=item.is_default_for_storage
            if is_default_for_storage is _UNSET
            else is_default_for_storage,
            is_default_for_publish_preview=item.is_default_for_publish_preview
            if is_default_for_publish_preview is _UNSET
            else is_default_for_publish_preview,
            notes=item.notes if notes is _UNSET else notes,
            resource_type=item.resource_type if resource_type is _UNSET else resource_type,
            resource_external_id=item.resource_external_id if resource_external_id is _UNSET else resource_external_id,
            resource_label=item.resource_label if resource_label is _UNSET else resource_label,
            binding_role=item.binding_role if binding_role is _UNSET else binding_role,
            environment=item.environment if environment is _UNSET else environment,
            confirmation_policy=item.confirmation_policy if confirmation_policy is _UNSET else confirmation_policy,
            delegation_policy=item.delegation_policy if delegation_policy is _UNSET else delegation_policy,
            allow_shared_account=item.allow_shared_account if allow_shared_account is _UNSET else allow_shared_account,
            permission_scopes=list(item.permission_scopes_json or []) if permission_scopes is _UNSET else permission_scopes,
        )
        self._validate_account_sharing(
            project_id=project_id,
            account_id=normalized["account_id"],
            allow_shared_account=bool(normalized["allow_shared_account"]),
            current_link_id=item.id,
        )
        item.account_id = normalized["account_id"]
        item.label = normalized["label"]
        item.purpose = normalized["purpose"]
        item.status = normalized["status"]
        item.permission_profile = normalized["permission_profile"]
        item.allowed_capabilities_json = normalized["allowed_capabilities_json"]
        item.safety_notes = normalized["safety_notes"]
        item.is_default_for_project = normalized["is_default_for_project"]
        item.is_default_for_content = normalized["is_default_for_content"]
        item.is_default_for_analytics = normalized["is_default_for_analytics"]
        item.is_default_for_storage = normalized["is_default_for_storage"]
        item.is_default_for_publish_preview = normalized["is_default_for_publish_preview"]
        item.notes = normalized["notes"]
        item.resource_type = normalized["resource_type"]
        item.resource_external_id = normalized["resource_external_id"]
        item.resource_label = normalized["resource_label"]
        item.binding_role = normalized["binding_role"]
        item.environment = normalized["environment"]
        item.confirmation_policy = normalized["confirmation_policy"]
        item.delegation_policy = normalized["delegation_policy"]
        item.allow_shared_account = normalized["allow_shared_account"]
        item.permission_scopes_json = normalized["permission_scopes_json"]
        item.connection_status = "unverified"
        item.connection_detail = "Binding changed; run live verification again."
        item.last_verified_at = None
        item.verification_evidence_json = {}
        self.links.save(item)
        self._apply_default_arbitration(project_id, item)
        return self._build_view(item)

    def disable_link(self, project_id: int, link_id: int) -> ProjectAppLinkView | None:
        item = self.links.get_for_project(project_id, link_id)
        if item is None:
            return None
        item.status = "disabled"
        item.is_default_for_project = False
        item.is_default_for_content = False
        item.is_default_for_analytics = False
        item.is_default_for_storage = False
        item.is_default_for_publish_preview = False
        item.connection_status = "disabled"
        item.connection_detail = "Binding is disabled."
        self.links.save(item)
        return self._build_view(item)

    def verify_link(self, project_id: int, link_id: int) -> ProjectAppLinkView | None:
        """Weryfikuje referencję połączenia bez ujawniania tokenu lub sekretu."""
        item = self.links.get_for_project(project_id, link_id)
        if item is None:
            return None
        checked_at = datetime.now(UTC)
        account = self.accounts.get_by_id(item.account_id) if item.account_id is not None else None
        evidence: dict[str, object] = {
            "checked_at": checked_at.isoformat(),
            "integration_key": item.integration_key,
            "resource_external_id": item.resource_external_id,
            "secret_exposed": False,
        }
        if item.status == "disabled":
            status = "disabled"
            detail = "Binding is disabled."
        elif account is None:
            status = "missing_account"
            detail = "Binding has no platform account."
        else:
            connector_key = self._oauth_connector_key(item.integration_key)
            connection = (
                self.oauth_connections.get_latest_for_connector_account(connector_key, account.id)
                if connector_key is not None
                else None
            )
            if connector_key is not None and connection is None:
                status = "not_connected"
                detail = f"No OAuth connection for {connector_key} and account {account.id}."
            elif connection is not None:
                health = self.oauth_runtime.health_check(connection.id)
                evidence.update(
                    {
                        "connector_key": connector_key,
                        "oauth_connection_id": connection.id,
                        "token_reference_present": bool(connection.token_ref),
                        "granted_scopes": list(connection.scopes_json or []),
                    }
                )
                status = "connected" if health.ok else health.status
                detail = health.message
                if health.ok:
                    item.permission_scopes_json = list(connection.scopes_json or [])
            elif account.secret_ref and account.storage_backend != "metadata_only":
                status = "credential_reference_present"
                detail = "Credential reference exists; this provider has no live verifier in Atnos yet."
                evidence["credential_reference_present"] = True
            else:
                status = "verification_unavailable"
                detail = "Provider has no live verifier or usable credential reference."
                evidence["credential_reference_present"] = False
        item.connection_status = status
        item.connection_detail = detail
        item.last_verified_at = checked_at
        item.verification_evidence_json = evidence
        self.links.save(item)
        return self._build_view(item)

    def summarize_links(self, project_id: int | None) -> tuple[dict[str, object], ...]:
        if project_id is None:
            return ()
        project = self.projects.get(project_id)
        if project is None:
            return ()
        return tuple(self._build_summary(item) for item in self.links.list_for_project(project_id))

    def summarize_defaults(self, project_id: int | None) -> dict[str, object]:
        if project_id is None:
            return self._empty_defaults_summary()
        project = self.projects.get(project_id)
        if project is None:
            return self._empty_defaults_summary()

        items = self.links.list_for_project(project_id)
        return {
            "linked_app_count": len(items),
            "project": self._build_default_role_summary(items, "project"),
            "content": self._build_default_role_summary(items, "content"),
            "analytics": self._build_default_role_summary(items, "analytics"),
            "storage": self._build_default_role_summary(items, "storage"),
            "publish_preview": self._build_default_role_summary(items, "publish_preview"),
        }

    def _normalize_payload(
        self,
        *,
        project: Project,
        integration_key: str,
        custom_integration_id: int | None,
        account_id: int | None,
        label: str | None,
        purpose: str | None,
        status: str | None,
        permission_profile: str | None,
        allowed_capabilities: list[str],
        safety_notes: str | None,
        is_default_for_project: bool,
        is_default_for_content: bool,
        is_default_for_analytics: bool,
        is_default_for_storage: bool,
        is_default_for_publish_preview: bool,
        notes: str | None,
        resource_type: str | None,
        resource_external_id: str | None,
        resource_label: str | None,
        binding_role: str,
        environment: str,
        confirmation_policy: str,
        delegation_policy: str,
        allow_shared_account: bool,
        permission_scopes: list[str],
    ) -> dict[str, object]:
        creator_project = self.creator_projects.get_project_by_canonical_project_id(project.id)
        project_type = creator_project.project_type if creator_project is not None else None

        normalized_key, integration_label = self._resolve_integration(integration_key, custom_integration_id)
        self._validate_project_type_integration(project_type, normalized_key)
        normalized_status = self._normalize_enum(status or "configured", ALLOWED_APP_LINK_STATUSES, "status")
        normalized_profile = self._normalize_enum(
            permission_profile or "metadata_only",
            ALLOWED_PERMISSION_PROFILES,
            "permission_profile",
        )
        normalized_capabilities = self._normalize_capabilities(allowed_capabilities)
        self._validate_profile_capability_match(normalized_profile, normalized_capabilities)
        self._validate_project_type_defaults(
            project_type=project_type,
            is_default_for_publish_preview=is_default_for_publish_preview,
            allowed_capabilities=normalized_capabilities,
        )
        normalized_defaults = self._normalize_default_flags(
            status=normalized_status,
            allowed_capabilities=normalized_capabilities,
            is_default_for_project=is_default_for_project,
            is_default_for_content=is_default_for_content,
            is_default_for_analytics=is_default_for_analytics,
            is_default_for_storage=is_default_for_storage,
            is_default_for_publish_preview=is_default_for_publish_preview,
        )
        account = self._resolve_account(account_id)
        if account is not None:
            self._validate_account_integration_alignment(account, normalized_key)
            self._validate_account_capabilities(account, normalized_capabilities)
        normalized_resource_type = self._normalize_enum(
            resource_type or self._default_resource_type(normalized_key),
            ALLOWED_RESOURCE_TYPES,
            "resource_type",
        )
        normalized_resource_external_id = self._normalize_optional_identifier(
            resource_external_id or (account.account_identifier if account is not None else None),
            "resource_external_id",
            max_length=320,
        )
        normalized_resource_label = self._normalize_optional_identifier(
            resource_label or (account.label if account is not None else None),
            "resource_label",
            max_length=160,
        )
        normalized_environment = self._normalize_enum(environment, ALLOWED_ENVIRONMENTS, "environment")
        normalized_confirmation_policy = self._normalize_enum(
            confirmation_policy,
            ALLOWED_CONFIRMATION_POLICIES,
            "confirmation_policy",
        )
        normalized_delegation_policy = self._normalize_enum(
            delegation_policy,
            ALLOWED_DELEGATION_POLICIES,
            "delegation_policy",
        )
        normalized_scopes = self._normalize_scopes(permission_scopes)
        self._reject_secret_like_texts(
            {
                "label": label,
                "purpose": purpose,
                "safety_notes": safety_notes,
                "notes": notes,
            }
        )
        normalized_label = self._normalize_required_text(label or integration_label, "label", max_length=160)
        return {
            "integration_key": normalized_key,
            "custom_integration_id": custom_integration_id,
            "account_id": account.id if account is not None else None,
            "resource_type": normalized_resource_type,
            "resource_external_id": normalized_resource_external_id,
            "resource_label": normalized_resource_label,
            "binding_role": self._normalize_required_text(binding_role, "binding_role", max_length=64).lower(),
            "environment": normalized_environment,
            "confirmation_policy": normalized_confirmation_policy,
            "delegation_policy": normalized_delegation_policy,
            "allow_shared_account": bool(allow_shared_account),
            "permission_scopes_json": normalized_scopes,
            "connection_status": "unverified",
            "connection_detail": "Run live verification for this binding.",
            "last_verified_at": None,
            "verification_evidence_json": {},
            "label": normalized_label,
            "purpose": self._normalize_optional_text(purpose),
            "status": normalized_status,
            "permission_profile": normalized_profile,
            "allowed_capabilities_json": normalized_capabilities,
            "safety_notes": self._normalize_optional_text(safety_notes),
            "is_default_for_project": normalized_defaults["is_default_for_project"],
            "is_default_for_content": normalized_defaults["is_default_for_content"],
            "is_default_for_analytics": normalized_defaults["is_default_for_analytics"],
            "is_default_for_storage": normalized_defaults["is_default_for_storage"],
            "is_default_for_publish_preview": normalized_defaults["is_default_for_publish_preview"],
            "notes": self._normalize_optional_text(notes),
        }

    def _build_view(self, item: ProjectAppLink) -> ProjectAppLinkView:
        integration_label = self._integration_label(item.integration_key, item.custom_integration_id)
        account = self.accounts.get_by_id(item.account_id) if item.account_id is not None else None
        return ProjectAppLinkView(
            id=item.id,
            project_id=item.project_id,
            integration_key=item.integration_key,
            custom_integration_id=item.custom_integration_id,
            account_id=item.account_id,
            resource_type=item.resource_type,
            resource_external_id=item.resource_external_id,
            resource_label=item.resource_label,
            binding_role=item.binding_role,
            environment=item.environment,
            confirmation_policy=item.confirmation_policy,
            delegation_policy=item.delegation_policy,
            allow_shared_account=item.allow_shared_account,
            permission_scopes=tuple(item.permission_scopes_json or []),
            connection_status=item.connection_status,
            connection_detail=item.connection_detail,
            last_verified_at=item.last_verified_at.isoformat() if item.last_verified_at is not None else None,
            verification_evidence=dict(item.verification_evidence_json or {}),
            label=item.label,
            purpose=item.purpose,
            status=item.status,
            permission_profile=item.permission_profile,
            allowed_capabilities=tuple(item.allowed_capabilities_json or []),
            safety_notes=item.safety_notes,
            is_default_for_project=item.is_default_for_project,
            is_default_for_content=item.is_default_for_content,
            is_default_for_analytics=item.is_default_for_analytics,
            is_default_for_storage=item.is_default_for_storage,
            is_default_for_publish_preview=item.is_default_for_publish_preview,
            notes=item.notes,
            integration_label=integration_label,
            account_summary=self._build_account_summary(account),
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
        )

    def _build_summary(self, item: ProjectAppLink) -> dict[str, object]:
        view = self._build_view(item)
        return {
            "id": view.id,
            "integration_key": view.integration_key,
            "integration_label": view.integration_label,
            "account_id": view.account_id,
            "account_label": view.account_summary["label"] if view.account_summary is not None else None,
            "resource_type": view.resource_type,
            "resource_external_id": view.resource_external_id,
            "resource_label": view.resource_label,
            "environment": view.environment,
            "connection_status": view.connection_status,
            "permission_profile": view.permission_profile,
            "status": view.status,
            "purpose": view.purpose,
            "allowed_capabilities": list(view.allowed_capabilities),
            "is_default_for_project": view.is_default_for_project,
            "is_default_for_content": view.is_default_for_content,
            "is_default_for_analytics": view.is_default_for_analytics,
            "is_default_for_storage": view.is_default_for_storage,
            "is_default_for_publish_preview": view.is_default_for_publish_preview,
        }

    def _apply_default_arbitration(self, project_id: int, item: ProjectAppLink) -> None:
        siblings = self.links.list_for_project(project_id)
        for role, field_name in DEFAULT_ROLE_FIELDS.items():
            if not getattr(item, field_name):
                continue
            for sibling in siblings:
                if sibling.id == item.id:
                    continue
                if getattr(sibling, field_name):
                    setattr(sibling, field_name, False)
                    self.links.save(sibling)
        self.links.save(item)

    def _normalize_default_flags(
        self,
        *,
        status: str,
        allowed_capabilities: list[str],
        is_default_for_project: bool,
        is_default_for_content: bool,
        is_default_for_analytics: bool,
        is_default_for_storage: bool,
        is_default_for_publish_preview: bool,
    ) -> dict[str, bool]:
        if status == "disabled":
            if any(
                (
                    is_default_for_project,
                    is_default_for_content,
                    is_default_for_analytics,
                    is_default_for_storage,
                    is_default_for_publish_preview,
                )
            ):
                raise ValueError("Disabled app links cannot be default.")
            return {
                "is_default_for_project": False,
                "is_default_for_content": False,
                "is_default_for_analytics": False,
                "is_default_for_storage": False,
                "is_default_for_publish_preview": False,
            }

        normalized = {
            "is_default_for_project": bool(is_default_for_project),
            "is_default_for_content": bool(is_default_for_content),
            "is_default_for_analytics": bool(is_default_for_analytics),
            "is_default_for_storage": bool(is_default_for_storage),
            "is_default_for_publish_preview": bool(is_default_for_publish_preview),
        }

        for role, field_name in DEFAULT_ROLE_FIELDS.items():
            if not normalized[field_name]:
                continue
            required_capabilities = DEFAULT_ROLE_REQUIRED_CAPABILITIES.get(role)
            if required_capabilities and required_capabilities.isdisjoint(allowed_capabilities):
                required_text = ", ".join(sorted(required_capabilities))
                raise ValueError(
                    f"Default role '{role}' requires one of: {required_text}."
                )

        return normalized

    def _build_default_role_summary(
        self,
        items: list[ProjectAppLink],
        role: str,
    ) -> dict[str, object] | None:
        field_name = DEFAULT_ROLE_FIELDS[role]
        item = next((entry for entry in items if getattr(entry, field_name)), None)
        if item is None:
            return None
        view = self._build_view(item)
        return {
            "id": view.id,
            "label": view.label,
            "integration_key": view.integration_key,
            "integration_label": view.integration_label,
            "account_id": view.account_id,
            "account_label": view.account_summary["label"] if view.account_summary is not None else None,
            "status": view.status,
            "permission_profile": view.permission_profile,
            "allowed_capabilities": list(view.allowed_capabilities),
        }

    @staticmethod
    def _empty_defaults_summary() -> dict[str, object]:
        return {
            "linked_app_count": 0,
            "project": None,
            "content": None,
            "analytics": None,
            "storage": None,
            "publish_preview": None,
        }

    def _resolve_integration(self, integration_key: str, custom_integration_id: int | None) -> tuple[str, str]:
        normalized = self._normalize_required_text(integration_key, "integration_key", max_length=120).lower()
        descriptors = {item.key: item for item in self.integrations.list_integrations()}
        descriptor = descriptors.get(normalized)
        if descriptor is None:
            raise ValueError(f"Unknown integration '{normalized}'.")
        if custom_integration_id is not None:
            custom = self.custom_integrations.get_by_id(custom_integration_id)
            if custom is None:
                raise ValueError(f"Unknown custom_integration_id '{custom_integration_id}'.")
            if custom.key != normalized:
                raise ValueError("custom_integration_id does not match integration_key.")
        return normalized, descriptor.label

    def _integration_label(self, integration_key: str, custom_integration_id: int | None) -> str:
        if custom_integration_id is not None:
            custom = self.custom_integrations.get_by_id(custom_integration_id)
            if custom is not None:
                return custom.label
        for descriptor in self.integrations.list_integrations():
            if descriptor.key == integration_key:
                return descriptor.label
        return integration_key

    def _resolve_account(self, account_id: int | None) -> AccountCredential | None:
        if account_id is None:
            return None
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise ValueError(f"Unknown account_id '{account_id}'.")
        return account

    def _validate_account_sharing(
        self,
        *,
        project_id: int,
        account_id: object,
        allow_shared_account: bool,
        current_link_id: int | None = None,
    ) -> None:
        if not isinstance(account_id, int):
            return
        other_projects = {
            link.project_id
            for link in self.links.list_for_account(account_id)
            if link.id != current_link_id and link.status != "disabled" and link.project_id != project_id
        }
        if other_projects and not allow_shared_account:
            project_list = ", ".join(str(item) for item in sorted(other_projects))
            raise ValueError(
                "This platform account is already active in project(s) "
                f"{project_list}; set allow_shared_account=true explicitly to reuse it."
            )

    @staticmethod
    def _default_resource_type(integration_key: str) -> str:
        return {
            "youtube": "channel",
            "gmail": "mailbox",
            "xtb": "broker_account",
            "broker-generic": "broker_account",
            "market-data-generic": "broker_account",
        }.get(integration_key, "account")

    @staticmethod
    def _oauth_connector_key(integration_key: str) -> str | None:
        return {
            "youtube": "youtube-studio",
            "gmail": "gmail",
            "github": "github",
            "google-calendar": "google-calendar",
        }.get(integration_key)

    @staticmethod
    def _build_account_summary(account: AccountCredential | None) -> dict[str, object] | None:
        if account is None:
            return None
        return {
            "id": account.id,
            "label": account.label,
            "provider_key": account.provider_key,
            "account_identifier": account.account_identifier,
            "status": account.status,
            "risk_level": account.risk_level,
            "linked_integration_key": account.linked_integration_key,
        }

    @staticmethod
    def _normalize_required_text(value: str, field_name: str, *, max_length: int) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} exceeds {max_length} characters.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @staticmethod
    def _normalize_optional_identifier(value: str | None, field_name: str, *, max_length: int) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} exceeds {max_length} characters.")
        return normalized

    @staticmethod
    def _normalize_scopes(values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = " ".join(value.split())
            if not item:
                continue
            if len(item) > 500:
                raise ValueError("permission scope exceeds 500 characters.")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @staticmethod
    def _normalize_enum(value: str, allowed: set[str], field_name: str) -> str:
        normalized = value.strip().lower()
        if normalized not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            raise ValueError(f"{field_name} must be one of: {allowed_text}.")
        return normalized

    @staticmethod
    def _normalize_capabilities(values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            normalized_value = value.strip().lower()
            if normalized_value not in ALLOWED_APP_LINK_CAPABILITIES:
                allowed_text = ", ".join(sorted(ALLOWED_APP_LINK_CAPABILITIES))
                raise ValueError(f"allowed_capabilities must use only: {allowed_text}.")
            if normalized_value not in normalized:
                normalized.append(normalized_value)
        return normalized

    @staticmethod
    def _validate_profile_capability_match(permission_profile: str, capabilities: list[str]) -> None:
        required_capability = PROFILE_REQUIRED_CAPABILITIES.get(permission_profile)
        if required_capability is not None and required_capability not in capabilities:
            raise ValueError(
                f"permission_profile '{permission_profile}' requires capability '{required_capability}'."
            )

    @staticmethod
    def _validate_account_capabilities(account: AccountCredential, capabilities: list[str]) -> None:
        account_capabilities = set(account.allowed_capabilities_json or [])
        missing = [capability for capability in capabilities if capability not in account_capabilities]
        if missing:
            raise ValueError(
                f"Linked account {account.id} does not support requested capabilities: {', '.join(missing)}."
            )

    @staticmethod
    def _validate_account_integration_alignment(account: AccountCredential, integration_key: str) -> None:
        """Reject an explicit account-to-integration mismatch before it reaches a workflow."""
        linked_key = (account.linked_integration_key or "").strip().lower()
        if linked_key and linked_key != integration_key:
            raise ValueError(
                f"Linked account {account.id} belongs to integration '{linked_key}', not '{integration_key}'."
            )

    @staticmethod
    def _validate_project_type_integration(project_type: str | None, integration_key: str) -> None:
        if project_type == "market_lab" and integration_key in CONTENT_ONLY_INTEGRATIONS:
            raise ValueError(
                f"Market Lab projects cannot link content integration '{integration_key}'."
            )
        if project_type is not None and project_type != "market_lab" and integration_key in MARKET_ONLY_INTEGRATIONS:
            raise ValueError(
                f"Integration '{integration_key}' can be linked only to Market Lab projects."
            )

    @staticmethod
    def _validate_project_type_defaults(
        *,
        project_type: str | None,
        is_default_for_publish_preview: bool,
        allowed_capabilities: list[str],
    ) -> None:
        if project_type == "market_lab" and is_default_for_publish_preview:
            raise ValueError("Market Lab projects cannot use publish preview defaults.")
        if project_type != "market_lab" and "trading_later" in allowed_capabilities:
            raise ValueError("Trading-capable app links are allowed only for market_lab projects.")

    @staticmethod
    def _reject_secret_like_texts(values: dict[str, str | None]) -> None:
        for field_name, value in values.items():
            if value is None:
                continue
            lowered = value.lower()
            if any(marker in lowered for marker in SECRET_LIKE_FIELDS):
                raise ValueError(f"{field_name} cannot contain secret-like fields or values.")

    def _require_project(self, project_id: int) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project_id '{project_id}'.")
        return project
