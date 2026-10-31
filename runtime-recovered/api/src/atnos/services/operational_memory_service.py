from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from atnos.db.models.decision_log import DecisionLogEntry
from atnos.db.models.enums import (
    DecisionLogKind,
    OperationalWorkItemPriority,
    OperationalWorkItemReviewState,
    OperationalWorkItemStatus,
    SelfImprovementSeverity,
    SelfImprovementStatus,
)
from atnos.db.models.operational_work_item import OperationalWorkItem
from atnos.db.models.self_improvement_note import SelfImprovementNote
from atnos.repositories.decision_log_repository import DecisionLogRepository
from atnos.repositories.operational_work_item_repository import OperationalWorkItemRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.self_improvement_note_repository import SelfImprovementNoteRepository

_UNSET = object()


class OperationalMemoryNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class OperationalMemorySnapshot:
    project_id: int | None
    source_note: SelfImprovementNote | None
    work_items: tuple["OperationalMemoryWorkItemView", ...]
    active_decisions: tuple[DecisionLogEntry, ...]


@dataclass(frozen=True, slots=True)
class OperationalMemoryDecisionContext:
    active_count: int
    latest_summary: str | None
    latest_kind: DecisionLogKind | None
    latest_due_at: datetime | None
    recent_entries: tuple[DecisionLogEntry, ...]


@dataclass(frozen=True, slots=True)
class OperationalMemoryWorkItemView:
    item: OperationalWorkItem
    source_note: SelfImprovementNote | None
    decision_context: OperationalMemoryDecisionContext | None


@dataclass(frozen=True, slots=True)
class OperationalMemoryPromotionDraft:
    source_note_id: int
    title: str
    summary: str
    next_step: str | None
    priority: OperationalWorkItemPriority


@dataclass(frozen=True, slots=True)
class OperationalMemoryPromotionPreview:
    project_id: int
    note: SelfImprovementNote | None
    work_item_draft: OperationalMemoryPromotionDraft | None
    eligible: bool
    reason: str | None
    warnings: tuple[str, ...]


class OperationalMemoryService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.work_items = OperationalWorkItemRepository(session)
        self.projects = ProjectRepository(session)
        self.notes = SelfImprovementNoteRepository(session)
        self.decisions = DecisionLogRepository(session)

    def create_work_item(
        self,
        *,
        title: str,
        summary: str,
        next_step: str | None = None,
        project_id: int | None = None,
        source_note_id: int | None = None,
        priority: OperationalWorkItemPriority | None = None,
        status: OperationalWorkItemStatus = OperationalWorkItemStatus.ACTIVE,
        review_state: OperationalWorkItemReviewState = OperationalWorkItemReviewState.UNREVIEWED,
        blocked_reason: str | None = None,
    ) -> OperationalWorkItem:
        source_note = self._require_anchor(project_id=project_id, source_note_id=source_note_id)
        resolved_priority = priority or self._priority_from_note(source_note) or OperationalWorkItemPriority.P1
        self._reject_null_enum(review_state, field_name="review_state")

        if source_note_id is not None and status in self.work_items.OPEN_STATUSES:
            existing = self.work_items.get_open_for_source_note(source_note_id)
            if existing is not None:
                raise ValueError(
                    f"Source note {source_note_id} already maps to open work item {existing.id}."
                )

        return self.work_items.create(
            project_id=project_id,
            source_note_id=source_note_id,
            title=self._normalize_required_text(title, field_name="title"),
            summary=self._normalize_required_text(summary, field_name="summary"),
            next_step=self._normalize_optional_text(next_step, field_name="next_step"),
            status=status,
            priority=resolved_priority,
            review_state=review_state,
            blocked_reason=self._resolve_blocked_reason(
                status=status,
                blocked_reason=blocked_reason,
                existing_value=None,
            ),
        )

    def update_work_item(
        self,
        work_item_id: int,
        *,
        title: str | None | object = _UNSET,
        summary: str | None | object = _UNSET,
        next_step: str | None | object = _UNSET,
        status: OperationalWorkItemStatus | None | object = _UNSET,
        priority: OperationalWorkItemPriority | None | object = _UNSET,
        review_state: OperationalWorkItemReviewState | None | object = _UNSET,
        blocked_reason: str | None | object = _UNSET,
    ) -> OperationalWorkItem:
        item = self.work_items.get(work_item_id)
        if item is None:
            raise OperationalMemoryNotFoundError(f"Unknown operational_work_item_id '{work_item_id}'.")

        self._reject_null_enum(status, field_name="status")
        self._reject_null_enum(priority, field_name="priority")
        self._reject_null_enum(review_state, field_name="review_state")

        next_status = item.status if status is _UNSET else status
        if (
            item.source_note_id is not None
            and next_status in self.work_items.OPEN_STATUSES
        ):
            existing = self.work_items.get_open_for_source_note(item.source_note_id, excluding_id=item.id)
            if existing is not None:
                raise ValueError(
                    f"Source note {item.source_note_id} already maps to open work item {existing.id}."
                )

        if title is not _UNSET:
            item.title = self._normalize_required_text(title, field_name="title")
        if summary is not _UNSET:
            item.summary = self._normalize_required_text(summary, field_name="summary")
        if next_step is not _UNSET:
            item.next_step = self._normalize_optional_text(next_step, field_name="next_step")
        if status is not _UNSET:
            item.status = status
        if priority is not _UNSET:
            item.priority = priority
        if review_state is not _UNSET:
            item.review_state = review_state
        item.blocked_reason = self._resolve_blocked_reason(
            status=item.status,
            blocked_reason=blocked_reason,
            existing_value=item.blocked_reason,
        )

        return self.work_items.save(item)

    def list_work_items(
        self,
        *,
        project_id: int | None = None,
        source_note_id: int | None = None,
        status: OperationalWorkItemStatus | None = None,
        limit: int | None = None,
    ) -> list[OperationalWorkItem]:
        self._require_anchor(project_id=project_id, source_note_id=source_note_id, require_anchor=False)
        return self.work_items.list(
            project_id=project_id,
            source_note_id=source_note_id,
            status=status,
            limit=limit,
        )

    def build_snapshot(
        self,
        *,
        project_id: int | None = None,
        source_note_id: int | None = None,
        work_item_limit: int = 8,
        decision_limit: int = 8,
    ) -> OperationalMemorySnapshot:
        source_note = self._require_anchor(project_id=project_id, source_note_id=source_note_id)
        work_items = self.work_items.list(
            project_id=project_id,
            source_note_id=source_note_id,
            limit=work_item_limit,
        )
        if project_id is not None:
            active_decisions = self.decisions.list_active(project_id=project_id, limit=decision_limit)
        else:
            active_decisions = []
        decision_context = self._build_decision_context(active_decisions)
        source_note_map = self._build_source_note_map(work_items)
        return OperationalMemorySnapshot(
            project_id=project_id,
            source_note=source_note,
            work_items=tuple(
                OperationalMemoryWorkItemView(
                    item=item,
                    source_note=source_note_map.get(item.source_note_id),
                    decision_context=decision_context if item.project_id is not None else None,
                )
                for item in work_items
            ),
            active_decisions=tuple(active_decisions),
        )

    def build_promotion_preview(
        self,
        *,
        project_id: int,
        note_id: int | None = None,
    ) -> OperationalMemoryPromotionPreview:
        self._require_anchor(project_id=project_id, source_note_id=None)
        note = self._resolve_promotion_note(note_id=note_id, allow_missing_selected=True)
        if note is None:
            return OperationalMemoryPromotionPreview(
                project_id=project_id,
                note=None,
                work_item_draft=None,
                eligible=False,
                reason=(
                    "Brak wybranej notatki do promowania. Najpierw zaznacz jedna notatke "
                    "w Improvement Notes."
                ),
                warnings=(),
            )

        existing = self.work_items.get_open_for_source_note(note.id)
        if existing is not None:
            return OperationalMemoryPromotionPreview(
                project_id=project_id,
                note=note,
                work_item_draft=None,
                eligible=False,
                reason=(
                    f"Ta notatka ma juz otwarty work item #{existing.id}. "
                    "Zamknij go albo wybierz inna note."
                ),
                warnings=(),
            )

        draft, warnings = self._build_promotion_draft(note)
        return OperationalMemoryPromotionPreview(
            project_id=project_id,
            note=note,
            work_item_draft=draft,
            eligible=True,
            reason=None,
            warnings=warnings,
        )

    def promote_note_to_work_item(
        self,
        *,
        project_id: int,
        note_id: int | None = None,
    ) -> OperationalWorkItem:
        self._require_anchor(project_id=project_id, source_note_id=None)
        note = self._resolve_promotion_note(note_id=note_id, allow_missing_selected=False)
        if note is None:
            raise ValueError("No selected improvement note is available for promotion.")

        draft, _warnings = self._build_promotion_draft(note)
        return self.create_work_item(
            project_id=project_id,
            source_note_id=note.id,
            title=draft.title,
            summary=draft.summary,
            next_step=draft.next_step,
            priority=draft.priority,
        )

    def _require_anchor(
        self,
        *,
        project_id: int | None,
        source_note_id: int | None,
        require_anchor: bool = True,
    ) -> SelfImprovementNote | None:
        if require_anchor and project_id is None and source_note_id is None:
            raise ValueError("Operational memory item must be anchored to a project or source note.")

        if project_id is not None and self.projects.get(project_id) is None:
            raise ValueError(f"Unknown project_id '{project_id}'.")

        source_note = None
        if source_note_id is not None:
            source_note = self.notes.get(source_note_id)
            if source_note is None:
                raise ValueError(f"Unknown source_note_id '{source_note_id}'.")
        return source_note

    @staticmethod
    def _priority_from_note(note: SelfImprovementNote | None) -> OperationalWorkItemPriority | None:
        if note is None:
            return None
        mapping = {
            SelfImprovementSeverity.P0: OperationalWorkItemPriority.P0,
            SelfImprovementSeverity.P1: OperationalWorkItemPriority.P1,
            SelfImprovementSeverity.P2: OperationalWorkItemPriority.P2,
        }
        return mapping.get(note.severity)

    def _resolve_promotion_note(
        self,
        *,
        note_id: int | None,
        allow_missing_selected: bool,
    ) -> SelfImprovementNote | None:
        if note_id is not None:
            note = self.notes.get(note_id)
            if note is None:
                raise ValueError(f"Unknown source_note_id '{note_id}'.")
            if note.status is not SelfImprovementStatus.SELECTED:
                raise ValueError(
                    "Only a selected improvement note can be promoted to operational memory."
                )
            return note

        selected = self.notes.list_selected()
        if not selected:
            if allow_missing_selected:
                return None
            raise ValueError("No selected improvement note is available for promotion.")
        return selected[0]

    def _build_promotion_draft(
        self,
        note: SelfImprovementNote,
    ) -> tuple[OperationalMemoryPromotionDraft, tuple[str, ...]]:
        title_candidate = note.chosen_next_patch or note.proposed_fix or note.friction
        title = self._truncate_text(
            self._normalize_required_text(title_candidate, field_name="title"),
            max_length=200,
        )
        summary = self._truncate_text(
            self._normalize_required_text(note.friction, field_name="summary"),
            max_length=4000,
        )

        next_step_candidates: list[str] = []
        for candidate in (note.proposed_fix, note.chosen_next_patch):
            normalized = self._normalize_optional_text(candidate, field_name="next_step")
            if isinstance(normalized, str) and normalized != title and normalized not in next_step_candidates:
                next_step_candidates.append(normalized)
        next_step = (
            self._truncate_text(next_step_candidates[0], max_length=4000)
            if next_step_candidates
            else None
        )

        warnings: list[str] = []
        if note.proposed_fix is None and note.chosen_next_patch is None:
            warnings.append(
                "Preview opiera sie tylko na friction note. Doprecyzuj proposed fix albo "
                "chosen next patch, jesli chcesz lepszy work item."
            )

        return (
            OperationalMemoryPromotionDraft(
                source_note_id=note.id,
                title=title,
                summary=summary,
                next_step=next_step,
                priority=self._priority_from_note(note) or OperationalWorkItemPriority.P1,
            ),
            tuple(warnings),
        )

    def _build_source_note_map(
        self,
        work_items: list[OperationalWorkItem],
    ) -> dict[int, SelfImprovementNote]:
        source_note_ids = {
            item.source_note_id
            for item in work_items
            if item.source_note_id is not None
        }
        if not source_note_ids:
            return {}
        return {
            note.id: note
            for note in self.notes.list_by_ids(source_note_ids)
        }

    @staticmethod
    def _build_decision_context(
        active_decisions: list[DecisionLogEntry],
    ) -> OperationalMemoryDecisionContext | None:
        if not active_decisions:
            return None
        latest = active_decisions[0]
        return OperationalMemoryDecisionContext(
            active_count=len(active_decisions),
            latest_summary=latest.summary,
            latest_kind=latest.kind,
            latest_due_at=latest.due_at,
            recent_entries=tuple(active_decisions[:3]),
        )

    @staticmethod
    def _truncate_text(value: str, *, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        return f"{value[: max_length - 3].rstrip()}..."

    @staticmethod
    def _normalize_required_text(value: str | None | object, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} cannot be empty.")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None | object, *, field_name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string or null.")
        normalized = value.strip()
        return normalized or None

    def _resolve_blocked_reason(
        self,
        *,
        status: OperationalWorkItemStatus,
        blocked_reason: str | None | object,
        existing_value: str | None,
    ) -> str | None:
        if status is not OperationalWorkItemStatus.BLOCKED:
            if blocked_reason is _UNSET:
                return None
            normalized = self._normalize_optional_text(blocked_reason, field_name="blocked_reason")
            if normalized is not None:
                raise ValueError("blocked_reason can only be set when status is blocked.")
            return None

        if blocked_reason is _UNSET:
            return existing_value
        return self._normalize_optional_text(blocked_reason, field_name="blocked_reason")

    @staticmethod
    def _reject_null_enum(value: object, *, field_name: str) -> None:
        if value is None:
            raise ValueError(f"{field_name} cannot be null.")
