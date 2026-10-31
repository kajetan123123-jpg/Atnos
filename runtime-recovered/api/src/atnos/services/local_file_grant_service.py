from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from typing import TYPE_CHECKING

from atnos.db.models.enums import ExecutionTraceKind, ExecutionTraceStatus
from atnos.repositories.execution_trace_repository import ExecutionTraceRepository

if TYPE_CHECKING:
    from atnos.db.models.local_file_grant import LocalFileGrant


BLOCKED_EXTENSIONS = {".env", ".pem", ".key", ".crt", ".p12", ".pfx"}
BLOCKED_PATTERNS = (
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".aws/credentials",
    "wallet.dat",
    ".ssh/",
    ".gnupg/",
)
BLOCKED_DIRS = (
    "appdata/local/google",
    "appdata/roaming/mozilla",
    "library/application support",
    ".config/git/credentials",
)
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class LocalFileGrantError(ValueError):
    status_code = 400


class LocalFileGrantForbiddenError(LocalFileGrantError):
    status_code = 403


class LocalFileGrantRevokedError(LocalFileGrantError):
    status_code = 410


@dataclass(frozen=True, slots=True)
class LocalFilePreview:
    absolute_path: str
    relative_path: str
    size_bytes: int
    content: str


@dataclass(frozen=True, slots=True)
class LocalFileTraceContext:
    project_id: int | None = None
    conversation_id: int | None = None
    message_id: int | None = None


@dataclass(frozen=True, slots=True)
class LocalFileGrantDescriptor:
    grant_root: str
    mode: str
    status: str
    max_file_size_mb: int
    max_file_size_bytes: int | None = None


class LocalFileGrantService:
    def __init__(
        self,
        *,
        grant_root: Path | str | None = None,
        allow_write: bool | None = None,
        max_file_size_bytes: int | None = None,
        grant: LocalFileGrant | None = None,
        trace_context: LocalFileTraceContext | None = None,
        trace_repository: ExecutionTraceRepository | None = None,
    ) -> None:
        descriptor = self._build_descriptor(
            grant_root=grant_root,
            allow_write=allow_write,
            max_file_size_bytes=max_file_size_bytes,
            grant=grant,
        )
        self.root = Path(descriptor.grant_root).expanduser().resolve()
        self.mode = descriptor.mode
        self.status = descriptor.status
        self.max_file_size_bytes = (
            max(1, int(descriptor.max_file_size_bytes))
            if descriptor.max_file_size_bytes is not None
            else max(1, int(descriptor.max_file_size_mb)) * 1024 * 1024
        )
        self.trace_context = trace_context
        self.execution_traces = trace_repository

    @classmethod
    def from_grant_record(
        cls,
        grant: LocalFileGrant,
        *,
        trace_context: LocalFileTraceContext | None = None,
        trace_repository: ExecutionTraceRepository | None = None,
    ) -> LocalFileGrantService:
        return cls(
            grant=grant,
            trace_context=trace_context,
            trace_repository=trace_repository,
        )

    def read_text(self, path: str | Path, *, encoding: str | None = None) -> LocalFilePreview:
        self._ensure_active()
        resolved = self.resolve_path(path)
        if not resolved.exists() or not resolved.is_file():
            raise LocalFileGrantError(f"File '{resolved}' was not found.")
        size_bytes = resolved.stat().st_size
        if size_bytes > self.max_file_size_bytes:
            raise LocalFileGrantError("File exceeds the maximum allowed size for this grant.")
        try:
            content = resolved.read_text(encoding=encoding or "utf-8")
        except UnicodeDecodeError:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        return LocalFilePreview(
            absolute_path=str(resolved),
            relative_path=self._relative_path(resolved),
            size_bytes=size_bytes,
            content=content,
        )

    def write_files_atomically(self, files: dict[str, str]) -> list[str]:
        self._ensure_write_allowed()
        if not files:
            return []
        resolved_files: dict[Path, str] = {}
        for relative_path, content in files.items():
            resolved_files[self.resolve_path(relative_path)] = content

        temp_dir = Path(tempfile.mkdtemp(prefix="atnos-codegen-", dir=str(self.root)))
        backups: list[tuple[Path, Path]] = []
        created_paths: list[Path] = []
        try:
            for target_path, content in resolved_files.items():
                temp_target = temp_dir / target_path.relative_to(self.root)
                temp_target.parent.mkdir(parents=True, exist_ok=True)
                temp_target.write_text(content, encoding="utf-8")
                self._enforce_file_size(temp_target)

            for target_path in resolved_files:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if target_path.exists():
                    backup_path = temp_dir / "__backup__" / target_path.relative_to(self.root)
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_path, backup_path)
                    backups.append((target_path, backup_path))
                else:
                    created_paths.append(target_path)
                shutil.copy2(temp_dir / target_path.relative_to(self.root), target_path)
            saved = [str(path) for path in resolved_files]
            self._log_write_operation("file_write", saved)
            return saved
        except Exception as exc:
            for target_path, backup_path in reversed(backups):
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, target_path)
            for target_path in reversed(created_paths):
                try:
                    if target_path.exists():
                        target_path.unlink()
                except OSError:
                    pass
            self._log_failed_operation("file_write", exc)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def delete_paths(self, paths: list[str | Path]) -> list[str]:
        self._ensure_write_allowed()
        deleted: list[str] = []
        try:
            for raw_path in paths:
                resolved = self.resolve_path(raw_path)
                if resolved.exists():
                    if resolved.is_dir():
                        shutil.rmtree(resolved)
                    else:
                        resolved.unlink()
                    deleted.append(str(resolved))
            self._log_write_operation("file_delete", deleted)
            return deleted
        except Exception as exc:
            self._log_failed_operation("file_delete", exc)
            raise

    def resolve_path(self, path: str | Path) -> Path:
        self._ensure_active()
        raw = str(path)
        if "\x00" in raw:
            raise LocalFileGrantError("Null bytes are not allowed in file paths.")
        normalized = Path(raw)
        if any(part == ".." for part in normalized.parts):
            raise LocalFileGrantError("Path traversal is not allowed.")
        if self._is_blocked_secret_path(raw):
            raise LocalFileGrantError("This path is blocked by local secret policy.")
        for part in normalized.parts:
            stem = Path(part).stem.lower()
            if stem in WINDOWS_RESERVED_NAMES:
                raise LocalFileGrantError(f"Reserved Windows path segment '{part}' is not allowed.")
        candidate = (normalized if normalized.is_absolute() else self.root / normalized).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise LocalFileGrantError("The requested path escapes the granted root.") from exc
        if candidate.name.lower() == ".env" or candidate.suffix.lower() in BLOCKED_EXTENSIONS:
            raise LocalFileGrantError("This file type is blocked by local secret policy.")
        return candidate

    def _ensure_active(self) -> None:
        if self.status != "active":
            raise LocalFileGrantRevokedError("This local file grant has been revoked.")

    def _ensure_write_allowed(self) -> None:
        self._ensure_active()
        if self.mode != "read_write":
            raise LocalFileGrantForbiddenError("This local file grant is read-only.")

    def _enforce_file_size(self, candidate: Path) -> None:
        size_bytes = candidate.stat().st_size
        if size_bytes > self.max_file_size_bytes:
            raise LocalFileGrantError("File exceeds the maximum allowed size for this grant.")

    def _relative_path(self, path: Path) -> str:
        return str(path.relative_to(self.root)).replace("\\", "/")

    def _log_write_operation(self, operation: str, paths: list[str]) -> None:
        if self.execution_traces is None or self.trace_context is None:
            return
        if (
            self.trace_context.project_id is None
            and self.trace_context.conversation_id is None
            and self.trace_context.message_id is None
        ):
            return
        self.execution_traces.create(
            project_id=self.trace_context.project_id,
            conversation_id=self.trace_context.conversation_id,
            message_id=self.trace_context.message_id,
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.COMPLETED,
            step_name=operation,
            input_summary=", ".join(paths[:3]) if paths else None,
            output_summary=f"{len(paths)} path(s) affected",
            metadata_json={
                "kind": operation,
                "paths": paths,
                "grant_root": str(self.root),
                "grant_mode": self.mode,
            },
        )

    def _log_failed_operation(self, operation: str, exc: Exception) -> None:
        if self.execution_traces is None or self.trace_context is None:
            return
        if (
            self.trace_context.project_id is None
            and self.trace_context.conversation_id is None
            and self.trace_context.message_id is None
        ):
            return
        self.execution_traces.create(
            project_id=self.trace_context.project_id,
            conversation_id=self.trace_context.conversation_id,
            message_id=self.trace_context.message_id,
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.FAILED,
            step_name=operation,
            error_message=str(exc),
            metadata_json={
                "kind": operation,
                "grant_root": str(self.root),
                "grant_mode": self.mode,
            },
        )

    @staticmethod
    def _build_descriptor(
        *,
        grant_root: Path | str | None,
        allow_write: bool | None,
        max_file_size_bytes: int | None,
        grant: LocalFileGrant | None,
    ) -> LocalFileGrantDescriptor:
        if grant is not None:
            return LocalFileGrantDescriptor(
                grant_root=grant.grant_root,
                mode=str(grant.mode or "read_only").strip().lower() or "read_only",
                status=str(grant.status or "active").strip().lower() or "active",
                max_file_size_mb=max(1, int(grant.max_file_size_mb or 50)),
                max_file_size_bytes=None,
            )
        if grant_root is None:
            raise LocalFileGrantError("grant_root is required when no stored grant record is provided.")
        resolved_allow_write = bool(allow_write)
        resolved_size_bytes = max_file_size_bytes or 50 * 1024 * 1024
        size_mb = max(1, int((resolved_size_bytes + (1024 * 1024 - 1)) // (1024 * 1024)))
        return LocalFileGrantDescriptor(
            grant_root=str(grant_root),
            mode="read_write" if resolved_allow_write else "read_only",
            status="active",
            max_file_size_mb=size_mb,
            max_file_size_bytes=max(1, int(resolved_size_bytes)),
        )

    @staticmethod
    def _is_blocked_secret_path(raw: str) -> bool:
        lowered = raw.replace("\\", "/").lower()
        if any(pattern in lowered for pattern in BLOCKED_PATTERNS):
            return True
        return any(blocked_dir in lowered for blocked_dir in BLOCKED_DIRS)
