from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeProbeService

from test_support import build_test_settings, make_temp_dir


class LocalShortRuntimeProbeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.ffmpeg_path = self.root / "bin" / "ffmpeg.exe"
        self.piper_exe = self.root / "bin" / "piper.exe"
        self.whisper_exe = self.root / "bin" / "whisper-cli.exe"
        self.piper_model = self.root / "models" / "piper" / "pl_PL-darkman-medium.onnx"
        self.piper_config = self.root / "models" / "piper" / "pl_PL-darkman-medium.onnx.json"
        self.whisper_model = self.root / "models" / "whisper" / "ggml-medium.bin"
        for path in (
            self.ffmpeg_path,
            self.piper_exe,
            self.whisper_exe,
            self.piper_model,
            self.piper_config,
            self.whisper_model,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stub", encoding="utf-8")
        self.settings.piper_exe = str(self.piper_exe)
        self.settings.piper_pl_model_path = self.piper_model
        self.settings.piper_pl_config_path = self.piper_config
        self.settings.whisper_cpp_exe = str(self.whisper_exe)
        self.settings.whisper_model_path = self.whisper_model
        self.settings.ollama_synthesis_model = "qwen3:8b"

    def test_snapshot_reports_ready_when_all_components_exist(self) -> None:
        service = LocalShortRuntimeProbeService(self.settings)

        with patch(
            "atnos.services.local_short_runtime_probe_service.imageio_ffmpeg.get_ffmpeg_exe",
            return_value=str(self.ffmpeg_path),
        ), patch(
            "atnos.services.local_short_runtime_probe_service.requests.get",
            return_value=type(
                "_Resp",
                (),
                {"status_code": 200, "json": lambda self: {"models": [{"name": "qwen3:8b"}]}},
            )(),
        ):
            snapshot = service.snapshot()

        self.assertTrue(snapshot.enabled)
        self.assertTrue(snapshot.ready)
        self.assertEqual(len(snapshot.components), 4)

    def test_snapshot_reports_degraded_ollama_fallback_when_main_model_is_missing(self) -> None:
        self.settings.ollama_synthesis_model = "qwen3:14b"
        self.settings.ollama_fast_model = "qwen3:8b"
        service = LocalShortRuntimeProbeService(self.settings)

        with patch(
            "atnos.services.local_short_runtime_probe_service.imageio_ffmpeg.get_ffmpeg_exe",
            return_value=str(self.ffmpeg_path),
        ), patch(
            "atnos.services.local_short_runtime_probe_service.requests.get",
            return_value=type(
                "_Resp",
                (),
                {"status_code": 200, "json": lambda self: {"models": [{"name": "qwen3:8b"}]}},
            )(),
        ):
            snapshot = service.snapshot()

        self.assertTrue(snapshot.ready)
        ollama = next(item for item in snapshot.components if item.key == "ollama")
        self.assertIn("qwen3:14b", ollama.reason)
        self.assertIn("qwen3:8b", ollama.reason)

    def test_snapshot_reports_missing_components(self) -> None:
        self.whisper_model.unlink()
        service = LocalShortRuntimeProbeService(self.settings)

        with patch(
            "atnos.services.local_short_runtime_probe_service.imageio_ffmpeg.get_ffmpeg_exe",
            return_value=str(self.ffmpeg_path),
        ), patch(
            "atnos.services.local_short_runtime_probe_service.requests.get",
            side_effect=RuntimeError("offline"),
        ):
            snapshot = service.snapshot()

        self.assertFalse(snapshot.ready)
        self.assertIn("Brakuje gotowych runtime'ow", snapshot.reason)
        failing_keys = {item.key for item in snapshot.components if not item.ready}
        self.assertIn("ollama", failing_keys)
        self.assertIn("whisper_cpp", failing_keys)

    def test_snapshot_uses_elevenlabs_tts_component_when_configured(self) -> None:
        self.settings.shorts_tts_backend = "elevenlabs"
        self.settings.shorts_allow_cloud_tts = True
        self.settings.elevenlabs_api_key = "test-key"
        self.settings.elevenlabs_voice_id = "voice-123"
        service = LocalShortRuntimeProbeService(self.settings)

        with patch(
            "atnos.services.local_short_runtime_probe_service.imageio_ffmpeg.get_ffmpeg_exe",
            return_value=str(self.ffmpeg_path),
        ), patch(
            "atnos.services.local_short_runtime_probe_service.requests.get",
            return_value=type(
                "_Resp",
                (),
                {"status_code": 200, "json": lambda self: {"models": [{"name": "qwen3:8b"}]}},
            )(),
        ):
            snapshot = service.snapshot()

        self.assertTrue(snapshot.ready)
        component_keys = {item.key for item in snapshot.components}
        self.assertIn("elevenlabs_tts", component_keys)
        self.assertNotIn("piper", component_keys)

    def test_snapshot_accepts_profile_specific_elevenlabs_voice(self) -> None:
        self.settings.shorts_tts_backend = "elevenlabs"
        self.settings.shorts_allow_cloud_tts = True
        self.settings.elevenlabs_api_key = "test-key"
        self.settings.elevenlabs_voice_id = None
        self.settings.elevenlabs_voice_id_wealth_blueprint = "wealth-voice"
        service = LocalShortRuntimeProbeService(self.settings)

        with patch(
            "atnos.services.local_short_runtime_probe_service.imageio_ffmpeg.get_ffmpeg_exe",
            return_value=str(self.ffmpeg_path),
        ), patch(
            "atnos.services.local_short_runtime_probe_service.requests.get",
            return_value=type(
                "_Resp",
                (),
                {"status_code": 200, "json": lambda self: {"models": [{"name": "qwen3:8b"}]}},
            )(),
        ):
            snapshot = service.snapshot()

        self.assertTrue(snapshot.ready)
        elevenlabs = next(item for item in snapshot.components if item.key == "elevenlabs_tts")
        self.assertEqual(elevenlabs.path, "wealth-voice")

    def test_snapshot_rejects_elevenlabs_when_all_voice_ids_are_missing(self) -> None:
        self.settings.shorts_tts_backend = "elevenlabs"
        self.settings.shorts_allow_cloud_tts = True
        self.settings.elevenlabs_api_key = "test-key"
        self.settings.elevenlabs_voice_id = None
        self.settings.elevenlabs_voice_id_wealth_blueprint = None
        self.settings.elevenlabs_voice_id_dark_chronicles = None
        self.settings.elevenlabs_voice_id_sigma_squad = None
        service = LocalShortRuntimeProbeService(self.settings)

        with patch(
            "atnos.services.local_short_runtime_probe_service.imageio_ffmpeg.get_ffmpeg_exe",
            return_value=str(self.ffmpeg_path),
        ), patch(
            "atnos.services.local_short_runtime_probe_service.requests.get",
            return_value=type(
                "_Resp",
                (),
                {"status_code": 200, "json": lambda self: {"models": [{"name": "qwen3:8b"}]}},
            )(),
        ):
            snapshot = service.snapshot()

        self.assertFalse(snapshot.ready)
        elevenlabs = next(item for item in snapshot.components if item.key == "elevenlabs_tts")
        self.assertEqual(elevenlabs.reason, "ELEVENLABS_VOICE_ID is missing.")


if __name__ == "__main__":
    unittest.main()
