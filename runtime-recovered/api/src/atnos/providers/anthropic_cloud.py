from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx

from atnos.db.models.enums import ModelTask
from atnos.providers.base import (
    ProviderCapability,
    ProviderReadiness,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderExecutionResult,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUsage,
)
from atnos.services.context_builder_service import LocalAttachmentMetadata, ProviderExecutionContext


class AnthropicCloudProviderAdapter(ProviderDescriptor):
    API_VERSION = "2023-06-01"
    MODEL_ENV_BY_TASK = {
        ModelTask.GENERAL_CHAT: "ANTHROPIC_CHAT_MODEL",
        ModelTask.ORCHESTRATION: "ANTHROPIC_CHAT_MODEL",
        ModelTask.CODING: "ANTHROPIC_CODE_MODEL",
        ModelTask.SEARCH_RESEARCH: "ANTHROPIC_RESEARCH_MODEL",
        ModelTask.VISION: "ANTHROPIC_VISION_MODEL",
        ModelTask.SYNTHESIS: "ANTHROPIC_SYNTHESIS_MODEL",
    }
    DEFAULT_MODEL_BY_TASK = {
        ModelTask.GENERAL_CHAT: "claude-sonnet-4-20250514",
        ModelTask.ORCHESTRATION: "claude-sonnet-4-20250514",
        ModelTask.CODING: "claude-sonnet-4-20250514",
        ModelTask.SEARCH_RESEARCH: "claude-sonnet-4-20250514",
        ModelTask.VISION: "claude-sonnet-4-20250514",
        ModelTask.SYNTHESIS: "claude-sonnet-4-20250514",
    }
    SUPPORTED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    def __init__(self, *, app_root: Path) -> None:
        super().__init__(
            key="anthropic-cloud",
            label="Anthropic Cloud",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
        )
        self.app_root = app_root
        self.request_timeout_s = float(os.getenv("ANTHROPIC_TIMEOUT_S", "120"))

    def configuration_status(self, *, provider) -> ProviderReadiness:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            return ProviderReadiness(ready=True)
        return ProviderReadiness(
            ready=False,
            reason="Brakuje API key dla providera Anthropic.",
            failure_kind="provider_configuration",
        )

    def execute(self, *, task, context, provider, model_config) -> ProviderExecutionResult:
        if not self.supports_task(task):
            raise ProviderConfigurationError(f"Provider '{self.key}' does not support task '{task.value}'.")

        readiness = self.configuration_status(provider=provider)
        if not readiness.ready:
            raise ProviderConfigurationError(
                "Provider Anthropic nie jest skonfigurowany. Dodaj API key albo wroc do lokalnego runtime."
            )
        api_key = os.getenv("ANTHROPIC_API_KEY")

        model_name = self._resolve_model_name(task)
        content_blocks = self._build_content_blocks(task=task, context=context)
        endpoint = f"{(provider.endpoint_url or 'https://api.anthropic.com').rstrip('/')}/v1/messages"
        payload = {
            "model": model_name,
            "max_tokens": model_config.max_output_tokens or 1024,
            "messages": [
                {
                    "role": "user",
                    "content": content_blocks,
                }
            ],
        }

        try:
            with httpx.Client(timeout=self.request_timeout_s) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": self.API_VERSION,
                        "content-type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Anthropic request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Anthropic API is unavailable.") from exc

        if response.status_code >= 500:
            raise ProviderUnavailableError("Anthropic API returned a server error.")
        if response.status_code >= 400:
            raise ProviderResponseError(self._extract_error_message(response))

        data = response.json()
        text_fragments: list[str] = []
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                text_fragments.append(block["text"])
        content = "".join(text_fragments).strip()
        if not content:
            raise ProviderResponseError("Anthropic returned an empty response.")

        usage_payload = data.get("usage", {})
        input_tokens = usage_payload.get("input_tokens") if isinstance(usage_payload, dict) else None
        output_tokens = usage_payload.get("output_tokens") if isinstance(usage_payload, dict) else None
        total_tokens = None
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total_tokens = input_tokens + output_tokens

        return ProviderExecutionResult(
            content=content,
            provider_message_id=data.get("id"),
            usage=ProviderUsage(
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                total_tokens=total_tokens,
            ),
            finish_reason=data.get("stop_reason"),
            raw_metadata={
                "provider_key": self.key,
                "runtime_model": data.get("model", model_name),
                "type": data.get("type"),
            },
        )

    def _build_content_blocks(
        self,
        *,
        task: ModelTask,
        context: ProviderExecutionContext,
    ) -> list[dict[str, object]]:
        text_block = {"type": "text", "text": context.provider_prompt}

        if task != ModelTask.VISION:
            return [text_block]

        if not context.allow_external_attachment_content:
            raise ProviderConfigurationError(
                "External vision execution is blocked by policy: attachment content egress is disabled."
            )

        image_blocks = self._build_image_blocks(context.local_attachments)
        if not image_blocks:
            raise ProviderConfigurationError(
                "External vision execution requires at least one readable local image attachment."
            )
        return [*image_blocks, text_block]

    def _build_image_blocks(
        self,
        local_attachments: tuple[LocalAttachmentMetadata, ...],
    ) -> list[dict[str, object]]:
        blocks: list[dict[str, object]] = []
        for attachment in local_attachments:
            attachment_path = self._resolve_attachment_path(attachment.storage_path)
            if not attachment_path.exists():
                continue

            media_type = self._media_type_for_path(attachment_path)
            if media_type is None:
                continue

            encoded = base64.b64encode(attachment_path.read_bytes()).decode("ascii")
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": encoded,
                    },
                }
            )
        return blocks

    def _resolve_attachment_path(self, storage_path: str) -> Path:
        candidate = Path(storage_path)
        if candidate.is_absolute():
            return candidate
        return (self.app_root / candidate).resolve()

    def _resolve_model_name(self, task: ModelTask) -> str:
        env_key = self.MODEL_ENV_BY_TASK.get(task)
        if env_key is not None:
            env_value = os.getenv(env_key)
            if env_value:
                return env_value

        default_model = self.DEFAULT_MODEL_BY_TASK.get(task)
        if default_model is None:
            raise ProviderConfigurationError(f"No Anthropic model mapping exists for task '{task.value}'.")
        return default_model

    @classmethod
    def _extract_error_message(cls, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Anthropic returned status {response.status_code}."

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if isinstance(message, str) and message:
                    return message

        return f"Anthropic returned status {response.status_code}."

    @classmethod
    def _media_type_for_path(cls, path: Path) -> str | None:
        suffix = path.suffix.lower()
        mapping = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = mapping.get(suffix)
        if media_type in cls.SUPPORTED_IMAGE_MEDIA_TYPES:
            return media_type
        return None
