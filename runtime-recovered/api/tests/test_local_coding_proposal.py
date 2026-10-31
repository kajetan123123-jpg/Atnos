from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import httpx
from unittest.mock import AsyncMock, patch

from test_support import make_temp_dir
from atnos.ai_workers.coding_proposal import source_snapshot, validate_proposal
from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegateTaskInput
from atnos.ai_workers.ollama_worker import OllamaWorker
from atnos.core.config import Settings
from atnos.services.background_subagent_service import BackgroundSubagentService
from atnos.llm.ollama import OllamaChatBackend
from atnos.ai_workers.coding_proposal import proposal_schema


class LocalCodingProposalTest(unittest.TestCase):
    def test_oversized_request_is_rejected_before_starting_thread(self):
        with patch("atnos.services.background_subagent_service.threading.Thread") as thread, self.assertRaisesRegex(ValueError, "4000"):
            BackgroundSubagentService(self.settings).start(prompt="x" * 4001, task_type="coding", source_paths=[self.name])
        thread.assert_not_called()

    def setUp(self):
        self.temp = make_temp_dir()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = "runtime-recovered/api/src/example.py"
        self.file = self.root / self.name
        self.file.parent.mkdir(parents=True)
        self.file.write_text("def value():\n    return 1\n", encoding="utf-8")
        self.settings = Settings(app_root=self.root)
        self.edit = {"edits": [{"path": self.name, "old": "return 1", "new": "return 2"}]}

    def test_valid_proposal_is_only_a_reviewable_diff(self):
        sources = source_snapshot(self.root, [self.name])
        result = validate_proposal(json.dumps(self.edit), sources)
        self.assertIn("+    return 2", result["diff"])
        self.assertEqual(result["verification"]["status"], "awaiting_human_review")
        self.assertFalse(result["verification"]["tests_run"])
        self.assertFalse(result["main_worktree_modified"])
        self.assertEqual(self.file.read_text(), sources[self.name])

    def test_rejects_escape_and_non_source(self):
        for name in ["../secret.py", "C:/secret.py", ".env", "runtime-recovered/api/src/a.env"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                source_snapshot(self.root, [name])

    def test_rejects_missing_duplicate_and_outside_edits(self):
        sources = source_snapshot(self.root, [self.name])
        for edit in [dict(path="other.py", old="return 1", new="return 2"), dict(path=self.name, old="absent", new="x"), dict(path=self.name, old="return 1", new="return 1")]:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                validate_proposal(json.dumps({"edits": [edit]}), sources)
        with self.assertRaises(ValueError):
            validate_proposal(json.dumps(self.edit), {self.name: "return 1\nreturn 1\n"})

    def test_coding_cannot_route_to_cloud_or_skip_review(self):
        baseline = dict(task_type="coding", prompt="Fix value", context={}, preferred_worker="ollama", allowed_workers=("ollama",), privacy_mode="strict_local", priority="normal", requested_by="test", requires_approval_for_result=True)
        AiDelegationRouter._validate_input(DelegateTaskInput(**baseline))
        # Existing explicit cloud delegation remains available outside local coding mode.
        AiDelegationRouter._validate_input(DelegateTaskInput(**(baseline | {"preferred_worker": "codex", "allowed_workers": ("codex",), "privacy_mode": "allow_cloud_api"})))
        for change in [{"preferred_worker": "codex"}, {"allowed_workers": ("ollama", "codex")}, {"privacy_mode": "allow_cloud_api"}, {"requires_approval_for_result": False}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                AiDelegationRouter._validate_input(DelegateTaskInput(**(baseline | change)))

    def test_rejects_real_invalid_model_candidate(self):
        bad = {"edits": [{"path": self.name, "old": "    return 1", "new": "    def start(\n    if missing:"}]}
        with self.assertRaisesRegex(ValueError, "syntax invalid"):
            validate_proposal(json.dumps(bad), source_snapshot(self.root, [self.name]))

    def test_worker_records_usage_and_no_test_claim(self):
        response = SimpleNamespace(content=json.dumps(self.edit), input_tokens=123, output_tokens=34)
        with patch("atnos.ai_workers.ollama_worker.OllamaChatBackend") as backend:
            backend.return_value.chat_complete = AsyncMock(return_value=response)
            backend.return_value.model = "local-model"
            result = asyncio.run(OllamaWorker(self.settings).run(task_type="coding", prompt="Fix value", context={"source_paths": [self.name]}))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result["usage"]["input_tokens"], 123)
        self.assertFalse(result.result["verification"]["tests_run"])

    def test_stale_source_or_bad_output_fails(self):
        async def change_source(**kwargs):
            self.file.write_text("changed\n")
            return SimpleNamespace(content=json.dumps(self.edit), input_tokens=1, output_tokens=1)
        with patch("atnos.ai_workers.ollama_worker.OllamaChatBackend") as backend:
            backend.return_value.chat_complete = change_source
            result = asyncio.run(OllamaWorker(self.settings).run(task_type="coding", prompt="Fix value", context={"source_paths": [self.name]}))
        self.assertEqual(result.status, "failed")
        self.assertIn("stale", result.summary)

    def test_background_preserves_code_and_pins_worker(self):
        captured = []
        async def delegate(payload):
            captured.append(payload)
            return {}
        class ImmediateThread:
            def __init__(self, target, name, daemon):
                self.target, self.name = target, name
            def start(self):
                self.target()
        with patch("atnos.services.background_subagent_service.threading.Thread", ImmediateThread), patch("atnos.services.background_subagent_service.DatabaseManager"), patch("atnos.services.background_subagent_service.AiDelegationRouter") as router:
            router.return_value.delegate = delegate
            BackgroundSubagentService(self.settings).start(prompt="def f():\n    pass", task_type="coding", source_paths=[self.name], preferred_worker="codex", privacy_mode="allow_cloud_api")
        self.assertEqual(captured[0].prompt, "def f():\n    pass")
        self.assertEqual(captured[0].allowed_workers, ("ollama",))
        self.assertEqual(captured[0].privacy_mode, "strict_local")
        self.assertTrue(captured[0].requires_approval_for_result)

    def test_general_background_preserves_multiline_prompt_regression(self):
        captured = []
        async def delegate(payload):
            captured.append(payload)
            return {}
        class ImmediateThread:
            def __init__(self, target, name, daemon):
                self.target, self.name = target, name
            def start(self):
                self.target()
        with patch("atnos.services.background_subagent_service.threading.Thread", ImmediateThread), patch("atnos.services.background_subagent_service.DatabaseManager"), patch("atnos.services.background_subagent_service.AiDelegationRouter") as router:
            router.return_value.delegate = delegate
            BackgroundSubagentService(self.settings).start(prompt="  Header\n    indented example\nEnd  ", task_type="general")
        self.assertEqual(captured[0].prompt, "Header\n    indented example\nEnd")

    def test_chat_tool_forwards_explicit_sources(self):
        from atnos.tools.chat_agent_tools import AiDelegateBackgroundTool
        with patch("atnos.services.background_subagent_service.BackgroundSubagentService") as service:
            service.return_value.start.return_value = {"status": "started_background"}
            result = asyncio.run(AiDelegateBackgroundTool(settings=self.settings).execute(prompt="Fix", task_type="coding", source_paths=[self.name]))
        self.assertEqual(result["status"], "started_background")
        self.assertEqual(service.return_value.start.call_args.kwargs["source_paths"], [self.name])

    def test_chat_tool_normalizes_absolute_repo_path_but_rejects_outside(self):
        from atnos.tools.chat_agent_tools import AiDelegateBackgroundTool
        with patch("atnos.services.background_subagent_service.BackgroundSubagentService") as service:
            asyncio.run(AiDelegateBackgroundTool(settings=self.settings).execute(prompt="Fix", task_type="coding", source_paths=[str(self.file)]))
            self.assertEqual(service.return_value.start.call_args.kwargs["source_paths"], [self.name])
        with patch("atnos.services.background_subagent_service.BackgroundSubagentService") as service, self.assertRaisesRegex(ValueError, "inside"):
            asyncio.run(AiDelegateBackgroundTool(settings=self.settings).execute(prompt="Fix", task_type="coding", source_paths=[str(self.root.parent / "outside.py")]))
        service.return_value.start.assert_not_called()

    def test_ollama_receives_schema_and_context_size(self):
        schema = proposal_schema([self.name])
        def respond(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["format"], schema)
            self.assertEqual(payload["options"]["num_ctx"], 8192)
            return httpx.Response(200, json={"message": {"content": json.dumps(self.edit)}, "done_reason": "stop"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("atnos.llm.ollama.httpx.AsyncClient", return_value=client):
            result = asyncio.run(OllamaChatBackend(settings=self.settings, model="local", context_tokens=8192, output_schema=schema).chat_complete(messages=[{"role": "user", "content": "Fix"}]))
        self.assertEqual(json.loads(result.content), self.edit)


if __name__ == "__main__":
    unittest.main()
