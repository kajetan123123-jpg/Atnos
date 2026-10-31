from __future__ import annotations

from typing import Any

__all__ = ["ShortAgent", "ShortAgentResult"]


def __getattr__(name: str) -> Any:
    if name in {"ShortAgent", "ShortAgentResult"}:
        from content.short_agent import ShortAgent, ShortAgentResult

        return {"ShortAgent": ShortAgent, "ShortAgentResult": ShortAgentResult}[name]
    raise AttributeError(f"module 'content' has no attribute {name!r}")
