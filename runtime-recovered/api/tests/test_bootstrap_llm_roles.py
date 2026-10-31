from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.repositories.model_config_repository import ModelConfigRepository


def test_fresh_install_assigns_fast_chat_and_deep_orchestration_models() -> None:
    with make_temp_dir() as temporary:
        app = create_test_app(Path(temporary))
        try:
            # Settings historically fills CHAT from MAIN; FAST should still win
            # for a new local chat configuration.
            app.state.settings.ollama_main_model = "qwen3:14b"
            app.state.settings.ollama_chat_model = "qwen3:14b"
            app.state.settings.ollama_fast_model = "qwen3.5:9b"
            with TestClient(app):
                pass
            with app.state.database.session_scope() as session:
                models = ModelConfigRepository(session)
                chat = models.get_by_key("ollama-local/chat-default")
                orchestration = models.get_by_key("ollama-local/orchestration-default")
                assert chat is not None and orchestration is not None
                assert chat.runtime_model_name == "qwen3.5:9b"
                assert orchestration.runtime_model_name == "qwen3:14b"
        finally:
            app.state.database.dispose()
