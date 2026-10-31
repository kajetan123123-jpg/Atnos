from __future__ import annotations

import os
import time
from pathlib import Path
from time import monotonic

import httpx

from atnos.db.models.enums import ModelTask
from atnos.providers.base import (
    LocalFailureType,
    LocalProviderRuntimeError,
    ProviderCapability,
    ProviderDescriptor,
    ProviderExecutionResult,
    ProviderHealthCheck,
    ProviderUsage,
    build_local_failure_detail,
)


class LlamaCppLocalProviderAdapter(ProviderDescriptor):
    TASK_TIMEOUTS_S = {
        ModelTask.GENERAL_CHAT: 45,
        ModelTask.ORCHESTRATION: 90,
        ModelTask.SYNTHESIS: 120,
        ModelTask.VISION: 120,
    }

    def __init__(self, *, app_root: Path) -> None:
        super().__init__(
            key="llamacpp-local",
            label="llama.cpp Local",
            capabilities=(ProviderCapability.CHAT,),
            local_first=True,
        )
        self.app_root = app_root
        self.request_timeout_s = float(os.getenv("LLAMACPP_TIMEOUT_S", "120"))
        self.health_timeout_s = float(os.getenv("LLAMACPP_HEALTH_TIMEOUT_S", "2"))

    def check_health(self, *, provider) -> ProviderHealthCheck:
        base_url = (provider.endpoint_url or "").strip().rstrip("/")
        if not base_url:
            return ProviderHealthCheck(
                status="unknown",
                status_reason="No endpoint URL configured for llama.cpp runtime.",
            )

        started_at = monotonic()
        try:
            with httpx.Client(timeout=self.health_timeout_s) as client:
                response = client.get(f"{base_url}/v1/models")
        except httpx.TimeoutException:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason="llama.cpp health check timed out.",
            )
        except httpx.HTTPError:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason="llama.cpp local runtime is unavailable.",
            )

        latency_ms = max(1, int((monotonic() - started_at) * 1000))

        if response.status_code >= 500:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason="llama.cpp local runtime returned a server error.",
                latency_ms=latency_ms,
            )

        if response.status_code >= 400:
            return ProviderHealthCheck(
                status="unknown",
                status_reason=self._extract_error_message(response),
                latency_ms=latency_ms,
            )

        try:
            payload = response.json()
        except ValueError:
            return ProviderHealthCheck(
                status="unknown",
                status_reason="llama.cpp health check returned invalid JSON.",
                latency_ms=latency_ms,
            )

        return ProviderHealthCheck(
            status="healthy",
            status_reason="llama.cpp local runtime responded to /v1/models.",
            installed_models=self._extract_installed_models(payload),
            latency_ms=latency_ms,
        )

    def resolve_request_timeout_s(self, *, task: ModelTask, prompt_chars: int) -> float:
        timeout_s = float(self.TASK_TIMEOUTS_S.get(task, self.request_timeout_s))
        if prompt_chars > 9000:
            timeout_s += 60.0
        return timeout_s

    def execute(self, *, task, context, provider, model_config) -> ProviderExecutionResult:
        if task != ModelTask.GENERAL_CHAT or not self.supports_task(task):
            raise self._local_failure(
                failure_type=LocalFailureType.CAPABILITY_MISMATCH,
                model_key=model_config.key,
                runtime_model_name=getattr(model_config, "runtime_model_name", None),
            )

        base_url = (provider.endpoint_url or "").strip().rstrip("/")
        if not base_url:
            raise self._local_failure(
                failure_type=LocalFailureType.INVALID_ENDPOINT,
                model_key=model_config.key,
                runtime_model_name=getattr(model_config, "runtime_model_name", None),
                diagnostics={"runtime_message": "llama.cpp endpoint URL is not configured."},
            )

        model_name = self._resolve_model_name(model_config)
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": context.provider_prompt,
                }
            ],
            "stream": False,
        }
        if model_config.max_output_tokens is not None:
            payload["max_tokens"] = model_config.max_output_tokens

        timeout_s = self.resolve_request_timeout_s(
            task=task,
            prompt_chars=context.provider_prompt_chars,
        )
        retry_attempted = False

        for attempt in range(2):
            try:
                data = self._request_chat_completion(
                    base_url=base_url,
                    payload=payload,
                    timeout_s=timeout_s,
                    model_key=model_config.key,
                    runtime_model_name=model_name,
                )
            except LocalProviderRuntimeError as exc:
                failure_detail = self._with_retry_diagnostics(
                    exc.failure_detail,
                    timeout_s=timeout_s,
                    retry_attempted=retry_attempted,
                )
                if (
                    failure_detail.failure_type == LocalFailureType.REQUEST_TIMEOUT.value
                    and attempt == 0
                ):
                    retry_attempted = True
                    self.check_health(provider=provider)
                    continue
                if (
                    failure_detail.failure_type == LocalFailureType.RUNTIME_BUSY.value
                    and attempt == 0
                ):
                    retry_attempted = True
                    time.sleep(2.0)
                    continue
                if failure_detail.failure_type == LocalFailureType.RUNTIME_UNREACHABLE.value:
                    self.check_health(provider=provider)
                raise LocalProviderRuntimeError(
                    self._with_retry_diagnostics(
                        failure_detail,
                        timeout_s=timeout_s,
                        retry_attempted=retry_attempted,
                    )
                ) from exc

            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise self._local_failure(
                    failure_type=LocalFailureType.GENERATION_ERROR,
                    model_key=model_config.key,
                    runtime_model_name=model_name,
                    diagnostics={"runtime_message": "llama.cpp returned no choices."},
                )

            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                raise self._local_failure(
                    failure_type=LocalFailureType.GENERATION_ERROR,
                    model_key=model_config.key,
                    runtime_model_name=model_name,
                    diagnostics={"runtime_message": "llama.cpp returned an invalid choice payload."},
                )

            message = first_choice.get("message")
            if not isinstance(message, dict):
                raise self._local_failure(
                    failure_type=LocalFailureType.GENERATION_ERROR,
                    model_key=model_config.key,
                    runtime_model_name=model_name,
                    diagnostics={"runtime_message": "llama.cpp returned no assistant message."},
                )

            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise self._local_failure(
                    failure_type=LocalFailureType.GENERATION_ERROR,
                    model_key=model_config.key,
                    runtime_model_name=model_name,
                    diagnostics={"runtime_message": "llama.cpp returned an empty response."},
                )

            usage_payload = data.get("usage", {})
            input_tokens = (
                usage_payload.get("prompt_tokens") if isinstance(usage_payload, dict) else None
            )
            output_tokens = (
                usage_payload.get("completion_tokens")
                if isinstance(usage_payload, dict)
                else None
            )
            total_tokens = (
                usage_payload.get("total_tokens") if isinstance(usage_payload, dict) else None
            )

            return ProviderExecutionResult(
                content=content.strip(),
                provider_message_id=data.get("id"),
                usage=ProviderUsage(
                    input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                    output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                    total_tokens=total_tokens if isinstance(total_tokens, int) else None,
                ),
                finish_reason=(
                    first_choice.get("finish_reason")
                    if isinstance(first_choice.get("finish_reason"), str)
                    else None
                ),
                raw_metadata={
                    "provider_key": self.key,
                    "runtime_model": data.get("model", model_name),
                    "object": data.get("object"),
                },
            )

        raise self._local_failure(
            failure_type=LocalFailureType.UNKNOWN,
            model_key=model_config.key,
            runtime_model_name=model_name,
        )

    def _request_chat_completion(
        self,
        *,
        base_url: str,
        payload: dict[str, object],
        timeout_s: float,
        model_key: str,
        runtime_model_name: str,
    ) -> dict[str, object]:
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(f"{base_url}/v1/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise self._local_failure(
                failure_type=LocalFailureType.REQUEST_TIMEOUT,
                model_key=model_key,
                runtime_model_name=runtime_model_name,
                diagnostics={"timeout_ms": int(timeout_s * 1000)},
            ) from exc
        except httpx.HTTPError as exc:
            raise self._local_failure(
                failure_type=LocalFailureType.RUNTIME_UNREACHABLE,
                model_key=model_key,
                runtime_model_name=runtime_model_name,
                diagnostics={
                    "timeout_ms": int(timeout_s * 1000),
                    "http_error": exc.__class__.__name__,
                },
            ) from exc

        if response.status_code >= 400:
            error_message = self._extract_error_message(response)
            raise self._local_failure(
                failure_type=self._classify_response_failure(
                    status_code=response.status_code,
                    error_message=error_message,
                ),
                model_key=model_key,
                runtime_model_name=runtime_model_name,
                diagnostics={
                    "timeout_ms": int(timeout_s * 1000),
                    "http_status": response.status_code,
                    "runtime_message": error_message,
                },
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise self._local_failure(
                failure_type=LocalFailureType.GENERATION_ERROR,
                model_key=model_key,
                runtime_model_name=runtime_model_name,
                diagnostics={
                    "timeout_ms": int(timeout_s * 1000),
                    "runtime_message": "llama.cpp returned invalid JSON.",
                },
            ) from exc
        if not isinstance(data, dict):
            raise self._local_failure(
                failure_type=LocalFailureType.GENERATION_ERROR,
                model_key=model_key,
                runtime_model_name=runtime_model_name,
                diagnostics={
                    "timeout_ms": int(timeout_s * 1000),
                    "runtime_message": "llama.cpp returned a non-dict payload.",
                },
            )
        return data

    @staticmethod
    def _resolve_model_name(model_config) -> str:
        explicit_runtime_model = getattr(model_config, "runtime_model_name", None)
        if isinstance(explicit_runtime_model, str) and explicit_runtime_model.strip():
            return explicit_runtime_model.strip()

        configured_model = os.getenv("LLAMACPP_CHAT_MODEL")
        if configured_model:
            return configured_model
        return model_config.key.split("/")[-1]

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"llama.cpp returned status {response.status_code}."

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if isinstance(message, str) and message:
                    return message
            if isinstance(error_payload, str) and error_payload:
                return error_payload

        return f"llama.cpp returned status {response.status_code}."

    @staticmethod
    def _extract_installed_models(payload: object) -> tuple[str, ...]:
        if not isinstance(payload, dict):
            return ()

        models_payload = payload.get("data")
        if not isinstance(models_payload, list):
            return ()

        discovered: list[str] = []
        for item in models_payload:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                discovered.append(model_id.strip())

        return tuple(sorted(set(discovered)))

    def _classify_response_failure(
        self,
        *,
        status_code: int,
        error_message: str,
    ) -> LocalFailureType:
        normalized = error_message.lower()
        if status_code == 404:
            if "model" in normalized and "not found" in normalized:
                return LocalFailureType.MODEL_MISSING
            return LocalFailureType.INVALID_ENDPOINT
        if status_code in {409, 423, 425, 429, 503}:
            return LocalFailureType.RUNTIME_BUSY
        if "model" in normalized and ("not found" in normalized or "missing" in normalized):
            return LocalFailureType.MODEL_MISSING
        if any(
            token in normalized
            for token in (
                "context length",
                "context window",
                "prompt too long",
                "too many tokens",
                "input is too long",
            )
        ):
            return LocalFailureType.CONTEXT_TOO_LARGE
        if any(
            token in normalized
            for token in (
                "does not support",
                "unsupported",
                "vision model required",
                "images are not supported",
            )
        ):
            return LocalFailureType.CAPABILITY_MISMATCH
        if any(token in normalized for token in ("busy", "overloaded", "try again later")):
            return LocalFailureType.RUNTIME_BUSY
        if status_code >= 500:
            return LocalFailureType.GENERATION_ERROR
        return LocalFailureType.UNKNOWN

    def _with_retry_diagnostics(
        self,
        failure_detail,
        *,
        timeout_s: float,
        retry_attempted: bool,
    ):
        diagnostics = dict(failure_detail.diagnostics or {})
        diagnostics["timeout_ms"] = int(timeout_s * 1000)
        diagnostics["retry_attempted"] = retry_attempted
        return build_local_failure_detail(
            failure_type=LocalFailureType(failure_detail.failure_type),
            provider_key=failure_detail.provider_key,
            provider_label=self.label,
            model_key=failure_detail.model_key,
            runtime_model_name=failure_detail.runtime_model_name,
            diagnostics=diagnostics,
        )

    def _local_failure(
        self,
        *,
        failure_type: LocalFailureType,
        model_key: str,
        runtime_model_name: str | None,
        diagnostics: dict[str, object] | None = None,
    ) -> LocalProviderRuntimeError:
        return LocalProviderRuntimeError(
            build_local_failure_detail(
                failure_type=failure_type,
                provider_key=self.key,
                provider_label=self.label,
                model_key=model_key,
                runtime_model_name=runtime_model_name,
                diagnostics=diagnostics,
            )
        )
