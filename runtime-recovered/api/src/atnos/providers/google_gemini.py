from __future__ import annotations

import base64
import os
from pathlib import Path
from time import perf_counter

import httpx

from atnos.core.config import Settings
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
from atnos.services.llm_usage_tracker_service import LlmUsageTrackerService


class GoogleGeminiProviderAdapter(ProviderDescriptor):
    MODEL_ENV_BY_TASK = {
        ModelTask.GENERAL_CHAT: "GEMINI_CHAT_MODEL",
        ModelTask.ORCHESTRATION: "GEMINI_CHAT_MODEL",
        ModelTask.CODING: "GEMINI_CODE_MODEL",
        ModelTask.SEARCH_RESEARCH: "GEMINI_RESEARCH_MODEL",
        ModelTask.VISION: "GEMINI_VISION_MODEL",
        ModelTask.SYNTHESIS: "GEMINI_SYNTHESIS_MODEL",
    }
    DEFAULT_MODEL_BY_TASK = {
        ModelTask.GENERAL_CHAT: "gemini-3.6-flash",
        ModelTask.ORCHESTRATION: "gemini-3.6-flash",
        ModelTask.CODING: "gemini-3.6-flash",
        ModelTask.SEARCH_RESEARCH: "gemini-3.6-flash",
        ModelTask.VISION: "gemini-3.6-flash",
        ModelTask.SYNTHESIS: "gemini-3.6-flash",
    }
    SUPPORTED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    def __init__(self, *, app_root: Path, settings: Settings | None = None) -> None:
        super().__init__(
            key="gemini-cloud",
            label="Google Gemini",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
        )
        self.app_root = app_root
        self.settings = settings or Settings()
        self.default_base_url = "https://generativelanguage.googleapis.com"
        self.request_timeout_s = float(os.getenv("GEMINI_TIMEOUT_S", "120"))
        self.health_timeout_s = float(os.getenv("GEMINI_HEALTH_TIMEOUT_S", "10"))

    def configuration_status(self, *, provider) -> ProviderReadiness:
        api_key = self._api_key()
        if api_key:
            return ProviderReadiness(ready=True)
        return ProviderReadiness(
            ready=False,
            reason="Brakuje API key dla providera Gemini.",
            failure_kind="provider_configuration",
        )

    def check_health(self, *, provider) -> ProviderHealthCheck:
        readiness = self.configuration_status(provider=provider)
        if not readiness.ready:
            return ProviderHealthCheck(
                status="unknown",
                status_reason=readiness.reason or "Gemini is not configured.",
            )

        started = perf_counter()
        endpoint = f"{(provider.endpoint_url or self.default_base_url).rstrip('/')}/v1beta/models"
        try:
            with httpx.Client(timeout=self.health_timeout_s) as client:
                response = client.get(endpoint, params={"key": self._api_key()})
        except httpx.TimeoutException:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason="Gemini API health check timed out.",
            )
        except httpx.HTTPError as exc:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason=f"Gemini API health check failed: {exc}",
            )

        latency_ms = max(1, int((perf_counter() - started) * 1000))
        if response.status_code >= 400:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason=self._extract_error_message(response),
                latency_ms=latency_ms,
            )

        return ProviderHealthCheck(
            status="healthy",
            status_reason="Gemini responded to /v1beta/models.",
            installed_models=self._parse_model_names(response),
            latency_ms=latency_ms,
        )

    def execute(self, *, task, context, provider, model_config) -> ProviderExecutionResult:
        if not self.supports_task(task):
            raise ProviderConfigurationError(f"Provider '{self.key}' does not support task '{task.value}'.")

        readiness = self.configuration_status(provider=provider)
        if not readiness.ready:
            raise ProviderConfigurationError(
                "Provider Gemini nie jest skonfigurowany. Dodaj GEMINI_API_KEY albo wroc do lokalnego runtime."
            )

        model_name = self._resolve_model_name(task, model_config_runtime_name=model_config.runtime_model_name)
        usage_tracker = LlmUsageTrackerService(self.settings)
        limit_status = usage_tracker.check_gemini_limit()
        if limit_status == "exceeded":
            raise ProviderUnavailableError(
                "Gemini daily request limit is exhausted; use local Ollama fallback."
            )
        endpoint = (
            f"{(provider.endpoint_url or self.default_base_url).rstrip('/')}"
            f"/v1beta/models/{model_name}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": self._build_parts(task=task, context=context),
                }
            ],
            "generationConfig": {
                "maxOutputTokens": model_config.max_output_tokens or 1024,
            },
        }

        try:
            usage_snapshot = usage_tracker.record_request(provider_key=self.key, model_name=model_name)
            with httpx.Client(timeout=self.request_timeout_s) as client:
                response = client.post(
                    endpoint,
                    params={"key": self._api_key()},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Gemini request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Gemini API is unavailable.") from exc

        if response.status_code >= 500:
            raise ProviderUnavailableError("Gemini API returned a server error.")
        if response.status_code >= 400:
            raise ProviderResponseError(self._extract_error_message(response))

        data = response.json()
        content = self._parse_content_text(data)
        if not content:
            raise ProviderResponseError("Gemini returned an empty response.")

        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount") if isinstance(usage, dict) else None
        output_tokens = usage.get("candidatesTokenCount") if isinstance(usage, dict) else None
        total_tokens = usage.get("totalTokenCount") if isinstance(usage, dict) else None

        return ProviderExecutionResult(
            content=content,
            usage=ProviderUsage(
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                total_tokens=total_tokens if isinstance(total_tokens, int) else None,
            ),
            finish_reason=self._extract_finish_reason(data),
            raw_metadata={
                "provider_key": self.key,
                "runtime_model": model_name,
                "usage_limit_status": limit_status,
                "gemini_requests_today": usage_snapshot.gemini_requests_today,
            },
        )

    def _build_parts(
        self,
        *,
        task: ModelTask,
        context: ProviderExecutionContext,
    ) -> list[dict[str, object]]:
        parts: list[dict[str, object]] = [{"text": context.provider_prompt}]
        if task != ModelTask.VISION:
            return parts

        if not context.allow_external_attachment_content:
            raise ProviderConfigurationError(
                "External vision execution is blocked by policy: attachment content egress is disabled."
            )

        image_parts = self._build_image_parts(context.local_attachments)
        if not image_parts:
            raise ProviderConfigurationError(
                "Gemini vision execution requires at least one readable local image attachment."
            )
        return [*image_parts, *parts]

    def _build_image_parts(
        self,
        local_attachments: tuple[LocalAttachmentMetadata, ...],
    ) -> list[dict[str, object]]:
        parts: list[dict[str, object]] = []
        for attachment in local_attachments:
            attachment_path = self._resolve_attachment_path(attachment.storage_path)
            if not attachment_path.exists():
                continue
            media_type = self._media_type_for_path(attachment_path)
            if media_type is None:
                continue
            parts.append(
                {
                    "inline_data": {
                        "mime_type": media_type,
                        "data": base64.b64encode(attachment_path.read_bytes()).decode("ascii"),
                    }
                }
            )
        return parts

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

        env_key = self.MODEL_ENV_BY_TASK.get(task)
        if env_key is not None:
            env_value = os.getenv(env_key)
            if env_value:
                return env_value

        shared_model = os.getenv("GEMINI_MODEL")
        if shared_model:
            return shared_model

        default_model = self.DEFAULT_MODEL_BY_TASK.get(task)
        if default_model is None:
            raise ProviderConfigurationError(f"No Gemini model mapping exists for task '{task.value}'.")
        return default_model

    def _api_key(self) -> str:
        return (self.settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()

    @classmethod
    def _extract_error_message(cls, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Gemini returned status {response.status_code}."

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if isinstance(message, str) and message:
                    return message
        return f"Gemini returned status {response.status_code}."

    @staticmethod
    def _parse_model_names(response: httpx.Response) -> tuple[str, ...]:
        try:
            payload = response.json()
        except ValueError:
            return ()
        models = payload.get("models")
        if not isinstance(models, list):
            return ()
        names: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.removeprefix("models/").strip())
        return tuple(names)

    @staticmethod
    def _parse_content_text(payload: dict[str, object]) -> str:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return ""
        fragments: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    fragments.append(part["text"])
        return "".join(fragments).strip()

    @staticmethod
    def _extract_finish_reason(payload: dict[str, object]) -> str | None:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None
        first = candidates[0]
        if not isinstance(first, dict):
            return None
        finish_reason = first.get("finishReason")
        return finish_reason if isinstance(finish_reason, str) else None

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
