from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path
from time import monotonic

import httpx

from atnos.core.config import Settings
from atnos.db.models.enums import ModelTask
from atnos.providers.base import (
    LocalFailureType,
    LocalProviderRuntimeError,
    ProviderCapability,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderExecutionResult,
    ProviderHealthCheck,
    ProviderUsage,
    build_local_failure_detail,
)
from atnos.services.ollama_model_roles_service import OllamaModelRolesService
from atnos.services.context_builder_service import LocalAttachmentMetadata
from atnos.services.llm_usage_tracker_service import LlmUsageTrackerService


class OllamaLocalProviderAdapter(ProviderDescriptor):
    SYNTHESIS_MIN_OUTPUT_TOKENS = 1536
    SUPPORTED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}
    # Cold VRAM load on RX 7600 XT routinely takes 60–120s under MAX_LOADED_MODELS=1.
    TASK_TIMEOUTS_S = {
        ModelTask.GENERAL_CHAT: 180,
        ModelTask.ORCHESTRATION: 180,
        ModelTask.CODING: 180,
        ModelTask.SEARCH_RESEARCH: 150,
        ModelTask.SYNTHESIS: 240,
        ModelTask.VISION: 180,
    }
    CHAT_NUM_CTX = 4096
    HEAVY_NUM_CTX = 8192
    KEEP_ALIVE = "30m"

    def __init__(self, *, app_root: Path, settings: Settings) -> None:
        super().__init__(
            key="ollama-local",
            label="Ollama Local",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=True,
        )
        self.app_root = app_root
        self.settings = settings
        self.default_base_url = settings.ollama_base_url
        self.request_timeout_s = settings.ollama_timeout_s
        self.health_timeout_s = settings.ollama_health_timeout_s

    def check_health(self, *, provider) -> ProviderHealthCheck:
        base_url = (provider.endpoint_url or self.default_base_url).rstrip("/")
        started_at = monotonic()

        try:
            with httpx.Client(timeout=self.health_timeout_s) as client:
                response = client.get(f"{base_url}/api/tags")
        except httpx.TimeoutException:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason="Ollama health check timed out.",
            )
        except httpx.HTTPError:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason="Ollama local runtime is unavailable.",
            )

        latency_ms = max(1, int((monotonic() - started_at) * 1000))

        if response.status_code >= 500:
            return ProviderHealthCheck(
                status="unreachable",
                status_reason="Ollama local runtime returned a server error.",
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
                status_reason="Ollama health check returned invalid JSON.",
                latency_ms=latency_ms,
            )

        installed_models = self._extract_installed_models(payload)
        return ProviderHealthCheck(
            status="healthy",
            status_reason="Ollama local runtime responded to /api/tags.",
            installed_models=installed_models,
            latency_ms=latency_ms,
        )

    def resolve_request_timeout_s(self, *, task: ModelTask, prompt_chars: int) -> float:
        timeout_s = float(self.TASK_TIMEOUTS_S.get(task, self.request_timeout_s))
        if prompt_chars > 9000:
            timeout_s += 90.0
        if prompt_chars > 20000:
            timeout_s += 60.0
        # Never exceed the configured Ollama ceiling, but also never undercut task budget.
        return min(max(timeout_s, float(self.TASK_TIMEOUTS_S.get(task, 120))), float(self.request_timeout_s))

    def execute(self, *, task, context, provider, model_config) -> ProviderExecutionResult:
        if task not in {
            ModelTask.GENERAL_CHAT,
            ModelTask.ORCHESTRATION,
            ModelTask.CODING,
            ModelTask.SEARCH_RESEARCH,
            ModelTask.SYNTHESIS,
            ModelTask.VISION,
        }:
            raise self._local_failure(
                failure_type=LocalFailureType.CAPABILITY_MISMATCH,
                model_key=model_config.key,
                runtime_model_name=getattr(model_config, "runtime_model_name", None),
            )
        if not self.supports_task(task):
            raise self._local_failure(
                failure_type=LocalFailureType.CAPABILITY_MISMATCH,
                model_key=model_config.key,
                runtime_model_name=getattr(model_config, "runtime_model_name", None),
            )

        base_url = (provider.endpoint_url or self.default_base_url).rstrip("/")
        model_name = self._resolve_model_name(model_config, task=task)
        model_name = self._prefer_resident_model(
            configured_model=model_name,
            task=task,
            base_url=base_url,
        )
        user_message: dict[str, object] = {
            "role": "user",
            "content": context.provider_prompt,
        }
        messages: list[dict[str, object]] = []
        if task == ModelTask.VISION:
            grounding = self._load_vision_grounding_prompt()
            if grounding:
                messages.append({"role": "system", "content": grounding})
            user_message["images"] = self._build_image_payload(context.local_attachments)
        messages.append(user_message)
        payload: dict[str, object] = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "keep_alive": self.KEEP_ALIVE,
        }
        generation_max_tokens = self._resolve_generation_budget(model_config, task=task)
        options: dict[str, object] = {}
        if generation_max_tokens is not None:
            options["num_predict"] = generation_max_tokens
        if task == ModelTask.GENERAL_CHAT:
            options.setdefault("num_ctx", self.CHAT_NUM_CTX)
            options.setdefault("num_predict", generation_max_tokens or 1024)
        elif task in {
            ModelTask.ORCHESTRATION,
            ModelTask.CODING,
            ModelTask.SEARCH_RESEARCH,
            ModelTask.SYNTHESIS,
        }:
            options.setdefault("num_ctx", self.HEAVY_NUM_CTX)
        if task == ModelTask.VISION:
            options["num_ctx"] = self.HEAVY_NUM_CTX
            # Prefer grounded description over creative guessing.
            options.setdefault("temperature", 0.1)
            options.setdefault("top_p", 0.8)
        if options:
            payload["options"] = options
        thinking_mode = self._resolve_thinking_mode(model_name=model_name, task=task)
        if thinking_mode is not None:
            payload["think"] = thinking_mode

        timeout_s = self.resolve_request_timeout_s(
            task=task,
            prompt_chars=context.provider_prompt_chars,
        )
        retry_attempted = False

        for attempt in range(2):
            attempt_timeout_s = timeout_s
            if retry_attempted:
                attempt_timeout_s = min(timeout_s + 120.0, float(self.request_timeout_s))
            try:
                data = self._request_generate(
                    base_url=base_url,
                    payload=payload,
                    timeout_s=attempt_timeout_s,
                    model_key=model_config.key,
                    runtime_model_name=model_name,
                )
            except LocalProviderRuntimeError as exc:
                failure_detail = self._with_retry_diagnostics(
                    exc.failure_detail,
                    timeout_s=attempt_timeout_s,
                    retry_attempted=retry_attempted,
                )
                if (
                    failure_detail.failure_type == LocalFailureType.REQUEST_TIMEOUT.value
                    and attempt == 0
                ):
                    retry_attempted = True
                    # Prefer whatever is already resident after a slow cold load.
                    model_name = self._prefer_resident_model(
                        configured_model=model_name,
                        task=task,
                        base_url=base_url,
                    )
                    payload["model"] = model_name
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
                        timeout_s=attempt_timeout_s,
                        retry_attempted=retry_attempted,
                    )
                ) from exc

            content = self._extract_chat_content(data)
            if not content:
                raise self._local_failure(
                    failure_type=LocalFailureType.GENERATION_ERROR,
                    model_key=model_config.key,
                    runtime_model_name=model_name,
                    diagnostics={"runtime_message": "Ollama returned an empty response."},
                )

            input_tokens = self._coerce_int(data.get("prompt_eval_count"))
            output_tokens = self._coerce_int(data.get("eval_count"))
            total_tokens = None
            if input_tokens is not None and output_tokens is not None:
                total_tokens = input_tokens + output_tokens
            usage_snapshot = LlmUsageTrackerService(self.settings).record_request(
                provider_key=self.key,
                model_name=model_name,
            )

            return ProviderExecutionResult(
                content=content,
                provider_message_id=data.get("created_at"),
                usage=ProviderUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                ),
                finish_reason=data.get("done_reason"),
                raw_metadata={
                    "provider_key": self.key,
                    "runtime_model": data.get("model", model_name),
                    "generation_max_tokens": generation_max_tokens,
                    "transport": "ollama_api_chat",
                    "thinking_mode": thinking_mode,
                    "prompt_eval_count": input_tokens,
                    "eval_count": output_tokens,
                    "total_duration": data.get("total_duration"),
                    "load_duration": data.get("load_duration"),
                    "prompt_eval_duration": data.get("prompt_eval_duration"),
                    "eval_duration": data.get("eval_duration"),
                    "ollama_main_requests_today": usage_snapshot.ollama_main_requests_today,
                    "ollama_fast_requests_today": usage_snapshot.ollama_fast_requests_today,
                    "ollama_vision_requests_today": usage_snapshot.ollama_vision_requests_today,
                },
            )

        raise self._local_failure(
            failure_type=LocalFailureType.UNKNOWN,
            model_key=model_config.key,
            runtime_model_name=model_name,
        )

    def _request_generate(
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
                response = client.post(f"{base_url}/api/chat", json=payload)
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
                    "runtime_message": "Ollama returned invalid JSON.",
                },
            ) from exc
        if not isinstance(data, dict):
            raise self._local_failure(
                failure_type=LocalFailureType.GENERATION_ERROR,
                model_key=model_key,
                runtime_model_name=runtime_model_name,
                diagnostics={
                    "timeout_ms": int(timeout_s * 1000),
                    "runtime_message": "Ollama returned a non-dict payload.",
                },
            )
        return data

    @staticmethod
    def _extract_chat_content(data: dict[str, object]) -> str:
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()

        # Compatibility with older unit-test payloads and legacy Ollama generate responses.
        response = data.get("response")
        if isinstance(response, str):
            return response.strip()

        return ""

    @staticmethod
    def _resolve_thinking_mode(*, model_name: str, task: ModelTask) -> bool | None:
        normalized_model = model_name.strip().lower()
        if "qwen3" not in normalized_model and "deepseek-r1" not in normalized_model:
            return None
        # Chat must stay non-thinking — thinking burns the 45–180s budget before tokens appear.
        return task == ModelTask.ORCHESTRATION

    def _list_loaded_models(self, base_url: str) -> list[str]:
        try:
            with httpx.Client(timeout=self.health_timeout_s) as client:
                response = client.get(f"{base_url.rstrip('/')}/api/ps")
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return []
        loaded: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name.strip():
                loaded.append(name.strip())
        return loaded

    def _prefer_resident_model(
        self,
        *,
        configured_model: str,
        task: ModelTask,
        base_url: str,
    ) -> str:
        """Avoid VRAM swap when another chat-capable model is already resident."""
        if task == ModelTask.VISION:
            return configured_model

        loaded = self._list_loaded_models(base_url)
        if not loaded:
            return configured_model
        if configured_model in loaded:
            return configured_model

        role_models = [
            (self.settings.ollama_fast_model or "").strip(),
            (self.settings.ollama_chat_model or "").strip(),
            (self.settings.ollama_main_model or "").strip(),
            (self.settings.ollama_orchestration_model or "").strip(),
        ]
        for candidate in role_models:
            if candidate and candidate in loaded:
                return candidate

        for resident in loaded:
            lowered = resident.lower()
            if any(token in lowered for token in ("-vl", "vision", "bge-", "embed")):
                continue
            return resident
        return configured_model

    def _resolve_model_name(self, model_config, *, task: ModelTask) -> str:
        explicit_runtime_model = getattr(model_config, "runtime_model_name", None)
        if isinstance(explicit_runtime_model, str) and explicit_runtime_model.strip():
            return self._resolve_configured_or_fallback_model(
                explicit_runtime_model.strip(),
                task=task,
                model_key=model_config.key,
            )

        configured_model = self._resolve_env_model_name(task=task)
        if configured_model:
            return self._resolve_configured_or_fallback_model(
                configured_model,
                task=task,
                model_key=model_config.key,
            )

        discovered_via_http = self._discover_models_via_http()
        preferred_http_model = self._pick_discovered_model_for_task(
            discovered_via_http,
            task=task,
        )
        if preferred_http_model:
            return preferred_http_model

        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise self._local_failure(
                failure_type=LocalFailureType.MODEL_MISSING,
                model_key=model_config.key,
                runtime_model_name=getattr(model_config, "key", None),
                diagnostics={"runtime_message": "Installed Ollama models could not be discovered."},
            ) from exc

        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        discovered_models = [line.split()[0] for line in lines[1:] if line.split()]

        preferred_model = self._pick_discovered_model_for_task(discovered_models, task=task)
        if preferred_model:
            return preferred_model

        raise self._local_failure(
            failure_type=LocalFailureType.MODEL_MISSING,
            model_key=model_config.key,
            runtime_model_name=getattr(model_config, "key", None),
        )

    def _resolve_configured_or_fallback_model(
        self,
        configured_model: str,
        *,
        task: ModelTask,
        model_key: str,
    ) -> str:
        installed_models = self._discover_models_via_http()
        if not installed_models:
            return configured_model

        if configured_model in installed_models:
            return configured_model

        fallback_model = self._configured_missing_fallback_model(
            task=task,
            installed_models=installed_models,
        )
        if fallback_model:
            return fallback_model

        raise self._local_failure(
            failure_type=LocalFailureType.MODEL_MISSING,
            model_key=model_key,
            runtime_model_name=configured_model,
            diagnostics={
                "available_models": installed_models,
                "configured_model": configured_model,
            },
        )

    def _configured_missing_fallback_model(
        self,
        *,
        task: ModelTask,
        installed_models: list[str],
    ) -> str | None:
        if task == ModelTask.VISION:
            return None

        preferred = (
            (self.settings.ollama_fast_model or "").strip()
            or OllamaModelRolesService.DEFAULT_FAST_TEXT_MODEL
        )
        if preferred in installed_models:
            return preferred

        return None

    def _resolve_env_model_name(self, *, task: ModelTask) -> str | None:
        if task == ModelTask.VISION:
            configured_model = self.settings.ollama_vision_model
            if configured_model and configured_model.strip():
                return configured_model.strip()
            return None

        if task == ModelTask.SYNTHESIS:
            for configured_model in (
                self.settings.ollama_document_model,
                self.settings.ollama_synthesis_model,
                self.settings.ollama_main_model,
                self.settings.ollama_chat_model,
            ):
                if configured_model and configured_model.strip():
                    return configured_model.strip()
            return None

        if task == ModelTask.ORCHESTRATION:
            for configured_model in (
                self.settings.ollama_orchestration_model,
                self.settings.ollama_main_model,
                self.settings.ollama_chat_model,
            ):
                if configured_model and configured_model.strip():
                    return configured_model.strip()
            return None

        if task in {ModelTask.CODING, ModelTask.SEARCH_RESEARCH}:
            for configured_model in (
                self.settings.ollama_orchestration_model,
                self.settings.ollama_main_model,
                self.settings.ollama_chat_model,
            ):
                if configured_model and configured_model.strip():
                    return configured_model.strip()
            return None

        if task == ModelTask.GENERAL_CHAT:
            configured_model = (
                self.settings.ollama_fast_model
                or self.settings.ollama_chat_model
                or self.settings.ollama_main_model
            )
        else:
            configured_model = self.settings.ollama_chat_model or self.settings.ollama_main_model
        if configured_model and configured_model.strip():
            return configured_model.strip()
        return None

    def _resolve_generation_budget(self, model_config, *, task: ModelTask) -> int | None:
        configured_budget = getattr(model_config, "max_output_tokens", None)
        if not isinstance(configured_budget, int) or configured_budget <= 0:
            configured_budget = None

        if task == ModelTask.SYNTHESIS:
            if configured_budget is None:
                return self.SYNTHESIS_MIN_OUTPUT_TOKENS
            return max(configured_budget, self.SYNTHESIS_MIN_OUTPUT_TOKENS)

        return configured_budget

    def _load_vision_grounding_prompt(self) -> str | None:
        prompt_path = (
            Path(__file__).resolve().parents[1] / "prompts" / "vision_grounding.txt"
        )
        try:
            content = prompt_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return content or None

    def _build_image_payload(
        self,
        local_attachments: tuple[LocalAttachmentMetadata, ...],
    ) -> list[str]:
        images: list[str] = []
        for attachment in local_attachments:
            attachment_path = self._resolve_attachment_path(attachment.storage_path)
            if not attachment_path.exists():
                continue
            if self._media_type_for_path(attachment_path) is None:
                continue
            images.append(base64.b64encode(attachment_path.read_bytes()).decode("ascii"))
        if not images:
            raise ProviderConfigurationError(
                "Local vision execution requires at least one readable image attachment."
            )
        return images

    def _resolve_attachment_path(self, storage_path: str) -> Path:
        candidate = Path(storage_path)
        if candidate.is_absolute():
            return candidate
        return (self.app_root / candidate).resolve()

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"Ollama returned status {response.status_code}."

        if isinstance(payload, dict):
            error_message = payload.get("error")
            if isinstance(error_message, str) and error_message:
                return error_message

        return f"Ollama returned status {response.status_code}."

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        return value if isinstance(value, int) else None

    @staticmethod
    def _extract_installed_models(payload: object) -> tuple[str, ...]:
        if not isinstance(payload, dict):
            return ()

        models_payload = payload.get("models")
        if not isinstance(models_payload, list):
            return ()

        discovered: list[str] = []
        for item in models_payload:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                discovered.append(name.strip())

        return tuple(sorted(set(discovered)))

    @classmethod
    def _media_type_for_path(cls, path: Path) -> str | None:
        suffix = path.suffix.lower()
        mapping = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        media_type = mapping.get(suffix)
        if media_type in cls.SUPPORTED_IMAGE_MEDIA_TYPES:
            return media_type
        return None

    def _discover_models_via_http(self) -> list[str]:
        base_url = self.default_base_url.rstrip("/")
        try:
            with httpx.Client(timeout=self.health_timeout_s) as client:
                response = client.get(f"{base_url}/api/tags")
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        return list(self._extract_installed_models(payload))

    @staticmethod
    def _pick_discovered_model_for_task(
        discovered_models: list[str] | tuple[str, ...],
        *,
        task: ModelTask,
    ) -> str | None:
        normalized = [
            model_name.strip()
            for model_name in discovered_models
            if isinstance(model_name, str) and model_name.strip()
        ]
        if not normalized:
            return None
        if task == ModelTask.VISION:
            for token in (
                "qwen3-vl",
                "qwen2.5-vl",
                "llava",
                "bakllava",
                "moondream",
                "vision",
                "-vl",
            ):
                preferred = next(
                    (model_name for model_name in normalized if token in model_name.lower()),
                    None,
                )
                if preferred:
                    return preferred
            return normalized[0]

        preferred_model = next(
            (
                model_name
                for model_name in normalized
                if model_name == "atnos-core" or model_name.startswith("atnos-core:")
            ),
            None,
        )
        if preferred_model:
            return preferred_model

        non_vision_model = next(
            (
                model_name
                for model_name in normalized
                if all(
                    token not in model_name.lower()
                    for token in ("llava", "bakllava", "moondream", "vision", "-vl")
                )
            ),
            None,
        )
        if non_vision_model:
            return non_vision_model
        return normalized[0]

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
                "context size",
                "exceed_context_size",
                "exceeds the available context",
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
