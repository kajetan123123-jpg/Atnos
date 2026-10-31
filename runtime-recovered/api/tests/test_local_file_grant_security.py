from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from atnos.services.local_file_grant_service import (
    LocalFileGrantError,
    LocalFileGrantForbiddenError,
    LocalFileGrantRevokedError,
    LocalFileGrantService,
)


class LocalFileGrantSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory(prefix="atnos-local-grant-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def test_rejects_traversal_secret_and_reserved_paths(self) -> None:
        outside_path = (self.root.parent / "outside-secret.txt").resolve()
        invalid_paths = [
            "../secret.txt",
            "..\\secret.txt",
            "nested/../escape.txt",
            str(outside_path),
            "bad\x00name.txt",
            "CON.txt",
            "LPT1.log",
            ".env",
            "certs/server.pem",
            ".ssh/id_rsa",
            ".gnupg/pubring.kbx",
            ".aws/credentials",
            "wallet.dat",
            "AppData/Local/Google/Chrome/User Data/Default/Cookies",
            "Library/Application Support/Keychain/login.keychain-db",
            ".config/git/credentials",
        ]
        service = LocalFileGrantService(grant_root=self.root, allow_write=True)

        for candidate in invalid_paths:
            with self.subTest(candidate=candidate):
                with self.assertRaises(LocalFileGrantError):
                    service.resolve_path(candidate)

    def test_rejects_symlink_escape_when_platform_allows_symlinks(self) -> None:
        outside_dir = self.root.parent / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)
        outside_file = outside_dir / "real.txt"
        outside_file.write_text("escape", encoding="utf-8")
        link_path = self.root / "link.txt"
        try:
            link_path.symlink_to(outside_file)
        except (NotImplementedError, OSError):
            self.skipTest("Symlink creation is not available on this Windows runtime.")

        service = LocalFileGrantService(grant_root=self.root, allow_write=True)
        with self.assertRaises(LocalFileGrantError):
            service.resolve_path("link.txt")

    def test_enforces_max_file_size_on_reads_and_writes(self) -> None:
        service = LocalFileGrantService(
            grant_root=self.root,
            allow_write=True,
            max_file_size_bytes=1024,
        )
        oversized = self.root / "big.txt"
        oversized.write_text("A" * 2048, encoding="utf-8")

        with self.assertRaises(LocalFileGrantError):
            service.read_text("big.txt")
        with self.assertRaises(LocalFileGrantError):
            service.write_files_atomically({"next.txt": "B" * 2048})

    def test_read_only_grant_cannot_write_or_delete(self) -> None:
        readonly = LocalFileGrantService(grant_root=self.root, allow_write=False)

        with self.assertRaises(LocalFileGrantForbiddenError) as write_error:
            readonly.write_files_atomically({"note.txt": "blocked"})
        with self.assertRaises(LocalFileGrantForbiddenError) as delete_error:
            readonly.delete_paths(["note.txt"])

        self.assertEqual(write_error.exception.status_code, 403)
        self.assertEqual(delete_error.exception.status_code, 403)

    def test_revoked_grant_returns_410(self) -> None:
        revoked = LocalFileGrantService.from_grant_record(
            SimpleNamespace(
                grant_root=str(self.root),
                mode="read_write",
                status="revoked",
                max_file_size_mb=50,
            )
        )

        with self.assertRaises(LocalFileGrantRevokedError) as error:
            revoked.resolve_path("note.txt")

        self.assertEqual(error.exception.status_code, 410)


if __name__ == "__main__":
    unittest.main()
