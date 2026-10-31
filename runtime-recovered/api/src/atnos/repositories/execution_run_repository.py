from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.execution_run import ExecutionRun, ExecutionStep


class ExecutionRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_runs(self, *, limit: int = 20) -> list[ExecutionRun]:
        statement = (
            select(ExecutionRun)
            .order_by(ExecutionRun.created_at.desc(), ExecutionRun.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def list_running_runs(self) -> list[ExecutionRun]:
        statement = (
            select(ExecutionRun)
            .where(ExecutionRun.status == "running")
            .order_by(ExecutionRun.updated_at.desc(), ExecutionRun.id.desc())
        )
        return list(self.session.scalars(statement))

    def list_runs_for_project(self, project_id: int, *, limit: int = 20) -> list[ExecutionRun]:
        statement = (
            select(ExecutionRun)
            .where(ExecutionRun.project_id == project_id)
            .order_by(ExecutionRun.created_at.desc(), ExecutionRun.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def get_run(self, run_id: int) -> ExecutionRun | None:
        statement = select(ExecutionRun).where(ExecutionRun.id == run_id)
        return self.session.scalar(statement)

    def create_run(self, **kwargs) -> ExecutionRun:
        item = ExecutionRun(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save_run(self, item: ExecutionRun) -> ExecutionRun:
        self.session.add(item)
        self.session.flush()
        return item

    def list_steps(self, run_id: int) -> list[ExecutionStep]:
        statement = (
            select(ExecutionStep)
            .where(ExecutionStep.execution_run_id == run_id)
            .order_by(ExecutionStep.step_index.asc(), ExecutionStep.id.asc())
        )
        return list(self.session.scalars(statement))

    def create_step(self, **kwargs) -> ExecutionStep:
        item = ExecutionStep(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save_step(self, item: ExecutionStep) -> ExecutionStep:
        self.session.add(item)
        self.session.flush()
        return item
