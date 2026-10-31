from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from atnos.core.config import Settings
from atnos.repositories.execution_trace_repository import ExecutionTraceRepository


class TraceRetentionError(ValueError):
    pass


class TraceRetentionConfirmationRequiredError(TraceRetentionError):
    pass


@dataclass(slots=True)
class TraceRetentionPreview:
    retention_days: int
    cutoff_at: datetime
    total_trace_count: int
    eligible_trace_count: int
    newest_eligible_created_at: datetime | None
    oldest_eligible_created_at: datetime | None
    enabled: bool
    will_delete: bool = False


@dataclass(slots=True)
class TraceRetentionCleanupResult:
    deleted_count: int
    retention_days: int
    cutoff_at: datetime
    remaining_trace_count: int
    enabled: bool
    dry_run: bool = False


class TraceRetentionService:
    def __init__(
        self,
        repository: ExecutionTraceRepository,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.settings = settings

    def preview_cleanup(self, retention_days: int | None = None) -> TraceRetentionPreview:
        effective_days = self._resolve_retention_days(retention_days)
        cutoff_at = self._build_cutoff(effective_days)
        total_trace_count = self.repository.count_all()
        eligible_trace_count = self.repository.count_older_than(cutoff_at)
        oldest_eligible_created_at, newest_eligible_created_at = (
            self.repository.oldest_newest_eligible(cutoff_at)
        )
        return TraceRetentionPreview(
            retention_days=effective_days,
            cutoff_at=cutoff_at,
            total_trace_count=total_trace_count,
            eligible_trace_count=eligible_trace_count,
            newest_eligible_created_at=newest_eligible_created_at,
            oldest_eligible_created_at=oldest_eligible_created_at,
            enabled=self.settings.trace_retention_enabled,
            will_delete=False,
        )

    def cleanup_old_traces(
        self,
        retention_days: int | None = None,
        *,
        confirm: bool = False,
    ) -> TraceRetentionCleanupResult:
        effective_days = self._resolve_retention_days(retention_days)
        cutoff_at = self._build_cutoff(effective_days)
        if confirm is not True:
            raise TraceRetentionConfirmationRequiredError(
                "Trace cleanup requires confirm=true."
            )
        deleted_count = self.repository.delete_older_than(cutoff_at)
        remaining_trace_count = self.repository.count_all()
        return TraceRetentionCleanupResult(
            deleted_count=deleted_count,
            retention_days=effective_days,
            cutoff_at=cutoff_at,
            remaining_trace_count=remaining_trace_count,
            enabled=self.settings.trace_retention_enabled,
            dry_run=False,
        )

    def _resolve_retention_days(self, retention_days: int | None) -> int:
        effective_days = retention_days or self.settings.trace_retention_days
        if effective_days < 1:
            raise TraceRetentionError("retention_days must be at least 1.")
        return effective_days

    @staticmethod
    def _build_cutoff(retention_days: int) -> datetime:
        return datetime.now(UTC) - timedelta(days=retention_days)
