from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from atnos.db.models.enums import ExecutionTraceKind, ExecutionTraceStatus
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.services.trace_metadata_sanitizer import sanitize_trace_metadata
from atnos.services.trace_summary_sanitizer import sanitize_trace_summary


class ExecutionTraceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        step_name: str,
        trace_kind: ExecutionTraceKind,
        status: ExecutionTraceStatus = ExecutionTraceStatus.PENDING,
        conversation_id: int | None = None,
        message_id: int | None = None,
        project_id: int | None = None,
        provider_key: str | None = None,
        plugin_key: str | None = None,
        model_key: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        duration_ms: int | None = None,
        error_message: str | None = None,
        metadata_json: dict | None = None,
    ) -> ExecutionTrace:
        trace = ExecutionTrace(
            step_name=step_name,
            trace_kind=trace_kind,
            status=status,
            conversation_id=conversation_id,
            message_id=message_id,
            project_id=project_id,
            provider_key=provider_key,
            plugin_key=plugin_key,
            model_key=model_key,
            input_summary=sanitize_trace_summary(input_summary, field_name="input_summary"),
            output_summary=sanitize_trace_summary(output_summary, field_name="output_summary"),
            duration_ms=duration_ms,
            error_message=sanitize_trace_summary(error_message, field_name="error_message"),
            metadata_json=sanitize_trace_metadata(metadata_json),
        )
        self.session.add(trace)
        self.session.flush()
        return trace

    def list_for_message(self, message_id: int) -> list[ExecutionTrace]:
        statement = (
            select(ExecutionTrace)
            .where(ExecutionTrace.message_id == message_id)
            .order_by(ExecutionTrace.created_at.asc())
        )
        return list(self.session.scalars(statement))

    def count_all(self) -> int:
        statement = select(func.count()).select_from(ExecutionTrace)
        return int(self.session.scalar(statement) or 0)

    def count_older_than(self, cutoff: datetime) -> int:
        statement = (
            select(func.count())
            .select_from(ExecutionTrace)
            .where(ExecutionTrace.created_at < cutoff)
        )
        return int(self.session.scalar(statement) or 0)

    def oldest_newest_eligible(
        self,
        cutoff: datetime,
    ) -> tuple[datetime | None, datetime | None]:
        statement = select(
            func.min(ExecutionTrace.created_at),
            func.max(ExecutionTrace.created_at),
        ).where(ExecutionTrace.created_at < cutoff)
        oldest, newest = self.session.execute(statement).one()
        return oldest, newest

    def delete_older_than(self, cutoff: datetime) -> int:
        statement = delete(ExecutionTrace).where(ExecutionTrace.created_at < cutoff)
        result = self.session.execute(statement)
        return int(result.rowcount or 0)
