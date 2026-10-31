from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from content.script_generator import ShortScript
from content.visual_storyboard import VisualStoryboardService


class VisualStoryboardServiceTest(unittest.TestCase):
    def test_generate_creates_multiple_vertical_frames_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = ShortScript(
                title="Lokalne AI bez chaosu",
                hook="Nie zaczynaj od dziesieciu narzedzi.",
                body=(
                    "Najpierw wybierz jeden workflow.",
                    "Potem sprawdz runtime i artefakty.",
                    "Na koncu dopiero mysl o uploadzie.",
                ),
                cta="Zapisz checklist przed kolejnym renderem.",
                voiceover="test",
            )

            storyboard = VisualStoryboardService().generate(
                script=script,
                topic="lokalne AI i automatyzacja",
                output_dir=root,
            )

            self.assertGreaterEqual(len(storyboard.frames), 4)
            self.assertTrue(storyboard.manifest_path.exists())
            manifest = json.loads(storyboard.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["engine"], "pillow-editorial-storyboard")
            self.assertGreaterEqual(len(manifest["scenes"]), 4)
            for frame in storyboard.frames:
                self.assertTrue(frame.exists())
                with Image.open(frame) as image:
                    self.assertEqual(image.size, (1080, 1920))


if __name__ == "__main__":
    unittest.main()
