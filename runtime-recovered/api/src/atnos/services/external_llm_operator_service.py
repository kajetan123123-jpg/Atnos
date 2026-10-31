from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import json
import re
import time
from typing import Any, Literal
import unicodedata
from urllib.parse import quote, urlparse

import httpx
import websockets
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.providers.registry import ProviderRegistry
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.browser_access_diagnostics_service import BrowserAccessDiagnosticsService
from atnos.services.browser_runtime_service import BrowserRuntimeError, BrowserRuntimeService
from atnos.services.desktop_chat_operator_service import DesktopChatOperatorService
from atnos.services.local_host_control_service import LocalHostControlService
from atnos.services.peer_consultation_service import PeerConsultationService
from atnos.services.runtime_settings_service import RuntimeControlSnapshot


ExternalLlmPath = Literal["api", "browser", "desktop", "blocked"]
ExternalLlmTaskType = Literal["coding", "research", "writing", "general"]
ExternalLlmTarget = Literal["claude", "chatgpt", "openai", "gemini", "grok", "best_external"]
ExternalLlmFallbackPolicy = Literal["normal", "browser_only"]


@dataclass(frozen=True, slots=True)
class ExternalLlmRequest:
    target: ExternalLlmTarget
    prompt: str
    task_type: ExternalLlmTaskType
    mode_preference: Literal["api", "browser", "desktop"] | None
    explicit_target: bool
    fallback_policy: ExternalLlmFallbackPolicy = "normal"


@dataclass(frozen=True, slots=True)
class ExternalLlmExecutionOutcome:
    ok: bool
    route_path: ExternalLlmPath
    provider_key: str
    model_key: str
    reason: str
    answer_text: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    blocker: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalLlmOperationResult:
    activated: bool
    assistant_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    selected_provider_key: str | None = None
    selected_model_key: str | None = None
    routing_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _BrowserLlmProfile:
    target: ExternalLlmTarget
    label: str
    domain: str
    preferred_url: str
    input_selectors: tuple[str, ...]
    send_selectors: tuple[str, ...]
    response_selectors: tuple[str, ...]
    logged_out_fragments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DevToolsTab:
    tab_id: str
    title: str | None
    url: str
    websocket_url: str


class ExternalLlmOperatorService:
    OPENAI_MODEL_CANDIDATES = ("gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1")
    ANTHROPIC_MODEL_CANDIDATES = ("claude-sonnet-4-20250514", "claude-3-7-sonnet-latest")
    PROVIDER_ALIASES: dict[str, ExternalLlmTarget] = {
        "claude": "claude",
        "anthropic": "claude",
        "chatgpt": "chatgpt",
        "chat gpt": "chatgpt",
        "openai": "openai",
        "gemini": "gemini",
        "grok": "grok",
    }
    ACTION_VERBS = (
        "zapytaj",
        "ask",
        "uzyj",
        "użyj",
        "use",
        "wyslij",
        "wyślij",
        "send",
        "skonsultuj",
        "consult",
        "skorzystaj",
        "korzystaj",
        "popros",
        "poproś",
        "odpytaj",
    )
    BEST_EXTERNAL_PATTERNS = (
        "best external llm",
        "best available external llm",
        "najlepszy zewnetrzny llm",
        "najlepszy zewnętrzny llm",
        "browserowe ai",
        "przegladarkowe ai",
        "browser ai",
        "ai w przegladarce",
        "inny llm",
        "other llm",
        "another llm",
    )
    CODING_HINTS = (
        "repo",
        "bug",
        "test",
        "tests",
        "refactor",
        "debug",
        "cli",
        "commit",
        "python",
        "typescript",
        "code",
    )
    RESEARCH_HINTS = (
        "research",
        "link",
        "links",
        "source",
        "sources",
        "cytat",
        "cytaty",
        "latest",
        "documentation",
        "docs",
        "porown",
        "porówn",
        "compare",
    )
    WRITING_HINTS = (
        "mail",
        "email",
        "copy",
        "write",
        "napisz",
        "tekst",
        "article",
        "post",
        "offer",
        "proposal",
    )
    EXPLICIT_REQUEST_RE = re.compile(
        r"^\s*(zapytaj|ask|uzyj|użyj|use|wyslij|wyślij|send)\s+"
        r"(claude|anthropic|chatgpt|chat gpt|openai|gemini|kimi|grok)\b[: ]*(.*)$",
        flags=re.IGNORECASE | re.DOTALL,
    )
    BROWSER_PROFILES: dict[ExternalLlmTarget, _BrowserLlmProfile] = {
        "claude": _BrowserLlmProfile(
            target="claude",
            label="Claude",
            domain="claude.ai",
            preferred_url="https://claude.ai/new",
            input_selectors=("textarea", "[contenteditable='true']"),
            send_selectors=(
                "button[type='submit']",
                "button[aria-label*='Send' i]",
                "button[aria-label*='Wyślij' i]",
            ),
            response_selectors=("[data-testid*='assistant']", ".assistant", "article"),
            logged_out_fragments=("login", "signin", "auth"),
        ),
        "chatgpt": _BrowserLlmProfile(
            target="chatgpt",
            label="ChatGPT",
            domain="chatgpt.com",
            preferred_url="https://chatgpt.com/",
            input_selectors=("textarea", "[contenteditable='true']"),
            send_selectors=(
                "button[type='submit']",
                "button[aria-label*='Send message' i]",
                "button[aria-label*='Wyślij' i]",
            ),
            response_selectors=(
                "[data-message-author-role='assistant']",
                "[data-testid*='assistant']",
                "article",
            ),
            logged_out_fragments=("login", "signin", "auth"),
        ),
        "openai": _BrowserLlmProfile(
            target="openai",
            label="ChatGPT",
            domain="chatgpt.com",
            preferred_url="https://chatgpt.com/",
            input_selectors=("textarea", "[contenteditable='true']"),
            send_selectors=(
                "button[type='submit']",
                "button[aria-label*='Send message' i]",
                "button[aria-label*='Wyślij' i]",
            ),
            response_selectors=(
                "[data-message-author-role='assistant']",
                "[data-testid*='assistant']",
                "article",
            ),
            logged_out_fragments=("login", "signin", "auth"),
        ),
    }

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.account_credentials = AccountCredentialsService(session, settings)

    def maybe_handle(
        self,
        *,
        content: str,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot,
    ) -> ExternalLlmOperationResult:
        request = self._detect_request(content)
        if request is None:
            return ExternalLlmOperationResult(activated=False)

        outcome = self._execute_request(
            request=request,
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        )
        return ExternalLlmOperationResult(
            activated=True,
            assistant_content=self._render_assistant_message(request=request, outcome=outcome),
            metadata={
                "external_llm_operator": {
                    "target": request.target,
                    "task_type": request.task_type,
                    "mode_preference": request.mode_preference,
                    "explicit_target": request.explicit_target,
                    "fallback_policy": request.fallback_policy,
                    "route_path": outcome.route_path,
                    "provider_key": outcome.provider_key,
                    "model_key": outcome.model_key,
                    "reason": outcome.reason,
                    "blocker": outcome.blocker,
                    **dict(outcome.metadata),
                }
            },
            selected_provider_key=(
                outcome.provider_key
                if outcome.route_path == "api"
                else "external-llm-operator"
                if outcome.route_path == "blocked"
                else f"external-{outcome.route_path}-operator"
            ),
            selected_model_key=outcome.model_key,
            routing_reason=outcome.reason,
        )

    def dispatch_auto(
        self,
        *,
        prompt: str,
        target: ExternalLlmTarget,
        task_type: ExternalLlmTaskType,
        mode_preference: Literal["api", "browser", "desktop"] | None,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot,
        routing_reason: str,
        fallback_policy: ExternalLlmFallbackPolicy = "normal",
    ) -> ExternalLlmOperationResult:
        cleaned_prompt = self._clean_extracted_prompt(prompt.strip()) or prompt.strip()
        routed_prompt = self._build_auto_routed_prompt(
            prompt=cleaned_prompt,
            target=target,
            task_type=task_type,
            mode_preference=mode_preference,
            routing_reason=routing_reason,
        )
        request = ExternalLlmRequest(
            target=target,
            prompt=routed_prompt,
            task_type=task_type,
            mode_preference=mode_preference,
            explicit_target=False,
            fallback_policy=fallback_policy,
        )
        outcome = self._execute_request(
            request=request,
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        )
        return ExternalLlmOperationResult(
            activated=True,
            assistant_content=self._render_assistant_message(request=request, outcome=outcome),
            metadata={
                "external_llm_operator": {
                    "target": request.target,
                    "task_type": request.task_type,
                    "mode_preference": request.mode_preference,
                    "explicit_target": request.explicit_target,
                    "fallback_policy": request.fallback_policy,
                    "auto_routed": True,
                    "auto_routing_reason": routing_reason,
                    "route_path": outcome.route_path,
                    "provider_key": outcome.provider_key,
                    "model_key": outcome.model_key,
                    "reason": outcome.reason,
                    "blocker": outcome.blocker,
                    **dict(outcome.metadata),
                }
            },
            selected_provider_key=(
                outcome.provider_key
                if outcome.route_path == "api"
                else "external-llm-operator"
                if outcome.route_path == "blocked"
                else f"external-{outcome.route_path}-operator"
            ),
            selected_model_key=outcome.model_key,
            routing_reason=routing_reason,
        )

    def _detect_request(self, content: str) -> ExternalLlmRequest | None:
        normalized = self._normalize_text(content)
        if not normalized:
            return None

        explicit_match = self._match_explicit_request(content=content, normalized=normalized)
        if explicit_match is not None:
            raw_target, raw_prompt = explicit_match
            prompt = self._clean_extracted_prompt(raw_prompt.strip()) or content.strip()
            target = self.PROVIDER_ALIASES.get(raw_target)
            if target is not None:
                mode_preference = self._extract_mode_preference(normalized)
                return ExternalLlmRequest(
                    target=target,
                    prompt=prompt,
                    task_type=self._classify_task_type(normalized),
                    mode_preference=mode_preference,
                    explicit_target=True,
                    fallback_policy=self._resolve_fallback_policy(
                        mode_preference=mode_preference,
                    ),
                )

        if any(pattern in normalized for pattern in self.BEST_EXTERNAL_PATTERNS):
            mode_preference = self._extract_mode_preference(normalized)
            return ExternalLlmRequest(
                target="best_external",
                prompt=content.strip(),
                task_type=self._classify_task_type(normalized),
                mode_preference=mode_preference,
                explicit_target=False,
                fallback_policy=self._resolve_fallback_policy(mode_preference=mode_preference),
            )

        if not any(verb in normalized for verb in self.ACTION_VERBS):
            return None

        for alias, target in self.PROVIDER_ALIASES.items():
            if alias in normalized:
                mode_preference = self._extract_mode_preference(normalized)
                return ExternalLlmRequest(
                    target=target,
                    prompt=self._clean_extracted_prompt(self._strip_target_phrase(content, alias)),
                    task_type=self._classify_task_type(normalized),
                    mode_preference=mode_preference,
                    explicit_target=True,
                    fallback_policy=self._resolve_fallback_policy(mode_preference=mode_preference),
                )
        return None

    def _match_explicit_request(
        self,
        *,
        content: str,
        normalized: str,
    ) -> tuple[str, str] | None:
        explicit_match = self.EXPLICIT_REQUEST_RE.match(content or "")
        if explicit_match is not None:
            return (
                self._normalize_text(explicit_match.group(2)),
                (explicit_match.group(3) or "").strip(),
            )
        prefixes = (
            "zapytaj",
            "ask",
            "uzyj",
            "use",
            "wyslij",
            "send",
            "skorzystaj z",
            "korzystaj z",
        )
        for alias in self.PROVIDER_ALIASES:
            normalized_alias = self._normalize_text(alias)
            for prefix in prefixes:
                candidate_prefix = f"{prefix} {normalized_alias}"
                if normalized.startswith(candidate_prefix):
                    stripped = (content or "").strip()
                    raw_prompt = stripped[len(candidate_prefix):].strip(" :,-")
                    return normalized_alias, raw_prompt
        return None

    def _execute_request(
        self,
        *,
        request: ExternalLlmRequest,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot,
    ) -> ExternalLlmExecutionOutcome:
        # Global privacy policy applies to every external transport, including
        # browser/desktop fallback, before credentials or sessions are accessed.
        if not self.settings.cloud_llm_allowed:
            return ExternalLlmExecutionOutcome(
                ok=False, route_path="blocked", provider_key="external-llm-operator",
                model_key="policy_blocked", reason="CLOUD_LLM_ALLOWED=false.",
                blocker="cloud_llm_disabled",
            )
        candidate_targets = self._resolve_candidate_targets(request)
        attempts: list[str] = []

        for target in candidate_targets:
            for route_path in self._resolve_route_order(request=request, target=target):
                if route_path == "api":
                    outcome = self._try_api_path(
                        target=target,
                        prompt=request.prompt,
                        allow_external_fallback=allow_external_fallback,
                        runtime_controls=runtime_controls,
                    )
                elif route_path == "browser":
                    outcome = self._try_browser_path(
                        target=target,
                        prompt=request.prompt,
                        runtime_controls=runtime_controls,
                    )
                elif route_path == "desktop":
                    outcome = self._try_desktop_path(
                        target=target,
                        prompt=request.prompt,
                        runtime_controls=runtime_controls,
                    )
                else:
                    continue

                if outcome.ok:
                    return outcome
                attempts.append(outcome.blocker or outcome.reason)

        provider_label = self._label_for_target(request.target)
        blocker = "; ".join(dict.fromkeys(filter(None, attempts))) or "Brak dzialajacego external execution path."
        return ExternalLlmExecutionOutcome(
            ok=False,
            route_path="blocked",
            provider_key="external-llm-operator",
            model_key="blocked",
            reason=f"External LLM routing zatrzymal sie na blockerach dla `{provider_label}`.",
            blocker=blocker,
            metadata={"attempted_targets": list(candidate_targets)},
        )

    def _resolve_candidate_targets(self, request: ExternalLlmRequest) -> tuple[ExternalLlmTarget, ...]:
        if request.target != "best_external":
            return (request.target,)
        if request.task_type == "coding":
            return ("openai", "claude", "chatgpt")
        if request.task_type in {"research", "writing"}:
            return ("claude", "openai", "chatgpt")
        return ("openai", "claude", "chatgpt")

    def _resolve_route_order(
        self,
        *,
        request: ExternalLlmRequest,
        target: ExternalLlmTarget,
    ) -> tuple[ExternalLlmPath, ...]:
        if request.mode_preference == "api":
            return ("api", "browser", "desktop")
        if request.fallback_policy == "browser_only":
            return ("browser",)
        if request.mode_preference == "browser":
            return ("browser", "api", "desktop")
        if request.mode_preference == "desktop":
            return ("desktop", "browser", "api")
        if target in {"gemini", "grok"}:
            return ("browser", "api")
        return ("api", "browser", "desktop")

    def _try_api_path(
        self,
        *,
        target: ExternalLlmTarget,
        prompt: str,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot,
    ) -> ExternalLlmExecutionOutcome:
        if target == "claude":
            return self._run_cloud_prompt(
                target=target,
                prompt=prompt,
                provider_key="anthropic-cloud",
                endpoint="https://api.anthropic.com/v1/messages",
                model_candidates=self.ANTHROPIC_MODEL_CANDIDATES,
                headers_builder=lambda secret: {
                    "x-api-key": secret,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                payload_builder=self._build_anthropic_request,
                response_parser=self._parse_anthropic_response,
                allow_external_fallback=allow_external_fallback,
                runtime_controls=runtime_controls,
            )
        if target in {"openai", "chatgpt"}:
            return self._run_cloud_prompt(
                target=target,
                prompt=prompt,
                provider_key="openai-cloud",
                endpoint="https://api.openai.com/v1/chat/completions",
                model_candidates=self.OPENAI_MODEL_CANDIDATES,
                headers_builder=lambda secret: {
                    "Authorization": f"Bearer {secret}",
                    "content-type": "application/json",
                },
                payload_builder=self._build_openai_request,
                response_parser=self._parse_openai_response,
                allow_external_fallback=allow_external_fallback,
                runtime_controls=runtime_controls,
            )
        return ExternalLlmExecutionOutcome(
            ok=False,
            route_path="api",
            provider_key=f"{target}-api",
            model_key="unavailable",
            reason=f"API path nie jest wspierany dla `{self._label_for_target(target)}` w tym passie.",
            blocker=f"Brak zaimplementowanego API path dla `{self._label_for_target(target)}`.",
        )

    def _run_cloud_prompt(
        self,
        *,
        target: ExternalLlmTarget,
        prompt: str,
        provider_key: str,
        endpoint: str,
        model_candidates: tuple[str, ...],
        headers_builder,
        payload_builder,
        response_parser,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot,
    ) -> ExternalLlmExecutionOutcome:
        if not allow_external_fallback:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="api",
                provider_key=provider_key,
                model_key="policy_blocked",
                reason="API path jest zablokowany przez execution policy.",
                blocker="Execution policy ma `allow_external_fallback=false`.",
            )
        if not runtime_controls.external_actions_enabled:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="api",
                provider_key=provider_key,
                model_key="runtime_blocked",
                reason="API path jest zablokowany przez runtime controls.",
                blocker="Runtime ma `external_actions_enabled=false`.",
            )

        account = self._get_configured_account_by_provider(provider_key)
        if account is None:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="api",
                provider_key=provider_key,
                model_key="unavailable",
                reason=f"Brak skonfigurowanego konta dla `{provider_key}`.",
                blocker=f"Vault nie ma aktywnego konta `{provider_key}` z sekretem.",
            )
        secret = self.account_credentials.resolve_secret_for_adapter(account.id)
        if not secret:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="api",
                provider_key=provider_key,
                model_key="unavailable",
                reason=f"Nie da sie rozwiazac sekretu dla `{provider_key}`.",
                blocker=f"Local vault nie zwrocil sekretu dla `{provider_key}`.",
            )
        secret_error = self._validate_secret_for_provider(provider_key=provider_key, secret=secret)
        if secret_error:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="api",
                provider_key=provider_key,
                model_key="invalid_secret",
                reason=f"Skonfigurowany sekret `{provider_key}` wyglada niepoprawnie.",
                blocker=secret_error,
            )

        last_error = "No supported model candidate succeeded."
        for model_name in model_candidates:
            try:
                with httpx.Client(timeout=75) as client:
                    response = client.post(
                        endpoint,
                        headers=headers_builder(secret),
                        json=payload_builder(model_name, prompt),
                    )
                if response.status_code >= 400:
                    last_error = self._extract_http_error(response)
                    if response.status_code in {400, 404}:
                        continue
                    raise RuntimeError(last_error)
                answer_text = response_parser(response.json())
                if not answer_text:
                    last_error = "Zewnetrzny model zwrocil pusty wynik."
                    continue
                provider_label = self._provider_label(provider_key)
                return ExternalLlmExecutionOutcome(
                    ok=True,
                    route_path="api",
                    provider_key=provider_key,
                    model_key=model_name,
                    reason=(
                        f"Wybralem API path: `{provider_label}` / `{model_name}`. "
                        "To byl najlepszy dostepny external executor dla tego requestu."
                    ),
                    answer_text=answer_text,
                    metadata={
                        "target": target,
                        "transport": "api",
                        "account_provider_key": provider_key,
                    },
                )
            except Exception as exc:
                last_error = self._sanitize_reason(str(exc))
                continue

        return ExternalLlmExecutionOutcome(
            ok=False,
            route_path="api",
            provider_key=provider_key,
            model_key=model_candidates[0],
            reason=f"API path `{provider_key}` nie dowiozl odpowiedzi.",
            blocker=last_error,
        )

    def _try_browser_path(
        self,
        *,
        target: ExternalLlmTarget,
        prompt: str,
        runtime_controls: RuntimeControlSnapshot,
    ) -> ExternalLlmExecutionOutcome:
        profile = self.BROWSER_PROFILES.get(target)
        if profile is None:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="browser",
                provider_key=f"{target}-browser",
                model_key=f"browser:{target}",
                reason=f"Browser path nie jest wspierany dla `{self._label_for_target(target)}` w tym passie.",
                blocker=f"Brak wspieranego browser profile dla `{self._label_for_target(target)}`.",
            )
        if not runtime_controls.browser_control_enabled:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="browser",
                provider_key=f"{target}-browser",
                model_key=f"browser:{target}",
                reason="Browser path jest wylaczony w runtime controls.",
                blocker="Runtime ma `browser_control_enabled=false`.",
            )
        if not runtime_controls.external_actions_enabled:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="browser",
                provider_key=f"{target}-browser",
                model_key=f"browser:{target}",
                reason="Browser path jest zablokowany przez runtime controls.",
                blocker="Runtime ma `external_actions_enabled=false`.",
            )

        diagnostics_service = BrowserAccessDiagnosticsService(self.session, settings=self.settings)
        diagnostics = diagnostics_service.diagnose_chrome(tab_limit=12)
        port, bootstrap_note, bridge_blocker = self._resolve_live_devtools_port(
            diagnostics=diagnostics,
            runtime_controls=runtime_controls,
        )
        if port is None:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="browser",
                provider_key=f"{target}-browser",
                model_key=f"browser:{target}",
                reason=f"Browser path dla `{profile.label}` jest zablokowany.",
                blocker=bridge_blocker or diagnostics.summary,
                metadata={"diagnostics_mode": diagnostics.mode},
            )

        try:
            answer_text, browser_meta = asyncio.run(
                self._run_browser_prompt_via_devtools(
                    profile=profile,
                    prompt=prompt,
                    port=port,
                )
            )
        except Exception as exc:
            blocker = self._sanitize_reason(str(exc))
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="browser",
                provider_key=f"{target}-browser",
                model_key=f"browser:{target}",
                reason=f"Browser path `{profile.label}` nie dowiozl odpowiedzi.",
                blocker=blocker,
                metadata={"diagnostics_mode": diagnostics.mode, "bridge_note": bootstrap_note},
            )

        return ExternalLlmExecutionOutcome(
            ok=True,
            route_path="browser",
            provider_key=f"{target}-browser",
            model_key=f"browser:{target}",
            reason=(
                f"Wybralem browser path: `{profile.label}` przez live Chrome DevTools. "
                "API bylo niedostepne albo browser path byl wybrany jawnie."
            ),
            answer_text=answer_text,
            metadata={
                "transport": "browser",
                "diagnostics_mode": diagnostics.mode,
                "bridge_note": bootstrap_note,
                **browser_meta,
            },
        )

    def _try_desktop_path(
        self,
        *,
        target: ExternalLlmTarget,
        prompt: str,
        runtime_controls: RuntimeControlSnapshot,
    ) -> ExternalLlmExecutionOutcome:
        if target != "kimi":
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="desktop",
                provider_key=f"{target}-desktop",
                model_key=f"desktop:{target}",
                reason=f"Desktop path nie jest wspierany dla `{self._label_for_target(target)}`.",
                blocker=f"Brak desktop-app workflow dla `{self._label_for_target(target)}`.",
            )
        if not runtime_controls.external_actions_enabled:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="desktop",
                provider_key="desktop-kimi",
                model_key="desktop:kimi",
                reason="Desktop path jest zablokowany przez runtime controls.",
                blocker="Runtime ma `external_actions_enabled=false`.",
            )
        host = LocalHostControlService(self.session, settings=self.settings)
        apps = host.list_desktop_apps()
        kimi_app = next((item for item in apps if "kimi" in item.label.casefold()), None)
        if kimi_app is None:
            return ExternalLlmExecutionOutcome(
                ok=False,
                route_path="desktop",
                provider_key="desktop-kimi",
                model_key="desktop:kimi",
                reason="Kimi desktop path nie jest dostepny na tym PC.",
                blocker="Atnos nie widzi aplikacji `Kimi` w katalogu desktop apps.",
            )
        desktop_dispatch = DesktopChatOperatorService(
            self.session,
            settings=self.settings,
            provider_registry=self.provider_registry,
        ).maybe_handle(
            content=f"zapytaj Kimi o {prompt}"
        )
        metadata = {"desktop_dispatch": desktop_dispatch.metadata} if desktop_dispatch.activated else {}
        answer_text = str(
            (desktop_dispatch.metadata.get("desktop_operator") or {}).get("answer_text")
            if desktop_dispatch.metadata
            else ""
        ).strip()
        if answer_text:
            return ExternalLlmExecutionOutcome(
                ok=True,
                route_path="desktop",
                provider_key="desktop-kimi",
                model_key="desktop:kimi",
                reason="Desktop Kimi path completed with verified answer capture.",
                answer_text=answer_text,
                metadata=metadata,
            )
        return ExternalLlmExecutionOutcome(
            ok=False,
            route_path="desktop",
            provider_key="desktop-kimi",
            model_key="desktop:kimi",
            reason="Desktop path zostal wybrany dla Kimi, ale verified answer capture nie zwrocil tresci.",
            blocker="Kimi desktop workflow did not return verified answer text after focus, accessibility and screenshot fallbacks.",
            metadata=metadata,
        )

    def _resolve_live_devtools_port(
        self,
        *,
        diagnostics,
        runtime_controls: RuntimeControlSnapshot,
    ) -> tuple[int | None, str | None, str | None]:
        if diagnostics.mode == "devtools_live" and diagnostics.remote_debugging_port is not None:
            return diagnostics.remote_debugging_port, "existing_live_bridge", None
        if diagnostics.browser_running:
            reasons = "; ".join(diagnostics.reasons) if diagnostics.reasons else diagnostics.summary
            return None, None, (
                "Chrome jest uruchomiony, ale nie wystawia live DevTools bridge dla biezacej sesji. "
                f"Szczegoly: {reasons}"
            )
        if not runtime_controls.browser_control_enabled:
            return None, None, "Browser control jest wylaczony, wiec Atnos nie moze uruchomic live bridge."
        try:
            launch = BrowserRuntimeService(self.settings).ensure_chrome_devtools_bridge(
                restart_existing=False,
                timeout_s=20,
            )
        except BrowserRuntimeError as exc:
            return None, None, self._sanitize_reason(str(exc))
        return launch.port, launch.status, None

    async def _run_browser_prompt_via_devtools(
        self,
        *,
        profile: _BrowserLlmProfile,
        prompt: str,
        port: int,
    ) -> tuple[str, dict[str, object]]:
        tab = await self._resolve_or_open_tab(profile=profile, port=port)
        async with websockets.connect(tab.websocket_url, max_size=8_000_000) as websocket:
            await self._cdp_send(websocket, "Page.enable")
            await self._cdp_send(websocket, "Runtime.enable")
            await self._cdp_send(websocket, "Page.bringToFront")
            current_url = await self._read_current_url(websocket)
            if profile.domain not in (urlparse(current_url).hostname or ""):
                await self._cdp_send(websocket, "Page.navigate", {"url": profile.preferred_url})
                await asyncio.sleep(4.0)
            current_url = await self._read_current_url(websocket)
            lowered_url = current_url.lower()
            if any(fragment in lowered_url for fragment in profile.logged_out_fragments):
                raise RuntimeError(
                    f"Target page for {profile.label} looks logged out (`{current_url}`)."
                )

            baseline_text = await self._extract_response_text(websocket, profile)
            focused = await self._focus_prompt_input(websocket, profile)
            if not focused:
                raise RuntimeError(
                    f"No writable prompt input was detected on `{current_url}`. "
                    "Session may be logged out or the UI changed."
                )

            await self._cdp_send(websocket, "Input.insertText", {"text": prompt})
            await asyncio.sleep(0.35)
            submitted = await self._submit_prompt(websocket, profile)
            if not submitted:
                await self._press_enter(websocket)
            answer_text = await self._wait_for_response_text(
                websocket,
                profile,
                baseline_text=baseline_text,
            )
            if not answer_text:
                raise RuntimeError(
                    f"Prompt was submitted to {profile.label}, but no readable assistant answer was extracted."
                )
            return answer_text, {"final_url": current_url, "tab_url": tab.url, "tab_title": tab.title}

    async def _resolve_or_open_tab(self, *, profile: _BrowserLlmProfile, port: int) -> _DevToolsTab:
        tab = self._find_matching_tab(port=port, domain=profile.domain)
        if tab is not None:
            return tab
        tab = self._open_new_devtools_tab(port=port, url=profile.preferred_url)
        if tab is not None:
            return tab
        LocalHostControlService(self.session, settings=self.settings).open_browser_url(profile.preferred_url)
        deadline = time.time() + 12.0
        while time.time() < deadline:
            await asyncio.sleep(1.5)
            tab = self._find_matching_tab(port=port, domain=profile.domain)
            if tab is not None:
                return tab
        raise RuntimeError(
            f"Chrome DevTools bridge is live, but no readable `{profile.label}` tab was found after opening `{profile.preferred_url}`."
        )

    def _find_matching_tab(self, *, port: int, domain: str) -> _DevToolsTab | None:
        tabs = self._list_devtools_tabs(port)
        matching = [item for item in tabs if domain in (urlparse(item.url).hostname or "")]
        if not matching:
            return None
        matching.sort(key=lambda item: ("/new" not in item.url, item.title or "", item.url))
        return matching[0]

    def _open_new_devtools_tab(self, *, port: int, url: str) -> _DevToolsTab | None:
        safe_url = quote(url, safe=":/?&=%#")
        endpoint = f"http://127.0.0.1:{int(port)}/json/new?{safe_url}"
        for method in ("put", "get"):
            try:
                with httpx.Client(timeout=4) as client:
                    response = getattr(client, method)(endpoint)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            websocket_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
            tab_url = str(payload.get("url") or url).strip()
            if not websocket_url:
                continue
            return _DevToolsTab(
                tab_id=str(payload.get("id") or ""),
                title=(str(payload.get("title")) if payload.get("title") else None),
                url=tab_url,
                websocket_url=websocket_url,
            )
        return None

    def _list_devtools_tabs(self, port: int) -> list[_DevToolsTab]:
        endpoint = f"http://127.0.0.1:{int(port)}/json/list"
        with httpx.Client(timeout=4) as client:
            response = client.get(endpoint)
        if response.status_code >= 400:
            raise RuntimeError(f"Chrome DevTools list endpoint returned HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Chrome DevTools list endpoint returned an invalid payload.")
        tabs: list[_DevToolsTab] = []
        for raw_item in payload:
            if not isinstance(raw_item, dict):
                continue
            if raw_item.get("type") != "page":
                continue
            url = str(raw_item.get("url") or "").strip()
            websocket_url = str(raw_item.get("webSocketDebuggerUrl") or "").strip()
            if not url or not websocket_url:
                continue
            tabs.append(
                _DevToolsTab(
                    tab_id=str(raw_item.get("id") or ""),
                    title=(str(raw_item.get("title")) if raw_item.get("title") else None),
                    url=url,
                    websocket_url=websocket_url,
                )
            )
        return tabs

    async def _focus_prompt_input(self, websocket, profile: _BrowserLlmProfile) -> bool:
        expression = (
            "(function(){"
            f"const selectors={json.dumps(list(profile.input_selectors))};"
            "for (const selector of selectors){"
            " const nodes=Array.from(document.querySelectorAll(selector));"
            " for (const node of nodes){"
            "  const disabled=Boolean(node.disabled)||node.getAttribute('aria-disabled')==='true'||node.hasAttribute('readonly');"
            "  if(disabled){continue;}"
            "  if(typeof node.focus==='function'){node.focus();}"
            "  return true;"
            " }"
            "}"
            "return false;"
            "})()"
        )
        return bool(await self._evaluate_js(websocket, expression))

    async def _submit_prompt(self, websocket, profile: _BrowserLlmProfile) -> bool:
        expression = (
            "(function(){"
            f"const selectors={json.dumps(list(profile.send_selectors))};"
            "for (const selector of selectors){"
            " const nodes=Array.from(document.querySelectorAll(selector));"
            " for (const node of nodes){"
            "  const disabled=Boolean(node.disabled)||node.getAttribute('aria-disabled')==='true';"
            "  if(disabled){continue;}"
            "  if(typeof node.click==='function'){node.click(); return true;}"
            " }"
            "}"
            "return false;"
            "})()"
        )
        return bool(await self._evaluate_js(websocket, expression))

    async def _press_enter(self, websocket) -> None:
        await self._cdp_send(
            websocket,
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
        )
        await self._cdp_send(
            websocket,
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
        )

    async def _wait_for_response_text(
        self,
        websocket,
        profile: _BrowserLlmProfile,
        *,
        baseline_text: str,
        timeout_s: float = 95.0,
    ) -> str:
        deadline = time.time() + timeout_s
        last_seen = ""
        stable_hits = 0
        baseline = baseline_text.strip()
        while time.time() < deadline:
            current = (await self._extract_response_text(websocket, profile)).strip()
            if current and current != baseline:
                if current == last_seen:
                    stable_hits += 1
                else:
                    last_seen = current
                    stable_hits = 0
                if stable_hits >= 1 or len(current) >= 180:
                    return current
            await asyncio.sleep(3.0)
        return last_seen or ""

    async def _extract_response_text(self, websocket, profile: _BrowserLlmProfile) -> str:
        expression = (
            "(function(){"
            f"const selectors={json.dumps(list(profile.response_selectors))};"
            "for (const selector of selectors){"
            " const nodes=Array.from(document.querySelectorAll(selector))"
            "  .map(node => (node.innerText || node.textContent || '').trim())"
            "  .filter(Boolean);"
            " if(nodes.length){return nodes[nodes.length - 1];}"
            "}"
            "return '';"
            "})()"
        )
        value = await self._evaluate_js(websocket, expression)
        return str(value or "")

    async def _read_current_url(self, websocket) -> str:
        value = await self._evaluate_js(websocket, "window.location.href")
        return str(value or "")

    async def _evaluate_js(self, websocket, expression: str) -> Any:
        payload = await self._cdp_send(
            websocket,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        if "value" in result:
            return result.get("value")
        return None

    async def _cdp_send(self, websocket, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = time.time_ns() % 1_000_000_000
        await websocket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw_message = await websocket.recv()
            payload = json.loads(raw_message)
            if payload.get("id") == message_id:
                if "error" in payload:
                    raise RuntimeError(f"Chrome DevTools call `{method}` failed: {payload['error']}")
                return payload.get("result") or {}

    def _extract_mode_preference(
        self,
        normalized: str,
    ) -> Literal["api", "browser", "desktop"] | None:
        if any(token in normalized for token in ("przegladar", "przeglądar", "browser", "chrome")):
            return "browser"
        if any(token in normalized for token in ("desktop", "aplikacj", "app")):
            return "desktop"
        if "api" in normalized:
            return "api"
        return None

    def _classify_task_type(self, normalized: str) -> ExternalLlmTaskType:
        if any(token in normalized for token in self.CODING_HINTS):
            return "coding"
        if any(token in normalized for token in self.RESEARCH_HINTS):
            return "research"
        if any(token in normalized for token in self.WRITING_HINTS):
            return "writing"
        return "general"

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
        return " ".join(ascii_only.split())
        replacements = {
            "ą": "a",
            "ć": "c",
            "ę": "e",
            "ł": "l",
            "ń": "n",
            "ó": "o",
            "ś": "s",
            "ż": "z",
            "ź": "z",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return " ".join(normalized.split())

    def _strip_target_phrase(self, content: str, alias: str) -> str:
        pattern = re.compile(
            rf"^\s*(zapytaj|ask|uzyj|użyj|use|wyslij|wyślij|send)\s+{re.escape(alias)}\b[: ]*(.*)$",
            flags=re.IGNORECASE | re.DOTALL,
        )
        match = pattern.match(content or "")
        if match is None:
            stripped = content.strip()
            normalized = self._normalize_text(stripped)
            normalized_alias = self._normalize_text(alias)
            fallback_prefixes = (
                f"zapytaj {normalized_alias}",
                f"ask {normalized_alias}",
                f"uzyj {normalized_alias}",
                f"use {normalized_alias}",
                f"wyslij {normalized_alias}",
                f"send {normalized_alias}",
                f"skorzystaj z {normalized_alias}",
                f"korzystaj z {normalized_alias}",
            )
            for prefix in fallback_prefixes:
                if normalized.startswith(prefix):
                    candidate = stripped[len(prefix):].strip(" :,-")
                    return candidate or stripped
            return stripped
        stripped = (match.group(2) or "").strip()
        return stripped or content.strip()

    @staticmethod
    def _clean_extracted_prompt(value: str) -> str:
        cleaned = value.strip()
        lowered = cleaned.casefold()
        for prefix in ("o ", "about ", "for ", "na temat "):
            if lowered.startswith(prefix):
                return cleaned[len(prefix):].strip()
        return cleaned

    @staticmethod
    def _resolve_fallback_policy(
        *,
        mode_preference: Literal["api", "browser", "desktop"] | None,
    ) -> ExternalLlmFallbackPolicy:
        if mode_preference == "browser":
            return "browser_only"
        return "normal"

    def _build_auto_routed_prompt(
        self,
        *,
        prompt: str,
        target: ExternalLlmTarget,
        task_type: ExternalLlmTaskType,
        mode_preference: Literal["api", "browser", "desktop"] | None,
        routing_reason: str,
    ) -> str:
        normalized = self._normalize_text(prompt)
        lines = [
            "Auto-routed quality frame for the user's real task.",
            f"Target executor: {self._label_for_target(target)}.",
            f"Routing reason: {routing_reason}",
            "Stay anchored to the user's exact wording and scope.",
            "Do not silently substitute another product, framework or company with a similar name.",
            "If the user mentions Atnos, Dansok, the app, the product or the repo, treat that as the user's real project context.",
            "If context is missing, state one short assumption instead of inventing a different context.",
        ]
        if task_type == "research":
            lines.extend(
                [
                    "This request expects current, externally verifiable information.",
                    "Use only facts you can verify from the live path you actually have.",
                    "If you cannot verify freshness or sources, say that explicitly instead of guessing.",
                    "Prefer short, action-oriented synthesis with direct links to sources.",
                ]
            )
        elif task_type == "coding":
            lines.extend(
                [
                    "Keep the answer implementation-oriented.",
                    "Prefer concrete architecture, tradeoffs, and next steps over generic explanation.",
                ]
            )
        elif task_type == "writing":
            lines.extend(
                [
                    "Keep the writing polished but practical.",
                    "Do not drift into marketing filler when the user asked for operational content.",
                ]
            )
        elif any(token in normalized for token in ("atnos", "dansok", "architektur", "produkt", "repo")):
            lines.extend(
                [
                    "The user is asking about their own product context.",
                    "Answer as if you are supporting that real app, not a public product with a similar name.",
                ]
            )
        if mode_preference == "browser":
            lines.append("If the browser path is unavailable, report that clearly instead of pretending you have live access.")
        return "\n".join(lines) + "\n\nUser request:\n" + prompt.strip()

    def _get_configured_account_by_provider(self, provider_key: str):
        for item in self.account_credentials.list_accounts():
            if item.provider_key != provider_key:
                continue
            if item.status != "configured":
                continue
            if not item.has_secret:
                continue
            return item
        return None

    @staticmethod
    def _build_openai_request(model_name: str, prompt: str) -> dict[str, Any]:
        return {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1_200,
        }

    @staticmethod
    def _parse_openai_response(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
        return ""

    @staticmethod
    def _build_anthropic_request(model_name: str, prompt: str) -> dict[str, Any]:
        return {
            "model": model_name,
            "max_tokens": 1_200,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }

    @staticmethod
    def _parse_anthropic_response(payload: dict[str, Any]) -> str:
        content_blocks = payload.get("content")
        if not isinstance(content_blocks, list):
            return ""
        fragments: list[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                fragments.append(block["text"])
        return "".join(fragments).strip()

    @staticmethod
    def _extract_http_error(response: httpx.Response) -> str:
        return PeerConsultationService._extract_http_error(response)

    @staticmethod
    def _validate_secret_for_provider(*, provider_key: str, secret: str) -> str | None:
        return PeerConsultationService._validate_secret_for_provider(provider_key=provider_key, secret=secret)

    @staticmethod
    def _sanitize_reason(value: str) -> str:
        return PeerConsultationService._sanitize_reason(value)

    @staticmethod
    def _provider_label(provider_key: str) -> str:
        if provider_key == "anthropic-cloud":
            return "Anthropic Cloud"
        if provider_key == "openai-cloud":
            return "OpenAI Cloud"
        return provider_key

    @classmethod
    def _label_for_target(cls, target: ExternalLlmTarget) -> str:
        mapping = {
            "claude": "Claude",
            "chatgpt": "ChatGPT",
            "openai": "OpenAI",
            "gemini": "Gemini",
            "grok": "Grok",
            "best_external": "best external LLM",
        }
        return mapping[target]

    def _render_assistant_message(
        self,
        *,
        request: ExternalLlmRequest,
        outcome: ExternalLlmExecutionOutcome,
    ) -> str:
        header = [
            f"Wybralem tor: `{outcome.route_path}`.",
            f"Cel: `{self._label_for_target(request.target)}`." if request.target != "best_external" else "Cel: `best external LLM`.",
            f"Powod: {outcome.reason}",
        ]
        if outcome.ok and outcome.answer_text:
            return (
                "\n".join(header)
                + "\n\nOdpowiedz zewnetrznego modelu:\n\n"
                + outcome.answer_text.strip()
            ).strip()
        blocker = outcome.blocker or "Nieznany bloker zewnetrznego execution path."
        browser_only_note = ""
        if request.fallback_policy == "browser_only":
            browser_only_note = (
                "\n\nTen request zostal zablokowany zamiast cichego fallbacku do API, "
                "bo wymagal live browser execution albo jawnie wskazywal browser AI."
            )
        return (
            "\n".join(header)
            + browser_only_note
            + "\n\nBloker:\n"
            + blocker
        ).strip()
