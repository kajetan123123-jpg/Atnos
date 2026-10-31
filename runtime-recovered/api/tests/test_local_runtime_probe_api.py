from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, create_test_app, make_temp_dir

from atnos.services.local_runtime_probe_service import (
    HttpProbeResult,
    LocalRuntimeProbeService,
    LocalRuntimeProbeView,
)
from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeAssets
from atnos.services.wav2lip_runtime_service import Wav2LipInstallState


class LocalRuntimeProbeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_probe_ollama_reports_ready_when_endpoint_responds(self) -> None:
        self.settings.ollama_main_model = "qwen3:8b"
        self.settings.ollama_chat_model = "qwen3:8b"
        self.settings.ollama_fast_model = "qwen3:8b"
        self.settings.ollama_vision_model = "qwen3-vl:8b"
        service = LocalRuntimeProbeService(self.settings)
        with patch.object(
            service,
            "_probe_http_json",
            return_value=HttpProbeResult(
                ok=True,
                status_code=200,
                payload={"models": [{"name": "qwen3:8b"}, {"name": "qwen3-vl:8b"}]},
                error=None,
                latency_ms=18,
            ),
        ):
            result = service.probe_ollama()

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.ready)
        self.assertIn("installed_models", result.details)
        self.assertEqual(result.details["installed_models"], ["qwen3:8b", "qwen3-vl:8b"])

    def test_probe_ollama_reports_fallback_only_when_main_model_is_missing(self) -> None:
        self.settings.ollama_main_model = "qwen3:14b"
        self.settings.ollama_chat_model = "qwen3:14b"
        self.settings.ollama_fast_model = "qwen3:8b"
        self.settings.ollama_vision_model = "qwen3-vl:8b"
        service = LocalRuntimeProbeService(self.settings)
        with patch.object(
            service,
            "_probe_http_json",
            return_value=HttpProbeResult(
                ok=True,
                status_code=200,
                payload={"models": [{"name": "qwen3:8b"}, {"name": "qwen3-vl:8b"}]},
                error=None,
                latency_ms=18,
            ),
        ):
            result = service.probe_ollama()

        self.assertEqual(result.status, "fallback_only")
        self.assertFalse(result.ready)
        self.assertEqual(result.details["model_roles"]["active_text_model"], "qwen3:8b")
        self.assertIn("qwen3:14b", result.details["model_roles"]["missing_models"])

    def test_probe_comfyui_reports_configured_when_files_exist_but_server_is_down(self) -> None:
        comfy_dir = self.settings.comfyui_install_dir
        (comfy_dir / "ComfyUI").mkdir(parents=True, exist_ok=True)
        (comfy_dir / "ComfyUI" / "main.py").write_text("print('ok')", encoding="utf-8")
        (comfy_dir / "run_amd_gpu.bat").write_text("echo ok", encoding="utf-8")
        service = LocalRuntimeProbeService(self.settings)
        with patch.object(
            service,
            "_probe_http_json",
            return_value=HttpProbeResult(
                ok=False,
                status_code=None,
                payload=None,
                error="connection refused",
                latency_ms=None,
            ),
        ):
            result = service.probe_comfyui()

        self.assertEqual(result.status, "configured")
        self.assertFalse(result.ready)
        self.assertIn("artifact_hits", result.details)
        self.assertTrue(result.details["artifact_hits"])

    def test_probe_comfyui_reports_configured_when_server_is_up_but_no_checkpoint_exists(self) -> None:
        service = LocalRuntimeProbeService(self.settings)
        with patch.object(
            service,
            "_probe_http_json",
            return_value=HttpProbeResult(
                ok=True,
                status_code=200,
                payload={"system": {"os": "windows"}},
                error=None,
                latency_ms=15,
            ),
        ):
            result = service.probe_comfyui()

        self.assertEqual(result.status, "configured")
        self.assertFalse(result.ready)
        self.assertEqual(result.details["checkpoint_hits"], [])

    def test_probe_ffmpeg_amf_reports_ready_when_encoder_is_present(self) -> None:
        service = LocalRuntimeProbeService(self.settings)
        with patch.object(service, "_resolve_ffmpeg_path", return_value="F:/Apps/ffmpeg/bin/ffmpeg.exe"):
            with patch("atnos.services.local_runtime_probe_service.subprocess.run") as mocked_run:
                mocked_run.return_value.returncode = 0
                mocked_run.return_value.stdout = " V..... h264_amf\n V..... av1_amf\n"
                mocked_run.return_value.stderr = ""
                result = service.probe_ffmpeg_amf()

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.ready)
        self.assertIn("h264_amf", result.details["available_amf_encoders"])

    def test_probe_kokoro_local_reports_ready_when_runtime_assets_exist(self) -> None:
        service = LocalRuntimeProbeService(self.settings)
        assets = KokoroLocalRuntimeAssets(
            python_module="kokoro_onnx",
            model_path=self.settings.kokoro_model_path,
            voices_path=self.settings.kokoro_voices_path,
            preferred_voice="af_heart",
            espeak_data_path="F:/Apps/eSpeak/data",
            espeak_library_path="F:/Apps/eSpeak/libespeak-ng.dll",
        )
        with patch(
            "atnos.services.local_runtime_probe_service.KokoroLocalRuntimeService.available_python_module",
            return_value="kokoro_onnx",
        ):
            with patch(
                "atnos.services.local_runtime_probe_service.KokoroLocalRuntimeService.resolve_assets",
                return_value=assets,
            ):
                result = service.probe_kokoro_local()

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.ready)
        self.assertEqual(result.details["preferred_voice"], "af_heart")

    def test_probe_wav2lip_reports_ready_when_runtime_is_complete(self) -> None:
        service = LocalRuntimeProbeService(self.settings)
        custom_node_dir = self.settings.comfyui_install_dir / "ComfyUI" / "custom_nodes" / "ComfyUI_wav2lip"
        runner_script = self.settings.app_root / "scripts" / "wav2lip_runner.py"
        state = Wav2LipInstallState(
            custom_node_dir=custom_node_dir,
            python_executable=self.settings.comfyui_install_dir / "python_embeded" / "python.exe",
            runner_script=runner_script,
            checkpoint_path=self.settings.wav2lip_checkpoint_path,
            face_detector_path=self.settings.wav2lip_face_detector_path,
            exists=True,
        )
        with patch(
            "atnos.services.local_runtime_probe_service.Wav2LipRuntimeService.describe_install",
            return_value=state,
        ):
            with patch(
                "atnos.services.local_runtime_probe_service.Wav2LipRuntimeService.dependency_status",
                return_value={
                    "cv2": True,
                    "librosa": True,
                    "pydub": True,
                    "soundfile": True,
                    "numba": True,
                    "scipy": True,
                    "tqdm": True,
                },
            ):
                result = service.probe_wav2lip()

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.ready)
        self.assertEqual(result.details["dependency_state"]["cv2"], True)


class LocalRuntimeProbeApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_local_runtime_probe_endpoint_returns_runtime_rows(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        fake_items = [
            LocalRuntimeProbeView(
                key="ollama-local",
                label="Ollama Local",
                status="ready",
                ready=True,
                probe_kind="http",
                reason="ok",
                checked_at=datetime.now(timezone.utc),
                details={"installed_models": ["qwen3:8b"]},
                suggestions=("none",),
            ),
            LocalRuntimeProbeView(
                key="comfyui",
                label="ComfyUI",
                status="missing",
                ready=False,
                probe_kind="http",
                reason="missing",
                checked_at=datetime.now(timezone.utc),
                details={},
                suggestions=("install",),
            ),
        ]

        with patch(
            "atnos.api.routes.connectors.LocalRuntimeProbeService.probe_all",
            return_value=fake_items,
        ):
            with TestClient(app) as client:
                response = client.get("/connectors/local-runtimes/probe")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["key"], "ollama-local")
        self.assertTrue(payload[0]["ready"])
        self.assertEqual(payload[1]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
