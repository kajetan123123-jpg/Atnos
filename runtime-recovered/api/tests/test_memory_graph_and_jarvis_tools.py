from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from atnos.core.config import Settings
from atnos.services.memory_graph_service import MemoryGraphService
from atnos.services.mcp_bridge_service import McpBridgeService
from atnos.services.proactive_briefing_service import ProactiveBriefingService
from atnos.tools.chat_agent_tools import build_chat_agent_registry


class MemoryGraphAndChatToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            app_env="test",
            app_root=root,
            data_dir=data_dir,
        )
        self.settings.ensure_directories()

    def test_memory_graph_remember_relate_search(self) -> None:
        graph = MemoryGraphService(self.settings)
        remembered = graph.remember(
            name="operator",
            entity_type="person",
            observation="Preferuje lokalnego Jarvisa na E:",
        )
        self.assertEqual(remembered["status"], "remembered")
        related = graph.relate(
            from_name="operator",
            to_name="Atnos",
            relation_type="owns",
        )
        self.assertEqual(related["status"], "related")
        hits = graph.search("Jarvis")
        self.assertGreaterEqual(hits["count"], 1)
        self.assertTrue(any("operator" in str(item["entity"].get("name")) for item in hits["hits"]))

    def test_mcp_bridge_memory_tools(self) -> None:
        bridge = McpBridgeService(self.settings)
        servers = bridge.list_servers()
        self.assertTrue(any(item["name"] == "memory" for item in servers["builtin"]))
        result = bridge.call_memory(
            tool="remember",
            arguments={"name": "Comfort", "observation": "Comfy desktop app", "entity_type": "app"},
        )
        self.assertEqual(result["status"], "remembered")
        search = bridge.call_memory(tool="search", arguments={"query": "Comfy"})
        self.assertGreaterEqual(search["count"], 1)

    def test_chat_registry_includes_jarvis_tools(self) -> None:
        class _Session:
            pass

        registry = build_chat_agent_registry(
            settings=self.settings,
            session=_Session(),  # type: ignore[arg-type]
            conversation_id=1,
            provider_registry=None,
            memory_enabled=True,
            files_enabled=True,
            internet_enabled=True,
            include_inventory=False,
        )
        names = {tool.name for tool in registry.list_all()}
        for expected in (
            "memory_graph_remember",
            "memory_graph_search",
            "memory_graph_relate",
            "memory_semantic_search",
            "mcp_list_servers",
            "mcp_memory_call",
            "screen_capture_understand",
            "ai_delegate_background",
            "ai_delegate_status",
        ):
            self.assertIn(expected, names)

    def test_morning_briefing_gate_without_prior(self) -> None:
        class _Repo:
            def __init__(self) -> None:
                self.snapshot_value = type(
                    "Snap",
                    (),
                    {"last_briefing_at": None, "last_user_interaction_at": None},
                )()

            def snapshot(self):
                return self.snapshot_value

        class _Session:
            pass

        service = ProactiveBriefingService(
            _Session(),  # type: ignore[arg-type]
            settings=self.settings,
            provider_registry=None,  # type: ignore[arg-type]
        )
        service.runtime_settings = _Repo()  # type: ignore[method-assign]
        self.assertTrue(service.should_emit_scheduled_morning())


if __name__ == "__main__":
    unittest.main()
