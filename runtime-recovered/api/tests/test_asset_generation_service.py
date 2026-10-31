from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PIL import Image

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.asset_generation_service import AssetGenerationService
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationError


class AssetGenerationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_generate_story_assets_uses_comfyui_background_when_available(self) -> None:
        service = AssetGenerationService(self.settings)
        captured_calls: list[dict[str, object]] = []

        def _fake_generate_background(*, prompt: str, output_path: Path, width: int, height: int, seed: int, **kwargs):
            captured_calls.append({"prompt": prompt, **kwargs})
            Image.new("RGB", (width, height), color=(180, 32, 64)).save(output_path)
            return None

        with patch.object(service.comfyui, "can_generate", return_value=True):
            with patch.object(
                service.comfyui,
                "generate_background",
                side_effect=_fake_generate_background,
            ) as mocked_generate:
                assets = service.generate_story_assets(
                    title="Macro Fruit Crush",
                    topic="Satisfying fruit destruction",
                    scenes=[{"headline": "Soczyste pekniecie", "caption": "Makro jablka i sok", "fruit": "apple"}],
                    conversation_id=55,
                )

        self.assertEqual(len(assets.frames), 1)
        self.assertTrue(assets.frames[0].absolute_path.exists())
        self.assertTrue(assets.thumbnail.absolute_path.exists())
        self.assertEqual(mocked_generate.call_count, 2)
        self.assertEqual(captured_calls[0]["steps_override"], 22)
        self.assertEqual(captured_calls[1]["steps_override"], 24)
        self.assertIn("single hero subject", str(captured_calls[0]["prompt"]))

    def test_generate_story_assets_falls_back_when_comfyui_generation_fails(self) -> None:
        service = AssetGenerationService(self.settings)

        with patch.object(service.comfyui, "can_generate", return_value=True):
            with patch.object(
                service.comfyui,
                "generate_background",
                side_effect=ComfyUiImageGenerationError("boom"),
            ):
                assets = service.generate_story_assets(
                    title="Frozen Fruit Crack",
                    topic="Cold macro textures",
                    scenes=[{"headline": "Lodowy trzask", "caption": "Mrozony arbuz peka", "fruit": "watermelon"}],
                    conversation_id=56,
                )

        self.assertEqual(len(assets.frames), 1)
        self.assertTrue(assets.frames[0].absolute_path.exists())
        self.assertTrue(assets.thumbnail.absolute_path.exists())

    def test_generate_story_assets_premium_profile_uses_stronger_comfyui_overrides(self) -> None:
        service = AssetGenerationService(self.settings)
        captured_calls: list[dict[str, object]] = []

        def _fake_generate_background(*, prompt: str, output_path: Path, width: int, height: int, seed: int, **kwargs):
            captured_calls.append({"prompt": prompt, **kwargs})
            Image.new("RGB", (width, height), color=(64, 32, 180)).save(output_path)
            return None

        with patch.object(service.comfyui, "can_generate", return_value=True):
            with patch.object(service.comfyui, "generate_background", side_effect=_fake_generate_background):
                service.generate_story_assets(
                    title="Premium Fruit Crush",
                    topic="High fidelity macro motion",
                    scenes=[{"headline": "Soczysty premium kadr", "caption": "Makro detalu", "fruit": "apple"}],
                    conversation_id=57,
                    quality_profile="premium",
                )

        self.assertEqual(captured_calls[0]["steps_override"], 28)
        self.assertEqual(captured_calls[1]["steps_override"], 30)
        self.assertGreater(float(captured_calls[0]["cfg_scale_override"]), 6.4)


if __name__ == "__main__":
    unittest.main()
