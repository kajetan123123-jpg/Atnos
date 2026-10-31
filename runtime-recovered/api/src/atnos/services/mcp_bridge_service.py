"""Cienki most MCP → lokalne możliwości Atnosa.

Nie uruchamia zewnętrznych procesów npx w domyślnym torze.
`server=memory` mapuje na MemoryGraphService (kształt MCP memory).
Opcjonalne serwery z configu `mcp-servers.json` mogą być listowane.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.services.memory_graph_service import MemoryGraphService


class McpBridgeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.memory = MemoryGraphService(settings)
        config_root = Path(settings.data_dir).expanduser().resolve().parent / "config"
        self.config_path = config_root / "mcp-servers.json"

    def list_servers(self) -> dict[str, Any]:
        configured: list[dict[str, Any]] = []
        if self.config_path.exists():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("servers"), list):
                    for item in raw["servers"]:
                        if isinstance(item, dict) and item.get("name"):
                            configured.append(
                                {
                                    "name": str(item.get("name")),
                                    "transport": str(item.get("transport") or "stdio"),
                                    "enabled": bool(item.get("enabled", True)),
                                    "notes": str(item.get("notes") or ""),
                                }
                            )
            except (OSError, json.JSONDecodeError):
                configured = []
        builtin = [
            {
                "name": "memory",
                "transport": "local",
                "enabled": True,
                "notes": "Wbudowany memory graph Atnosa (MCP-compatible shape).",
            }
        ]
        return {
            "builtin": builtin,
            "configured": configured,
            "config_path": str(self.config_path),
            "hint": (
                "Domyślnie używaj tools memory_graph_* / memory_semantic_search. "
                "Zewnętrzne MCP stdio można dodać później w mcp-servers.json."
            ),
        }

    def call_memory(self, *, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        name = str(tool or "").strip()
        if name in {"create_entities", "add_observations", "remember"}:
            return self.memory.remember(
                name=str(args.get("name") or args.get("entity_name") or ""),
                entity_type=str(args.get("entity_type") or args.get("type") or "fact"),
                observation=str(args.get("observation") or args.get("content") or ""),
            )
        if name in {"create_relations", "relate"}:
            return self.memory.relate(
                from_name=str(args.get("from_name") or args.get("from") or ""),
                to_name=str(args.get("to_name") or args.get("to") or ""),
                relation_type=str(args.get("relation_type") or args.get("relation") or "related_to"),
            )
        if name in {"search_nodes", "search", "read_graph"}:
            if name == "read_graph":
                return self.memory.snapshot()
            return self.memory.search(str(args.get("query") or ""), limit=int(args.get("limit") or 12))
        raise ValueError(f"Unsupported MCP memory tool '{tool}'.")
