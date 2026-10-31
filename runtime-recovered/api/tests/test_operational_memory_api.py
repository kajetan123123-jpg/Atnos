from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.db.models.account_action_draft import AccountActionDraft
from atnos.db.models.enums import (
    DecisionLogKind,
    DecisionLogStatus,
    MessageRole,
    MessageStatus,
    OperationalWorkItemPriority,
    OperationalWorkItemReviewState,
    OperationalWorkItemStatus,
    SelfImprovementDecision,
    SelfImprovementSeverity,
    SelfImprovementStatus,
)
from atnos.db.models.execution_run import ExecutionRun
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.decision_log_repository import DecisionLogRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.self_improvement_note_repository import SelfImprovementNoteRepository


class OperationalMemoryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_create_work_item_from_note_inherits_priority_from_note_severity(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                note = SelfImprovementNoteRepository(session).create(
                    source="manual_dogfooding",
                    view="settings",
                    friction="Review notes should survive into operational memory.",
                    severity=SelfImprovementSeverity.P0,
                    proposed_fix="Persist one selected work item per note.",
                    decision=SelfImprovementDecision.FOLLOW_UP,
                    status=SelfImprovementStatus.SELECTED,
                )

            response = client.post(
                "/operational-memory/work-items",
                json={
                    "source_note_id": note.id,
                    "title": "Domknac selected-note review flow",
                    "summary": "Przelozyc selected note na jeden kontrolowany work item.",
                    "next_step": "Rozpisac backend-first API.",
                },
            )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["source_note_id"], note.id)
        self.assertEqual(payload["priority"], "P0")
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["review_state"], "unreviewed")
        self.assertIsNone(payload["blocked_reason"])
        self.assertEqual(payload["next_step"], "Rozpisac backend-first API.")

    def test_rejects_second_open_work_item_for_the_same_source_note(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                note = SelfImprovementNoteRepository(session).create(
                    source="manual_dogfooding",
                    view="chat",
                    friction="One note should not fan out into duplicate active work items.",
                    severity=SelfImprovementSeverity.P1,
                    proposed_fix=None,
                    decision=SelfImprovementDecision.UNDECIDED,
                    status=SelfImprovementStatus.SELECTED,
                )

            first = client.post(
                "/operational-memory/work-items",
                json={
                    "source_note_id": note.id,
                    "title": "Pierwszy kandydat",
                    "summary": "Pierwszy work item dla notatki.",
                },
            )
            second = client.post(
                "/operational-memory/work-items",
                json={
                    "source_note_id": note.id,
                    "title": "Drugi kandydat",
                    "summary": "Nie powinien przejsc jako drugi open item.",
                },
            )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 400)
        self.assertIn("already maps to open work item", second.json()["detail"])

    def test_patch_work_item_status_and_triage_metadata(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Operational Memory Project")

            created = client.post(
                "/operational-memory/work-items",
                json={
                    "project_id": project.id,
                    "title": "Domknac review surface",
                    "summary": "Spokojny review flow potrzebuje osobnego next step.",
                    "priority": "P1",
                },
            )
            work_item_id = created.json()["id"]

            blocked = client.patch(
                f"/operational-memory/work-items/{work_item_id}",
                json={
                    "status": "blocked",
                    "next_step": "Czekac na decyzje produktowa.",
                    "review_state": "in_review",
                    "blocked_reason": "Czekamy na decyzje o zakresie v2.",
                },
            )
            done = client.patch(
                f"/operational-memory/work-items/{work_item_id}",
                json={
                    "status": "done",
                    "next_step": None,
                    "review_state": "reviewed",
                },
            )

        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(blocked.json()["status"], "blocked")
        self.assertEqual(blocked.json()["review_state"], "in_review")
        self.assertEqual(blocked.json()["blocked_reason"], "Czekamy na decyzje o zakresie v2.")
        self.assertEqual(blocked.json()["next_step"], "Czekac na decyzje produktowa.")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["status"], "done")
        self.assertEqual(done.json()["review_state"], "reviewed")
        self.assertIsNone(done.json()["blocked_reason"])
        self.assertIsNone(done.json()["next_step"])

    def test_rejects_blocked_reason_when_item_is_not_blocked(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Operational Memory Guardrail")

            created = client.post(
                "/operational-memory/work-items",
                json={
                    "project_id": project.id,
                    "title": "Nie mieszac blocked metadata z active item",
                    "summary": "Blocked reason ma byc tylko dla blocked status.",
                },
            )
            work_item_id = created.json()["id"]

            response = client.patch(
                f"/operational-memory/work-items/{work_item_id}",
                json={"blocked_reason": "To nie powinno przejsc dla active item."},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("blocked_reason can only be set when status is blocked", response.json()["detail"])

    def test_rejects_explicit_null_review_state(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Operational Memory Validation")

            created = client.post(
                "/operational-memory/work-items",
                json={
                    "project_id": project.id,
                    "title": "Validation candidate",
                    "summary": "Review state null should be rejected.",
                },
            )
            work_item_id = created.json()["id"]

            response = client.patch(
                f"/operational-memory/work-items/{work_item_id}",
                json={"review_state": None},
            )

        self.assertEqual(response.status_code, 422)

    def test_snapshot_returns_work_items_and_project_decisions(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Snapshot Project")
                note = SelfImprovementNoteRepository(session).create(
                    source="manual_dogfooding",
                    view="self-improvement",
                    friction="Work item powinien pokazywac zrodlo review note bez zgadywania.",
                    severity=SelfImprovementSeverity.P1,
                    proposed_fix="Dodac source note context do snapshotu operational memory.",
                    decision=SelfImprovementDecision.FOLLOW_UP,
                    status=SelfImprovementStatus.SELECTED,
                )
                conversation = ConversationRepository(session).create(
                    title="Snapshot conversation",
                    project_id=project.id,
                )
                message = MessageRepository(session).create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Ustalamy, ze najpierw robimy backend-first operational memory.",
                )
                DecisionLogRepository(session).create(
                    conversation_id=conversation.id,
                    project_id=project.id,
                    kind=DecisionLogKind.DECISION,
                    summary="Najpierw backend-first operational memory.",
                    made_at=datetime.now(UTC),
                    status=DecisionLogStatus.ACTIVE,
                    due_at=None,
                    resolved_at=None,
                    resolution_summary=None,
                    evidence_message_ids=[message.id],
                )
                DecisionLogRepository(session).create(
                    conversation_id=conversation.id,
                    project_id=project.id,
                    kind=DecisionLogKind.PENDING_ACTION,
                    summary="Potem dodac spokojny review drill-down dla decyzji.",
                    made_at=datetime.now(UTC),
                    status=DecisionLogStatus.ACTIVE,
                    due_at=None,
                    resolved_at=None,
                    resolution_summary=None,
                    evidence_message_ids=[message.id],
                )
                DecisionLogRepository(session).create(
                    conversation_id=conversation.id,
                    project_id=project.id,
                    kind=DecisionLogKind.COMMITMENT,
                    summary="Nie mieszac review surface z execution semantics.",
                    made_at=datetime.now(UTC),
                    status=DecisionLogStatus.ACTIVE,
                    due_at=None,
                    resolved_at=None,
                    resolution_summary=None,
                    evidence_message_ids=[message.id],
                )

            created = client.post(
                "/operational-memory/work-items",
                json={
                    "project_id": project.id,
                    "source_note_id": note.id,
                    "title": "Zapisac work item store",
                    "summary": "Nowa tabela ma trzymac stan pracy bez execution semantics.",
                    "priority": "P1",
                },
            )
            snapshot = client.get(f"/operational-memory/snapshot?project_id={project.id}")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(snapshot.status_code, 200)
        payload = snapshot.json()
        self.assertEqual(payload["project_id"], project.id)
        self.assertEqual(len(payload["work_items"]), 1)
        self.assertEqual(payload["work_items"][0]["title"], "Zapisac work item store")
        self.assertEqual(payload["work_items"][0]["review_state"], "unreviewed")
        self.assertIsNone(payload["work_items"][0]["blocked_reason"])
        self.assertEqual(
            payload["work_items"][0]["source_note"]["friction"],
            "Work item powinien pokazywac zrodlo review note bez zgadywania.",
        )
        self.assertEqual(
            payload["work_items"][0]["decision_context"]["active_count"],
            3,
        )
        self.assertEqual(
            payload["work_items"][0]["decision_context"]["latest_summary"],
            "Nie mieszac review surface z execution semantics.",
        )
        self.assertEqual(
            len(payload["work_items"][0]["decision_context"]["recent_entries"]),
            3,
        )
        self.assertEqual(
            payload["work_items"][0]["decision_context"]["recent_entries"][0]["summary"],
            "Nie mieszac review surface z execution semantics.",
        )
        self.assertEqual(
            payload["work_items"][0]["decision_context"]["recent_entries"][2]["summary"],
            "Najpierw backend-first operational memory.",
        )
        self.assertEqual(len(payload["active_decisions"]), 3)
        self.assertEqual(
            payload["active_decisions"][0]["summary"],
            "Nie mieszac review surface z execution semantics.",
        )

    def test_promotion_preview_returns_selected_note_and_derived_draft(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Promotion Preview Project")
                note = SelfImprovementNoteRepository(session).create(
                    source="manual_dogfooding",
                    view="self-improvement",
                    friction="Nie da sie latwo przeniesc selected note do stanu pracy projektu.",
                    severity=SelfImprovementSeverity.P1,
                    proposed_fix="Dodac kontrolowane promotion path z selected note do work item.",
                    decision=SelfImprovementDecision.FOLLOW_UP,
                    status=SelfImprovementStatus.SELECTED,
                )
                note.chosen_next_patch = "Domknac manual promotion path do Operational Memory"
                session.add(note)

            response = client.post(
                "/operational-memory/promotions/preview",
                json={"project_id": project.id},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["eligible"])
        self.assertEqual(payload["project_id"], project.id)
        self.assertEqual(payload["note"]["id"], note.id)
        self.assertEqual(
            payload["work_item_draft"]["title"],
            "Domknac manual promotion path do Operational Memory",
        )
        self.assertEqual(
            payload["work_item_draft"]["next_step"],
            "Dodac kontrolowane promotion path z selected note do work item.",
        )
        self.assertEqual(payload["work_item_draft"]["priority"], "P1")

    def test_promotion_preview_returns_calm_reason_when_no_selected_note_exists(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Promotion No Selection Project")

            response = client.post(
                "/operational-memory/promotions/preview",
                json={"project_id": project.id},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["eligible"])
        self.assertIsNone(payload["note"])
        self.assertIsNone(payload["work_item_draft"])
        self.assertIn("Brak wybranej notatki", payload["reason"])

    def test_promotes_selected_note_to_work_item_for_project(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Promotion Project")
                note = SelfImprovementNoteRepository(session).create(
                    source="manual_dogfooding",
                    view="settings",
                    friction="Selected note powinna przechodzic do work item bez execution semantics.",
                    severity=SelfImprovementSeverity.P0,
                    proposed_fix="Dodac review-only promotion flow.",
                    decision=SelfImprovementDecision.ACCEPTED,
                    status=SelfImprovementStatus.SELECTED,
                )

            created = client.post(
                "/operational-memory/promotions",
                json={"project_id": project.id},
            )
            duplicate = client.post(
                "/operational-memory/promotions",
                json={"project_id": project.id},
            )

        self.assertEqual(created.status_code, 201)
        payload = created.json()
        self.assertEqual(payload["project_id"], project.id)
        self.assertEqual(payload["source_note_id"], note.id)
        self.assertEqual(payload["priority"], "P0")
        self.assertEqual(payload["status"], "active")
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("already maps to open work item", duplicate.json()["detail"])

    def test_rejects_promotion_for_note_that_is_not_selected(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Promotion Guardrail Project")
                note = SelfImprovementNoteRepository(session).create(
                    source="manual_dogfooding",
                    view="chat",
                    friction="Open note nie jest jeszcze kandydatem do promotion.",
                    severity=SelfImprovementSeverity.P2,
                    proposed_fix=None,
                    decision=SelfImprovementDecision.UNDECIDED,
                    status=SelfImprovementStatus.OPEN,
                )

            response = client.post(
                "/operational-memory/promotions/preview",
                json={"project_id": project.id, "note_id": note.id},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("selected improvement note", response.json()["detail"])

    def test_selected_note_promotion_stays_non_executing(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Promotion Non Executing")
                note = SelfImprovementNoteRepository(session).create(
                    source="manual_dogfooding",
                    view="documents",
                    friction="Promotion ma zostac tylko review metadata.",
                    severity=SelfImprovementSeverity.P1,
                    proposed_fix="Tworzyc tylko work item.",
                    decision=SelfImprovementDecision.FOLLOW_UP,
                    status=SelfImprovementStatus.SELECTED,
                )
                before_runs = session.query(ExecutionRun).count()
                before_traces = session.query(ExecutionTrace).count()
                before_drafts = session.query(AccountActionDraft).count()

            response = client.post(
                "/operational-memory/promotions",
                json={"project_id": project.id, "note_id": note.id},
            )

            with self.app.state.database.session_scope() as session:
                after_runs = session.query(ExecutionRun).count()
                after_traces = session.query(ExecutionTrace).count()
                after_drafts = session.query(AccountActionDraft).count()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(before_runs, after_runs)
        self.assertEqual(before_traces, after_traces)
        self.assertEqual(before_drafts, after_drafts)

    def test_operational_memory_stays_non_executing(self) -> None:
        with TestClient(self.app) as client:
            with self.app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Non executing memory")
                before_runs = session.query(ExecutionRun).count()
                before_traces = session.query(ExecutionTrace).count()
                before_drafts = session.query(AccountActionDraft).count()

            response = client.post(
                "/operational-memory/work-items",
                json={
                    "project_id": project.id,
                    "title": "Zapisac tylko stan pracy",
                    "summary": "Operational memory nie ma uruchamiac taskow ani draftow.",
                },
            )

            with self.app.state.database.session_scope() as session:
                after_runs = session.query(ExecutionRun).count()
                after_traces = session.query(ExecutionTrace).count()
                after_drafts = session.query(AccountActionDraft).count()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(before_runs, after_runs)
        self.assertEqual(before_traces, after_traces)
        self.assertEqual(before_drafts, after_drafts)


if __name__ == "__main__":
    unittest.main()
