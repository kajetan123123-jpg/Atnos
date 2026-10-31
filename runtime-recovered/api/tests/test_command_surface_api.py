from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient


TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.db.models.enums import ModelTask
from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.repositories.model_config_repository import ModelConfigRepository
from atnos.services.runtime_inventory_service import (
    RuntimeInventorySnapshot,
    RuntimeInventorySummary,
    RuntimeProviderInventoryItem,
    RuntimeVoiceSttInventoryItem,
)


class CommandSurfaceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_resume_agent_execution_endpoint_restarts_paused_run(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with app.state.database.session_scope() as session:
                run = ExecutionRunRepository(session).create_run(
                    project_id=1,
                    scheduler_job_id=None,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="agent.browser_recipe",
                    status="paused",
                    goal="Paused browser recipe",
                    connector_key="browser",
                    platform_key="browser",
                    risk_level="medium",
                    input_json={"step": "navigate"},
                    output_json={"pause_requested": True},
                    artifacts_json={},
                    error_message=None,
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                )
                run_id = run.id
                session.commit()

            response = client.post(f"/agent/executions/{run_id}/resume")

            self.assertEqual(response.status_code, 200)
            payload = response.json()["run"]
            self.assertEqual(payload["status"], "running")
            self.assertNotIn("pause_requested", payload["output_json"])
            self.assertIsNone(payload["finished_at"])

            with app.state.database.session_scope() as session:
                updated = ExecutionRunRepository(session).get_run(run_id)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.status, "running")
                self.assertNotIn("pause_requested", dict(updated.output_json or {}))
                self.assertIsNone(updated.finished_at)

    def test_provider_card_probe_accepts_draft_secret_and_maps_rate_limit(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        response_429 = httpx.Response(
            429,
            headers={"retry-after": "17"},
            request=httpx.Request("GET", "https://api.openai.com/v1/models"),
        )

        with patch("atnos.services.ui_adapter_service.httpx.Client") as mocked_client:
            mocked_client.return_value.__enter__.return_value.get.return_value = response_429

            with TestClient(app) as client:
                response = client.post(
                    "/settings/provider-cards/openai/test",
                    json={"api_key": "sk-test-1234567890abcdef"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "rate_limited")
        self.assertEqual(payload["connection_state"], "rate_limited")
        self.assertEqual(payload["reset_in_sec"], 17)
        self.assertIsInstance(payload["tested_at"], str)

        mocked_client.return_value.__enter__.return_value.get.assert_called_once()
        _, kwargs = mocked_client.return_value.__enter__.return_value.get.call_args
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer sk-test-1234567890abcdef",
        )

    def test_local_chat_model_switch_updates_local_general_chat_runtime_alias(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        snapshot = RuntimeInventorySnapshot(
            providers=(
                RuntimeProviderInventoryItem(
                    provider_id=1,
                    provider_key="ollama-local",
                    label="Ollama Local",
                    kind="local_llm",
                    local_first=True,
                    is_enabled=True,
                    endpoint_url="http://localhost:11434",
                    has_runtime_adapter=True,
                    adapter_capabilities=("chat",),
                    configured_capabilities=("chat",),
                    status="ready",
                    status_reason="ok",
                    health_status="healthy",
                    health_reason="ok",
                    installed_models=("llama3", "mistral"),
                    health_latency_ms=12,
                ),
            ),
            models=(),
            tasks=(),
            summary=RuntimeInventorySummary(
                ready_providers=1,
                enabled_models=1,
                tasks_ready=1,
                has_local_chat=True,
                has_external_fallback=True,
            ),
            voice_stt=RuntimeVoiceSttInventoryItem(
                configured_engine="browser-speech",
                configured_model_path=None,
                configured_device="cpu",
                effective_engine="browser-speech",
                status="browser_fallback",
                status_reason="ok",
                dependency_available=True,
                model_path_exists=None,
            ),
        )

        with patch(
            "atnos.api.routes.settings.RuntimeInventoryService.snapshot",
            return_value=snapshot,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/settings/runtime/local-chat-model",
                    json={"runtime_model_name": "llama3"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["runtime_model_name"], "llama3")

        with app.state.database.session_scope() as session:
            models = ModelConfigRepository(session).list_enabled_for_task(ModelTask.GENERAL_CHAT)
            local_chat = next(
                model
                for model in models
                if model.provider is not None and model.provider.key == "ollama-local"
            )
            self.assertEqual(local_chat.runtime_model_name, "llama3")


if __name__ == "__main__":
    unittest.main()
