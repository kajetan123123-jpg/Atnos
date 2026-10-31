from __future__ import annotations

import ctypes
import base64
import json
import os
import re
import sys
from ctypes import POINTER, Structure, byref, cast, create_string_buffer, string_at
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from atnos.core.config import Settings


AES_256_GCM_STORAGE_BACKEND = "aes_256_gcm"
OS_KEYRING_STORAGE_BACKEND = "os_keyring"
METADATA_ONLY_STORAGE_BACKEND = "metadata_only"
METADATA_ONLY_SECRET_REF = "metadata_only:redacted"
AES_SECRET_REF_PATTERN = re.compile(r"^aes_256_gcm:([0-9a-f]{32})$")
OS_KEYRING_SECRET_REF_PATTERN = re.compile(r"^os_keyring:([0-9a-f]{32})$")
CRYPTPROTECT_UI_FORBIDDEN = 0x01
AES_KEY_SIZE_BYTES = 32
AES_NONCE_SIZE_BYTES = 12
AES_MASTER_KEY_FILE = "aes-256-gcm.master.key"


class SecretVaultUnavailableError(RuntimeError):
    pass


class SecretVaultResolutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SecretStorageStatus:
    storage_backend: str
    is_secret_storage_enabled: bool
    summary: str
    warnings: tuple[str, ...]


class _DataBlob(Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", POINTER(ctypes.c_byte)),
    ]


class SecretVaultService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_storage_status(self) -> SecretStorageStatus:
        backend = self.settings.resolved_account_secret_storage_backend
        if backend == AES_256_GCM_STORAGE_BACKEND:
            return SecretStorageStatus(
                storage_backend=backend,
                is_secret_storage_enabled=True,
                summary="Secrets are stored server-side in an AES-256-GCM encrypted local vault.",
                warnings=(),
            )
        if backend == OS_KEYRING_STORAGE_BACKEND:
            return SecretStorageStatus(
                storage_backend=backend,
                is_secret_storage_enabled=True,
                summary="Secrets are stored server-side in a Windows-protected local vault.",
                warnings=(),
            )

        return SecretStorageStatus(
            storage_backend=METADATA_ONLY_STORAGE_BACKEND,
            is_secret_storage_enabled=False,
            summary="Secure secret storage is unavailable on this runtime. Atnos can only register metadata.",
            warnings=(
                "Secrets are not persisted when storage falls back to metadata-only mode.",
            ),
        )

    def store_secret(self, secret_value: str) -> tuple[str, str | None]:
        normalized = self._normalize_required_secret(secret_value)
        backend = self.settings.resolved_account_secret_storage_backend
        if backend == AES_256_GCM_STORAGE_BACKEND:
            secret_ref = self._create_secret_ref(AES_256_GCM_STORAGE_BACKEND)
            self._write_aes_secret(secret_ref, normalized)
            return backend, secret_ref
        if backend == OS_KEYRING_STORAGE_BACKEND:
            secret_ref = self._create_secret_ref(OS_KEYRING_STORAGE_BACKEND)
            self._write_protected_secret(secret_ref, normalized)
            return backend, secret_ref
        return METADATA_ONLY_STORAGE_BACKEND, METADATA_ONLY_SECRET_REF

    def rotate_secret(
        self,
        *,
        secret_value: str,
        existing_secret_ref: str | None = None,
    ) -> tuple[str, str | None]:
        normalized = self._normalize_required_secret(secret_value)
        backend = self.settings.resolved_account_secret_storage_backend
        if backend == AES_256_GCM_STORAGE_BACKEND:
            secret_ref = (
                existing_secret_ref
                if existing_secret_ref and self._is_aes_secret_ref(existing_secret_ref)
                else self._create_secret_ref(AES_256_GCM_STORAGE_BACKEND)
            )
            self._write_aes_secret(secret_ref, normalized)
            return backend, secret_ref
        if backend == OS_KEYRING_STORAGE_BACKEND:
            secret_ref = (
                existing_secret_ref
                if existing_secret_ref and self._is_os_keyring_secret_ref(existing_secret_ref)
                else self._create_secret_ref()
            )
            self._write_protected_secret(secret_ref, normalized)
            return backend, secret_ref
        return METADATA_ONLY_STORAGE_BACKEND, METADATA_ONLY_SECRET_REF

    def resolve_secret(self, secret_ref: str | None) -> str:
        if not secret_ref:
            raise SecretVaultResolutionError("No secret is configured for this account.")
        if self._is_aes_secret_ref(secret_ref):
            return self._read_aes_secret(secret_ref)
        if not self._is_os_keyring_secret_ref(secret_ref):
            raise SecretVaultResolutionError("This account does not have retrievable secret storage.")
        return self._read_protected_secret(secret_ref)

    def delete_secret(self, secret_ref: str | None) -> None:
        if not secret_ref or not (self._is_aes_secret_ref(secret_ref) or self._is_os_keyring_secret_ref(secret_ref)):
            return
        target_path = self._resolve_secret_path(secret_ref)
        if target_path.exists():
            target_path.unlink()

    def _create_secret_ref(self, backend: str) -> str:
        return f"{backend}:{uuid4().hex}"

    def _write_aes_secret(self, secret_ref: str, secret_value: str) -> None:
        target_path = self._resolve_secret_path(secret_ref)
        key = self._load_or_create_aes_master_key()
        nonce = os.urandom(AES_NONCE_SIZE_BYTES)
        ciphertext = AESGCM(key).encrypt(nonce, secret_value.encode("utf-8"), secret_ref.encode("utf-8"))
        target_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "algorithm": "AES-256-GCM",
                    "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
                    "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _read_aes_secret(self, secret_ref: str) -> str:
        target_path = self._resolve_secret_path(secret_ref)
        if not target_path.exists():
            raise SecretVaultResolutionError("Secret reference is missing from the local vault.")
        try:
            payload = json.loads(target_path.read_text(encoding="utf-8"))
            nonce = base64.urlsafe_b64decode(str(payload["nonce"]).encode("ascii"))
            ciphertext = base64.urlsafe_b64decode(str(payload["ciphertext"]).encode("ascii"))
            plaintext = AESGCM(self._load_or_create_aes_master_key()).decrypt(
                nonce,
                ciphertext,
                secret_ref.encode("utf-8"),
            )
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise SecretVaultResolutionError("Stored AES-256-GCM secret could not be opened.") from exc

    def _load_or_create_aes_master_key(self) -> bytes:
        env_key = os.getenv("ATNOS_SECRET_VAULT_KEY")
        if env_key:
            key = _decode_aes_key(env_key)
            if key is None:
                raise SecretVaultUnavailableError("ATNOS_SECRET_VAULT_KEY must be a base64-encoded 32-byte key.")
            return key

        key_path = self.settings.account_secret_vault_dir / AES_MASTER_KEY_FILE
        if key_path.exists():
            key = _decode_aes_key(key_path.read_text(encoding="utf-8").strip())
            if key is None:
                raise SecretVaultUnavailableError("AES-256-GCM master key file is invalid.")
            return key

        key = AESGCM.generate_key(bit_length=256)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="utf-8")
        # chmod(0o600) can remove every usable ACL entry on Windows.
        # The launcher protects the vault directory there; POSIX runtimes still
        # receive owner-only permissions on the master key file.
        if os.name != "nt":
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
        return key

    def _write_protected_secret(self, secret_ref: str, secret_value: str) -> None:
        target_path = self._resolve_secret_path(secret_ref)
        try:
            protected_bytes = self._protect(secret_value.encode("utf-8"))
        except OSError as exc:
            raise SecretVaultUnavailableError(
                "Secure secret storage is unavailable on this runtime."
            ) from exc
        target_path.write_bytes(protected_bytes)

    def _read_protected_secret(self, secret_ref: str) -> str:
        target_path = self._resolve_secret_path(secret_ref)
        if not target_path.exists():
            raise SecretVaultResolutionError("Secret reference is missing from the local vault.")
        try:
            return self._unprotect(target_path.read_bytes()).decode("utf-8")
        except OSError as exc:
            raise SecretVaultResolutionError("Stored secret could not be opened from the local vault.") from exc

    def _resolve_secret_path(self, secret_ref: str) -> Path:
        match = AES_SECRET_REF_PATTERN.fullmatch(secret_ref) or OS_KEYRING_SECRET_REF_PATTERN.fullmatch(secret_ref)
        if match is None:
            raise SecretVaultResolutionError("Invalid secret reference.")
        secret_id = match.group(1)
        target_path = (self.settings.account_secret_vault_dir / f"{secret_id}.bin").resolve()
        vault_root = self.settings.account_secret_vault_dir.resolve()
        if target_path.parent != vault_root:
            raise SecretVaultResolutionError("Secret reference resolved outside the local vault.")
        return target_path

    @staticmethod
    def _is_aes_secret_ref(secret_ref: str) -> bool:
        return AES_SECRET_REF_PATTERN.fullmatch(secret_ref) is not None

    @staticmethod
    def _is_os_keyring_secret_ref(secret_ref: str) -> bool:
        return OS_KEYRING_SECRET_REF_PATTERN.fullmatch(secret_ref) is not None

    @staticmethod
    def _normalize_required_secret(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("secret_value cannot be empty.")
        return normalized

    @staticmethod
    def _bytes_to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = create_string_buffer(data)
        blob = _DataBlob(len(data), cast(buffer, POINTER(ctypes.c_byte)))
        return blob, buffer

    @staticmethod
    def _blob_to_bytes(blob: _DataBlob) -> bytes:
        return string_at(blob.pbData, blob.cbData)

    def _protect(self, plaintext: bytes) -> bytes:
        if sys.platform != "win32":
            raise OSError("Windows-protected storage is unavailable on this platform.")

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        input_blob, buffer = self._bytes_to_blob(plaintext)
        output_blob = _DataBlob()
        if not crypt32.CryptProtectData(
            byref(input_blob),
            "Atnos account secret",
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            byref(output_blob),
        ):
            raise ctypes.WinError()
        try:
            return self._blob_to_bytes(output_blob)
        finally:
            kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
            kernel32.LocalFree.restype = wintypes.HLOCAL
            kernel32.LocalFree(cast(output_blob.pbData, wintypes.HLOCAL))
            del buffer

    def _unprotect(self, protected_bytes: bytes) -> bytes:
        if sys.platform != "win32":
            raise OSError("Windows-protected storage is unavailable on this platform.")

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        input_blob, buffer = self._bytes_to_blob(protected_bytes)
        output_blob = _DataBlob()
        description = wintypes.LPWSTR()
        if not crypt32.CryptUnprotectData(
            byref(input_blob),
            byref(description),
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            byref(output_blob),
        ):
            raise ctypes.WinError()
        try:
            return self._blob_to_bytes(output_blob)
        finally:
            kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
            kernel32.LocalFree.restype = wintypes.HLOCAL
            kernel32.LocalFree(cast(output_blob.pbData, wintypes.HLOCAL))
            if description:
                kernel32.LocalFree(cast(description, wintypes.HLOCAL))
            del buffer


def _decode_aes_key(value: str) -> bytes | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        key = base64.urlsafe_b64decode(normalized.encode("ascii"))
    except Exception:
        try:
            key = base64.b64decode(normalized.encode("ascii"))
        except Exception:
            return None
    return key if len(key) == AES_KEY_SIZE_BYTES else None
