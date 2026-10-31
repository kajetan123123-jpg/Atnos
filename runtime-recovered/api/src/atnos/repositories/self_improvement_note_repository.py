from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.enums import SelfImprovementDecision, SelfImprovementSeverity, SelfImprovementStatus
from atnos.db.models.self_improvement_note import SelfImprovementNote


class SelfImprovementNoteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        source: str,
        view: str,
        friction: str,
        severity: SelfImprovementSeverity,
        proposed_fix: str | None,
        decision: SelfImprovementDecision = SelfImprovementDecision.UNDECIDED,
        status: SelfImprovementStatus = SelfImprovementStatus.OPEN,
    ) -> SelfImprovementNote:
        note = SelfImprovementNote(
            source=source,
            view=view,
            friction=friction,
            severity=severity,
            proposed_fix=proposed_fix,
            decision=decision,
            status=status,
        )
        self.session.add(note)
        self.session.flush()
        self.session.refresh(note)
        return note

    def list(self, status: SelfImprovementStatus | None = None) -> list[SelfImprovementNote]:
        statement = select(SelfImprovementNote)
        if status is not None:
            statement = statement.where(SelfImprovementNote.status == status)
        statement = statement.order_by(
            SelfImprovementNote.updated_at.desc(),
            SelfImprovementNote.id.desc(),
        )
        return list(self.session.scalars(statement))

    def get(self, note_id: int) -> SelfImprovementNote | None:
        return self.session.get(SelfImprovementNote, note_id)

    def list_by_ids(self, note_ids: list[int] | tuple[int, ...] | set[int]) -> list[SelfImprovementNote]:
        normalized_ids = sorted({int(note_id) for note_id in note_ids if int(note_id) > 0})
        if not normalized_ids:
            return []
        statement = (
            select(SelfImprovementNote)
            .where(SelfImprovementNote.id.in_(normalized_ids))
            .order_by(SelfImprovementNote.updated_at.desc(), SelfImprovementNote.id.desc())
        )
        return list(self.session.scalars(statement))

    def list_selected(self) -> list[SelfImprovementNote]:
        statement = (
            select(SelfImprovementNote)
            .where(SelfImprovementNote.status == SelfImprovementStatus.SELECTED)
            .order_by(
                SelfImprovementNote.updated_at.desc(),
                SelfImprovementNote.id.desc(),
            )
        )
        return list(self.session.scalars(statement))

    def clear_selected_except(self, note_id: int) -> list[SelfImprovementNote]:
        selected_notes = [
            note for note in self.list_selected() if note.id != note_id
        ]
        for note in selected_notes:
            note.status = SelfImprovementStatus.OPEN
            self.session.add(note)
        return selected_notes

    def update_triage(
        self,
        note: SelfImprovementNote,
        *,
        severity: SelfImprovementSeverity | None | object,
        proposed_fix: str | None | object,
        chosen_next_patch: str | None | object,
        decision: SelfImprovementDecision | None | object,
        status: SelfImprovementStatus | None | object,
        unset: object,
    ) -> SelfImprovementNote:
        if severity is not unset:
            note.severity = severity
        if proposed_fix is not unset:
            note.proposed_fix = proposed_fix
        if chosen_next_patch is not unset:
            note.chosen_next_patch = chosen_next_patch
        if decision is not unset:
            note.decision = decision
        if status is not unset:
            note.status = status
        self.session.add(note)
        self.session.flush()
        self.session.refresh(note)
        return note
