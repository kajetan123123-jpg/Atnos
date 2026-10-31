from __future__ import annotations

from sqlalchemy.orm import Session

from atnos.db.models.enums import SelfImprovementDecision, SelfImprovementSeverity, SelfImprovementStatus
from atnos.db.models.self_improvement_note import SelfImprovementNote
from atnos.repositories.self_improvement_note_repository import SelfImprovementNoteRepository

_UNSET = object()


class SelfImprovementNotesNotFoundError(Exception):
    pass


class SelfImprovementNotesSelectionError(ValueError):
    pass


class SelfImprovementNotesService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.notes = SelfImprovementNoteRepository(session)

    def list_notes(self, *, status: SelfImprovementStatus | None = None) -> list[SelfImprovementNote]:
        return self.notes.list(status)

    def get_selected_note(self) -> SelfImprovementNote | None:
        selected_notes = self.notes.list_selected()
        if not selected_notes:
            return None
        return selected_notes[0]

    def create_note(
        self,
        *,
        source: str,
        view: str,
        friction: str,
        severity: SelfImprovementSeverity,
        proposed_fix: str | None = None,
    ) -> SelfImprovementNote:
        return self.notes.create(
            source=self._normalize_required_text(source, field_name="source"),
            view=self._normalize_required_text(view, field_name="view"),
            friction=self._normalize_required_text(friction, field_name="friction"),
            severity=severity,
            proposed_fix=self._normalize_optional_text(proposed_fix),
            decision=SelfImprovementDecision.UNDECIDED,
            status=SelfImprovementStatus.OPEN,
        )

    def update_triage(
        self,
        note_id: int,
        *,
        severity: SelfImprovementSeverity | None | object = _UNSET,
        proposed_fix: str | None | object = _UNSET,
        chosen_next_patch: str | None | object = _UNSET,
        decision: SelfImprovementDecision | None | object = _UNSET,
        status: SelfImprovementStatus | None | object = _UNSET,
    ) -> SelfImprovementNote:
        note = self.notes.get(note_id)
        if note is None:
            raise SelfImprovementNotesNotFoundError(f"Unknown self_improvement_note_id '{note_id}'.")

        self._reject_null_enum(severity, field_name="severity")
        self._reject_null_enum(decision, field_name="decision")
        self._reject_null_enum(status, field_name="status")

        next_status = status if status is not _UNSET else note.status

        if status is SelfImprovementStatus.SELECTED and note.status is SelfImprovementStatus.CLOSED:
            raise SelfImprovementNotesSelectionError(
                "Closed note cannot be selected for review. Reopen it first."
            )

        if status is SelfImprovementStatus.SELECTED:
            self.notes.clear_selected_except(note_id)

        note = self.notes.update_triage(
            note,
            severity=severity,
            proposed_fix=self._normalize_optional_text(proposed_fix) if proposed_fix is not _UNSET else _UNSET,
            chosen_next_patch=(
                self._normalize_optional_text(chosen_next_patch)
                if chosen_next_patch is not _UNSET
                else _UNSET
            ),
            decision=decision,
            status=status,
            unset=_UNSET,
        )
        if next_status is SelfImprovementStatus.CLOSED and note.status is SelfImprovementStatus.CLOSED:
            # Closing a selected note should leave the system with no selected note.
            # The note itself is already closed above; other selected notes are impossible
            # because the service invariant keeps at most one selected note.
            return note
        return note

    @staticmethod
    def _normalize_required_text(value: str, *, field_name: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None | object) -> str | None | object:
        if value is _UNSET:
            return _UNSET
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _reject_null_enum(value: object, *, field_name: str) -> None:
        if value is None:
            raise ValueError(f"{field_name} cannot be null.")
