from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleError, WhisperCppSubtitleService

from test_support import build_test_settings, make_temp_dir


class WhisperCppSubtitleServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.exe_path = self.root / "bin" / "whisper-cli.exe"
        self.model_path = self.root / "models" / "whisper" / "ggml-medium.bin"
        self.audio_path = self.root / "audio.wav"
        self.exe_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.exe_path.write_text("stub", encoding="utf-8")
        self.model_path.write_text("model", encoding="utf-8")
        self.audio_path.write_bytes(b"RIFFaudio-data")
        self.settings.whisper_cpp_exe = str(self.exe_path)
        self.settings.whisper_model_path = self.model_path
        self.settings.whisper_language = "pl"

    def test_runtime_availability_reports_missing_executable(self) -> None:
        self.exe_path.unlink()
        service = WhisperCppSubtitleService(self.settings)

        runtime = service.runtime_availability()

        self.assertFalse(runtime.available)
        self.assertIn("not configured or not found", runtime.reason)

    def test_build_command_uses_srt_output_flags(self) -> None:
        service = WhisperCppSubtitleService(self.settings)
        output_path = self.root / "captions.srt"

        command = service.build_command(audio_path=self.audio_path, output_path=output_path)

        self.assertEqual(Path(command[0]).resolve(), self.exe_path.resolve())
        self.assertIn("-osrt", command)
        self.assertIn("-of", command)
        self.assertIn("-l", command)
        self.assertIn("pl", command)

    def test_generate_writes_srt_descriptor(self) -> None:
        service = WhisperCppSubtitleService(self.settings)

        def fake_run(command, **_kwargs):
            output_base = Path(command[command.index("-of") + 1])
            output_base.with_suffix(".srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nTest\n",
                encoding="utf-8",
            )
            return type("_Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("atnos.services.whisper_cpp_subtitle_service.subprocess.run", side_effect=fake_run):
            descriptor = service.generate(audio_path=self.audio_path, conversation_id=42)

        self.assertEqual(descriptor.filename, "atnos-subtitles.srt")
        self.assertEqual(descriptor.file_extension, ".srt")
        self.assertTrue(descriptor.absolute_path.exists())

    def test_generate_raises_when_runtime_fails(self) -> None:
        service = WhisperCppSubtitleService(self.settings)

        with patch(
            "atnos.services.whisper_cpp_subtitle_service.subprocess.run",
            return_value=type("_Result", (), {"returncode": 1, "stdout": "", "stderr": "boom"})(),
        ):
            with self.assertRaises(WhisperCppSubtitleError):
                service.generate(audio_path=self.audio_path, conversation_id=42)


if __name__ == "__main__":
    unittest.main()
