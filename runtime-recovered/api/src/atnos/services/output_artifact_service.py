from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from atnos.core.config import Settings


class ManagedArtifactPathError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManagedArtifactDescriptor:
    filename: str
    storage_path: str
    absolute_path: Path
    media_type: str
    file_extension: str
    size_bytes: int


class OutputArtifactService:
    ALLOWED_EXTENSIONS = {
        ".docx",
        ".pptx",
        ".xlsx",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".wav",
        ".srt",
        ".mp4",
        ".json",
        ".md",
        ".txt",
    }
    MAX_OUTPUT_SIZE_BYTES = 512 * 1024 * 1024

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.outputs_dir = (self.settings.data_dir / "atnos-outputs").resolve()
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def create_output_path(self, *, conversation_id: int | None, extension: str) -> Path:
        normalized_extension = extension.lower().strip()
        if normalized_extension not in self.ALLOWED_EXTENSIONS:
            raise ManagedArtifactPathError("Unsupported output artifact extension.")
        bucket = str(conversation_id or "global")
        target_dir = (self.outputs_dir / bucket).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / f"{uuid4().hex}{normalized_extension}"

    def finalize_output(
        self,
        *,
        absolute_path: Path,
        filename: str,
        media_type: str,
    ) -> ManagedArtifactDescriptor:
        resolved = absolute_path.resolve()
        self._ensure_managed_path(resolved)
        if not resolved.exists() or not resolved.is_file():
            raise ManagedArtifactPathError("Generated artifact file was not created.")
        size_bytes = resolved.stat().st_size
        if size_bytes > self.MAX_OUTPUT_SIZE_BYTES:
            try:
                resolved.unlink(missing_ok=True)
            except OSError:
                pass
            raise ManagedArtifactPathError(
                f"Generated artifact exceeds the {self.MAX_OUTPUT_SIZE_BYTES} byte limit."
            )
        try:
            storage_path = resolved.relative_to(self.settings.app_root.resolve()).as_posix()
        except ValueError:
            storage_path = resolved.as_posix()
        return ManagedArtifactDescriptor(
            filename=filename,
            storage_path=storage_path,
            absolute_path=resolved,
            media_type=media_type,
            file_extension=resolved.suffix.lower(),
            size_bytes=size_bytes,
        )

    def resolve_download_path(self, storage_path: str) -> Path:
        normalized = storage_path.strip()
        if not normalized or ".." in normalized.replace("\\", "/").split("/"):
            raise ManagedArtifactPathError("storage_path is invalid.")
        requested = Path(normalized)
        candidate = (
            requested.resolve()
            if requested.is_absolute()
            else (self.settings.app_root / requested).resolve()
        )
        self._ensure_managed_path(candidate)
        if not candidate.exists() or not candidate.is_file():
            raise ManagedArtifactPathError("Artifact file was not found.")
        return candidate

    def _ensure_managed_path(self, candidate: Path) -> None:
        allowed_roots = (
            self.outputs_dir.resolve(),
            self.settings.uploads_dir.resolve(),
            self.settings.browser_downloads_dir.resolve(),
            self.settings.workspace_staging_dir.resolve(),
        )
        for root in allowed_roots:
            try:
                candidate.relative_to(root)
                return
            except ValueError:
                continue
        raise ManagedArtifactPathError("storage_path points outside managed local artifact roots.")
