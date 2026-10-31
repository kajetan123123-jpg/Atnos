from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

TESTS_DIR = Path(__file__).resolve().parent
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.runtime_health_service import RuntimeComponentHealth, RuntimeHealthService  # noqa: E402
from test_support import build_test_settings, make_temp_dir  # noqa: E402


def _ready_component(key: str, label: str | None = None) -> RuntimeComponentHealth:
    return RuntimeComponentHealth(
        key=key,
        label=label or key,
        status="ready",
        detail=f"{key} checked.",
        next_action="No action required.",
        metadata={"source": "test"},
    )


@pytest.mark.parametrize("status_code", [301, 401, 403, 404, 429, 500, 503])
def test_http_error_or_redirect_is_never_ready(tmp_path: Path, status_code: int) -> None:
    service = RuntimeHealthService(build_test_settings(tmp_path))
    with patch("atnos.services.runtime_health_service.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = httpx.Response(status_code)
        result = service._http("comfyui", "ComfyUI", "http://localhost:8188/system_stats")
    assert result.status == "blocked"
    assert result.metadata["status_code"] == status_code
    assert result.next_action != "No action required."
    summary = service._readiness_summary(components=[result], status="blocked")
    assert summary["can_run_core"] is False
    assert "comfyui" in summary["blocking_components"]


@pytest.mark.parametrize("body", [
    b"<html>Sign in</html>", b"null", b"[]", b"{}",
    b'{"system": {}, "devices": []}',
    b'{"system": {"os": "win32"}, "devices": [{"name": "GPU"}]}',
    b'{"system": {"os": "win32"}, "devices": [null]}',
])
def test_comfyui_200_requires_system_stats_contract(tmp_path: Path, body: bytes) -> None:
    service = RuntimeHealthService(build_test_settings(tmp_path))
    with patch("atnos.services.runtime_health_service.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = httpx.Response(200, content=body)
        result = service._http("comfyui", "ComfyUI", "http://localhost:8188/system_stats")
    assert result.status == "blocked"
    assert result.metadata["contract_valid"] is False
    assert "system_stats" in result.detail


@pytest.mark.parametrize("device_type", ["cpu", "cuda"])
def test_comfyui_valid_contract_is_ready_without_requiring_gpu(tmp_path: Path, device_type: str) -> None:
    service = RuntimeHealthService(build_test_settings(tmp_path))
    with patch("atnos.services.runtime_health_service.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = httpx.Response(200, json={
            "system": {"os": "win32"}, "devices": [{"name": "local device", "type": device_type}],
        })
        result = service._http("comfyui", "ComfyUI", "http://localhost:8188/system_stats")
    assert result.status == "ready"
    assert result.metadata["contract_valid"] is True


def test_http_timeout_is_missing_not_ready(tmp_path: Path) -> None:
    service = RuntimeHealthService(build_test_settings(tmp_path))
    with patch("atnos.services.runtime_health_service.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectTimeout("offline")
        result = service._http("comfyui", "ComfyUI", "http://localhost:8188/system_stats")
    assert result.status == "missing"


@pytest.mark.parametrize("status_code", [301, 401, 404, 500])
def test_f5_endpoint_errors_never_mean_ready(tmp_path: Path, status_code: int) -> None:
    service = RuntimeHealthService(build_test_settings(tmp_path))
    with patch("atnos.services.runtime_health_service.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = httpx.Response(status_code)
        result = service._f5_tts()
    assert result.status != "ready"
    assert result.metadata["endpoint_ready"] is False
    assert result.metadata["endpoint_error"] == f"HTTP {status_code}"


def test_runtime_health_snapshot_reports_package_manager_and_local_host_control() -> None:
    with make_temp_dir() as temp:
        settings = build_test_settings(Path(temp))
        service = RuntimeHealthService(settings)

        with (
            patch.object(
                RuntimeHealthService,
                "_executable",
                side_effect=lambda key, label, executable, args: _ready_component(key, label),
            ),
            patch.object(
                RuntimeHealthService,
                "_http",
                side_effect=lambda key, label, url: _ready_component(key, label),
            ),
            patch.object(RuntimeHealthService, "_f5_tts", return_value=_ready_component("f5_tts", "F5-TTS")),
            patch.object(RuntimeHealthService, "_ollama", return_value=_ready_component("ollama", "Ollama")),
            patch.object(RuntimeHealthService, "_connection_components", return_value=[]),
        ):
            payload = service.snapshot(session=None)

    components = {item["key"]: item for item in payload["components"]}

    assert "pnpm" in components
    assert components["pnpm"]["status"] == "ready"
    assert "ffmpeg" in components
    assert "f5_tts" in components
    assert "local_host_control" in components
    assert components["local_host_control"]["status"] == "degraded"
    assert components["local_host_control"]["evidence"]["source"] == "local_host_control"
    assert components["local_host_control"]["first_action"]


def test_runtime_health_prefers_configured_ffmpeg_path() -> None:
    with make_temp_dir() as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        fake_ffmpeg = root / "tools" / "ffmpeg.exe"
        fake_ffmpeg.parent.mkdir(parents=True, exist_ok=True)
        fake_ffmpeg.write_text("not a real executable, only a configured path marker", encoding="utf-8")
        settings.ffmpeg_exe = str(fake_ffmpeg)

        detected = RuntimeHealthService(settings)._known_executable("ffmpeg")

    assert detected == str(fake_ffmpeg)


def test_runtime_health_detects_bundled_pnpm_fallback(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    bundled = (
        tmp_path
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "bin"
        / "fallback"
        / "pnpm.cmd"
    )
    bundled.parent.mkdir(parents=True)
    bundled.write_text("@echo off", encoding="utf-8")

    with (
        patch("atnos.services.runtime_health_service.shutil.which", return_value=None),
        patch("atnos.services.runtime_health_service.Path.home", return_value=tmp_path),
    ):
        detected = RuntimeHealthService(settings)._known_executable("pnpm")

    assert detected == str(bundled)


def test_runtime_health_normalizes_f5_predict_endpoint_to_service_root() -> None:
    with make_temp_dir() as temp:
        settings = build_test_settings(Path(temp))
        settings.f5_tts_base_url = "http://127.0.0.1:7860/api/predict"

        url = RuntimeHealthService(settings)._f5_tts_health_url()

    assert url == "http://127.0.0.1:7860/"


def test_runtime_health_reports_installed_stopped_f5_as_on_demand(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    runtime = tmp_path / "F5-TTS-runtime"
    python = runtime / ".venv" / "Scripts" / "python.exe"
    reference = runtime / "reference.wav"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    reference.write_bytes(b"RIFF")
    settings.f5_tts_install_dir = runtime
    settings.f5_tts_runtime_python = python
    settings.f5_tts_launch_command = "python -m f5_tts.infer.infer_gradio"
    settings.f5_tts_ref_audio_path = reference

    with patch("atnos.services.runtime_health_service.httpx.Client", side_effect=OSError("offline")):
        component = RuntimeHealthService(settings)._f5_tts().to_dict()

    assert component["status"] == "degraded"
    assert component["metadata"]["mode"] == "on_demand"
    assert component["metadata"]["required_for_core"] is False
    assert "intentionally stopped" in component["detail"]


def test_optional_runtime_is_not_a_core_blocker() -> None:
    with make_temp_dir() as temp:
        settings = build_test_settings(Path(temp))
        service = RuntimeHealthService(settings)
        components = [
            _ready_component("backend"),
            RuntimeComponentHealth(
                key="f5_tts",
                label="F5-TTS",
                status="missing",
                detail="offline",
                next_action="install",
                metadata={"required_for_core": False},
            ),
        ]

        readiness = service._readiness_summary(components=components, status="degraded")

    assert readiness["can_run_core"] is True
    assert readiness["blocking_components"] == []
    assert readiness["optional_unavailable_components"] == ["f5_tts"]


def test_runtime_health_local_host_control_without_database_is_explicitly_degraded() -> None:
    with make_temp_dir() as temp:
        settings = build_test_settings(Path(temp))

        component = RuntimeHealthService(settings)._local_host_control(session=None).to_dict()

    assert component["key"] == "local_host_control"
    assert component["status"] == "degraded"
    assert "Database session is not available" in component["detail"]
    assert component["next_action"] == "Refresh runtime after backend database is available."


def test_runtime_health_marks_ollama_missing_when_model_catalog_is_empty() -> None:
    with make_temp_dir() as temp:
        settings = build_test_settings(Path(temp))
        response = MagicMock(status_code=200)
        response.json.return_value = {"models": []}
        client = MagicMock()
        client.get.return_value = response
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch("atnos.services.runtime_health_service.httpx.Client", return_value=client):
            component = RuntimeHealthService(settings)._ollama().to_dict()

    assert component["status"] == "missing"
    assert component["metadata"]["model_count"] == 0
    assert "no local model is installed" in component["detail"]
    assert "Install a local Ollama model" in component["next_action"]


def test_runtime_health_reports_ollama_ready_only_with_an_installed_model() -> None:
    with make_temp_dir() as temp:
        settings = build_test_settings(Path(temp))
        response = MagicMock(status_code=200)
        response.json.return_value = {"models": [{"name": "qwen3:8b"}]}
        client = MagicMock()
        client.get.return_value = response
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch("atnos.services.runtime_health_service.httpx.Client", return_value=client):
            component = RuntimeHealthService(settings)._ollama().to_dict()

    assert component["status"] == "ready"
    assert component["metadata"]["models"] == ["qwen3:8b"]


def test_runtime_health_reports_active_watchdog_from_real_process_probe() -> None:
    with make_temp_dir() as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        (root / "AtnosRuntimeWatchdog.ps1").write_text("# test watchdog", encoding="utf-8")
        runtime_dir = settings.data_dir.parent / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "runtime-watchdog.state.json").write_text(
            '{"pid":4242,"status":"ready","heartbeat_at":"2099-01-01T00:00:00+00:00","api_ready":true,"web_ready":true}',
            encoding="utf-8",
        )

        with patch.object(RuntimeHealthService, "_repo_root", return_value=root):
            component = RuntimeHealthService(settings)._runtime_watchdog().to_dict()

    assert component["status"] == "ready"
    assert component["metadata"]["process_id"] == 4242
    assert "PID=4242" in component["detail"]


def test_runtime_health_identifies_the_official_desktop_launch_chain() -> None:
    with make_temp_dir() as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        (root / "Start-Atnos.vbs").write_text("' launcher entrypoint", encoding="utf-8")
        (root / "Launch-Atnos.ps1").write_text("# runtime launcher", encoding="utf-8")
        (root / "Atnos Shell.exe").write_bytes(b"managed-shell")
        (root / "Atnos Desktop.exe").write_bytes(b"legacy-shell")

        with patch.object(RuntimeHealthService, "_repo_root", return_value=root):
            component = RuntimeHealthService(settings)._desktop_shell().to_dict()

    assert component["status"] == "ready"
    assert "Start-Atnos.vbs -> Launch-Atnos.ps1 -> Atnos Shell.exe" in component["detail"]
    assert component["metadata"]["official_entrypoint"].endswith("Start-Atnos.vbs")
    assert component["metadata"]["managed_shell"].endswith("Atnos Shell.exe")
    assert component["metadata"]["legacy_shells"][0].endswith("Atnos Desktop.exe")


def test_runtime_health_does_not_mark_a_legacy_shell_as_officially_ready() -> None:
    with make_temp_dir() as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        (root / "Launch-Atnos.ps1").write_text("# runtime launcher", encoding="utf-8")
        (root / "Atnos Portable.exe").write_bytes(b"legacy-shell")

        with patch.object(RuntimeHealthService, "_repo_root", return_value=root):
            component = RuntimeHealthService(settings)._desktop_shell().to_dict()

    assert component["status"] == "degraded"
    assert "official Start-Atnos.vbs entrypoint is incomplete" in component["detail"]
