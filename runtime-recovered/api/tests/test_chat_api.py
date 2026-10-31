from __future__ import annotations

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pydantic import ValidationError

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.browser_access_diagnostics_service import ChromeBrowserAccessDiagnosticsView
from atnos.services.creative_asset_operator_service import CreativeAssetOperatorService
from atnos.services.desktop_browser_observer_service import BrowserPageLearningView
from atnos.services.external_llm_operator_service import ExternalLlmExecutionOutcome
from atnos.services.external_llm_operator_service import ExternalLlmOperatorService
from atnos.services.chat_orchestration_service import ChatOrchestrationService
from atnos.api.routes.chat import ChatExecutionPreferencesInput
from atnos.services.local_host_control_service import LocalDesktopAppView, LocalHostCommandResultView
from atnos.services.local_video_generation_service import VideoGenerationResult
from atnos.services.runtime_settings_service import RuntimeSettingsService
from atnos.services.trusted_auto_service import TrustedAutoService
from atnos.providers.base import ProviderExecutionResult, ProviderUnavailableError
from agent.models import AgentRunResult, AgentRunStatus, AgentTaskType, RiskLevel, VerificationResult
from channel_ops import (
    AnalyticsFeedbackService,
    ChannelOpsService,
    ContentStatus,
    DailyChannelItemResult,
    DailyChannelRunResult,
)
from content.short_agent import ShortAgentResult
from youtube.youtube_models import YouTubeUploadResult

from test_support import bootstrap_test_app, create_test_app, enable_local_file_write, make_temp_dir


class ChatApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_chat_provider_posture_requires_a_canonical_value(self) -> None:
        for posture in (
            "prefer-local",
            "improve-when-needed",
            "ask-before-external",
            "strict-local",
        ):
            with self.subTest(posture=posture):
                parsed = ChatExecutionPreferencesInput(provider_posture=posture)
                self.assertEqual(parsed.provider_posture, posture)

        with self.assertRaises(ValidationError):
            ChatExecutionPreferencesInput(provider_posture="strict_local")

    def test_explicit_agentic_browser_requests_bypass_shortcut_operator(self) -> None:
        self.assertTrue(
            ChatOrchestrationService._is_explicit_agentic_browser_request(
                "agentic: use browser_open then browser_learn"
            )
        )
        self.assertTrue(
            ChatOrchestrationService._is_explicit_agentic_browser_request(
                "use browser_learn for https://example.com"
            )
        )
        self.assertFalse(
            ChatOrchestrationService._is_explicit_agentic_browser_request(
                "sprawdź https://example.com i podsumuj stronę"
            )
        )

    def test_explicit_browser_tool_request_does_not_open_desktop_app(self) -> None:
        service = object.__new__(ChatOrchestrationService)
        service.desktop_operator = Mock()

        result = service._handle_desktop_operator_turn(
            request=SimpleNamespace(content="agentic: browser_open https://www.nasa.gov/"),
            conversation_result=None,
            execution_policy=None,
            skill_decision=None,
            skill_trace=None,
        )

        self.assertIsNone(result)
        service.desktop_operator.maybe_handle.assert_not_called()

    def test_chat_generates_pptx_attachment_for_presentation_request(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            response = client.post(
                "/chat",
                json={"content": "prezentacja o local-first AI w 5 slajdach"},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "local-capability")

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            messages = detail.json()["messages"]
            assistant = messages[-1]
            attachments = assistant["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["file_extension"], ".pptx")
            self.assertTrue((self.root / attachments[0]["storage_path"]).exists())

    def test_chat_generates_xlsx_attachment_for_new_excel_request(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            response = client.post(
                "/chat",
                json={
                    "content": (
                        "stworz nowy arkusz Excel z podsumowaniem 4 tematow.\n"
                        "1. AI shorty | nauka hookow i retencji | Atnos ma stosowac zasady | uzytkownik ma wskazac nisze | P1\n"
                        "2. Faceless shorty | praktyka workflow | Atnos ma poprawic jakosc | uzytkownik ma zatwierdzic styl | P1\n"
                    )
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "local-capability")
            self.assertEqual(payload["selected_model_key"], "local-capability")
            self.assertEqual(payload["detected_intent"], "document_workflow")

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            assistant = detail.json()["messages"][-1]
            attachments = assistant["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["file_extension"], ".xlsx")
            workbook_path = self.root / attachments[0]["storage_path"]
            self.assertTrue(workbook_path.exists())
            workbook = load_workbook(workbook_path)
            self.assertEqual(workbook.sheetnames, ["Podsumowanie", "Zadania użytkownika"])
            summary_sheet = workbook["Podsumowanie"]
            tasks_sheet = workbook["Zadania użytkownika"]
            self.assertEqual(summary_sheet["A1"].value, "Podsumowanie")
            self.assertIn("Raport wygenerowany lokalnie przez Atnos", summary_sheet["A2"].value)
            self.assertEqual(summary_sheet["B5"].value, "AI shorty")
            self.assertEqual(tasks_sheet["B5"].value, "uzytkownik ma wskazac nisze")
            self.assertEqual(summary_sheet.freeze_panes, "A5")
            self.assertTrue(summary_sheet.tables)
            self.assertTrue(tasks_sheet.tables)
            self.assertEqual(summary_sheet["A1"].font.bold, True)
            self.assertEqual(summary_sheet["A1"].fill.fill_type, "solid")
            self.assertEqual(summary_sheet["A4"].font.bold, True)
            self.assertGreater(summary_sheet.column_dimensions["B"].width or 0, 15)
            self.assertGreaterEqual(len(summary_sheet.conditional_formatting), 1)

    def test_chat_generates_png_attachment_for_graphic_request(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            response = client.post(
                "/chat",
                json={"content": "zrob grafike promujaca Atnosa z napisem Full Access Active"},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "creative-asset-operator")
            self.assertEqual(payload["selected_model_key"], "canvas-composer-local")
            self.assertEqual(payload["detected_intent"], "creative_asset")
            self.assertIn("Wygenerowalem", payload["assistant_message"])
            self.assertIn("atnos:image-generation", payload["skill_decision"]["selected_skills"])

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            messages = detail.json()["messages"]
            assistant = messages[-1]
            attachments = assistant["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["file_extension"], ".png")
            self.assertEqual(attachments[0]["kind"], "image")
            self.assertTrue((self.root / attachments[0]["storage_path"]).exists())

    def test_chat_routes_typoed_polish_graphic_request_to_creative_asset(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            response = client.post(
                "/chat",
                json={"content": "stwóz mi grafię czerwonego kota"},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "creative-asset-operator")
            self.assertEqual(payload["detected_intent"], "creative_asset")
            self.assertIn("Wygenerowalem", payload["assistant_message"])

    def test_chat_does_not_attach_voice_reply_for_plain_text_turn_even_when_voice_replies_enabled(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            RuntimeSettingsService(session, app.state.settings).update_owner_controls(
                voice_replies_enabled=True,
            )
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            with patch(
                "atnos.services.chat_orchestration_service.VoiceSynthesisService.synthesize",
                side_effect=AssertionError("voice synthesis should stay off for plain text turns"),
            ):
                response = client.post(
                    "/chat",
                    json={"content": "zrob grafike czerwonego kota"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "creative-asset-operator")

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            attachments = detail.json()["messages"][-1]["attachments"]
            self.assertEqual([item["kind"] for item in attachments], ["image"])

    def test_chat_force_external_selects_first_healthy_cloud_provider(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "sk-test-openai",
                "ANTHROPIC_API_KEY": "",
                "GEMINI_API_KEY": "",
            },
            clear=False,
        ):
            with TestClient(app) as client, patch(
                "atnos.services.chat_orchestration_service.logger.info"
            ) as log_info:
                response = client.post(
                    "/chat",
                    json={
                        "content": "Napisz krotka odpowiedz testowa o architekturze Atnosa.",
                        "force_external": True,
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "openai-cloud")
        self.assertIn("force_external", payload["routing_reason"])
        metadata = payload["assistant_message_metadata_json"]
        self.assertEqual(metadata["llm_router_task_type"], "text")
        self.assertEqual(metadata["llm_router_provider_key"], "ollama-local")
        self.assertEqual(metadata["llm_router_model"], "qwen3:14b")
        self.assertEqual(metadata["actual_runtime_model_name"], "gpt-5.2")
        self.assertEqual(metadata["llm_router"]["actual_provider_key"], "openai-cloud")
        log_info.assert_any_call(
            "provider_selected=%s reason=%s",
            "openai-cloud",
            "force_external",
        )

    def test_chat_force_external_falls_back_to_ollama_when_cloud_is_unconfigured(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "GEMINI_API_KEY": "",
            },
            clear=False,
        ):
            with TestClient(app) as client, patch(
                "atnos.services.chat_orchestration_service.logger.info"
            ) as log_info:
                response = client.post(
                    "/chat",
                    json={
                        "content": "Napisz krotka odpowiedz testowa bez chmury.",
                        "force_external": True,
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "ollama-local")
        self.assertEqual(payload["routing_reason"], "cloud_unavailable \u2192 fallback: ollama-local")
        log_info.assert_any_call(
            "provider_selected=%s reason=%s",
            "ollama-local",
            "policy_fallback",
        )

    def test_chat_falls_back_to_next_provider_when_selected_provider_fails(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "sk-test-openai",
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "GEMINI_API_KEY": "",
            },
            clear=False,
        ):
            with TestClient(app) as client:
                openai_adapter = app.state.providers.get("openai-cloud")
                anthropic_adapter = app.state.providers.get("anthropic-cloud")
                self.assertIsNotNone(openai_adapter)
                self.assertIsNotNone(anthropic_adapter)
                with patch.object(
                    openai_adapter,
                    "execute",
                    side_effect=ProviderUnavailableError("OpenAI transient failure."),
                ), patch.object(
                    anthropic_adapter,
                    "execute",
                    return_value=ProviderExecutionResult(content="Recovered on Anthropic."),
                ):
                    response = client.post(
                        "/chat",
                        json={
                            "content": "Napisz krotka odpowiedz testowa z fallbackiem.",
                            "force_external": True,
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "anthropic-cloud")
        self.assertIn("(prze\u0142\u0105czono na anthropic-cloud)", payload["assistant_message"])
        self.assertIn("Recovered on Anthropic.", payload["assistant_message"])

    def test_chat_routes_explicit_ubuntu_request_to_sandbox_agent(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        fake_result = AgentRunResult(
            task="Sprawdź stan laptopa Ubuntu i zrób raport",
            task_type=AgentTaskType.UBUNTU_CONTROL,
            skill="atnos:ubuntu-full-control",
            risk_level=RiskLevel.LOW,
            status=AgentRunStatus.COMPLETED,
            response="Ubuntu sandbox: wykonano przez SSH.\n- raport: `/home/atnos/atnos/logs/system_report.md`",
            approval_required=False,
            verification=VerificationResult(ok=True, status="verified", message="ok"),
            metadata={"target": {"host": "198.51.100.216"}},
        )

        with TestClient(app) as client, patch(
            "atnos.services.chat_orchestration_service.UbuntuAgentAdapter.maybe_run",
            return_value=fake_result,
        ) as adapter_mock:
            response = client.post(
                "/chat",
                json={"content": "Sprawdź stan laptopa Ubuntu i zrób raport"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "ubuntu-sandbox-agent")
        self.assertEqual(payload["selected_model_key"], "agent-orchestrator")
        self.assertIn("Ubuntu sandbox", payload["assistant_message"])
        adapter_mock.assert_called_once()

    def test_chat_can_route_graphic_request_to_external_openai_image_api(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            AccountCredentialsService(session, app.state.settings).create_account(
                label="OpenAI",
                provider_key="openai-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-test-openai-image-1234567890",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            class _FakeResponse:
                status_code = 200
                text = '{"data":[{"b64_json":"..."}]}'

                @staticmethod
                def json() -> dict[str, object]:
                    return {
                        "data": [
                            {
                                "b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY0cAAAAASUVORK5CYII="
                            }
                        ]
                    }

            class _FakeHttpxClient:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def post(self, *args, **kwargs):
                    return _FakeResponse()

            with patch.object(
                CreativeAssetOperatorService,
                "_preferred_openai_image_model",
                return_value="gpt-image-2",
            ), patch(
                "atnos.services.creative_asset_operator_service.ComfyUiImageGenerationService.can_generate",
                return_value=False,
            ), patch(
                "atnos.services.creative_asset_operator_service.httpx.Client",
                _FakeHttpxClient,
            ):
                response = client.post(
                    "/chat",
                    json={"content": "wygeneruj fotorealistyczna ilustracje futurystycznego laboratorium przez OpenAI"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "openai-cloud")
            self.assertTrue(payload["selected_model_key"].startswith("openai-image-api:"))
            self.assertEqual(payload["detected_intent"], "creative_asset")
            self.assertIn("openai-image-api", payload["assistant_message"])

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            assistant = detail.json()["messages"][-1]
            attachments = assistant["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["kind"], "image")
            self.assertEqual(attachments[0]["file_extension"], ".png")

    def test_chat_passes_premium_quality_profile_to_openai_image_lane(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            AccountCredentialsService(session, app.state.settings).create_account(
                label="OpenAI",
                provider_key="openai-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-test-openai-image-1234567890",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
        with TestClient(app) as client:
            enable_local_file_write(client.app)
            captured_payload: dict[str, object] = {}

            class _FakeResponse:
                status_code = 200
                text = '{"data":[{"b64_json":"..."}]}'

                @staticmethod
                def json() -> dict[str, object]:
                    return {
                        "data": [
                            {
                                "b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY0cAAAAASUVORK5CYII="
                            }
                        ]
                    }

            class _FakeHttpxClient:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def post(self, *args, **kwargs):
                    captured_payload.update(kwargs.get("json") or {})
                    return _FakeResponse()

            with patch.object(
                CreativeAssetOperatorService,
                "_preferred_openai_image_model",
                return_value="gpt-image-2",
            ), patch(
                "atnos.services.creative_asset_operator_service.ComfyUiImageGenerationService.can_generate",
                return_value=False,
            ), patch(
                "atnos.services.creative_asset_operator_service.httpx.Client",
                _FakeHttpxClient,
            ):
                response = client.post(
                    "/chat",
                    json={
                        "content": "wygeneruj obraz abstrakcyjnej rzezby przez OpenAI",
                        "execution_preferences": {
                            "quality_profile": "premium",
                        },
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "openai-cloud")
            self.assertTrue(payload["selected_model_key"].startswith("openai-image-api:"))
            self.assertEqual(captured_payload.get("quality"), "high")

    def test_chat_can_route_graphic_request_to_ideogram_browser_lane(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            def fake_generate_browser(self, *, request, conversation_id, allow_external_fallback, runtime_controls):
                output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
                from PIL import Image

                Image.new("RGB", (512, 512), "#8844EE").save(output_path)
                return self.artifacts.finalize_output(
                    absolute_path=output_path,
                    filename="atnos-generic-image.png",
                    media_type="image/png",
                )

            with patch.object(
                CreativeAssetOperatorService,
                "_generate_with_ideogram_browser",
                fake_generate_browser,
            ):
                response = client.post(
                    "/chat",
                    json={"content": "wygeneruj obraz futurystycznego miasta przez Ideogram w przegladarce"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "ideogram-browser")
            self.assertEqual(payload["selected_model_key"], "ideogram-browser")
            self.assertEqual(payload["detected_intent"], "creative_asset")
            self.assertIn("Ideogram", payload["assistant_message"])

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            assistant = detail.json()["messages"][-1]
            attachments = assistant["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["kind"], "image")
            self.assertEqual(attachments[0]["file_extension"], ".png")

    def test_chat_falls_back_from_ideogram_browser_to_openai_image_api(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            AccountCredentialsService(session, app.state.settings).create_account(
                label="OpenAI",
                provider_key="openai-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-test-openai-image-1234567890",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            class _FakeResponse:
                status_code = 200
                text = '{"data":[{"b64_json":"..."}]}'

                @staticmethod
                def json() -> dict[str, object]:
                    return {
                        "data": [
                            {
                                "b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY0cAAAAASUVORK5CYII="
                            }
                        ]
                    }

            class _FakeHttpxClient:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def post(self, *args, **kwargs):
                    return _FakeResponse()

            with patch.object(
                CreativeAssetOperatorService,
                "_generate_with_ideogram_browser",
                side_effect=RuntimeError("Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny."),
            ), patch(
                "atnos.services.creative_asset_operator_service.httpx.Client",
                _FakeHttpxClient,
            ):
                response = client.post(
                    "/chat",
                    json={"content": "wygeneruj obraz futurystycznego miasta przez Ideogram w przegladarce"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "openai-cloud")
            self.assertTrue(payload["selected_model_key"].startswith("openai-image-api:"))
            self.assertIn("Pierwotny tor `ideogram-browser` nie dowiozl zadania", payload["assistant_message"])

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            assistant = detail.json()["messages"][-1]
            attachments = assistant["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["kind"], "image")
            self.assertEqual(attachments[0]["file_extension"], ".png")

    def test_chat_falls_back_to_local_canvas_when_ideogram_and_openai_image_api_fail(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            AccountCredentialsService(session, app.state.settings).create_account(
                label="OpenAI",
                provider_key="openai-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-test-openai-image-1234567890",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
        with TestClient(app) as client:
            enable_local_file_write(client.app)

            with patch.object(
                CreativeAssetOperatorService,
                "_generate_with_ideogram_browser",
                side_effect=RuntimeError("Ideogram browser lane nie dowiozl grafiki: Cloudflare challenge aktywny."),
            ), patch.object(
                CreativeAssetOperatorService,
                "_generate_with_openai_image_api",
                side_effect=RuntimeError("Billing hard limit has been reached."),
            ):
                response = client.post(
                    "/chat",
                    json={"content": "wygeneruj obraz futurystycznego miasta przez Ideogram w przegladarce"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "creative-asset-operator")
            self.assertEqual(payload["selected_model_key"], "canvas-composer-local")
            self.assertIn("Cloud image API nie dowiozl obrazu", payload["assistant_message"])

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            assistant = detail.json()["messages"][-1]
            attachments = assistant["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0]["kind"], "image")
            self.assertEqual(attachments[0]["file_extension"], ".png")

    def test_chat_routes_short_request_to_local_video_workflow_with_quality_profile(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        kicked = {"value": False}
        with TestClient(app) as client:
            client.app.state.scheduler_runtime = type(
                "_SchedulerRuntimeStub",
                (),
                {"kick_trusted_auto": staticmethod(lambda: kicked.__setitem__("value", True))},
            )()
            enable_local_file_write(client.app)
            captured: dict[str, object] = {}

            def fake_run_job_now(
                _trusted_auto,
                *,
                project_id,
                job_type,
                goal,
                connector_key,
                platform_key,
                account_id,
                trust_grant_id,
                payload_json,
                requires_trusted_auto,
                execute_now,
            ):
                captured.update(
                    {
                        "project_id": project_id,
                        "job_type": job_type,
                        "goal": goal,
                        "connector_key": connector_key,
                        "platform_key": platform_key,
                        "account_id": account_id,
                        "trust_grant_id": trust_grant_id,
                        "quality_profile": payload_json.get("quality_profile"),
                        "publish_mode": payload_json.get("publish_mode"),
                        "approved_for_publish": payload_json.get("approved_for_publish"),
                        "peer_consultation": payload_json.get("peer_consultation"),
                        "requires_trusted_auto": requires_trusted_auto,
                        "execute_now": execute_now,
                    }
                )
                return {
                    "job": {
                        "job_id": 71,
                        "status": "queued",
                        "job_type": "campaign",
                    },
                    "run": None,
                }

            with patch.object(
                TrustedAutoService,
                "run_job_now",
                fake_run_job_now,
            ):
                response = client.post(
                    "/chat",
                    json={
                        "content": "zrob shorta o Atnosie do YouTube Shorts z mocnym hookiem o local-first AI",
                        "execution_preferences": {
                            "quality_profile": "premium",
                        },
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["selected_provider_key"], "creative-video-operator")
            self.assertEqual(payload["selected_model_key"], "campaign-youtube-short")
            self.assertEqual(payload["detected_intent"], "creative_video")
            self.assertIn("Uruchomilem workflow shorta", payload["assistant_message"])
            self.assertIn("Tryb jakosci: `premium`.", payload["assistant_message"])
            self.assertIn("atnos:content-pipeline", payload["skill_decision"]["selected_skills"])
            self.assertTrue(kicked["value"])
            self.assertEqual(captured["job_type"], "campaign")
            self.assertEqual(captured["quality_profile"], "premium")
            self.assertEqual(captured["publish_mode"], "local_only")
            self.assertFalse(bool(captured["approved_for_publish"]))
            self.assertFalse(bool(captured["peer_consultation"]))
            self.assertFalse(bool(captured["requires_trusted_auto"]))
            self.assertFalse(bool(captured["execute_now"]))

            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
            self.assertEqual(detail.status_code, 200)
            assistant = detail.json()["messages"][-1]
            attachments = assistant["attachments"]
            self.assertEqual(attachments, [])
            self.assertEqual(
                assistant["metadata_json"]["creative_video_operator"]["workflow_state"]["status"],
                "queued",
            )

    def test_chat_generates_mp4_attachment_for_generic_video_request(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        def fake_generate_video(*, output_path: Path, **kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-mp4")
            return VideoGenerationResult(
                output_path=output_path.resolve(),
                backend="ltx-video",
                width=512,
                height=512,
                duration_sec=10,
                fps=24,
                frame_count=240,
                vram_total_gb=16.0,
            )

        with TestClient(app) as client, patch(
            "atnos.services.creative_video_operator_service.LocalVideoGenerationService.generate_video",
            side_effect=fake_generate_video,
        ):
            enable_local_file_write(client.app)
            response = client.post(
                "/chat",
                json={"content": "stworz 10-sekundowe wideo promujace Atnosa"},
            )
            detail = client.get(f"/ui/chat/conversations/{response.json()['conversation_id']}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "creative-video-operator")
        self.assertEqual(payload["selected_model_key"], "ltx-video")
        self.assertIn("Wygenerowalem lokalne wideo MP4", payload["assistant_message"])
        self.assertNotIn("scenariusz", payload["assistant_message"].lower())

        self.assertEqual(detail.status_code, 200)
        assistant = detail.json()["messages"][-1]
        attachments = assistant["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["kind"], "video")
        self.assertEqual(attachments[0]["file_extension"], ".mp4")

    def test_chat_explicit_polish_short_request_returns_final_mp4_path(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        output_dir = self.root / "data" / "shorts" / "run"
        output_dir.mkdir(parents=True, exist_ok=True)
        fake_result = ShortAgentResult(
            status="manual_upload_ready",
            topic="Atnos",
            output_dir=output_dir,
            final_mp4=output_dir / "final.mp4",
            quality_report={"overall_score": 8.0},
            artifacts={
                "render_qa": str(output_dir / "render_qa.json"),
                "production_report": str(output_dir / "production_report.md"),
            },
            repair_attempts=1,
            quality_run={
                "status": "DONE_VERIFIED",
                "score": 8.0,
                "report_path": str(output_dir / "quality.md"),
                "evidence_count": 3,
            },
        )
        with TestClient(app) as client, patch(
            "atnos.services.creative_video_operator_service.ShortAgent.run",
            return_value=fake_result,
        ):
            enable_local_file_write(client.app)
            response = client.post(
                "/chat",
                json={"content": "Zrób mi shorta po polsku o Atnosie"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-short-agent")
        self.assertIn("final.mp4", payload["assistant_message"])
        self.assertIn("DONE_VERIFIED", payload["assistant_message"])

        detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
        self.assertEqual(detail.status_code, 200)
        assistant = detail.json()["messages"][-1]
        self.assertEqual(
            assistant["metadata_json"]["creative_video_operator"]["quality_run"]["status"],
            "DONE_VERIFIED",
        )

    def test_chat_routes_channel_ops_prepare_short_command(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        ChannelOpsService(app.state.settings).register_channel(
            channel_id="lokalne-ai",
            display_name="Lokalne AI",
            niche="lokalne AI i automatyzacja",
            language="pl",
            audience="tworcy solo",
            daily_target_count=1,
        )
        output_dir = self.root / "data" / "channel_ops" / "channels" / "lokalne-ai" / "renders" / "run-1"
        fake_result = DailyChannelRunResult(
            timestamp="2026-05-20T00:00:00+00:00",
            processed_channels=1,
            processed_items=1,
            ready_for_review=[],
            results=[
                DailyChannelItemResult(
                    channel_id="lokalne-ai",
                    item_id="item-1",
                    topic="lokalne AI: odcinek 1",
                    status="manual_upload_ready",
                    output_dir=str(output_dir),
                    final_mp4=str(output_dir / "final.mp4"),
                    quality_score=8.0,
                    youtube_readiness_status="missing_client_secret",
                )
            ],
            daily_log_path=str(self.root / "data" / "channel_ops" / "daily_runs" / "fake.json"),
        )

        with TestClient(app) as client, patch(
            "atnos.services.channel_ops_chat_service.DailyChannelRunner.run",
            return_value=fake_result,
        ):
            response = client.post(
                "/chat",
                json={"content": "Przygotuj 1 short na kanal lokalne-ai"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "channel-ops")
        self.assertEqual(payload["selected_model_key"], "daily-channel-runner")
        self.assertIn("manual_upload_ready", payload["assistant_message"])
        self.assertIn("final.mp4", payload["assistant_message"])
        self.assertIn("atnos:channel-ops", payload["skill_decision"]["selected_skills"])
        self.assertEqual(payload["assistant_message_metadata_json"]["channel_ops"]["status"], "manual_upload_ready")

    def test_chat_routes_channel_ops_approval_queue(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        ops = ChannelOpsService(app.state.settings)
        ops.register_channel(
            channel_id="lokalne-ai",
            display_name="Lokalne AI",
            niche="lokalne AI i automatyzacja",
            language="pl",
            audience="tworcy solo",
            daily_target_count=1,
        )
        item = ops.enqueue_topic("lokalne-ai", "Jak lokalne AI oszczedza czas")
        ops.calendar.update_item(
            item.with_update(
                status=ContentStatus.MANUAL_UPLOAD_READY,
                final_mp4=str(self.root / "data" / "shorts" / "final.mp4"),
                quality_score=8.0,
            )
        )

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                json={"content": "Pokaz kolejke upload approval"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "channel-ops")
        self.assertEqual(payload["selected_model_key"], "approval-queue")
        self.assertIn("Kolejka upload approval", payload["assistant_message"])
        self.assertIn("final.mp4", payload["assistant_message"])

    def test_chat_routes_channel_ops_analytics(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        ops = ChannelOpsService(app.state.settings)
        ops.register_channel(
            channel_id="lokalne-ai",
            display_name="Lokalne AI",
            niche="lokalne AI i automatyzacja",
            language="pl",
            audience="tworcy solo",
            daily_target_count=1,
        )
        AnalyticsFeedbackService(app.state.settings, channel_ops=ops).ingest_video_metrics(
            "lokalne-ai",
            "video-1",
            {
                "topic": "Dlaczego lokalne AI oszczedza czas",
                "views": 1500,
                "averageViewDuration": 34,
                "estimatedMinutesWatched": 900,
                "likes": 120,
                "shares": 30,
                "subscribersGained": 9,
                "subscribersLost": 0,
            },
        )

        with TestClient(app) as client:
            response = client.post(
                "/chat",
                json={"content": "Co dziala najlepiej na kanale lokalne-ai?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "channel-ops")
        self.assertEqual(payload["selected_model_key"], "analytics-feedback")
        self.assertIn("Co dziala najlepiej", payload["assistant_message"])
        self.assertIn("Nastepne tematy", payload["assistant_message"])

    def test_chat_youtube_private_upload_preview_and_confirmation(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        run_dir = self.root / "data" / "shorts" / "20260517-test"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "final.mp4").write_bytes(b"fake-video")
        (run_dir / "qa_report.json").write_text('{"overall_status":"passed"}', encoding="utf-8")
        (run_dir / "production_report.md").write_text("- topic: Atnos\n\nRaport", encoding="utf-8")
        fake_upload = YouTubeUploadResult(
            video_id="abc123",
            title="Short: Atnos",
            description="Opis",
            privacy="private",
            local_file=run_dir / "final.mp4",
            youtube_url="https://www.youtube.com/watch?v=abc123",
        )

        with TestClient(app) as client, patch(
            "atnos.skills.youtube_skill.upload_private_short",
            return_value=fake_upload,
        ):
            preview_response = client.post(
                "/chat",
                json={"content": "Wrzuć tego shorta prywatnie na YouTube"},
            )
            self.assertEqual(preview_response.status_code, 200)
            preview_payload = preview_response.json()
            self.assertEqual(preview_payload["selected_provider_key"], "youtube-skill")
            self.assertIn("approval", preview_payload["assistant_message"])

            confirmation_response = client.post(
                "/chat",
                json={
                    "conversation_id": preview_payload["conversation_id"],
                    "content": "potwierdz",
                },
            )

        self.assertEqual(confirmation_response.status_code, 200)
        confirmation_payload = confirmation_response.json()
        self.assertEqual(confirmation_payload["selected_model_key"], "youtube-private-upload")
        self.assertIn("abc123", confirmation_payload["assistant_message"])

    def test_chat_previews_and_confirms_recurring_task(self) -> None:
        app = create_test_app(self.root, scheduled_tasks_enabled=True)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            first = client.post(
                "/chat",
                json={"content": "kazdy poniedzialek o 9 zrob przeglad projektu"},
            )
            self.assertEqual(first.status_code, 200)
            first_payload = first.json()
            self.assertEqual(first_payload["selected_provider_key"], "local-capability")
            self.assertIn("zaplanowane zadanie", first_payload["assistant_message"].lower())

            second = client.post(
                "/chat",
                json={
                    "conversation_id": first_payload["conversation_id"],
                    "content": "potwierdz",
                },
            )
            self.assertEqual(second.status_code, 200)
            second_payload = second.json()
            self.assertIn("zadanie cykliczne", second_payload["assistant_message"].lower())

            tasks = client.get("/agent/scheduled-tasks")
            self.assertEqual(tasks.status_code, 200)
            self.assertEqual(len(tasks.json()["scheduled_tasks"]), 1)

    def test_chat_handles_prompt_optimizer_request_locally(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch(
            "atnos.services.chat_orchestration_service.KnowledgeLibraryService.build_system_section",
            return_value=(
                "Local curated knowledge library matches:\n"
                "- Personal agent product principles [product]\n"
                "  summary: Chat is the main surface. Settings configure behavior."
            ),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={"content": "Write a Codex prompt to add a prompt optimizer agent to Atnos."},
                )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-prompt-optimizer")
        self.assertEqual(payload["detected_intent"], "prompt_optimization")
        self.assertIn("Target tool: Codex", payload["assistant_message"])
        self.assertIn("Local context:", payload["assistant_message"])

    def test_chat_handles_simple_greeting_locally(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            response = client.post(
                "/chat",
                json={"content": "hej"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-quick-chat")
        self.assertEqual(payload["detected_intent"], "general_chat")
        self.assertIn("Jestem", payload["assistant_message"])
        self.assertEqual(payload["skill_decision"]["selected_skills"], [])
        self.assertIn("No skill loaded", payload["skill_decision"]["reasoning_summary"])

    def test_chat_calculates_simple_arithmetic_without_claiming_a_model_skill(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            response = client.post("/chat", json={"content": "7+8"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["assistant_message"], "7+8 = 15")
        self.assertEqual(payload["selected_provider_key"], "local-quick-chat")
        self.assertEqual(payload["selected_model_key"], "local-quick-chat")
        self.assertEqual(payload["skill_decision"]["selected_skills"], [])
        self.assertTrue(payload["assistant_message_metadata_json"]["quick_chat"]["deterministic"])
        self.assertFalse(any(trace["step_name"] == "provider-execution" for trace in payload["traces"]))

    def test_chat_routes_explicit_claude_request_through_external_api_operator(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.object(
            ExternalLlmOperatorService,
            "_try_api_path",
            return_value=ExternalLlmExecutionOutcome(
                ok=True,
                route_path="api",
                provider_key="anthropic-cloud",
                model_key="claude-sonnet-4-20250514",
                reason="Wybralem API path: `Anthropic Cloud` / `claude-sonnet-4-20250514`.",
                answer_text="Claude answer from API path.",
                metadata={"transport": "api"},
            ),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={"content": "zapytaj Claude o trzy zalety lokalnego agenta AI"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["detected_intent"], "external_llm_operator")
        self.assertEqual(payload["selected_provider_key"], "anthropic-cloud")
        self.assertIn("Wybralem tor: `api`.", payload["assistant_message"])
        self.assertIn("Claude answer from API path.", payload["assistant_message"])
        with TestClient(app) as client:
            detail = client.get(f"/ui/chat/conversations/{payload['conversation_id']}")
        self.assertEqual(detail.status_code, 200)
        assistant_metadata = detail.json()["messages"][-1]["metadata_json"]
        self.assertEqual(assistant_metadata["selected_provider_key"], "anthropic-cloud")
        self.assertEqual(assistant_metadata["selected_model_key"], "claude-sonnet-4-20250514")
        self.assertIn("Anthropic Cloud", assistant_metadata["routing_reason"])

    def test_chat_auto_routes_premium_deep_turn_to_claude_for_quality(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.object(
            ExternalLlmOperatorService,
            "_try_api_path",
            return_value=ExternalLlmExecutionOutcome(
                ok=True,
                route_path="api",
                provider_key="anthropic-cloud",
                model_key="claude-sonnet-4-20250514",
                reason="Wybralem API path: `Anthropic Cloud` / `claude-sonnet-4-20250514`.",
                answer_text="Claude premium answer.",
                metadata={"transport": "api"},
            ),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={
                        "content": "Rozpisz mi porzadnie architekture pamieci projektu w Atnosie.",
                        "execution_preferences": {
                            "quality_profile": "premium",
                            "active_work_mode": "deep",
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["detected_intent"], "external_llm_operator")
        self.assertEqual(payload["selected_provider_key"], "anthropic-cloud")
        self.assertIn("Claude premium answer.", payload["assistant_message"])

    def test_chat_auto_routes_live_research_turn_to_browser_claude(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.object(
            ExternalLlmOperatorService,
            "_try_browser_path",
            return_value=ExternalLlmExecutionOutcome(
                ok=True,
                route_path="browser",
                provider_key="claude-browser",
                model_key="browser:claude",
                reason="Wybralem browser path: `Claude` przez live Chrome DevTools.",
                answer_text="Claude browser research answer.",
                metadata={"transport": "browser"},
            ),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={
                        "content": "Sprawdz aktualne informacje o Gemini i daj linki do zrodel.",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["detected_intent"], "external_llm_operator")
        self.assertEqual(payload["selected_provider_key"], "external-browser-operator")
        self.assertIn("Claude browser research answer.", payload["assistant_message"])

    def test_chat_blocks_live_research_when_browser_path_fails_instead_of_silent_api_fallback(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.object(
            ExternalLlmOperatorService,
            "_try_browser_path",
            return_value=ExternalLlmExecutionOutcome(
                ok=False,
                route_path="browser",
                provider_key="claude-browser",
                model_key="browser:claude",
                reason="Browser path unavailable.",
                blocker="Chrome bridge nie odpowiada.",
            ),
        ) as browser_path, patch.object(
            ExternalLlmOperatorService,
            "_try_api_path",
        ) as api_path:
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={
                        "content": "Sprawdz aktualne informacje o Gemini i daj linki do zrodel.",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["detected_intent"], "external_llm_operator")
        self.assertEqual(payload["selected_provider_key"], "external-llm-operator")
        self.assertIn("wymagal live browser execution", payload["assistant_message"])
        browser_path.assert_called_once()
        api_path.assert_not_called()

    def test_chat_falls_back_to_browser_external_operator_when_browser_path_wins(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch.object(
            ExternalLlmOperatorService,
            "_try_api_path",
            return_value=ExternalLlmExecutionOutcome(
                ok=False,
                route_path="api",
                provider_key="openai-cloud",
                model_key="gpt-4.1-mini",
                reason="API path unavailable.",
                blocker="No configured OpenAI account is available.",
            ),
        ), patch.object(
            ExternalLlmOperatorService,
            "_try_browser_path",
            return_value=ExternalLlmExecutionOutcome(
                ok=True,
                route_path="browser",
                provider_key="chatgpt-browser",
                model_key="browser:chatgpt",
                reason="Wybralem browser path: ChatGPT przez live Chrome DevTools.",
                answer_text="ChatGPT browser answer.",
                metadata={"transport": "browser"},
            ),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={"content": "zapytaj ChatGPT o plan publikacji na jutro"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["detected_intent"], "external_llm_operator")
        self.assertEqual(payload["selected_provider_key"], "external-browser-operator")
        self.assertIn("Wybralem tor: `browser`.", payload["assistant_message"])
        self.assertIn("ChatGPT browser answer.", payload["assistant_message"])

    def test_chat_handles_capability_question_locally(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            response = client.post(
                "/chat",
                json={"content": "co umiesz?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-quick-chat")
        self.assertIn("Chrome", payload["assistant_message"])

    def test_prompt_improve_endpoint_rewrites_browser_request(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            response = client.post(
                "/chat/prompt-improve",
                json={"content": "powiedz jakie karty są otwarte w przeglądarce chrome?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["changed"])
        self.assertIn("Google Chrome", payload["improved_content"])
        self.assertIn("URL", payload["improved_content"])
        self.assertIn("co blokuje odczyt", payload["improved_content"])

    def test_chat_handles_browser_tab_request_locally(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        diagnostics = ChromeBrowserAccessDiagnosticsView(
            browser_running=True,
            process_count=5,
            mode="devtools_live",
            summary="Atnos ma live odczyt kart przez Chrome DevTools.",
            reasons=("Chrome jest uruchomiony z --remote-debugging-port.",),
            remote_debugging_port=9222,
            visible_windows=(),
            tabs=(
                {"title": "GitHub", "url": "https://github.com/", "source": "devtools"},
                {"title": "Instagram", "url": "https://www.instagram.com/", "source": "devtools"},
            ),
            detected_apps=("GitHub", "Instagram"),
            session_source_file=None,
            checked_at="2026-05-16T10:05:10+00:00",
        )
        with patch(
            "atnos.services.browser_chat_operator_service.BrowserAccessDiagnosticsService.diagnose_chrome",
            return_value=diagnostics,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={"content": "powiedz jakie karty sa otwarte w przegladarce chrome"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-browser-operator")
        self.assertEqual(payload["detected_intent"], "browser_operator")
        self.assertIn("https://github.com/", payload["assistant_message"])
        self.assertIn("GitHub, Instagram", payload["assistant_message"])
        self.assertIn("atnos:browser-operator", payload["skill_decision"]["selected_skills"])

    def test_chat_pauses_for_missing_pdf_input_before_provider_execution(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            response = client.post(
                "/chat",
                json={"content": "Przeanalizuj ten PDF"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-skill-router")
        self.assertIn("anthropic-skills:pdf", payload["skill_decision"]["selected_skills"])
        self.assertIn("ścieżka do PDF", payload["skill_decision"]["required_inputs"])

    def test_chat_learns_from_active_browser_page_and_mentions_obsidian_path(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        learning = BrowserPageLearningView(
            action_kind="browser_learn_active_page",
            ok=True,
            target_url="https://github.com/",
            final_url="https://github.com/",
            selector="active-browser-window",
            note_id="general-browser-learning-github-20260516-020000",
            curated_path="F:/AtnosLibrary/curated/general/browser-learning-github.md",
            knowledge_path="F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/general/browser-learning-github.md",
            vault_note_path="Atnos/Research/BrowserLearning/general/browser-learning-github.md",
            extracted_text_excerpt="GitHub ma otwarte repo i pasek zakladek.",
            analysis="Wniosek: Atnos moze wyciagnac stan aktywnej karty i zapisac go do Obsidiana.",
            provider_key="ollama-local",
            model_key="ollama-local/document-default",
            runtime_fallback="active_browser_window_capture",
            started_at="2026-05-16T10:10:00+00:00",
            finished_at="2026-05-16T10:10:07+00:00",
            browser_artifacts={"foreground_screenshot": "F:/AtnosSystem/AppData/Roaming/Atnos/data/browser-active.png"},
        )
        with patch(
            "atnos.services.browser_chat_operator_service.DesktopBrowserObserverService.learn_from_active_browser_page",
            return_value=learning,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={
                        "content": (
                            "przeanalizuj to co widzisz w otwartej karcie chrome "
                            "i zapisz wnioski do obsydiana"
                        )
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-browser-operator")
        self.assertEqual(payload["detected_intent"], "browser_operator")
        self.assertIn("Atnos/Research/BrowserLearning/general/browser-learning-github.md", payload["assistant_message"])
        self.assertIn("ollama-local", payload["assistant_message"])

    def test_chat_browser_research_passes_memory_disabled_preference(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        learning = BrowserPageLearningView(
            action_kind="browser_learn_page",
            ok=True,
            target_url="https://example.com/",
            final_url="https://example.com/",
            selector="body",
            note_id=None,
            curated_path=None,
            knowledge_path=None,
            vault_note_path=None,
            extracted_text_excerpt="Example source text.",
            analysis="The source contains the requested fact.",
            provider_key="ollama-local",
            model_key="ollama-local/document-default",
            runtime_fallback="memory_persistence_disabled",
            started_at="2026-10-18T10:00:00+00:00",
            finished_at="2026-10-18T10:00:01+00:00",
            browser_artifacts={},
        )
        with patch(
            "atnos.services.browser_chat_operator_service.DesktopBrowserObserverService.learn_from_webpage",
            return_value=learning,
        ) as learn:
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={
                        "content": "sprawdź https://example.com/ i podaj jeden fakt",
                        "execution_preferences": {
                            "provider_posture": "strict-local",
                            "memory_enabled": False,
                            "internet_enabled": True,
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["selected_provider_key"], "local-browser-operator")
        self.assertFalse(learn.call_args.kwargs["persist_memory"])

    def test_chat_opens_obsidian_locally(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        obsidian_app = LocalDesktopAppView(
            app_key="obsidian",
            label="Obsidian",
            category="knowledge",
            installed=True,
            running=True,
            window_visible=True,
            launch_supported=True,
            focus_supported=True,
            preferred_entry_path="C:/Users/test/Desktop/Obsidian.lnk",
            preferred_entry_kind="shortcut",
            running_processes=("obsidian.exe",),
            visible_window_titles=("Notes - Obsidian",),
            notes=(),
        )
        command = LocalHostCommandResultView(
            action_kind="desktop_app_focus",
            command="focus-window",
            exit_code=0,
            stdout="Focused Obsidian window.",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-16T11:00:00+00:00",
            finished_at="2026-05-16T11:00:01+00:00",
        )
        with patch(
            "atnos.services.desktop_chat_operator_service.LocalHostControlService.list_desktop_apps",
            return_value=[obsidian_app],
        ), patch(
            "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app",
            return_value=command,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={"content": "otworz obsydiana"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-desktop-operator")
        self.assertEqual(payload["detected_intent"], "desktop_operator")
        self.assertIn("Obsidian", payload["assistant_message"])

    def test_chat_dispatches_prompt_to_kimi_locally(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        kimi_app = LocalDesktopAppView(
            app_key="desktop-kimi",
            label="Kimi",
            category="utility",
            installed=True,
            running=True,
            window_visible=True,
            launch_supported=True,
            focus_supported=True,
            preferred_entry_path="C:/Users/test/Desktop/Kimi.lnk",
            preferred_entry_kind="shortcut",
            running_processes=("kimi.exe",),
            visible_window_titles=("Kimi",),
            notes=(),
        )
        command = LocalHostCommandResultView(
            action_kind="desktop_app_focus",
            command="focus-window",
            exit_code=0,
            stdout="Focused Kimi window.",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-16T11:05:00+00:00",
            finished_at="2026-05-16T11:05:01+00:00",
        )
        with patch(
            "atnos.services.desktop_chat_operator_service.LocalHostControlService.list_desktop_apps",
            return_value=[kimi_app],
        ), patch(
            "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app",
            return_value=command,
        ), patch(
            "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app_input",
            return_value=command,
        ), patch(
            "atnos.services.desktop_chat_operator_service.LocalHostControlService.type_text",
            return_value=command,
        ) as type_text, patch(
            "atnos.services.desktop_chat_operator_service.LocalHostControlService.press_keys",
            return_value=command,
        ) as press_keys, patch(
            "atnos.services.desktop_chat_operator_service.DesktopChatOperatorService._wait_for_kimi_answer",
            return_value=("Plan publikacji: rano hook, wieczorem podsumowanie.", "uia_text_change"),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    json={"content": "zapytaj Kimi o plan publikacji na jutro"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "local-desktop-operator")
        self.assertEqual(payload["detected_intent"], "desktop_operator")
        self.assertIn("Kimi", payload["assistant_message"])
        self.assertIn("Plan publikacji", payload["assistant_message"])
        type_text.assert_called_once()
        press_keys.assert_called_once()
