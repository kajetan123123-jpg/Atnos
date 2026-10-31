from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.api.routes.chat import (
    ChatStreamCancelled,
    _queue_get_with_timeout,
    _QUEUE_TIMEOUT,
)


class ChatStreamCancelTest(unittest.TestCase):
    def test_ollama_stream_propagates_cancellation(self) -> None:
        import httpx, json
        from unittest.mock import patch
        from atnos.core.config import Settings
        from atnos.llm.ollama import OllamaChatBackend
        line=json.dumps({'message':{'content':'audit'},'done':False})+'\n'
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=line)))
        def cancelled(_token): raise ChatStreamCancelled('test token cancellation')
        with patch('atnos.llm.ollama.httpx.AsyncClient',return_value=client):
            with self.assertRaises(ChatStreamCancelled):
                asyncio.run(OllamaChatBackend(settings=Settings(_env_file=None),model='audit').chat_complete(messages=[{'role':'user','content':'test'}],on_token=cancelled))

    def test_real_orchestrator_cancel_stops_before_tool_effect(self) -> None:
        from atnos.agentic import AgentContext, OrchestratorAgent
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.tools.base import BaseTool
        from atnos.tools.registry import ToolRegistry
        effects=[]
        class Tool(BaseTool):
            name='audit_effect'; description='test'; parameters={'type':'object','properties':{}}
            async def execute(self,**kwargs): effects.append('executed'); return {'status':'ok'}
        class Backend:
            async def chat_complete(self,**kwargs): return ChatResponse(content='',tool_calls=(ToolCall(name='audit_effect',arguments={}),))
        registry=ToolRegistry(); registry.register_tool(Tool())
        def cancelled(event):
            if event['type']=='tool_start': raise ChatStreamCancelled('test cancel before effect')
        with self.assertRaises(ChatStreamCancelled):
            asyncio.run(OrchestratorAgent().run('audit',AgentContext(system_prompt='test',backend=Backend(),registry=registry,max_turns=2),on_event=cancelled))
        self.assertEqual(effects,[])

    def test_emit_raises_after_cancel_event(self) -> None:
        """STAB-CHAT-001: once cancel is set, further stream emits must fail closed."""
        event_queue: queue.Queue = queue.Queue()
        cancel_event = threading.Event()

        def emit(event: dict) -> None:
            if cancel_event.is_set():
                raise ChatStreamCancelled("Client cancelled the chat stream.")
            event_queue.put(event)

        emit({"type": "status", "stage": "start"})
        self.assertEqual(event_queue.get_nowait()["stage"], "start")

        cancel_event.set()
        with self.assertRaises(ChatStreamCancelled):
            emit({"type": "token", "text": "should-not-queue"})
        self.assertTrue(event_queue.empty())

    def test_queue_get_timeout_sentinel(self) -> None:
        event_queue: queue.Queue = queue.Queue()
        self.assertIs(_queue_get_with_timeout(event_queue, 0.05), _QUEUE_TIMEOUT)
        event_queue.put({"type": "token", "text": "x"})
        self.assertEqual(_queue_get_with_timeout(event_queue, 0.05)["text"], "x")

    def test_disconnect_loop_sets_cancel_event(self) -> None:
        """Generator must set cancel_event when the ASGI client reports disconnect."""
        cancel_event = threading.Event()
        event_queue: queue.Queue = queue.Queue()
        event_queue.put({"type": "status", "stage": "start"})
        event_queue.put(None)

        class FakeRequest:
            def __init__(self) -> None:
                self.calls = 0

            async def is_disconnected(self) -> bool:
                self.calls += 1
                # First poll allows one event; subsequent polls simulate abort.
                return self.calls > 1

        request = FakeRequest()

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        cancel_event.set()
                        break
                    item = await asyncio.to_thread(_queue_get_with_timeout, event_queue, 0.5)
                    if item is _QUEUE_TIMEOUT:
                        continue
                    if item is None:
                        break
                    yield f"data: {item}\n\n"
                    if await request.is_disconnected():
                        cancel_event.set()
                        break
            finally:
                cancel_event.set()

        async def run() -> list[str]:
            chunks: list[str] = []
            async for chunk in event_generator():
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(run())
        self.assertEqual(len(chunks), 1)
        self.assertTrue(cancel_event.is_set())
        self.assertGreaterEqual(request.calls, 2)


if __name__ == "__main__":
    unittest.main()
