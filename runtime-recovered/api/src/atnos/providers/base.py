from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Mapping

if TYPE_CHECKING:
    from atnos.db.models.enums import ModelTask
    from atnos.db.models.model_config import ModelConfig
    from atnos.db.models.provider import Provider
    from atnos.services.context_builder_service import ProviderExecutionContext


class ProviderCapability(str, Enum):
    CHAT = "chat"
    SYNTHESIS = "synthesis"
    VISION = "vision"
    AUDIO_IN = "audio_in"
    AUDIO_OUT = "audio_out"
    IMAGE_GENERATION = "image_generation"


class LocalFailureType(str, Enum):
    RUNTIME_UNREACHABLE = "runtime_unreachable"
    MODEL_MISSING = "model_missing"
    REQUEST_TIMEOUT = "request_timeout"
    RUNTIME_BUSY = "runtime_busy"
    INVALID_ENDPOINT = "invalid_endpoint"
    CAPABILITY_MISMATCH = "capability_mismatch"
    CONTEXT_TOO_LARGE = "context_too_large"
    GENERATION_ERROR = "generation_error"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)


class ProviderExecutionError(RuntimeError):
    pass


class ProviderConfigurationError(ProviderExecutionError):
    pass


class ProviderUnavailableError(ProviderExecutionError):
    pass


class ProviderTimeoutError(ProviderExecutionError):
    pass


class ProviderResponseError(ProviderExecutionError):
    pass


@dataclass(slots=True, frozen=True)
class LocalModelFailureDetail:
    failure_type: str
    provider_key: str
    model_key: str
    runtime_model_name: str | None
    user_title: str
    user_detail: str
    retryable: bool
    can_fallback: bool
    suggested_actions: tuple[str, ...]
    diagnostics: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "failure_type": self.failure_type,
            "provider_key": self.provider_key,
            "model_key": self.model_key,
            "runtime_model_name": self.runtime_model_name,
            "user_title": self.user_title,
            "user_detail": self.user_detail,
            "retryable": self.retryable,
            "can_fallback": self.can_fallback,
            "suggested_actions": list(self.suggested_actions),
            "diagnostics": dict(self.diagnostics) if self.diagnostics is not None else None,
        }


class LocalProviderRuntimeError(ProviderExecutionError):
    def __init__(self, failure_detail: LocalModelFailureDetail) -> None:
        super().__init__(failure_detail.user_detail)
        self.failure_detail = failure_detail


@dataclass(slots=True, frozen=True)
class ProviderExecutionResult:
    content: str
    provider_message_id: str | None = None
    usage: ProviderUsage | None = None
    finish_reason: str | None = None
    raw_metadata: dict | None = None


@dataclass(slots=True, frozen=True)
class ProviderHealthCheck:
    status: Literal["healthy", "unreachable", "not_supported", "unknown"]
    status_reason: str
    installed_models: tuple[str, ...] = ()
    latency_ms: int | None = None


@dataclass(slots=True, frozen=True)
class ProviderReadiness:
    ready: bool
    reason: str | None = None
    failure_kind: Literal["provider_configuration", "provider_unavailable"] | None = None


class ProviderDescriptor:
    def __init__(
        self,
        *,
        key: str,
        label: str,
        capabilities: tuple[ProviderCapability, ...],
        local_first: bool,
        response_prefix: str | None = None,
    ) -> None:
        self.key = key
        self.label = label
        self.capabilities = capabilities
        self.local_first = local_first
        self.response_prefix = response_prefix

    def supports_task(self, task: "ModelTask") -> bool:
        return required_capability_for_task(task) in self.capabilities

    def configuration_status(self, *, provider: "Provider") -> ProviderReadiness:
        return ProviderReadiness(ready=True)

    def execution_readiness(
        self,
        *,
        provider: "Provider",
        task: "ModelTask",
    ) -> ProviderReadiness:
        if not self.supports_task(task):
            return ProviderReadiness(
                ready=False,
                reason=f"Provider '{self.key}' does not support task '{_task_value(task)}'.",
                failure_kind="provider_configuration",
            )
        return self.configuration_status(provider=provider)

    def check_health(self, *, provider: "Provider") -> ProviderHealthCheck:
        return ProviderHealthCheck(
            status="not_supported",
            status_reason=f"Provider '{self.key}' does not expose a live health check.",
        )

    def execute(
        self,
        *,
        task: ModelTask,
        context: "ProviderExecutionContext",
        provider: "Provider",
        model_config: "ModelConfig",
    ) -> ProviderExecutionResult:
        if not self.supports_task(task):
            raise ProviderConfigurationError(
                f"Provider '{self.key}' does not support task '{_task_value(task)}'."
            )
        if self.response_prefix is None:
            raise ProviderConfigurationError(
                f"Provider '{self.key}' does not have a runtime execution adapter."
            )
        preview = context.provider_prompt[:800]
        return ProviderExecutionResult(
            content=f"{self.response_prefix} [{model_config.key}] {preview}".strip(),
            provider_message_id=f"{self.key}:{model_config.key}:stub",
            usage=ProviderUsage(input_tokens=len(context.provider_prompt.split()), output_tokens=24, total_tokens=None),
            finish_reason="stop",
            raw_metadata={
                "provider_key": self.key,
                "task": _task_value(task),
                "visible_attachment_count": len(context.visible_attachments),
            },
        )


def _task_value(task: Any) -> str:
    enum_value = getattr(task, "value", None)
    if enum_value is not None:
        return enum_value

    task_value = str(task)
    if "." in task_value:
        task_value = task_value.rsplit(".", 1)[-1]
    if task_value.isupper():
        return task_value.lower()
    return task_value


def required_capability_for_task(task: "ModelTask") -> ProviderCapability:
    mapping = {
        "general_chat": ProviderCapability.CHAT,
        "orchestration": ProviderCapability.CHAT,
        "coding": ProviderCapability.CHAT,
        "search_research": ProviderCapability.CHAT,
        "vision": ProviderCapability.VISION,
        "synthesis": ProviderCapability.SYNTHESIS,
        "transcription": ProviderCapability.AUDIO_IN,
        "image_generation": ProviderCapability.IMAGE_GENERATION,
    }
    return mapping[_task_value(task)]


def build_local_failure_detail(
    *,
    failure_type: LocalFailureType,
    provider_key: str,
    provider_label: str,
    model_key: str,
    runtime_model_name: str | None,
    diagnostics: Mapping[str, object] | None = None,
) -> LocalModelFailureDetail:
    runtime_label = runtime_model_name or model_key
    retryable = failure_type in {
        LocalFailureType.REQUEST_TIMEOUT,
        LocalFailureType.RUNTIME_BUSY,
    }
    title_map = {
        LocalFailureType.RUNTIME_UNREACHABLE: "Lokalny runtime jest niedostepny",
        LocalFailureType.MODEL_MISSING: "Brakuje lokalnego modelu",
        LocalFailureType.REQUEST_TIMEOUT: "Lokalny model nie odpowiedzial na czas",
        LocalFailureType.RUNTIME_BUSY: "Lokalny runtime jest zajety",
        LocalFailureType.INVALID_ENDPOINT: "Endpoint runtime jest niepoprawny",
        LocalFailureType.CAPABILITY_MISMATCH: "Model nie obsluguje tego zadania",
        LocalFailureType.CONTEXT_TOO_LARGE: "Prompt jest za duzy dla lokalnego modelu",
        LocalFailureType.GENERATION_ERROR: "Lokalny runtime zwrocil blad generacji",
        LocalFailureType.UNKNOWN: "Lokalny runtime zakonczyl sie nieznanym bledem",
    }
    detail_map = {
        LocalFailureType.RUNTIME_UNREACHABLE: (
            f"Atnos nie moze polaczyc sie z lokalnym runtime {provider_label}. "
            "Sprawdz, czy usluga dziala i czy endpoint jest poprawny."
        ),
        LocalFailureType.MODEL_MISSING: (
            f"Model {runtime_label} nie jest dostepny w lokalnym runtime {provider_label}."
        ),
        LocalFailureType.REQUEST_TIMEOUT: (
            f"Lokalny model {runtime_label} nie zakonczyl generacji w przewidzianym czasie."
        ),
        LocalFailureType.RUNTIME_BUSY: (
            f"Lokalny runtime {provider_label} jest chwilowo zajety albo przeciążony."
        ),
        LocalFailureType.INVALID_ENDPOINT: (
            f"Skonfigurowany endpoint dla {provider_label} nie odpowiada na oczekiwanym API."
        ),
        LocalFailureType.CAPABILITY_MISMATCH: (
            f"Model {runtime_label} nie obsluguje tego typu zadania w {provider_label}."
        ),
        LocalFailureType.CONTEXT_TOO_LARGE: (
            f"To zadanie przekracza bezpieczny limit kontekstu dla modelu {runtime_label}."
        ),
        LocalFailureType.GENERATION_ERROR: (
            f"Lokalny runtime {provider_label} zwrocil blad podczas generacji odpowiedzi."
        ),
        LocalFailureType.UNKNOWN: (
            f"Nie udalo sie wiarygodnie sklasyfikowac bledu lokalnego runtime {provider_label}."
        ),
    }
    suggested_actions_map = {
        LocalFailureType.RUNTIME_UNREACHABLE: (
            "Uruchom lokalny runtime ponownie.",
            "Sprawdz endpoint runtime w Ustawieniach.",
            "Sprobuj ponownie po chwili.",
        ),
        LocalFailureType.MODEL_MISSING: (
            "Doinstaluj brakujacy model lokalny.",
            "Zmien model domyslny dla tego zadania.",
            "Uzyj fallbacku cloud, jesli jest dozwolony.",
        ),
        LocalFailureType.REQUEST_TIMEOUT: (
            "Sprobuj ponownie.",
            "Skróc prompt albo podziel zadanie na kroki.",
            "Rozwaz fallback cloud dla tego zadania.",
        ),
        LocalFailureType.RUNTIME_BUSY: (
            "Odczekaj chwile i sprobuj ponownie.",
            "Zamknij inne ciezkie generacje w lokalnym runtime.",
            "Rozwaz fallback cloud dla tego zadania.",
        ),
        LocalFailureType.INVALID_ENDPOINT: (
            "Popraw adres endpointu runtime w Ustawieniach.",
            "Sprawdz, czy runtime wystawia zgodne API.",
        ),
        LocalFailureType.CAPABILITY_MISMATCH: (
            "Zmien model na taki, ktory obsluguje to zadanie.",
            "Dla wizji lub dlugiej syntezy wybierz odpowiedni runtime.",
        ),
        LocalFailureType.CONTEXT_TOO_LARGE: (
            "Skróc prompt albo liczbe zalacznikow.",
            "Podziel zadanie na mniejsze kroki.",
        ),
        LocalFailureType.GENERATION_ERROR: (
            "Sprobuj ponownie.",
            "Sprawdz logi lokalnego runtime.",
            "Zmien model, jesli blad sie powtarza.",
        ),
        LocalFailureType.UNKNOWN: (
            "Sprobuj ponownie.",
            "Sprawdz logi lokalnego runtime.",
            "Zweryfikuj konfiguracje modelu i endpointu.",
        ),
    }
    return LocalModelFailureDetail(
        failure_type=failure_type.value,
        provider_key=provider_key,
        model_key=model_key,
        runtime_model_name=runtime_model_name,
        user_title=title_map[failure_type],
        user_detail=detail_map[failure_type],
        retryable=retryable,
        can_fallback=False,
        suggested_actions=suggested_actions_map[failure_type],
        diagnostics=dict(diagnostics) if diagnostics is not None else None,
    )
