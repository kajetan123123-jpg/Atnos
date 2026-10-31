from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from atnos.providers.base import ProviderExecutionResult
from tests.test_support import bootstrap_test_app, create_test_app, make_temp_dir


def test_chat_basic_responds() -> None:
    temp_dir = make_temp_dir()
    try:
        app = create_test_app(Path(temp_dir.name))
        bootstrap_test_app(app)
        try:
            provider = app.state.providers.get("ollama-local")
            assert provider is not None

            with TestClient(app) as client, patch.object(
                provider,
                "execute",
                return_value=ProviderExecutionResult(content="Smoke OK: chat odpowiada."),
            ):
                response = client.post("/chat", json={"content": "Odpowiedz jednym zdaniem: smoke test."})

            assert response.status_code == 200
            payload = response.json()
            assert payload["conversation_id"] > 0
            assert "Smoke OK" in payload["assistant_message"]
        finally:
            app.state.database.dispose()
    finally:
        temp_dir.cleanup()
