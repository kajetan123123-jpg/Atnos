from __future__ import annotations

import base64
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.services.gmail_connector_service import GmailConnectorService  # noqa: E402


def test_gmail_mime_preserves_polish_subject_and_body() -> None:
    raw = GmailConnectorService._build_mime(
        to="account-22af0cc8@example.invalid",
        subject="Atnos - załączniki i próbki",
        body="Cześć operator,\nZałączniki są gotowe. Możesz sprawdzić jakość głosu.",
    )

    padded = raw + "=" * (-len(raw) % 4)
    message = message_from_bytes(base64.urlsafe_b64decode(padded), policy=default)

    assert isinstance(message, EmailMessage)
    assert message["Subject"] == "Atnos - załączniki i próbki"
    assert message.get_content_charset() == "utf-8"
    assert message.get_content() == "Cześć operator,\nZałączniki są gotowe. Możesz sprawdzić jakość głosu.\n"
