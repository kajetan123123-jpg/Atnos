from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.wav2lip_runtime_service import Wav2LipRuntimeService


class Wav2LipRuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        comfy_root = self.settings.comfyui_install_dir
        (comfy_root / "python_embeded").mkdir(parents=True, exist_ok=True)
        (comfy_root / "python_embeded" / "python.exe").write_text("python", encoding="utf-8")
        custom_node_dir = comfy_root / "ComfyUI" / "custom_nodes" / "ComfyUI_wav2lip" / "Wav2Lip"
        custom_node_dir.mkdir(parents=True, exist_ok=True)
        (custom_node_dir / "wav2lip_node.py").write_text("print('ok')", encoding="utf-8")
        self.settings.wav2lip_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.wav2lip_checkpoint_path.write_text("checkpoint", encoding="utf-8")
        self.settings.wav2lip_face_detector_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.wav2lip_face_detector_path.write_text("detector", encoding="utf-8")
        runner_dir = self.settings.app_root / "scripts"
        runner_dir.mkdir(parents=True, exist_ok=True)
        (runner_dir / "wav2lip_runner.py").write_text("print('runner')", encoding="utf-8")

    def test_describe_install_detects_custom_node_layout(self) -> None:
        service = Wav2LipRuntimeService(self.settings)
        state = service.describe_install()

        self.assertTrue(state.exists)
        self.assertIsNotNone(state.custom_node_dir)
        self.assertEqual(state.checkpoint_path, self.settings.wav2lip_checkpoint_path.resolve())
        self.assertEqual(state.face_detector_path, self.settings.wav2lip_face_detector_path.resolve())

    def test_dependency_status_reads_embedded_python_probe(self) -> None:
        service = Wav2LipRuntimeService(self.settings)
        stdout = '{"cv2": true, "librosa": true, "pydub": true, "soundfile": true, "numba": true, "scipy": true, "tqdm": true}'
        with patch("atnos.services.wav2lip_runtime_service.subprocess.run") as mocked_run:
            mocked_run.return_value.returncode = 0
            mocked_run.return_value.stdout = stdout
            mocked_run.return_value.stderr = ""
            status = service.dependency_status()

        self.assertTrue(all(status.values()))

    def test_verify_face_detectable_uses_verify_mode(self) -> None:
        service = Wav2LipRuntimeService(self.settings)
        portrait = self.root / "portrait.png"
        portrait.write_bytes(b"portrait")
        state = service.describe_install()
        with (
            patch.object(service, "ensure_ready", return_value=state),
            patch("atnos.services.wav2lip_runtime_service.subprocess.run") as mocked_run,
        ):
            mocked_run.return_value.returncode = 0
            mocked_run.return_value.stdout = '{"verified": true}'
            mocked_run.return_value.stderr = ""

            service.verify_face_detectable(portrait_path=portrait)

        command = mocked_run.call_args.args[0]
        self.assertIn("--verify-face-only", command)
        self.assertIn(str(portrait.resolve()), command)


if __name__ == "__main__":
    unittest.main()
