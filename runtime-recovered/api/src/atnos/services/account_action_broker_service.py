from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.account_action_draft_repository import AccountActionDraftRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.account_credentials_service import AccountCredentialView, AccountCredentialsService
from atnos.services.integrations_registry import IntegrationsRegistryService

ALLOWED_ACTION_TYPES: Final[dict[str, tuple[str, ...]]] = {
    "read_metadata_preview": ("read_metadata",),
    "analytics_read_preview": ("read_metadata", "read_content"),
    "export_preview": ("draft_write",),
    "content_upload_preview": ("publish_later",),
    "publish_preview": ("publish_later",),
    "message_preview": ("confirmed_write_later",),
    "calendar_write_preview": ("connector_write", "confirmed_write_later"),
    "github_write_preview": ("connector_write", "confirmed_write_later"),
    "code_apply_preview": ("connector_write",),
    "account_onboarding_preview": ("trusted_login", "browser_workflow"),
    "market_data_read_preview": ("financial_read_later",),
    "broker_read_preview": ("financial_read_later",),
    "trade_preview_later": ("trading_later",),
}

ACTION_RISK_LEVELS: Final[dict[str, str]] = {
    "read_metadata_preview": "low",
    "analytics_read_preview": "medium",
    "export_preview": "medium",
    "content_upload_preview": "high",
    "publish_preview": "high",
    "message_preview": "high",
    "calendar_write_preview": "high",
    "github_write_preview": "high",
    "code_apply_preview": "high",
    "account_onboarding_preview": "high",
    "market_data_read_preview": "high",
    "broker_read_preview": "critical",
    "trade_preview_later": "critical",
}

ALLOWED_DRAFT_STATUSES: Final[set[str]] = {
    "draft",
    "awaiting_approval",
    "approved",
    "rejected",
    "cancelled",
    "executed_later",
}

PUBLIC_MUTABLE_STATUSES: Final[set[str]] = {
    "draft",
    "awaiting_approval",
    "approved",
    "rejected",
    "cancelled",
}


class AccountActionDraftNotFoundError(Exception):
    pass


class AccountActionDraftTransitionError(ValueError):
    pass


class AccountActionDraftCapabilityError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class AccountActionDraftView:
    id: int
    account_id: int
    integration_key: str
    project_id: int | None
    project_name: str | None
    action_type: str
    action_title: str
    action_summary: str
    risk_level: str
    status: str
    requires_approval: bool
    preview_payload: dict[str, object]
    approval_notes: str | None
    rejection_reason: str | None
    audit_log: tuple[dict[str, object], ...]
    created_by: str
    approved_at: datetime | None
    rejected_at: datetime | None
    executed_at: datetime | None
    required_capabilities: tuple[str, ...]
    account_summary: AccountCredentialView
    created_at: datetime
    updated_at: datetime


class AccountActionBrokerService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = AccountActionDraftRepository(session)
        self.accounts = AccountCredentialsService(session, settings)
        self.projects = ProjectRepository(session)
        self.integrations = IntegrationsRegistryService(session, settings)

    def list_action_drafts(self) -> list[AccountActionDraftView]:
        return [self._build_view(item) for item in self.repository.list_all()]

    def get_action_draft(self, draft_id: int) -> AccountActionDraftView | None:
        item = self.repository.get_by_id(draft_id)
        if item is None:
            return None
        return self._build_view(item)

    def create_action_draft(
        self,
        *,
        account_id: int,
        integration_key: str,
        project_id: int | None,
        action_type: str,
        action_title: str,
        action_summary: str,
        preview_payload: dict[str, object],
        approval_notes: str | None,
        created_by: str = "user",
    ) -> AccountActionDraftView:
        account = self._require_account(account_id)
        normalized_integration_key = self._require_integration_key(integration_key)
        project_name = self._get_project_name(project_id)
        normalized_action_type = self._normalize_action_type(action_type)
        required_capabilities = ALLOWED_ACTION_TYPES[normalized_action_type]
        self._ensure_capability_match(account, normalized_action_type, required_capabilities)

        item = self.repository.create(
            account_id=account.id,
            integration_key=normalized_integration_key,
            project_id=project_id,
            action_type=normalized_action_type,
            action_title=self._normalize_required_text(action_title, "action_title"),
            action_summary=self._normalize_required_text(action_summary, "action_summary"),
            risk_level=ACTION_RISK_LEVELS[normalized_action_type],
            status="draft",
            requires_approval=True,
            preview_payload_json=self._normalize_preview_payload(preview_payload),
            approval_notes=self._normalize_optional_text(approval_notes),
            rejection_reason=None,
            audit_log_json=[
                self._build_audit_entry(
                    event="created",
                    from_status=None,
                    to_status="draft",
                    actor=created_by,
                    detail={
                        "integration_key": normalized_integration_key,
                        "project_id": project_id,
                        "project_name": project_name,
                        "required_capabilities": list(required_capabilities),
                    },
                )
            ],
            created_by=created_by,
            approved_at=None,
            rejected_at=None,
            executed_at=None,
        )
        return self._build_view(item)

    def submit_for_approval(self, draft_id: int) -> AccountActionDraftView:
        item = self._require_draft(draft_id)
        self._transition_status(item, "awaiting_approval", actor="user")
        return self._build_view(item)

    def approve(self, draft_id: int) -> AccountActionDraftView:
        item = self._require_draft(draft_id)
        self._transition_status(item, "approved", actor="user")
        item.approved_at = self._utcnow()
        self.repository.save(item)
        return self._build_view(item)

    def reject(self, draft_id: int, *, rejection_reason: str) -> AccountActionDraftView:
        item = self._require_draft(draft_id)
        item.rejection_reason = self._normalize_required_text(rejection_reason, "rejection_reason")
        self._transition_status(
            item,
            "rejected",
            actor="user",
            detail={"rejection_reason": item.rejection_reason},
        )
        item.rejected_at = self._utcnow()
        self.repository.save(item)
        return self._build_view(item)

    def cancel(self, draft_id: int) -> AccountActionDraftView:
        item = self._require_draft(draft_id)
        self._transition_status(item, "cancelled", actor="user")
        return self._build_view(item)

    def mark_executed(
        self,
        draft_id: int,
        *,
        actor: str = "atnos",
        detail: dict[str, object] | None = None,
    ) -> AccountActionDraftView:
        item = self._require_draft(draft_id)
        if item.status != "approved":
            raise AccountActionDraftTransitionError(
                f"Cannot execute action draft from status '{item.status}'."
            )
        item.status = "executed_later"
        item.executed_at = self._utcnow()
        item.audit_log_json = [
            *(item.audit_log_json or []),
            self._build_audit_entry(
                event="executed_later",
                from_status="approved",
                to_status="executed_later",
                actor=actor,
                detail=detail or {},
            ),
        ]
        self.repository.save(item)
        return self._build_view(item)

    def record_execution_failure(
        self,
        draft_id: int,
        *,
        actor: str = "atnos",
        detail: dict[str, object] | None = None,
    ) -> AccountActionDraftView:
        """Dodaje audyt nieudanego wykonania bez falszywego statusu wykonania."""
        item = self._require_draft(draft_id)
        if item.status != "approved":
            raise AccountActionDraftTransitionError(
                f"Cannot record execution failure from status '{item.status}'."
            )
        item.audit_log_json = [
            *(item.audit_log_json or []),
            self._build_audit_entry(
                event="execution_failed_rolled_back",
                from_status="approved",
                to_status="approved",
                actor=actor,
                detail=detail or {},
            ),
        ]
        self.repository.save(item)
        return self._build_view(item)

    def _build_view(self, item) -> AccountActionDraftView:
        account = self._require_account(item.account_id)
        project = self.projects.get(item.project_id) if item.project_id is not None else None
        required_capabilities = ALLOWED_ACTION_TYPES.get(item.action_type, ())
        return AccountActionDraftView(
            id=item.id,
            account_id=item.account_id,
            integration_key=item.integration_key,
            project_id=item.project_id,
            project_name=project.name if project is not None else None,
            action_type=item.action_type,
            action_title=item.action_title,
            action_summary=item.action_summary,
            risk_level=item.risk_level,
            status=item.status,
            requires_approval=item.requires_approval,
            preview_payload=dict(item.preview_payload_json or {}),
            approval_notes=item.approval_notes,
            rejection_reason=item.rejection_reason,
            audit_log=tuple(item.audit_log_json or []),
            created_by=item.created_by,
            approved_at=item.approved_at,
            rejected_at=item.rejected_at,
            executed_at=item.executed_at,
            required_capabilities=required_capabilities,
            account_summary=account,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _transition_status(
        self,
        item,
        next_status: str,
        *,
        actor: str,
        detail: dict[str, object] | None = None,
    ) -> None:
        normalized_next_status = self._normalize_public_status(next_status)
        allowed_from = {
            "awaiting_approval": {"draft"},
            "approved": {"draft", "awaiting_approval"},
            "rejected": {"draft", "awaiting_approval"},
            "cancelled": {"draft", "awaiting_approval", "approved"},
            "executed_later": {"approved"},
        }
        current_status = item.status
        if current_status == normalized_next_status:
            return
        if normalized_next_status not in allowed_from:
            raise AccountActionDraftTransitionError(
                f"Status transition to '{normalized_next_status}' is not allowed in v0."
            )
        if current_status not in allowed_from[normalized_next_status]:
            raise AccountActionDraftTransitionError(
                f"Cannot move action draft from '{current_status}' to '{normalized_next_status}'."
            )

        item.status = normalized_next_status
        item.audit_log_json = [
            *(item.audit_log_json or []),
            self._build_audit_entry(
                event=normalized_next_status,
                from_status=current_status,
                to_status=normalized_next_status,
                actor=actor,
                detail=detail or {},
            ),
        ]
        self.repository.save(item)

    def _require_draft(self, draft_id: int):
        item = self.repository.get_by_id(draft_id)
        if item is None:
            raise AccountActionDraftNotFoundError(f"Action draft {draft_id} was not found.")
        return item

    def _require_account(self, account_id: int) -> AccountCredentialView:
        account = self.accounts.get_account(account_id)
        if account is None:
            raise AccountActionDraftNotFoundError(f"Account {account_id} was not found.")
        return account

    def _get_project_name(self, project_id: int | None) -> str | None:
        if project_id is None:
            return None
        project = self.projects.get(project_id)
        if project is None:
            raise AccountActionDraftNotFoundError(f"Project {project_id} was not found.")
        return project.name

    def _require_integration_key(self, integration_key: str) -> str:
        normalized = self._normalize_required_text(integration_key, "integration_key").lower()
        available_keys = {item.key for item in self.integrations.list_integrations()}
        if normalized not in available_keys:
            raise AccountActionDraftNotFoundError(f"Integration '{normalized}' was not found.")
        return normalized

    @staticmethod
    def _normalize_action_type(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_ACTION_TYPES:
            allowed = ", ".join(sorted(ALLOWED_ACTION_TYPES))
            raise ValueError(f"action_type must be one of: {allowed}.")
        return normalized

    @staticmethod
    def _normalize_public_status(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in PUBLIC_MUTABLE_STATUSES:
            allowed = ", ".join(sorted(PUBLIC_MUTABLE_STATUSES))
            raise ValueError(f"status must be one of: {allowed}.")
        return normalized

    @staticmethod
    def _normalize_required_text(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_preview_payload(value: dict[str, object]) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("preview_payload must be a JSON object.")
        return value

    @staticmethod
    def _ensure_capability_match(
        account: AccountCredentialView,
        action_type: str,
        required_capabilities: tuple[str, ...],
    ) -> None:
        account_capabilities = set(account.allowed_capabilities)
        if account_capabilities.intersection(required_capabilities):
            return
        joined = ", ".join(required_capabilities)
        raise AccountActionDraftCapabilityError(
            f"Account {account.id} lacks required capability for {action_type}. Needs one of: {joined}."
        )

    @classmethod
    def _build_audit_entry(
        cls,
        *,
        event: str,
        from_status: str | None,
        to_status: str,
        actor: str,
        detail: dict[str, object],
    ) -> dict[str, object]:
        return {
            "event": event,
            "from_status": from_status,
            "to_status": to_status,
            "actor": actor,
            "at": cls._utcnow().isoformat(),
            "detail": detail,
        }

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
