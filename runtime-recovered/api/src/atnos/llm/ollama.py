from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from atnos.core.config import Settings
from atnos.core.logging import redact_secrets
from atnos.llm.base import ChatResponse, ToolCall, LLMRunCancelled
from atnos.providers.base import ProviderResponseError, ProviderTimeoutError, ProviderUnavailableError
from atnos.services.ollama_model_roles_service import OllamaModelRolesService

TokenCallback = Callable[[str], None]


class OllamaChatBackend:
    # 8k fits tool schemas on 16 GB VRAM without the 12k cold-load tax that made chat unusable.
    TOOL_CONTEXT_TOKENS = 8192

    def __init__(
        self,
        *,
        settings: Settings,
        model: str | None = None,
        timeout_s: float | None = None,
        think: bool | None = None,
        context_tokens: int | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        self.settings = settings
        self.model = model or settings.ollama_main_model or settings.ollama_chat_model or OllamaModelRolesService.DEFAULT_MAIN_TEXT_MODEL
        self.timeout_s = timeout_s or settings.ollama_timeout_s
        self.think = think
        self.context_tokens = context_tokens
        self.output_schema = output_schema

    async def chat_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_token: TokenCallback | None = None,
    ) -> ChatResponse:
        if on_token is not None:
            return await self._chat_complete_stream(
                messages=messages,
                system=system,
                tools=tools,
                on_token=on_token,
            )
        payload_messages = self._build_messages(system=system, messages=messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "stream": False,
            "keep_alive": "30m",
        }
        if tools:
            payload["tools"] = tools
            # Ollama allocates only 4k context by default on a 16 GB GPU.
            # Atnos' system prompt plus tool schemas exceed that limit.
            payload["options"] = {"num_ctx": self.TOOL_CONTEXT_TOKENS}
        if self.think is not None:
            payload["think"] = self.think
        if self.context_tokens is not None:
            payload.setdefault("options", {})["num_ctx"] = max(2048, min(8192, self.context_tokens))
        if self.output_schema is not None:
            payload["format"] = self.output_schema

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Ollama /api/chat request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Ollama /api/chat is unavailable.") from exc

        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"Ollama /api/chat returned HTTP {response.status_code}: {self._extract_error_message(response)}")
        if response.status_code >= 400:
            raise ProviderResponseError(self._extract_error_message(response))

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Ollama /api/chat returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("Ollama /api/chat returned a non-object payload.")

        message = data.get("message")
        content = ""
        tool_calls: tuple[ToolCall, ...] = ()
        if isinstance(message, dict):
            raw_content = message.get("content")
            content = raw_content.strip() if isinstance(raw_content, str) else ""
            tool_calls = self._parse_tool_calls(message.get("tool_calls"))

        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=self._int(data.get("prompt_eval_count")),
            output_tokens=self._int(data.get("eval_count")),
            finish_reason=self._str(data.get("done_reason")),
            raw_payload=data,
        )

    async def _chat_complete_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        on_token: TokenCallback,
    ) -> ChatResponse:
        payload_messages = self._build_messages(system=system, messages=messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "stream": True,
            "keep_alive": "30m",
        }
        if tools:
            payload["tools"] = tools
            payload["options"] = {"num_ctx": self.TOOL_CONTEXT_TOKENS}
        if self.think is not None:
            payload["think"] = self.think
        if self.context_tokens is not None:
            payload.setdefault("options", {})["num_ctx"] = max(2048, min(8192, self.context_tokens))
        if self.output_schema is not None:
            payload["format"] = self.output_schema

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        finish_reason: str | None = None
        raw_payload: dict[str, Any] | None = None

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                async with client.stream(
                    "POST",
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=payload,
                ) as response:
                    if response.status_code >= 500:
                        await response.aread()
                        raise ProviderUnavailableError(
                            f"Ollama /api/chat returned HTTP {response.status_code}: {self._extract_error_message(response)}")
                    if response.status_code >= 400:
                        await response.aread()
                        raise ProviderResponseError(self._extract_error_message(response))
                    async for line in response.aiter_lines():
                        if not line or not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        raw_payload = data
                        message = data.get("message")
                        if isinstance(message, dict):
                            delta = message.get("content")
                            if isinstance(delta, str) and delta:
                                content_parts.append(delta)
                                try:
                                    on_token(delta)
                                except LLMRunCancelled:
                                    raise
                                except Exception:
                                    pass
                            parsed_calls = self._parse_tool_calls(message.get("tool_calls"))
                            if parsed_calls:
                                tool_calls.extend(parsed_calls)
                        if data.get("done") is True:
                            input_tokens = self._int(data.get("prompt_eval_count"))
                            output_tokens = self._int(data.get("eval_count"))
                            finish_reason = self._str(data.get("done_reason"))
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Ollama /api/chat request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Ollama /api/chat is unavailable.") from exc

        content = "".join(content_parts).strip()
        return ChatResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            raw_payload=raw_payload,
        )

    @staticmethod
    def _build_messages(*, system: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if system and system.strip():
            result.append({"role": "system", "content": system.strip()})
        result.extend(messages)
        return result

    @classmethod
    def _parse_tool_calls(cls, payload: object) -> tuple[ToolCall, ...]:
        if not isinstance(payload, list):
            return ()
        calls: list[ToolCall] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            function_payload = item.get("function")
            if not isinstance(function_payload, dict):
                continue
            name = function_payload.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            raw_arguments = function_payload.get("arguments")
            calls.append(
                ToolCall(
                    id=cls._str(item.get("id")),
                    name=name.strip(),
                    arguments=cls._parse_arguments(raw_arguments),
                    raw_arguments=raw_arguments if isinstance(raw_arguments, (str, dict)) else None,
                )
            )
        return tuple(calls)

    @staticmethod
    def _parse_arguments(raw_arguments: object) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return dict(raw_arguments)
        if not isinstance(raw_arguments, str) or not raw_arguments.strip():
            return {}
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Ollama returned status {response.status_code}."
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, str) and error.strip():
                # Only the provider's error field; never echo an entire response
                # object, request body or credentials into durable chat history.
                return str(redact_secrets(error.strip()))[:512]
        return f"Ollama returned status {response.status_code}."

    @staticmethod
    def _int(value: object) -> int | None:
        return value if isinstance(value, int) else None

    @staticmethod
    def _str(value: object) -> str | None:
        return value if isinstance(value, str) else None
