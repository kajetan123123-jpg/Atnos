from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import subprocess
from time import monotonic, sleep
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psutil

from atnos.services.runtime_stop_safety import gpu_stop_allowed, matching_python_processes
from atnos.core.config import Settings
from atnos.services.hardware_guard_service import HardwareGuardService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ComfyUiInstallState:
    install_dir: Path | None
    run_script: Path | None
    fallback_script: Path | None
    python_executable: Path | None
    main_script: Path | None
    checkpoint_dir: Path | None
    exists: bool


@dataclass(frozen=True, slots=True)
class ComfyUiRuntimeReadiness:
    image_generation: bool
    reason: str | None = None
    detail: str | None = None
    started: bool = False
    lowvram: bool = False
    install_dir: str | None = None
    main_script: str | None = None
    python_executable: str | None = None
    checkpoint_dir: str | None = None
    log_tail: str | None = None
    quarantined_custom_nodes: tuple[str, ...] = ()


class ComfyUiRuntimeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._active_base_url = str(settings.comfyui_base_url).strip().rstrip("/")

    @property
    def base_url(self) -> str:
        """Return the last verified Comfy endpoint, falling back to configuration."""

        return self._active_base_url or str(self.settings.comfyui_base_url).strip().rstrip("/")

    def _candidate_base_urls(self) -> list[str]:
        configured = str(self.settings.comfyui_base_url).strip().rstrip("/")
        candidates = [configured]
        try:
            parsed = urlsplit(configured)
        except ValueError:
            return candidates
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            return candidates
        host = parsed.hostname or "127.0.0.1"
        scheme = parsed.scheme or "http"
        for port in range(8188, 8196):
            candidate = urlunsplit((scheme, f"{host}:{port}", "", "", "")).rstrip("/")
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _remember_active_base_url(self, base_url: str) -> None:
        resolved = base_url.rstrip("/")
        self._active_base_url = resolved
        if str(self.settings.comfyui_base_url).rstrip("/") == resolved:
            return
        try:
            self.settings.comfyui_base_url = resolved
        except Exception:
            logger.debug("Could not update in-memory ComfyUI base URL", exc_info=True)
        logger.info("Discovered active Comfy Desktop endpoint at %s", resolved)

    def describe_install(self) -> ComfyUiInstallState:
        install_dir = self.resolve_install_dir()
        if install_dir is None:
            return ComfyUiInstallState(
                install_dir=None,
                run_script=None,
                fallback_script=None,
                python_executable=None,
                main_script=None,
                checkpoint_dir=None,
                exists=False,
            )
        run_script = self._existing_file(install_dir / "run_amd_gpu.bat")
        fallback_script = self._existing_file(install_dir / "run_amd_gpu_disable_smart_memory.bat")
        python_executable = next(
            (
                candidate
                for candidate in (
                    self._existing_file(install_dir / "python_embeded" / "python.exe"),
                    self._existing_file(install_dir / "ComfyUI" / ".venv" / "Scripts" / "python.exe"),
                    self._existing_file(install_dir / "standalone-env" / "python.exe"),
                )
                if candidate is not None
            ),
            None,
        )
        main_script = self._existing_file(install_dir / "ComfyUI" / "main.py")
        checkpoint_dir = self._existing_dir(install_dir / "ComfyUI" / "models" / "checkpoints")
        return ComfyUiInstallState(
            install_dir=install_dir,
            run_script=run_script,
            fallback_script=fallback_script,
            python_executable=python_executable,
            main_script=main_script,
            checkpoint_dir=checkpoint_dir,
            exists=True,
        )

    def resolve_install_dir(self) -> Path | None:
        candidates = [
            self.settings.comfyui_install_dir,
            self.settings.app_root.parent / "ComfyUI_windows_portable",
            self.settings.app_root.parent / "ComfyUI",
        ]
        if self.settings.app_env != "test":
            candidates.extend(
                [
                    Path("E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Installs/Atnos/ComfyUI"),
                ]
            )
        seen: set[str] = set()
        for candidate in candidates:
            if candidate is None:
                continue
            resolved = candidate.resolve()
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            if not resolved.exists() or not resolved.is_dir():
                continue
            if (resolved / "ComfyUI" / "main.py").exists():
                return resolved
        return None

    def list_checkpoint_candidates(self) -> list[Path]:
        state = self.describe_install()
        roots = [
            state.checkpoint_dir,
            self.settings.data_dir / "models" / "comfyui" / "checkpoints",
        ]
        if self.settings.app_env != "test":
            roots.extend(
                [
                    Path("E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Shared/models/checkpoints"),
                ]
            )
        candidates: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            if root is None or not root.exists():
                continue
            for pattern in ("*.safetensors", "*.ckpt", "*.pt", "*.pth"):
                for item in sorted(root.glob(pattern)):
                    key = str(item.resolve()).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(item.resolve())
        return candidates

    def ensure_startup_ready(self, *, wait_timeout_s: int = 180) -> ComfyUiRuntimeReadiness:
        lowvram = bool(self.settings.comfyui_lowvram)
        if self._server_ready():
            state = self.describe_install()
            return ComfyUiRuntimeReadiness(
                image_generation=True,
                reason=None,
                detail="ComfyUI already ready",
                lowvram=lowvram,
                **self._readiness_paths(state),
            )

        if self._f5_tts_ready():
            state = self.describe_install()
            return ComfyUiRuntimeReadiness(
                image_generation=False,
                reason="GPU runtime conflict",
                detail="F5-TTS is active. Stop F5-TTS before starting ComfyUI.",
                **self._readiness_paths(state),
            )

        state = self.describe_install()
        if not state.exists or state.install_dir is None:
            return ComfyUiRuntimeReadiness(
                image_generation=False,
                reason="ComfyUI startup failed",
                detail="ComfyUI install path was not found",
                log_tail=self.error_log_tail(),
            )

        if not self.auto_launch_supported(state):
            return ComfyUiRuntimeReadiness(
                image_generation=False,
                reason="Comfy Desktop instance is offline",
                detail=(
                    "Start the Atnos instance in Comfy Desktop. Its managed Python environment "
                    "will not be launched independently."
                ),
                **self._readiness_paths(state),
            )

        try:
            if not self._launch_from_state(state, lowvram=lowvram, disable_smart_memory=False):
                return ComfyUiRuntimeReadiness(
                    image_generation=False,
                    reason="ComfyUI startup failed",
                    detail="No runnable ComfyUI entrypoint was found",
                    **self._readiness_paths(state),
                )
            if self._wait_until_ready(wait_timeout_s=wait_timeout_s):
                return ComfyUiRuntimeReadiness(
                    image_generation=True,
                    reason=None,
                    detail="ComfyUI started",
                    started=True,
                    lowvram=lowvram,
                    **self._readiness_paths(state),
                )
        except Exception as exc:
            logger.exception("ComfyUI startup check failed")
            return ComfyUiRuntimeReadiness(
                image_generation=False,
                reason="ComfyUI startup failed",
                detail=str(exc),
                **self._readiness_paths(state),
                log_tail=self.error_log_tail(),
            )

        log_tail = self.error_log_tail()
        quarantined = self._quarantine_known_blocking_custom_nodes(log_tail, state=state)
        if quarantined:
            logger.warning("Quarantined blocking ComfyUI custom node(s): %s", ", ".join(quarantined))
            if not self._terminate_runtime_processes():
                return ComfyUiRuntimeReadiness(image_generation=False, reason="ComfyUI recovery deferred", detail="Existing process/queue cannot safely be stopped", **self._readiness_paths(state))
            try:
                if self._launch_from_state(state, lowvram=lowvram, disable_smart_memory=False):
                    if self._wait_until_ready(wait_timeout_s=wait_timeout_s):
                        return ComfyUiRuntimeReadiness(
                            image_generation=True,
                            reason=None,
                            detail="ComfyUI started after quarantining blocking custom node(s)",
                            started=True,
                            lowvram=lowvram,
                            **self._readiness_paths(state),
                            quarantined_custom_nodes=tuple(quarantined),
                        )
            except Exception:
                logger.exception("ComfyUI retry after custom node quarantine failed")
            log_tail = self.error_log_tail()

        logger.error("ComfyUI startup failed: %s", log_tail)
        return ComfyUiRuntimeReadiness(
            image_generation=False,
            reason="ComfyUI startup failed",
            detail=log_tail,
            started=True,
            **self._readiness_paths(state),
            log_tail=log_tail,
            quarantined_custom_nodes=tuple(quarantined),
        )

    def ensure_server_running(self, *, wait_timeout_s: int = 180, lowvram: bool | None = None) -> bool:
        HardwareGuardService.require_gpu("ComfyUI runtime")
        resolved_lowvram = bool(self.settings.comfyui_lowvram) if lowvram is None else bool(lowvram)
        if self._server_ready():
            return True
        if self._f5_tts_ready():
            logger.warning("ComfyUI start blocked because F5-TTS is active")
            return False

        state = self.describe_install()
        if not state.exists or state.install_dir is None:
            return False

        if not self.auto_launch_supported(state):
            logger.warning(
                "ComfyUI is managed by a desktop application and is offline; "
                "automatic portable launch is intentionally disabled."
            )
            return False

        if not self._launch_from_state(state, lowvram=resolved_lowvram, disable_smart_memory=False):
            return False
        if self._wait_until_ready(wait_timeout_s=wait_timeout_s):
            return True

        if self._launch_from_state(state, lowvram=resolved_lowvram, disable_smart_memory=True):
            if self._wait_until_ready(wait_timeout_s=wait_timeout_s):
                return True
        return False

    def probe_live_api(
        self,
        *,
        required_class_types: set[str] | None = None,
        required_models: dict[str, set[str]] | None = None,
    ) -> dict[str, Any]:
        """Sprawdza aktywne API ComfyUI bez uruchamiania procesu ani pobierania modeli."""

        required = {item for item in (required_class_types or set()) if item}
        required_model_names = {
            folder: {name for name in names if name}
            for folder, names in (required_models or {}).items()
            if folder and names
        }
        errors: list[str] = []
        with httpx.Client(timeout=max(1.0, float(self.settings.comfyui_health_timeout_s))) as client:
            for base_url in self._candidate_base_urls():
                try:
                    stats = client.get(f"{base_url}/system_stats")
                except Exception as exc:
                    errors.append(f"{base_url}: {type(exc).__name__}")
                    continue
                if stats.status_code >= 400:
                    errors.append(f"{base_url}: HTTP {stats.status_code}")
                    continue
                try:
                    object_info = client.get(f"{base_url}/object_info")
                    object_info.raise_for_status()
                    payload = object_info.json()
                except Exception as exc:
                    return {
                        "ready": False,
                        "api_online": True,
                        "base_url": base_url,
                        "reason": f"ComfyUI object_info unavailable: {type(exc).__name__}: {exc}",
                        "available_class_types": [],
                        "missing_class_types": sorted(required),
                        "available_models": {},
                        "missing_models": {
                            folder: sorted(names) for folder, names in required_model_names.items()
                        },
                    }
                available = set(payload) if isinstance(payload, dict) else set()
                missing = sorted(required - available)
                available_models: dict[str, list[str]] = {}
                missing_models: dict[str, list[str]] = {}
                for folder, names in required_model_names.items():
                    try:
                        response = client.get(f"{base_url}/models/{folder}")
                        response.raise_for_status()
                        model_payload = response.json()
                    except Exception as exc:
                        errors.append(f"{base_url}/models/{folder}: {type(exc).__name__}")
                        visible: set[str] = set()
                    else:
                        visible = {
                            str(item)
                            for item in model_payload
                            if isinstance(model_payload, list) and isinstance(item, str)
                        }
                    available_models[folder] = sorted(visible)
                    folder_missing = sorted(names - visible)
                    if folder_missing:
                        missing_models[folder] = folder_missing
                self._remember_active_base_url(base_url)
                ready = not missing and not missing_models
                reason = None
                if missing:
                    reason = "Required ComfyUI nodes are not installed."
                elif missing_models:
                    reason = "Required model files are not visible in the active ComfyUI model catalog."
                return {
                    "ready": ready,
                    "api_online": True,
                    "base_url": base_url,
                    "reason": reason,
                    "available_class_types": sorted(available),
                    "missing_class_types": missing,
                    "available_models": available_models,
                    "missing_models": missing_models,
                    "errors": errors,
                }
        return {
            "ready": False,
            "api_online": False,
            "base_url": None,
            "reason": "ComfyUI API is offline.",
            "errors": errors,
            "available_class_types": [],
            "missing_class_types": sorted(required),
            "available_models": {},
            "missing_models": {
                folder: sorted(names) for folder, names in required_model_names.items()
            },
        }

    def restart_server(self, *, wait_timeout_s: int = 30, lowvram: bool | None = None) -> bool:
        state = self.describe_install()
        if not self.auto_launch_supported(state):
            logger.warning(
                "ComfyUI restart skipped: active installation is managed by Comfy Desktop."
            )
            return False
        if not self._terminate_runtime_processes():
            logger.warning("ComfyUI restart deferred: active/unknown queue, GPU lease or ownership")
            return False
        return self.ensure_server_running(wait_timeout_s=wait_timeout_s, lowvram=lowvram)

    def auto_launch_supported(self, state: ComfyUiInstallState) -> bool:
        if self._is_desktop_managed(state):
            return bool(self.settings.comfyui_allow_managed_launch)
        return bool(
            state.exists
            and state.install_dir
            and (
                (state.python_executable and state.main_script)
                or state.run_script
                or state.fallback_script
            )
        )

    @staticmethod
    def _is_desktop_managed(state: ComfyUiInstallState) -> bool:
        return bool(state.install_dir and (state.install_dir / ".comfyui-desktop-2").exists())

    def stop_server(self, *, require_idle: bool = True, wait_timeout_s: int = 15) -> dict[str, Any]:
        """Stop only the managed ComfyUI runtime, never an active render queue."""

        if not self._server_ready() and not self._owned_runtime_processes():
            return {
                "status": "already_stopped",
                "stopped": True,
                "queue_running": 0,
                "queue_pending": 0,
            }

        queue_running, queue_pending = self._queue_counts()
        if queue_running is None or queue_pending is None or queue_running or queue_pending or not gpu_stop_allowed(self.settings):
            return {
                "status": "busy",
                "stopped": False,
                "queue_running": queue_running,
                "queue_pending": queue_pending,
                "reason": "ComfyUI queue/GPU activity is busy or cannot be verified.",
            }

        if not self._terminate_runtime_processes():
            return {"status": "stop_deferred", "stopped": False, "queue_running": queue_running,
                    "queue_pending": queue_pending, "reason": "Queue changed or process ownership unverified."}
        deadline = monotonic() + max(1, wait_timeout_s)
        while monotonic() < deadline:
            if not self._server_ready():
                return {
                    "status": "stopped",
                    "stopped": True,
                    "queue_running": queue_running,
                    "queue_pending": queue_pending,
                }
            sleep(0.5)
        return {
            "status": "stop_timeout",
            "stopped": False,
            "queue_running": queue_running,
            "queue_pending": queue_pending,
            "reason": "ComfyUI did not stop before the timeout.",
        }

    def _server_ready(self) -> bool:
        with httpx.Client(timeout=self.settings.comfyui_health_timeout_s) as client:
            for base_url in self._candidate_base_urls():
                try:
                    response = client.get(f"{base_url}/system_stats")
                except Exception:
                    continue
                if response.status_code < 400:
                    self._remember_active_base_url(base_url)
                    return True
        return False

    def _queue_counts(self) -> tuple[int | None, int | None]:
        url = f"{self.base_url}/queue"
        try:
            with httpx.Client(timeout=self.settings.comfyui_health_timeout_s) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return None, None
        if not isinstance(payload, dict) or not isinstance(payload.get("queue_running"), list) or not isinstance(payload.get("queue_pending"), list):
            return None, None
        return len(payload["queue_running"]), len(payload["queue_pending"])

    def _f5_tts_ready(self) -> bool:
        if self.settings.app_env == "test":
            return False
        base = str(self.settings.f5_tts_base_url or "http://localhost:7860").strip().rstrip("/")
        for suffix in ("/api/predict", "/gradio_api/call/basic_tts", "/gradio_api"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        try:
            with httpx.Client(timeout=2.0) as client:
                return client.get(f"{base.rstrip('/')}/config").status_code < 400
        except Exception:
            return False

    def _wait_until_ready(self, *, wait_timeout_s: int) -> bool:
        deadline = monotonic() + max(10, wait_timeout_s)
        while monotonic() < deadline:
            if self._server_ready():
                self._reconcile_owned_listener()
                return True
            sleep(2.0)
        return False

    def _reconcile_owned_listener(self):
        """Bind a launched venv wrapper to its exact known Comfy child listener.

        A listener on the configured port alone is never ownership proof. The
        saved launcher identity must still be alive and the child must be its
        descendant, with the same known interpreter/main.py/cwd/port contract.
        """
        try:
            path = self.settings.runtime_dir / 'comfyui-managed.instance.json'
            marker = json.loads(path.read_text('utf-8-sig'))
            port = urlsplit(self.base_url).port or 8188
            if marker.get('marker') != 'atnos-managed-comfy' or marker.get('port') != port:
                return False
            launcher = psutil.Process(int(marker['pid']))
            if abs(launcher.create_time() - float(marker['process_started_at_epoch'])) >= .01:
                return False
            if Path(launcher.cmdline()[0]).resolve() != Path(marker['executable']).resolve():
                return False
            matches = {process.pid: process for process in self._matching_install_processes()}
            if launcher.pid not in matches:
                return False
            descendants = {process.pid for process in launcher.children(recursive=True)}
            allowed_pids = {launcher.pid, *descendants}
            listeners = {connection.pid for connection in psutil.net_connections(kind='tcp')
                         if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port}
            if len(listeners) != 1:
                return False
            pid = next(iter(listeners))
            if pid not in allowed_pids or pid not in matches:
                return False
            process = matches[pid]
            argv = process.cmdline()
            ports = [argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg == '--port']
            if ports != [str(port)]:
                return False
            from atnos.production.steps.common import write_json
            write_json(path, {**marker, 'pid': pid, 'process_started_at_epoch': process.create_time(),
                              'executable': str(Path(argv[0]).resolve()), 'status': 'READY',
                              'launcher_pid': launcher.pid, 'launcher_started_at_epoch': launcher.create_time(),
                              'updated_at': datetime.now(timezone.utc).isoformat()})
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError, psutil.Error):
            return False

    def _launch_script(self, script_path: Path, *, working_dir: Path) -> None:
        log_dir = (self.settings.data_dir / "runtime").resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "comfyui.out.log"
        stderr_path = log_dir / "comfyui.err.log"
        with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
            subprocess.Popen(
                ["cmd.exe", "/c", str(script_path)],
                cwd=str(working_dir),
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    def _launch_python(
        self,
        python_executable: Path,
        main_script: Path,
        *,
        working_dir: Path,
        disable_smart_memory: bool,
        lowvram: bool = False,
    ) -> None:
        log_dir = (self.settings.data_dir / "runtime").resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "comfyui.out.log"
        stderr_path = log_dir / "comfyui.err.log"
        state = self.describe_install()
        desktop_managed = self._is_desktop_managed(state)
        args = [
            str(python_executable),
            "-s",
            str(main_script.relative_to(working_dir)),
        ]
        if not desktop_managed:
            args.append("--windows-standalone-build")
        else:
            args.extend(
                [
                    "--enable-manager",
                    "--disable-all-custom-nodes",
                    "--disable-async-offload",
                    # Match Start-ComfyUI.ps1: this Windows AMD runtime uses
                    # estimate-based loading, not the dynamic VRAM allocator.
                    "--disable-dynamic-vram",
                    "--disable-mmap",
                    "--reserve-vram",
                    str(self.settings.comfyui_reserve_vram_gb),
                ]
            )
            if self.settings.comfyui_cache_classic:
                args.append("--cache-classic")
            if self.settings.comfyui_shared_model_paths is not None:
                args.extend(
                    [
                        "--extra-model-paths-config",
                        str(self.settings.comfyui_shared_model_paths),
                    ]
                )
            if self.settings.comfyui_input_dir is not None:
                args.extend(["--input-directory", str(self.settings.comfyui_input_dir)])
            if self.settings.comfyui_output_dir is not None:
                args.extend(["--output-directory", str(self.settings.comfyui_output_dir)])
        if disable_smart_memory:
            args.append("--disable-smart-memory")
        if lowvram:
            args.append("--lowvram")
        endpoint = urlsplit(self.base_url)
        port = endpoint.port or 8188
        args.extend(['--listen', '127.0.0.1', '--port', str(port)])
        # ComfyUI inherits this process environment. Pin its profile, temp,
        # model-download and framework caches to the configured Atnos data
        # volume so an on-demand start cannot silently write them to C:.
        data_dir = Path(self.settings.data_dir).resolve()
        if os.name == 'nt' and data_dir.drive.casefold() != 'e:':
            raise RuntimeError('Atnos-managed ComfyUI requires its data directory on E:')
        profile_root = data_dir.parent
        if (len(data_dir.parents) >= 4 and data_dir.name.casefold() == 'data'
                and data_dir.parent.name.casefold() == 'atnos'
                and data_dir.parent.parent.name.casefold() == 'roaming'
                and data_dir.parent.parent.parent.name.casefold() == 'appdata'):
            profile_root = data_dir.parents[3]
        appdata = profile_root / 'AppData' / 'Roaming'
        local_appdata = profile_root / 'AppData' / 'Local'
        temp_dir = data_dir / 'temp'
        cache_dir = data_dir / 'tool-caches'
        paths = (temp_dir, data_dir / 'pycache', cache_dir / 'huggingface',
                 cache_dir / 'xdg', cache_dir / 'torch', cache_dir / 'pip', appdata, local_appdata)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({
            'USERPROFILE': str(profile_root), 'HOME': str(profile_root),
            'HOMEDRIVE': data_dir.drive, 'HOMEPATH': str(profile_root)[len(data_dir.drive):],
            'APPDATA': str(appdata), 'LOCALAPPDATA': str(local_appdata),
            'TEMP': str(temp_dir), 'TMP': str(temp_dir),
            'PYTHONPYCACHEPREFIX': str(data_dir / 'pycache'),
            'HF_HOME': str(cache_dir / 'huggingface'),
            'XDG_CACHE_HOME': str(cache_dir / 'xdg'),
            'TORCH_HOME': str(cache_dir / 'torch'), 'PIP_CACHE_DIR': str(cache_dir / 'pip'),
        })
        with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
            launched = subprocess.Popen(
                args,
                cwd=str(working_dir),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        try:
            started = psutil.Process(launched.pid).create_time()
            from atnos.production.steps.common import write_json
            write_json(self.settings.runtime_dir / 'comfyui-managed.instance.json', {
                'pid': launched.pid, 'process_started_at_epoch': started,
                'executable': str(python_executable.resolve()), 'argv': args[1:], 'port': port,
                'status': 'STARTING', 'marker': 'atnos-managed-comfy', 'owner': 'atnos-runtime-service'})
        except (OSError, psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError, AttributeError):
            logger.warning('Could not record Comfy process identity; automatic stop will remain unavailable')

    def _launch_from_state(
        self,
        state: ComfyUiInstallState,
        *,
        lowvram: bool,
        disable_smart_memory: bool,
    ) -> bool:
        if state.install_dir is None:
            return False
        if self._matching_install_processes():
            logger.warning("ComfyUI startup already exists; no duplicate process launched")
            return False
        if state.python_executable is not None and state.main_script is not None:
            self._launch_python(
                state.python_executable,
                state.main_script,
                working_dir=state.main_script.parent,
                disable_smart_memory=disable_smart_memory,
                lowvram=lowvram,
            )
            return True
        if lowvram:
            return False
        if disable_smart_memory and state.fallback_script is not None:
            self._launch_script(state.fallback_script, working_dir=state.install_dir)
            return True
        if disable_smart_memory:
            return False
        if state.run_script is not None:
            self._launch_script(state.run_script, working_dir=state.install_dir)
            return True
        if state.fallback_script is not None:
            self._launch_script(state.fallback_script, working_dir=state.install_dir)
            return True
        return False

    def _quarantine_known_blocking_custom_nodes(
        self,
        log_tail: str,
        *,
        state: ComfyUiInstallState,
    ) -> list[str]:
        """Move known startup-blocking custom nodes aside without deleting them."""
        if state.install_dir is None:
            return []
        lowered = log_tail.casefold()
        known_blockers = {
            "ComfyUI_wav2lip": ("comfyui_wav2lip", "wav2lip", "offload-arch"),
        }
        custom_nodes_dir = state.install_dir / "ComfyUI" / "custom_nodes"
        if not custom_nodes_dir.exists():
            return []

        quarantined: list[str] = []
        quarantine_root = (self.settings.quarantine_dir / "comfyui_custom_nodes").resolve()
        quarantine_root.mkdir(parents=True, exist_ok=True)
        for node_name, markers in known_blockers.items():
            if not all(marker in lowered for marker in markers):
                continue
            source = custom_nodes_dir / node_name
            if not source.exists() or not source.is_dir():
                continue
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = quarantine_root / f"{node_name}-{stamp}"
            suffix = 1
            while target.exists():
                suffix += 1
                target = quarantine_root / f"{node_name}-{stamp}-{suffix}"
            try:
                source.rename(target)
            except OSError as exc:
                logger.warning("Failed to quarantine ComfyUI custom node %s: %s", source, exc)
                continue
            quarantined.append(f"{node_name}->{target}")
        return quarantined

    def _matching_install_processes(self):
        state = self.describe_install()
        if not state.main_script:
            return []
        interpreters = [state.python_executable]
        if state.install_dir:
            interpreters.extend([state.install_dir / 'standalone-env/python.exe',
                                 state.install_dir / 'ComfyUI/.venv/Scripts/python.exe',
                                 state.install_dir / 'python_embeded/python.exe'])
        matches = {}
        for interpreter in interpreters:
            if interpreter is None:
                continue
            for process in matching_python_processes(python=interpreter, script=state.main_script,
                                                       cwd=state.main_script.parent):
                matches[getattr(process, 'pid', id(process))] = process
        return list(matches.values())

    def _owned_runtime_processes(self):
        # Running the same installation is insufficient stop authorization.
        # The launcher's exact PID/creation identity and endpoint must agree.
        runtime_dir = getattr(self.settings, 'runtime_dir', None)
        if runtime_dir is None:
            return []
        try:
            marker = json.loads((runtime_dir / 'comfyui-managed.instance.json').read_text('utf-8-sig'))
            pid, started = marker.get('pid'), float(marker['process_started_at_epoch'])
            port = urlsplit(self.base_url).port or 8188
            if (type(pid) is not int or pid <= 0 or not math.isfinite(started) or started <= 0
                    or marker.get('marker') != 'atnos-managed-comfy' or marker.get('port') != port):
                return []
            executable = Path(marker['executable']).resolve()
        except (OSError, ValueError, TypeError, KeyError):
            return []
        owned = []
        for process in self._matching_install_processes():
            try:
                argv = process.cmdline()
                ports = [argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg == '--port']
                if (process.pid == pid and abs(process.create_time() - started) < .01
                        and Path(argv[0]).resolve() == executable and ports == [str(port)]):
                    owned.append(process)
            except (OSError, psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                continue
        return owned

    def _terminate_runtime_processes(self) -> bool:
        # Unknown HTTP state must never be treated as an empty render queue.
        if self._queue_counts() != (0, 0) or not gpu_stop_allowed(self.settings):
            return False
        candidates = self._owned_runtime_processes()
        if not candidates:
            return False
        # Check again after process inspection, immediately before signals.
        if self._queue_counts() != (0, 0) or not gpu_stop_allowed(self.settings):
            return False
        for process in candidates:
            try:
                process.terminate()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        _, alive = psutil.wait_procs(candidates, timeout=5)
        # Do not escalate a graceful stop into a forced kill of uncertain work.
        return not alive

    def error_log_tail(self, *, max_chars: int = 4000) -> str:
        stderr_path = (self.settings.data_dir / "runtime" / "comfyui.err.log").resolve()
        stdout_path = (self.settings.data_dir / "runtime" / "comfyui.out.log").resolve()
        chunks: list[str] = []
        for path in (stderr_path, stdout_path):
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text:
                chunks.append(f"{path.name}:\n{text[-max_chars:]}")
        return "\n\n".join(chunks)[-max_chars:] if chunks else "No ComfyUI runtime logs were written."

    @staticmethod
    def _readiness_paths(state: ComfyUiInstallState) -> dict[str, str | None]:
        return {
            "install_dir": str(state.install_dir) if state.install_dir is not None else None,
            "main_script": str(state.main_script) if state.main_script is not None else None,
            "python_executable": str(state.python_executable) if state.python_executable is not None else None,
            "checkpoint_dir": str(state.checkpoint_dir) if state.checkpoint_dir is not None else None,
        }

    @staticmethod
    def _existing_file(path: Path) -> Path | None:
        return path.resolve() if path.exists() and path.is_file() else None

    @staticmethod
    def _existing_dir(path: Path) -> Path | None:
        return path.resolve() if path.exists() and path.is_dir() else None
