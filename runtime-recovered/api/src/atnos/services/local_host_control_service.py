from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import ctypes
import difflib
import getpass
import glob
import json
import os
from pathlib import Path
import psutil
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from atnos.core.config import Settings
from atnos.core.logging import append_redacted_jsonl
from atnos.services.browser_recipe_runner_service import BrowserRecipeRunnerService
from atnos.services.runtime_settings_service import RuntimeSettingsService
from sqlalchemy.orm import Session


class LocalHostControlError(RuntimeError):
    pass


def desktop_aliases_path(settings: Settings | None = None) -> Path:
    if settings is not None and getattr(settings, "data_dir", None) is not None:
        return Path(settings.data_dir).resolve() / "desktop-aliases.json"
    return Path("E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data") / "desktop-aliases.json"


def load_persisted_desktop_aliases(settings: Settings | None = None) -> dict[str, str]:
    """Learned query → app_key map (mirrors site-aliases.json for WWW)."""
    path = desktop_aliases_path(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    aliases: dict[str, str] = {}
    for key, value in raw.items():
        name = " ".join(str(key or "").casefold().split()).strip()
        app_key = str(value or "").strip()
        if name and app_key and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", app_key):
            aliases[name] = app_key
    return aliases


def persist_desktop_alias_file(
    *,
    query: str,
    app_key: str,
    settings: Settings | None = None,
) -> Path | None:
    cleaned_query = " ".join(str(query or "").split()).strip()
    cleaned_key = str(app_key or "").strip()
    if not cleaned_query or not cleaned_key:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", cleaned_key):
        return None
    path = desktop_aliases_path(settings)
    aliases = load_persisted_desktop_aliases(settings)
    key = cleaned_query.casefold()
    if aliases.get(key) == cleaned_key:
        return path if path.exists() else None
    aliases[key] = cleaned_key
    compact = re.sub(r"\s+", " ", key)
    aliases[compact] = cleaned_key
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(sorted(aliases.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path
    except OSError:
        return None


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class _INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
    )


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (
        ("type", ctypes.c_ulong),
        ("u", _INPUT_UNION),
    )


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


@dataclass(frozen=True, slots=True)
class LocalHostCapabilitySnapshotView:
    host_label: str
    platform: str
    current_user: str
    current_workdir: str
    powershell_available: bool
    browser_open_available: bool
    playwright_adapter_available: bool
    desktop_input_available: bool
    browser_recipe_available: bool
    elevated: bool
    execution_enabled: bool
    trusted_auto_enabled: bool
    command_log_path: str
    last_checked_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalHostCommandResultView:
    action_kind: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    workdir: str
    elevated: bool
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalHostBrowserRecipeResultView:
    action_kind: str
    ok: bool
    final_url: str
    results: list[dict[str, Any]]
    artifacts: dict[str, str]
    started_at: str
    finished_at: str
    runtime_fallback: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalHostWindowMatchView:
    hwnd: int
    title: str
    process_id: int
    process_name: str
    is_foreground: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalHostScreenshotCaptureView:
    screenshot_path: str
    window_title: str
    width: int
    height: int
    captured_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalDesktopAppView:
    app_key: str
    label: str
    category: str
    installed: bool
    running: bool
    window_visible: bool
    launch_supported: bool
    focus_supported: bool
    preferred_entry_path: str | None
    preferred_entry_kind: str | None
    running_processes: tuple[str, ...]
    visible_window_titles: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _KnownDesktopAppSpec:
    app_key: str
    label: str
    category: str
    process_names: tuple[str, ...]
    title_hints: tuple[str, ...]
    path_patterns: tuple[str, ...]
    shortcut_names: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    steam_app_id: str | None = None
    not_found_hint: str | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedDesktopAppSpec:
    app_key: str
    label: str
    category: str
    process_names: tuple[str, ...]
    title_hints: tuple[str, ...]
    resolved_entry_path: Path | None
    resolved_entry_kind: str | None
    running_processes: tuple[str, ...]
    visible_window_titles: tuple[str, ...]
    notes: tuple[str, ...]
    steam_app_id: str | None = None
    not_found_hint: str | None = None

    def to_view(self) -> LocalDesktopAppView:
        steam_launchable = bool(self.steam_app_id)
        installed = (
            self.resolved_entry_path is not None
            or bool(self.running_processes)
            or steam_launchable
        )
        running = bool(self.running_processes)
        window_visible = bool(self.visible_window_titles)
        return LocalDesktopAppView(
            app_key=self.app_key,
            label=self.label,
            category=self.category,
            installed=installed,
            running=running,
            window_visible=window_visible,
            launch_supported=self.resolved_entry_path is not None or steam_launchable,
            focus_supported=window_visible,
            preferred_entry_path=str(self.resolved_entry_path) if self.resolved_entry_path is not None else None,
            preferred_entry_kind=self.resolved_entry_kind or ("steam" if steam_launchable else None),
            running_processes=self.running_processes,
            visible_window_titles=self.visible_window_titles,
            notes=self.notes,
        )


class LocalHostControlService:
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    SW_SHOW = 5
    SW_RESTORE = 9
    UIA_TREE_SCOPE_DESCENDANTS = 0x4
    UIA_CONTROL_TYPE_PROPERTY_ID = 30003
    UIA_EDIT_CONTROL_TYPE_ID = 50004
    UIA_DOCUMENT_CONTROL_TYPE_ID = 50030
    UIA_VALUE_PATTERN_ID = 10002
    CSIDL_DESKTOPDIRECTORY = 0x10
    CSIDL_COMMON_DESKTOPDIRECTORY = 0x19
    CSIDL_PROGRAMS = 0x02
    CSIDL_COMMON_PROGRAMS = 0x17
    KNOWN_BROWSER_PROCESSES = ("opera.exe", "chrome.exe", "msedge.exe", "firefox.exe")
    # Free-form user queries → canonical app_key (PL/EN typos and nicknames).
    DESKTOP_APP_QUERY_ALIASES: dict[str, str] = {
        "comfort": "comfy-desktop",
        "comfi": "comfy-desktop",
        "comfy": "comfy-desktop",
        "comfyui": "comfy-desktop",
        "comfy ui": "comfy-desktop",
        "comfy desktop": "comfy-desktop",
        "comfui": "comfy-desktop",
        "komfy": "comfy-desktop",
        "code": "vscode",
        "vs code": "vscode",
        "vscode": "vscode",
        "visual studio code": "vscode",
        "visualcode": "vscode",
        "docker": "docker-desktop",
        "dockr": "docker-desktop",
        "github": "github-desktop",
        "gh desktop": "github-desktop",
        "git": "git-bash",
        "bash": "git-bash",
        "atnos": "atnos-desktop",
        "atnos app": "atnos-desktop",
        "cap cut": "capcut",
        "capkut": "capcut",
        "capcutt": "capcut",
        "obsidain": "obsidian",
        "obsydyan": "obsidian",
        "obsydian": "obsidian",
        "wallpaper engine": "wallpaper-engine",
        "wallpaperengine": "wallpaper-engine",
        "wallpaper": "wallpaper-engine",
        "wallpapper": "wallpaper-engine",
        "wall paper engine": "wallpaper-engine",
        "we engine": "wallpaper-engine",
        "tapeta engine": "wallpaper-engine",
        "tapety engine": "wallpaper-engine",
    }
    # Fuzzy auto-launch: catalog >= this; Start Menu uses clear-winner rules below.
    _CATALOG_AUTO_LAUNCH_MIN_SCORE = 80
    _FUZZY_STRONG_SCORE = 85
    _FUZZY_OK_SCORE = 82
    _FUZZY_MIN_QUERY_LEN = 4
    _FUZZY_STRONG_RATIO = 0.92
    _FUZZY_OK_RATIO = 0.86
    _CLEAR_WINNER_MARGIN = 8
    KNOWN_DESKTOP_APPS: tuple[_KnownDesktopAppSpec, ...] = (
        _KnownDesktopAppSpec(
            app_key="kimi",
            label="Kimi",
            category="ai",
            process_names=("kimi.exe", "kimi app.exe", "kimi-k2.exe"),
            title_hints=("kimi",),
            path_patterns=(
                r"%LOCALAPPDATA%\Programs\kimi-desktop\Kimi.exe",
                r"%LOCALAPPDATA%\Programs\Kimi\Kimi.exe",
                r"%LOCALAPPDATA%\Kimi\Kimi.exe",
                r"%ProgramFiles%\Kimi\Kimi.exe",
            ),
            shortcut_names=("Kimi.lnk",),
            aliases=("kimi app", "kimi desktop"),
        ),
        _KnownDesktopAppSpec(
            app_key="comfy-desktop",
            label="Comfy Desktop",
            category="ai-video",
            process_names=("comfy desktop.exe", "comfyui.exe"),
            title_hints=("comfy desktop", "comfyui", "comfy", "comfort"),
            path_patterns=(
                r"E:\Atnos\Tools\ComfyUI-Desktop-App\Comfy Desktop\Comfy Desktop.exe",
                r"%LOCALAPPDATA%\Programs\Comfy Desktop\Comfy Desktop.exe",
                r"%ProgramFiles%\Comfy Desktop\Comfy Desktop.exe",
            ),
            shortcut_names=("Comfy Desktop.lnk", "ComfyUI.lnk"),
            aliases=("comfort", "comfi", "comfy", "comfyui", "comfy ui", "comfy desktop"),
        ),
        _KnownDesktopAppSpec(
            app_key="atnos-desktop",
            label="ATNOS",
            category="atnos",
            process_names=("atnos desktop.exe", "atnos portable.exe", "atnos shell.exe", "atnos.exe"),
            title_hints=("atnos",),
            path_patterns=(
                r"E:\Atnos\App\Atnos Desktop.exe",
                r"E:\Atnos\App\Atnos Portable.exe",
                r"E:\Atnos\App\Atnos Shell.exe",
            ),
            shortcut_names=("ATNOS.lnk", "Atnos.lnk", "Atnos Desktop.lnk", "Atnos Aplikacja.lnk"),
            aliases=("atnos", "atnos app", "atnos desktop", "atnos shell"),
        ),
        _KnownDesktopAppSpec(
            app_key="capcut",
            label="CapCut",
            category="video",
            process_names=("capcut.exe",),
            title_hints=("capcut",),
            path_patterns=(
                r"%LOCALAPPDATA%\CapCut\Apps\CapCut.exe",
                r"%LOCALAPPDATA%\Programs\CapCut\CapCut.exe",
            ),
            shortcut_names=("CapCut.lnk",),
            aliases=("cap cut", "capcut", "capkut", "capcutt"),
        ),
        _KnownDesktopAppSpec(
            app_key="wallpaper-engine",
            label="Wallpaper Engine",
            category="desktop",
            process_names=(
                "wallpaper64.exe",
                "wallpaper32.exe",
                "wallpaper.exe",
                "wallpaper_engine.exe",
            ),
            title_hints=("wallpaper engine", "wallpaper"),
            path_patterns=(
                r"%ProgramFiles(x86)%\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe",
                r"%ProgramFiles%\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe",
                r"E:\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe",
                r"E:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper64.exe",
                r"D:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper64.exe",
                r"C:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper64.exe",
            ),
            shortcut_names=("Wallpaper Engine.lnk",),
            aliases=(
                "wallpaper engine",
                "wallpaperengine",
                "wallpaper",
                "wallpapper",
                "wall paper engine",
                "we engine",
                "tapeta engine",
                "tapety engine",
            ),
            steam_app_id="431960",
            not_found_hint=(
                "Wallpaper Engine to program Steam (AppID 431960). "
                "Uruchomię przez Steam albo lokalny exe, jeśli jest. "
                "Nie zastępuję go generowaniem tapet DALL-E/Midjourney."
            ),
        ),
        _KnownDesktopAppSpec(
            app_key="obsidian",
            label="Obsidian",
            category="knowledge",
            process_names=("obsidian.exe",),
            title_hints=("obsidian",),
            path_patterns=(
                r"%LOCALAPPDATA%\Obsidian\Obsidian.exe",
                r"%LOCALAPPDATA%\Programs\Obsidian\Obsidian.exe",
            ),
            shortcut_names=("Obsidian.lnk",),
            aliases=("obsydian", "obsydyan", "obsidain", "obsidian vault"),
        ),
        _KnownDesktopAppSpec(
            app_key="github-desktop",
            label="GitHub Desktop",
            category="developer",
            process_names=("githubdesktop.exe",),
            title_hints=("github desktop",),
            path_patterns=(
                r"%LOCALAPPDATA%\GitHubDesktop\GitHubDesktop.exe",
                r"%LOCALAPPDATA%\GitHubDesktop\app-*\GitHubDesktop.exe",
            ),
            shortcut_names=("GitHub Desktop.lnk",),
            aliases=("github", "gh desktop", "github desktop"),
        ),
        _KnownDesktopAppSpec(
            app_key="docker-desktop",
            label="Docker Desktop",
            category="developer",
            process_names=("docker desktop.exe", "com.docker.backend.exe"),
            title_hints=("docker desktop",),
            path_patterns=(
                r"E:\Atnos\Tools\Docker\Docker Desktop.exe",
                r"%ProgramFiles%\Docker\Docker\Docker Desktop.exe",
                r"%LOCALAPPDATA%\Programs\Docker Desktop\Docker Desktop.exe",
            ),
            shortcut_names=("Docker Desktop.lnk",),
            aliases=("docker", "docker desktop"),
        ),
        _KnownDesktopAppSpec(
            app_key="vscode",
            label="Visual Studio Code",
            category="developer",
            process_names=("code.exe",),
            title_hints=("visual studio code",),
            path_patterns=(
                r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
                r"%ProgramFiles%\Microsoft VS Code\Code.exe",
            ),
            shortcut_names=("Visual Studio Code.lnk", "VS Code.lnk"),
            aliases=("code", "vs code", "vscode", "visual studio code"),
        ),
        _KnownDesktopAppSpec(
            app_key="git-bash",
            label="Git Bash",
            category="developer",
            process_names=("git-bash.exe", "mintty.exe", "bash.exe"),
            title_hints=("git bash", "mingw64", "mingw32"),
            path_patterns=(
                r"E:\Atnos\Tools\Git\git-bash.exe",
                r"E:\Atnos\Tools\PortableGit\git-bash.exe",
                r"%ProgramFiles%\Git\git-bash.exe",
                r"%ProgramFiles(x86)%\Git\git-bash.exe",
            ),
            shortcut_names=("Git Bash.lnk",),
            aliases=("git", "bash", "git bash"),
        ),
    )
    VK_CODE_MAP = {
        "CTRL": 0x11,
        "CONTROL": 0x11,
        "SHIFT": 0x10,
        "ALT": 0x12,
        "WIN": 0x5B,
        "ENTER": 0x0D,
        "TAB": 0x09,
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "SPACE": 0x20,
        "BACKSPACE": 0x08,
        "DELETE": 0x2E,
        "UP": 0x26,
        "DOWN": 0x28,
        "LEFT": 0x25,
        "RIGHT": 0x27,
        "HOME": 0x24,
        "END": 0x23,
        "PAGEUP": 0x21,
        "PAGEDOWN": 0x22,
        "F1": 0x70,
        "F2": 0x71,
        "F3": 0x72,
        "F4": 0x73,
        "F5": 0x74,
        "F6": 0x75,
        "F7": 0x76,
        "F8": 0x77,
        "F9": 0x78,
        "F10": 0x79,
        "F11": 0x7A,
        "F12": 0x7B,
    }

    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.runtime_controls = RuntimeSettingsService(session, settings).snapshot()
        self.log_dir = self.settings.data_dir / "local-host-control"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.command_log_path = self.log_dir / "commands.log"

    def get_capability_snapshot(self) -> LocalHostCapabilitySnapshotView:
        powershell_available = shutil.which("powershell") is not None or shutil.which("pwsh") is not None
        browser_open_available = shutil.which("powershell") is not None
        playwright_adapter_available = (
            (self.settings.app_root / "node_modules" / "playwright").exists()
            or (self.settings.app_root / "runtime-recovered" / "api" / "node_modules" / "playwright").exists()
            or (Path.cwd() / "node_modules" / "playwright").exists()
        )
        desktop_input_available = os.name == "nt"
        return LocalHostCapabilitySnapshotView(
            host_label=socket.gethostname() or "windows-control-plane",
            platform=os.name,
            current_user=getpass.getuser(),
            current_workdir=str(self.settings.app_root),
            powershell_available=powershell_available,
            browser_open_available=browser_open_available,
            playwright_adapter_available=playwright_adapter_available,
            desktop_input_available=desktop_input_available,
            browser_recipe_available=playwright_adapter_available and self.runtime_controls.browser_control_enabled,
            elevated=self._is_elevated(),
            execution_enabled=not self.runtime_controls.emergency_stop_enabled and not self.runtime_controls.pause_all_jobs,
            trusted_auto_enabled=(
                self.runtime_controls.autonomy_mode == "trusted_auto"
                and self.runtime_controls.external_actions_enabled
                and not self.runtime_controls.emergency_stop_enabled
                and not self.runtime_controls.pause_all_jobs
            ),
            command_log_path=str(self.command_log_path),
            last_checked_at=self._now().isoformat(),
        )

    def run_powershell(
        self,
        command: str,
        *,
        timeout_s: int = 120,
        workdir: str | Path | None = None,
    ) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        command_text = command.strip()
        if not command_text:
            raise LocalHostControlError("command is required.")
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            raise LocalHostControlError("PowerShell is not available on this host.")
        resolved_workdir = Path(workdir or self.settings.app_root).resolve()
        started_at = self._now().isoformat()
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command_text],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, int(timeout_s)),
                check=False,
                cwd=str(resolved_workdir),
            )
            exit_code = int(result.returncode)
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = exc.stdout or ""
            stderr = exc.stderr or "command timed out"
        finished_at = self._now().isoformat()
        view = LocalHostCommandResultView(
            action_kind="powershell",
            command=command_text,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            workdir=str(resolved_workdir),
            elevated=self._is_elevated(),
            started_at=started_at,
            finished_at=finished_at,
        )
        self._append_log(
            {
                "action_kind": view.action_kind,
                "command": view.command,
                "exit_code": view.exit_code,
                "stdout": view.stdout,
                "stderr": view.stderr,
                "workdir": view.workdir,
                "elevated": view.elevated,
                "started_at": view.started_at,
                "finished_at": view.finished_at,
            }
        )
        return view

    def run_public_powershell(
        self,
        command: str,
        *,
        timeout_s: int = 120,
        workdir: str | Path | None = None,
    ) -> LocalHostCommandResultView:
        """Uruchamia jawnie wlaczony tryb developerski w dozwolonym katalogu."""

        if self.settings.app_env == "production":
            raise LocalHostControlError("Raw PowerShell is disabled in production.")
        if not self.settings.local_host_raw_powershell_enabled:
            raise LocalHostControlError(
                "Raw PowerShell is disabled. Use a typed local-host operation instead."
            )
        resolved_workdir = self._resolve_public_workdir(workdir)
        return self.run_powershell(
            command,
            timeout_s=timeout_s,
            workdir=resolved_workdir,
        )

    def open_browser_url(self, url: str) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"}:
            raise LocalHostControlError("Only http/https URLs are supported.")
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            raise LocalHostControlError("PowerShell is not available on this host.")
        safe_url = url.replace("'", "''")
        started_at = self._now().isoformat()
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", f"Start-Process '{safe_url}'"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            cwd=str(self.settings.app_root),
        )
        finished_at = self._now().isoformat()
        view = LocalHostCommandResultView(
            action_kind="browser_open",
            command=f"Start-Process '{url}'",
            exit_code=int(result.returncode),
            stdout=result.stdout,
            stderr=result.stderr,
            workdir=str(self.settings.app_root),
            elevated=self._is_elevated(),
            started_at=started_at,
            finished_at=finished_at,
        )
        self._append_log(view.to_dict())
        return view

    def list_desktop_apps(self) -> list[LocalDesktopAppView]:
        return [item.to_view() for item in self._build_desktop_app_catalog()]

    def _build_desktop_app_catalog(self) -> list[_ResolvedDesktopAppSpec]:
        process_map = self._list_processes_by_name()
        desktop_windows = self.find_windows()
        views: list[_ResolvedDesktopAppSpec] = []
        for spec in self.KNOWN_DESKTOP_APPS:
            resolved_entry_path, resolved_entry_kind = self._resolve_app_entry(spec)
            matched_process_names = tuple(
                sorted(
                    {
                        process_name
                        for process_name in process_map
                        if process_name.lower() in spec.process_names
                    }
                )
            )
            matched_windows = tuple(
                window.title
                for window in desktop_windows
                if self._window_matches_app(window, spec)
            )
            notes: list[str] = []
            if resolved_entry_path is not None and resolved_entry_kind is not None:
                notes.append(f"entry={resolved_entry_kind}")
            elif spec.steam_app_id:
                notes.append(f"steam_app_id={spec.steam_app_id}")
            elif matched_process_names:
                notes.append("running_without_resolved_entry")
            else:
                notes.append("entry_not_found")
            if matched_windows:
                notes.append("window_visible")
            views.append(
                _ResolvedDesktopAppSpec(
                    app_key=spec.app_key,
                    label=spec.label,
                    category=spec.category,
                    process_names=spec.process_names,
                    title_hints=spec.title_hints,
                    resolved_entry_path=resolved_entry_path,
                    resolved_entry_kind=resolved_entry_kind,
                    running_processes=matched_process_names,
                    visible_window_titles=matched_windows,
                    notes=tuple(notes),
                    steam_app_id=spec.steam_app_id,
                    not_found_hint=spec.not_found_hint,
                )
            )
        views.extend(
            self._discover_shortcut_apps(
                process_map=process_map,
                desktop_windows=desktop_windows,
                reserved_app_keys={item.app_key for item in views},
                reserved_labels={item.label.casefold() for item in views},
                reserved_entry_paths={
                    str(item.resolved_entry_path).casefold()
                    for item in views
                    if item.resolved_entry_path is not None
                },
            )
        )
        return views

    def open_desktop_app(self, app_key: str) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        app_spec = self._get_desktop_app_runtime_spec(app_key)
        if app_spec is None:
            raise LocalHostControlError(f"Unknown desktop app '{app_key}'.")
        app_view = app_spec.to_view()
        if app_view.window_visible:
            return self.focus_desktop_app(app_key)
        entry_path = app_spec.resolved_entry_path
        entry_kind = app_spec.resolved_entry_kind
        if (entry_path is None or entry_kind is None) and app_spec.steam_app_id:
            return self._launch_steam_app(
                steam_app_id=app_spec.steam_app_id,
                label=app_spec.label,
                app_key=app_spec.app_key,
            )
        if entry_path is None or entry_kind is None:
            raise LocalHostControlError(
                f"Desktop app '{app_spec.label}' is not installed or no launch entry was found."
            )
        started_at = self._now().isoformat()
        result = subprocess.CompletedProcess(args=[str(entry_path)], returncode=1, stdout="", stderr="")
        recovery_notes: list[str] = []
        target_window: LocalHostWindowMatchView | None = None
        # Opening an app never kills an existing app when window discovery fails.
        for attempt in range(1):
            result = self._launch_desktop_entry(
                entry_path=entry_path,
                entry_kind=entry_kind,
                label=app_spec.label,
            )
            if int(result.returncode) != 0:
                recovery_notes.append(f"launch_failed:{result.stderr}")
                continue
            target_window = self._wait_for_app_window(app_spec, timeout_s=15.0)
            if target_window is None:
                target_window = self._restore_hidden_app_window(app_spec)
            if target_window is not None:
                if self._force_foreground_window(target_window.hwnd):
                    recovery_notes.append("window_visible=true")
                    recovery_notes.append("foreground_forced")
                    break
                recovery_notes.append("foreground_force_failed")
                target_window = None
            else:
                recovery_notes.append("window_visible=false_after_15s")
        if target_window is None:
            raise LocalHostControlError(
                f"Desktop app '{app_spec.label}' started but no visible, focusable window was found after retries. "
                f"Recovery: {'; '.join(recovery_notes)}"
            )
        finished_at = self._now().isoformat()
        view = LocalHostCommandResultView(
            action_kind="desktop_app_open",
            command=f"Start-Process -FilePath '{entry_path}'",
            exit_code=int(result.returncode),
            stdout="; ".join(
                item
                for item in (
                    result.stdout or f"Opened {app_spec.label} via {entry_kind}.",
                    f"Focused window '{target_window.title}'.",
                    *recovery_notes,
                )
                if item
            ),
            stderr=result.stderr,
            workdir=str(self.settings.app_root),
            elevated=self._is_elevated(),
            started_at=started_at,
            finished_at=finished_at,
        )
        self._append_log({**view.to_dict(), "app_key": app_spec.app_key, "entry_kind": entry_kind})
        return view

    # Ambiguous fragments that fuzzy-match Windows / NVIDIA control UIs.
    _SYSTEM_PANEL_AMBIGUOUS_QUERIES = frozenset(
        {
            "panel",
            "control",
            "controls",
            "settings",
            "setting",
            "ustawienia",
            "ustawienie",
            "sterowanie",
            "sterowan",
            "sterowania",
            "cp",
            "cpl",
        }
    )
    _SYSTEM_PANEL_EXPLICIT_QUERIES = frozenset(
        {
            "panel sterowania",
            "control panel",
            "windows control panel",
            "nvidia control panel",
            "ustawienia windows",
            "windows settings",
            "system settings",
            "microsoft settings",
        }
    )
    _SYSTEM_PANEL_RESULT_MARKERS = (
        "panel sterowania",
        "control panel",
        "nvidia control panel",
        "windows settings",
        "immersivecontrolpanel",
        "microsoft.windows.controlpanel",
    )

    @classmethod
    def _normalize_app_query(cls, query: str) -> str:
        return " ".join(str(query or "").strip().casefold().split())

    @classmethod
    def _is_explicit_system_panel_request(cls, normalized: str) -> bool:
        return normalized in cls._SYSTEM_PANEL_EXPLICIT_QUERIES

    @classmethod
    def _is_ambiguous_system_panel_query(cls, normalized: str) -> bool:
        if not normalized:
            return False
        if cls._is_explicit_system_panel_request(normalized):
            return False
        if normalized in cls._SYSTEM_PANEL_AMBIGUOUS_QUERIES:
            return True
        tokens = normalized.split()
        if len(tokens) <= 2 and any(token in cls._SYSTEM_PANEL_AMBIGUOUS_QUERIES for token in tokens):
            # e.g. "open panel", "panel windows" without explicit full name
            if not any(marker in normalized for marker in ("sterowania", "nvidia", "windows settings")):
                return True
        return False

    @classmethod
    def _is_blocked_system_panel_result(cls, label: str, *, app_id: str = "") -> bool:
        hay = f"{label} {app_id}".casefold()
        return any(marker in hay for marker in cls._SYSTEM_PANEL_RESULT_MARKERS)

    @classmethod
    def _fuzzy_match_score(cls, query: str, candidates: Iterable[str]) -> int:
        """Map best edit similarity onto catalog/start-menu scores (0 / 82 / 85)."""
        q = cls._normalize_app_query(query)
        if len(q) < cls._FUZZY_MIN_QUERY_LEN:
            return 0
        best_ratio = 0.0
        for raw in candidates:
            cand = cls._normalize_app_query(str(raw or ""))
            if not cand:
                continue
            ratio = difflib.SequenceMatcher(None, q, cand).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
        if best_ratio >= cls._FUZZY_STRONG_RATIO:
            return cls._FUZZY_STRONG_SCORE
        if best_ratio >= cls._FUZZY_OK_RATIO:
            return cls._FUZZY_OK_SCORE
        return 0

    @classmethod
    def _is_clear_score_winner(cls, scored: list[tuple[int, Any]]) -> bool:
        if not scored:
            return False
        if len(scored) == 1:
            return True
        return scored[0][0] - scored[1][0] >= cls._CLEAR_WINNER_MARGIN

    def _merged_query_aliases(self) -> dict[str, str]:
        merged = dict(self.DESKTOP_APP_QUERY_ALIASES)
        merged.update(load_persisted_desktop_aliases(self.settings))
        return merged

    @classmethod
    def _score_start_menu_name(cls, *, name: str, query: str) -> int:
        label = " ".join(str(name or "").strip().casefold().split())
        q = cls._normalize_app_query(query)
        if not label or not q:
            return 0
        if label == q:
            return 100
        # Ambiguous fragments like "panel"/"control" must never rank as strong hits.
        if q in cls._SYSTEM_PANEL_AMBIGUOUS_QUERIES:
            return 0
        if label.startswith(q + " ") or label.startswith(q):
            return 92 if len(q) >= 4 else 75
        # Prefer whole-token hits over raw substring (avoids accidental launches).
        tokens = [t for t in label.replace("-", " ").split() if t]
        if q in tokens:
            return 88
        if len(q) >= 6 and q in label:
            return 70
        fuzzy = cls._fuzzy_match_score(q, [label, *tokens])
        return fuzzy

    def open_or_resolve_desktop_app(self, *, query: str, list_only: bool = False) -> dict[str, object]:
        """Match local desktop apps by name and optionally launch the best match."""
        normalized = self._normalize_app_query(query)
        if not normalized:
            raise LocalHostControlError("App query is empty.")

        if self._is_ambiguous_system_panel_query(normalized) and not list_only:
            return {
                "status": "blocked",
                "query": query,
                "resolved_alias": None,
                "matches": [],
                "match_count": 0,
                "reason": "system_panel_ambiguous",
                "hint": (
                    "Nie otwieram Panelu sterowania / Ustawień Windows dla niejednoznacznego "
                    "zapytania. Podaj dokładną nazwę aplikacji (np. CapCut, Obsidian) "
                    "albo napisz wprost „Panel sterowania” / „Ustawienia Windows”."
                ),
            }

        alias_target = self._merged_query_aliases().get(normalized)
        alias_by_key = {spec.app_key: spec.aliases for spec in self.KNOWN_DESKTOP_APPS}
        catalog = [item.to_view() for item in self._build_desktop_app_catalog()]
        scored: list[tuple[int, LocalDesktopAppView, str]] = []
        for app in catalog:
            label = app.label.casefold()
            key = app.app_key.casefold()
            aliases = tuple(a.casefold() for a in alias_by_key.get(app.app_key, ()))
            score = 0
            match_via = "none"
            if alias_target and app.app_key == alias_target:
                score = 100
                match_via = "alias"
            elif label == normalized or key == normalized or normalized in aliases:
                score = 100
                match_via = "exact"
            elif any(normalized in alias or alias in normalized for alias in aliases if alias):
                score = 90
                match_via = "alias_partial"
            elif normalized in label or normalized in key:
                score = 80
                match_via = "substring"
            elif label in normalized:
                score = 70
                match_via = "label_in_query"
            else:
                fuzzy = self._fuzzy_match_score(normalized, (label, key, *aliases))
                if fuzzy:
                    score = fuzzy
                    match_via = "fuzzy"
            if score:
                scored.append((score, app, match_via))
        scored.sort(key=lambda item: (-item[0], item[1].label.casefold()))
        matches = [
            {
                "app_key": app.app_key,
                "label": app.label,
                "installed": app.installed,
                "launch_supported": app.launch_supported,
                "running": app.running,
                "score": score,
                "matched_via_alias": bool(alias_target and app.app_key == alias_target),
                "match_via": match_via,
            }
            for score, app, match_via in scored[:8]
        ]

        if list_only:
            return {
                "status": "listed",
                "query": query,
                "resolved_alias": alias_target,
                "matches": matches,
                "match_count": len(matches),
            }

        # Strong catalog hit, or clear fuzzy winner — no weak ambiguous auto-launch.
        best = scored[0][1] if scored else None
        best_score = scored[0][0] if scored else 0
        best_via = scored[0][2] if scored else "none"
        can_auto = (
            best is not None
            and best.launch_supported
            and best_score >= self._CATALOG_AUTO_LAUNCH_MIN_SCORE
            and (best_via != "fuzzy" or self._is_clear_score_winner([(s, a) for s, a, _ in scored]))
        )
        if can_auto and best is not None:
            try:
                result = self.open_desktop_app(best.app_key)
                return {
                    "status": "opened",
                    "query": query,
                    "app_key": best.app_key,
                    "label": best.label,
                    "resolved_alias": alias_target,
                    "match_via": best_via,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "matches": matches,
                }
            except LocalHostControlError as exc:
                catalog_error = str(exc)
        else:
            catalog_error = None

        # Alias hit without launchable entry: try Start Menu with canonical label.
        start_query = normalized
        if alias_target:
            for spec in self.KNOWN_DESKTOP_APPS:
                if spec.app_key == alias_target:
                    start_query = spec.label.casefold()
                    break
        start_menu = self._launch_start_menu_app(start_query)
        if start_menu.get("status") == "opened":
            start_menu["query"] = query
            start_menu["resolved_alias"] = alias_target
            start_menu["catalog_matches"] = matches
            if catalog_error:
                start_menu["catalog_error"] = catalog_error
            return start_menu
        if start_menu.get("status") == "blocked":
            start_menu["query"] = query
            start_menu["resolved_alias"] = alias_target
            start_menu["catalog_matches"] = matches
            if catalog_error:
                start_menu["catalog_error"] = catalog_error
            return start_menu

        known_hint = None
        if alias_target:
            for spec in self.KNOWN_DESKTOP_APPS:
                if spec.app_key == alias_target and spec.not_found_hint:
                    known_hint = spec.not_found_hint
                    break
        payload: dict[str, object] = {
            "status": "not_found",
            "query": query,
            "resolved_alias": alias_target,
            "matches": matches,
            "match_count": len(matches),
            "hint": (
                (known_hint + " ")
                if known_hint
                else ""
            )
            + (
                "Nie znaleziono lokalnej aplikacji. Podaj dokładniejszą nazwę "
                "z menu Start albo ścieżkę .exe/.lnk — nie otwieraj WWW "
                "i nie oferuj Midjourney/DALL-E zamiast programu."
                + (
                    f" Podobne: {', '.join(str(m.get('label')) for m in matches[:3])}."
                    if matches
                    else ""
                )
            ),
        }
        if catalog_error:
            payload["catalog_error"] = catalog_error
        if start_menu.get("error"):
            payload["start_menu_error"] = start_menu.get("error")
        if start_menu.get("matches"):
            payload["start_menu_matches"] = start_menu.get("matches")
        return payload

    def _launch_start_menu_app(self, query: str) -> dict[str, object]:
        normalized = self._normalize_app_query(query)
        if len(normalized) < 3:
            return {"status": "not_found", "matches": [], "reason": "query_too_short"}
        if self._is_ambiguous_system_panel_query(normalized):
            return {
                "status": "blocked",
                "matches": [],
                "reason": "system_panel_ambiguous",
                "hint": (
                    "Odrzucono niejednoznaczne zapytanie pasujące do Panelu sterowania / "
                    "Ustawień Windows."
                ),
            }

        safe_query = normalized.replace("'", "''")
        # List candidates only — scoring + blocklist happen in Python before launch.
        list_command = (
            "$ErrorActionPreference = 'Stop'; "
            f"$q = '{safe_query}'; "
            "$apps = @(Get-StartApps | Where-Object { $_.Name } | ForEach-Object { "
            "[pscustomobject]@{ name=$_.Name; app_id=$_.AppID } "
            "} | Select-Object -First 400); "
            "[pscustomobject]@{ status='listed'; apps=@($apps) } | ConvertTo-Json -Compress -Depth 4"
        )
        try:
            listed = self.run_powershell(list_command, timeout_s=45)
        except LocalHostControlError as exc:
            return {"status": "error", "error": str(exc)}
        raw = (listed.stdout or "").strip()
        if not raw:
            return {
                "status": "error",
                "error": listed.stderr or "Empty Get-StartApps response",
                "exit_code": listed.exit_code,
            }
        try:
            listed_payload = json.loads(raw.splitlines()[-1])
        except json.JSONDecodeError:
            return {
                "status": "error",
                "error": f"Unparseable StartApps output: {raw[:400]}",
                "exit_code": listed.exit_code,
            }
        apps_raw = listed_payload.get("apps") if isinstance(listed_payload, dict) else None
        if not isinstance(apps_raw, list):
            return {"status": "error", "error": "Unexpected StartApps payload"}

        scored: list[tuple[int, str, str]] = []
        for item in apps_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            app_id = str(item.get("app_id") or "").strip()
            if not name or not app_id:
                continue
            score = self._score_start_menu_name(name=name, query=normalized)
            if score <= 0:
                continue
            scored.append((score, name, app_id))
        scored.sort(key=lambda row: (-row[0], row[1].casefold()))
        match_names = [name for _, name, _ in scored[:5]]
        if not scored:
            return {"status": "not_found", "matches": []}

        best_score, best_name, best_app_id = scored[0]
        explicit_panel = self._is_explicit_system_panel_request(normalized)
        if self._is_blocked_system_panel_result(best_name, app_id=best_app_id) and not explicit_panel:
            return {
                "status": "blocked",
                "matches": match_names,
                "label": best_name,
                "app_id": best_app_id,
                "reason": "system_panel_blocked",
                "hint": (
                    "Najlepsze dopasowanie to Panel sterowania / Ustawienia Windows — "
                    "nie otwieram go bez wyraźnej prośby."
                ),
            }
        # Weak substring matches must not auto-launch.
        # Fuzzy (82–85) may launch only as a clear winner.
        clear_winner = self._is_clear_score_winner([(score, name) for score, name, _ in scored])
        strong_enough = best_score >= 88 or (best_score >= self._FUZZY_STRONG_SCORE and clear_winner)
        if not strong_enough:
            return {
                "status": "not_found",
                "matches": match_names,
                "reason": "weak_start_menu_match",
                "hint": "Dopasowanie z menu Start jest zbyt słabe — podaj dokładniejszą nazwę.",
            }

        safe_app_id = best_app_id.replace("'", "''")
        launch_command = (
            "$ErrorActionPreference = 'Stop'; "
            f"Start-Process (\"shell:AppsFolder\\{safe_app_id}\"); "
            "[pscustomobject]@{ status='opened' } | ConvertTo-Json -Compress"
        )
        try:
            launched = self.run_powershell(launch_command, timeout_s=45)
        except LocalHostControlError as exc:
            return {"status": "error", "error": str(exc), "matches": match_names}
        if int(launched.exit_code) != 0:
            return {
                "status": "error",
                "error": launched.stderr or "Start-Process failed",
                "exit_code": launched.exit_code,
                "matches": match_names,
            }
        return {
            "status": "opened",
            "label": best_name,
            "app_id": best_app_id,
            "matches": match_names,
            "score": best_score,
        }

    def _launch_steam_app(
        self,
        *,
        steam_app_id: str,
        label: str,
        app_key: str,
    ) -> LocalHostCommandResultView:
        """Launch a Steam title via steam:// URI (install or run if owned)."""
        self._ensure_execution_allowed()
        app_id = str(steam_app_id or "").strip()
        if not app_id.isdigit():
            raise LocalHostControlError(f"Invalid Steam app id for '{label}'.")
        uri = f"steam://rungameid/{app_id}"
        started_at = self._now().isoformat()
        safe_uri = uri.replace("'", "''")
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f"Start-Process '{safe_uri}'; "
            "[pscustomobject]@{ status='opened'; uri='"
            + safe_uri
            + "' } | ConvertTo-Json -Compress"
        )
        launched = self.run_powershell(command, timeout_s=45)
        if int(launched.exit_code) != 0:
            raise LocalHostControlError(
                launched.stderr
                or f"Steam launch failed for {label} ({uri}). Is Steam installed?"
            )
        finished_at = self._now().isoformat()
        return LocalHostCommandResultView(
            action_kind="desktop_app_open",
            command=f"Start-Process '{uri}'",
            exit_code=0,
            stdout=(
                f"Opened {label} via Steam ({uri}). "
                f"Jeśli gra nie jest zainstalowana, Steam pokaże instalację. app_key={app_key}"
            ),
            stderr=launched.stderr or "",
            workdir=str(self.settings.app_root),
            elevated=self._is_elevated(),
            started_at=started_at,
            finished_at=finished_at,
        )

    def focus_desktop_app(self, app_key: str) -> LocalHostCommandResultView:
        app_spec = self._get_desktop_app_runtime_spec(app_key)
        if app_spec is None:
            raise LocalHostControlError(f"Unsupported desktop app '{app_key}'.")
        title_hint = app_spec.title_hints[0] if app_spec.title_hints else None
        process_name = app_spec.process_names[0] if app_spec.process_names else None
        try:
            return self.activate_window(
                title_contains=title_hint,
                process_name=process_name,
                browser_only=False,
            )
        except LocalHostControlError:
            if title_hint is not None:
                return self.activate_window(
                    title_contains=title_hint,
                    process_name=None,
                    browser_only=False,
                )
            raise

    def focus_desktop_app_input(self, app_key: str) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        app_spec = self._get_desktop_app_runtime_spec(app_key)
        if app_spec is None:
            raise LocalHostControlError(f"Unsupported desktop app '{app_key}'.")
        target = self._wait_for_app_window(app_spec, timeout_s=2.0) or self._restore_hidden_app_window(app_spec)
        if target is None:
            raise LocalHostControlError(f"No visible desktop window was found for '{app_spec.label}'.")
        started_at = self._now().isoformat()
        self._force_foreground_window(target.hwnd)
        method = self._focus_first_editable_control(target.hwnd)
        if method is None:
            self._click_window_center(target.hwnd)
            if self._looks_like_chat_input_app(app_spec):
                self._click_window_chat_input_zone(target.hwnd)
                method = "window_center_click_then_chat_input_zone"
            else:
                method = "window_center_click"
        finished_at = self._now().isoformat()
        return self._build_input_result(
            action_kind="desktop_input_focus",
            command=f"desktop.input_focus(app_key={json.dumps(app_key)})",
            stdout=f"Focused input for '{app_spec.label}' using {method}.",
            started_at=started_at,
            finished_at=finished_at,
        )

    def read_desktop_app_accessibility_text(self, app_key: str, *, limit: int = 24000) -> str:
        self._ensure_execution_allowed()
        app_spec = self._get_desktop_app_runtime_spec(app_key)
        if app_spec is None:
            raise LocalHostControlError(f"Unsupported desktop app '{app_key}'.")
        target = self._wait_for_app_window(app_spec, timeout_s=2.0) or self._restore_hidden_app_window(app_spec)
        if target is None:
            raise LocalHostControlError(f"No visible desktop window was found for '{app_spec.label}'.")
        self._force_foreground_window(target.hwnd)
        return self._read_uia_text(target.hwnd, limit=limit)

    def _launch_desktop_entry(
        self,
        *,
        entry_path: Path,
        entry_kind: str,
        label: str,
    ) -> subprocess.CompletedProcess[str]:
        if os.name == "nt" and hasattr(os, "startfile"):
            try:
                os.startfile(str(entry_path))
                return subprocess.CompletedProcess(
                    args=[str(entry_path)],
                    returncode=0,
                    stdout=f"Opened {label} via {entry_kind}.",
                    stderr="",
                )
            except OSError as exc:
                return subprocess.CompletedProcess(
                    args=[str(entry_path)],
                    returncode=1,
                    stdout="",
                    stderr=str(exc),
                )
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            raise LocalHostControlError("PowerShell is not available on this host.")
        safe_path = str(entry_path).replace("'", "''")
        return subprocess.run(
            [powershell, "-NoProfile", "-Command", f"Start-Process -FilePath '{safe_path}'"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            cwd=str(self.settings.app_root),
        )

    def _wait_for_app_window(
        self,
        app_spec: _ResolvedDesktopAppSpec,
        *,
        timeout_s: float,
    ) -> LocalHostWindowMatchView | None:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while time.monotonic() <= deadline:
            windows = self._find_app_windows(app_spec, include_hidden=False)
            if windows:
                return windows[0]
            time.sleep(0.5)
        return None

    def _restore_hidden_app_window(self, app_spec: _ResolvedDesktopAppSpec) -> LocalHostWindowMatchView | None:
        for window in self._find_app_windows(app_spec, include_hidden=True):
            try:
                ctypes.windll.user32.ShowWindow(window.hwnd, self.SW_RESTORE)
                ctypes.windll.user32.ShowWindow(window.hwnd, self.SW_SHOW)
                self._force_foreground_window(window.hwnd)
                time.sleep(0.5)
            except Exception:
                continue
            refreshed = self._find_app_windows(app_spec, include_hidden=False)
            if refreshed:
                return refreshed[0]
        return None

    def _find_app_windows(
        self,
        app_spec: _ResolvedDesktopAppSpec,
        *,
        include_hidden: bool,
    ) -> list[LocalHostWindowMatchView]:
        title_hint = app_spec.title_hints[0] if app_spec.title_hints else None
        process_name = app_spec.process_names[0] if app_spec.process_names else None
        windows: list[LocalHostWindowMatchView] = []
        if process_name:
            windows.extend(
                self.find_windows(
                    title_contains=title_hint,
                    process_name=process_name,
                    include_hidden=include_hidden,
                )
            )
        if not windows and title_hint:
            windows.extend(
                self.find_windows(
                    title_contains=title_hint,
                    process_name=None,
                    include_hidden=include_hidden,
                )
            )
        seen: set[int] = set()
        unique: list[LocalHostWindowMatchView] = []
        for window in windows:
            if window.hwnd in seen:
                continue
            seen.add(window.hwnd)
            unique.append(window)
        return unique

    def _terminate_app_processes(self, app_spec: _ResolvedDesktopAppSpec) -> None:
        raise LocalHostControlError("Name-based application termination is disabled.")

    def run_browser_recipe(
        self,
        *,
        steps: list[dict[str, object]],
        headless: bool | None = None,
        action_timeout_ms: int | None = None,
        total_timeout_ms: int | None = None,
        retry_limit: int | None = None,
        allowed_domains: list[str] | None = None,
        recipe_name: str | None = None,
        recipe_payload: dict[str, object] | None = None,
        conversation_id: int | None = None,
    ) -> LocalHostBrowserRecipeResultView:
        self._ensure_execution_allowed()
        if not self.runtime_controls.browser_control_enabled:
            raise LocalHostControlError("Browser control is disabled in runtime settings.")
        started_at = self._now().isoformat()
        try:
            runner = BrowserRecipeRunnerService(
                self.settings,
                session=self.session,
                runtime_controls=self.runtime_controls,
            )
            if recipe_name:
                payload = runner.run_named_recipe(
                    recipe_name,
                    payload={
                        **dict(recipe_payload or {}),
                        "conversation_id": conversation_id,
                        "headless": headless,
                        "action_timeout_ms": action_timeout_ms,
                        "total_timeout_ms": total_timeout_ms,
                        "retry_limit": retry_limit,
                        "allowed_domains": allowed_domains or [],
                    },
                )
            else:
                payload = runner.run(
                    {
                        "headless": headless,
                        "steps": steps,
                        "action_timeout_ms": action_timeout_ms,
                        "total_timeout_ms": total_timeout_ms,
                        "retry_limit": retry_limit,
                        "allowed_domains": allowed_domains or [],
                        "conversation_id": conversation_id,
                    }
                )
        except Exception as exc:
            finished_at = self._now().isoformat()
            view = LocalHostBrowserRecipeResultView(
                action_kind="browser_recipe",
                ok=False,
                final_url="",
                results=[],
                artifacts={},
                runtime_fallback=None,
                started_at=started_at,
                finished_at=finished_at,
            )
            self._append_log(
                {
                    **view.to_dict(),
                    "error": str(exc),
                    "steps": steps,
                    "headless": headless,
                    "action_timeout_ms": action_timeout_ms,
                    "total_timeout_ms": total_timeout_ms,
                    "retry_limit": retry_limit,
                    "allowed_domains": allowed_domains or [],
                    "recipe_name": recipe_name,
                }
            )
            raise LocalHostControlError(str(exc)) from exc
        finished_at = self._now().isoformat()
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        view = LocalHostBrowserRecipeResultView(
            action_kind="browser_recipe",
            ok=bool(payload.get("ok")),
            final_url=str(payload.get("finalUrl") or ""),
            results=[item for item in results if isinstance(item, dict)],
            artifacts={str(key): str(value) for key, value in artifacts.items()},
            runtime_fallback=(
                str(payload.get("runtime_fallback"))
                if payload.get("runtime_fallback") is not None
                else None
            ),
            started_at=started_at,
            finished_at=finished_at,
        )
        self._append_log(
            {
                **view.to_dict(),
                "steps": steps,
                "headless": headless,
                "action_timeout_ms": action_timeout_ms,
                "total_timeout_ms": total_timeout_ms,
                "retry_limit": retry_limit,
                "allowed_domains": allowed_domains or [],
                "recipe_name": recipe_name,
            }
        )
        return view

    def move_mouse(self, *, x: int, y: int) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        self._ensure_desktop_input_available()
        started_at = self._now().isoformat()
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        finished_at = self._now().isoformat()
        return self._build_input_result(
            action_kind="mouse_move",
            command=f"mouse.move({int(x)}, {int(y)})",
            stdout=f"Moved pointer to ({int(x)}, {int(y)}).",
            started_at=started_at,
            finished_at=finished_at,
        )

    def click_mouse(
        self,
        *,
        button: str = "left",
        clicks: int = 1,
        x: int | None = None,
        y: int | None = None,
    ) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        self._ensure_desktop_input_available()
        normalized_button = button.strip().lower()
        button_flags = {
            "left": (self.MOUSEEVENTF_LEFTDOWN, self.MOUSEEVENTF_LEFTUP),
            "right": (self.MOUSEEVENTF_RIGHTDOWN, self.MOUSEEVENTF_RIGHTUP),
            "middle": (self.MOUSEEVENTF_MIDDLEDOWN, self.MOUSEEVENTF_MIDDLEUP),
        }.get(normalized_button)
        if button_flags is None:
            raise LocalHostControlError("button must be one of: left, right, middle.")
        if x is not None and y is not None:
            ctypes.windll.user32.SetCursorPos(int(x), int(y))
        started_at = self._now().isoformat()
        for _ in range(max(1, int(clicks))):
            self._ensure_foreground_input_context()
            self._send_mouse_input(button_flags[0])
            self._send_mouse_input(button_flags[1])
        finished_at = self._now().isoformat()
        cursor_suffix = f" at ({int(x)}, {int(y)})" if x is not None and y is not None else ""
        return self._build_input_result(
            action_kind="mouse_click",
            command=f"mouse.click(button='{normalized_button}', clicks={max(1, int(clicks))}{', x=' + str(int(x)) + ', y=' + str(int(y)) if x is not None and y is not None else ''})",
            stdout=f"Clicked {normalized_button} mouse button {max(1, int(clicks))} time(s){cursor_suffix}.",
            started_at=started_at,
            finished_at=finished_at,
        )

    def scroll_mouse(self, *, delta: int) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        self._ensure_desktop_input_available()
        started_at = self._now().isoformat()
        self._send_mouse_input(self.MOUSEEVENTF_WHEEL, mouse_data=int(delta))
        finished_at = self._now().isoformat()
        return self._build_input_result(
            action_kind="mouse_scroll",
            command=f"mouse.scroll(delta={int(delta)})",
            stdout=f"Scrolled mouse wheel by {int(delta)}.",
            started_at=started_at,
            finished_at=finished_at,
        )

    def type_text(self, *, text: str) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        self._ensure_desktop_input_available()
        if not text:
            raise LocalHostControlError("text is required.")
        started_at = self._now().isoformat()
        for char in text:
            self._ensure_foreground_input_context()
            self._send_unicode_char(char)
        finished_at = self._now().isoformat()
        preview = text if len(text) <= 160 else f"{text[:157]}..."
        return self._build_input_result(
            action_kind="keyboard_type",
            command=f"keyboard.type({json.dumps(preview, ensure_ascii=False)})",
            stdout=f"Typed {len(text)} character(s).",
            started_at=started_at,
            finished_at=finished_at,
        )

    def paste_text(self, *, text: str) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        self._ensure_desktop_input_available()
        if not text:
            raise LocalHostControlError("text is required.")
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        command = (
            "$bytes=[Convert]::FromBase64String('"
            + encoded
            + "'); $text=[Text.Encoding]::UTF8.GetString($bytes); Set-Clipboard -Value $text"
        )
        started_at = self._now().isoformat()
        clipboard = self.run_powershell(command, timeout_s=10)
        if clipboard.exit_code != 0:
            raise LocalHostControlError(f"Clipboard paste fallback failed: {clipboard.stderr}")
        self.press_keys(keys=["CTRL", "V"])
        finished_at = self._now().isoformat()
        return self._build_input_result(
            action_kind="keyboard_paste",
            command=f"keyboard.paste({len(text)} chars)",
            stdout=f"Pasted {len(text)} character(s) through clipboard fallback.",
            started_at=started_at,
            finished_at=finished_at,
        )

    def press_keys(self, *, keys: list[str]) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        self._ensure_desktop_input_available()
        if not keys:
            raise LocalHostControlError("keys are required.")
        normalized_keys = [self._normalize_key_name(item) for item in keys if str(item).strip()]
        if not normalized_keys:
            raise LocalHostControlError("keys are required.")
        started_at = self._now().isoformat()
        codes = [self._resolve_virtual_key_code(item) for item in normalized_keys]
        for code in codes:
            self._ensure_foreground_input_context()
            self._send_virtual_key(code, key_up=False)
        for code in reversed(codes):
            self._ensure_foreground_input_context()
            self._send_virtual_key(code, key_up=True)
        finished_at = self._now().isoformat()
        combo = "+".join(normalized_keys)
        return self._build_input_result(
            action_kind="keyboard_hotkey",
            command=f"keyboard.hotkey({combo})",
            stdout=f"Pressed hotkey combo: {combo}.",
            started_at=started_at,
            finished_at=finished_at,
        )

    def find_windows(
        self,
        *,
        title_contains: str | None = None,
        process_name: str | None = None,
        browser_only: bool = False,
        include_hidden: bool = False,
    ) -> list[LocalHostWindowMatchView]:
        self._ensure_desktop_input_available()
        normalized_title = title_contains.strip().lower() if title_contains else None
        normalized_process = process_name.strip().lower() if process_name else None
        foreground_hwnd = self._get_foreground_hwnd()
        results: list[LocalHostWindowMatchView] = []

        def callback(hwnd: int, _: int) -> bool:
            if not include_hidden and not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            title = self._read_window_title(hwnd)
            if not title:
                return True
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            resolved_process_name = self._resolve_process_name(pid.value)
            if browser_only and resolved_process_name.lower() not in self.KNOWN_BROWSER_PROCESSES:
                return True
            if normalized_process and resolved_process_name.lower() != normalized_process:
                return True
            if normalized_title and normalized_title not in title.lower():
                return True
            results.append(
                LocalHostWindowMatchView(
                    hwnd=int(hwnd),
                    title=title,
                    process_id=int(pid.value),
                    process_name=resolved_process_name,
                    is_foreground=int(hwnd) == foreground_hwnd,
                )
            )
            return True

        enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback)
        ctypes.windll.user32.EnumWindows(enum_windows_proc, 0)
        return sorted(
            results,
            key=lambda item: (
                not item.is_foreground,
                0 if (normalized_title and normalized_title in item.title.lower()) else 1,
                item.process_name,
                item.title.lower(),
            ),
        )

    def activate_window(
        self,
        *,
        title_contains: str | None = None,
        process_name: str | None = None,
        browser_only: bool = False,
    ) -> LocalHostCommandResultView:
        self._ensure_execution_allowed()
        matches = self.find_windows(
            title_contains=title_contains,
            process_name=process_name,
            browser_only=browser_only,
        )
        if not matches:
            raise LocalHostControlError("No matching desktop window was found.")
        target = matches[0]
        started_at = self._now().isoformat()
        if self._get_foreground_hwnd() == target.hwnd:
            finished_at = self._now().isoformat()
            return self._build_input_result(
                action_kind="window_activate",
                command=(
                    f"window.activate(title_contains={json.dumps(title_contains) if title_contains else 'null'}, "
                    f"process_name={json.dumps(process_name) if process_name else 'null'}, browser_only={str(browser_only).lower()})"
                ),
                stdout=f"Window '{target.title}' ({target.process_name}) was already active.",
                started_at=started_at,
                finished_at=finished_at,
            )
        self._force_foreground_window(target.hwnd)
        time.sleep(0.35)
        if self._get_foreground_hwnd() != target.hwnd:
            self._click_window_center(target.hwnd)
            time.sleep(0.35)
        if self._get_foreground_hwnd() != target.hwnd:
            raise LocalHostControlError(f"Could not focus window '{target.title}'.")
        finished_at = self._now().isoformat()
        return self._build_input_result(
            action_kind="window_activate",
            command=(
                f"window.activate(title_contains={json.dumps(title_contains) if title_contains else 'null'}, "
                f"process_name={json.dumps(process_name) if process_name else 'null'}, browser_only={str(browser_only).lower()})"
            ),
            stdout=f"Activated window '{target.title}' ({target.process_name}).",
            started_at=started_at,
            finished_at=finished_at,
        )

    def capture_foreground_window_screenshot(
        self,
        *,
        label: str = "capture",
    ) -> LocalHostScreenshotCaptureView:
        self._ensure_execution_allowed()
        self._ensure_desktop_input_available()
        foreground_hwnd = self._get_foreground_hwnd()
        if foreground_hwnd <= 0:
            raise LocalHostControlError("No foreground window is available for capture.")
        rect = self._get_window_rect(foreground_hwnd)
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        target_dir = (self.settings.browser_downloads_dir / "desktop-captures").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = (target_dir / f"{label}-{uuid4().hex}.png").resolve()
        self._run_window_capture_script(
            screenshot_path,
            left=rect.left,
            top=rect.top,
            width=width,
            height=height,
        )
        view = LocalHostScreenshotCaptureView(
            screenshot_path=str(screenshot_path),
            window_title=self._read_window_title(foreground_hwnd),
            width=width,
            height=height,
            captured_at=self._now().isoformat(),
        )
        self._append_log(
            {
                "action_kind": "window_capture",
                "command": f"window.capture_foreground({json.dumps(label)})",
                **view.to_dict(),
            }
        )
        return view

    def _get_desktop_app_runtime_spec(self, app_key: str) -> _ResolvedDesktopAppSpec | None:
        normalized = app_key.strip().lower()
        for spec in self._build_desktop_app_catalog():
            if spec.app_key == normalized:
                return spec
        return None

    def _discover_shortcut_apps(
        self,
        *,
        process_map: dict[str, list[psutil.Process]],
        desktop_windows: list[LocalHostWindowMatchView],
        reserved_app_keys: set[str],
        reserved_labels: set[str],
        reserved_entry_paths: set[str],
    ) -> list[_ResolvedDesktopAppSpec]:
        views: list[_ResolvedDesktopAppSpec] = []
        seen_shortcuts: set[str] = set()
        seen_entry_paths = set(reserved_entry_paths)
        seen_labels = set(reserved_labels)
        seen_app_keys = set(reserved_app_keys)
        for shortcut, resolved_target in self._resolve_shortcut_targets_bulk():
            normalized_shortcut = str(shortcut).casefold()
            if normalized_shortcut in seen_shortcuts:
                continue
            seen_shortcuts.add(normalized_shortcut)
            if not resolved_target.exists():
                continue
            entry_path = resolved_target.resolve()
            normalized_entry = str(entry_path).casefold()
            label = self._normalize_shortcut_label(shortcut.stem)
            if not label:
                continue
            if not self._should_expose_discovered_shortcut(label=label, entry_path=entry_path):
                continue
            normalized_label = label.casefold()
            if normalized_label in seen_labels or normalized_entry in seen_entry_paths:
                continue
            process_names = self._build_discovered_process_names(entry_path)
            title_hints = self._build_discovered_title_hints(label)
            matched_process_names = tuple(
                sorted(
                    {
                        process_name
                        for process_name in process_map
                        if process_name.casefold() in {item.casefold() for item in process_names}
                    }
                )
            )
            matched_windows = tuple(
                window.title
                for window in desktop_windows
                if self._window_matches_dynamic_app(
                    window,
                    process_names=process_names,
                    title_hints=title_hints,
                )
            )
            app_key = self._build_discovered_app_key(label, seen_app_keys)
            seen_app_keys.add(app_key)
            seen_labels.add(normalized_label)
            seen_entry_paths.add(normalized_entry)
            notes = [
                "entry=shortcut-target",
                "source=shortcut-discovery",
                f"shortcut={shortcut}",
            ]
            if matched_windows:
                notes.append("window_visible")
            views.append(
                _ResolvedDesktopAppSpec(
                    app_key=app_key,
                    label=label,
                    category=self._classify_discovered_app(label=label, entry_path=entry_path),
                    process_names=process_names,
                    title_hints=title_hints,
                    resolved_entry_path=entry_path,
                    resolved_entry_kind="shortcut-target",
                    running_processes=matched_process_names,
                    visible_window_titles=matched_windows,
                    notes=tuple(notes),
                )
            )
        return sorted(views, key=lambda item: (item.category, item.label.casefold()))

    def _ensure_execution_allowed(self) -> None:
        if self.runtime_controls.emergency_stop_enabled:
            raise LocalHostControlError("Local host execution is blocked by the emergency stop.")
        if self.runtime_controls.pause_all_jobs:
            raise LocalHostControlError("Local host execution is paused by runtime controls.")

    def _resolve_public_workdir(self, workdir: str | Path | None) -> Path:
        resolved = Path(workdir or self.settings.app_root).resolve()
        allowed_roots = {
            Path(self.settings.app_root).resolve(),
            Path(self.settings.data_dir).resolve(),
        }
        if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
            raise LocalHostControlError(
                "Public local-host workdir must stay inside app_root or data_dir."
            )
        return resolved

    @staticmethod
    def _ensure_desktop_input_available() -> None:
        if os.name != "nt":
            raise LocalHostControlError("Desktop input control is only supported on Windows hosts.")

    def _append_log(self, payload: dict[str, Any]) -> None:
        append_redacted_jsonl(self.command_log_path, payload)

    def _resolve_app_entry(self, spec: _KnownDesktopAppSpec) -> tuple[Path | None, str | None]:
        for pattern in spec.path_patterns:
            for candidate in self._expand_path_pattern(pattern):
                if candidate.exists():
                    return candidate.resolve(), "executable"
        for shortcut_name in spec.shortcut_names:
            shortcut = self._find_shortcut(shortcut_name)
            if shortcut is not None:
                shortcut_target = self._resolve_shortcut_target(shortcut)
                if shortcut_target is not None and shortcut_target.exists():
                    return shortcut_target.resolve(), "shortcut-target"
                if shortcut_target is not None and not shortcut_target.exists():
                    continue
                return shortcut.resolve(), "shortcut"
        return None, None

    def _expand_path_pattern(self, pattern: str) -> list[Path]:
        expanded = os.path.expandvars(pattern)
        if "*" in expanded:
            return [Path(item) for item in sorted(glob.glob(expanded))]
        return [Path(expanded)]

    def _find_shortcut(self, shortcut_name: str) -> Path | None:
        for root in self._iter_shortcut_roots():
            direct = root / shortcut_name
            if direct.exists():
                return direct
            for candidate in root.rglob(shortcut_name):
                if candidate.exists():
                    return candidate
        return None

    @staticmethod
    def _resolve_shortcut_target(shortcut_path: Path) -> Path | None:
        if os.name != "nt" or shortcut_path.suffix.lower() != ".lnk":
            return None
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return None
        safe_path = str(shortcut_path).replace("'", "''")
        command = (
            "$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut"
            f"('{safe_path}'); "
            "$shortcut.TargetPath"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        target = str(result.stdout or "").strip()
        if not target:
            return None
        return Path(target)

    def _resolve_shortcut_targets_bulk(self) -> tuple[tuple[Path, Path], ...]:
        if os.name != "nt":
            return ()
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            return ()
        roots = [root for root in self._iter_shortcut_roots() if root.exists()]
        if not roots:
            return ()
        quoted_roots = ", ".join(
            "'" + str(root).replace("'", "''") + "'"
            for root in roots
        )
        command = (
            "$ErrorActionPreference = 'SilentlyContinue'; "
            "$shell = New-Object -ComObject WScript.Shell; "
            f"$roots = @({quoted_roots}); "
            "$results = foreach ($root in $roots) { "
            "if (-not (Test-Path $root)) { continue }; "
            "Get-ChildItem -Path $root -Filter *.lnk -Recurse -File | ForEach-Object { "
            "$shortcut = $shell.CreateShortcut($_.FullName); "
            "if ($shortcut.TargetPath) { "
            "[pscustomobject]@{ shortcut = $_.FullName; target = $shortcut.TargetPath } "
            "} "
            "} "
            "}; "
            "$results | ConvertTo-Json -Compress -Depth 3"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        if result.returncode != 0:
            return ()
        raw_payload = str(result.stdout or "").strip()
        if not raw_payload:
            return ()
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return ()
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return ()
        resolved: list[tuple[Path, Path]] = []
        seen: set[tuple[str, str]] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            shortcut_raw = str(item.get("shortcut") or "").strip()
            target_raw = str(item.get("target") or "").strip()
            if not shortcut_raw or not target_raw:
                continue
            pair_key = (shortcut_raw.casefold(), target_raw.casefold())
            if pair_key in seen:
                continue
            seen.add(pair_key)
            resolved.append((Path(shortcut_raw), Path(target_raw)))
        return tuple(resolved)

    def _iter_shortcut_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for csidl in (
            self.CSIDL_DESKTOPDIRECTORY,
            self.CSIDL_COMMON_DESKTOPDIRECTORY,
            self.CSIDL_PROGRAMS,
            self.CSIDL_COMMON_PROGRAMS,
        ):
            resolved = self._get_special_folder_path(csidl)
            if resolved is not None and resolved.exists() and resolved not in roots:
                roots.append(resolved)
        return tuple(roots)

    @staticmethod
    def _get_special_folder_path(csidl: int) -> Path | None:
        if os.name != "nt":
            return None
        buffer = ctypes.create_unicode_buffer(260)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
        if result != 0:
            return None
        value = str(buffer.value).strip()
        return Path(value) if value else None

    @staticmethod
    def _window_matches_app(window: LocalHostWindowMatchView, spec: _KnownDesktopAppSpec) -> bool:
        process_name = window.process_name.lower()
        if process_name in spec.process_names:
            return True
        title = window.title.lower()
        return any(hint in title for hint in spec.title_hints)

    @staticmethod
    def _window_matches_dynamic_app(
        window: LocalHostWindowMatchView,
        *,
        process_names: tuple[str, ...],
        title_hints: tuple[str, ...],
    ) -> bool:
        process_name = window.process_name.casefold()
        if process_name in {item.casefold() for item in process_names}:
            return True
        title = window.title.casefold()
        return any(hint and hint in title for hint in title_hints)

    @staticmethod
    def _normalize_shortcut_label(raw_label: str) -> str:
        value = " ".join(str(raw_label or "").replace("_", " ").split()).strip()
        for suffix in (" - Shortcut", " Shortcut"):
            if value.endswith(suffix):
                value = value[: -len(suffix)].strip()
        return value

    @staticmethod
    def _build_discovered_process_names(entry_path: Path) -> tuple[str, ...]:
        suffix = entry_path.suffix.casefold()
        if suffix == ".exe":
            return (entry_path.name.casefold(),)
        return ()

    @staticmethod
    def _build_discovered_title_hints(label: str) -> tuple[str, ...]:
        normalized = label.casefold().strip()
        if not normalized:
            return ()
        hints = [normalized]
        if "desktop" in normalized:
            hints.append(normalized.replace(" desktop", "").strip())
        return tuple(dict.fromkeys(item for item in hints if item))

    @staticmethod
    def _build_discovered_app_key(label: str, existing_keys: set[str]) -> str:
        base = "".join(
            char if char.isalnum() else "-"
            for char in label.casefold()
        ).strip("-")
        while "--" in base:
            base = base.replace("--", "-")
        if not base:
            base = "desktop-app"
        candidate = f"desktop-{base}"
        if candidate not in existing_keys:
            return candidate
        counter = 2
        while f"{candidate}-{counter}" in existing_keys:
            counter += 1
        return f"{candidate}-{counter}"

    @staticmethod
    def _classify_discovered_app(*, label: str, entry_path: Path) -> str:
        fingerprint = f"{label} {entry_path}".casefold()
        if any(token in fingerprint for token in ("git", "code", "docker", "studio", "terminal", "bash")):
            return "developer"
        if any(token in fingerprint for token in ("obsidian", "notion", "note", "docs")):
            return "knowledge"
        if any(token in fingerprint for token in ("capcut", "premiere", "davinci", "video", "creator")):
            return "video"
        if any(token in fingerprint for token in ("chrome", "edge", "firefox", "opera", "browser")):
            return "browser"
        if any(token in fingerprint for token in ("discord", "slack", "mail", "outlook")):
            return "communication"
        return "utility"

    @staticmethod
    def _should_expose_discovered_shortcut(*, label: str, entry_path: Path) -> bool:
        fingerprint = f"{label} {entry_path}".casefold()
        blocked_terms = (
            "uninstall",
            "installer",
            "installshield",
            "help",
            "readme",
            "release note",
            "license",
            "manual",
            "troubleshoot",
        )
        if any(term in fingerprint for term in blocked_terms):
            return False
        blocked_target_names = {
            "uninstall.exe",
            "unins000.exe",
            "setup.exe",
            "explorer.exe",
            "msiexec.exe",
        }
        if entry_path.name.casefold() in blocked_target_names:
            return False
        if entry_path.suffix.casefold() in {".chm", ".ico"}:
            return False
        return True

    @staticmethod
    def _list_processes_by_name() -> dict[str, list[psutil.Process]]:
        mapping: dict[str, list[psutil.Process]] = {}
        for process in psutil.process_iter(["name"]):
            try:
                name = str(process.info.get("name") or "").strip()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if not name:
                continue
            mapping.setdefault(name, []).append(process)
        return mapping

    def _build_input_result(
        self,
        *,
        action_kind: str,
        command: str,
        stdout: str,
        started_at: str,
        finished_at: str,
    ) -> LocalHostCommandResultView:
        view = LocalHostCommandResultView(
            action_kind=action_kind,
            command=command,
            exit_code=0,
            stdout=stdout,
            stderr="",
            workdir=str(self.settings.app_root),
            elevated=self._is_elevated(),
            started_at=started_at,
            finished_at=finished_at,
        )
        self._append_log(view.to_dict())
        return view

    def _send_unicode_char(self, char: str) -> None:
        codepoint = ord(char)
        down = _INPUT(
            type=self.INPUT_KEYBOARD,
            ki=_KEYBDINPUT(0, codepoint, self.KEYEVENTF_UNICODE, 0, None),
        )
        up = _INPUT(
            type=self.INPUT_KEYBOARD,
            ki=_KEYBDINPUT(0, codepoint, self.KEYEVENTF_UNICODE | self.KEYEVENTF_KEYUP, 0, None),
        )
        self._send_inputs([down, up])

    def _send_virtual_key(self, vk_code: int, *, key_up: bool) -> None:
        flags = self.KEYEVENTF_KEYUP if key_up else 0
        item = _INPUT(
            type=self.INPUT_KEYBOARD,
            ki=_KEYBDINPUT(vk_code, 0, flags, 0, None),
        )
        self._send_inputs([item])

    def _send_mouse_input(self, flags: int, *, mouse_data: int = 0) -> None:
        item = _INPUT(
            type=self.INPUT_MOUSE,
            mi=_MOUSEINPUT(0, 0, mouse_data, flags, 0, None),
        )
        self._send_inputs([item])

    def _send_inputs(self, inputs: list[_INPUT]) -> None:
        if not inputs:
            return
        buffer = (_INPUT * len(inputs))(*inputs)
        sent = ctypes.windll.user32.SendInput(len(inputs), ctypes.byref(buffer), ctypes.sizeof(_INPUT))
        if sent != len(inputs):
            raise LocalHostControlError(f"Desktop input injection failed with result={sent}.")

    def _ensure_foreground_input_context(self) -> None:
        hwnd = self._get_foreground_hwnd()
        if hwnd > 0:
            self._force_foreground_window(hwnd)

    def _force_foreground_window(self, hwnd: int) -> bool:
        if hwnd <= 0:
            return False
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            current_thread = int(kernel32.GetCurrentThreadId())
            target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
            foreground_hwnd = int(user32.GetForegroundWindow())
            foreground_thread = (
                int(user32.GetWindowThreadProcessId(foreground_hwnd, None))
                if foreground_hwnd > 0
                else 0
            )
            attached_pairs: list[tuple[int, int]] = []
            for source, target in ((current_thread, target_thread), (foreground_thread, target_thread)):
                if source > 0 and target > 0 and source != target:
                    try:
                        if user32.AttachThreadInput(source, target, True):
                            attached_pairs.append((source, target))
                    except Exception:
                        continue
            try:
                user32.ShowWindow(hwnd, self.SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.SetActiveWindow(hwnd)
                user32.SetFocus(hwnd)
            finally:
                for source, target in reversed(attached_pairs):
                    try:
                        user32.AttachThreadInput(source, target, False)
                    except Exception:
                        pass
            time.sleep(0.08)
            return int(user32.GetForegroundWindow()) == hwnd
        except Exception:
            return False

    def _click_window_center(self, hwnd: int) -> None:
        rect = self._get_window_rect(hwnd)
        center_x = rect.left + max(1, (rect.right - rect.left) // 2)
        center_y = rect.top + max(1, (rect.bottom - rect.top) // 2)
        self._force_foreground_window(hwnd)
        ctypes.windll.user32.SetCursorPos(int(center_x), int(center_y))
        self._send_mouse_input(self.MOUSEEVENTF_LEFTDOWN)
        self._send_mouse_input(self.MOUSEEVENTF_LEFTUP)

    def _click_window_chat_input_zone(self, hwnd: int) -> None:
        rect = self._get_window_rect(hwnd)
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        x = rect.left + width // 2
        y = rect.top + int(height * 0.86)
        self._force_foreground_window(hwnd)
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        self._send_mouse_input(self.MOUSEEVENTF_LEFTDOWN)
        self._send_mouse_input(self.MOUSEEVENTF_LEFTUP)

    @staticmethod
    def _looks_like_chat_input_app(app_spec: _ResolvedDesktopAppSpec) -> bool:
        fingerprint = f"{app_spec.app_key} {app_spec.label} {' '.join(app_spec.title_hints)}".casefold()
        return any(token in fingerprint for token in ("kimi", "chat", "claude", "gpt", "gemini"))

    def _focus_first_editable_control(self, hwnd: int) -> str | None:
        try:
            automation = self._create_uia_automation()
            root = automation.ElementFromHandle(hwnd)
            condition = automation.CreatePropertyCondition(
                self.UIA_CONTROL_TYPE_PROPERTY_ID,
                self.UIA_EDIT_CONTROL_TYPE_ID,
            )
            elements = root.FindAll(self.UIA_TREE_SCOPE_DESCENDANTS, condition)
            for index in range(int(elements.Length)):
                element = elements.GetElement(index)
                try:
                    if hasattr(element, "CurrentIsEnabled") and not bool(element.CurrentIsEnabled):
                        continue
                    element.SetFocus()
                    rect = element.CurrentBoundingRectangle
                    left = int(getattr(rect, "left", getattr(rect, "Left", 0)))
                    top = int(getattr(rect, "top", getattr(rect, "Top", 0)))
                    right = int(getattr(rect, "right", getattr(rect, "Right", 0)))
                    bottom = int(getattr(rect, "bottom", getattr(rect, "Bottom", 0)))
                    if right > left and bottom > top:
                        ctypes.windll.user32.SetCursorPos((left + right) // 2, (top + bottom) // 2)
                        self._send_mouse_input(self.MOUSEEVENTF_LEFTDOWN)
                        self._send_mouse_input(self.MOUSEEVENTF_LEFTUP)
                    return "uia_edit_control"
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def _read_uia_text(self, hwnd: int, *, limit: int) -> str:
        try:
            automation = self._create_uia_automation()
            root = automation.ElementFromHandle(hwnd)
            condition = automation.CreateTrueCondition()
            elements = root.FindAll(self.UIA_TREE_SCOPE_DESCENDANTS, condition)
            parts: list[str] = []
            seen: set[str] = set()
            root_name = self._safe_uia_text(getattr(root, "CurrentName", ""))
            if root_name:
                seen.add(root_name)
                parts.append(root_name)
            max_items = min(int(elements.Length), 500)
            for index in range(max_items):
                element = elements.GetElement(index)
                for value in self._iter_uia_element_text_values(element):
                    if not value or value in seen:
                        continue
                    seen.add(value)
                    parts.append(value)
                    if sum(len(item) + 1 for item in parts) >= limit:
                        return "\n".join(parts)[:limit]
            return "\n".join(parts)[:limit]
        except Exception as exc:
            raise LocalHostControlError(f"UI Automation text read failed: {exc}") from exc

    @staticmethod
    def _create_uia_automation() -> Any:
        import comtypes.client
        from comtypes import GUID

        try:
            return comtypes.client.CreateObject("UIAutomationClient.CUIAutomation")
        except Exception:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import IUIAutomation

            return comtypes.client.CreateObject(
                GUID("{ff48dba4-60ef-4201-aa87-54103eef594e}"),
                interface=IUIAutomation,
            )

    def _iter_uia_element_text_values(self, element: Any) -> list[str]:
        values: list[str] = []
        for attr in ("CurrentName", "CurrentHelpText", "CurrentItemStatus"):
            values.append(self._safe_uia_text(getattr(element, attr, "")))
        try:
            value_pattern = element.GetCurrentPattern(self.UIA_VALUE_PATTERN_ID)
            values.append(self._safe_uia_text(getattr(value_pattern, "CurrentValue", "")))
        except Exception:
            pass
        return [item for item in values if item]

    @staticmethod
    def _safe_uia_text(value: object) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ""
        return text[:4000]

    @classmethod
    def _normalize_key_name(cls, value: str) -> str:
        text = value.strip().upper()
        return text.replace(" ", "")

    @classmethod
    def _resolve_virtual_key_code(cls, key_name: str) -> int:
        mapped = cls.VK_CODE_MAP.get(key_name)
        if mapped is not None:
            return mapped
        if len(key_name) == 1 and key_name.isalnum():
            return ord(key_name.upper())
        raise LocalHostControlError(f"Unsupported key name '{key_name}'.")

    @staticmethod
    def _get_foreground_hwnd() -> int:
        try:
            return int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            return 0

    @staticmethod
    def _read_window_title(hwnd: int) -> str:
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return str(buffer.value).strip()

    @staticmethod
    def _resolve_process_name(pid: int) -> str:
        if pid <= 0:
            return ""
        try:
            return psutil.Process(pid).name()
        except Exception:
            return ""

    @staticmethod
    def _get_window_rect(hwnd: int) -> _RECT:
        rect = _RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise LocalHostControlError("Could not read the foreground window rectangle.")
        return rect

    def _run_window_capture_script(
        self,
        screenshot_path: Path,
        *,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            raise LocalHostControlError("PowerShell is not available on this host.")
        safe_path = str(screenshot_path).replace("'", "''")
        script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing
$left = {int(left)}
$top = {int(top)}
$width = {int(width)}
$height = {int(height)}
$bitmap = New-Object System.Drawing.Bitmap([Math]::Max(1, $width), [Math]::Max(1, $height))
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($left, $top, 0, 0, $bitmap.Size)
$bitmap.Save('{safe_path}', [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
"""
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            cwd=str(self.settings.app_root),
        )
        if result.returncode != 0:
            raise LocalHostControlError(result.stderr.strip() or result.stdout.strip() or "Foreground window capture failed.")
        if not screenshot_path.exists():
            raise LocalHostControlError("Foreground window capture did not produce a screenshot file.")

    @staticmethod
    def _is_elevated() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
