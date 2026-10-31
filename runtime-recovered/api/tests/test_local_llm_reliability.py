from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, create_test_app, make_temp_dir

from atnos.db.models.enums import ExecutionTraceStatus, MessageStatus, ModelTask
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.db.models.message import Message
from atnos.providers.base import (
    LocalFailureType,
    LocalProviderRuntimeError,
    ProviderHealthCheck,
    build_local_failure_detail,
)
from atnos.providers.llamacpp_local import LlamaCppLocalProviderAdapter
from atnos.providers.ollama_local import OllamaLocalProviderAdapter
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.context_builder_service import ProviderExecutionContext
from atnos.services.provider_health_cache_service import ProviderHealthCacheService


class SummaryWritebackProbe:
    calls = 0

    def __init__(self, session) -> None:
        self.session = session

    def refresh_after_turn(self, **kwargs):
        type(self).calls += 1
        raise AssertionError("summary writeback should not run for failed local-runtime turns")


def make_provider_context(prompt: str = "Hej Atnos") -> ProviderExecutionContext:
    return ProviderExecutionContext(
        provider_prompt=prompt,
        summary="test-context",
        visible_attachments=tuple(),
        local_attachments=tuple(),
        allow_external_attachment_content=False,
        allow_external_attachment_metadata=False,
        project_context_applied=False,
        active_document_attachment_id=None,
        provider_prompt_chars=len(prompt),
        active_document_preview_chars=0,
        active_document_preview_limit=None,
        selected_review_note_id=None,
        capability_snapshot=None,
    )


def make_model_config(
    *,
    key: str = "ollama-local/chat-default",
    runtime_model_name: str = "atnos-core",
    max_output_tokens: int = 256,
):
    return SimpleNamespace(
        key=key,
        runtime_model_name=runtime_model_name,
        max_output_tokens=max_output_tokens,
    )


class LocalLlmReliabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        ProviderHealthCacheService._cache.clear()
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    @staticmethod
    def _without_cloud_provider_keys():
        return patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
            },
            clear=False,
        )

    def test_task_aware_timeouts(self) -> None:
        settings = build_test_settings(self.root)
        ollama = OllamaLocalProviderAdapter(app_root=self.root, settings=settings)
        llamacpp = LlamaCppLocalProviderAdapter(app_root=self.root)

        self.assertEqual(
            ollama.resolve_request_timeout_s(task=ModelTask.GENERAL_CHAT, prompt_chars=1200),
            180,
        )
        self.assertEqual(
            ollama.resolve_request_timeout_s(task=ModelTask.ORCHESTRATION, prompt_chars=1200),
            180,
        )
        self.assertEqual(
            ollama.resolve_request_timeout_s(task=ModelTask.SYNTHESIS, prompt_chars=1200),
            240,
        )
        self.assertEqual(
            ollama.resolve_request_timeout_s(task=ModelTask.VISION, prompt_chars=1200),
            180,
        )
        self.assertEqual(
            ollama.resolve_request_timeout_s(task=ModelTask.GENERAL_CHAT, prompt_chars=9501),
            270,
        )
        self.assertEqual(
            llamacpp.resolve_request_timeout_s(task=ModelTask.GENERAL_CHAT, prompt_chars=9501),
            105,
        )

    def test_retry_once_on_timeout(self) -> None:
        settings = build_test_settings(self.root)
        adapter = OllamaLocalProviderAdapter(app_root=self.root, settings=settings)
        provider = SimpleNamespace(endpoint_url="http://127.0.0.1:11434")
        model_config = make_model_config()
        context = make_provider_context()
        timeout_detail = build_local_failure_detail(
            failure_type=LocalFailureType.REQUEST_TIMEOUT,
            provider_key="ollama-local",
            provider_label="Ollama Local",
            model_key=model_config.key,
            runtime_model_name=model_config.runtime_model_name,
            diagnostics={"timeout_ms": 45_000},
        )

        with patch.object(
            adapter,
            "_discover_models_via_http",
            return_value=["atnos-core"],
        ), patch.object(
            adapter,
            "_request_generate",
            side_effect=[
                LocalProviderRuntimeError(timeout_detail),
                {
                    "response": "Odpowiedz po retry.",
                    "created_at": "2026-05-13T10:00:00Z",
                    "model": "atnos-core",
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 20,
                },
            ],
        ) as request_mock, patch.object(
            adapter,
            "check_health",
            return_value=ProviderHealthCheck(
                status="healthy",
                status_reason="runtime healthy",
                installed_models=("atnos-core",),
            ),
        ) as health_mock:
            result = adapter.execute(
                task=ModelTask.GENERAL_CHAT,
                context=context,
                provider=provider,
                model_config=model_config,
            )

        self.assertEqual(result.content, "Odpowiedz po retry.")
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(health_mock.call_count, 1)

    def test_no_retry_on_unreachable(self) -> None:
        adapter = LlamaCppLocalProviderAdapter(app_root=self.root)
        provider = SimpleNamespace(endpoint_url="http://127.0.0.1:8080")
        model_config = make_model_config(
            key="llamacpp-local/chat-default",
            runtime_model_name="qwen3-8b-instruct",
        )
        context = make_provider_context()
        unreachable_detail = build_local_failure_detail(
            failure_type=LocalFailureType.RUNTIME_UNREACHABLE,
            provider_key="llamacpp-local",
            provider_label="llama.cpp Local",
            model_key=model_config.key,
            runtime_model_name=model_config.runtime_model_name,
        )

        with patch.object(
            adapter,
            "_request_chat_completion",
            side_effect=LocalProviderRuntimeError(unreachable_detail),
        ) as request_mock, patch.object(
            adapter,
            "check_health",
            return_value=ProviderHealthCheck(
                status="unreachable",
                status_reason="offline",
            ),
        ) as health_mock:
            with self.assertRaises(LocalProviderRuntimeError) as ctx:
                adapter.execute(
                    task=ModelTask.GENERAL_CHAT,
                    context=context,
                    provider=provider,
                    model_config=model_config,
                )

        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual(health_mock.call_count, 1)
        self.assertFalse(ctx.exception.failure_detail.diagnostics["retry_attempted"])

    def test_health_refresh_after_timeout(self) -> None:
        SummaryWritebackProbe.calls = 0
        with self._without_cloud_provider_keys():
            app = create_test_app(self.root)
            app.state.summary_writeback_factory = SummaryWritebackProbe
            self.addCleanup(app.state.database.dispose)
            adapter = app.state.providers.get("ollama-local")
            self.assertIsNotNone(adapter)

            timeout_detail = build_local_failure_detail(
                failure_type=LocalFailureType.REQUEST_TIMEOUT,
                provider_key="ollama-local",
                provider_label="Ollama Local",
                model_key="ollama-local/chat-default",
                runtime_model_name="atnos-core",
                diagnostics={"timeout_ms": 45_000},
            )

            with TestClient(app) as client:
                with app.state.database.session_scope() as session:
                    owner = UserRepository(session).get_owner()
                    self.assertIsNotNone(owner)
                    policy = ExecutionPolicyRepository(session).get_for_user(owner.id)
                    self.assertIsNotNone(policy)
                    ExecutionPolicyRepository(session).update(
                        policy,
                        allow_external_fallback=True,
                    )

                with patch.object(
                    adapter,
                    "execute",
                    side_effect=LocalProviderRuntimeError(timeout_detail),
                ), patch.object(
                    adapter,
                    "check_health",
                    return_value=ProviderHealthCheck(
                        status="healthy",
                        status_reason="runtime healthy",
                        installed_models=("atnos-core", "qwen3:8b"),
                    ),
                ):
                    response = client.post("/chat", json={"content": "Napisz odpowiedz lokalnie"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        runtime_error = payload["runtime_error"]
        self.assertEqual(runtime_error["failure_type"], "request_timeout")
        self.assertEqual(runtime_error["diagnostics"]["health_status"], "healthy")
        self.assertTrue(runtime_error["diagnostics"]["model_present"])
        self.assertTrue(runtime_error["can_fallback"])
        self.assertEqual(SummaryWritebackProbe.calls, 0)

    def test_failed_turn_not_saved_as_success(self) -> None:
        SummaryWritebackProbe.calls = 0
        with self._without_cloud_provider_keys():
            app = create_test_app(self.root)
            app.state.summary_writeback_factory = SummaryWritebackProbe
            self.addCleanup(app.state.database.dispose)
            adapter = app.state.providers.get("ollama-local")
            self.assertIsNotNone(adapter)

            failure_detail = build_local_failure_detail(
                failure_type=LocalFailureType.RUNTIME_UNREACHABLE,
                provider_key="ollama-local",
                provider_label="Ollama Local",
                model_key="ollama-local/chat-default",
                runtime_model_name="atnos-core",
            )

            with TestClient(app) as client, patch.object(
                adapter,
                "execute",
                side_effect=LocalProviderRuntimeError(failure_detail),
            ), patch.object(
                adapter,
                "check_health",
                return_value=ProviderHealthCheck(
                    status="unreachable",
                    status_reason="offline",
                    installed_models=(),
                ),
            ):
                response = client.post("/chat", json={"content": "Napisz jedno zdanie o lokalnym runtime."})
                self.assertEqual(response.status_code, 200)
                conversation_id = response.json()["conversation_id"]

        with app.state.database.session_scope() as session:
            messages = list(
                session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.sequence_number.asc())
                )
            )
            traces = list(
                session.scalars(
                    select(ExecutionTrace)
                    .where(ExecutionTrace.conversation_id == conversation_id)
                    .order_by(ExecutionTrace.created_at.asc())
                )
            )

        assistant_message = messages[-1]
        self.assertEqual(assistant_message.role.value, "assistant")
        self.assertEqual(assistant_message.status, MessageStatus.FAILED)
        self.assertEqual(assistant_message.content, "")
        self.assertIn("runtime_error", assistant_message.metadata_json)
        self.assertEqual(SummaryWritebackProbe.calls, 0)

        placeholder_trace = next(
            trace for trace in traces if trace.step_name == "assistant-response-placeholder"
        )
        self.assertEqual(placeholder_trace.status, ExecutionTraceStatus.FAILED)
        provider_trace = next(trace for trace in traces if trace.step_name == "provider-execution")
        self.assertEqual(provider_trace.status, ExecutionTraceStatus.FAILED)
        self.assertEqual(provider_trace.metadata_json["failure_type"], "runtime_unreachable")

    def test_runtime_error_shape_matches_frontend(self) -> None:
        with self._without_cloud_provider_keys():
            app = create_test_app(self.root)
            self.addCleanup(app.state.database.dispose)
            adapter = app.state.providers.get("ollama-local")
            self.assertIsNotNone(adapter)

            failure_detail = build_local_failure_detail(
                failure_type=LocalFailureType.RUNTIME_BUSY,
                provider_key="ollama-local",
                provider_label="Ollama Local",
                model_key="ollama-local/chat-default",
                runtime_model_name="atnos-core",
                diagnostics={"timeout_ms": 45_000},
            )

            with TestClient(app) as client, patch.object(
                adapter,
                "execute",
                side_effect=LocalProviderRuntimeError(failure_detail),
            ), patch.object(
                adapter,
                "check_health",
                return_value=ProviderHealthCheck(
                    status="healthy",
                    status_reason="runtime healthy",
                    installed_models=("atnos-core",),
                ),
            ):
                response = client.post("/chat", json={"content": "Napisz jedno zdanie o lokalnym runtime."})

        self.assertEqual(response.status_code, 200)
        runtime_error = response.json()["runtime_error"]
        self.assertEqual(
            set(runtime_error.keys()),
            {
                "failure_type",
                "provider_key",
                "model_key",
                "runtime_model_name",
                "user_title",
                "user_detail",
                "retryable",
                "can_fallback",
                "suggested_actions",
                "diagnostics",
            },
        )
        self.assertIsInstance(runtime_error["suggested_actions"], list)
        self.assertIsInstance(runtime_error["diagnostics"], dict)

    def test_configured_gemini_fallback_completes_after_local_runtime_failure(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        adapter = app.state.providers.get("ollama-local")
        self.assertIsNotNone(adapter)

        failure_detail = build_local_failure_detail(
            failure_type=LocalFailureType.RUNTIME_UNREACHABLE,
            provider_key="ollama-local",
            provider_label="Ollama Local",
            model_key="ollama-local/chat-default",
            runtime_model_name="atnos-core",
        )

        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "test-gemini-key",
            },
            clear=False,
        ), TestClient(app) as client, patch.object(
            adapter,
            "execute",
            side_effect=LocalProviderRuntimeError(failure_detail),
        ), patch.object(
            adapter,
            "check_health",
            return_value=ProviderHealthCheck(
                status="healthy",
                status_reason="runtime initially healthy",
                installed_models=("atnos-core",),
            ),
        ):
            response = client.post("/chat", json={"content": "Napisz jedno zdanie o lokalnym runtime."})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["runtime_error"])
        self.assertEqual(payload["selected_provider_key"], "gemini-cloud")
        self.assertIn("prze", payload["assistant_message"].lower())
        self.assertIn("gemini-cloud", payload["assistant_message"])
