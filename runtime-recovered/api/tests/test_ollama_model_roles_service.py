from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.db.models.enums import ModelTask
from atnos.providers.base import LocalFailureType, LocalProviderRuntimeError
from atnos.providers.ollama_local import OllamaLocalProviderAdapter
from atnos.services.context_builder_service import LocalAttachmentMetadata
from atnos.services.ollama_model_roles_service import OllamaModelRolesService


class OllamaModelRolesServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.settings.ollama_main_model = "qwen3:14b"
        self.settings.ollama_chat_model = "qwen3:14b"
        self.settings.ollama_fast_model = "qwen3:8b"
        self.settings.ollama_vision_model = "qwen3-vl:8b"

    def test_ready_when_main_and_vision_are_installed(self) -> None:
        snapshot = OllamaModelRolesService(self.settings).probe(
            installed_models=("qwen3:14b", "qwen3:8b", "qwen3-vl:8b"),
        )

        self.assertEqual(snapshot.status, "ready")
        self.assertTrue(snapshot.ready)
        self.assertEqual(snapshot.active_text_model, "qwen3:14b")
        self.assertEqual(snapshot.active_vision_model, "qwen3-vl:8b")

    def test_fallback_only_when_main_is_missing_but_fast_exists(self) -> None:
        snapshot = OllamaModelRolesService(self.settings).probe(
            installed_models=("qwen3:8b", "qwen3-vl:8b"),
        )

        self.assertEqual(snapshot.status, "fallback_only")
        self.assertFalse(snapshot.ready)
        self.assertEqual(snapshot.active_text_model, "qwen3:8b")
        self.assertIn("qwen3:14b", snapshot.missing_models)

    def test_missing_vision_model_blocks_vision_role(self) -> None:
        snapshot = OllamaModelRolesService(self.settings).probe(
            installed_models=("qwen3:14b", "qwen3:8b"),
        )

        self.assertEqual(snapshot.status, "missing_vision_model")
        self.assertFalse(snapshot.ready)
        self.assertIsNone(snapshot.active_vision_model)

    def test_write_status_persists_json_without_secrets(self) -> None:
        service = OllamaModelRolesService(self.settings)
        snapshot = service.probe(installed_models=("qwen3:8b", "qwen3-vl:8b"))

        target = service.write_status(snapshot)
        payload = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "fallback_only")
        self.assertEqual(payload["main_text_model"], "qwen3:14b")
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_pull_missing_models_calls_ollama_pull_endpoint_for_missing_roles(self) -> None:
        service = OllamaModelRolesService(self.settings)
        snapshot = service.probe(installed_models=("qwen3:8b", "qwen3-vl:8b"))
        calls: list[dict[str, object]] = []

        class _Response:
            content = b'{"status":"success"}'

            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict[str, object]:
                return {"status": "success"}

        class _Client:
            def __init__(self, *, timeout: int) -> None:
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def post(self, url: str, *, json: dict[str, object]):
                calls.append({"url": url, "json": json, "timeout": self.timeout})
                return _Response()

        with patch("atnos.services.ollama_model_roles_service.httpx.Client", _Client):
            result = service.pull_missing_models(snapshot)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(calls, [
            {
                "url": "http://127.0.0.1:11434/api/pull",
                "json": {"name": "qwen3:14b", "stream": False},
                "timeout": 900,
            }
        ])

    def test_pull_missing_models_reports_unreachable_without_attempting_pull(self) -> None:
        service = OllamaModelRolesService(self.settings)
        snapshot = service.probe(installed_models=())
        object.__setattr__(snapshot, "status", "unreachable")

        result = service.pull_missing_models(snapshot)

        self.assertEqual(result["status"], "unreachable")
        self.assertEqual(result["results"], {})

    def test_ollama_adapter_uses_fast_fallback_for_missing_text_main(self) -> None:
        adapter = OllamaLocalProviderAdapter(app_root=self.root, settings=self.settings)
        model_config = type(
            "_ModelConfig",
            (),
            {
                "key": "ollama-local/chat-default",
                "runtime_model_name": "qwen3:14b",
            },
        )()
        adapter._discover_models_via_http = lambda: ["qwen3:8b", "qwen3-vl:8b"]  # type: ignore[method-assign]

        resolved = adapter._resolve_model_name(model_config, task=ModelTask.GENERAL_CHAT)

        self.assertEqual(resolved, "qwen3:8b")

    def test_ollama_adapter_does_not_use_text_fallback_for_missing_vision(self) -> None:
        adapter = OllamaLocalProviderAdapter(app_root=self.root, settings=self.settings)
        model_config = type(
            "_ModelConfig",
            (),
            {
                "key": "ollama-local/vision-default",
                "runtime_model_name": "qwen3-vl:8b",
            },
        )()
        adapter._discover_models_via_http = lambda: ["qwen3:14b", "qwen3:8b"]  # type: ignore[method-assign]

        with self.assertRaises(LocalProviderRuntimeError):
            adapter._resolve_model_name(model_config, task=ModelTask.VISION)

    def test_ollama_adapter_posts_provider_requests_to_chat_endpoint(self) -> None:
        adapter = OllamaLocalProviderAdapter(app_root=self.root, settings=self.settings)
        captured: dict[str, object] = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json() -> dict[str, object]:
                return {"message": {"role": "assistant", "content": "Dziala"}, "model": "qwen3:14b"}

        class _Client:
            def __init__(self, *, timeout: float) -> None:
                captured["timeout"] = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def post(self, url: str, *, json: dict[str, object]):
                captured["url"] = url
                captured["payload"] = json
                return _Response()

        with patch("atnos.providers.ollama_local.httpx.Client", _Client):
            data = adapter._request_generate(
                base_url="http://127.0.0.1:11434",
                payload={
                    "model": "qwen3:14b",
                    "messages": [{"role": "user", "content": "test"}],
                    "stream": False,
                },
                timeout_s=12.0,
                model_key="ollama-local/chat-default",
                runtime_model_name="qwen3:14b",
            )

        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(data["message"], {"role": "assistant", "content": "Dziala"})

    def test_execute_builds_chat_payload_with_thinking_for_orchestration(self) -> None:
        adapter = OllamaLocalProviderAdapter(app_root=self.root, settings=self.settings)
        adapter._discover_models_via_http = lambda: ["qwen3:14b", "qwen3:8b", "qwen3-vl:8b"]  # type: ignore[method-assign]
        captured: dict[str, object] = {}

        def _fake_request(**kwargs):
            captured.update(kwargs)
            return {
                "message": {"role": "assistant", "content": "Plan gotowy."},
                "model": "qwen3:14b",
                "prompt_eval_count": 4,
                "eval_count": 6,
                "done_reason": "stop",
            }

        adapter._request_generate = _fake_request  # type: ignore[method-assign]
        result = adapter.execute(
            task=ModelTask.ORCHESTRATION,
            context=SimpleNamespace(
                provider_prompt="Zaplanuj kroki.",
                provider_prompt_chars=15,
                local_attachments=tuple(),
            ),
            provider=SimpleNamespace(endpoint_url="http://127.0.0.1:11434"),
            model_config=SimpleNamespace(
                key="ollama-local/orchestration-default",
                runtime_model_name="qwen3:14b",
                max_output_tokens=256,
            ),
        )

        payload = captured["payload"]
        assert isinstance(payload, dict)
        self.assertNotIn("prompt", payload)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Zaplanuj kroki."}])
        self.assertEqual(payload["think"], True)
        self.assertEqual(result.raw_metadata["transport"], "ollama_api_chat")
        self.assertEqual(result.raw_metadata["thinking_mode"], True)
        self.assertEqual(result.raw_metadata["ollama_main_requests_today"], 1)

    def test_execute_places_vision_images_inside_chat_message(self) -> None:
        adapter = OllamaLocalProviderAdapter(app_root=self.root, settings=self.settings)
        adapter._discover_models_via_http = lambda: ["qwen3:14b", "qwen3:8b", "qwen3-vl:8b"]  # type: ignore[method-assign]
        image_path = self.root / "screen.png"
        image_path.write_bytes(b"not-a-real-png-but-ok-for-base64")
        captured: dict[str, object] = {}

        def _fake_request(**kwargs):
            captured.update(kwargs)
            return {
                "message": {"role": "assistant", "content": "Widze interfejs."},
                "model": "qwen3-vl:8b",
            }

        adapter._request_generate = _fake_request  # type: ignore[method-assign]
        adapter.execute(
            task=ModelTask.VISION,
            context=SimpleNamespace(
                provider_prompt="Opisz screenshot.",
                provider_prompt_chars=17,
                local_attachments=(
                    LocalAttachmentMetadata(attachment_id=1, storage_path=str(image_path), checksum=None),
                ),
            ),
            provider=SimpleNamespace(endpoint_url="http://127.0.0.1:11434"),
            model_config=SimpleNamespace(
                key="ollama-local/vision-default",
                runtime_model_name="qwen3-vl:8b",
                max_output_tokens=256,
            ),
        )

        payload = captured["payload"]
        assert isinstance(payload, dict)
        messages = payload["messages"]
        assert isinstance(messages, list)
        # An optional grounding system prompt can precede the user message.
        # Images belong to the user message in Ollama's chat protocol.
        user_messages = [message for message in messages if message.get('role') == 'user']
        self.assertEqual(len(user_messages), 1)
        self.assertIn("images", user_messages[0])
        self.assertTrue(all('images' not in message for message in messages if message.get('role') == 'system'))
        self.assertEqual(payload["think"], False)
        self.assertEqual(payload["options"], {"num_predict": 256, "num_ctx": 8192, "temperature": 0.1, "top_p": 0.8})
        usage = json.loads(adapter.settings.llm_usage_file.read_text(encoding="utf-8"))
        self.assertEqual(usage["ollama_vision_requests_today"], 1)

    def test_context_size_error_is_classified(self) -> None:
        adapter = OllamaLocalProviderAdapter(app_root=self.root, settings=self.settings)

        result = adapter._classify_response_failure(
            status_code=400,
            error_message="request exceeds the available context size (4096 tokens)",
        )

        self.assertEqual(result, LocalFailureType.CONTEXT_TOO_LARGE)


if __name__ == "__main__":
    unittest.main()
