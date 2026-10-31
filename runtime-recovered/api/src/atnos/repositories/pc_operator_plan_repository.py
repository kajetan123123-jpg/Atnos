from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.pc_operator_plan import PCOperatorPlan


class PCOperatorPlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[PCOperatorPlan]:
        statement = select(PCOperatorPlan).order_by(
            PCOperatorPlan.updated_at.desc(),
            PCOperatorPlan.id.desc(),
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, plan_id: int) -> PCOperatorPlan | None:
        statement = select(PCOperatorPlan).where(PCOperatorPlan.id == plan_id)
        return self.session.scalar(statement)

    def create(
        self,
        *,
        project_id: int | None,
        title: str,
        goal: str,
        proposed_steps_json: list[dict[str, object]],
        target_surface: str,
        risk_level: str,
        status: str,
        requires_approval: bool,
        blocked_reason: str | None,
    ) -> PCOperatorPlan:
        item = PCOperatorPlan(
            project_id=project_id,
            title=title,
            goal=goal,
            proposed_steps_json=proposed_steps_json,
            target_surface=target_surface,
            risk_level=risk_level,
            status=status,
            requires_approval=requires_approval,
            blocked_reason=blocked_reason,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: PCOperatorPlan) -> PCOperatorPlan:
        self.session.add(item)
        self.session.flush()
        return item
