from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.tools.chat_hard_stop import (
    classify_delete,
    classify_shell_command,
    looks_like_confirmation,
)


class ChatHardStopTest(unittest.TestCase):
    def test_delete_inside_safe_roots_is_free(self) -> None:
        needs, reason = classify_delete(r"E:\Atnos\Temp\demo.txt")
        self.assertFalse(needs)
        self.assertEqual(reason, "delete_inside_safe_root")
        needs, reason = classify_delete(r"E:\Rolki\Wealth-Blueprint\a.mp4")
        self.assertFalse(needs)

    def test_delete_outside_safe_roots_needs_confirmation(self) -> None:
        needs, reason = classify_delete(r"C:\Users\Public\secret.txt")
        self.assertTrue(needs)
        self.assertIn("outside", reason)

    def test_shell_shutdown_and_email_need_confirmation(self) -> None:
        needs, _ = classify_shell_command("Stop-Computer -Force")
        self.assertTrue(needs)
        needs, reason = classify_shell_command("Send-MailMessage -To a@b.c -Subject x")
        self.assertTrue(needs)
        self.assertEqual(reason, "email_send")

    def test_safe_shell_is_allowed(self) -> None:
        needs, reason = classify_shell_command("Get-ChildItem E:\\Atnos")
        self.assertFalse(needs)
        self.assertEqual(reason, "allowed")

    def test_confirmation_phrases(self) -> None:
        self.assertTrue(looks_like_confirmation("potwierdzam"))
        self.assertTrue(looks_like_confirmation("Tak"))
        self.assertFalse(looks_like_confirmation("może później"))


if __name__ == "__main__":
    unittest.main()
