from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.agentic import AgentContext, LoopGuard, OrchestratorAgent
from atnos.llm.base import ChatResponse, ToolCall
from atnos.llm.ollama import OllamaChatBackend
from atnos.tools import ToolExecutor, ToolRegistry, discover_builtin_tools


class _ScriptedBackend:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def chat_complete(self, *, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if not self.responses:
            return ChatResponse(content="no more responses")
        return self.responses.pop(0)


class AgenticCoreTest(unittest.TestCase):
    def test_unexecuted_required_operation_does_not_stream_unverified_prose(self):
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        events = []
        class StreamingBackend:
            async def chat_complete(self, *, messages, system=None, tools=None, on_token=None):
                if on_token:
                    on_token("I already executed it.")
                return ChatResponse(content="I already executed it.")
        result = asyncio.run(OrchestratorAgent().run("Use echo", AgentContext(system_prompt="Use tools", backend=StreamingBackend(), registry=registry, max_turns=2, required_tool_names=("echo",)), on_event=events.append))
        self.assertEqual(result.tool_calls_made, ())
        self.assertFalse(any(item["type"] == "token" for item in events))

    def test_required_remaining_tool_is_reminded_after_partial_execution(self):
        from atnos.tools.builtin.echo import EchoTool
        class SecondEcho(EchoTool):
            name = "second_echo"
        registry = ToolRegistry()
        registry.register_tool(EchoTool())
        registry.register_tool(SecondEcho())
        backend = _ScriptedBackend([
            ChatResponse(content="", tool_calls=(ToolCall(name="echo", arguments={"text": "read"}),)),
            ChatResponse(content="I read it; done."),
            ChatResponse(content="", tool_calls=(ToolCall(name="second_echo", arguments={"text": "delegated"}),)),
            ChatResponse(content="Both steps executed."),
        ])
        result = asyncio.run(OrchestratorAgent().run("Read then delegate", AgentContext(system_prompt="Use tools", backend=backend, registry=registry, max_turns=4, required_tool_names=("echo", "second_echo"))))
        self.assertEqual([item.name for item in result.tool_outcomes], ["echo", "second_echo"])
        self.assertEqual(result.turns_used, 4)
        self.assertIn("name: second_echo.", str(backend.calls[2]["messages"]))

    def test_required_tool_gets_one_reminder_inside_existing_turn_budget(self) -> None:
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        backend = _ScriptedBackend([
            ChatResponse(content="I will use echo."),
            ChatResponse(content="", tool_calls=(ToolCall(name="echo", arguments={"text": "actual"}),)),
            ChatResponse(content="actual"),
        ])
        result = asyncio.run(OrchestratorAgent().run("Use echo", AgentContext(system_prompt="Use tools", backend=backend, registry=registry, max_turns=3, required_tool_names=("echo",))))
        self.assertEqual(result.turns_used, 3)
        self.assertEqual(result.tool_outcomes[0].data, {"text": "actual"})
        self.assertIn("only reminder", str(backend.calls[1]["messages"]))

    def test_required_tool_reminder_cannot_loop_or_exceed_turn_budget(self) -> None:
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        backend = _ScriptedBackend([ChatResponse(content="prose"), ChatResponse(content="still prose")])
        result = asyncio.run(OrchestratorAgent().run("Use echo", AgentContext(system_prompt="Use tools", backend=backend, registry=registry, max_turns=2, required_tool_names=("echo",))))
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(result.tool_calls_made, ())

    def test_unknown_tool_feedback_lists_exact_available_names_without_execution(self) -> None:
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        result = asyncio.run(ToolExecutor(registry).execute_tool_call(ToolCall(name="web_learn", arguments={})))
        self.assertFalse(result.success)
        self.assertIn("Available tool names:", result.error)
        self.assertIn("echo", result.error)
        self.assertIn("no operation was executed", result.error)

    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_ollama_backend_parses_tool_calls(self) -> None:
        payload = [
            {
                "id": "call-1",
                "function": {
                    "name": "echo",
                    "arguments": '{"text":"hello"}',
                },
            }
        ]

        parsed = OllamaChatBackend._parse_tool_calls(payload)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].name, "echo")
        self.assertEqual(parsed[0].arguments, {"text": "hello"})
        self.assertEqual(parsed[0].id, "call-1")

    def test_ollama_tool_request_reserves_enough_context_for_schemas(self) -> None:
        captured: list[dict[str, object]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "ok"}, "done_reason": "stop"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        backend = OllamaChatBackend(settings=self.settings, model="qwen3.5:9b")
        with patch("atnos.llm.ollama.httpx.AsyncClient", return_value=client):
            result = asyncio.run(
                backend.chat_complete(
                    messages=[{"role": "user", "content": "Read status"}],
                    tools=[{"type": "function", "function": {"name": "self_state"}}],
                )
            )

        self.assertEqual(result.content, "ok")
        self.assertEqual(captured[0]["options"], {"num_ctx": 8192})

    def test_ollama_stream_keeps_tool_calls_from_multiple_chunks(self) -> None:
        chunks = [
            {"message": {"tool_calls": [{"id": "one", "function": {"name": "echo", "arguments": {"text": "first"}}}]}, "done": False},
            {"message": {"tool_calls": [{"id": "two", "function": {"name": "echo", "arguments": {"text": "second"}}}]}, "done": False},
            {"message": {"content": "done"}, "done": True, "done_reason": "stop"},
        ]

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content="\n".join(json.dumps(chunk) for chunk in chunks) + "\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        backend = OllamaChatBackend(settings=self.settings, model="qwen3.5:9b")
        tokens: list[str] = []
        with patch("atnos.llm.ollama.httpx.AsyncClient", return_value=client):
            result = asyncio.run(
                backend.chat_complete(
                    messages=[{"role": "user", "content": "Use both tools"}],
                    tools=[{"type": "function", "function": {"name": "echo"}}],
                    on_token=tokens.append,
                )
            )

        self.assertEqual([call.id for call in result.tool_calls], ["one", "two"])
        self.assertEqual([call.arguments["text"] for call in result.tool_calls], ["first", "second"])
        self.assertEqual(result.content, "done")
        self.assertEqual(tokens, ["done"])

    def test_tool_registry_discovers_echo_and_executor_runs_it(self) -> None:
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        echo = registry.get("echo")
        self.assertIsNotNone(echo)
        assert echo is not None

        result = asyncio.run(ToolExecutor(registry).execute_tool_call(ToolCall(name="echo", arguments={"text": "ok"})))

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"text": "ok"})
        self.assertIn("echo", registry.get_ollama_tools()[0]["function"]["name"])

    def test_loop_guard_stops_identical_calls(self) -> None:
        guard = LoopGuard(max_identical_calls=2, max_same_tool_consecutive=10)

        self.assertFalse(guard.register_tool_call("echo", {"text": "x"}).should_stop)
        self.assertFalse(guard.register_tool_call("echo", {"text": "x"}).should_stop)
        verdict = guard.register_tool_call("echo", {"text": "x"})

        self.assertTrue(verdict.should_stop)
        self.assertEqual(verdict.severity, "stop")

    def test_orchestrator_executes_tool_then_returns_final_response(self) -> None:
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        backend = _ScriptedBackend(
            [
                ChatResponse(
                    content="",
                    tool_calls=(ToolCall(name="echo", arguments={"text": "hello"}, id="call-1"),),
                ),
                ChatResponse(content="Wynik: hello"),
            ]
        )

        result = asyncio.run(
            OrchestratorAgent().run(
                "echo hello",
                AgentContext(
                    system_prompt="Use tools when helpful.",
                    backend=backend,
                    registry=registry,
                    max_turns=5,
                ),
            )
        )

        self.assertEqual(result.stopped_reason, "completed")
        self.assertEqual(result.final_response, "Wynik: hello")
        self.assertEqual(len(result.tool_calls_made), 1)
        second_call_messages = backend.calls[1]["messages"]
        self.assertIsInstance(second_call_messages, list)
        self.assertEqual(second_call_messages[-1]["role"], "tool")
        self.assertIn('"text": "hello"', second_call_messages[-1]["content"])

    def test_orchestrator_includes_history_before_current_message(self) -> None:
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        backend = _ScriptedBackend([ChatResponse(content="Kontynuuję Comfort")])

        result = asyncio.run(
            OrchestratorAgent().run(
                "to nie strona, lokalna aplikacja",
                AgentContext(
                    system_prompt="Keep task context.",
                    backend=backend,
                    registry=registry,
                    max_turns=3,
                ),
                history_messages=[
                    {"role": "user", "content": "otwórz aplikację Comfort"},
                    {"role": "assistant", "content": "Próbowałem otworzyć stronę."},
                ],
            )
        )

        self.assertEqual(result.stopped_reason, "completed")
        first_messages = backend.calls[0]["messages"]
        self.assertEqual(first_messages[0]["content"], "otwórz aplikację Comfort")
        self.assertEqual(first_messages[-1]["content"], "to nie strona, lokalna aplikacja")
        self.assertEqual(len(first_messages), 3)

    def test_orchestrator_stops_repeated_tool_loop(self) -> None:
        registry = ToolRegistry()
        discover_builtin_tools(registry)
        backend = _ScriptedBackend(
            [
                ChatResponse(tool_calls=(ToolCall(name="echo", arguments={"text": "loop"}),), content=""),
                ChatResponse(tool_calls=(ToolCall(name="echo", arguments={"text": "loop"}),), content=""),
                ChatResponse(tool_calls=(ToolCall(name="echo", arguments={"text": "loop"}),), content=""),
            ]
        )

        result = asyncio.run(
            OrchestratorAgent().run(
                "loop",
                AgentContext(system_prompt="", backend=backend, registry=registry, max_turns=5),
            )
        )

        self.assertEqual(result.stopped_reason, "loop_detected")


if __name__ == "__main__":
    unittest.main()
