from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.piper_tts_service import PiperTtsError, PiperTtsService

from test_support import build_test_settings, make_temp_dir


class PiperTtsServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.exe_path = self.root / "bin" / "piper.exe"
        self.model_path = self.root / "models" / "piper" / "pl_PL-darkman-medium.onnx"
        self.config_path = self.root / "models" / "piper" / "pl_PL-darkman-medium.onnx.json"
        self.exe_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.exe_path.write_text("stub", encoding="utf-8")
        self.model_path.write_text("model", encoding="utf-8")
        self.config_path.write_text("{}", encoding="utf-8")
        self.settings.piper_exe = str(self.exe_path)
        self.settings.piper_pl_model_path = self.model_path
        self.settings.piper_pl_config_path = self.config_path

    def test_runtime_availability_reports_missing_model(self) -> None:
        self.model_path.unlink()
        service = PiperTtsService(self.settings)

        runtime = service.runtime_availability()

        self.assertFalse(runtime.available)
        self.assertIn("model file is missing", runtime.reason)

    def test_build_command_uses_input_file_and_output_file(self) -> None:
        service = PiperTtsService(self.settings)
        input_path = self.root / "input.txt"
        output_path = self.root / "output.wav"

        command = service.build_command(input_path=input_path, output_path=output_path, speed=1.2)

        self.assertEqual(Path(command[0]).resolve(), self.exe_path.resolve())
        self.assertIn("--input-file", command)
        self.assertIn("-f", command)
        self.assertIn(str(output_path), command)
        self.assertEqual(Path(command[command.index("-m") + 1]).resolve(), self.model_path.resolve())
        self.assertIn("--length-scale", command)

    def test_synthesize_creates_managed_wav_descriptor(self) -> None:
        service = PiperTtsService(self.settings)

        def fake_run(command, **_kwargs):
            output_index = command.index("-f") + 1
            output_path = Path(command[output_index])
            output_path.write_bytes(b"RIFFstub-wave-data")
            return type("_Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("atnos.services.piper_tts_service.subprocess.run", side_effect=fake_run):
            descriptor = service.synthesize(
                text="To jest test lokalnego lektora shorta.",
                conversation_id=42,
            )

        self.assertEqual(descriptor.filename, "atnos-narration.wav")
        self.assertEqual(descriptor.file_extension, ".wav")
        self.assertTrue(descriptor.absolute_path.exists())

    def test_synthesize_raises_when_no_output_is_written(self) -> None:
        service = PiperTtsService(self.settings)

        with patch(
            "atnos.services.piper_tts_service.subprocess.run",
            return_value=type("_Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        ):
            with self.assertRaises(PiperTtsError):
                service.synthesize(text="Test", conversation_id=1)


if __name__ == "__main__":
    unittest.main()
