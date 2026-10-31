from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.db.models.enums import AttachmentKind
from atnos.services.attachment_understanding_service import AttachmentUnderstandingService


# Minimal valid 1x1 PNG
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class AttachmentVisionUnderstandingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AttachmentUnderstandingService()

    def test_png_is_vision_ready_not_unsupported(self) -> None:
        result = self.service.understand(
            filename="desktop.png",
            content=_PNG_BYTES,
            content_type="image/png",
        )
        self.assertEqual(result.attachment_kind, AttachmentKind.IMAGE)
        self.assertEqual(result.detected_kind, "image")
        self.assertEqual(result.status, "vision_ready")
        self.assertNotEqual(result.status, "unsupported")
        self.assertIn("vision", (result.status_reason or "").lower())

    def test_png_detected_by_extension_when_mime_missing(self) -> None:
        result = self.service.understand(
            filename="shot.PNG",
            content=_PNG_BYTES,
            content_type="application/octet-stream",
        )
        self.assertEqual(result.attachment_kind, AttachmentKind.IMAGE)
        self.assertEqual(result.status, "vision_ready")


if __name__ == "__main__":
    unittest.main()
