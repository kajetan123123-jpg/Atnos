from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.project import Project, ProjectMemory
from atnos.repositories.project_app_link_repository import ProjectAppLinkRepository
from atnos.repositories.project_memory_repository import ProjectMemoryRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.db.models.self_improvement_note import SelfImprovementNote
from atnos.repositories.self_improvement_note_repository import SelfImprovementNoteRepository
from atnos.services.integration_config_service import (
    IntegrationConfigService,
    OBSIDIAN_KEY,
    OBSIDIAN_VAULT_NAME,
)
from atnos.services.self_improvement_notes_service import SelfImprovementNotesService
from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS

logger = logging.getLogger(__name__)

EXPORT_ROOT = Path("Atnos") / "Improvement Notes"
PROJECT_MEMORY_EXPORT_ROOT = Path("Atnos") / "Project Memory"
CAPABILITY_ROADMAP_PATH = "docs/product/atnos-capability-roadmap-v0.md"


@dataclass(frozen=True, slots=True)
class ObsidianExportPreview:
    filename: str
    relative_path: str
    markdown: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObsidianExportConfirmResult:
    status: str
    relative_path: str
    bytes_written: int


@dataclass(frozen=True, slots=True)
class ProjectMemoryExportSnapshot:
    project: Project
    project_memories: tuple[ProjectMemory, ...]


class ObsidianExportError(Exception):
    pass


class ObsidianExportConfigurationError(ObsidianExportError):
    pass


class ObsidianExportNotFoundError(ObsidianExportError):
    pass


class ObsidianExportPathError(ObsidianExportError):
    pass


class ObsidianExportService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.notes = SelfImprovementNoteRepository(session)
        self.note_service = SelfImprovementNotesService(session)
        self.projects = ProjectRepository(session)
        self.project_memories = ProjectMemoryRepository(session)
        self.project_app_links = ProjectAppLinkRepository(session)
        self.integration_config = IntegrationConfigService(session, settings)

    def build_preview(
        self,
        *,
        note_id: int | None = None,
        project_id: int | None = None,
        title: str | None = None,
        filename: str | None = None,
    ) -> ObsidianExportPreview:
        if note_id is not None and project_id is not None:
            raise ObsidianExportPathError("Provide either note_id or project_id, not both.")

        if project_id is not None:
            snapshot = self._resolve_project_memory_snapshot(project_id)
            safe_filename = self._build_project_memory_filename(
                project=snapshot.project,
                title=title,
                filename=filename,
            )
            relative_path = self._project_memory_relative_path(safe_filename)
            markdown = self._build_project_memory_markdown(snapshot=snapshot, title=title)
        else:
            note = self._resolve_note(note_id)
            safe_filename = self._build_filename(note=note, title=title, filename=filename)
            relative_path = self._relative_path(safe_filename)
            markdown = self._build_markdown(note=note, title=title)
        return ObsidianExportPreview(
            filename=safe_filename,
            relative_path=relative_path.as_posix(),
            markdown=markdown,
            warnings=self._configuration_warnings(),
        )

    def confirm_export(
        self,
        *,
        note_id: int | None = None,
        project_id: int | None = None,
        title: str | None = None,
        filename: str | None = None,
    ) -> ObsidianExportConfirmResult:
        config = self.integration_config.get_obsidian_config()
        if not config.export_enabled:
            raise ObsidianExportConfigurationError("Obsidian export is disabled.")
        if config.vault_path is None or not config.path_exists:
            raise ObsidianExportConfigurationError("OBSIDIAN_VAULT_PATH is not configured.")

        preview = self.build_preview(
            note_id=note_id,
            project_id=project_id,
            title=title,
            filename=filename,
        )
        vault_path = config.vault_path.resolve()
        target_path = self._safe_target_path(vault_path, Path(preview.relative_path))
        target_path = self._avoid_collision(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        encoded = preview.markdown.encode("utf-8")
        target_path.write_bytes(encoded)
        relative_path = target_path.relative_to(vault_path).as_posix()
        export_target_label = f"project_id={project_id}" if project_id is not None else f"note_id={self._resolve_note(note_id).id}"

        logger.info(
            "Obsidian export confirmed | target=%s | relative_path=%s | bytes_written=%s",
            export_target_label,
            relative_path,
            len(encoded),
        )
        return ObsidianExportConfirmResult(
            status="exported",
            relative_path=relative_path,
            bytes_written=len(encoded),
        )

    def _resolve_note(self, note_id: int | None) -> SelfImprovementNote:
        if note_id is not None:
            note = self.notes.get(note_id)
            if note is None:
                raise ObsidianExportNotFoundError(f"Unknown self_improvement_note_id '{note_id}'.")
            return note

        note = self.note_service.get_selected_note()
        if note is None:
            raise ObsidianExportNotFoundError("No selected improvement note is available for export.")
        return note

    def _resolve_project_memory_snapshot(self, project_id: int) -> ProjectMemoryExportSnapshot:
        project = self.projects.get(project_id)
        if project is None:
            raise ObsidianExportNotFoundError(f"Unknown project_id '{project_id}'.")

        linked_obsidian = next(
            (
                item
                for item in self.project_app_links.list_for_project(project_id)
                if item.integration_key == OBSIDIAN_KEY and item.status != "disabled"
            ),
            None,
        )
        if linked_obsidian is None:
            raise ObsidianExportConfigurationError("Project is not linked to Obsidian.")

        project_memories = tuple(self.project_memories.list_active_for_project(project_id, limit=6))
        return ProjectMemoryExportSnapshot(
            project=project,
            project_memories=project_memories,
        )

    def _configuration_warnings(self) -> tuple[str, ...]:
        config = self.integration_config.get_obsidian_config()
        return tuple(
            f"{warning}; confirm export will be unavailable."
            if warning == "OBSIDIAN_VAULT_PATH is not configured."
            else warning
            for warning in config.warnings
        )

    def _build_filename(
        self,
        *,
        note: SelfImprovementNote,
        title: str | None,
        filename: str | None,
    ) -> str:
        if filename is not None:
            return self._sanitize_filename(filename)

        base_title = title or f"Improvement Note {note.id}"
        return self._sanitize_filename(f"{base_title}.md")

    def _build_project_memory_filename(
        self,
        *,
        project: Project,
        title: str | None,
        filename: str | None,
    ) -> str:
        if filename is not None:
            return self._sanitize_filename(filename)

        base_title = title or f"{project.name} Memory"
        return self._sanitize_filename(f"{base_title}.md")

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        candidate = filename.strip()
        if not candidate:
            raise ObsidianExportPathError("Filename cannot be empty.")
        if "/" in candidate or "\\" in candidate or ".." in candidate:
            raise ObsidianExportPathError("Filename cannot contain path separators or traversal.")

        if not candidate.lower().endswith(".md"):
            candidate = f"{candidate}.md"

        stem = candidate[:-3]
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", stem)
        stem = re.sub(r"\s+", "-", stem.strip())
        stem = re.sub(r"-{2,}", "-", stem).strip(".- ")
        if not stem:
            raise ObsidianExportPathError("Filename must contain at least one safe character.")
        return f"{stem.lower()}.md"

    @staticmethod
    def _relative_path(filename: str) -> Path:
        return EXPORT_ROOT / filename

    @staticmethod
    def _project_memory_relative_path(filename: str) -> Path:
        return PROJECT_MEMORY_EXPORT_ROOT / filename

    @staticmethod
    def _safe_target_path(vault_path: Path, relative_path: Path) -> Path:
        if relative_path.is_absolute():
            raise ObsidianExportPathError("Export path must be relative.")
        target_path = (vault_path / relative_path).resolve()
        try:
            target_path.relative_to(vault_path)
        except ValueError as exc:
            raise ObsidianExportPathError("Export path escapes the Obsidian vault.") from exc
        return target_path

    @staticmethod
    def _avoid_collision(target_path: Path) -> Path:
        if not target_path.exists():
            return target_path

        timestamp = int(time.time())
        candidate = target_path.with_name(f"{target_path.stem}-{timestamp}{target_path.suffix}")
        index = 1
        while candidate.exists():
            candidate = target_path.with_name(f"{target_path.stem}-{timestamp}-{index}{target_path.suffix}")
            index += 1
        return candidate

    def _build_markdown(self, *, note: SelfImprovementNote, title: str | None) -> str:
        exported_at = datetime.now(timezone.utc).isoformat()
        heading = title.strip() if title and title.strip() else f"Improvement Note #{note.id}"
        proposed_fix = note.proposed_fix or "Brak"
        chosen_next_patch = note.chosen_next_patch or "Brak"
        created_at = note.created_at.isoformat()

        return "\n".join(
            [
                "---",
                "type: atnos-improvement-note",
                "source: atnos",
                f"note_id: {note.id}",
                f"view: {note.view}",
                f"severity: {note.severity.value}",
                f"decision: {note.decision.value}",
                f"status: {note.status.value}",
                f"created_at: {created_at}",
                f"exported_at: {exported_at}",
                "---",
                "",
                f"# {heading}",
                "",
                "## Friction",
                "",
                note.friction,
                "",
                "## Proposed fix",
                "",
                proposed_fix,
                "",
                "## Chosen next patch",
                "",
                chosen_next_patch,
                "",
                "## Atnos review context",
                "",
                "This note was exported from Atnos as a confirmed user action.",
                "No code was executed.",
                "No connector write was performed beyond this local markdown export.",
                "Obsidian is visible memory, not the runtime database.",
                f"Capability roadmap: {CAPABILITY_ROADMAP_PATH}",
                "",
            ]
        )

    def _build_project_memory_markdown(
        self,
        *,
        snapshot: ProjectMemoryExportSnapshot,
        title: str | None,
    ) -> str:
        exported_at = datetime.now(timezone.utc).isoformat()
        heading = title.strip() if title and title.strip() else f"{snapshot.project.name} Memory"
        project_summary = self._sanitize_export_text(
            snapshot.project.summary or snapshot.project.description or "No project memory summary is available yet.",
            limit=1_800,
        )
        memory_entries = [
            self._sanitize_export_text(memory.content, limit=420)
            for memory in snapshot.project_memories
            if memory.content.strip()
        ]

        lines = [
            "---",
            "type: atnos-project-memory",
            "source: atnos",
            f"project_id: {snapshot.project.id}",
            f"project_slug: {snapshot.project.slug}",
            f"vault_name: {OBSIDIAN_VAULT_NAME}",
            f"exported_at: {exported_at}",
            "---",
            "",
            f"# {heading}",
            "",
            "## Current project memory",
            "",
            project_summary,
            "",
        ]

        if memory_entries:
            lines.extend(
                [
                    "## Recent memory entries",
                    "",
                    *[f"- {entry}" for entry in memory_entries[:5]],
                    "",
                ]
            )

        lines.extend(
            [
                "## Atnos visible memory contract",
                "",
                "This markdown was exported from Atnos after an explicit user action.",
                "No connector sync was performed.",
                "No external API call was performed.",
                "Obsidian remains visible project memory, not the runtime database.",
                f"Capability roadmap: {CAPABILITY_ROADMAP_PATH}",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _sanitize_export_text(value: str, *, limit: int) -> str:
        normalized = " ".join(value.split())
        redacted = normalized
        redacted = re.sub(
            r"(?i)\b(bearer)\s+\S+",
            "[REDACTED]",
            redacted,
        )
        redacted = re.sub(
            r"(?i)\bsk-[A-Za-z0-9_\-]{6,}\b",
            "[REDACTED]",
            redacted,
        )
        redacted = re.sub(
            r"(?i)\b(password|token|api_key|secret_ref|client_secret|private_key|account_secret|secret_value|credential)\b\s*[:=]?\s*\S*",
            "[REDACTED]",
            redacted,
        )
        for token in FORBIDDEN_KEY_TOKENS:
            redacted = re.sub(
                rf"(?i)\b{re.escape(token)}\b",
                "[REDACTED]",
                redacted,
            )
        if len(redacted) <= limit:
            return redacted
        return redacted[: limit - 3].rstrip() + "..."
