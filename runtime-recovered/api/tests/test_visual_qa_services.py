from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atnos.core.config import Settings
from atnos.services.screen_capture_service import ScreenCaptureError, ScreenCaptureService
from atnos.services.visual_qa_service import VisualQAService


class VisualQAServicesTest(unittest.TestCase):
    def test_ffmpeg_record_command_uses_windows_gdigrab(self) -> None:
        command = ScreenCaptureService.build_ffmpeg_record_command(
            ffmpeg="ffmpeg",
            output_path=Path("F:/Apps/Atnos/data/visual-qa/recordings/test.mp4"),
            duration_seconds=7,
        )

        self.assertEqual(command[:6], ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "15"])
        self.assertIn("desktop", command)
        self.assertIn("-t", command)
        self.assertIn("7", command)
        self.assertEqual(command[-1], "F:\\Apps\\Atnos\\data\\visual-qa\\recordings\\test.mp4")

    def test_missing_configured_ffmpeg_returns_clear_error(self) -> None:
        with TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            settings = Settings(
                app_root=root,
                data_dir=root / "data",
                visual_qa_dir=root / "data" / "visual-qa",
                visual_qa_log_path=root / "data" / "logs" / "visual_qa.jsonl",
                ffmpeg_exe="definitely-missing-ffmpeg-binary",
            )

            with self.assertRaisesRegex(ScreenCaptureError, "FFMPEG_EXE is not available"):
                ScreenCaptureService(settings)._resolve_ffmpeg()

    def test_review_prompt_preserves_product_direction(self) -> None:
        prompt = VisualQAService.build_review_prompt(
            goal="popraw czytelnosc statusu pracy agenta",
            area="Chat",
            extra_context="User przewija starsze wiadomosci.",
            screenshot_path=Path("F:/Apps/Atnos/data/visual-qa/captures/screen.png"),
            recording_path=None,
        )

        self.assertIn("Jestes krytycznym reviewerem produktu Atnos", prompt)
        self.assertIn("Chat i Ustawienia", prompt)
        self.assertIn("bez udawania postepu", prompt)
        self.assertIn("APPLY / REVIEW_FIRST / SKIP", prompt)

    def test_codex_handoff_requires_filtered_application(self) -> None:
        handoff = VisualQAService.build_codex_handoff_prompt(
            goal="popraw composer",
            area="Chat",
            screenshot_path=Path("F:/Apps/Atnos/data/visual-qa/captures/screen.png"),
            recording_path=None,
            review_text="Dodaj 3 konkretne poprawki.",
        )

        self.assertIn("zastosuj tylko te zmiany, ktore pasuja", handoff)
        self.assertIn("Nie dodawaj nowej nawigacji", handoff)
        self.assertIn("Zapisuj artefakty na F:", handoff)

    def test_external_input_artifact_is_copied_into_visual_qa_dir(self) -> None:
        with TemporaryDirectory() as root_dir, TemporaryDirectory() as external_dir:
            root = Path(root_dir)
            settings = Settings(
                app_root=root,
                data_dir=root / "data",
                visual_qa_dir=root / "data" / "visual-qa",
                visual_qa_log_path=root / "data" / "logs" / "visual_qa.jsonl",
            )
            settings.ensure_directories()
            source = Path(external_dir) / "screen.png"
            source.write_bytes(b"fake image bytes")
            service = object.__new__(VisualQAService)
            service.settings = settings

            copied = service._copy_input_artifact_if_needed(source, "captures")

            self.assertTrue(copied.exists())
            self.assertNotEqual(copied.resolve(), source.resolve())
            self.assertTrue(copied.resolve().is_relative_to(settings.visual_qa_dir.resolve()))
            self.assertEqual(copied.read_bytes(), b"fake image bytes")


if __name__ == "__main__":
    unittest.main()
