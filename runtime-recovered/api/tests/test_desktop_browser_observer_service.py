from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch
import time
import hashlib

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir
from atnos.services.desktop_browser_observer_service import _CdpPageSnapshot
from atnos.services.desktop_browser_observer_service import DesktopBrowserObserverService
from atnos.services.integration_config_service import ObsidianEffectiveConfig


class _BrowserRunnerStub:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def run(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        return self.response


class _WorkflowLLMStub:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    def generate_text(self, *, prompt: str, provider_posture) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(
            content=self.content,
            provider_key="ollama-local",
            model_key="ollama-local/document-default",
        )


class _KnowledgeWorkspaceStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def save_curated_note(self, **kwargs) -> dict[str, str]:
        self.calls.append(kwargs)
        return {
            "note_id": "general-browser-learning-test",
            "curated_path": "F:/AtnosLibrary/curated/general/test.md",
            "knowledge_path": "F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/general/test.md",
        }


class _IntegrationConfigStub:
    def __init__(self, vault_root: Path | None, *, path_exists: bool = True, export_enabled: bool = True) -> None:
        self.vault_root = vault_root
        self.path_exists = path_exists
        self.export_enabled = export_enabled

    def get_obsidian_config(self) -> ObsidianEffectiveConfig:
        return ObsidianEffectiveConfig(
            vault_path=self.vault_root,
            export_enabled=self.export_enabled,
            source="test",
            path_exists=self.path_exists,
            warnings=(),
        )


class DesktopBrowserObserverServiceTest(unittest.TestCase):
    def test_cdp_extraction_works_inside_an_agent_event_loop(self):
        import asyncio
        import threading
        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        service.settings = self.settings
        observed = []
        expected = _CdpPageSnapshot('https://example.org/', 'https://example.org/',
            'Exact title', 'isolated', 'Actual page body with more than forty characters.', 'cdp', (), {})
        async def extract(**kwargs):
            observed.append(threading.get_ident())
            return expected
        service._extract_webpage_via_cdp_async = extract
        async def invoke():
            caller = threading.get_ident()
            result = service._extract_webpage_via_cdp(target_url='https://example.org/',
                selector='body', deadline_monotonic=time.monotonic()+5)
            self.assertEqual(result.title, expected.title)
            self.assertNotEqual(observed, [caller])
        with patch('atnos.services.desktop_browser_observer_service.BrowserRuntimeService') as runtime:
            runtime.return_value.ensure_chrome_devtools_bridge.return_value = SimpleNamespace(status='already_running')
            asyncio.run(invoke())

    def test_http_page_title_is_extracted_from_source_without_model_inference(self):
        import httpx
        response = httpx.Response(200, text='<html><title>Example &amp; Domain</title><body>'
            'This public page is a documentation example, not a service.</body></html>',
            request=httpx.Request('GET', 'https://example.org/'))
        with patch('atnos.services.desktop_browser_observer_service.httpx.Client') as client:
            client.return_value.__enter__.return_value.get.return_value = response
            text, final_url, title = DesktopBrowserObserverService._fetch_webpage_text_http('https://example.org/')
        self.assertEqual(title, 'Example & Domain')
        self.assertEqual(final_url, 'https://example.org/')
        self.assertIn('documentation example', text)

    def test_native_summary_rejects_a_model_invented_source_port(self) -> None:
        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        with patch.object(service, "_generate_local_summary_direct", return_value=("Seven boxes. Source: http://127.0.0.1:18765/", "ollama-local", "chat")):
            with self.assertRaisesRegex(RuntimeError, "source URL absent"):
                service._summarize_webpage_text_resilient(target_url="http://127.0.0.1:18767/", page_text="Seven boxes are moved by the crane.", deadline_monotonic=time.monotonic()+60)

    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_native_summary_loads_reviewed_browser_instructions_with_real_digest(self) -> None:
        from atnos.skills.skill_prompt import DEFAULT_INSTRUCTIONS_ROOT

        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        prompt = service._build_webpage_summary_prompt(
            target_url="https://example.com/", page_text="Untrusted source text.", research_focus="Podsumuj źródło",
        )
        content = (DEFAULT_INSTRUCTIONS_ROOT / "browser-operator.md").read_text(encoding="utf-8").strip()
        self.assertIn(content, prompt)
        self.assertIn("Untrusted source text.", prompt)
        context = service._page_skill_instruction_context
        self.assertEqual(context["instruction_status"], "loaded")
        self.assertEqual(context["loaded_instructions"][0]["sha256"], hashlib.sha256(content.encode("utf-8")).hexdigest())

    def test_local_summary_uses_registered_provider_endpoint_and_model(self) -> None:
        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        service.settings = self.settings
        service.workflow_llm = MagicMock()
        service.workflow_llm.selection.select_for_task.return_value = SimpleNamespace(
            provider=SimpleNamespace(key="ollama-local", endpoint_url="http://127.0.0.1:11436"),
            model_config=SimpleNamespace(key="ollama-local/document-default", runtime_model_name="qwen3:14b"),
        )
        with patch("atnos.services.desktop_browser_observer_service.httpx.Client") as client_class:
            post = client_class.return_value.__enter__.return_value.post
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"message": {"content": "Fakt z podanego tekstu."}}
            content, provider, model = service._generate_local_summary_direct(
                prompt="Streść źródło", deadline_monotonic=time.monotonic() + 30,
            )
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:11436/api/chat")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "qwen3:14b")
        self.assertEqual(content, "Fakt z podanego tekstu.")
        self.assertEqual(model, "ollama-local/document-default")
        self.assertEqual(provider, "ollama-local")
        self.assertEqual(service.workflow_llm.selection.select_for_task.call_args.kwargs["task"].value, "general_chat")
        self.assertEqual(service.workflow_llm.selection.select_for_task.call_args.kwargs["provider_posture"].value, "strict-local")
        self.assertFalse(service.workflow_llm.selection.select_for_task.call_args.kwargs["force_external"])

    def test_local_summary_failure_does_not_force_paid_cloud_fallback(self) -> None:
        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        with patch.object(service, "_generate_local_summary_direct", side_effect=RuntimeError("wrong endpoint")), patch.object(service, "_generate_cloud_summary_with_timeout") as cloud:
            with self.assertRaisesRegex(RuntimeError, "wrong endpoint.*explicitly authorized"):
                service._summarize_webpage_text_resilient(target_url="https://example.com/", page_text="A public source", deadline_monotonic=time.monotonic() + 60)
        cloud.assert_not_called()

    def test_learn_from_webpage_uses_cdp_and_writes_capture_note(self) -> None:
        vault_root = self.root / "obsydian" / "atnos"
        vault_root.mkdir(parents=True, exist_ok=True)

        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        service.settings = self.settings
        service.browser_recipes = _BrowserRunnerStub({})
        service.workflow_llm = _WorkflowLLMStub(
            "1. Narzedzie daje gotowy kierunek.\n2. Najlepszy wniosek to jawny format wyjsciowy.\n3. Warto podpiac to do Prompt Optimizer.\n4. Zapamietac, ze krotki brief daje lepszy wynik."
        )
        service.knowledge_workspace = _KnowledgeWorkspaceStub()
        service.integration_config = _IntegrationConfigStub(vault_root)

        def extract_stub(**kwargs):
            self.assertEqual(kwargs["target_url"], "https://example.com/mock-ai")
            self.assertEqual(kwargs["selector"], "#response")
            return _CdpPageSnapshot(
                target_url="https://example.com/mock-ai",
                final_url="https://example.com/mock-ai",
                title="Mock AI",
                target_id="target-1",
                extracted_text="Model odpowiedzial: Najlepszy prompt to krotki cel, kontekst i oczekiwany format.",
                extraction_mode="inner_text",
                runtime_notes=("chrome_bridge_already_live", "target_selected", "extraction_inner_text"),
                artifacts={"cdp_target_id": "target-1"},
            )

        def summarize_stub(**kwargs):
            self.assertIn("Najlepszy prompt", kwargs["page_text"])
            return (
                "1. Narzedzie daje gotowy kierunek.\n2. Najlepszy wniosek to jawny format wyjsciowy.\n3. Warto podpiac to do Prompt Optimizer.\n4. Zapamietac, ze krotki brief daje lepszy wynik.",
                "ollama-local",
                "ollama-local/atnos-core",
                ("local_llm_ok",),
            )

        service._extract_webpage_via_cdp = extract_stub
        service._summarize_webpage_text_resilient = summarize_stub

        result = service.learn_from_webpage(
            target_url="https://example.com/mock-ai",
            selector="#response",
            steps_before_extract=[
                {"type": "type", "selector": "#prompt", "text": "Napisz prompt do researchu"},
                {"type": "click", "selector": "#send"},
                {"type": "verify_state", "selector": "#response"},
            ],
            research_focus="prompt optimization",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.selector, "#response")
        self.assertEqual(result.provider_key, "ollama-local")
        self.assertIn("Prompt Optimizer", result.analysis or "")
        self.assertIsNotNone(result.vault_note_path)
        self.assertIn("Atnos/Capture/", result.vault_note_path or "")
        note_path = vault_root / Path(result.vault_note_path or "")
        self.assertTrue(note_path.exists())
        self.assertIn("Najlepszy wniosek", note_path.read_text(encoding="utf-8"))
        self.assertEqual(service.browser_recipes.calls, [])

    def test_learn_from_webpage_saves_incomplete_fallback_note_when_cdp_fails(self) -> None:
        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        service.settings = self.settings
        service.browser_recipes = _BrowserRunnerStub({})
        service.workflow_llm = _WorkflowLLMStub("unused")
        service.knowledge_workspace = _KnowledgeWorkspaceStub()
        service.integration_config = _IntegrationConfigStub(None, path_exists=False, export_enabled=False)

        def extract_stub(**kwargs):
            raise RuntimeError("Chrome DevTools unavailable")

        service._extract_webpage_via_cdp = extract_stub

        result = service.learn_from_webpage(target_url=None, selector="body")

        self.assertTrue(result.ok)
        self.assertIsNone(result.vault_note_path)
        self.assertIn("analiza niekompletna", result.analysis or "")
        self.assertEqual(result.browser_artifacts.get("save_target"), "fallback")
        saved_path = Path(result.browser_artifacts["saved_note_path"])
        self.assertTrue(saved_path.exists())
        self.assertIn("Chrome DevTools unavailable", saved_path.read_text(encoding="utf-8"))
        self.assertEqual(service.browser_recipes.calls, [])

    def test_learn_from_webpage_does_not_persist_when_memory_is_disabled(self) -> None:
        vault_root = self.root / "obsydian" / "atnos"
        vault_root.mkdir(parents=True, exist_ok=True)

        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        service.settings = self.settings
        service.browser_recipes = _BrowserRunnerStub({})
        service.workflow_llm = _WorkflowLLMStub("Wniosek: źródło podaje podstawowe informacje.")
        service.knowledge_workspace = _KnowledgeWorkspaceStub()
        service.integration_config = _IntegrationConfigStub(vault_root)
        service._extract_webpage_via_cdp = lambda **kwargs: _CdpPageSnapshot(
            target_url="https://example.com/",
            final_url="https://example.com/",
            title="Example",
            target_id="target-no-memory",
            extracted_text="NASA",
            extraction_mode="inner_text",
            runtime_notes=("target_selected",),
            artifacts={},
        )
        service._fetch_webpage_text_http = lambda target_url, **kwargs: ("NASA", target_url, "Example", ())
        service._summarize_webpage_text_resilient = lambda **kwargs: (
            "Wniosek: źródło podaje podstawowe informacje.",
            "ollama-local",
            "ollama-local/atnos-core",
            ("local_llm_ok",),
        )

        result = service.learn_from_webpage(
            target_url="https://example.com/",
            research_focus="sprawdź stronę",
            persist_memory=False,
        )

        self.assertTrue(result.ok)
        self.assertIn("memory_persistence_disabled", result.runtime_fallback or "")
        self.assertIn("Nie zapisano wyniku", result.analysis or "")
        self.assertNotIn("Zapisano wynik", result.analysis or "")
        self.assertIsNone(result.note_id)
        self.assertIsNone(result.curated_path)
        self.assertIsNone(result.knowledge_path)
        self.assertIsNone(result.vault_note_path)
        self.assertIsNone(result.browser_artifacts.get("saved_note_path"))
        self.assertEqual(service.knowledge_workspace.calls, [])
        self.assertEqual(list(vault_root.rglob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
