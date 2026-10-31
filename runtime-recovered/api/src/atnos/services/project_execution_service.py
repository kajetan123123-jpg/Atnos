from __future__ import annotations

"""Ograniczone wykonanie zmian kodu z dowodem, testem i rollbackiem.

Serwis nie przyjmuje komendy powloki ani dowolnego katalogu. Operuje wyłącznie
na zatwierdzonym drafcie ``code_apply_preview`` i tylko w katalogu Atnosa
dozwolonym przez konfiguracje. Test jest uruchamiany wyłącznie przez
``ProjectVerificationService`` z jawna recepta.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import ExecutionTraceKind, ExecutionTraceStatus
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.db.models.rollback_point import RollbackPoint
from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.local_file_grant_service import LocalFileGrantService
from atnos.services.project_verification_service import (
    ProjectVerificationResult,
    ProjectVerificationService,
    VerificationRecipe,
)


class ProjectExecutionError(ValueError):
    """Kontrolowana zmiana nie spelnila warunkow wykonania."""


class VerificationRunner(Protocol):
    def run(self, recipe: VerificationRecipe) -> ProjectVerificationResult: ...


@dataclass(frozen=True, slots=True)
class ProjectChangeExecutionResult:
    """Wynik zmiany kodu, zawsze z jawnym stanem weryfikacji."""

    status: str
    verification_status: str
    written_files: tuple[str, ...]
    rollback_point_id: int
    rollback_status: str
    verification: dict[str, object] | None
    project_root: str
    next_step: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "verification_status": self.verification_status,
            "written_files": list(self.written_files),
            "rollback_point_id": self.rollback_point_id,
            "rollback_status": self.rollback_status,
            "verification": self.verification,
            "project_root": self.project_root,
            "next_step": self.next_step,
        }


class ProjectExecutionService:
    """Stosuje zatwierdzony diff lokalnie i przywraca go po bledzie testu."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        verifier: VerificationRunner | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.verifier = verifier or ProjectVerificationService(settings)
        self.broker = AccountActionBrokerService(session, settings)

    def execute_approved_code_apply(self, *, draft_id: int) -> ProjectChangeExecutionResult:
        draft = self.broker.get_action_draft(draft_id)
        if draft is None:
            raise ProjectExecutionError(f"Code apply draft {draft_id} was not found.")
        if draft.action_type != "code_apply_preview":
            raise ProjectExecutionError("Selected draft is not a code apply preview.")
        if draft.status != "approved":
            raise ProjectExecutionError("Code apply draft must be approved before execution.")

        payload = draft.preview_payload
        root = self._resolve_allowed_root(str(payload.get("project_root") or payload.get("target_dir") or ""))
        files = self._parse_files(payload)
        grant = LocalFileGrantService(grant_root=root, allow_write=True)
        targets = {relative: grant.resolve_path(relative) for relative in files}
        rollback_point = self._create_rollback_point(draft_id=draft_id, project_id=draft.project_id, root=root, targets=targets)
        trace = self._create_trace(draft_id=draft_id, project_id=draft.project_id, root=root, rollback_point=rollback_point)

        try:
            written = tuple(grant.write_files_atomically(files))
        except Exception as exc:
            rollback_point.status = "failed"
            rollback_point.failure_detail = f"Write failed before verification: {type(exc).__name__}: {exc}"
            self._finish_trace(trace, status=ExecutionTraceStatus.FAILED, summary="Safe code apply failed before verification.", error=rollback_point.failure_detail)
            self.session.flush()
            raise ProjectExecutionError(rollback_point.failure_detail) from exc

        recipe = self._verification_recipe(payload)
        if recipe is None:
            rollback_point.status = "applied_unverified"
            self._finish_trace(
                trace,
                status=ExecutionTraceStatus.COMPLETED,
                summary="Code change applied, but no controlled verification recipe was attached.",
                error=None,
            )
            self.broker.mark_executed(
                draft_id,
                actor="atnos",
                detail={
                    "written_files": list(written),
                    "project_root": str(root),
                    "rollback_point_id": rollback_point.id,
                    "verification_status": "not_requested",
                },
            )
            self.session.flush()
            return ProjectChangeExecutionResult(
                status="applied_unverified",
                verification_status="not_requested",
                written_files=written,
                rollback_point_id=rollback_point.id,
                rollback_status=rollback_point.status,
                verification=None,
                project_root=str(root),
                next_step="Uruchom dozwolona recepte weryfikacji przed oznaczeniem zadania jako ukonczone.",
            )

        outcome = self.verifier.run(recipe)
        verification = outcome.to_dict()
        rollback_point.verification_json = verification
        if outcome.status == "verified":
            rollback_point.status = "verified"
            self._finish_trace(
                trace,
                status=ExecutionTraceStatus.COMPLETED,
                summary=f"Code change verified by {recipe}.",
                error=None,
                metadata={"verification": verification},
            )
            self.broker.mark_executed(
                draft_id,
                actor="atnos",
                detail={
                    "written_files": list(written),
                    "project_root": str(root),
                    "rollback_point_id": rollback_point.id,
                    "verification_status": "verified",
                    "verification_recipe": recipe,
                },
            )
            self.session.flush()
            return ProjectChangeExecutionResult(
                status="verified",
                verification_status="verified",
                written_files=written,
                rollback_point_id=rollback_point.id,
                rollback_status=rollback_point.status,
                verification=verification,
                project_root=str(root),
                next_step="Zmiana ma dowod testu. Zaktualizuj Project Brain i przygotuj raport.",
            )

        self._restore(rollback_point, root)
        rollback_point.status = "rolled_back"
        rollback_point.failure_detail = outcome.summary
        self._finish_trace(
            trace,
            status=ExecutionTraceStatus.FAILED,
            summary=f"Verification {recipe} failed; original files were restored.",
            error=outcome.summary,
            metadata={"verification": verification, "rollback": "completed"},
        )
        self.broker.record_execution_failure(
            draft_id,
            actor="atnos",
            detail={
                "rollback_point_id": rollback_point.id,
                "verification_status": outcome.status,
                "verification_recipe": recipe,
                "restored_files": [str(path) for path in targets.values()],
            },
        )
        self.session.flush()
        return ProjectChangeExecutionResult(
            status="rolled_back",
            verification_status=outcome.status,
            written_files=written,
            rollback_point_id=rollback_point.id,
            rollback_status=rollback_point.status,
            verification=verification,
            project_root=str(root),
            next_step="Przejrzyj wynik weryfikacji; zmiana zostala wycofana i nie moze byc oznaczona jako ukonczona.",
        )

    def _resolve_allowed_root(self, raw_root: str) -> Path:
        if not raw_root.strip():
            raise ProjectExecutionError("Code apply draft is missing project_root.")
        candidate = Path(raw_root).expanduser().resolve()
        if not candidate.is_dir():
            raise ProjectExecutionError("project_root must point to an existing directory.")
        for allowed_root in self._allowed_roots():
            try:
                candidate.relative_to(allowed_root)
                return candidate
            except ValueError:
                continue
        allowed = ", ".join(str(item) for item in self._allowed_roots())
        raise ProjectExecutionError(f"project_root is outside the allowed Atnos project roots: {allowed}.")

    def _allowed_roots(self) -> tuple[Path, ...]:
        app_root = self.settings.app_root.resolve()
        roots = [app_root]
        repository_root = app_root.parent.parent if app_root.name.lower() == "api" else app_root
        if (repository_root / "frontend").is_dir() and (repository_root / "runtime-recovered").is_dir():
            roots.append(repository_root.resolve())
        return tuple(dict.fromkeys(roots))

    @staticmethod
    def _parse_files(payload: dict[str, object]) -> dict[str, str]:
        files_payload = payload.get("files")
        if not isinstance(files_payload, list) or not files_payload:
            raise ProjectExecutionError("Code apply draft has no file payload.")
        files: dict[str, str] = {}
        for entry in files_payload:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip().replace("\\", "/")
            content = entry.get("proposed_content")
            if not path or not isinstance(content, str):
                continue
            if path in files:
                raise ProjectExecutionError(f"Code apply draft contains duplicate path '{path}'.")
            files[path] = content
        if not files:
            raise ProjectExecutionError("Code apply draft has no valid file changes.")
        return files

    @staticmethod
    def _verification_recipe(payload: dict[str, object]) -> VerificationRecipe | None:
        raw = str(payload.get("verification_recipe") or "").strip()
        if not raw:
            return None
        allowed: set[str] = {
            "backend_project_command_tests",
            "frontend_project_command_tests",
            "frontend_build",
        }
        if raw not in allowed:
            raise ProjectExecutionError("Code apply draft requested an unsupported verification recipe.")
        return raw  # type: ignore[return-value]

    def _create_rollback_point(
        self,
        *,
        draft_id: int,
        project_id: int | None,
        root: Path,
        targets: dict[str, Path],
    ) -> RollbackPoint:
        snapshot_root = self.settings.data_dir / "execution" / "rollback-points" / uuid4().hex
        originals_dir = snapshot_root / "originals"
        originals_dir.mkdir(parents=True, exist_ok=False)
        changes: list[dict[str, object]] = []
        for relative_path, target in targets.items():
            existed = target.exists()
            original_path: str | None = None
            original_hash: str | None = None
            if existed:
                original = originals_dir / relative_path
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, original)
                original_path = str(original.relative_to(snapshot_root)).replace("\\", "/")
                original_hash = _sha256_file(target)
            changes.append(
                {
                    "path": relative_path,
                    "existed": existed,
                    "original_path": original_path,
                    "original_hash": original_hash,
                }
            )
        manifest_path = snapshot_root / "manifest.json"
        manifest = {
            "version": 1,
            "created_at": _utc_now().isoformat(),
            "target_root": str(root),
            "changes": changes,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
        rollback_point = RollbackPoint(
            project_id=project_id,
            action_draft_id=draft_id,
            execution_trace_id=None,
            label=f"Code apply draft #{draft_id}",
            status="created",
            target_root=str(root),
            manifest_path=str(manifest_path),
            changes_json=changes,
            verification_json={},
            failure_detail=None,
        )
        self.session.add(rollback_point)
        self.session.flush()
        return rollback_point

    def _create_trace(
        self,
        *,
        draft_id: int,
        project_id: int | None,
        root: Path,
        rollback_point: RollbackPoint,
    ) -> ExecutionTrace | None:
        if project_id is None:
            return None
        trace = ExecutionTrace(
            project_id=project_id,
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.RUNNING,
            step_name="safe_project_code_apply",
            provider_key="controlled_project_execution",
            input_summary=f"Approved code apply draft #{draft_id}",
            output_summary="Applying approved local code change.",
            metadata_json={
                "draft_id": draft_id,
                "target_root": str(root),
                "rollback_point_id": rollback_point.id,
            },
        )
        self.session.add(trace)
        self.session.flush()
        rollback_point.execution_trace_id = trace.id
        return trace

    def _restore(self, rollback_point: RollbackPoint, root: Path) -> None:
        snapshot_root = Path(rollback_point.manifest_path).parent.resolve()
        for item in rollback_point.changes_json:
            relative_path = str(item.get("path") or "")
            if not relative_path:
                continue
            target = (root / relative_path).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ProjectExecutionError("Rollback manifest contains a path outside project_root.") from exc
            if bool(item.get("existed")):
                backup_relative = str(item.get("original_path") or "")
                backup = (snapshot_root / backup_relative).resolve()
                try:
                    backup.relative_to(snapshot_root)
                except ValueError as exc:
                    raise ProjectExecutionError("Rollback manifest contains an invalid backup path.") from exc
                if not backup.is_file():
                    raise ProjectExecutionError(f"Rollback backup is missing for '{relative_path}'.")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
            elif target.exists():
                target.unlink()

    @staticmethod
    def _finish_trace(
        trace: ExecutionTrace | None,
        *,
        status: ExecutionTraceStatus,
        summary: str,
        error: str | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if trace is None:
            return
        trace.status = status
        trace.output_summary = summary
        trace.error_message = error
        if metadata:
            trace.metadata_json = {**(trace.metadata_json or {}), **metadata}


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)
