from __future__ import annotations

from pathlib import Path
import queue
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.render_service import RenderArtifactQa, RenderService, resolve_video_export_attempts


class _FakeQueue:
    def get_nowait(self):
        raise queue.Empty


class _FakeProcess:
    def __init__(self) -> None:
        self.exitcode = 0
        self._alive = True

    def start(self) -> None:
        return None

    def join(self, timeout=None) -> None:
        return None

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self._alive = False


class RenderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_watchdog_timeout_recovers_valid_output(self) -> None:
        service = RenderService(self.settings)
        output_path = service.artifacts.create_output_path(conversation_id=5, extension=".mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"0" * (RenderService.MIN_OUTPUT_SIZE_BYTES + 128))

        audio_path = self.root / "narration.wav"
        audio_path.write_bytes(b"fake-audio")
        frame_path = self.root / "frame.png"
        frame_path.write_bytes(b"fake-frame")

        captured: dict[str, object] = {}

        def fake_validate(path: Path, *, extra_warnings=()):
            captured["path"] = path
            captured["extra_warnings"] = extra_warnings
            return RenderArtifactQa(
                duration_sec=23.14,
                width=1080,
                height=1920,
                has_audio=True,
                size_bytes=output_path.stat().st_size,
                warnings=tuple(extra_warnings),
            )

        fake_context = SimpleNamespace(
            Queue=lambda: _FakeQueue(),
            Process=lambda *args, **kwargs: _FakeProcess(),
        )

        with patch(
            "atnos.services.render_service.multiprocessing.get_context",
            return_value=fake_context,
        ), patch.object(
            service.artifacts,
            "create_output_path",
            return_value=output_path,
        ), patch.object(
            service,
            "_validate_render_output",
            side_effect=fake_validate,
        ):
            result = service.render_short(
                frame_paths=[frame_path],
                audio_path=audio_path,
                conversation_id=5,
                subtitles_path=None,
            )

        self.assertTrue(result.watchdog_recovered)
        self.assertEqual(result.descriptor.absolute_path.resolve(), output_path.resolve())
        self.assertEqual(captured["path"], output_path)
        self.assertTrue(captured["extra_warnings"])
        self.assertIn("watchdog", str(captured["extra_warnings"][0]).lower())

    def test_quality_render_profile_prefers_slower_h264_export(self) -> None:
        attempts = resolve_video_export_attempts(render_profile="quality", force_cpu_path=False)

        self.assertEqual(attempts[0][0], "libx264")
        self.assertIn("medium", attempts[0][1])
        self.assertIn("18", attempts[0][1])

    def test_balanced_render_profile_keeps_gpu_then_cpu_fallback(self) -> None:
        attempts = resolve_video_export_attempts(render_profile="balanced", force_cpu_path=False)

        self.assertEqual(attempts[0][0], "h264_amf")
        self.assertEqual(attempts[1][0], "libx264")

    def test_fast_recovery_profile_uses_ultrafast_cpu_encode(self) -> None:
        attempts = resolve_video_export_attempts(render_profile="fast_recovery", force_cpu_path=False)

        self.assertEqual(attempts[0][0], "libx264")
        self.assertIn("ultrafast", attempts[0][1])


if __name__ == "__main__":
    unittest.main()
