from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.db.models.enums import DecisionLogKind, DecisionLogStatus
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.runtime_control_settings_repository import RuntimeControlSettingsRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.proactive_briefing_service import ProactiveBriefingService


class ProactiveBriefingServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_should_emit_on_bootstrap_respects_quiet_window(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                owner = UserRepository(session).get_owner()
                assert owner is not None
                controls_repo = RuntimeControlSettingsRepository(session)
                controls = controls_repo.get_for_user(owner.id)
                assert controls is not None
                controls_repo.update(
                    controls,
                    last_user_interaction_at=datetime.now(UTC) - timedelta(hours=9),
                    last_briefing_at=datetime.now(UTC) - timedelta(days=1),
                )
                service = ProactiveBriefingService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                self.assertTrue(service.should_emit_on_bootstrap())

                controls_repo.update(
                    controls,
                    last_user_interaction_at=datetime.now(UTC) - timedelta(hours=1),
                    last_briefing_at=datetime.now(UTC),
                )
                self.assertFalse(service.should_emit_on_bootstrap())

    def test_chat_briefing_endpoint_returns_structured_briefing_payload(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Projekt briefing")
                conversation = ConversationRepository(session).create(
                    title="Sprint follow-up",
                    project_id=project.id,
                )
                ConversationRepository(session).update_summary(
                    conversation,
                    "Trzeba domknac review i raport sprintu.",
                )
                project.summary = "Dzis konczymy review i update roadmapy."
                service = ProactiveBriefingService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                service.briefing.decisions.decisions.create(
                    conversation_id=conversation.id,
                    project_id=project.id,
                    kind=DecisionLogKind.COMMITMENT,
                    summary="Dowiezc raport sprintu.",
                    made_at=datetime.now(UTC),
                    status=DecisionLogStatus.ACTIVE,
                    due_at=datetime.now(UTC) + timedelta(hours=6),
                    resolved_at=None,
                    resolution_summary=None,
                    evidence_message_ids=[501],
                )

            with (
                patch(
                    "atnos.services.github_connector_service.GitHubConnectorService.is_connected",
                    return_value=False,
                ),
                patch(
                    "atnos.services.linear_connector_service.LinearConnectorService.is_connected",
                    return_value=False,
                ),
            ):
                response = client.post(f"/chat/briefing?project_id={project.id}")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["metadata_json"]["kind"], "briefing")
            briefing_payload = payload["metadata_json"]["payload"]
            self.assertEqual(briefing_payload["project_id"], project.id)
            section_kinds = [section["kind"] for section in briefing_payload["sections"]]
            self.assertIn("decisions", section_kinds)
            self.assertIn("project_memory", section_kinds)
            self.assertIn("Decyzje i commitments", payload["content"])

    def test_bootstrap_creates_briefing_conversation_after_idle_gap(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with app.state.database.session_scope() as session:
                owner = UserRepository(session).get_owner()
                assert owner is not None
                controls_repo = RuntimeControlSettingsRepository(session)
                controls = controls_repo.get_for_user(owner.id)
                assert controls is not None
                controls_repo.update(
                    controls,
                    last_user_interaction_at=datetime.now(UTC) - timedelta(hours=9),
                    last_briefing_at=None,
                )

            response = client.get("/ui/bootstrap")
            self.assertEqual(response.status_code, 200)
            titles = [item["title"] for item in response.json()["recent_conversations"]]
            self.assertTrue(any("Briefing" in title for title in titles))
