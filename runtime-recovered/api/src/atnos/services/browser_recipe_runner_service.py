from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.browser_domain_whitelist_repository import (
    BrowserDomainWhitelistRepository,
)
from atnos.repositories.user_repository import UserRepository
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.runtime_settings_service import RuntimeControlSnapshot


DEFAULT_ACTION_TIMEOUT_MS = 15_000
DEFAULT_TOTAL_TIMEOUT_MS = 300_000
DEFAULT_RETRY_LIMIT = 2
DEFAULT_COOKIE_SELECTORS = (
    "button[id*='accept']",
    "button[class*='cookie']",
    "button[aria-label*='accept' i]",
    "button[title*='accept' i]",
    "[data-testid*='accept']",
)
DEFAULT_MODAL_CLOSE_SELECTORS = (
    "button[aria-label='Close']",
    "button[aria-label='Zamknij']",
    "[role='dialog'] button[class*='close']",
    "[role='dialog'] button[title*='close' i]",
    ".modal button[class*='close']",
)
ALWAYS_BLOCKED_DOMAINS = ("paypal.com", "stripe.com")
ALWAYS_BLOCKED_SUFFIXES = (".gov", ".gov.pl")
ALWAYS_BLOCKED_TOKENS = ("bank",)
ALWAYS_BLOCKED_HOSTS = ("localhost", "metadata.google.internal")
SUPPORTED_STEP_TYPES = {
    "navigate",
    "wait",
    "click",
    "type",
    "upload_file",
    "download",
    "extract",
    "submit",
    "verify_state",
    "screenshot",
    "collect_links",
    "fill_fields",
}


class BrowserRecipeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "browser_recipe_failed",
        artifacts: dict[str, str] | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.artifacts = artifacts or {}
        self.details = details or {}


class BrowserRecipeBlockedError(BrowserRecipeError):
    pass


class BrowserRecipeApprovalRequiredError(BrowserRecipeError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserRecipeStep:
    step_type: str
    payload: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_input(cls, item: dict[str, object]) -> BrowserRecipeStep:
        raw_type = str(item.get("type") or "").strip().lower()
        if raw_type not in SUPPORTED_STEP_TYPES:
            raise BrowserRecipeError(
                f"Unsupported browser recipe step '{raw_type}'.",
                code="unsupported_step",
            )
        payload = {key: value for key, value in item.items() if key != "type"}
        return cls(step_type=raw_type, payload=payload)

    def to_dict(self) -> dict[str, object]:
        return {"type": self.step_type, **self.payload}


@dataclass(frozen=True, slots=True)
class BrowserRecipePayload:
    steps: tuple[BrowserRecipeStep, ...]
    headless: bool
    action_timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS
    total_timeout_ms: int = DEFAULT_TOTAL_TIMEOUT_MS
    retry_limit: int = DEFAULT_RETRY_LIMIT
    allowed_domains: tuple[str, ...] = ()
    conversation_id: int | None = None
    recipe_name: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_input(
        cls,
        payload: dict[str, object],
        *,
        default_headless: bool,
    ) -> BrowserRecipePayload:
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise BrowserRecipeError("Browser recipe requires at least one step.", code="missing_steps")
        steps = tuple(
            BrowserRecipeStep.from_input(item)
            for item in raw_steps
            if isinstance(item, dict)
        )
        if not steps:
            raise BrowserRecipeError("Browser recipe requires at least one valid step.", code="missing_steps")
        raw_headless = payload.get("headless")
        headless = default_headless if raw_headless is None else bool(raw_headless)
        raw_domains = payload.get("allowed_domains")
        allowed_domains = tuple(
            str(item).strip().lower()
            for item in raw_domains
            if isinstance(item, str) and str(item).strip()
        ) if isinstance(raw_domains, list) else ()
        raw_metadata = payload.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        return cls(
            steps=steps,
            headless=headless,
            action_timeout_ms=max(1_000, int(payload.get("action_timeout_ms") or DEFAULT_ACTION_TIMEOUT_MS)),
            total_timeout_ms=max(5_000, int(payload.get("total_timeout_ms") or DEFAULT_TOTAL_TIMEOUT_MS)),
            retry_limit=max(0, min(4, int(payload.get("retry_limit") or DEFAULT_RETRY_LIMIT))),
            allowed_domains=allowed_domains,
            conversation_id=(
                int(payload["conversation_id"])
                if payload.get("conversation_id") not in {None, ""}
                else None
            ),
            recipe_name=(
                str(payload["recipe_name"]).strip() if payload.get("recipe_name") else None
            ),
            metadata={str(key): value for key, value in metadata.items()},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "headless": self.headless,
            "action_timeout_ms": self.action_timeout_ms,
            "total_timeout_ms": self.total_timeout_ms,
            "retry_limit": self.retry_limit,
            "allowed_domains": list(self.allowed_domains),
            "conversation_id": self.conversation_id,
            "recipe_name": self.recipe_name,
            "metadata": dict(self.metadata),
        }


class BrowserRecipeRunnerService:
    def __init__(
        self,
        settings: Settings,
        *,
        session: Session | None = None,
        runtime_controls: RuntimeControlSnapshot | None = None,
    ) -> None:
        self.settings = settings
        self.browser_runtime = BrowserRuntimeService(settings)
        self.session = session
        self.runtime_controls = runtime_controls

    def run(self, recipe: dict[str, object] | BrowserRecipePayload) -> dict[str, Any]:
        browser_state = self.browser_runtime.describe()
        if not browser_state.ready:
            raise BrowserRecipeError(browser_state.status_reason, code="runtime_unavailable")

        payload = (
            recipe
            if isinstance(recipe, BrowserRecipePayload)
            else BrowserRecipePayload.from_input(
                recipe,
                default_headless=self._default_headless(),
            )
        )
        self._enforce_domain_policy(payload)

        automation_profile_dir = (self.settings.data_dir / "browser-automation-profile").resolve()
        automation_profile_dir.mkdir(parents=True, exist_ok=True)
        browser_executable = (
            self.browser_runtime.resolve_managed_browser_executable()
            or browser_state.browser_executable
        )
        if browser_executable is None:
            raise BrowserRecipeError(
                "Playwright Chromium runtime is not available.",
                code="runtime_unavailable",
            )

        final_screenshot_path = (
            self.settings.browser_downloads_dir / f"browser-final-{uuid4().hex}.png"
        ).resolve()
        error_screenshot_path = self._debug_browser_path("error")
        trace_path = (
            self.settings.browser_trace_dir / f"trusted-auto-trace-{uuid4().hex}.zip"
        ).resolve()
        spec = {
            "profileDir": automation_profile_dir.as_posix(),
            "downloadsDir": browser_state.downloads_dir.as_posix(),
            "tracePath": trace_path.as_posix(),
            "finalScreenshotPath": final_screenshot_path.as_posix(),
            "errorScreenshotPath": error_screenshot_path.as_posix(),
            "browserExecutable": browser_executable.as_posix(),
            "headless": payload.headless,
            "actionTimeoutMs": payload.action_timeout_ms,
            "totalTimeoutMs": payload.total_timeout_ms,
            "retryLimit": payload.retry_limit,
            "steps": [step.to_dict() for step in payload.steps],
            "cookieSelectors": list(DEFAULT_COOKIE_SELECTORS),
            "modalCloseSelectors": list(DEFAULT_MODAL_CLOSE_SELECTORS),
            "overlayProbeTimeoutMs": 3_000,
            "recipeName": payload.recipe_name,
            "metadata": dict(payload.metadata),
        }
        raw_payload = self._run_with_crash_recovery(spec, timeout_ms=payload.total_timeout_ms)
        result = self._normalize_runner_result(raw_payload)
        runtime_fallback_reason = None
        if not self.browser_runtime.is_managed_runtime(browser_executable):
            runtime_fallback_reason = (
                "Managed Chromium was unavailable, so browser execution fell back to an external desktop browser."
            )
        else:
            runtime_fallback_reason = (
                "Managed Playwright Chromium handled this run as the default browser executor."
            )
        result["runtime_fallback"] = runtime_fallback_reason
        return result

    def run_named_recipe(
        self,
        recipe_name: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        from atnos.services.browser_recipes import resolve_browser_recipe

        handler = resolve_browser_recipe(recipe_name)
        return handler(self, payload or {})

    def _run_with_crash_recovery(
        self,
        spec: dict[str, object],
        *,
        timeout_ms: int,
    ) -> dict[str, object]:
        crash_attempts = 0
        while True:
            payload = self._invoke_runner(spec, timeout_ms=timeout_ms)
            if bool(payload.get("ok")):
                return payload
            error_code = str(payload.get("code") or "")
            if error_code == "browser_crashed" and crash_attempts < 1:
                crash_attempts += 1
                continue
            return payload

    def _invoke_runner(
        self,
        spec: dict[str, object],
        *,
        timeout_ms: int,
    ) -> dict[str, object]:
        script_path = self._resolve_browser_runner_script()
        spec_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                suffix=".json",
                delete=False,
                encoding="utf-8",
                dir=self.settings.browser_trace_dir,
            ) as handle:
                json.dump(spec, handle)
                spec_path = handle.name

            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = self.settings.playwright_browsers_path.as_posix()
            command = ["node", str(script_path), spec_path]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(5, int(timeout_ms / 1000) + 10),
                    check=False,
                    cwd=str(script_path.parent),
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise BrowserRecipeError(
                    "Browser recipe exceeded the total timeout.",
                    code="recipe_timeout",
                    details={"stdout": exc.stdout or "", "stderr": exc.stderr or ""},
                    artifacts=self._extract_known_artifacts(spec),
                ) from exc
            try:
                payload = json.loads(result.stdout.strip() or "{}")
            except json.JSONDecodeError as exc:
                raise BrowserRecipeError(
                    result.stderr.strip() or "Browser runner returned invalid JSON.",
                    code="invalid_runner_payload",
                    artifacts=self._extract_known_artifacts(spec),
                ) from exc
            if result.returncode != 0 and "code" not in payload:
                payload["code"] = "runner_failed"
            if result.returncode != 0 and not payload.get("error"):
                payload["error"] = result.stderr.strip() or "Browser recipe failed."
            return payload
        finally:
            if spec_path:
                try:
                    Path(spec_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _normalize_runner_result(self, payload: dict[str, object]) -> dict[str, Any]:
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
        normalized = {
            "ok": bool(payload.get("ok")),
            "results": payload.get("results") if isinstance(payload.get("results"), list) else [],
            "finalUrl": str(payload.get("finalUrl") or ""),
            "artifacts": {str(key): str(value) for key, value in artifacts.items()},
        }
        if normalized["ok"]:
            return normalized
        raise BrowserRecipeError(
            str(payload.get("error") or "Browser recipe failed."),
            code=str(payload.get("code") or "browser_recipe_failed"),
            artifacts=normalized["artifacts"],
            details={
                "results": normalized["results"],
                "final_url": normalized["finalUrl"],
            },
        )

    def _enforce_domain_policy(self, payload: BrowserRecipePayload) -> None:
        configured_allow_any = True
        if self.runtime_controls is not None:
            configured_allow_any = bool(self.runtime_controls.browser_allow_any_url)
        stored_domains = self._stored_whitelist_domains()
        effective_domains = {
            self._normalize_domain(item)
            for item in (*payload.allowed_domains, *stored_domains)
            if self._normalize_domain(item)
        }

        for step in payload.steps:
            if step.step_type != "navigate":
                continue
            url = str(step.payload.get("url") or "").strip()
            if not url:
                raise BrowserRecipeError("Navigate step requires a URL.", code="invalid_recipe")
            parsed = urlparse(url)
            if parsed.scheme.lower() not in {"http", "https"}:
                raise BrowserRecipeBlockedError(
                    "Browser navigation only allows http and https URLs.",
                    code="blocked_protocol",
                    details={"url": url, "scheme": parsed.scheme.lower()},
                )
            host = self._normalize_domain(parsed.hostname)
            if not host:
                raise BrowserRecipeError("Navigate step requires a valid http/https URL.", code="invalid_recipe")
            if self._is_private_network_target(host):
                raise BrowserRecipeBlockedError(
                    f"Navigation to private or local target '{host}' is blocked.",
                    code="blocked_private_network",
                    details={"host": host, "url": url},
                )
            if self._is_blacklisted_domain(host):
                raise BrowserRecipeBlockedError(
                    f"Navigation to '{host}' is blocked by browser domain policy.",
                    code="blocked_domain",
                    details={"host": host, "url": url},
                )
            if configured_allow_any:
                continue
            if self._is_domain_allowed(host, effective_domains):
                continue
            raise BrowserRecipeApprovalRequiredError(
                f"Navigation to '{host}' requires approval because browser_allow_any_url is disabled.",
                code="approval_required",
                details={"host": host, "url": url},
            )

    def _stored_whitelist_domains(self) -> tuple[str, ...]:
        if self.session is None:
            return ()
        owner = UserRepository(self.session).get_owner()
        if owner is None:
            return ()
        rows = BrowserDomainWhitelistRepository(self.session).list_for_user(owner.id)
        return tuple(str(item.domain).strip().lower() for item in rows if item.domain)

    @staticmethod
    def _normalize_domain(value: str | None) -> str:
        if not value:
            return ""
        return str(value).strip().lower().lstrip(".")

    @staticmethod
    def _is_domain_allowed(host: str, allowed_domains: Iterable[str]) -> bool:
        for candidate in allowed_domains:
            normalized = str(candidate).strip().lower().lstrip(".")
            if not normalized:
                continue
            if host == normalized or host.endswith(f".{normalized}"):
                return True
        return False

    @staticmethod
    def _is_blacklisted_domain(host: str) -> bool:
        normalized = host.lower()
        if any(normalized == domain or normalized.endswith(f".{domain}") for domain in ALWAYS_BLOCKED_DOMAINS):
            return True
        if any(normalized.endswith(suffix) for suffix in ALWAYS_BLOCKED_SUFFIXES):
            return True
        return any(token in normalized for token in ALWAYS_BLOCKED_TOKENS)

    @staticmethod
    def _is_private_network_target(host: str) -> bool:
        normalized = host.strip().lower().rstrip(".")
        if normalized in ALWAYS_BLOCKED_HOSTS or normalized.endswith(".localhost"):
            return True
        try:
            addresses = [ipaddress.ip_address(normalized)]
        except ValueError:
            try:
                addresses = {
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
                }
            except (OSError, ValueError):
                # A browser target that cannot be resolved safely is not actionable.
                return True
        return any(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            for address in addresses
        )

    def _default_headless(self) -> bool:
        if self.runtime_controls is not None:
            return bool(self.runtime_controls.stealth_headless_enabled)
        return False

    def _resolve_browser_runner_script(self) -> Path:
        candidates = (
            self.settings.app_root / "runtime-recovered" / "api" / "scripts" / "playwright_trusted_auto_runner.mjs",
            self.settings.app_root / "scripts" / "playwright_trusted_auto_runner.mjs",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        raise BrowserRecipeError("Trusted Auto browser runner script was not found.", code="runner_missing")

    def _debug_browser_path(self, prefix: str) -> Path:
        target_dir = (self.settings.data_dir / "atnos-debug" / "browser").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return (target_dir / f"{prefix}-{timestamp}-{uuid4().hex}.png").resolve()

    @staticmethod
    def _extract_known_artifacts(spec: dict[str, object]) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        for artifact_key, spec_key in (
            ("final_screenshot", "finalScreenshotPath"),
            ("error_screenshot", "errorScreenshotPath"),
            ("trace", "tracePath"),
        ):
            raw_value = spec.get(spec_key)
            if raw_value:
                artifacts[artifact_key] = str(raw_value)
        return artifacts
