from __future__ import annotations

from atnos.tools.base import BaseTool
from atnos.tools.registry import default_tool_registry


class EchoTool(BaseTool):
    name = "echo"
    description = "Return the provided text unchanged. Useful for agent loop smoke tests."
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to return unchanged.",
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs):
        return {"text": str(kwargs.get("text") or "")}


default_tool_registry.register_tool(EchoTool())
