from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.jarvis.core.config import JarvisConfigStore


class JarvisOllamaClient:
    def __init__(self, settings: Settings, config_store: JarvisConfigStore) -> None:
        self.settings = settings
        self.config_store = config_store

    async def generate_text(self, *, prompt: str, model: str | None = None, system: str | None = None) -> str:
        config = self.config_store.current()
        payload_prompt = prompt if not system else f"{system}\n\n{prompt}"
        response = await self._post_chat(
            model=model or config.llm.orchestration_model,
            prompt=payload_prompt,
        )
        return self._extract_chat_content(response)

    async def generate_vision_json(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        model: str | None = None,
    ) -> dict[str, Any]:
        config = self.config_store.current()
        response = await self._post_chat(
            model=model or config.llm.vision_model,
            prompt=prompt,
            images=[base64.b64encode(image_bytes).decode("ascii")],
        )
        text = self._extract_chat_content(response)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    async def _post_chat(self, *, model: str, prompt: str, images: list[str] | None = None) -> dict[str, Any]:
        base_url = self.config_store.current().llm.ollama_base_url or self.settings.ollama_base_url
        timeout_s = self.config_store.current().llm.timeout_s
        user_message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user_message["images"] = images
        payload: dict[str, Any] = {
            "model": model,
            "messages": [user_message],
            "stream": False,
            "think": False,
        }
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _extract_chat_content(response: dict[str, Any]) -> str:
        message = response.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "").strip()
        return str(response.get("response") or "").strip()
