from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class RuntimeComponentHealth:
    key: str
    label: str
    status: str
    detail: str
    next_action: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        evidence = {
            "source": self.key,
            "status": self.status,
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "verdict": self._verdict(),
            "detail": self.detail,
            "next_action": self.next_action,
            "first_action": self.next_action,
            "evidence": evidence,
            "metadata": dict(self.metadata),
        }

    def _verdict(self) -> str:
        if self.status == "ready":
            return "ready"
        if self.status == "degraded":
            return "degraded"
        return "blocked"


class RuntimeHealthService:
    OPTIONAL_COMPONENT_KEYS = {
        "f5_tts",
        "gmail_connection",
        "youtube_connection",
        "xtb_demo_market_data",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def snapshot(self, *, session: Any | None = None) -> dict[str, Any]:
        components = [
            self._backend(),
            self._frontend(),
            self._desktop_web(),
            self._desktop_shell(),
            self._runtime_watchdog(),
            self._executable("python", "Python", sys.executable, ["--version"]),
            self._executable("node", "Node.js", self._known_executable("node"), ["--version"]),
            self._executable("pnpm", "pnpm package manager", self._known_executable("pnpm"), ["--version"]),
            self._executable("git", "Git", self._known_executable("git"), ["--version"]),
            self._executable("ffmpeg", "FFmpeg", self._known_executable("ffmpeg"), ["-version"]),
            self._ollama(),
            self._http("comfyui", "ComfyUI", f"{self.settings.comfyui_base_url.rstrip('/')}/system_stats"),
            self._f5_tts(),
        ]
        components.append(self._local_host_control(session=session))
        components.extend(self._connection_components(session=session))
        blockers = [item for item in components if self._is_core_blocker(item)]
        degraded = [item for item in components if item.status == "degraded"]
        optional_unavailable = [
            item
            for item in components
            if item.key in self.OPTIONAL_COMPONENT_KEYS and item.status in {"missing", "blocked"}
        ]
        overall = "blocked" if blockers else "degraded" if degraded or optional_unavailable else "ready"
        readiness = self._readiness_summary(components=components, status=overall)
        return {
            "status": overall,
            "components": [item.to_dict() for item in components],
            "summary": {
                "ready": sum(1 for item in components if item.status == "ready"),
                "degraded": len(degraded),
                "missing": sum(1 for item in components if item.status == "missing"),
                "blocked": sum(1 for item in components if item.status == "blocked"),
            },
            "readiness": readiness,
            "launcher": self._launcher_metadata(),
        }

    def _backend(self) -> RuntimeComponentHealth:
        return RuntimeComponentHealth(
            key="backend",
            label="FastAPI backend",
            status="ready",
            detail="Backend is serving this health request.",
            next_action="No action required.",
            metadata={"app_env": self.settings.app_env, "api_port": self.settings.api_port},
        )

    def _frontend(self) -> RuntimeComponentHealth:
        frontend_dir = self._frontend_dir()
        package_json = frontend_dir / "package.json"
        dist_dir = frontend_dir / "dist"
        if not package_json.exists():
            return RuntimeComponentHealth(
                key="frontend",
                label="React frontend",
                status="missing",
                detail=f"package.json not found at {package_json}.",
                next_action="Restore frontend directory or correct app_root.",
                metadata={"path": str(frontend_dir)},
            )
        status = "ready" if dist_dir.exists() else "degraded"
        return RuntimeComponentHealth(
            key="frontend",
            label="React frontend build",
            status=status,
            detail="Frontend dist exists." if status == "ready" else "Frontend package exists but dist/ is missing.",
            next_action=(
                "Run frontend build, then deploy it to desktop-web."
                if status == "degraded"
                else "No action required."
            ),
            metadata={"path": str(frontend_dir), "dist_exists": dist_dir.exists()},
        )

    def _desktop_web(self) -> RuntimeComponentHealth:
        repo_root = self._repo_root()
        desktop_web_dir = repo_root / "desktop-web"
        desktop_index = desktop_web_dir / "index.html"
        frontend_dist_index = self._frontend_dir() / "dist" / "index.html"
        if not desktop_index.exists():
            return RuntimeComponentHealth(
                key="desktop_web",
                label="Desktop web bundle",
                status="missing",
                detail=f"desktop-web index.html not found at {desktop_index}.",
                next_action='Run scripts/deploy_frontend_to_desktop_web.ps1 -Build before using Atnos Desktop.',
                metadata={"path": str(desktop_web_dir), "index_exists": False},
            )

        desktop_mtime = desktop_index.stat().st_mtime
        frontend_mtime = frontend_dist_index.stat().st_mtime if frontend_dist_index.exists() else None
        stale = frontend_mtime is not None and desktop_mtime + 1 < frontend_mtime
        status = "degraded" if stale else "ready"
        return RuntimeComponentHealth(
            key="desktop_web",
            label="Desktop web bundle",
            status=status,
            detail=(
                "desktop-web exists but is older than frontend/dist."
                if stale
                else "desktop-web is present for the desktop shell."
            ),
            next_action=(
                'Run scripts/deploy_frontend_to_desktop_web.ps1 -Build to update the desktop app bundle.'
                if stale
                else "No action required."
            ),
            metadata={
                "path": str(desktop_web_dir),
                "index_exists": True,
                "frontend_dist_index_exists": frontend_dist_index.exists(),
                "stale_vs_frontend_dist": stale,
            },
        )

    def _desktop_shell(self) -> RuntimeComponentHealth:
        repo_root = self._repo_root()
        launcher = repo_root / "Launch-Atnos.ps1"
        official_entrypoint = repo_root / "Start-Atnos.vbs"
        managed_shell = repo_root / "Atnos Shell.exe"
        desktop_shell = repo_root / "Atnos Desktop.exe"
        portable_shell = repo_root / "Atnos Portable.exe"
        legacy_shells = [path for path in (desktop_shell, portable_shell) if path.exists()]
        available_shells = [path for path in (managed_shell, *legacy_shells) if path.exists()]
        if not launcher.exists():
            return RuntimeComponentHealth(
                key="desktop_shell",
                label="Atnos desktop shell",
                status="missing",
                detail=f"Launch-Atnos.ps1 not found at {launcher}.",
                next_action="Restore Launch-Atnos.ps1 so the app can start backend, web shell and WebView.",
                metadata={
                    "official_entrypoint": str(official_entrypoint),
                    "launcher_path": str(launcher),
                    "managed_shell": str(managed_shell),
                    "legacy_shells": [str(path) for path in legacy_shells],
                    "available_shells": [str(path) for path in available_shells],
                },
            )

        official_ready = official_entrypoint.exists() and managed_shell.exists()
        if official_ready:
            status = "ready"
            detail = "Official app chain is available: Start-Atnos.vbs -> Launch-Atnos.ps1 -> Atnos Shell.exe."
            next_action = "Start ATNOS from the ATNOS desktop shortcut; do not run shell executables directly."
        elif available_shells:
            status = "degraded"
            detail = "A shell executable exists, but the official Start-Atnos.vbs entrypoint is incomplete."
            next_action = "Restore Start-Atnos.vbs and Atnos Shell.exe before using the desktop app."
        else:
            status = "missing"
            detail = "The launcher exists, but the managed desktop shell is missing."
            next_action = "Build or restore Atnos Shell.exe."

        return RuntimeComponentHealth(
            key="desktop_shell",
            label="Atnos desktop shell",
            status=status,
            detail=detail,
            next_action=next_action,
            metadata={
                "official_entrypoint": str(official_entrypoint),
                "launcher_path": str(launcher),
                "managed_shell": str(managed_shell),
                "legacy_shells": [str(path) for path in legacy_shells],
                "available_shells": [str(path) for path in available_shells],
            },
        )

    def _runtime_watchdog(self) -> RuntimeComponentHealth:
        """Odczytaj swiezy heartbeat zapisany przez lokalny proces watchdoga."""
        script_path = self._repo_root() / "AtnosRuntimeWatchdog.ps1"
        state_path = self.settings.data_dir.parent / "runtime" / "runtime-watchdog.state.json"
        if not script_path.exists():
            return RuntimeComponentHealth(
                key="runtime_watchdog",
                label="Runtime watchdog",
                status="missing",
                detail=f"Watchdog script not found at {script_path}.",
                next_action="Restore AtnosRuntimeWatchdog.ps1, then relaunch Atnos Desktop.",
                metadata={"script_path": str(script_path), "script_exists": False},
            )

        if not state_path.exists():
            return RuntimeComponentHealth(
                key="runtime_watchdog",
                label="Runtime watchdog",
                status="missing",
                detail="Watchdog script exists, but no runtime heartbeat was found.",
                next_action="Start ATNOS from the official desktop shortcut to launch the runtime watchdog.",
                metadata={"script_path": str(script_path), "script_exists": True, "state_path": str(state_path)},
            )

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8-sig"))
            heartbeat_raw = str(payload.get("heartbeat_at") or "")
            heartbeat_at = datetime.fromisoformat(heartbeat_raw.replace("Z", "+00:00"))
            if heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (datetime.now(timezone.utc) - heartbeat_at.astimezone(timezone.utc)).total_seconds())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return RuntimeComponentHealth(
                key="runtime_watchdog",
                label="Runtime watchdog",
                status="degraded",
                detail=f"{type(exc).__name__}: watchdog heartbeat could not be parsed.",
                next_action="Restart Atnos Desktop so the watchdog can write a fresh heartbeat.",
                metadata={"script_path": str(script_path), "script_exists": True, "state_path": str(state_path)},
            )

        metadata = {
            "script_path": str(script_path),
            "script_exists": True,
            "state_path": str(state_path),
            "process_id": payload.get("pid"),
            "watchdog_status": payload.get("status"),
            "heartbeat_at": heartbeat_at.isoformat(),
            "heartbeat_age_seconds": round(age_seconds, 2),
            "api_ready": bool(payload.get("api_ready")),
            "web_ready": bool(payload.get("web_ready")),
            "last_recovery_at": payload.get("last_recovery_at"),
        }
        if age_seconds > 90:
            return RuntimeComponentHealth(
                key="runtime_watchdog",
                label="Runtime watchdog",
                status="missing",
                detail=f"Watchdog heartbeat is stale ({round(age_seconds, 1)} seconds old).",
                next_action="Restart ATNOS from the official desktop shortcut to refresh the watchdog.",
                metadata=metadata,
            )

        return RuntimeComponentHealth(
            key="runtime_watchdog",
            label="Runtime watchdog",
            status="ready" if payload.get("status") == "ready" else "degraded",
            detail=(
                f"Fresh watchdog heartbeat from PID={payload.get('pid')} ({round(age_seconds, 1)} seconds ago)."
                if payload.get("status") == "ready"
                else "Watchdog heartbeat is fresh and is recovering local services."
            ),
            next_action="No action required." if payload.get("status") == "ready" else "Wait for the local API and web shell recovery to finish.",
            metadata=metadata,
        )

    def _executable(self, key: str, label: str, executable: str | None, args: list[str]) -> RuntimeComponentHealth:
        if not executable:
            return RuntimeComponentHealth(
                key=key,
                label=label,
                status="missing",
                detail=f"{label} executable was not found in PATH.",
                next_action=f"Install {label} or add it to PATH.",
                metadata={},
            )
        try:
            result = subprocess.run(
                [executable, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
            first_line = (result.stdout or result.stderr or "").splitlines()[0] if (result.stdout or result.stderr) else ""
            return RuntimeComponentHealth(
                key=key,
                label=label,
                status="ready" if result.returncode == 0 else "degraded",
                detail=first_line or f"{label} command returned code {result.returncode}.",
                next_action="No action required." if result.returncode == 0 else f"Check {label} installation.",
                metadata={"path": executable, "return_code": result.returncode},
            )
        except Exception as exc:
            return RuntimeComponentHealth(
                key=key,
                label=label,
                status="degraded",
                detail=f"{type(exc).__name__}: {exc}",
                next_action=f"Check {label} installation.",
                metadata={"path": executable},
            )

    def _frontend_dir(self) -> Path:
        candidates = [
            self.settings.app_root / "frontend",
            self.settings.app_root.parent / "frontend",
            self.settings.app_root.parent.parent / "frontend",
        ]
        for candidate in candidates:
            if (candidate / "package.json").exists():
                return candidate
        return candidates[0]

    def _repo_root(self) -> Path:
        frontend_dir = self._frontend_dir()
        candidates = [
            self.settings.app_root,
            frontend_dir.parent,
            self.settings.app_root.parent,
            self.settings.app_root.parent.parent,
        ]
        for candidate in candidates:
            if (candidate / "Launch-Atnos.ps1").exists() or (candidate / "desktop-web").exists():
                return candidate
        return frontend_dir.parent

    def _known_executable(self, key: str) -> str | None:
        if key == "ffmpeg" and self.settings.ffmpeg_exe:
            configured = Path(self.settings.ffmpeg_exe).expanduser()
            if configured.exists() or shutil.which(self.settings.ffmpeg_exe):
                return str(configured) if configured.exists() else shutil.which(self.settings.ffmpeg_exe)
        if key == "git":
            # PATH can retain dead pre-migration entries. Prefer the
            # canonical Atnos installation before consulting inherited PATH.
            canonical_git = Path("E:/Atnos/Tools/Git/cmd/git.exe")
            if canonical_git.exists():
                return str(canonical_git)
        detected = shutil.which(key)
        if detected:
            return detected
        codex_runtime = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
        fallbacks = {
            "node": (codex_runtime / "node" / "bin" / "node.exe",),
            "pnpm": (
                codex_runtime / "bin" / "fallback" / "pnpm.cmd",
                codex_runtime / "bin" / "pnpm.cmd",
            ),
            "git": (codex_runtime / "native" / "git" / "cmd" / "git.exe",),
        }
        for fallback in fallbacks.get(key, ()):
            if fallback.exists():
                return str(fallback)
        if key == "ffmpeg":
            try:
                import imageio_ffmpeg

                imageio_ffmpeg_path = Path(imageio_ffmpeg.get_ffmpeg_exe())
                if imageio_ffmpeg_path.exists():
                    return str(imageio_ffmpeg_path)
            except Exception:
                return None
        return None

    def _f5_tts_health_url(self) -> str:
        configured = self.settings.f5_tts_base_url.strip() or "http://127.0.0.1:7860/"
        parsed = urlsplit(configured)
        if parsed.scheme and parsed.netloc and parsed.path.rstrip("/") == "/api/predict":
            return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        return configured

    def _f5_tts(self) -> RuntimeComponentHealth:
        """Treat an installed, stopped F5-TTS runtime as an on-demand capability."""

        url = self._f5_tts_health_url()
        try:
            with httpx.Client(timeout=1.0) as client:
                response = client.get(url)
            if 200 <= response.status_code < 300:
                return RuntimeComponentHealth(
                    key="f5_tts",
                    label="F5-TTS",
                    status="ready",
                    detail=f"F5-TTS is running on demand (HTTP {response.status_code}).",
                    next_action="No action required.",
                    metadata={
                        "url": url,
                        "status_code": response.status_code,
                        "mode": "on_demand",
                        "required_for_core": False,
                    },
                )
            endpoint_error = f"HTTP {response.status_code}"
        except Exception as exc:
            endpoint_error = f"{type(exc).__name__}: {exc}"

        try:
            from atnos.services.f5_tts_runtime_service import F5TTSRuntimeService

            runtime = F5TTSRuntimeService(self.settings)
            install_dir = runtime.resolve_install_dir()
            runtime_python = runtime.resolve_runtime_python(install_dir)
            reference_audio = runtime.resolve_reference_audio(install_dir)
            installed = bool(
                install_dir
                and runtime_python
                and runtime_python.exists()
                and str(self.settings.f5_tts_launch_command or "").strip()
            )
        except Exception:
            install_dir = None
            runtime_python = None
            reference_audio = None
            installed = False

        if installed:
            return RuntimeComponentHealth(
                key="f5_tts",
                label="F5-TTS",
                status="degraded",
                detail="F5-TTS is installed and intentionally stopped until voice generation needs it.",
                next_action="Stop ComfyUI, then start F5-TTS on demand before premium voice generation.",
                metadata={
                    "url": url,
                    "mode": "on_demand",
                    "endpoint_ready": False,
                    "endpoint_error": endpoint_error,
                    "install_dir": str(install_dir),
                    "runtime_python": str(runtime_python),
                    "reference_audio_ready": bool(reference_audio and reference_audio.exists()),
                    "required_for_core": False,
                },
            )

        return RuntimeComponentHealth(
            key="f5_tts",
            label="F5-TTS",
            status="missing",
            detail=f"F5-TTS on-demand runtime is unavailable ({endpoint_error}).",
            next_action="Install and configure F5-TTS before using local premium voice generation.",
            metadata={
                "url": url,
                "mode": "on_demand",
                "endpoint_ready": False,
                "endpoint_error": endpoint_error,
                "required_for_core": False,
            },
        )

    def _is_core_blocker(self, item: RuntimeComponentHealth) -> bool:
        return item.status in {"missing", "blocked"} and item.key not in self.OPTIONAL_COMPONENT_KEYS

    def _launcher_metadata(self) -> dict[str, Any]:
        repo_root = self._repo_root()
        launcher = repo_root / "Launch-Atnos.ps1"
        dev_launcher = repo_root / "scripts" / "Start-AtnosRuntime.ps1"
        command = f'powershell -ExecutionPolicy Bypass -File "{launcher}"'
        return {
            "script_path": str(launcher),
            "exists": launcher.exists(),
            "command": command,
            "log_dir": str(self.settings.data_dir.parent / "runtime"),
            "dev_script_path": str(dev_launcher),
            "dev_script_exists": dev_launcher.exists(),
        }

    def _http(self, key: str, label: str, url: str) -> RuntimeComponentHealth:
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(url)
            ready = 200 <= response.status_code < 300
            detail = f"HTTP {response.status_code} from {url}."
            metadata: dict[str, Any] = {"url": url, "status_code": response.status_code}
            if ready and key == "comfyui":
                # A proxy/login page can return 200 too. Check the endpoint's
                # stable fields, without requiring a GPU or a specific version.
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                system = payload.get("system") if isinstance(payload, dict) else None
                devices = payload.get("devices") if isinstance(payload, dict) else None
                ready = (
                    isinstance(system, dict)
                    and isinstance(system.get("os"), str)
                    and bool(system["os"].strip())
                    and isinstance(devices, list)
                    and bool(devices)
                    and all(
                        isinstance(device, dict)
                        and isinstance(device.get("name"), str)
                        and bool(device["name"].strip())
                        and isinstance(device.get("type"), str)
                        and bool(device["type"].strip())
                        for device in devices
                    )
                )
                metadata["contract_valid"] = ready
                if not ready:
                    detail += " Response does not match the ComfyUI system_stats contract."
            return RuntimeComponentHealth(
                key=key,
                label=label,
                status="ready" if ready else "blocked",
                detail=detail,
                next_action="No action required." if ready else f"Check {label} endpoint configuration, access and runtime logs.",
                metadata=metadata,
            )
        except Exception as exc:
            return RuntimeComponentHealth(
                key=key,
                label=label,
                status="missing",
                detail=f"{type(exc).__name__}: {exc}",
                next_action=f"Start {label} runtime or disable features that depend on it.",
                metadata={"url": url},
            )

    def _ollama(self) -> RuntimeComponentHealth:
        """Report Ollama as ready only when its model catalog contains a usable model."""
        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/tags"
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(url)
        except Exception as exc:
            return RuntimeComponentHealth(
                key="ollama",
                label="Ollama",
                status="missing",
                detail=f"{type(exc).__name__}: {exc}",
                next_action="Start Ollama, install a local model, then refresh runtime health.",
                metadata={"url": url},
            )

        if response.status_code != 200:
            return RuntimeComponentHealth(
                key="ollama",
                label="Ollama",
                status="degraded",
                detail=f"HTTP {response.status_code} from {url}; model catalog is unavailable.",
                next_action="Check Ollama runtime logs, then refresh runtime health.",
                metadata={"url": url, "status_code": response.status_code},
            )

        try:
            payload = response.json()
        except ValueError:
            return RuntimeComponentHealth(
                key="ollama",
                label="Ollama",
                status="degraded",
                detail="Ollama responded, but its model catalog could not be parsed.",
                next_action="Restart Ollama and refresh runtime health.",
                metadata={"url": url, "status_code": response.status_code},
            )

        raw_models = payload.get("models", []) if isinstance(payload, dict) else []
        installed_models = [
            str(item.get("name") or item.get("model") or "").strip()
            for item in raw_models
            if isinstance(item, dict)
            and str(item.get("name") or item.get("model") or "").strip()
        ]
        metadata = {
            "url": url,
            "status_code": response.status_code,
            "model_count": len(installed_models),
            "models": installed_models[:12],
        }
        if not installed_models:
            return RuntimeComponentHealth(
                key="ollama",
                label="Ollama",
                status="missing",
                detail="Ollama runtime is online, but no local model is installed.",
                next_action="Install a local Ollama model in Settings > Runtime, then refresh runtime health.",
                metadata=metadata,
            )

        return RuntimeComponentHealth(
            key="ollama",
            label="Ollama",
            status="ready",
            detail=f"Ollama runtime is online with {len(installed_models)} installed model(s).",
            next_action="No action required.",
            metadata=metadata,
        )

    def _local_host_control(self, *, session: Any | None) -> RuntimeComponentHealth:
        if session is None:
            return RuntimeComponentHealth(
                key="local_host_control",
                label="Local host control",
                status="degraded",
                detail="Database session is not available for local host capability checks.",
                next_action="Refresh runtime after backend database is available.",
                metadata={"source": "runtime_health_service"},
            )
        try:
            from atnos.services.local_host_control_service import LocalHostControlService

            service = LocalHostControlService(session, settings=self.settings)
            snapshot = service.get_capability_snapshot()
            apps = service.list_desktop_apps()
            important_keys = {
                "atnos-desktop",
                "comfy-desktop",
                "kimi",
                "capcut",
                "github-desktop",
                "docker-desktop",
                "git-bash",
            }
            important_apps = [app for app in apps if app.app_key in important_keys]
            installed_count = sum(1 for app in important_apps if app.installed)
            running_count = sum(1 for app in important_apps if app.running)
            if not snapshot.execution_enabled:
                component_status = "blocked"
                next_action = "Disable emergency stop or pause-all before running local host actions."
            elif not snapshot.desktop_input_available:
                component_status = "degraded"
                next_action = "Run Atnos on Windows desktop host to enable mouse and keyboard control."
            else:
                component_status = "ready"
                next_action = "No action required."
            return RuntimeComponentHealth(
                key="local_host_control",
                label="Local host control",
                status=component_status,
                detail=(
                    f"execution_enabled={snapshot.execution_enabled}; "
                    f"desktop_input={snapshot.desktop_input_available}; "
                    f"important_apps_installed={installed_count}/{len(important_keys)}; "
                    f"important_apps_running={running_count}/{len(important_keys)}."
                ),
                next_action=next_action,
                metadata={
                    "host_label": snapshot.host_label,
                    "platform": snapshot.platform,
                    "execution_enabled": snapshot.execution_enabled,
                    "trusted_auto_enabled": snapshot.trusted_auto_enabled,
                    "desktop_input_available": snapshot.desktop_input_available,
                    "browser_recipe_available": snapshot.browser_recipe_available,
                    "command_log_path": snapshot.command_log_path,
                    "important_apps": [
                        {
                            "app_key": app.app_key,
                            "label": app.label,
                            "installed": app.installed,
                            "running": app.running,
                            "window_visible": app.window_visible,
                            "launch_supported": app.launch_supported,
                        }
                        for app in important_apps
                    ],
                },
            )
        except Exception as exc:
            return RuntimeComponentHealth(
                key="local_host_control",
                label="Local host control",
                status="degraded",
                detail=f"{type(exc).__name__}: {exc}",
                next_action="Open Local Host diagnostics and repair desktop control configuration.",
                metadata={"source": "local_host_control_service"},
            )

    def _connection_components(self, *, session: Any | None) -> list[RuntimeComponentHealth]:
        components: list[RuntimeComponentHealth] = [self._xtb_demo_connection()]
        if session is None:
            components.extend(
                [
                    RuntimeComponentHealth(
                        key="gmail_connection",
                        label="Gmail OAuth",
                        status="degraded",
                        detail="Database session is not available for OAuth connection status.",
                        next_action="Open Settings and refresh runtime after backend database is available.",
                        metadata={"source": "runtime_health_service"},
                    ),
                    RuntimeComponentHealth(
                        key="youtube_connection",
                        label="YouTube OAuth",
                        status="degraded",
                        detail="Database session is not available for OAuth connection status.",
                        next_action="Open Settings and refresh runtime after backend database is available.",
                        metadata={"source": "runtime_health_service"},
                    ),
                ]
            )
            return components
        components.append(self._gmail_connection(session=session))
        components.append(self._youtube_connection(session=session))
        return components

    def _gmail_connection(self, *, session: Any) -> RuntimeComponentHealth:
        try:
            from atnos.services.gmail_connector_service import GmailConnectorService

            status = GmailConnectorService(session, self.settings).get_status()
            ready = bool(status.connected and status.live_read_enabled and status.live_write_enabled)
            configured = bool(status.provider_enabled and status.provider_configured)
            component_status = "ready" if ready else "degraded" if configured else "missing"
            detail = (
                f"OAuth {status.oauth_status}; connected={status.connected}; "
                f"read={status.live_read_enabled}; write={status.live_write_enabled}."
            )
            next_action = (
                "No action required."
                if ready
                else "Reconnect Gmail OAuth with gmail.readonly, gmail.compose and gmail.send scopes."
                if configured
                else "Configure Gmail OAuth client secret and connect Gmail."
            )
            return RuntimeComponentHealth(
                key="gmail_connection",
                label="Gmail OAuth",
                status=component_status,
                detail=detail,
                next_action=next_action,
                metadata={
                    "connector_key": status.connector_key,
                    "connection_id": status.connection_id,
                    "account_id": status.account_id,
                    "provider_configured": status.provider_configured,
                    "token_present": status.token_present,
                    "granted_scopes": list(status.granted_scopes),
                    "warnings": list(status.warnings),
                    "checked_at": status.checked_at.isoformat(),
                },
            )
        except Exception as exc:
            return RuntimeComponentHealth(
                key="gmail_connection",
                label="Gmail OAuth",
                status="degraded",
                detail=f"{type(exc).__name__}: {exc}",
                next_action="Open Gmail connector status and repair OAuth configuration.",
                metadata={"source": "gmail_connector_service"},
            )

    def _youtube_connection(self, *, session: Any) -> RuntimeComponentHealth:
        try:
            from atnos.services.youtube_connector_service import YouTubeConnectorService

            status = YouTubeConnectorService(session, self.settings).get_status()
            component_status = "ready" if status.ready else "degraded" if status.configured else "missing"
            detail = (
                f"configured={status.configured}; connected={status.connected}; "
                f"ready={status.ready}; warnings={len(status.warnings)}."
            )
            next_action = (
                "No action required."
                if status.ready
                else "Reconnect YouTube OAuth with upload, readonly and analytics scopes."
                if status.configured
                else "Configure YouTube OAuth client secret and connect YouTube Studio."
            )
            return RuntimeComponentHealth(
                key="youtube_connection",
                label="YouTube OAuth",
                status=component_status,
                detail=detail,
                next_action=next_action,
                metadata={
                    "connector_key": status.connector_key,
                    "account_id": status.account_id,
                    "configured": status.configured,
                    "connected": status.connected,
                    "ready": status.ready,
                    "granted_scopes": list(status.granted_scopes),
                    "warnings": list(status.warnings),
                },
            )
        except Exception as exc:
            return RuntimeComponentHealth(
                key="youtube_connection",
                label="YouTube OAuth",
                status="degraded",
                detail=f"{type(exc).__name__}: {exc}",
                next_action="Open YouTube connector status and repair OAuth configuration.",
                metadata={"source": "youtube_connector_service"},
            )

    def _xtb_demo_connection(self) -> RuntimeComponentHealth:
        try:
            from atnos.investment.cockpit import InvestmentCockpitService

            status = InvestmentCockpitService(self.settings).xtb_market_data_status()
            read_only_verified = bool(status.get("read_only_verified"))
            registered = bool(status.get("registered"))
            api_available = bool(status.get("api_available", True))
            component_status = (
                "blocked"
                if not api_available
                else "ready"
                if read_only_verified
                else "degraded"
                if registered
                else "missing"
            )
            detail = (
                "XTB retired public xAPI on 2025-03-14; direct demo read-only API is unavailable."
                if not api_available
                else f"registered={registered}; read_only_verified={read_only_verified}; "
                f"real_trading_enabled={bool(status.get('real_trading_enabled'))}."
            )
            next_action = (
                "No action required."
                if read_only_verified
                else str(status.get("next_action") or "Register XTB demo metadata and verify read-only access.")
            )
            return RuntimeComponentHealth(
                key="xtb_demo_market_data",
                label="XTB Demo market data",
                status=component_status,
                detail=detail,
                next_action=next_action,
                metadata={
                    "provider_key": status.get("provider_key"),
                    "environment": status.get("environment"),
                    "registered": registered,
                    "api_available": api_available,
                    "provider_status": status.get("provider_status"),
                    "api_retired_at": status.get("api_retired_at"),
                    "retirement_source": status.get("retirement_source"),
                    "has_credential_ref": bool(status.get("has_credential_ref")),
                    "broker_read_access": status.get("broker_read_access"),
                    "broker_status": status.get("broker_status"),
                    "read_only_verified": read_only_verified,
                    "last_check": status.get("last_check"),
                    "allowed_commands": status.get("allowed_commands"),
                    "forbidden_trading_commands": status.get("forbidden_trading_commands"),
                    "real_trading_enabled": bool(status.get("real_trading_enabled")),
                },
            )
        except Exception as exc:
            return RuntimeComponentHealth(
                key="xtb_demo_market_data",
                label="XTB Demo market data",
                status="degraded",
                detail=f"{type(exc).__name__}: {exc}",
                next_action="Open Portfolio Alpha settings and repair XTB demo metadata.",
                metadata={"source": "investment_cockpit"},
            )

    def _readiness_summary(self, *, components: list[RuntimeComponentHealth], status: str) -> dict[str, Any]:
        blocking = [item for item in components if self._is_core_blocker(item)]
        degraded = [item for item in components if item.status == "degraded"]
        optional_unavailable = [
            item
            for item in components
            if item.key in self.OPTIONAL_COMPONENT_KEYS and item.status in {"missing", "blocked"}
        ]
        first_issue = blocking[0] if blocking else degraded[0] if degraded else optional_unavailable[0] if optional_unavailable else None
        return {
            "verdict": "ready" if status == "ready" else "blocked" if blocking else "degraded",
            "can_run_core": status in {"ready", "degraded"},
            "headline": (
                "Runtime is ready."
                if status == "ready"
                else "Runtime has blocking missing components."
                if blocking
                else "Runtime is usable with degraded components."
            ),
            "first_action": first_issue.next_action if first_issue is not None else "No action required.",
            "blocking_components": [item.key for item in blocking],
            "degraded_components": [item.key for item in degraded],
            "optional_unavailable_components": [item.key for item in optional_unavailable],
            "evidence": {
                "source": "runtime_health_components",
                "component_count": len(components),
                "ready": sum(1 for item in components if item.status == "ready"),
                "degraded": len(degraded),
                "missing": sum(1 for item in components if item.status == "missing"),
                "blocked": sum(1 for item in components if item.status == "blocked"),
            },
        }
