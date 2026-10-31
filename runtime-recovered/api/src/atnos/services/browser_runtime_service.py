from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
import shutil
from pathlib import Path
import subprocess
import time
from typing import Literal
from uuid import uuid4

import httpx
import psutil

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class BrowserRuntimeState:
    profile_dir: Path
    downloads_dir: Path
    trace_dir: Path
    browsers_path: Path
    browser_executable: Path | None
    desktop_shell: Path | None
    ready: bool
    status_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_dir": self.profile_dir.as_posix(),
            "downloads_dir": self.downloads_dir.as_posix(),
            "trace_dir": self.trace_dir.as_posix(),
            "browsers_path": self.browsers_path.as_posix(),
            "browser_executable": self.browser_executable.as_posix()
            if self.browser_executable is not None
            else None,
            "desktop_shell": self.desktop_shell.as_posix() if self.desktop_shell is not None else None,
            "ready": self.ready,
            "status_reason": self.status_reason,
        }


ChromeBridgeStatus = Literal["already_live", "launched", "restarted_existing"]


@dataclass(frozen=True, slots=True)
class ChromeBridgeLaunchResult:
    ok: bool
    status: ChromeBridgeStatus
    port: int
    browser_executable: Path
    using_default_profile: bool
    restarted_existing: bool
    command: tuple[str, ...]
    notes: tuple[str, ...]
    started_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "status": self.status,
            "port": self.port,
            "browser_executable": self.browser_executable.as_posix(),
            "using_default_profile": self.using_default_profile,
            "restarted_existing": self.restarted_existing,
            "command": list(self.command),
            "notes": list(self.notes),
            "started_at": self.started_at,
        }


class BrowserRuntimeError(RuntimeError):
    pass


class BrowserRuntimeService:
    EXECUTABLE_CANDIDATES = (
        "chrome-win/chrome.exe",
        "chrome-win64/chrome.exe",
        "*/chrome-win/chrome.exe",
        "*/chrome-win64/chrome.exe",
        "chromium/chrome-win/chrome.exe",
        "chromium-*/chrome-win/chrome.exe",
        "chromium-*/chrome-win64/chrome.exe",
        "*/msedge/msedge.exe",
        "msedge/msedge.exe",
    )
    KNOWN_EXTERNAL_EXECUTABLES = (
        ("chrome", "Google/Chrome/Application/chrome.exe"),
        ("chrome-program-files", "Google/Chrome/Application/chrome.exe"),
        ("chrome-program-files-x86", "Google/Chrome/Application/chrome.exe"),
        ("brave", "BraveSoftware/Brave-Browser/Application/brave.exe"),
        ("opera-gx", "Programs/Opera GX/opera.exe"),
        ("opera", "Programs/Opera/opera.exe"),
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def describe(self) -> BrowserRuntimeState:
        browser_executable = self._resolve_browser_executable()
        desktop_shell = self._resolve_existing_file(self.settings.atnos_desktop_shell)
        if browser_executable is not None:
            managed_runtime = self._is_managed_path(browser_executable)
            runtime_name = self._runtime_name(browser_executable)
            return BrowserRuntimeState(
                profile_dir=self.settings.browser_profile_dir,
                downloads_dir=self.settings.browser_downloads_dir,
                trace_dir=self.settings.browser_trace_dir,
                browsers_path=self.settings.playwright_browsers_path,
                browser_executable=browser_executable,
                desktop_shell=desktop_shell,
                ready=True,
                status_reason=(
                    "F-only browser runtime is available."
                    if managed_runtime
                    else f"Dedicated {runtime_name} runtime is available. ATNOS profile, downloads and traces stay on F:."
                ),
            )
        if desktop_shell is not None:
            return BrowserRuntimeState(
                profile_dir=self.settings.browser_profile_dir,
                downloads_dir=self.settings.browser_downloads_dir,
                trace_dir=self.settings.browser_trace_dir,
                browsers_path=self.settings.playwright_browsers_path,
                browser_executable=None,
                desktop_shell=desktop_shell,
                ready=False,
                status_reason="Desktop shell is available on F:, but Playwright browser assets are missing.",
            )
        return BrowserRuntimeState(
            profile_dir=self.settings.browser_profile_dir,
            downloads_dir=self.settings.browser_downloads_dir,
            trace_dir=self.settings.browser_trace_dir,
            browsers_path=self.settings.playwright_browsers_path,
            browser_executable=None,
            desktop_shell=None,
            ready=False,
            status_reason="No F-only browser runtime was found yet.",
        )

    def stage_external_input(self, source: str | Path, *, bucket: str = "imports") -> Path:
        candidate = Path(source).expanduser()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Input file was not found: {candidate}")
        resolved = candidate.resolve()
        if self._is_managed_path(resolved):
            return resolved

        target_dir = (self.settings.workspace_staging_dir / bucket).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{uuid4().hex}{resolved.suffix.lower()}"
        shutil.copy2(resolved, target_path)
        return target_path.resolve()

    def ensure_chrome_devtools_bridge(
        self,
        *,
        port: int = 9222,
        restart_existing: bool = True,
        timeout_s: int = 20,
    ) -> ChromeBridgeLaunchResult:
        started_at = self._now().isoformat()
        normalized_port = int(port)
        if normalized_port < 1024 or normalized_port > 65535:
            raise BrowserRuntimeError("Chrome bridge port must be between 1024 and 65535.")

        existing_by_name = {
            process_name: self._list_browser_processes(process_name)
            for process_name in ("brave.exe", "chrome.exe", "msedge.exe")
        }
        existing = [proc for procs in existing_by_name.values() for proc in procs]
        existing_port = self._detect_remote_debugging_port(existing)
        if existing_port == normalized_port and self._is_devtools_port_live(existing_port):
            browser_executable = self._resolve_running_browser_executable(existing)
            if browser_executable is None:
                browser_executable = self.resolve_chrome_executable()
            if browser_executable is None:
                raise BrowserRuntimeError(
                    "Chromium DevTools bridge is live, but the browser executable could not be resolved."
                )
            return ChromeBridgeLaunchResult(
                ok=True,
                status="already_live",
                port=normalized_port,
                browser_executable=browser_executable,
                using_default_profile=not any(self._process_uses_profile(proc, self.settings.browser_profile_dir) for proc in existing),
                restarted_existing=False,
                command=(),
                notes=("Browser already exposes a live DevTools bridge on the requested port.",),
                started_at=started_at,
            )

        browser_executable = self.resolve_chrome_executable()
        if browser_executable is None:
            raise BrowserRuntimeError("Chromium browser executable (Brave/Chrome/Edge) was not found on this PC.")

        process_name = browser_executable.name.casefold()
        restarted_existing = False
        notes: list[str] = []
        matching_existing = existing_by_name.get(process_name) or []
        # A separate Atnos profile can coexist with the user's browser. Never
        # restart it, reuse its personal profile, or copy its cookies to Atnos.
        user_data_dir = self.settings.browser_profile_dir.resolve()
        if matching_existing:
            for process in matching_existing:
                if self._process_uses_profile(process, user_data_dir):
                    raise BrowserRuntimeError("Atnos browser profile is already in use without a live bridge; existing session preserved.")
            notes.append("Personal browser session preserved. This separate Atnos profile has no access to its signed-in tabs or cookies.")
        user_data_dir.mkdir(parents=True, exist_ok=True)

        command_parts = [
            str(browser_executable),
            f"--remote-debugging-port={normalized_port}",
            "--remote-debugging-address=127.0.0.1",
        ]
        command_parts.append(f"--user-data-dir={user_data_dir.as_posix()}")
        command_parts.append("--profile-directory=Default")
        command_parts.extend(
            [
                "--restore-last-session",
                "--new-window",
                "about:blank",
            ]
        )
        command = tuple(command_parts)
        self._launch_detached(command)
        self._wait_for_devtools_port(normalized_port, timeout_s=timeout_s)
        notes.append(f"{browser_executable.name} now runs with remote debugging enabled on localhost.")
        return ChromeBridgeLaunchResult(
            ok=True,
            status="restarted_existing" if restarted_existing else "launched",
            port=normalized_port,
            browser_executable=browser_executable,
            using_default_profile=False,
            restarted_existing=restarted_existing,
            command=command,
            notes=tuple(notes),
            started_at=started_at,
        )

    def _resolve_browser_executable(self) -> Path | None:
        explicit = self._resolve_existing_file(self.settings.browser_runtime_executable)
        if explicit is not None:
            return explicit

        managed = self.resolve_managed_browser_executable()
        if managed is not None:
            return managed

        return self._resolve_known_external_browser()

    def resolve_chrome_executable(self) -> Path | None:
        # Prefer Brave on this PC (Chromium family), then Chrome / Edge.
        for process_name in ("brave.exe", "chrome.exe", "msedge.exe"):
            running = self._resolve_running_browser_executable(self._list_browser_processes(process_name))
            if running is not None:
                return running

        local_appdata = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        candidates: list[Path] = []
        if local_appdata:
            candidates.extend(
                [
                    (Path(local_appdata) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe").resolve(),
                    (Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe").resolve(),
                    (Path(local_appdata) / "Microsoft" / "Edge" / "Application" / "msedge.exe").resolve(),
                ]
            )
        if program_files:
            candidates.extend(
                [
                    (Path(program_files) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe").resolve(),
                    (Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe").resolve(),
                    (Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe").resolve(),
                ]
            )
        if program_files_x86:
            candidates.extend(
                [
                    (Path(program_files_x86) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe").resolve(),
                    (Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe").resolve(),
                    (Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe").resolve(),
                ]
            )
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return self._resolve_known_external_browser()

    @staticmethod
    def _resolve_default_user_data_dir(browser_executable: Path) -> Path | None:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if not local_appdata:
            return None
        name = browser_executable.name.casefold()
        if name == "brave.exe":
            candidate = (Path(local_appdata) / "BraveSoftware" / "Brave-Browser" / "User Data").resolve()
        elif name == "msedge.exe":
            candidate = (Path(local_appdata) / "Microsoft" / "Edge" / "User Data").resolve()
        else:
            candidate = (Path(local_appdata) / "Google" / "Chrome" / "User Data").resolve()
        if candidate.exists() and candidate.is_dir():
            return candidate
        return None

    @staticmethod
    def _resolve_default_chrome_user_data_dir() -> Path | None:
        # Backward-compatible helper used by older callers/tests.
        local_appdata = os.environ.get("LOCALAPPDATA")
        if not local_appdata:
            return None
        for relative in (
            ("BraveSoftware", "Brave-Browser", "User Data"),
            ("Google", "Chrome", "User Data"),
            ("Microsoft", "Edge", "User Data"),
        ):
            candidate = (Path(local_appdata).joinpath(*relative)).resolve()
            if candidate.exists() and candidate.is_dir():
                return candidate
        return None

    def resolve_managed_browser_executable(self) -> Path | None:
        search_root = self.settings.playwright_browsers_path.resolve()
        for pattern in self.EXECUTABLE_CANDIDATES:
            matches = sorted(search_root.glob(pattern))
            if matches:
                return matches[0].resolve()
        return None

    def is_managed_runtime(self, candidate: Path) -> bool:
        return self._is_managed_path(candidate.resolve())

    def _resolve_known_external_browser(self) -> Path | None:
        local_appdata = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        roots = [
            Path(local_appdata).resolve() if local_appdata else None,
            Path(program_files).resolve() if program_files else None,
            Path(program_files_x86).resolve() if program_files_x86 else None,
        ]
        for _, relative_path in self.KNOWN_EXTERNAL_EXECUTABLES:
            for root in roots:
                if root is None:
                    continue
                candidate = (root / relative_path).resolve()
                if candidate.exists() and candidate.is_file():
                    return candidate
        return None

    @staticmethod
    def _runtime_name(path: Path) -> str:
        normalized = path.as_posix().lower()
        if "opera gx" in normalized:
            return "Opera GX"
        if "opera" in normalized:
            return "Opera"
        if "edge" in normalized or path.name.lower() == "msedge.exe":
            return "Microsoft Edge"
        return "browser"

    def _is_managed_path(self, candidate: Path) -> bool:
        roots = (
            self.settings.data_dir.resolve(),
            self.settings.app_root.resolve(),
        )
        for root in roots:
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _resolve_existing_file(path: Path | None) -> Path | None:
        if path is None:
            return None
        resolved = path.resolve()
        return resolved if resolved.exists() and resolved.is_file() else None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _list_browser_processes(process_name: str) -> list[psutil.Process]:
        normalized = process_name.strip().lower()
        processes: list[psutil.Process] = []
        for process in psutil.process_iter(["name", "exe", "cmdline"]):
            try:
                if str(process.info.get("name") or "").strip().lower() == normalized:
                    processes.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return processes

    @staticmethod
    def _process_uses_profile(process: psutil.Process, profile: Path) -> bool:
        try:
            args = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        for index, arg in enumerate(args):
            value = str(arg)
            if value.startswith("--user-data-dir="):
                raw_path = value.split("=", 1)[1]
            elif value == "--user-data-dir" and index + 1 < len(args):
                raw_path = str(args[index + 1])
            else:
                continue
            return Path(raw_path.strip('"')).resolve() == profile.resolve()
        return False

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

    @staticmethod
    def _resolve_running_browser_executable(processes: list[psutil.Process]) -> Path | None:
        for process in processes:
            try:
                executable = process.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            if not executable:
                continue
            candidate = Path(executable).resolve()
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _is_devtools_port_live(port: int) -> bool:
        try:
            with httpx.Client(timeout=1.5) as client:
                response = client.get(f"http://127.0.0.1:{port}/json/version")
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    def _wait_for_devtools_port(self, port: int, *, timeout_s: int) -> None:
        deadline = time.monotonic() + max(5, int(timeout_s))
        while time.monotonic() < deadline:
            if self._is_devtools_port_live(port):
                return
            time.sleep(0.4)
        raise BrowserRuntimeError(
            f"Chrome was launched, but the DevTools bridge on port {port} did not become ready in time."
        )

    @staticmethod
    def _launch_detached(command: tuple[str, ...]) -> None:
        if os.name == "nt":
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if powershell is not None:
                file_path = str(command[0]).replace("'", "''")
                escaped_arguments = ["'" + str(item).replace("'", "''") + "'" for item in command[1:]]
                argument_items = ",".join(escaped_arguments)
                ps_command = (
                    f"Start-Process -FilePath '{file_path}' "
                    f"-ArgumentList @({argument_items}) -WindowStyle Hidden"
                )
                result = subprocess.run(
                    [powershell, "-NoProfile", "-Command", ps_command],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                if result.returncode != 0:
                    raise BrowserRuntimeError(
                        f"Could not launch Chrome bridge via PowerShell: {(result.stderr or result.stdout or '').strip()}"
                    )
                return
        creationflags = 0
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            list(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )

    @staticmethod
    def _stop_browser_processes(process_name: str) -> None:
        raise BrowserRuntimeError("Name-based browser termination is disabled; existing sessions are preserved.")
