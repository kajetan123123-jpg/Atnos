from __future__ import annotations

import asyncio
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.core.event_bus import EventBus
from atnos.jarvis.types import JarvisEvent, PageLease, ToolResult
from atnos.jarvis.workers.browser.session_manager import (
    BrowserSessionManager,
    PendingManualLoginWindow,
    SessionRecord,
)

from .test_support import build_test_settings, make_temp_dir


class _FakePage:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[_FakePage] = []
        self.closed = False

    async def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class _FakePersistentPage:
    def __init__(self) -> None:
        self.goto_calls: list[tuple[str, str]] = []

    async def goto(self, url: str, *, wait_until: str) -> None:
        self.goto_calls.append((url, wait_until))


class _FakePersistentContext:
    def __init__(self) -> None:
        self.page = _FakePersistentPage()
        self.pages = [self.page]
        self.closed = False
        self.storage_state_paths: list[str] = []

    async def new_page(self) -> _FakePersistentPage:
        return self.page

    async def storage_state(self, *, path: str) -> None:
        self.storage_state_paths.append(path)
        Path(path).write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    async def close(self) -> None:
        self.closed = True


class _FakePersistentPlaywright:
    def __init__(self, context: _FakePersistentContext) -> None:
        self.context = context
        self.stopped = False
        self.chromium = SimpleNamespace(launch_persistent_context=self._launch_persistent_context)

    async def _launch_persistent_context(self, **_: object) -> _FakePersistentContext:
        return self.context

    async def stop(self) -> None:
        self.stopped = True


class TestBrowserSessionManager:
    def test_get_page_returns_warm_page_quickly(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            manager = BrowserSessionManager(settings, store, EventBus())
            context = _FakeContext()
            manager._records["claude"] = SessionRecord(  # noqa: SLF001
                service="claude",
                storage_state_path=store.paths.browser_sessions_dir / "claude.json",
                context=context,
                semaphore=asyncio.Semaphore(5),
            )

            async def scenario() -> PageLease | ToolResult:
                started = perf_counter()
                result = await manager.get_page("claude", "task-1")
                elapsed_ms = (perf_counter() - started) * 1000
                assert elapsed_ms < 50
                return result

            lease = asyncio.run(scenario())
            assert isinstance(lease, PageLease)
            assert lease.service == "claude"
            asyncio.run(manager.release_page(lease))

    def test_missing_session_returns_awaiting_auth_and_emits_event(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            bus = EventBus()
            manager = BrowserSessionManager(settings, store, bus)
            received: list[dict[str, str]] = []

            async def scenario() -> ToolResult:
                async def handler(payload) -> None:
                    received.append(payload)

                await bus.subscribe(JarvisEvent.NEEDS_MANUAL_LOGIN, handler)
                result = await manager.get_page("veed", "task-2")
                return result

            result = asyncio.run(scenario())
            assert isinstance(result, ToolResult)
            assert result.success is False
            assert result.reason == "awaiting_auth"
            assert received == [{"service": "veed", "task_id": "task-2"}]

    def test_page_limit_returns_queued_result(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            manager = BrowserSessionManager(settings, store, EventBus())
            manager._records["ideogram"] = SessionRecord(  # noqa: SLF001
                service="ideogram",
                storage_state_path=store.paths.browser_sessions_dir / "ideogram.json",
                context=_FakeContext(),
                semaphore=asyncio.Semaphore(0),
            )

            result = asyncio.run(manager.get_page("ideogram", "task-3"))
            assert isinstance(result, ToolResult)
            assert result.success is False
            assert result.queued is True
            assert result.retry_after_s == 3

    def test_manual_login_start_opens_visible_login_context_and_marks_pending(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            manager = BrowserSessionManager(settings, store, EventBus())
            persistent_context = _FakePersistentContext()
            persistent_playwright = _FakePersistentPlaywright(persistent_context)
            manager._start_playwright = AsyncMock(return_value=persistent_playwright)  # type: ignore[method-assign]
            manager.browser_runtime.describe = lambda: SimpleNamespace(  # type: ignore[method-assign]
                ready=True,
                browser_executable=None,
                status_reason="ready",
            )

            ok, session = asyncio.run(manager.start_manual_login("ideogram"))

            assert ok is True
            assert session["service"] == "ideogram"
            assert session["display_name"] == "Ideogram"
            assert session["pending_manual_login"] is True
            assert session["login_window_open"] is True
            assert persistent_context.page.goto_calls == [("https://ideogram.ai/login", "domcontentloaded")]

    def test_manual_login_for_url_registers_custom_service_and_opens_login(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            manager = BrowserSessionManager(settings, store, EventBus())
            persistent_context = _FakePersistentContext()
            persistent_playwright = _FakePersistentPlaywright(persistent_context)
            manager._start_playwright = AsyncMock(return_value=persistent_playwright)  # type: ignore[method-assign]
            manager.browser_runtime.describe = lambda: SimpleNamespace(  # type: ignore[method-assign]
                ready=True,
                browser_executable=None,
                status_reason="ready",
            )

            ok, session = asyncio.run(
                manager.start_manual_login_for_url(
                    service="Custom Clips AI",
                    display_name="Custom Clips AI",
                    login_url="customclips.example/login",
                    validation_url="https://customclips.example/app",
                )
            )

            assert ok is True
            assert session["service"] == "custom-clips-ai"
            assert session["display_name"] == "Custom Clips AI"
            assert session["login_url"] == "https://customclips.example/login"
            assert session["validation_url"] == "https://customclips.example/app"
            assert session["pending_manual_login"] is True
            assert persistent_context.page.goto_calls == [("https://customclips.example/login", "domcontentloaded")]
            assert "custom-clips-ai" in manager.known_services()
            assert store.paths.root_dir.joinpath("browser_session_specs.json").exists()

    def test_manual_login_start_falls_back_to_external_browser_when_playwright_is_unavailable(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            manager = BrowserSessionManager(settings, store, EventBus())
            attempt_dir = store.paths.root_dir / "manual_login_profiles" / "ideogram" / "attempt-1"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            manager.browser_runtime.describe = lambda: SimpleNamespace(  # type: ignore[method-assign]
                ready=True,
                browser_executable=Path(temp_dir) / "chrome.exe",
                status_reason="ready",
            )
            manager._start_playwright = AsyncMock(side_effect=RuntimeError(  # type: ignore[method-assign]
                "Brakuje pakietu Python Playwright w runtime Atnosa. Doinstaluj zaleznosc backendu i sprobuj ponownie."
            ))
            manager._launch_manual_login_external_browser = lambda spec, browser_state, attempt_id, profile_dir: PendingManualLoginWindow(  # type: ignore[method-assign]
                service=spec.service,
                playwright=None,
                context=None,
                page=None,
                profile_dir=attempt_dir,
                attempt_id="attempt-1",
                process=None,
                mode="external_process",
            )

            ok, session = asyncio.run(manager.start_manual_login("ideogram"))

            assert ok is True
            assert session["ready"] is False
            assert session["login_window_open"] is True
            assert "zamknij je i kliknij Potwierdz zapis" in str(session["status_reason"])

    def test_manual_login_confirm_saves_storage_state_and_activates_session(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            manager = BrowserSessionManager(settings, store, EventBus())
            persistent_context = _FakePersistentContext()
            persistent_playwright = _FakePersistentPlaywright(persistent_context)
            manager._start_playwright = AsyncMock(return_value=persistent_playwright)  # type: ignore[method-assign]
            manager.browser_runtime.describe = lambda: SimpleNamespace(  # type: ignore[method-assign]
                ready=True,
                browser_executable=None,
                status_reason="ready",
            )
            manager.is_session_valid = AsyncMock(return_value=True)  # type: ignore[method-assign]

            async def fake_load_context(record: SessionRecord) -> bool:
                record.context = _FakeContext()
                record.expired = False
                record.pending_manual_login = False
                return True

            manager._load_context = AsyncMock(side_effect=fake_load_context)  # type: ignore[method-assign]

            async def scenario() -> tuple[bool, dict[str, object]]:
                await manager.start_manual_login("ideogram")
                return await manager.confirm_manual_login("ideogram")

            ok, session = asyncio.run(scenario())

            assert ok is True
            assert session["ready"] is True
            assert session["pending_manual_login"] is False
            assert Path(str(session["storage_state_path"])).exists()
            assert persistent_context.closed is True
            assert persistent_playwright.stopped is True

    def test_manual_login_confirm_reports_validation_failure(self) -> None:
        with make_temp_dir() as temp_dir:
            settings = build_test_settings(Path(temp_dir))
            store = JarvisConfigStore(settings)
            manager = BrowserSessionManager(settings, store, EventBus())
            persistent_context = _FakePersistentContext()
            persistent_playwright = _FakePersistentPlaywright(persistent_context)
            manager._start_playwright = AsyncMock(return_value=persistent_playwright)  # type: ignore[method-assign]
            manager.browser_runtime.describe = lambda: SimpleNamespace(  # type: ignore[method-assign]
                ready=True,
                browser_executable=None,
                status_reason="ready",
            )
            manager._load_context = AsyncMock(return_value=True)  # type: ignore[method-assign]
            manager.is_session_valid = AsyncMock(return_value=False)  # type: ignore[method-assign]
            manager._records["ideogram"].context = _FakeContext()  # noqa: SLF001

            async def scenario() -> tuple[bool, dict[str, object]]:
                await manager.start_manual_login("ideogram")
                return await manager.confirm_manual_login("ideogram")

            ok, session = asyncio.run(scenario())

            assert ok is False
            assert session["ready"] is False
            assert session["pending_manual_login"] is True
            assert "Sesja nie przeszla walidacji" in str(session["status_reason"])
