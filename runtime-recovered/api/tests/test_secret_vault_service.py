from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atnos.core.config import Settings
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore
from atnos.services.secret_vault_service import (
    AES_256_GCM_STORAGE_BACKEND,
    AES_MASTER_KEY_FILE,
    SecretVaultResolutionError,
    SecretVaultService,
)


class SecretVaultServiceTest(unittest.TestCase):
    def test_windows_key_creation_does_not_apply_posix_chmod(self) -> None:
        with TemporaryDirectory(prefix="atnos-secret-vault-windows-") as tmpdir:
            settings = _settings(Path(tmpdir))
            vault = SecretVaultService(settings)

            with patch("atnos.services.secret_vault_service.os.name", "nt"), patch.object(
                Path,
                "chmod",
            ) as chmod:
                vault.store_secret("windows-acl-safe")

            chmod.assert_not_called()

    def test_auto_secret_vault_uses_aes_256_gcm_and_does_not_store_plaintext(self) -> None:
        with TemporaryDirectory(prefix="atnos-secret-vault-") as tmpdir:
            settings = _settings(Path(tmpdir))
            vault = SecretVaultService(settings)

            backend, secret_ref = vault.store_secret("access-token-secret")

            self.assertEqual(backend, AES_256_GCM_STORAGE_BACKEND)
            self.assertIsNotNone(secret_ref)
            self.assertTrue(str(secret_ref).startswith("aes_256_gcm:"))
            secret_file = next(
                path
                for path in settings.account_secret_vault_dir.glob("*.bin")
                if path.name != AES_MASTER_KEY_FILE
            )
            stored = secret_file.read_text(encoding="utf-8")
            self.assertNotIn("access-token-secret", stored)
            self.assertIn("AES-256-GCM", stored)
            self.assertEqual(vault.resolve_secret(secret_ref), "access-token-secret")

    def test_oauth_token_bundle_roundtrip_uses_encrypted_vault(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-vault-") as tmpdir:
            settings = _settings(Path(tmpdir))
            store = OAuthTokenStore(settings)

            token_ref = store.store_token_bundle(
                OAuthTokenBundle(
                    access_token="ya29.access-token",
                    refresh_token="refresh-token",
                    token_type="Bearer",
                    expires_in=3600,
                    scope="https://www.googleapis.com/auth/youtube.upload",
                )
            )
            raw = b"".join(path.read_bytes() for path in settings.account_secret_vault_dir.glob("*.bin"))

            self.assertTrue(str(token_ref).startswith("aes_256_gcm:"))
            self.assertNotIn(b"ya29.access-token", raw)
            self.assertNotIn(b"refresh-token", raw)
            loaded = store.load_token_bundle(token_ref)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.access_token, "ya29.access-token")
            self.assertEqual(loaded.refresh_token, "refresh-token")
            self.assertEqual(loaded.expires_in, 3600)

    def test_oauth_token_bundle_rotation_reuses_encrypted_ref_and_removes_plaintext(self) -> None:
        with TemporaryDirectory(prefix="atnos-oauth-vault-rotate-") as tmpdir:
            settings = _settings(Path(tmpdir))
            store = OAuthTokenStore(settings)
            token_ref = store.store_token_bundle(
                OAuthTokenBundle(
                    access_token="old-access-token",
                    refresh_token="old-refresh-token",
                    token_type="Bearer",
                    expires_in=3600,
                    scope="https://www.googleapis.com/auth/youtube.upload",
                )
            )
            secret_files_before = sorted(settings.account_secret_vault_dir.glob("*.bin"))

            rotated_ref = store.rotate_token_bundle(
                existing_token_ref=token_ref,
                bundle=OAuthTokenBundle(
                    access_token="new-access-token",
                    refresh_token="old-refresh-token",
                    token_type="Bearer",
                    expires_in=7200,
                    scope="https://www.googleapis.com/auth/youtube.upload",
                ),
            )

            self.assertEqual(rotated_ref, token_ref)
            secret_files_after = sorted(settings.account_secret_vault_dir.glob("*.bin"))
            self.assertEqual(secret_files_after, secret_files_before)
            raw = b"".join(path.read_bytes() for path in secret_files_after)
            self.assertNotIn(b"old-access-token", raw)
            self.assertNotIn(b"new-access-token", raw)
            loaded = store.load_token_bundle(rotated_ref)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.access_token, "new-access-token")
            self.assertEqual(loaded.expires_in, 7200)

    def test_delete_secret_removes_encrypted_payload(self) -> None:
        with TemporaryDirectory(prefix="atnos-secret-delete-") as tmpdir:
            settings = _settings(Path(tmpdir))
            vault = SecretVaultService(settings)
            _backend, secret_ref = vault.store_secret("delete-me")

            vault.delete_secret(secret_ref)

            with self.assertRaises(SecretVaultResolutionError):
                vault.resolve_secret(secret_ref)


def _settings(root: Path) -> Settings:
    return Settings(
        app_root=root,
        data_dir=root / "data",
        account_secret_storage_backend="auto",
        _env_file=root / ".env",
    )


if __name__ == "__main__":
    unittest.main()
