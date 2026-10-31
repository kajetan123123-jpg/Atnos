from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from atnos.core.config import Settings
from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.core.event_bus import EventBus
from atnos.jarvis.types import JarvisEvent, PageLease, ToolResult
from atnos.services.browser_runtime_service import BrowserRuntimeService

logger = getLogger(__name__)


@dataclass(slots=True)
class ServiceSessionSpec:
    service: str
    display_name: str
    login_url: str
    validation_url: str
    logged_out_url_fragments: tuple[str, ...] = ("login", "signin")
    logged_in_selectors: tuple[str, ...] = ()
    prefer_external_login: bool = False


@dataclass(slots=True)
class SessionRecord:
    service: str
    storage_state_path: Path
    context: Any | None = None
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(5))
    expired: bool = False
    pending_manual_login: bool = False
    source: str = "storage_state"


@dataclass(slots=True)
class PendingManualLoginWindow:
    service: str
    playwright: Any | None
    context: Any | None
    page: Any | None
    profile_dir: Path
    attempt_id: str
    process: subprocess.Popen[Any] | None = None
    mode: str = "playwright"
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BrowserSessionManager:
    SERVICE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")

    SERVICE_SPECS: dict[str, ServiceSessionSpec] = {
        "ideogram": ServiceSessionSpec(
            service="ideogram",
            display_name="Ideogram",
            login_url="https://ideogram.ai/login",
            validation_url="https://ideogram.ai/t/explore",
        ),
        "youtube": ServiceSessionSpec(
            service="youtube",
            display_name="YouTube Studio",
            login_url="https://studio.youtube.com/",
            validation_url="https://studio.youtube.com/",
            prefer_external_login=True,
        ),
        "gmail": ServiceSessionSpec(
            service="gmail",
            display_name="Gmail",
            login_url="https://mail.google.com/",
            validation_url="https://mail.google.com/",
            prefer_external_login=True,
        ),
        "google-calendar": ServiceSessionSpec(
            service="google-calendar",
            display_name="Google Calendar",
            login_url="https://calendar.google.com/",
            validation_url="https://calendar.google.com/",
            prefer_external_login=True,
        ),
        "github": ServiceSessionSpec(
            service="github",
            display_name="GitHub",
            login_url="https://github.com/login",
            validation_url="https://github.com/",
        ),
        "veed": ServiceSessionSpec(
            service="veed",
            display_name="VEED",
            login_url="https://www.veed.io/login",
            validation_url="https://www.veed.io/dashboard",
        ),
        "capcut": ServiceSessionSpec(
            service="capcut",
            display_name="CapCut",
            login_url="https://www.capcut.com/login",
            validation_url="https://www.capcut.com/editor",
        ),
        "claude": ServiceSessionSpec(
            service="claude",
            display_name="Claude",
            login_url="https://claude.ai/login",
            validation_url="https://claude.ai/new",
        ),
        "chatgpt": ServiceSessionSpec(
            service="chatgpt",
            display_name="ChatGPT",
            login_url="https://chatgpt.com/",
            validation_url="https://chatgpt.com/",
        ),
        "gemini": ServiceSessionSpec(
            service="gemini",
            display_name="Gemini",
            login_url="https://gemini.google.com/",
            validation_url="https://gemini.google.com/app",
            prefer_external_login=True,
        ),
        "videdge": ServiceSessionSpec(
            service="videdge",
            display_name="VidEdge",
            login_url="https://videdge.ai/",
            validation_url="https://videdge.ai/",
        ),
        "facelessreels": ServiceSessionSpec(
            service="facelessreels",
            display_name="FacelessReels",
            login_url="https://www.facelessreels.com/",
            validation_url="https://www.facelessreels.com/",
        ),
        "zebracat": ServiceSessionSpec(
            service="zebracat",
            display_name="Zebracat",
            login_url="https://www.zebracat.ai/",
            validation_url="https://www.zebracat.ai/",
        ),
        "runway": ServiceSessionSpec(
            service="runway",
            display_name="Runway",
            login_url="https://app.runwayml.com/login",
            validation_url="https://app.runwayml.com/",
        ),
        "luma": ServiceSessionSpec(
            service="luma",
            display_name="Luma",
            login_url="https://lumalabs.ai/dream-machine",
            validation_url="https://lumalabs.ai/dream-machine",
        ),
        "klap": ServiceSessionSpec(
            service="klap",
            display_name="Klap",
            login_url="https://klap.app/",
            validation_url="https://klap.app/",
        ),
        "autoshorts": ServiceSessionSpec(
            service="autoshorts",
            display_name="AutoShorts",
            login_url="https://www.automaticshorts.com/",
            validation_url="https://www.automaticshorts.com/",
        ),
        "autoaishorts": ServiceSessionSpec(
            service="autoaishorts",
            display_name="AutoAIShorts",
            login_url="https://www.autoaishorts.com/",
            validation_url="https://www.autoaishorts.com/",
        ),
        "elevenlabs": ServiceSessionSpec(
            service="elevenlabs",
            display_name="ElevenLabs",
            login_url="https://elevenlabs.io/app/sign-in",
            validation_url="https://elevenlabs.io/app",
        ),
        "canva": ServiceSessionSpec(
            service="canva",
            display_name="Canva",
            login_url="https://www.canva.com/login",
            validation_url="https://www.canva.com/",
        ),
    }

    def __init__(self, settings: Settings, config_store: JarvisConfigStore, event_bus: EventBus) -> None:
        self.settings = settings
        self.config_store = config_store
        self.event_bus = event_bus
        self.browser_runtime = BrowserRuntimeService(settings)
        self.config_store.ensure_layout()
        self._custom_specs: dict[str, ServiceSessionSpec] = {}
        self._service_specs: dict[str, ServiceSessionSpec] = dict(self.SERVICE_SPECS)
        self._load_custom_specs()
        self._records: dict[str, SessionRecord] = {
            service: self._build_record(service) for service in self._service_specs
        }
        self._pending_manual_logins: dict[str, PendingManualLoginWindow] = {}
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._log_path = self.config_store.paths.logs_dir / self.config_store.current().logging.browser_sessions_log

    async def preload_all(self) -> None:
        session_paths = [record.storage_state_path for record in self._records.values() if record.storage_state_path.exists()]
        if not session_paths:
            return
        await self._ensure_browser()
        for record in self._records.values():
            if record.storage_state_path.exists():
                await self._load_context(record)

    def known_services(self) -> tuple[str, ...]:
        return tuple(self._records.keys())

    def register_custom_service(
        self,
        *,
        service: str,
        display_name: str,
        login_url: str,
        validation_url: str | None = None,
        persist: bool = True,
    ) -> ServiceSessionSpec:
        normalized_service = self._normalize_service_key(service)
        normalized_login_url = self._normalize_http_url(login_url, field_name="login_url")
        normalized_validation_url = self._normalize_http_url(
            validation_url or self._origin_from_url(normalized_login_url),
            field_name="validation_url",
        )
        normalized_display_name = display_name.strip() or normalized_service
        prefer_external_login = self._should_prefer_external_login(normalized_login_url)
        existing = self._service_specs.get(normalized_service)
        if existing is not None and normalized_service in self.SERVICE_SPECS:
            return existing
        spec = ServiceSessionSpec(
            service=normalized_service,
            display_name=normalized_display_name[:120],
            login_url=normalized_login_url,
            validation_url=normalized_validation_url,
            prefer_external_login=prefer_external_login,
        )
        self._custom_specs[normalized_service] = spec
        self._service_specs[normalized_service] = spec
        self._ensure_record(normalized_service)
        if persist:
            self._persist_custom_specs()
        return spec

    async def start_manual_login_for_url(
        self,
        *,
        service: str,
        display_name: str,
        login_url: str,
        validation_url: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        spec = self.register_custom_service(
            service=service,
            display_name=display_name,
            login_url=login_url,
            validation_url=validation_url,
        )
        return await self.start_manual_login(spec.service)

    async def get_page(self, service: str, task_id: str) -> PageLease | ToolResult:
        async with self._lock:
            record = self._ensure_record(service)
        if record.context is None or record.expired:
            record.pending_manual_login = True
            await self._log_event(service, JarvisEvent.NEEDS_MANUAL_LOGIN, {"task_id": task_id})
            await self.event_bus.publish(JarvisEvent.NEEDS_MANUAL_LOGIN, {"service": service, "task_id": task_id})
            return ToolResult(success=False, reason="awaiting_auth", provider_key=service)
        semaphore_value = getattr(record.semaphore, "_value", 0)
        if semaphore_value <= 0:
            return ToolResult(success=False, queued=True, retry_after_s=3, reason="page_limit_reached", provider_key=service)
        await record.semaphore.acquire()
        page = await record.context.new_page()
        lease = PageLease(service=service, task_id=task_id, page=page, context_id=uuid4().hex)
        await self._log_event(service, JarvisEvent.PAGE_ACQUIRED, lease.to_dict())
        await self.event_bus.publish(JarvisEvent.PAGE_ACQUIRED, lease)
        return lease

    async def release_page(self, lease: PageLease) -> None:
        record = self._records.get(lease.service)
        try:
            await lease.page.close()
        finally:
            if record is not None:
                record.semaphore.release()
        await self._log_event(lease.service, JarvisEvent.PAGE_RELEASED, lease.to_dict())
        await self.event_bus.publish(JarvisEvent.PAGE_RELEASED, lease)

    async def is_session_valid(self, service: str) -> bool:
        record = self._records.get(service)
        if record is None or record.context is None:
            return False
        spec = self._service_specs[service]
        page = await record.context.new_page()
        try:
            await page.goto(spec.validation_url, wait_until="domcontentloaded")
            current_url = str(page.url).lower()
            return not any(fragment in current_url for fragment in spec.logged_out_url_fragments)
        except Exception:
            return False
        finally:
            await page.close()

    async def handle_manual_login_confirmed(self, payload: dict[str, Any]) -> None:
        service = str(payload.get("service") or "").strip().lower()
        if not service:
            return
        await self.confirm_manual_login(service)

    async def start_manual_login(self, service: str) -> tuple[bool, dict[str, Any]]:
        spec = self._require_spec(service)
        record = self._ensure_record(service)
        if service in self._pending_manual_logins:
            record.pending_manual_login = True
            return True, self.describe_session(service)

        browser_state = self.browser_runtime.describe()
        if not browser_state.ready:
            record.pending_manual_login = False
            return False, self._session_payload(
                record,
                spec,
                status_reason=browser_state.status_reason,
            )

        try:
            pending = await self._launch_manual_login_window(spec, browser_state)
        except RuntimeError as exc:
            record.pending_manual_login = False
            return False, self._session_payload(
                record,
                spec,
                status_reason=str(exc),
            )
        except Exception as exc:
            logger.exception("Failed to open manual login window | service=%s", service)
            record.pending_manual_login = False
            return False, self._session_payload(
                record,
                spec,
                status_reason=self._manual_login_error_message(exc),
            )
        self._pending_manual_logins[service] = pending
        record.pending_manual_login = True
        started_reason = (
            "Okno logowania jest otwarte. Dokoncz logowanie i kliknij Potwierdz zapis."
            if pending.mode == "playwright"
            else "Okno logowania zostalo otwarte. Po zakonczeniu logowania zamknij je i kliknij Potwierdz zapis."
        )
        await self._log_event(
            service,
            JarvisEvent.MANUAL_LOGIN_STARTED,
            {
                "login_url": spec.login_url,
                "validation_url": spec.validation_url,
                "profile_dir": pending.profile_dir.as_posix(),
                "mode": pending.mode,
            },
        )
        await self.event_bus.publish(
            JarvisEvent.MANUAL_LOGIN_STARTED,
            {
                "service": service,
                "login_url": spec.login_url,
                "validation_url": spec.validation_url,
                "profile_dir": pending.profile_dir.as_posix(),
                "mode": pending.mode,
            },
        )
        return True, self._session_payload(record, spec, status_reason=started_reason)

    async def confirm_manual_login(self, service: str) -> tuple[bool, dict[str, Any]]:
        spec = self._require_spec(service)
        record = self._ensure_record(service)
        pending = self._pending_manual_logins.get(service)

        if pending is not None:
            if pending.mode == "external_process":
                if pending.process is not None and pending.process.poll() is None:
                    return False, self._session_payload(
                        record,
                        spec,
                        status_reason="Okno logowania nadal jest otwarte. Zamknij je po zakonczeniu logowania i kliknij Potwierdz zapis.",
                    )
                try:
                    await self._save_storage_state_from_profile_dir(
                        profile_dir=pending.profile_dir,
                        destination=record.storage_state_path,
                    )
                except RuntimeError as exc:
                    return False, self._session_payload(
                        record,
                        spec,
                        status_reason=str(exc),
                    )
                except Exception:
                    logger.exception("Failed to save storage state from external login profile | service=%s", service)
                    return False, self._session_payload(
                        record,
                        spec,
                        status_reason="Nie udalo sie zapisac sesji z profilu browserowego po recznym logowaniu.",
                    )
                finally:
                    await self._close_pending_manual_login(service)
            else:
                try:
                    if pending.context is None:
                        raise RuntimeError("Brak aktywnego kontekstu logowania do zapisania sesji.")
                    await pending.context.storage_state(path=record.storage_state_path.as_posix())
                except Exception:
                    return False, self._session_payload(
                        record,
                        spec,
                        status_reason="Nie udalo sie zapisac sesji z otwartego okna logowania. Upewnij sie, ze okno nadal jest otwarte i sprobuj ponownie.",
                    )
                finally:
                    await self._close_pending_manual_login(service)

        if not record.storage_state_path.exists():
            record.pending_manual_login = False
            return False, self._session_payload(
                record,
                spec,
                status_reason="Brak zapisanej sesji. Otworz logowanie i potwierdz je ponownie.",
            )

        try:
            loaded = await self._load_context(record)
        except RuntimeError as exc:
            record.pending_manual_login = True
            return False, self._session_payload(
                record,
                spec,
                status_reason=str(exc),
            )
        if not loaded:
            record.pending_manual_login = True
            return False, self._session_payload(
                record,
                spec,
                status_reason="Nie udalo sie przeladowac zapisanej sesji browserowej.",
            )

        is_valid = await self.is_session_valid(service)
        if not is_valid:
            if record.context is not None:
                await record.context.close()
                record.context = None
            record.expired = True
            record.pending_manual_login = True
            return False, self._session_payload(
                record,
                spec,
                status_reason="Sesja nie przeszla walidacji. Dokoncz logowanie w oknie browsera i potwierdz ponownie.",
            )

        await self._log_event(service, JarvisEvent.MANUAL_LOGIN_SAVED, {"path": record.storage_state_path.as_posix()})
        await self.event_bus.publish(
            JarvisEvent.MANUAL_LOGIN_SAVED,
            {"service": service, "path": record.storage_state_path.as_posix()},
        )
        return True, self.describe_session(service)

    async def reload_context(self, service: str) -> bool:
        record = self._records.get(service)
        if record is None:
            return False
        return await self._load_context(record)

    async def mark_session_expired(self, service: str) -> None:
        record = self._records.get(service)
        if record is None:
            return
        record.expired = True
        record.pending_manual_login = True
        await self._log_event(service, JarvisEvent.SESSION_EXPIRED, {"service": service})

    def status(self) -> dict[str, Any]:
        return {
            service: {
                "storage_state_path": record.storage_state_path.as_posix(),
                "ready": record.context is not None and not record.expired,
                "expired": record.expired,
                "pending_manual_login": record.pending_manual_login,
                "has_storage_state": record.storage_state_path.exists(),
                "login_window_open": service in self._pending_manual_logins,
                "semaphore_free": getattr(record.semaphore, "_value", None),
            }
            for service, record in self._records.items()
        }

    def describe_sessions(self) -> list[dict[str, Any]]:
        return [self.describe_session(service) for service in self._service_specs]

    def describe_session(self, service: str) -> dict[str, Any]:
        spec = self._require_spec(service)
        record = self._ensure_record(service)
        return self._session_payload(record, spec)

    async def shutdown(self) -> None:
        for service in tuple(self._pending_manual_logins.keys()):
            await self._close_pending_manual_login(service)
        for record in self._records.values():
            if record.context is not None:
                try:
                    await record.context.close()
                except Exception:
                    logger.exception("Failed to close browser context | service=%s", record.service)
                record.context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.exception("Failed to close browser.")
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.exception("Failed to stop Playwright.")
            self._playwright = None

    async def _ensure_browser(self) -> None:
        if self._browser is not None and self._playwright is not None:
            return

        browser_state = self.browser_runtime.describe()
        if not browser_state.ready:
            raise RuntimeError(browser_state.status_reason)
        self._playwright = await self._start_playwright()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            executable_path=browser_state.browser_executable.as_posix() if browser_state.browser_executable else None,
        )

    async def _load_context(self, record: SessionRecord) -> bool:
        await self._ensure_browser()
        if not record.storage_state_path.exists():
            return False
        if record.context is not None:
            await record.context.close()
        record.context = await self._browser.new_context(storage_state=record.storage_state_path.as_posix())
        record.expired = False
        record.pending_manual_login = False
        await self._log_event(record.service, JarvisEvent.SESSION_RESTORED, {"path": record.storage_state_path.as_posix()})
        await self.event_bus.publish(JarvisEvent.SESSION_RESTORED, {"service": record.service, "path": record.storage_state_path.as_posix()})
        return True

    async def _refresh_from_manual_login(self, service: str) -> None:
        await self.confirm_manual_login(service)

    def _storage_state_path(self, service: str) -> Path:
        return (self.config_store.paths.browser_sessions_dir / f"{service}.json").resolve()

    def _manual_login_profile_dir(self, service: str, attempt_id: str) -> Path:
        path = (self.config_store.paths.root_dir / "manual_login_profiles" / service / attempt_id).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _build_record(self, service: str) -> SessionRecord:
        return SessionRecord(
            service=service,
            storage_state_path=self._storage_state_path(service),
            semaphore=asyncio.Semaphore(self.config_store.current().sessions.page_limit_per_service),
        )

    def _ensure_record(self, service: str) -> SessionRecord:
        normalized = self._normalize_service_key(service)
        if normalized not in self._service_specs:
            raise ValueError(f"Unsupported browser session service '{service}'.")
        record = self._records.get(normalized)
        if record is None:
            record = self._build_record(normalized)
            self._records[normalized] = record
        return record

    def _require_spec(self, service: str) -> ServiceSessionSpec:
        normalized = self._normalize_service_key(service)
        spec = self._service_specs.get(normalized)
        if spec is None:
            raise ValueError(f"Unsupported browser session service '{service}'.")
        return spec

    def _normalize_service_key(self, service: str) -> str:
        normalized = service.strip().lower().replace(".", "-")
        normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized).strip("-_")
        if not normalized or not self.SERVICE_KEY_PATTERN.match(normalized):
            raise ValueError(f"Invalid browser session service '{service}'.")
        return normalized

    def _normalize_http_url(self, value: str, *, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} is required.")
        if "://" not in normalized:
            normalized = "https://" + normalized
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{field_name} must be an http(s) URL.")
        return normalized

    def _origin_from_url(self, value: str) -> str:
        parsed = urlparse(value)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def _should_prefer_external_login(self, login_url: str) -> bool:
        host = urlparse(login_url).netloc.lower()
        return any(fragment in host for fragment in ("google.", "youtube.", "gmail.", "accounts.google."))

    @property
    def _custom_specs_path(self) -> Path:
        return (self.config_store.paths.root_dir / "browser_session_specs.json").resolve()

    def _load_custom_specs(self) -> None:
        path = self._custom_specs_path
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text("utf-8"))
        except Exception:
            logger.warning("Could not read custom browser session specs.", exc_info=True)
            return
        if not isinstance(payload, list):
            return
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                self.register_custom_service(
                    service=str(item.get("service") or ""),
                    display_name=str(item.get("display_name") or ""),
                    login_url=str(item.get("login_url") or ""),
                    validation_url=str(item.get("validation_url") or ""),
                    persist=False,
                )
            except ValueError:
                logger.warning("Skipping invalid custom browser session spec: %s", item)

    def _persist_custom_specs(self) -> None:
        payload = [
            {
                "service": spec.service,
                "display_name": spec.display_name,
                "login_url": spec.login_url,
                "validation_url": spec.validation_url,
            }
            for spec in self._custom_specs.values()
        ]
        self._custom_specs_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _session_payload(
        self,
        record: SessionRecord,
        spec: ServiceSessionSpec,
        *,
        status_reason: str | None = None,
    ) -> dict[str, Any]:
        ready = record.context is not None and not record.expired
        has_storage_state = record.storage_state_path.exists()
        login_window_open = record.service in self._pending_manual_logins
        pending = self._pending_manual_logins.get(record.service)
        if status_reason is None:
            if ready:
                status_reason = "Gotowa zapisana sesja browserowa."
            elif login_window_open:
                status_reason = (
                    "Okno logowania jest otwarte. Dokoncz logowanie i kliknij Potwierdz zapis."
                    if pending is None or pending.mode == "playwright"
                    else "Okno logowania jest otwarte. Po zakonczeniu logowania zamknij je i kliknij Potwierdz zapis."
                )
            elif record.expired:
                status_reason = "Sesja wygasla i wymaga ponownego logowania."
            elif has_storage_state:
                status_reason = "Zapisany stan sesji istnieje, ale nie jest jeszcze aktywny w runtime."
            else:
                status_reason = "Brak zapisanej sesji browserowej."
        return {
            "service": record.service,
            "display_name": spec.display_name,
            "login_url": spec.login_url,
            "validation_url": spec.validation_url,
            "storage_state_path": record.storage_state_path.as_posix(),
            "ready": ready,
            "expired": record.expired,
            "pending_manual_login": record.pending_manual_login,
            "has_storage_state": has_storage_state,
            "login_window_open": login_window_open,
            "status_reason": status_reason,
            "semaphore_free": getattr(record.semaphore, "_value", None),
        }

    async def _start_playwright(self):
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Brakuje pakietu Python Playwright w runtime Atnosa. Doinstaluj zaleznosc backendu i sprobuj ponownie."
            ) from exc

        return await async_playwright().start()

    async def _launch_manual_login_window(
        self,
        spec: ServiceSessionSpec,
        browser_state,
    ) -> PendingManualLoginWindow:
        attempt_id = uuid4().hex
        profile_dir = self._manual_login_profile_dir(spec.service, attempt_id)
        if spec.prefer_external_login:
            return self._launch_manual_login_external_browser(spec, browser_state, attempt_id, profile_dir)
        try:
            playwright = await self._start_playwright()
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir.as_posix(),
                headless=False,
                executable_path=browser_state.browser_executable.as_posix() if browser_state.browser_executable else None,
            )
            page = context.pages[0] if getattr(context, "pages", None) else None
            if page is None:
                page = await context.new_page()
            await page.goto(spec.login_url, wait_until="domcontentloaded")
            return PendingManualLoginWindow(
                service=spec.service,
                playwright=playwright,
                context=context,
                page=page,
                profile_dir=profile_dir,
                attempt_id=attempt_id,
                mode="playwright",
            )
        except Exception:
            logger.warning(
                "Playwright headful manual login failed; falling back to external browser process | service=%s",
                spec.service,
                exc_info=True,
            )
            return self._launch_manual_login_external_browser(spec, browser_state, attempt_id, profile_dir)

    def _launch_manual_login_external_browser(
        self,
        spec: ServiceSessionSpec,
        browser_state,
        attempt_id: str,
        profile_dir: Path,
    ) -> PendingManualLoginWindow:
        browser_executable = getattr(browser_state, "browser_executable", None)
        if browser_executable is None:
            raise RuntimeError("Brak lokalnej przegladarki do otwarcia recznego logowania.")
        args = [
            browser_executable.as_posix(),
            f"--user-data-dir={profile_dir.as_posix()}",
            "--no-first-run",
            "--disable-sync",
            "--new-window",
            spec.login_url,
        ]
        process = subprocess.Popen(  # noqa: S603
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return PendingManualLoginWindow(
            service=spec.service,
            playwright=None,
            context=None,
            page=None,
            profile_dir=profile_dir,
            attempt_id=attempt_id,
            process=process,
            mode="external_process",
        )

    async def _save_storage_state_from_profile_dir(self, *, profile_dir: Path, destination: Path) -> None:
        playwright = await self._start_playwright()
        context = None
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir.as_posix(),
                headless=True,
            )
            await context.storage_state(path=destination.as_posix())
        finally:
            if context is not None:
                await context.close()
            await playwright.stop()

    async def _close_pending_manual_login(self, service: str) -> None:
        pending = self._pending_manual_logins.pop(service, None)
        if pending is None:
            return
        if pending.context is not None:
            try:
                await pending.context.close()
            except Exception:
                logger.exception("Failed to close manual login context | service=%s", service)
        if pending.playwright is not None:
            try:
                await pending.playwright.stop()
            except Exception:
                logger.exception("Failed to stop manual login Playwright | service=%s", service)
        if pending.process is not None and pending.process.poll() is None:
            try:
                pending.process.terminate()
            except Exception:
                logger.exception("Failed to terminate manual login browser process | service=%s", service)
        try:
            shutil.rmtree(pending.profile_dir, ignore_errors=True)
        except Exception:
            logger.exception("Failed to cleanup manual login profile dir | service=%s", service)

    def _manual_login_error_message(self, exc: Exception) -> str:
        message = str(exc)
        lowered = message.lower()
        if "processsingleton" in lowered or "profile directory is already in use" in lowered:
            return (
                "Okno logowania nie moglo wystartowac, bo browser zlapal blokade profilu tymczasowego. "
                "Sprobuj ponownie; Atnos przygotuje nowa probe logowania."
            )
        if "browser executable" in lowered or "executable doesn't exist" in lowered:
            return "Runtime browsera nie jest kompletny. Sprawdz instalacje przegladarki lub Playwright browsers."
        return "Nie udalo sie otworzyc okna logowania browserowego. Sprawdz runtime Playwright i sprobuj ponownie."

    async def _log_event(self, service: str, event: JarvisEvent, payload: dict[str, Any]) -> None:
        line = json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": service,
                "event": str(event),
                "payload": payload,
            },
            ensure_ascii=False,
        )
        await asyncio.to_thread(self._append_log_line, line)

    def _append_log_line(self, line: str) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
