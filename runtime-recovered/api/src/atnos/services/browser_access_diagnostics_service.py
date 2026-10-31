from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import time
from typing import ClassVar, Literal

import httpx
import psutil
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.local_host_control_service import LocalHostControlService


ChromeAccessMode = Literal["devtools_live", "session_fallback", "window_titles_only", "unavailable"]

URL_PATTERN = re.compile(rb"https?://[^\x00-\x20\"'<>\\]+")
KNOWN_APP_MAP = {
    "chatgpt.com": "ChatGPT",
    "platform.openai.com": "OpenAI Platform",
    "claude.ai": "Claude",
    "console.anthropic.com": "Anthropic Console",
    "github.com": "GitHub",
    "instagram.com": "Instagram",
    "mail.google.com": "Gmail",
    "calendar.google.com": "Google Calendar",
    "drive.google.com": "Google Drive",
    "docs.google.com": "Google Docs",
    "sheets.google.com": "Google Sheets",
    "studio.youtube.com": "YouTube Studio",
    "youtube.com": "YouTube",
    "tiktok.com": "TikTok",
    "figma.com": "Figma",
    "lovable.dev": "Lovable",
    "vercel.com": "Vercel",
    "canva.com": "Canva",
    "slack.com": "Slack",
    "app.slack.com": "Slack",
    "hubspot.com": "HubSpot",
    "stripe.com": "Stripe",
    "airtable.com": "Airtable",
    "onlyfans.com": "OnlyFans",
    "metricool.com": "Metricool",
    "notion.so": "Notion",
}


@dataclass(frozen=True, slots=True)
class ChromeTabDiagnosticsView:
    title: str | None
    url: str
    source: Literal["devtools", "session"]

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ChromeBrowserAccessDiagnosticsView:
    browser_running: bool
    process_count: int
    mode: ChromeAccessMode
    summary: str
    reasons: tuple[str, ...]
    remote_debugging_port: int | None
    visible_windows: tuple[dict[str, object], ...]
    tabs: tuple[dict[str, object], ...]
    detected_apps: tuple[str, ...]
    session_source_file: str | None
    checked_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class BrowserAccessDiagnosticsService:
    CHROME_PROCESS_NAME = "chrome.exe"
    _CACHE_TTL_SECONDS: ClassVar[float] = 2.0
    _chrome_cache: ClassVar[dict[int, tuple[float, ChromeBrowserAccessDiagnosticsView]]] = {}

    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.local_host = LocalHostControlService(session, settings=settings)

    def diagnose_chrome(
        self,
        *,
        tab_limit: int = 20,
        use_cache: bool = True,
    ) -> ChromeBrowserAccessDiagnosticsView:
        normalized_tab_limit = max(1, int(tab_limit))
        if use_cache:
            cached = self._chrome_cache.get(normalized_tab_limit)
            if cached is not None:
                cached_at, cached_view = cached
                if time.monotonic() - cached_at <= self._CACHE_TTL_SECONDS:
                    return cached_view

        view = self._diagnose_chrome_uncached(tab_limit=normalized_tab_limit)
        if use_cache:
            self._chrome_cache[normalized_tab_limit] = (time.monotonic(), view)
        return view

    @classmethod
    def clear_cache(cls) -> None:
        cls._chrome_cache.clear()

    def _diagnose_chrome_uncached(self, *, tab_limit: int) -> ChromeBrowserAccessDiagnosticsView:
        chrome_processes = self._list_chrome_processes()
        visible_windows = self.local_host.find_windows(process_name=self.CHROME_PROCESS_NAME)
        remote_debugging_port = self._detect_remote_debugging_port(chrome_processes)

        if remote_debugging_port is not None:
            tabs = self._read_devtools_tabs(remote_debugging_port, tab_limit=tab_limit)
            if tabs:
                detected_apps = self._detect_apps([item.url for item in tabs])
                return ChromeBrowserAccessDiagnosticsView(
                    browser_running=bool(chrome_processes),
                    process_count=len(chrome_processes),
                    mode="devtools_live",
                    summary="Atnos ma live odczyt kart przez Chrome DevTools.",
                    reasons=("Chrome jest uruchomiony z --remote-debugging-port.",),
                    remote_debugging_port=remote_debugging_port,
                    visible_windows=tuple(item.to_dict() for item in visible_windows),
                    tabs=tuple(item.to_dict() for item in tabs),
                    detected_apps=detected_apps,
                    session_source_file=None,
                    checked_at=self._now().isoformat(),
                )

        session_urls, session_source_file = self._read_session_urls(limit=tab_limit)
        if session_urls:
            tabs = tuple(
                ChromeTabDiagnosticsView(title=None, url=url, source="session") for url in session_urls
            )
            detected_apps = self._detect_apps(session_urls)
            reasons = [
                "Chrome nie wystawia live DevTools dla biezacej sesji.",
                "Atnos czyta ostatni dostepny snapshot sesji z lokalnych plikow Chrome.",
            ]
            if visible_windows:
                reasons.append("Widoczne okna Chrome potwierdzaja, ze przegladarka jest uruchomiona.")
            return ChromeBrowserAccessDiagnosticsView(
                browser_running=bool(chrome_processes),
                process_count=len(chrome_processes),
                mode="session_fallback",
                summary="Atnos widzi tylko ostatni dostepny snapshot sesji Chrome, nie pelne live taby.",
                reasons=tuple(reasons),
                remote_debugging_port=remote_debugging_port,
                visible_windows=tuple(item.to_dict() for item in visible_windows),
                tabs=tuple(item.to_dict() for item in tabs),
                detected_apps=detected_apps,
                session_source_file=str(session_source_file) if session_source_file is not None else None,
                checked_at=self._now().isoformat(),
            )

        if visible_windows:
            return ChromeBrowserAccessDiagnosticsView(
                browser_running=True,
                process_count=len(chrome_processes),
                mode="window_titles_only",
                summary="Atnos widzi okna Chrome, ale nie ma kanalu do odczytu URL-i kart.",
                reasons=(
                    "Brak aktywnego Chrome DevTools bridge.",
                    "Pliki Sessions nie daly czytelnego snapshotu URL-i.",
                ),
                remote_debugging_port=remote_debugging_port,
                visible_windows=tuple(item.to_dict() for item in visible_windows),
                tabs=(),
                detected_apps=(),
                session_source_file=None,
                checked_at=self._now().isoformat(),
            )

        return ChromeBrowserAccessDiagnosticsView(
            browser_running=False,
            process_count=0,
            mode="unavailable",
            summary="Atnos nie widzi dzialajacego Chrome ani czytelnego snapshotu sesji.",
            reasons=(
                "Proces Chrome nie jest teraz widoczny dla local host bridge.",
                "Brak live DevTools i brak czytelnego fallbacku sesji.",
            ),
            remote_debugging_port=remote_debugging_port,
            visible_windows=(),
            tabs=(),
            detected_apps=(),
            session_source_file=None,
            checked_at=self._now().isoformat(),
        )

    def _list_chrome_processes(self) -> list[psutil.Process]:
        processes: list[psutil.Process] = []
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                if str(process.info.get("name") or "").lower() == self.CHROME_PROCESS_NAME:
                    processes.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return processes

    @staticmethod
    def _detect_remote_debugging_port(processes: list[psutil.Process]) -> int | None:
        for process in processes:
            try:
                cmdline = process.cmdline()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            for index, item in enumerate(cmdline):
                text = str(item).strip()
                if text.startswith("--remote-debugging-port="):
                    raw_port = text.split("=", maxsplit=1)[1]
                    if raw_port.isdigit():
                        return int(raw_port)
                if text == "--remote-debugging-port" and index + 1 < len(cmdline):
                    next_item = str(cmdline[index + 1]).strip()
                    if next_item.isdigit():
                        return int(next_item)
        return None

    def _read_devtools_tabs(
        self,
        remote_debugging_port: int,
        *,
        tab_limit: int,
    ) -> tuple[ChromeTabDiagnosticsView, ...]:
        endpoint = f"http://127.0.0.1:{remote_debugging_port}/json/list"
        try:
            with httpx.Client(timeout=2) as client:
                response = client.get(endpoint)
        except httpx.HTTPError:
            return ()
        if response.status_code >= 400:
            return ()
        try:
            payload = response.json()
        except ValueError:
            return ()
        if not isinstance(payload, list):
            return ()
        items: list[ChromeTabDiagnosticsView] = []
        for raw_item in payload:
            if not isinstance(raw_item, dict):
                continue
            if raw_item.get("type") != "page":
                continue
            url = raw_item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            title = raw_item.get("title") if isinstance(raw_item.get("title"), str) else None
            items.append(ChromeTabDiagnosticsView(title=title, url=url.strip(), source="devtools"))
            if len(items) >= tab_limit:
                break
        return tuple(items)

    def _read_session_urls(self, *, limit: int) -> tuple[tuple[str, ...], Path | None]:
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            return (), None
        sessions_dir = Path(local_app_data) / "Google" / "Chrome" / "User Data" / "Default" / "Sessions"
        if not sessions_dir.exists():
            return (), None

        candidates = sorted(
            sessions_dir.glob("Session_*"),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        ) + sorted(
            sessions_dir.glob("Tabs_*"),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
        for path in candidates:
            try:
                content = path.read_bytes()
            except OSError:
                continue
            urls = self._extract_urls_from_session_blob(content, limit=limit)
            if urls:
                return urls, path
        return (), None

    @staticmethod
    def _extract_urls_from_session_blob(content: bytes, *, limit: int) -> tuple[str, ...]:
        seen: set[str] = set()
        urls: list[str] = []
        for match in URL_PATTERN.finditer(content):
            try:
                url = match.group(0).decode("utf-8", errors="ignore").strip()
            except Exception:
                continue
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                break
        return tuple(urls)

    @staticmethod
    def _detect_apps(urls: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        found: list[str] = []
        seen: set[str] = set()
        for url in urls:
            lowered = url.lower()
            for needle, label in KNOWN_APP_MAP.items():
                if needle in lowered and label not in seen:
                    found.append(label)
                    seen.add(label)
        return tuple(found)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
