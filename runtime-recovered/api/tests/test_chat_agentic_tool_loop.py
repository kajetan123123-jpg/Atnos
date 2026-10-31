from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.llm.base import ChatResponse, ToolCall
from atnos.core.config import Settings
from atnos.repositories.model_config_repository import ModelConfigRepository
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.user_repository import UserRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.services.chat_orchestration_service import ChatOrchestrationService
from atnos.tools.chat_agent_tools import BrowserLearnTool, FsWriteTool, SelfStateTool


class _ScriptedBackend:
    def __init__(self, model: str = "qwen3.5:9b") -> None:
        self.model = model
        self.responses = [
            ChatResponse(
                content="",
                tool_calls=(ToolCall(name="echo", arguments={"text": "hello from chat"}, id="call-1"),),
            ),
            ChatResponse(content="Wynik narzędzia: hello from chat"),
        ]
        self.calls = []

    async def chat_complete(self, *, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        return self.responses.pop(0)


class ChatAgenticToolLoopTest(unittest.TestCase):
    def test_clear_coding_request_is_dispatched_once_after_model_only_describes_it(self):
        from atnos.tools.chat_agent_tools import AiDelegateBackgroundTool
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        target = self.root / "runtime-recovered/api/src/example.py"
        target.parent.mkdir(parents=True)
        target.write_text("value = 1\n", encoding="utf-8")
        text = f"Przeczytaj {target} i przygotuj propozycję poprawki kodu. Nie stosuj poprawki i nie zapisuj pamięci."
        backend = _ScriptedBackend()
        backend.responses = [ChatResponse(content="", tool_calls=(ToolCall(name="fs_read", arguments={"path": str(target)}),)), ChatResponse(content="Przygotowuję delegację."), ChatResponse(content="Opis zamiast zlecenia.")]
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", return_value=backend), patch.object(AiDelegateBackgroundTool, "execute", new=AsyncMock(return_value={"status": "started_background", "job_token": "actual-receipt"})) as delegate, TestClient(app) as client:
            payload = client.post("/chat", json={"content": text, "execution_preferences": {"memory_enabled": False, "file_enabled": True, "internet_enabled": False, "active_work_mode": "quick"}}).json()
        delegate.assert_awaited_once()
        self.assertEqual(delegate.await_args.kwargs["prompt"], text)
        self.assertEqual(delegate.await_args.kwargs["source_paths"], ["runtime-recovered/api/src/example.py"])
        self.assertTrue(payload["assistant_message_metadata_json"]["agentic_tool_loop"]["bounded_coding_dispatch"])
        self.assertIn("actual-receipt", payload["assistant_message"])
        self.assertEqual(target.read_text(), "value = 1\n")

    def test_natural_first_line_is_exact_without_model_or_character_guess(self):
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        target = self.root / "quote.md"
        target.write_text("# Dokładny cytat: źółć.\nNastępny wiersz.\n", encoding="utf-8")
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", side_effect=AssertionError("exact read must not need model")), TestClient(app) as client:
            payload = client.post("/chat", json={"content": f"Przeczytaj {target} i zacytuj wyłącznie jego pierwszy wiersz.", "execution_preferences": {"memory_enabled": False, "file_enabled": True, "internet_enabled": False, "active_work_mode": "quick"}}).json()
        self.assertEqual(payload["assistant_message"], "# Dokładny cytat: źółć.")
        self.assertEqual(payload["selected_provider_key"], "local-tools")
        self.assertEqual(payload["assistant_message_metadata_json"]["agentic_tool_loop"]["tools_used"], ["fs_read"])

    def test_natural_file_request_respects_disabled_file_access(self):
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", side_effect=AssertionError("must not invoke model with unavailable required tools")), TestClient(app) as client:
            payload = client.post("/chat", json={"content": "Przeczytaj E:\\Atnos\\App\\AGENTS.md", "execution_preferences": {"memory_enabled": False, "file_enabled": False, "internet_enabled": True, "active_work_mode": "quick"}}).json()
        self.assertIn("Nie wykonałem", payload["assistant_message"])
        self.assertEqual(payload["assistant_message_metadata_json"]["agentic_tool_loop"]["unavailable_tools"], ["fs_read"])

    def test_natural_coding_start_does_not_claim_applied_patch(self):
        from atnos.tools.chat_agent_tools import AiDelegateBackgroundTool
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        target = self.root / "example.py"
        target.write_text("value = 1\n", encoding="utf-8")
        backend = _ScriptedBackend()
        backend.responses = [ChatResponse(content="", tool_calls=(ToolCall(name="fs_read", arguments={"path": str(target)}),)), ChatResponse(content="Plik przeczytany, przygotowałem poprawkę."), ChatResponse(content="", tool_calls=(ToolCall(name="ai_delegate_background", arguments={"task_type": "coding", "prompt": "Fix value", "source_paths": ["runtime-recovered/api/src/example.py"]}),)), ChatResponse(content="Naprawiłem kod i wszystkie testy przeszły.")]
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", return_value=backend), patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._handle_creative_asset_turn", side_effect=AssertionError("code request must not generate graphics")), patch.object(AiDelegateBackgroundTool, "execute", new=AsyncMock(return_value={"status": "started_background", "job_token": "example-token"})), patch("atnos.services.chat_session_memory.persist_agentic_session_memory") as memory, TestClient(app) as client:
            payload = client.post("/chat", json={"content": f"Przeczytaj {target} i przygotuj propozycję poprawki kodu: w gałęzi if coding usuń powtórne przypisanie clean_prompt. Nie zapisuj pamięci.", "execution_preferences": {"memory_enabled": True, "file_enabled": True, "internet_enabled": False, "active_work_mode": "quick"}}).json()
        memory.assert_not_called()
        self.assertTrue(payload["assistant_message_metadata_json"]["agentic_tool_loop"]["settings"]["memory_enabled"])
        self.assertFalse(payload["assistant_message_metadata_json"]["agentic_tool_loop"]["settings"]["memory_write_enabled"])
        self.assertIn("example-token", payload["assistant_message"])
        self.assertIn("nie został jeszcze zmieniony ani przetestowany", payload["assistant_message"])
        self.assertNotIn("wszystkie testy przeszły", payload["assistant_message"])
        self.assertEqual(len(backend.calls), 3)

    def test_natural_status_request_reads_registry_even_when_model_only_guesses(self):
        from atnos.tools.chat_agent_tools import AiDelegateStatusTool
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()
        backend.responses = [ChatResponse(content="Nie ma zadań w tle."), ChatResponse(content="Nie wykryto żadnych procesów.")]
        actual = {"status": "ok", "tasks": [{"id": 901, "status": "running", "task_type": "review"}, {"id": 902, "status": "completed", "task_type": "coding", "verification_status": "awaiting_human_review"}]}
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", return_value=backend), patch.object(AiDelegateStatusTool, "execute", new=AsyncMock(return_value=actual)) as read, TestClient(app) as client:
            payload = client.post("/chat", json={"content": "Sprawdź jakie zadania w tle teraz działają. Niczego nie uruchamiaj.", "execution_preferences": {"memory_enabled": False, "file_enabled": True, "internet_enabled": False, "active_work_mode": "quick"}}).json()
        read.assert_awaited_once()
        loop = payload["assistant_message_metadata_json"]["agentic_tool_loop"]
        self.assertEqual(loop["tools_used"], ["ai_delegate_status"])
        self.assertEqual(loop["natural_tool_focus"], "background_status")
        self.assertEqual(loop["explicit_readonly_dispatch"], "ai_delegate_status")
        self.assertIn("#901", payload["assistant_message"])
        self.assertIn("wymaga przeglądu", payload["assistant_message"])
        self.assertNotIn("Nie wykryto", payload["assistant_message"])
        self.assertEqual(backend.calls, [])
        self.assertEqual(payload["selected_provider_key"], "local-tools")

    def test_natural_file_read_uses_only_read_tool(self):
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        target = self.root / "example.txt"
        target.write_text("Dokładny cytat: źółć.", encoding="utf-8")
        backend = _ScriptedBackend()
        backend.responses = [ChatResponse(content="", tool_calls=(ToolCall(name="fs_read", arguments={"path": str(target)}),)), ChatResponse(content="Dokładny cytat: źółć.")]
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", return_value=backend), TestClient(app) as client:
            payload = client.post("/chat", json={"content": f"Przeczytaj {target} i zacytuj jego treść.", "execution_preferences": {"memory_enabled": False, "file_enabled": True, "internet_enabled": False, "active_work_mode": "quick"}}).json()
        loop = payload["assistant_message_metadata_json"]["agentic_tool_loop"]
        self.assertEqual(loop["tools_used"], ["fs_read"])
        self.assertEqual(loop["available_tools"], ["fs_read"])
        self.assertEqual(payload["assistant_message"], "Dokładny cytat: źółć.")

    def test_successful_snapshot_and_write_are_rendered_from_actual_tool_results(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        target = self.root / "candidate.py"
        backend = _ScriptedBackend()
        backend.responses = [
            ChatResponse(content="", tool_calls=(ToolCall(name="self_state", arguments={}),)),
            ChatResponse(content="OpenAI is connected and working."),
            ChatResponse(content="", tool_calls=(ToolCall(name="fs_write", arguments={"path": str(target), "content": "pass"}),)),
            ChatResponse(content="I read and tested the file successfully."),
        ]
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", return_value=backend), patch.object(SelfStateTool, "execute", new=AsyncMock(return_value={"status": "ok", "can_do": {"external_llm_labels": ["OpenAI"], "external_llms_claude_openai_gemini": False}})), patch.object(FsWriteTool, "execute", new=AsyncMock(return_value={"status": "written", "path": str(target), "bytes": 4})), TestClient(app) as client:
            state = client.post("/chat", json={"content": "agentic: use self_state once"}).json()
            write = client.post("/chat", json={"content": f"agentic: use fs_write to create {target}"}).json()
        self.assertIn("wyłączony lub niepodłączony", state["assistant_message"])
        self.assertNotIn("connected and working", state["assistant_message"])
        self.assertIn(str(target), write["assistant_message"])
        self.assertNotIn("tested", write["assistant_message"])

    def test_self_state_distinguishes_registered_labels_from_access(self) -> None:
        result = ChatOrchestrationService._format_self_state_summary({"can_do": {
            "external_llm_labels": ["OpenAI", "Claude"],
            "external_llms_claude_openai_gemini": False,
        }})
        self.assertIn("Dostęp do zewnętrznych LLM: wyłączony lub niepodłączony", result)
        self.assertIn("same nazwy nie potwierdzają dostępu", result)
        unknown = ChatOrchestrationService._format_self_state_summary({"can_do": {"external_llm_labels": ["OpenAI"]}})
        self.assertIn("Dostęp do zewnętrznych LLM: niepotwierdzony", unknown)

    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_browser_learn_uses_explicit_url_even_when_model_marks_active_tab(self) -> None:
        class _LearningView:
            def to_dict(self):
                return {
                    "analysis": "NASA investigates the unknown in air and space.",
                    "final_url": "https://www.nasa.gov/",
                    "vault_note_path": None,
                    "curated_path": None,
                }

        from atnos.core.config import Settings

        tool = BrowserLearnTool(
            settings=Settings(_env_file=None),
            session=object(),
            provider_registry=object(),
            memory_enabled=False,
        )
        with patch("atnos.tools.chat_agent_tools.DesktopBrowserObserverService") as observer_class:
            observer = observer_class.return_value
            observer.learn_from_webpage.return_value = _LearningView()
            payload = asyncio.run(
                tool.execute(
                    url="https://www.nasa.gov/",
                    active_tab=True,
                    research_focus="mission",
                )
            )

        self.assertEqual(payload["final_url"], "https://www.nasa.gov/")
        observer.learn_from_webpage.assert_called_once()
        self.assertEqual(observer.learn_from_webpage.call_args.kwargs["target_url"], "https://www.nasa.gov/")
        self.assertFalse(observer.learn_from_webpage.call_args.kwargs["persist_memory"])
        observer.learn_from_active_browser_page.assert_not_called()

    def test_pc_execution_uses_fast_model_while_deep_analysis_uses_main_model(self) -> None:
        service = SimpleNamespace(
            settings=Settings(
                _env_file=None,
                ollama_fast_model="qwen3.5:9b",
                ollama_main_model="qwen3:14b",
            )
        )
        select_backend = ChatOrchestrationService._build_agentic_tool_loop_backend

        self.assertEqual(select_backend(service, work_mode="execute").model, "qwen3.5:9b")
        self.assertEqual(select_backend(service, work_mode="quick").model, "qwen3.5:9b")
        self.assertEqual(select_backend(service, work_mode="deep").model, "qwen3:14b")

    def test_browser_tool_reports_incomplete_analysis(self) -> None:
        tool = BrowserLearnTool(settings=Settings(_env_file=None), session=object(), provider_registry=object(), memory_enabled=False)
        with patch("atnos.tools.chat_agent_tools.DesktopBrowserObserverService") as observer_class:
            observer_class.return_value.learn_from_webpage.return_value.to_dict.return_value = {
                "ok": True, "analysis": "analiza niekompletna: lokalny model nie odpowiedział.",
                "runtime_fallback": "http_fallback_used; analysis_incomplete_llm_failed",
                "final_url": "https://example.com/",
            }
            payload = asyncio.run(tool.execute(url="https://example.com/"))
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("nie ogłaszaj zakończenia", payload["next_action"])

    def test_chat_does_not_claim_done_when_browser_analysis_is_incomplete(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()
        backend.responses = [
            ChatResponse(content="", tool_calls=(ToolCall(name="browser_learn", arguments={"url": "https://example.com/"}, id="browser-1"),)),
            ChatResponse(content="Gotowe, przeanalizowałem stronę."),
        ]
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", return_value=backend), patch.object(
            BrowserLearnTool, "execute", new=AsyncMock(return_value={"status": "incomplete", "summary": "analiza niekompletna: model timed out", "final_url": "https://example.com/"}),
        ), TestClient(app) as client:
            response = client.post("/chat", json={"content": "agentic: use browser_learn for https://example.com/", "execution_preferences": {"internet_enabled": True, "memory_enabled": False}})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["assistant_message_metadata_json"]["agentic_tool_loop"]["status"], "incomplete")
        self.assertIn("Nie ukończyłem", payload["assistant_message"])
        self.assertNotIn("Gotowe", payload["assistant_message"])

    def test_wrong_tool_call_does_not_satisfy_explicit_browser_request(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()
        backend.responses = [
            ChatResponse(content="", tool_calls=(ToolCall(name="external_llm_ask", arguments={"target": "browser_operator_simulate"}, id="wrong-tool"),)),
            ChatResponse(content="Gotowe, NASA bada kosmos."),
            ChatResponse(content="Nadal tylko opis bez wywołania żądanego narzędzia."),
        ]
        with patch("atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend", return_value=backend), TestClient(app) as client:
            response = client.post("/chat", json={"content": "agentic: use browser_learn for https://example.com/", "execution_preferences": {"internet_enabled": True, "memory_enabled": False}})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        loop = payload["assistant_message_metadata_json"]["agentic_tool_loop"]
        self.assertEqual(loop["status"], "unfulfilled_tool_request")
        self.assertEqual(loop["requested_tools_not_called"], ["browser_learn"])
        self.assertIn("Nie wykonałem żądanego narzędzia", payload["assistant_message"])
        self.assertNotIn("NASA", payload["assistant_message"])
        self.assertIn("Unknown tool", loop["tool_calls"][0]["summary"])

    def test_pc_tool_loop_follows_stored_local_chat_model_switch(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app):
            with app.state.database.session_scope() as session:
                models = ModelConfigRepository(session)
                selected = models.get_by_key("ollama-local/chat-default")
                assert selected is not None
                models.update_runtime_model_name(selected, "qwen3:8b")
                service = SimpleNamespace(
                    settings=Settings(
                        _env_file=None,
                        ollama_fast_model="qwen3.5:9b",
                        ollama_main_model="qwen3:14b",
                    ),
                    session=session,
                )
                select_backend = ChatOrchestrationService._build_agentic_tool_loop_backend
                self.assertEqual(select_backend(service, work_mode="execute").model, "qwen3:8b")
                models.update_runtime_model_name(selected, "qwen3.5:9b")
                self.assertEqual(select_backend(service, work_mode="quick").model, "qwen3.5:9b")
                self.assertEqual(select_backend(service, work_mode="deep").model, "qwen3:14b")

    def test_chat_can_run_explicit_agentic_tool_loop(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()

        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            return_value=backend,
        ), TestClient(app) as client:
            response = client.post("/chat", json={"content": "agentic: echo hello from chat"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "agentic-ollama-tools")
        self.assertEqual(payload["selected_model_key"], "ollama-local/agentic-tools")
        self.assertEqual(payload["assistant_message"], "Wynik narzędzia: hello from chat")
        metadata = payload["assistant_message_metadata_json"]
        self.assertEqual(metadata["agentic_tool_loop"]["status"], "completed")
        self.assertEqual(metadata["agentic_tool_loop"]["tool_calls"][0]["name"], "echo")
        self.assertEqual(metadata["actual_runtime_model_name"], "qwen3.5:9b")
        self.assertEqual(metadata["executed_model"], "qwen3.5:9b")
        self.assertEqual(metadata["executed_provider"], "ollama-local")
        self.assertFalse(metadata["fallback_used"])
        self.assertEqual(len(backend.calls), 2)
        self.assertTrue(backend.calls[0]["tools"])
        self.assertIn("wywołaj to narzędzie jako tool call", backend.calls[0]["system"])
        tool_names = {
            tool["function"]["name"]
            for tool in backend.calls[0]["tools"]
        }
        self.assertEqual(tool_names, {"echo"})
        self.assertEqual(backend.calls[1]["messages"][-1]["role"], "tool")
        self.assertTrue(metadata["agentic_tool_loop"]["always_on"])
        skill_trace = next(item for item in payload["traces"] if item["step_name"] == "skill-routing")
        skill_trace_metadata = skill_trace["metadata_json"]
        instruction_context = skill_trace_metadata["instruction_context"]
        self.assertEqual(instruction_context["instruction_status"], "loaded")
        self.assertEqual(
            instruction_context["loaded_instructions"][0]["skill_id"],
            "atnos:local-llm",
        )
        self.assertEqual(len(instruction_context["loaded_instructions"][0]["sha256"]), 64)
        self.assertEqual(skill_trace_metadata["actual_tool_invocations"], ["echo"])
        execution = metadata["atnos_skill_execution"]
        self.assertEqual(execution["selectedSkills"], ["atnos:local-llm"])
        self.assertEqual(execution["instructionStatus"], "loaded")
        self.assertEqual(execution["loadedInstructionSkills"], ["atnos:local-llm"])
        self.assertEqual(len(execution["loadedInstructionSources"][0]["sha256"]), 64)
        with app.state.database.session_scope() as session:
            messages = MessageRepository(session).list_for_conversation(payload["conversation_id"])
            persisted = next(message for message in messages if message.id == payload["assistant_message_id"])
            self.assertEqual(persisted.metadata_json["atnos_skill_execution"], execution)

    def test_chat_always_on_tool_loop_without_prefix(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()

        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            return_value=backend,
        ), patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._handle_quick_chat_turn",
            return_value=None,
        ), patch(
            "atnos.services.quick_chat_operator_service.QuickChatOperatorService.maybe_handle",
            return_value=type("R", (), {"activated": False, "assistant_content": None, "metadata": {}})(),
        ), TestClient(app) as client:
            response = client.post("/chat", json={"content": "Jaki jest twoj stan lokalny?"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "agentic-ollama-tools")
        metadata = payload["assistant_message_metadata_json"]
        self.assertTrue(metadata["agentic_tool_loop"]["always_on"])
        self.assertIn("self_state", metadata["agentic_tool_loop"]["available_tools"])

    def test_ordinary_question_uses_chat_path_without_agentic_tool_loop(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            side_effect=AssertionError("ordinary chat must not start the tool loop"),
        ), TestClient(app) as client:
            response = client.post(
                "/chat",
                json={"content": "Jak rozumieć koszt alternatywny w codziennym budżecie?"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_provider_key"], "ollama-local")
        self.assertNotIn("agentic_tool_loop", payload["assistant_message_metadata_json"])

    def test_named_pc_tool_request_is_not_reported_as_done_without_a_call(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()
        backend.responses = [ChatResponse(content="Wykonuję fs_list i podam wynik."), ChatResponse(content="Nadal tylko opis bez wywołania.")]

        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            return_value=backend,
        ), TestClient(app) as client:
            response = client.post(
                "/chat",
                json={
                    "content": "agentic: użyj fs_list dla E:\\Atnos\\App; niczego nie zmieniaj",
                    "execution_preferences": {"active_work_mode": "execute"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("Nie wykonałem", payload["assistant_message"])
        metadata = payload["assistant_message_metadata_json"]["agentic_tool_loop"]
        self.assertEqual(metadata["status"], "unfulfilled_tool_request")
        self.assertEqual(metadata["requested_tools_not_called"], ["fs_list"])
        self.assertEqual(payload["traces"][-1]["status"], "failed")

    def test_run_verb_and_agentic_prefix_cannot_claim_echo_without_a_tool_call(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()
        backend.responses = [
            ChatResponse(content="I ran echo and got the requested value."),
            ChatResponse(content="Still prose after the single reminder."),
            ChatResponse(content="ATNOS_ECHO_PREFIX_B was echoed successfully."),
            ChatResponse(content="Still prose after the single reminder."),
        ]

        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            return_value=backend,
        ), TestClient(app) as client:
            run_response = client.post(
                "/chat",
                json={"content": "Run the echo tool once with text ATNOS_ECHO_RUN_A"},
            )
            prefix_response = client.post(
                "/chat",
                json={"content": "agentic: echo ATNOS_ECHO_PREFIX_B"},
            )

        for response in (run_response, prefix_response):
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            metadata = payload["assistant_message_metadata_json"]["agentic_tool_loop"]
            self.assertEqual(metadata["status"], "unfulfilled_tool_request")
            self.assertEqual(metadata["requested_tools_not_called"], ["echo"])
            self.assertEqual(metadata["tools_used"], [])
            self.assertIn("Nie wykonałem", payload["assistant_message"])
            self.assertEqual(payload["traces"][-1]["status"], "failed")

    def test_explicit_tool_request_restricts_model_tool_list_to_requested_tool(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()

        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            return_value=backend,
        ), TestClient(app) as client:
            response = client.post(
                "/chat",
                json={"content": "Use echo with text ATNOS_ONLY_ECHO"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(backend.calls[0]["tools"]), 1)
        self.assertEqual(backend.calls[0]["tools"][0]["function"]["name"], "echo")
        payload = response.json()
        metadata = payload["assistant_message_metadata_json"]["agentic_tool_loop"]
        self.assertEqual(metadata["tools_used"], ["echo"])

    def test_explicit_self_state_is_dispatched_if_local_model_returns_only_prose(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        backend = _ScriptedBackend()
        backend.responses = [ChatResponse(content="I will check the local state now.")]
        state = {
            "status": "ok",
            "summary": "Read-only Atnos capability snapshot.",
            "can_do": {
                "realtime_internet": True,
                "local_files_without_chat_attachment": True,
                "external_llm_labels": [],
            },
            "obsidian": {"readable": True},
        }
        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            return_value=backend,
        ), patch(
            "atnos.tools.chat_agent_tools.SelfStateTool.execute",
            new=AsyncMock(return_value=state),
        ) as self_state, TestClient(app) as client:
            response = client.post(
                "/chat",
                json={
                    "content": "agentic: use self_state and report current local capabilities",
                    "execution_preferences": {"memory_enabled": False},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        metadata = payload["assistant_message_metadata_json"]["agentic_tool_loop"]
        self.assertIn("Read-only Atnos capability snapshot.", payload["assistant_message"])
        self.assertIn("Internet: skonfigurowany; pliki lokalne: włączone w rozmowie.", payload["assistant_message"])
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(metadata["tools_used"], ["self_state"])
        self.assertEqual(metadata["explicit_readonly_dispatch"], "self_state")
        self.assertEqual(metadata["tool_calls"][0]["name"], "self_state")
        self_state.assert_awaited_once_with()

    def test_read_only_policy_does_not_advertise_or_execute_write_tools(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        forbidden_file = self.root / "should-not-exist.txt"
        backend = _ScriptedBackend()
        backend.responses = [
            ChatResponse(
                content="",
                tool_calls=(ToolCall(name="fs_write", arguments={"path": str(forbidden_file), "content": "bad"}),),
            ),
            ChatResponse(content="Nie mogę tego wykonać w trybie tylko do odczytu."),
        ]

        with patch(
            "atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend",
            return_value=backend,
        ), TestClient(app) as client:
            with app.state.database.session_scope() as session:
                owner = UserRepository(session).get_owner()
                assert owner is not None
                policy = ExecutionPolicyRepository(session).get_for_user(owner.id)
                assert policy is not None
                ExecutionPolicyRepository(session).update(policy, allow_local_file_write=False)
            response = client.post("/chat", json={"content": "agentic: zapisz plik testowy"})

        self.assertEqual(response.status_code, 200)
        advertised = {tool["function"]["name"] for tool in backend.calls[0]["tools"]}
        self.assertIn("fs_list", advertised)
        for name in (
            "fs_write", "fs_delete", "shell_powershell", "obsidian_write",
            "memory_graph_remember", "memory_graph_relate", "mcp_memory_call", "ai_delegate_background",
        ):
            self.assertNotIn(name, advertised)
        self.assertFalse(forbidden_file.exists())


if __name__ == "__main__":
    unittest.main()
