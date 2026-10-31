from __future__ import annotations

import base64
import os
from pathlib import Path
from time import perf_counter

import httpx

from atnos.db.models.enums import ModelTask
from atnos.providers.base import (
    ProviderCapability,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderExecutionResult,
    ProviderHealthCheck,
    ProviderReadiness,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUsage,
)
from atnos.services.context_builder_service import LocalAttachmentMetadata, ProviderExecutionContext


class OpenAICompatibleCloudProviderAdapter(ProviderDescriptor):
    MODEL_ENV_BY_TASK = {
        ModelTask.GENERAL_CHAT: "CHAT_MODEL",
        ModelTask.ORCHESTRATION: "CHAT_MODEL",
        ModelTask.CODING: "CODE_MODEL",
        ModelTask.SEARCH_RESEARCH: "RESEARCH_MODEL",
        ModelTask.VISION: "VISION_MODEL",
        ModelTask.SYNTHESIS: "SYNTHESIS_MODEL",
    }
    DEFAULT_MODEL_BY_TASK = {
        ModelTask.GENERAL_CHAT: "gpt-5.2",
        ModelTask.ORCHESTRATION: "gpt-5.2",
        ModelTask.CODING: "gpt-5.2",
        ModelTask.SEARCH_RESEARCH: "gpt-5.2",
        ModelTask.VISION: "gpt-5.2",
        ModelTask.SYNTHESIS: "gpt-5.2",
    }
    SUPPORTED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    def __init__(
        self,
        *,
        provider_key: str,
        label: str,
        app_root: Path,
        api_key_env_var: str,
        default_base_url: str,
        model_env_prefix: str,
    ) -> None:
        super().__init__(
            key=provider_key,
            label=label,
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
        )
        self.app_root = app_root
        self.api_key_env_var = api_key_env_var
        self.default_base_url = default_base_url.rstrip("/")
        self.model_env_prefix = model_env_prefix
        self.request_timeout_s = float(os.getenv(f"{model_env_prefix}_TIMEOUT_S", "120"))
        self.health_timeout_s = float(os.getenv(f"{model_env_prefix}_HEALTH_TIMEOUT_S", "10"))

    def configuration_status(self, *, provider) -> ProviderReadiness:
        api_key = os.getenv(self.api_key_env_var)
        if api_key:
            return ProviderReadiness(ready=True)
        return ProviderReadiness(
            ready=False,
            reason=f"Brakuje API key dla providera {self.label}.",
            failure_kind="provider_configuration",
        )

    def check_health(self, *, provider) -> ProviderHealthCheck:
        readiness = self.configuration_status(provider=provider)
        if not readiness.ready:
            return ProviderHealthCheck(
                status="unknown",
                status_reason=readiness.reason or f"{self.label} is not configured.",
            )

        endpoint = f"{(provider.endpoint_url or self.default_base_url).rstrip('/')}/v1/models"
        started = perf_counter()
        try:
            with httpx.Client(timeout=self.health_timeout_s) as client:
                response = client.get(
                    endpoint,
                    headers={"Authorization": f"Bearer {os.getenv(self.api_key_env_var)}"},
                )
        except httpx.TimeoutException:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason=f"{self.label} API health check timed out.",
            )
        except httpx.HTTPError as exc:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason=f"{self.label} API health check failed: {exc}",
            )

        latency_ms = max(1, int((perf_counter() - started) * 1000))
        if response.status_code >= 400:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason=self._extract_error_message(response),
                latency_ms=latency_ms,
            )

        installed_models = self._parse_model_ids(response)
        return ProviderHealthCheck(
            status="healthy",
            status_reason=f"{self.label} responded to /v1/models.",
            installed_models=installed_models,
            latency_ms=latency_ms,
        )

    def execute(self, *, task, context, provider, model_config) -> ProviderExecutionResult:
        if not self.supports_task(task):
            raise ProviderConfigurationError(f"Provider '{self.key}' does not support task '{task.value}'.")

        readiness = self.configuration_status(provider=provider)
        if not readiness.ready:
            raise ProviderConfigurationError(
                f"Provider {self.label} nie jest skonfigurowany. Dodaj API key albo wroc do lokalnego runtime."
            )

        endpoint = f"{(provider.endpoint_url or self.default_base_url).rstrip('/')}/v1/chat/completions"
        payload = {
            "model": self._resolve_model_name(task, model_config_runtime_name=model_config.runtime_model_name),
            "max_tokens": model_config.max_output_tokens or 1024,
            "messages": [
                {
                    "role": "user",
                    "content": self._build_content_blocks(task=task, context=context),
                }
            ],
        }

        try:
            with httpx.Client(timeout=self.request_timeout_s) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {os.getenv(self.api_key_env_var)}",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.label} request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.label} API is unavailable.") from exc

        if response.status_code >= 500:
            raise ProviderUnavailableError(f"{self.label} API returned a server error.")
        if response.status_code >= 400:
            raise ProviderResponseError(self._extract_error_message(response))

        data = response.json()
        content = self._parse_content_text(data)
        if not content:
            raise ProviderResponseError(f"{self.label} returned an empty response.")

        usage_payload = data.get("usage", {})
        input_tokens = usage_payload.get("prompt_tokens") if isinstance(usage_payload, dict) else None
        output_tokens = usage_payload.get("completion_tokens") if isinstance(usage_payload, dict) else None
        total_tokens = usage_payload.get("total_tokens") if isinstance(usage_payload, dict) else None

        return ProviderExecutionResult(
            content=content,
            provider_message_id=data.get("id"),
            usage=ProviderUsage(
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                total_tokens=total_tokens if isinstance(total_tokens, int) else None,
            ),
            finish_reason=self._extract_finish_reason(data),
            raw_metadata={
                "provider_key": self.key,
                "runtime_model": self._extract_runtime_model(data, payload["model"]),
                "object": data.get("object"),
            },
        )

    def _build_content_blocks(
        self,
        *,
        task: ModelTask,
        context: ProviderExecutionContext,
    ) -> str | list[dict[str, object]]:
        if task != ModelTask.VISION:
            return context.provider_prompt

        if not context.allow_external_attachment_content:
            raise ProviderConfigurationError(
                "External vision execution is blocked by policy: attachment content egress is disabled."
            )

        blocks = self._build_image_blocks(context.local_attachments)
        if not blocks:
            raise ProviderConfigurationError(
                "External vision execution requires at least one readable local image attachment."
            )
        blocks.append({"type": "text", "text": context.provider_prompt})
        return blocks

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
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{encoded}",
                    },
                }
            )
        return blocks

    def _resolve_attachment_path(self, storage_path: str) -> Path:
        candidate = Path(storage_path)
        if candidate.is_absolute():
            return candidate
        return (self.app_root / candidate).resolve()

    def _resolve_model_name(
        self,
        task: ModelTask,
        *,
        model_config_runtime_name: str | None,
    ) -> str:
        runtime_model_name = (model_config_runtime_name or "").strip()
        if runtime_model_name:
            return runtime_model_name

        env_suffix = self.MODEL_ENV_BY_TASK.get(task)
        if env_suffix is not None:
            env_value = os.getenv(f"{self.model_env_prefix}_{env_suffix}")
            if env_value:
                return env_value

        default_model = self.DEFAULT_MODEL_BY_TASK.get(task)
        if default_model is None:
            raise ProviderConfigurationError(f"No {self.label} model mapping exists for task '{task.value}'.")
        return default_model

    @staticmethod
    def _parse_model_ids(response: httpx.Response) -> tuple[str, ...]:
        try:
            payload = response.json()
        except ValueError:
            return ()
        data = payload.get("data")
        if not isinstance(data, list):
            return ()
        models: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                models.append(model_id.strip())
        return tuple(models)

    @classmethod
    def _extract_error_message(cls, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"{response.request.url.host} returned status {response.status_code}."

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if isinstance(message, str) and message:
                    return message
            message = payload.get("message")
            if isinstance(message, str) and message:
                return message
        return f"{response.request.url.host} returned status {response.status_code}."

    @staticmethod
    def _parse_content_text(payload: dict[str, object]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            fragments: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    fragments.append(item["text"])
            return "".join(fragments).strip()
        return ""

    @staticmethod
    def _extract_finish_reason(payload: dict[str, object]) -> str | None:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        finish_reason = first.get("finish_reason")
        return finish_reason if isinstance(finish_reason, str) else None

    @staticmethod
    def _extract_runtime_model(payload: dict[str, object], fallback: str) -> str:
        model_name = payload.get("model")
        if isinstance(model_name, str) and model_name.strip():
            return model_name
        return fallback

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
