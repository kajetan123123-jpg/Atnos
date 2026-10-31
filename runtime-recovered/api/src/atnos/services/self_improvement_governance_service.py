from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import SelfImprovementDecision, SelfImprovementSeverity, SelfImprovementStatus
from atnos.db.models.self_improvement_note import SelfImprovementNote
from atnos.repositories.self_improvement_note_repository import SelfImprovementNoteRepository
from atnos.services.self_coding_draft_service import SelfCodingDraftService
from atnos.services.self_improvement_notes_service import SelfImprovementNotesService


SUPERVISED_SOURCE = "codex-supervised-self-development"
SUPERVISED_VIEW = "global-self-improvement"


@dataclass(frozen=True, slots=True)
class SelfImprovementGovernanceStatus:
    final_reviewer: str
    self_apply_allowed: bool
    handoff_dir: Path
    log_path: Path
    rules: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["handoff_dir"] = str(self.handoff_dir)
        payload["log_path"] = str(self.log_path)
        payload["rules"] = list(self.rules)
        return payload


@dataclass(frozen=True, slots=True)
class CodexHandoffPackage:
    note_id: int
    handoff_path: Path
    report_path: Path
    status: str
    message: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["handoff_path"] = str(self.handoff_path)
        payload["report_path"] = str(self.report_path)
        return payload


class SelfImprovementGovernanceService:
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.notes = SelfImprovementNoteRepository(session)
        self.note_service = SelfImprovementNotesService(session)
        self.drafts = SelfCodingDraftService(session, settings=settings)

    def status(self) -> SelfImprovementGovernanceStatus:
        return SelfImprovementGovernanceStatus(
            final_reviewer=self.settings.self_improvement_final_reviewer,
            self_apply_allowed=self.settings.self_improvement_allow_self_apply,
            handoff_dir=self.settings.self_improvement_handoff_dir,
            log_path=self.settings.self_improvement_log_path,
            rules=(
                "Atnos may collect QA findings, screenshots, logs, and improvement notes.",
                "Atnos may generate draft plans, test plans, and Codex handoff prompts.",
                "Atnos must not apply final self-improvement code patches unless self_apply is explicitly enabled.",
                "Final product/code changes go through Codex review and validation.",
                "External/cloud review of screenshots remains opt-in per request.",
            ),
        )

    def seed_supervised_program(self) -> SelfImprovementNote:
        existing = self._find_supervised_note()
        if existing is not None:
            return existing

        note = self.note_service.create_note(
            source=SUPERVISED_SOURCE,
            view=SUPERVISED_VIEW,
            friction=(
                "Atnos needs a supervised self-development loop: collect Visual QA evidence, "
                "identify UX/content quality issues, prepare a minimal patch plan, and hand the final "
                "change to Codex instead of auto-applying it."
            ),
            severity=SelfImprovementSeverity.P1,
            proposed_fix=(
                "Run Visual QA and quality checks, store artifacts on F:, create a Codex handoff, "
                "and keep final repository modifications blocked until Codex verifies the proposal."
            ),
        )
        return self.note_service.update_triage(
            note.id,
            decision=SelfImprovementDecision.FOLLOW_UP,
            status=SelfImprovementStatus.SELECTED,
            chosen_next_patch=(
                "Codex reviews the generated handoff and applies only focused changes that match "
                "Atnos product direction: simple personal agent, Chat + Settings, no debug clutter."
            ),
        )

    def write_codex_handoff(self, *, note_id: int | None = None) -> CodexHandoffPackage:
        self.settings.self_improvement_handoff_dir.mkdir(parents=True, exist_ok=True)
        self.settings.self_improvement_log_path.parent.mkdir(parents=True, exist_ok=True)

        preview = self.drafts.build_preview(note_id=note_id)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"self-improvement-{stamp}-{uuid4().hex[:8]}"
        handoff_path = (self.settings.self_improvement_handoff_dir / f"{run_id}_codex_handoff.md").resolve()
        report_path = (self.settings.self_improvement_handoff_dir / f"{run_id}_report.json").resolve()

        content = "\n".join(
            [
                "# Atnos Self-Improvement Handoff",
                "",
                "## Governance",
                "",
                f"- final_reviewer: {self.settings.self_improvement_final_reviewer}",
                f"- self_apply_allowed: {self.settings.self_improvement_allow_self_apply}",
                "- rule: Atnos prepares; Codex verifies and applies if it fits.",
                "",
                "## Codex Prompt",
                "",
                "# MODE: CODEX REVIEW AND IMPLEMENT IF IT FITS",
                "",
                "You are Codex working on Atnos.",
                "",
                "Atnos generated this self-improvement package, but it is not allowed to apply final code changes itself.",
                "Review the evidence and patch plan. Apply only focused changes that match the product direction.",
                "",
                "Product direction:",
                "- Atnos is a simple personal AI agent, not a model arena.",
                "- Main surfaces are Chat and Settings.",
                "- Prefer clarity, speed of daily use, content quality, and fewer debug surfaces.",
                "- Store artifacts on F: or the Ubuntu sandbox.",
                "",
                "Reject or rewrite the proposal if it adds clutter, unsafe autonomy, public publishing, or unrelated refactors.",
                "",
                "## Source Draft Prompt From Atnos",
                "",
                "Reference only. If this conflicts with the Codex gate above, follow the Codex gate.",
                "",
                preview.codex_prompt,
                "",
                "## Patch Plan",
                "",
                preview.patch_plan,
                "",
                "## Test Plan",
                "",
                preview.test_plan,
                "",
                "## Safety Notes",
                "",
                *[f"- {note}" for note in preview.safety_notes],
                "",
                "## Warnings",
                "",
                *[f"- {warning}" for warning in preview.warnings],
            ]
        )
        handoff_path.write_text(content, encoding="utf-8")

        package = CodexHandoffPackage(
            note_id=preview.note_id,
            handoff_path=handoff_path,
            report_path=report_path,
            status="codex_review_required",
            message="Self-improvement package is ready. Final code changes must go through Codex.",
        )
        report_path.write_text(json.dumps(package.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._log({"event": "codex_handoff_created", **package.to_dict()})
        return package

    def _find_supervised_note(self) -> SelfImprovementNote | None:
        for note in self.notes.list():
            if note.source == SUPERVISED_SOURCE and note.view == SUPERVISED_VIEW:
                return note
        return None

    def _log(self, payload: dict[str, object]) -> None:
        event = {"timestamp": datetime.now().isoformat(), **payload}
        with self.settings.self_improvement_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
