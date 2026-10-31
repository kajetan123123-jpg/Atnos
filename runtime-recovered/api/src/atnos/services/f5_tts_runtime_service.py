from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from time import monotonic, sleep
from typing import Any

import httpx
import psutil

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class F5TTSRuntimeStatus:
    endpoint: str
    endpoint_ready: bool
    endpoint_status_code: int | None
    endpoint_error: str | None
    package_installed: bool
    package_module_path: str | None
    gradio_installed: bool
    runtime_python: str | None
    runtime_python_exists: bool
    runtime_package_installed: bool
    runtime_gradio_installed: bool
    runtime_probe_error: str | None
    install_dir: str | None
    install_exists: bool
    detected_files: tuple[str, ...]
    launch_command_configured: bool
    launch_supported: bool
    reference_audio_path: str | None
    reference_audio_exists: bool
    diagnosis_code: str
    diagnosis_message: str
    blocking_reasons: tuple[str, ...]
    next_action: str
    suggested_commands: tuple[str, ...]
    started: bool = False
    stopped: bool = False
    transport: str = "gradio"
    transport_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class F5TTSRuntimeService:
    """Diagnozuje i opcjonalnie uruchamia lokalny runtime F5-TTS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> F5TTSRuntimeStatus:
        endpoint = str(self.settings.f5_tts_base_url or "").strip() or "http://localhost:7860/api/predict"
        endpoint_ready, endpoint_status_code, endpoint_error = self._probe_endpoint(endpoint)
        package_path = self._package_path("f5_tts")
        current_gradio_installed = self._package_path("gradio") is not None
        install_dir = self.resolve_install_dir()
        runtime_python = self.resolve_runtime_python(install_dir)
        runtime_package_installed, runtime_gradio_installed, runtime_probe_error = self._probe_runtime_python(runtime_python)
        detected_files = self._detected_files(install_dir)
        launch_command_configured = bool(str(self.settings.f5_tts_launch_command or "").strip())
        launch_supported = launch_command_configured
        package_installed = package_path is not None
        gradio_installed = current_gradio_installed or runtime_gradio_installed
        reference_audio = self.resolve_reference_audio(install_dir)
        reference_audio_exists = reference_audio is not None and reference_audio.exists()
        diagnosis_code, diagnosis_message, blocking_reasons = self._diagnosis(
            endpoint_ready=endpoint_ready,
            runtime_python_exists=runtime_python is not None and runtime_python.exists(),
            runtime_package_installed=runtime_package_installed,
            runtime_gradio_installed=runtime_gradio_installed,
            launch_supported=launch_supported,
            reference_audio_exists=reference_audio_exists,
            transport=self.settings.f5_tts_transport,
        )
        return F5TTSRuntimeStatus(
            endpoint=endpoint,
            endpoint_ready=endpoint_ready,
            endpoint_status_code=endpoint_status_code,
            endpoint_error=endpoint_error,
            package_installed=package_installed,
            package_module_path=package_path,
            gradio_installed=gradio_installed,
            runtime_python=str(runtime_python) if runtime_python else None,
            runtime_python_exists=runtime_python is not None and runtime_python.exists(),
            runtime_package_installed=runtime_package_installed,
            runtime_gradio_installed=runtime_gradio_installed,
            runtime_probe_error=runtime_probe_error,
            install_dir=str(install_dir) if install_dir else None,
            install_exists=install_dir is not None,
            detected_files=detected_files,
            launch_command_configured=launch_command_configured,
            launch_supported=launch_supported,
            reference_audio_path=str(reference_audio) if reference_audio else None,
            reference_audio_exists=reference_audio_exists,
            diagnosis_code=diagnosis_code,
            diagnosis_message=diagnosis_message,
            blocking_reasons=blocking_reasons,
            next_action=("f5_tts_ready_run_voice_provider_doctor" if diagnosis_code == "cli_ready" else
                         "configure_f5_tts_cli" if self.settings.f5_tts_transport == "cli" else self._next_action(
                endpoint_ready=endpoint_ready,
                package_installed=package_installed or runtime_package_installed,
                gradio_installed=gradio_installed,
                install_exists=install_dir is not None or bool(runtime_python),
                runtime_python_exists=runtime_python is not None and runtime_python.exists(),
                launch_supported=launch_supported,
            )),
            suggested_commands=self._suggested_commands(install_dir, runtime_python),
            transport=self.settings.f5_tts_transport,
            transport_ready=diagnosis_code in {"ready", "cli_ready"},
        )

    def resolve_reference_audio(self, install_dir: Path | None = None) -> Path | None:
        configured = self.settings.f5_tts_ref_audio_path
        if configured is not None:
            resolved = configured.expanduser().resolve()
            if resolved.exists() and resolved.is_file():
                return resolved
        if install_dir is None:
            return None
        sample = (
            install_dir
            / ".venv"
            / "Lib"
            / "site-packages"
            / "f5_tts"
            / "infer"
            / "examples"
            / "basic"
            / "basic_ref_en.wav"
        )
        return sample.resolve() if sample.exists() and sample.is_file() else None

    def subprocess_environment(self) -> dict[str, str]:
        """Keep CLI and server caches together under the configured Atnos data root."""
        environment = os.environ.copy()
        cache_root = (self.settings.data_dir / "tool-caches").resolve()
        temporary = (cache_root / "f5-temp").resolve()
        gradio_temp = (self.settings.f5_tts_gradio_temp_dir or temporary / "gradio").resolve()
        paths = {
            "HF_HOME": cache_root / "huggingface",
            "HF_HUB_CACHE": cache_root / "huggingface" / "hub",
            "HUGGINGFACE_HUB_CACHE": cache_root / "huggingface" / "hub",
            "HF_DATASETS_CACHE": cache_root / "huggingface" / "datasets",
            "TRANSFORMERS_CACHE": cache_root / "huggingface" / "transformers",
            "TORCH_HOME": cache_root / "torch",
            "XDG_CACHE_HOME": cache_root / "xdg-cache",
            "TEMP": temporary,
            "TMP": temporary,
            "TMPDIR": temporary,
            "GRADIO_TEMP_DIR": gradio_temp,
        }
        for variable, path in paths.items():
            path.mkdir(parents=True, exist_ok=True)
            environment[variable] = str(path)
        environment["PYTHONIOENCODING"] = "utf-8"
        if self.settings.f5_tts_ffmpeg_dir is not None:
            environment["PATH"] = f"{self.settings.f5_tts_ffmpeg_dir}{os.pathsep}{environment.get('PATH', '')}"
        return environment

    def start(self, *, wait_timeout_s: int = 120) -> F5TTSRuntimeStatus:
        before = self.status()
        if before.endpoint_ready or before.transport == "cli":
            return before
        if self._comfyui_ready():
            return F5TTSRuntimeStatus(
                **{
                    **before.to_dict(),
                    "diagnosis_code": "gpu_conflict_comfyui",
                    "diagnosis_message": "F5-TTS nie został uruchomiony, ponieważ ComfyUI używa GPU.",
                    "blocking_reasons": ("Zatrzymaj ComfyUI przed uruchomieniem F5-TTS.",),
                    "next_action": "stop_comfyui_before_f5_tts",
                }
            )
        command = str(self.settings.f5_tts_launch_command or "").strip()
        if not command:
            return before
        working_dir = self.resolve_install_dir() or self.settings.app_root
        log_dir = (self.settings.data_dir / "runtime").resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "f5_tts.out.log"
        stderr_path = log_dir / "f5_tts.err.log"
        runtime_python = Path(before.runtime_python) if before.runtime_python else None
        use_direct_runtime = bool(
            runtime_python
            and runtime_python.exists()
            and before.runtime_package_installed
            and before.runtime_gradio_installed
        )
        compat_launcher = (
            self.settings.app_root
            / "runtime-recovered"
            / "api"
            / "scripts"
            / "run_f5_tts_compat.py"
        ).resolve()
        launch_args = (
            [
                str(runtime_python),
                *(
                    [str(compat_launcher)]
                    if compat_launcher.is_file()
                    else ["-m", "f5_tts.infer.infer_gradio"]
                ),
                "-a",
                "-H",
                "127.0.0.1",
                "-p",
                "7860",
            ]
            if use_direct_runtime
            else ["cmd.exe", "/d", "/s", "/c", command]
        )
        launch_env = self.subprocess_environment()
        with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
            subprocess.Popen(
                launch_args,
                cwd=str(working_dir),
                env=launch_env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if self._wait_until_ready(wait_timeout_s=wait_timeout_s):
            current = self.status()
            return F5TTSRuntimeStatus(**{**current.to_dict(), "started": True})
        return self.status()

    def stop(self, *, wait_timeout_s: int = 15) -> F5TTSRuntimeStatus:
        """Zatrzymuje wyłącznie procesy należące do lokalnego serwera F5-TTS."""

        before = self.status()
        terminated = self._terminate_runtime_processes()
        if not terminated and before.endpoint_ready:
            return F5TTSRuntimeStatus(**{**before.to_dict(), "stopped": False,
                "diagnosis_code": "stop_deferred_activity_unknown",
                "diagnosis_message": "F5-TTS zachowany: brak wiarygodnego potwierdzenia braku aktywnej syntezy.",
                "next_action": "finish_synthesis_and_close_owned_server"})
        if not before.endpoint_ready:
            current = self.status()
            return F5TTSRuntimeStatus(**{**current.to_dict(), "stopped": terminated})
        deadline = monotonic() + max(1, wait_timeout_s)
        while monotonic() < deadline:
            current = self.status()
            if not current.endpoint_ready:
                return F5TTSRuntimeStatus(**{**current.to_dict(), "stopped": True})
            sleep(0.5)
        return self.status()

    def _comfyui_ready(self) -> bool:
        if self.settings.app_env == "test":
            return False
        url = f"{self.settings.comfyui_base_url.rstrip('/')}/system_stats"
        try:
            with httpx.Client(timeout=2.0) as client:
                return client.get(url).status_code < 400
        except Exception:
            return False

    def _terminate_runtime_processes(self) -> bool:
        # Gradio readiness does not establish that synthesis has finished. This
        # server currently has no identity-bound activity/drain protocol, so an
        # automatic resource switch must preserve it and report the blocker.
        return False

    def resolve_install_dir(self) -> Path | None:
        candidates: list[Path | None] = [
            self.settings.f5_tts_install_dir,
            self.settings.f5_tts_runtime_python.parent.parent.parent if self.settings.f5_tts_runtime_python else None,
            self.settings.app_root.parent / "F5-TTS",
            self.settings.app_root.parent / "f5-tts",
            self.settings.app_root.parent / "F5TTS",
            self.settings.app_root.parent / "F5-TTS-runtime",
        ]
        if self.settings.app_env != "test":
            candidates.extend(
                [
                    Path("E:/Atnos/Tools/F5-TTS-runtime"),
                ]
            )
        seen: set[str] = set()
        for candidate in candidates:
            if candidate is None:
                continue
            resolved = candidate.resolve()
            key = str(resolved).casefold()
            if key in seen:
                continue
            seen.add(key)
            if not resolved.exists() or not resolved.is_dir():
                continue
            if self._looks_like_f5_install(resolved) or self._venv_python(resolved) is not None:
                return resolved
        return None

    def resolve_runtime_python(self, install_dir: Path | None = None) -> Path | None:
        candidates: list[Path | None] = [
            self.settings.f5_tts_runtime_python,
            self._venv_python(install_dir) if install_dir else None,
            self._venv_python(self.settings.app_root.parent / "F5-TTS-runtime"),
        ]
        if self.settings.app_env != "test":
            candidates.append(self._venv_python(Path("E:/Atnos/Tools/F5-TTS-runtime")))
        seen: set[str] = set()
        for candidate in candidates:
            if candidate is None:
                continue
            resolved = candidate.resolve()
            key = str(resolved).casefold()
            if key in seen:
                continue
            seen.add(key)
            if resolved.exists() and resolved.is_file():
                return resolved
        return None

    def _wait_until_ready(self, *, wait_timeout_s: int) -> bool:
        deadline = monotonic() + max(1, wait_timeout_s)
        while monotonic() < deadline:
            if self.status().endpoint_ready:
                return True
            sleep(1.0)
        return False

    def _probe_endpoint(self, endpoint: str) -> tuple[bool, int | None, str | None]:
        if not endpoint:
            return False, None, "F5-TTS endpoint is not configured."
        health_url = self._gradio_health_url(endpoint)
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(health_url)
            return response.status_code < 400, response.status_code, None if response.status_code < 400 else response.text[:240]
        except Exception as exc:
            return False, None, str(exc)[:240]

    @staticmethod
    def _gradio_health_url(endpoint: str) -> str:
        base = endpoint.strip().rstrip("/")
        suffixes = ("/api/predict", "/gradio_api/call/basic_tts", "/gradio_api")
        for suffix in suffixes:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return f"{base.rstrip('/')}/config"

    @staticmethod
    def _package_path(name: str) -> str | None:
        spec = importlib.util.find_spec(name)
        if spec is None:
            return None
        return str(spec.origin or "")

    @staticmethod
    def _probe_runtime_python(runtime_python: Path | None) -> tuple[bool, bool, str | None]:
        if runtime_python is None or not runtime_python.exists():
            return False, False, None
        script = (
            "import importlib.util, json; "
            "print(json.dumps({"
            "'f5_tts': importlib.util.find_spec('f5_tts') is not None, "
            "'gradio': importlib.util.find_spec('gradio') is not None"
            "}))"
        )
        try:
            result = subprocess.run(
                [str(runtime_python), "-c", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            return False, False, str(exc)[:240]
        if result.returncode != 0:
            return False, False, (result.stderr or result.stdout or "runtime python probe failed")[:240]
        try:
            payload = json.loads(result.stdout)
        except Exception as exc:
            return False, False, f"runtime python probe returned invalid JSON: {exc}"
        return bool(payload.get("f5_tts")), bool(payload.get("gradio")), None

    @staticmethod
    def _looks_like_f5_install(path: Path) -> bool:
        markers = (
            path / "src" / "f5_tts",
            path / "f5_tts",
            path / "pyproject.toml",
            path / "app.py",
            path / "gradio_app.py",
            path / ".venv" / "Scripts" / "python.exe",
        )
        return any(item.exists() for item in markers)

    @staticmethod
    def _venv_python(path: Path | None) -> Path | None:
        if path is None:
            return None
        candidates = (
            path / ".venv" / "Scripts" / "python.exe",
            path / "venv" / "Scripts" / "python.exe",
            path / ".venv" / "bin" / "python",
            path / "venv" / "bin" / "python",
        )
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _detected_files(path: Path | None) -> tuple[str, ...]:
        if path is None:
            return ()
        candidates = (
            path / "src" / "f5_tts",
            path / "f5_tts",
            path / "pyproject.toml",
            path / "app.py",
            path / "gradio_app.py",
            path / "README.md",
            path / ".venv" / "Scripts" / "python.exe",
        )
        return tuple(str(item) for item in candidates if item.exists())

    @staticmethod
    def _next_action(
        *,
        endpoint_ready: bool,
        package_installed: bool,
        gradio_installed: bool,
        install_exists: bool,
        runtime_python_exists: bool,
        launch_supported: bool,
    ) -> str:
        if endpoint_ready:
            return "f5_tts_ready_run_voice_provider_doctor"
        if launch_supported:
            return "start_configured_f5_tts_runtime"
        if package_installed and gradio_installed:
            return "configure_f5_tts_launch_command"
        if runtime_python_exists:
            return "install_f5_tts_python_dependencies_and_configure_launch_command"
        if install_exists:
            return "install_f5_tts_python_dependencies_and_configure_launch_command"
        return "install_f5_tts_runtime"

    @staticmethod
    def _diagnosis(
        *,
        endpoint_ready: bool,
        runtime_python_exists: bool,
        runtime_package_installed: bool,
        runtime_gradio_installed: bool,
        launch_supported: bool,
        reference_audio_exists: bool,
        transport: str = "gradio",
    ) -> tuple[str, str, tuple[str, ...]]:
        reasons: list[str] = []
        if not runtime_python_exists:
            reasons.append("Nie znaleziono interpretera Python runtime F5-TTS.")
        if not runtime_package_installed:
            reasons.append("Pakiet f5_tts nie jest zainstalowany w runtime F5-TTS.")
        if transport != "cli" and not runtime_gradio_installed:
            reasons.append("Pakiet Gradio nie jest zainstalowany w runtime F5-TTS.")
        if transport != "cli" and not launch_supported:
            reasons.append("Brakuje skonfigurowanej komendy uruchomienia F5-TTS.")
        if transport != "cli" and not endpoint_ready:
            reasons.append("Serwer F5-TTS nie odpowiada na skonfigurowanym endpointcie.")
        if not reference_audio_exists:
            reasons.append("Brakuje referencyjnego pliku WAV wymaganego do generowania głosu.")

        if not runtime_python_exists:
            code = "runtime_missing"
            message = "Runtime F5-TTS nie jest kompletny: brakuje interpretera Python."
        elif not runtime_package_installed or (transport != "cli" and not runtime_gradio_installed):
            code = "dependencies_missing"
            message = "Runtime F5-TTS istnieje, ale brakuje wymaganych pakietów."
        elif transport == "cli":
            if not reference_audio_exists:
                code = "reference_audio_missing"
                message = "F5-TTS wymaga referencyjnej próbki głosu WAV."
            else:
                code = "cli_ready"
                message = "F5-TTS ma konfigurację do syntezy lokalnej na CPU. Jakość głosu sprawdź testem próbki."
        elif not launch_supported:
            code = "launch_command_missing"
            message = "F5-TTS jest zainstalowany, ale nie ma bezpiecznej komendy startowej."
        elif not endpoint_ready:
            code = "server_offline"
            message = "F5-TTS jest zainstalowany, lecz lokalny serwer na porcie 7860 nie działa."
        elif not reference_audio_exists:
            code = "reference_audio_missing"
            message = "Serwer F5-TTS działa, lecz nie skonfigurowano referencyjnej próbki głosu WAV."
        else:
            code = "ready"
            message = "F5-TTS działa i ma skonfigurowaną referencyjną próbkę głosu."
        return code, message, tuple(reasons)

    @staticmethod
    def _suggested_commands(install_dir: Path | None, runtime_python: Path | None = None) -> tuple[str, ...]:
        base = str(install_dir) if install_dir else "E:\\Atnos\\Tools\\F5-TTS-runtime"
        runtime = str(runtime_python) if runtime_python else f"{base}\\.venv\\Scripts\\python.exe"
        ffmpeg_bin = str(Path(os.environ.get("F5_TTS_FFMPEG_DIR", "E:/Atnos/Tools/FFmpeg/bin")))
        gradio_module = f"{runtime} -m f5_tts.infer.infer_gradio"
        return (
            f"cd /d {base} && {runtime} -m pip install f5-tts==1.1.20 gradio",
            f"cd /d {base} && {gradio_module} -a -H 127.0.0.1 -p 7860",
            f"set F5_TTS_RUNTIME_PYTHON={runtime}",
            f"set F5_TTS_LAUNCH_COMMAND=set PATH={ffmpeg_bin};%PATH% && {gradio_module} -a -H 127.0.0.1 -p 7860",
        )
