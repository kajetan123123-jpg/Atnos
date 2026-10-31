from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.external_llm_operator_service import (
    ExternalLlmExecutionOutcome,
    ExternalLlmOperatorService,
)
from atnos.services.runtime_settings_service import RuntimeSettingsService

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class ExternalLlmOperatorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)
        bootstrap_test_app(self.app)

    def _with_service(self, callback) -> None:
        with self.app.state.database.session_scope() as session:
            service = ExternalLlmOperatorService(
                session,
                settings=self.app.state.settings,
            )
            runtime_controls = RuntimeSettingsService(session, self.app.state.settings).snapshot()
            callback(service, session, runtime_controls)

    def test_does_not_activate_for_regular_chat(self) -> None:
        def run(service: ExternalLlmOperatorService, session, runtime_controls) -> None:
            result = service.maybe_handle(
                content="napisz mi plan dnia",
                allow_external_fallback=True,
                runtime_controls=runtime_controls,
            )
            self.assertFalse(result.activated)

        self._with_service(run)

    def test_routes_explicit_claude_request_through_api_when_account_exists(self) -> None:
        def run(service: ExternalLlmOperatorService, session, runtime_controls) -> None:
            AccountCredentialsService(session, self.app.state.settings).create_account(
                label="Anthropic",
                provider_key="anthropic-cloud",
                account_identifier="owner@example.com",
                secret_type="api_key",
                secret_value="sk-ant-test-secret-123456789",
                risk_level="medium",
                allowed_capabilities=["read_content"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
            fake_response = Mock()
            fake_response.status_code = 200
            fake_response.json.return_value = {
                "content": [{"type": "text", "text": "Claude answer about local agents."}]
            }
            with patch("httpx.Client.post", return_value=fake_response):
                result = service.maybe_handle(
                    content="zapytaj Claude o trzy zalety lokalnych agentow AI",
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

            self.assertTrue(result.activated)
            self.assertEqual(result.selected_provider_key, "anthropic-cloud")
            self.assertIn("Wybralem tor: `api`.", result.assistant_content or "")
            self.assertIn("Claude answer about local agents.", result.assistant_content or "")

        self._with_service(run)

    def test_falls_back_to_browser_when_api_path_is_unavailable(self) -> None:
        def run(service: ExternalLlmOperatorService, session, runtime_controls) -> None:
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
                result = service.maybe_handle(
                    content="zapytaj ChatGPT o plan publikacji na jutro",
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

            self.assertTrue(result.activated)
            self.assertEqual(result.selected_provider_key, "external-browser-operator")
            self.assertIn("Wybralem tor: `browser`.", result.assistant_content or "")
            self.assertIn("ChatGPT browser answer.", result.assistant_content or "")

        self._with_service(run)

    def test_prefers_browser_path_for_polish_browser_ai_request(self) -> None:
        def run(service: ExternalLlmOperatorService, session, runtime_controls) -> None:
            with patch.object(
                ExternalLlmOperatorService,
                "_try_browser_path",
                return_value=ExternalLlmExecutionOutcome(
                    ok=True,
                    route_path="browser",
                    provider_key="claude-browser",
                    model_key="browser:claude",
                    reason="Wybralem browser path: Claude przez live Chrome DevTools.",
                    answer_text="Claude browser answer.",
                    metadata={"transport": "browser"},
                ),
            ) as browser_path, patch.object(
                ExternalLlmOperatorService,
                "_try_api_path",
            ) as api_path:
                result = service.maybe_handle(
                    content="skorzystaj z Claude przez przeglądarkowe AI do planu publikacji na jutro",
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

            self.assertTrue(result.activated)
            self.assertEqual(result.selected_provider_key, "external-browser-operator")
            self.assertIn("Wybralem tor: `browser`.", result.assistant_content or "")
            browser_path.assert_called_once()
            api_path.assert_not_called()

        self._with_service(run)

    def test_browser_only_request_does_not_silently_fall_back_to_api(self) -> None:
        def run(service: ExternalLlmOperatorService, session, runtime_controls) -> None:
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
                result = service.maybe_handle(
                    content="skorzystaj z Claude przez przegladarkowe AI do planu publikacji na jutro",
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

            self.assertTrue(result.activated)
            self.assertEqual(result.selected_provider_key, "external-llm-operator")
            self.assertIn("wymagal live browser execution", result.assistant_content or "")
            browser_path.assert_called_once()
            api_path.assert_not_called()

        self._with_service(run)

    def test_auto_routed_prompt_adds_atnos_grounding_guardrails(self) -> None:
        def run(service: ExternalLlmOperatorService, session, runtime_controls) -> None:
            captured_prompt: dict[str, str] = {}

            def _fake_api_path(*, target, prompt, allow_external_fallback, runtime_controls):
                captured_prompt["value"] = prompt
                return ExternalLlmExecutionOutcome(
                    ok=True,
                    route_path="api",
                    provider_key="anthropic-cloud",
                    model_key="claude-sonnet-4-20250514",
                    reason="Wybralem API path: `Anthropic Cloud` / `claude-sonnet-4-20250514`.",
                    answer_text="Claude answer.",
                )

            with patch.object(
                ExternalLlmOperatorService,
                "_try_api_path",
                side_effect=_fake_api_path,
            ):
                result = service.dispatch_auto(
                    prompt="Rozpisz mi porzadnie architekture pamieci projektu w Atnosie.",
                    target="claude",
                    task_type="general",
                    mode_preference="api",
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                    routing_reason="quality pass",
                )

            self.assertTrue(result.activated)
            grounded_prompt = captured_prompt["value"]
            self.assertIn("Do not silently substitute another product", grounded_prompt)
            self.assertIn("If the user mentions Atnos", grounded_prompt)
            self.assertIn("User request:", grounded_prompt)

        self._with_service(run)

    def test_reports_exact_blocker_when_no_external_path_works(self) -> None:
        def run(service: ExternalLlmOperatorService, session, runtime_controls) -> None:
            with patch.object(
                ExternalLlmOperatorService,
                "_try_api_path",
                return_value=ExternalLlmExecutionOutcome(
                    ok=False,
                    route_path="api",
                    provider_key="anthropic-cloud",
                    model_key="claude-sonnet-4-20250514",
                    reason="API path unavailable.",
                    blocker="Vault nie ma aktywnego konta `anthropic-cloud` z sekretem.",
                ),
            ), patch.object(
                ExternalLlmOperatorService,
                "_try_browser_path",
                return_value=ExternalLlmExecutionOutcome(
                    ok=False,
                    route_path="browser",
                    provider_key="claude-browser",
                    model_key="browser:claude",
                    reason="Browser path unavailable.",
                    blocker="Chrome nie wystawia live DevTools bridge dla biezacej sesji.",
                ),
            ):
                result = service.maybe_handle(
                    content="zapytaj Claude o roadmape produktu",
                    allow_external_fallback=True,
                    runtime_controls=runtime_controls,
                )

            self.assertTrue(result.activated)
            self.assertEqual(result.selected_provider_key, "external-llm-operator")
            self.assertIn("Bloker:", result.assistant_content or "")
            self.assertIn("Chrome nie wystawia live DevTools bridge", result.assistant_content or "")

        self._with_service(run)
