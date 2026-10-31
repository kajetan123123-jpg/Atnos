from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from test_support import create_test_app  # noqa: E402
from atnos.production.voice_provider_doctor import voice_provider_next_action, voice_provider_remediation_actions  # noqa: E402
from atnos.services.f5_tts_runtime_service import F5TTSRuntimeService  # noqa: E402


def test_voice_provider_status_returns_last_known_state_without_live_probe(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/production/voice-provider/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["checked"] is False
    assert payload["public_voice_ready"] is False
    assert payload["next_action"] == "run_voice_provider_doctor"
    assert "providers" in payload
    assert "openai_tts" in payload["providers"]
    assert "elevenlabs" in payload["providers"]
    assert "f5_tts" in payload["providers"]
    assert "api_key" not in str(payload).casefold()


def test_voice_provider_next_action_prefers_enabling_openai_when_key_exists() -> None:
    action = voice_provider_next_action(
        {
            "public_publication_voice_ready": False,
            "openai_api_key_configured": True,
            "openai_tts_allow_cloud_tts": False,
            "openai_tts_configured": False,
            "elevenlabs_tts_live_error": "detected_unusual_activity",
        }
    )

    assert action == "enable_openai_tts_cloud_backend"


def test_voice_provider_next_action_blocks_public_voice_when_cloud_budget_is_not_allowed() -> None:
    action = voice_provider_next_action(
        {"public_publication_voice_ready": True},
        cloud_voice_budget={"allowed": False, "status": "budget_blocked"},
    )

    assert action == "approve_cloud_voice_budget_or_use_local_f5_tts"


def test_voice_provider_remediation_actions_explain_premium_voice_blockers() -> None:
    actions = voice_provider_remediation_actions(
        {
            "public_publication_voice_ready": False,
            "openai_tts_configured": True,
            "openai_tts_live_ready": False,
            "openai_tts_live_error": "insufficient_quota: You exceeded your current quota.",
            "f5_tts_configured": True,
            "f5_tts_live_ready": False,
            "f5_tts_live_error": "All connection attempts failed",
            "elevenlabs_configured": True,
            "elevenlabs_tts_live_ready": False,
            "elevenlabs_tts_live_error": "detected_unusual_activity",
            "kokoro_local_live_ready": True,
        }
    )

    action_ids = [item["id"] for item in actions]
    assert action_ids[:3] == [
        "fix_openai_tts_quota_or_project_limit",
        "start_local_f5_tts",
        "upgrade_or_unblock_elevenlabs_account",
    ]
    assert "use_kokoro_for_private_qa_only" in action_ids
    assert actions[0]["kind"] == "owner_external_action"
    assert actions[1]["kind"] == "owner_or_local_runtime_action"


def test_voice_provider_remediation_mentions_valid_openai_key_when_api_reachable() -> None:
    actions = voice_provider_remediation_actions(
        {
            "public_publication_voice_ready": False,
            "openai_tts_configured": True,
            "openai_tts_live_ready": False,
            "openai_tts_live_error": "insufficient_quota",
        },
        openai_api={"reachable": True, "status_code": 200, "error": None},
    )

    assert actions[0]["id"] == "fix_openai_tts_quota_or_project_limit"
    assert "API key is valid" in actions[0]["reason"]
    assert "project budget" in actions[0]["reason"]


def test_voice_provider_check_runs_live_probe_and_persists_report(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    report_path = settings.data_dir / "reports" / "voice_provider_doctor_test.json"
    md_path = settings.data_dir / "reports" / "voice_provider_doctor_test.md"

    async def fake_check(current_settings):
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{}", encoding="utf-8")
        md_path.write_text("# report", encoding="utf-8")
        return {
            "generated_at": "2026-06-06T12:45:00+00:00",
            "public_voice_ready": True,
            "selected_tts_backend": "openai_tts",
            "next_action": "premium_voice_ready_generate_private_public_quality_short",
            "providers": {
                "openai_tts": {"configured": True, "live_checked": True, "live_ready": True, "status_code": None, "error": None},
                "elevenlabs": {"configured": True, "live_checked": True, "live_ready": False, "status_code": 401, "error": "blocked"},
                "f5_tts": {"configured": True, "live_checked": True, "live_ready": False, "status_code": None, "error": "offline"},
            },
            "report_path": str(report_path),
            "markdown_report_path": str(md_path),
            "remediation_actions": [
                {
                    "id": "generate_private_public_quality_short",
                    "label": "Generate a private public-quality sample",
                    "kind": "safe_local_action",
                    "priority": "normal",
                    "reason": "At least one public-quality voice provider is live.",
                }
            ],
        }

    monkeypatch.setattr("atnos.api.routes.production.write_voice_provider_doctor_report", fake_check)

    with TestClient(app) as client:
        response = client.post("/api/production/voice-provider/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["public_voice_ready"] is True
    assert payload["next_action"] == "premium_voice_ready_generate_private_public_quality_short"
    assert payload["report_path"] == str(report_path)
    assert payload["markdown_report_path"] == str(md_path)
    assert payload["remediation_actions"][0]["id"] == "generate_private_public_quality_short"


def test_voice_provider_status_does_not_trust_stale_public_ready_report(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stale_time = (datetime.now(UTC) - timedelta(hours=12)).isoformat()
    (reports_dir / "voice_provider_doctor_stale.json").write_text(
        json.dumps(
            {
                "generated_at": stale_time,
                "public_voice_ready": True,
                "selected_tts_backend": "openai_tts",
                "next_action": "premium_voice_ready_generate_private_public_quality_short",
                "providers": {
                    "openai_tts": {
                        "configured": True,
                        "live_checked": True,
                        "live_ready": True,
                        "status_code": 200,
                        "error": None,
                        "model": "gpt-4o-mini-tts",
                    },
                    "elevenlabs": {"configured": True, "live_checked": True, "live_ready": False},
                    "f5_tts": {"configured": True, "live_checked": True, "live_ready": False},
                },
            }
        ),
        encoding="utf-8",
    )

    with TestClient(app) as client:
        response = client.get("/api/production/voice-provider/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["checked"] is True
    assert payload["report_stale"] is True
    assert payload["public_voice_ready"] is False
    assert payload["next_action"] == "run_voice_provider_doctor"


def test_voice_provider_status_returns_sanitized_remediation_actions(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "voice_provider_doctor_actions.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "public_voice_ready": False,
                "selected_tts_backend": "f5_tts_http",
                "next_action": "add_openai_billing_or_raise_project_quota",
                "remediation_actions": [
                    {
                        "id": "fix_openai_tts_quota_or_project_limit",
                        "label": "Fix OpenAI API billing/quota for TTS",
                        "kind": "owner_external_action",
                        "priority": "high",
                        "reason": "OpenAI returned insufficient_quota.",
                    }
                ],
                "providers": {
                    "openai_tts": {"configured": True, "live_checked": True, "live_ready": False, "status_code": 429},
                    "elevenlabs": {"configured": True, "live_checked": True, "live_ready": False},
                    "f5_tts": {"configured": True, "live_checked": True, "live_ready": False},
                },
            }
        ),
        encoding="utf-8",
    )

    with TestClient(app) as client:
        response = client.get("/api/production/voice-provider/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["remediation_actions"] == [
        {
            "id": "fix_openai_tts_quota_or_project_limit",
            "label": "Fix OpenAI API billing/quota for TTS",
            "kind": "owner_external_action",
            "priority": "high",
            "reason": "OpenAI returned insufficient_quota.",
        }
    ]


def test_f5_tts_runtime_status_reports_missing_runtime(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.f5_tts_install_dir = tmp_path / "missing-f5"
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_package_path", staticmethod(lambda name: None))

    with TestClient(app) as client:
        response = client.get("/api/production/voice-provider/f5-runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoint_ready"] is False
    assert payload["package_installed"] is False
    assert payload["gradio_installed"] is False
    assert payload["install_exists"] is False
    assert payload["launch_supported"] is False
    assert payload["diagnosis_code"] == "runtime_missing"
    assert payload["reference_audio_exists"] is False
    assert payload["blocking_reasons"]
    assert payload["next_action"] == "install_f5_tts_runtime"
    assert "api_key" not in str(payload).casefold()


def test_f5_tts_runtime_start_without_launch_command_is_safe_noop(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.f5_tts_launch_command = ""
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_package_path", staticmethod(lambda name: None))

    with TestClient(app) as client:
        response = client.post("/api/production/voice-provider/f5-runtime/start", json={"wait_timeout_s": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoint_ready"] is False
    assert payload["started"] is False
    assert payload["launch_command_configured"] is False
    assert payload["next_action"] == "install_f5_tts_runtime"


def test_f5_tts_runtime_start_uses_cold_start_safe_default(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    observed: list[int] = []

    class Result:
        @staticmethod
        def to_dict() -> dict[str, object]:
            return {
                "endpoint_ready": True,
                "started": True,
                "next_action": "f5_tts_ready_run_voice_provider_doctor",
            }

    def fake_start(self, *, wait_timeout_s: int = 120):
        observed.append(wait_timeout_s)
        return Result()

    monkeypatch.setattr(F5TTSRuntimeService, "start", fake_start)

    with TestClient(app) as client:
        response = client.post("/api/production/voice-provider/f5-runtime/start")

    assert response.status_code == 200
    assert response.json()["endpoint_ready"] is True
    assert observed == [120]


def test_f5_tts_runtime_detects_local_install_dir_and_requests_dependency_setup(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    install_dir = tmp_path / "F5-TTS"
    install_dir.mkdir()
    (install_dir / "pyproject.toml").write_text("[project]\nname = 'f5-tts'\n", encoding="utf-8")
    app.state.settings.f5_tts_install_dir = install_dir
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_package_path", staticmethod(lambda name: None))

    status = F5TTSRuntimeService(app.state.settings).status().to_dict()

    assert status["install_exists"] is True
    assert status["install_dir"] == str(install_dir.resolve())
    assert any(path.endswith("pyproject.toml") for path in status["detected_files"])
    assert status["next_action"] == "install_f5_tts_python_dependencies_and_configure_launch_command"


def test_f5_tts_runtime_health_check_reports_offline_server_and_missing_reference_audio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    runtime = tmp_path / "F5-TTS-runtime"
    python = runtime / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-placeholder")
    app.state.settings.f5_tts_install_dir = runtime
    app.state.settings.f5_tts_runtime_python = python
    app.state.settings.f5_tts_launch_command = "python -m f5_tts.infer.infer_gradio -a"
    app.state.settings.f5_tts_ref_audio_path = None
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "connection refused"))
    monkeypatch.setattr(F5TTSRuntimeService, "_package_path", staticmethod(lambda name: None))
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_runtime_python", staticmethod(lambda path: (True, True, None)))

    status = F5TTSRuntimeService(app.state.settings).status().to_dict()

    assert status["diagnosis_code"] == "server_offline"
    assert status["reference_audio_exists"] is False
    assert any("Serwer F5-TTS" in reason for reason in status["blocking_reasons"])
    assert any("WAV" in reason for reason in status["blocking_reasons"])


@pytest.mark.parametrize("reference_exists", [True, False])
def test_f5_cli_readiness_does_not_require_http_or_gradio(tmp_path: Path, monkeypatch, reference_exists) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.f5_tts_transport = "cli"
    settings.f5_tts_launch_command = ""
    runtime_python = tmp_path / "python.exe"
    runtime_python.write_bytes(b"python-placeholder")
    reference = tmp_path / "reference.wav"
    if reference_exists:
        reference.write_bytes(b"wave-placeholder")
    monkeypatch.setattr(F5TTSRuntimeService, "resolve_runtime_python", lambda *args: runtime_python)
    monkeypatch.setattr(F5TTSRuntimeService, "resolve_reference_audio", lambda *args: reference)
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda *args: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_package_path", staticmethod(lambda name: None))
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_runtime_python", staticmethod(lambda path: (True, False, None)))
    with patch("atnos.services.f5_tts_runtime_service.subprocess.Popen") as popen:
        status = F5TTSRuntimeService(settings).start(wait_timeout_s=1).to_dict()
    popen.assert_not_called()
    assert status["endpoint_ready"] is False
    assert status["transport"] == "cli"
    assert status["transport_ready"] == reference_exists
    assert status["diagnosis_code"] == ("cli_ready" if reference_exists else "reference_audio_missing")
    assert not any("Serwer" in reason or "Gradio" in reason for reason in status["blocking_reasons"])


def test_f5_tts_start_is_blocked_while_comfyui_is_ready(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.f5_tts_launch_command = "python -m f5_tts.infer.infer_gradio"
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_comfyui_ready", lambda self: True)

    with patch("atnos.services.f5_tts_runtime_service.subprocess.Popen") as popen:
        with TestClient(app) as client:
            response = client.post("/api/production/voice-provider/f5-runtime/start", json={"wait_timeout_s": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoint_ready"] is False
    assert payload["started"] is False
    assert payload["diagnosis_code"] == "gpu_conflict_comfyui"
    assert payload["next_action"] == "stop_comfyui_before_f5_tts"
    popen.assert_not_called()


def test_f5_tts_start_prefers_configured_runtime_python_over_cmd_shell(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    runtime = tmp_path / "F5-TTS-runtime"
    python = runtime / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-placeholder")
    app.state.settings.f5_tts_install_dir = runtime
    app.state.settings.f5_tts_runtime_python = python
    app.state.settings.f5_tts_gradio_temp_dir = tmp_path / "gradio-temp"
    app.state.settings.f5_tts_launch_command = '"python.exe" -m f5_tts.infer.infer_gradio'
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_package_path", staticmethod(lambda name: None))
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_runtime_python", staticmethod(lambda path: (True, True, None)))
    monkeypatch.setattr(F5TTSRuntimeService, "_comfyui_ready", lambda self: False)
    monkeypatch.setattr(F5TTSRuntimeService, "_wait_until_ready", lambda self, wait_timeout_s: False)

    with patch("atnos.services.f5_tts_runtime_service.subprocess.Popen") as popen:
        F5TTSRuntimeService(app.state.settings).start(wait_timeout_s=1)

    args, kwargs = popen.call_args
    assert args[0][:4] == [str(python), "-m", "f5_tts.infer.infer_gradio", "-a"]
    assert args[0][4:] == ["-H", "127.0.0.1", "-p", "7860"]
    assert kwargs["cwd"] == str(runtime.resolve())
    assert kwargs["env"]["GRADIO_TEMP_DIR"] == str(app.state.settings.f5_tts_gradio_temp_dir)
    assert kwargs["env"]["HF_HOME"] == str((app.state.settings.data_dir / "tool-caches" / "huggingface").resolve())
    assert kwargs["env"]["TRANSFORMERS_CACHE"] == str(
        (app.state.settings.data_dir / "tool-caches" / "huggingface" / "transformers").resolve()
    )


def test_f5_tts_start_uses_app_control_compatible_launcher_when_available(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    runtime = tmp_path / "F5-TTS-runtime"
    python = runtime / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-placeholder")
    compat_launcher = (
        app.state.settings.app_root
        / "runtime-recovered"
        / "api"
        / "scripts"
        / "run_f5_tts_compat.py"
    )
    compat_launcher.parent.mkdir(parents=True, exist_ok=True)
    compat_launcher.write_text("# test launcher\n", encoding="utf-8")
    app.state.settings.f5_tts_install_dir = runtime
    app.state.settings.f5_tts_runtime_python = python
    app.state.settings.f5_tts_launch_command = "python -m f5_tts.infer.infer_gradio"
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_package_path", staticmethod(lambda name: None))
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_runtime_python", staticmethod(lambda path: (True, True, None)))
    monkeypatch.setattr(F5TTSRuntimeService, "_comfyui_ready", lambda self: False)
    monkeypatch.setattr(F5TTSRuntimeService, "_wait_until_ready", lambda self, wait_timeout_s: False)

    with patch("atnos.services.f5_tts_runtime_service.subprocess.Popen") as popen:
        F5TTSRuntimeService(app.state.settings).start(wait_timeout_s=1)

    args, _kwargs = popen.call_args
    assert args[0][:2] == [str(python), str(compat_launcher.resolve())]
    assert args[0][2:] == ["-a", "-H", "127.0.0.1", "-p", "7860"]


def test_f5_tts_stop_terminates_hung_runtime_before_endpoint_is_ready(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(F5TTSRuntimeService, "_probe_endpoint", lambda self, endpoint: (False, None, "offline"))
    monkeypatch.setattr(F5TTSRuntimeService, "_terminate_runtime_processes", lambda self: True)

    status = F5TTSRuntimeService(app.state.settings).stop(wait_timeout_s=1)

    assert status.endpoint_ready is False
    assert status.stopped is True


def test_f5_tts_terminate_recognizes_compat_launcher(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    runtime = tmp_path / "F5-TTS-runtime"
    python = runtime / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-placeholder")
    app.state.settings.f5_tts_install_dir = runtime
    app.state.settings.f5_tts_runtime_python = python

    process = MagicMock()
    process.info = {
        "pid": 123,
        "exe": str(python),
        "cmdline": [str(python), str(tmp_path / "run_f5_tts_compat.py"), "-p", "7860"],
    }
    monkeypatch.setattr("atnos.services.f5_tts_runtime_service.psutil.process_iter", lambda _attrs: [process])
    monkeypatch.setattr("atnos.services.f5_tts_runtime_service.psutil.wait_procs", lambda _items, timeout: ([], []))

    terminated = F5TTSRuntimeService(app.state.settings)._terminate_runtime_processes()

    assert terminated is True
    process.terminate.assert_called_once_with()


def test_comfyui_startup_is_blocked_while_f5_tts_is_ready(tmp_path: Path, monkeypatch) -> None:
    from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

    app = create_test_app(tmp_path)
    service = ComfyUiRuntimeService(app.state.settings)
    monkeypatch.setattr(service, "_server_ready", lambda: False)
    monkeypatch.setattr(service, "_f5_tts_ready", lambda: True)

    readiness = service.ensure_startup_ready(wait_timeout_s=1)

    assert readiness.image_generation is False
    assert readiness.reason == "GPU runtime conflict"
    assert "Stop F5-TTS" in str(readiness.detail)


def test_comfyui_startup_uses_configured_lowvram_by_default(tmp_path: Path, monkeypatch) -> None:
    from atnos.services.comfyui_runtime_service import ComfyUiInstallState, ComfyUiRuntimeService

    app = create_test_app(tmp_path)
    app.state.settings.comfyui_lowvram = True
    service = ComfyUiRuntimeService(app.state.settings)
    install = ComfyUiInstallState(
        install_dir=tmp_path,
        run_script=None,
        fallback_script=None,
        python_executable=tmp_path / "python.exe",
        main_script=tmp_path / "main.py",
        checkpoint_dir=tmp_path,
        exists=True,
    )
    launch_calls: list[tuple[bool, bool]] = []
    monkeypatch.setattr(service, "_server_ready", lambda: False)
    monkeypatch.setattr(service, "_f5_tts_ready", lambda: False)
    monkeypatch.setattr(service, "describe_install", lambda: install)
    monkeypatch.setattr(
        service,
        "_launch_from_state",
        lambda _state, *, lowvram, disable_smart_memory: launch_calls.append((lowvram, disable_smart_memory)) or True,
    )
    monkeypatch.setattr(service, "_wait_until_ready", lambda *, wait_timeout_s: True)

    readiness = service.ensure_startup_ready(wait_timeout_s=1)

    assert readiness.image_generation is True
    assert readiness.lowvram is True
    assert launch_calls == [(True, False)]


def test_comfyui_stop_refuses_active_queue(tmp_path: Path, monkeypatch) -> None:
    from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

    app = create_test_app(tmp_path)
    service = ComfyUiRuntimeService(app.state.settings)
    monkeypatch.setattr(service, "_server_ready", lambda: True)
    monkeypatch.setattr(service, "_queue_counts", lambda: (1, 2))
    terminate_calls: list[bool] = []
    monkeypatch.setattr(service, "_terminate_runtime_processes", lambda: terminate_calls.append(True))

    result = service.stop_server(require_idle=True)

    assert result["status"] == "busy"
    assert result["stopped"] is False
    assert result["queue_running"] == 1
    assert result["queue_pending"] == 2
    assert terminate_calls == []


def test_comfyui_stop_releases_idle_runtime(tmp_path: Path, monkeypatch) -> None:
    from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

    app = create_test_app(tmp_path)
    service = ComfyUiRuntimeService(app.state.settings)
    readiness = iter((True, False))
    monkeypatch.setattr(service, "_server_ready", lambda: next(readiness))
    monkeypatch.setattr(service, "_queue_counts", lambda: (0, 0))
    terminate_calls: list[bool] = []
    monkeypatch.setattr(service, "_terminate_runtime_processes", lambda: terminate_calls.append(True))

    result = service.stop_server(require_idle=True)

    assert result["status"] == "stopped"
    assert result["stopped"] is True
    assert terminate_calls == [True]
