from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.account_action_draft import AccountActionDraft


class AccountActionDraftRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[AccountActionDraft]:
        statement = select(AccountActionDraft).order_by(
            AccountActionDraft.created_at.desc(),
            AccountActionDraft.id.desc(),
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, draft_id: int) -> AccountActionDraft | None:
        statement = select(AccountActionDraft).where(AccountActionDraft.id == draft_id)
        return self.session.scalar(statement)

    def create(
        self,
        *,
        account_id: int,
        integration_key: str,
        project_id: int | None,
        action_type: str,
        action_title: str,
        action_summary: str,
        risk_level: str,
        status: str,
        requires_approval: bool,
        preview_payload_json: dict[str, object],
        approval_notes: str | None,
        rejection_reason: str | None,
        audit_log_json: list[dict[str, object]],
        created_by: str,
        approved_at,
        rejected_at,
        executed_at,
    ) -> AccountActionDraft:
        item = AccountActionDraft(
            account_id=account_id,
            integration_key=integration_key,
            project_id=project_id,
            action_type=action_type,
            action_title=action_title,
            action_summary=action_summary,
            risk_level=risk_level,
            status=status,
            requires_approval=requires_approval,
            preview_payload_json=preview_payload_json,
            approval_notes=approval_notes,
            rejection_reason=rejection_reason,
            audit_log_json=audit_log_json,
            created_by=created_by,
            approved_at=approved_at,
            rejected_at=rejected_at,
            executed_at=executed_at,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: AccountActionDraft) -> AccountActionDraft:
        self.session.add(item)
        self.session.flush()
        return item
