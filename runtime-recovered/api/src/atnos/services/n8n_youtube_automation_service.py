from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.services.n8n_runtime_status_service import N8nRuntimeStatusService
from youtube.youtube_analytics import get_video_analytics
from youtube.youtube_models import YouTubeUploadMetadata, YouTubeUploadPreview
from youtube.upload_guard import validate_upload_video_file
from youtube.youtube_upload import prepare_upload, upload_private_short


class N8nYouTubeAutomationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class N8nShortCandidate:
    local_file: Path
    run_dir: Path
    title: str
    status: str
    ready_for_upload: bool
    quality_status: str
    quality_issues: tuple[str, ...] = ()
    quality_score: int = 0
    qa_path: Path | None = None
    package_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_file": str(self.local_file),
            "run_dir": str(self.run_dir),
            "title": self.title,
            "status": self.status,
            "ready_for_upload": self.ready_for_upload,
            "quality_status": self.quality_status,
            "quality_issues": list(self.quality_issues),
            "quality_score": self.quality_score,
            "qa_path": str(self.qa_path) if self.qa_path else None,
            "package_path": str(self.package_path) if self.package_path else None,
        }


class N8nYouTubeAutomationService:
    """File-backed approval queue used by n8n and Atnos for YouTube channel ops."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.n8n_data_dir / "youtube"
        self.pending_dir = self.root / "approvals" / "pending"
        self.approved_dir = self.root / "approvals" / "approved"
        self.results_dir = self.root / "results"
        self.log_path = settings.data_dir / "logs" / "n8n_youtube_automation.jsonl"
        for path in (self.pending_dir, self.approved_dir, self.results_dir, self.log_path.parent):
            path.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        runtime = N8nRuntimeStatusService(self.settings).snapshot(probe_docker=self.settings.app_env != "test")
        return {
            "enabled": self.settings.n8n_enabled,
            "base_url": self.settings.n8n_base_url,
            "mode": self.settings.n8n_youtube_automation_mode,
            "data_dir": str(self.settings.n8n_data_dir),
            "allow_public_upload": self.settings.allow_public_upload,
            "require_upload_approval": self.settings.require_upload_approval,
            "pending_approvals": len(list(self.pending_dir.glob("*.json"))),
            "approved_approvals": len(list(self.approved_dir.glob("*.json"))),
            "docker": runtime.to_dict(),
            "n8n_docker_status": runtime.docker_status,
            "fallback_runner_status": runtime.fallback_runner_status,
            "fallback_runner_available": runtime.fallback_runner_available,
            "workflow_templates_status": runtime.workflow_templates_status,
            "workflow_templates_count": runtime.workflow_templates_count,
        }

    def scan_ready_shorts(self, *, limit: int = 20) -> list[N8nShortCandidate]:
        shorts_dir = self.settings.shorts_output_dir
        if not shorts_dir.exists():
            return []
        candidates: list[N8nShortCandidate] = []
        for final in sorted(shorts_dir.glob("*/final.mp4"), key=lambda item: item.stat().st_mtime, reverse=True):
            run_dir = final.parent
            state = self._read_json(run_dir / "youtube_automation_state.json")
            if state.get("status") == "uploaded":
                continue
            qa_path = run_dir / "render_qa.json"
            package_path = run_dir / "youtube_upload_package.json"
            qa = self._read_json(qa_path)
            package = self._read_json(package_path)
            quality = self._candidate_quality(final=final, qa=qa)
            title = self._candidate_title(run_dir=run_dir, package=package, qa=qa)
            candidates.append(
                N8nShortCandidate(
                    local_file=final.resolve(),
                    run_dir=run_dir.resolve(),
                    title=title[:100],
                    status=str(state.get("status") or "ready_for_approval"),
                    ready_for_upload=quality["ready"],
                    quality_status=str(quality["status"]),
                    quality_issues=tuple(str(issue) for issue in quality["issues"]),
                    quality_score=int(quality["score"]),
                    qa_path=qa_path.resolve() if qa_path.exists() else None,
                    package_path=package_path.resolve() if package_path.exists() else None,
                )
            )
            if len(candidates) >= limit:
                break
        self._log("scan_ready_shorts", {"count": len(candidates)})
        return candidates

    def create_upload_approval(
        self,
        *,
        video_path: Path | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        preview = prepare_upload(
            settings=self.settings,
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
            privacy="private",
        )
        quality = self._validate_preview_ready(preview)
        approval_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]
        payload = {
            "approval_id": approval_id,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "approved_at": None,
            "source": "n8n_youtube_automation",
            "risk_level": "high",
            "required_action": "human_approval_before_private_upload",
            "safety": {
                "privacy": "private",
                "public_upload_blocked": not self.settings.allow_public_upload,
                "requires_upload_approval": True,
                "no_publication_without_human": True,
            },
            "quality_gate": quality,
            "preview": preview.to_dict(),
        }
        target = self.pending_dir / f"{approval_id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_run_state(preview.local_file.parent, {"status": "pending_approval", "approval_id": approval_id})
        self._log("create_upload_approval", {"approval_id": approval_id, "local_file": str(preview.local_file)})
        return payload

    def run_daily_ops(self, *, create_approval: bool = False, limit: int = 20) -> dict[str, Any]:
        candidates = self.scan_ready_shorts(limit=limit)
        ready = [
            item
            for item in candidates
            if item.ready_for_upload and item.status not in {"pending_approval", "approved_for_private_upload", "uploaded"}
        ]
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "mode": self.settings.n8n_youtube_automation_mode,
            "create_approval_requested": create_approval,
            "total_candidates": len(candidates),
            "ready_candidates": len(ready),
            "blocked_candidates": [item.to_dict() for item in candidates if not item.ready_for_upload],
            "next_candidate": ready[0].to_dict() if ready else None,
            "approval": None,
            "recommendation": "review_ready_candidate" if ready else "render_or_repair_more_shorts",
        }
        if create_approval and ready:
            payload["approval"] = self.create_upload_approval(video_path=ready[0].local_file)
            payload["recommendation"] = "approval_created_review_before_private_upload"
        self._log(
            "daily_ops",
            {
                "total_candidates": len(candidates),
                "ready_candidates": len(ready),
                "approval_created": bool(payload["approval"]),
            },
        )
        return payload

    def approve_upload(self, approval_id: str) -> dict[str, Any]:
        pending = self.pending_dir / f"{approval_id}.json"
        if not pending.exists():
            raise N8nYouTubeAutomationError(f"Approval not found in pending queue: {approval_id}")
        payload = self._read_json(pending)
        payload["status"] = "approved"
        payload["approved_at"] = datetime.now(UTC).isoformat()
        approved = self.approved_dir / pending.name
        approved.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.unlink()
        preview = payload.get("preview", {})
        local_file = Path(str(preview.get("local_file", "")))
        if local_file.exists():
            self._write_run_state(local_file.parent, {"status": "approved_for_private_upload", "approval_id": approval_id})
        self._log("approve_upload", {"approval_id": approval_id})
        return payload

    def upload_approved(self, approval_id: str, *, youtube_service=None) -> dict[str, Any]:
        approved = self.approved_dir / f"{approval_id}.json"
        if not approved.exists():
            raise N8nYouTubeAutomationError(f"Approval must be approved before upload: {approval_id}")
        payload = self._read_json(approved)
        if payload.get("status") != "approved":
            raise N8nYouTubeAutomationError(f"Approval is not approved: {approval_id}")
        preview = self._preview_from_payload(payload["preview"])
        result = upload_private_short(
            settings=self.settings,
            preview=preview,
            approved=True,
            youtube_service=youtube_service,
        )
        result_payload = {
            "approval_id": approval_id,
            "status": "uploaded_private",
            "uploaded_at": datetime.now(UTC).isoformat(),
            "result": result.to_dict(),
        }
        (self.results_dir / f"{approval_id}.json").write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload["status"] = "uploaded_private"
        payload["upload_result"] = result.to_dict()
        approved.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_run_state(preview.local_file.parent, {"status": "uploaded", "approval_id": approval_id, "video_id": result.video_id})
        self._log("upload_approved", {"approval_id": approval_id, "video_id": result.video_id})
        return result_payload

    def sync_analytics(
        self,
        *,
        video_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        analytics_service=None,
    ) -> dict[str, Any]:
        today = date.today()
        resolved_end = end_date or today.isoformat()
        resolved_start = start_date or (today - timedelta(days=28)).isoformat()
        result = get_video_analytics(
            video_id,
            resolved_start,
            resolved_end,
            settings=self.settings,
            analytics_service=analytics_service,
        )
        payload = result.to_dict()
        (self.results_dir / f"analytics-{video_id}-{resolved_start}-{resolved_end}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._log("sync_analytics", {"video_id": video_id, "start_date": resolved_start, "end_date": resolved_end})
        return payload

    def _ensure_enabled(self) -> None:
        if not self.settings.n8n_enabled or self.settings.n8n_youtube_automation_mode == "disabled":
            raise N8nYouTubeAutomationError("n8n YouTube automation is disabled.")

    def _preview_from_payload(self, payload: dict[str, Any]) -> YouTubeUploadPreview:
        metadata = YouTubeUploadMetadata(**payload["metadata"])
        if metadata.privacy != "private":
            raise N8nYouTubeAutomationError("n8n upload automation only supports private uploads.")
        return YouTubeUploadPreview(
            local_file=Path(payload["local_file"]),
            thumbnail_path=Path(payload["thumbnail_path"]) if payload.get("thumbnail_path") else None,
            captions_path=Path(payload["captions_path"]) if payload.get("captions_path") else None,
            requires_approval=True,
            metadata=metadata,
        )

    def _validate_preview_ready(self, preview: YouTubeUploadPreview) -> dict[str, Any]:
        qa = self._read_json(preview.local_file.parent / "render_qa.json")
        quality = self._candidate_quality(final=preview.local_file, qa=qa)
        if not quality["ready"]:
            raise N8nYouTubeAutomationError(
                "Upload approval blocked by quality gate: " + "; ".join(str(issue) for issue in quality["issues"])
            )
        return quality

    def _candidate_quality(self, *, final: Path, qa: dict[str, Any]) -> dict[str, Any]:
        issues: list[str] = []
        score = 100
        validation = validate_upload_video_file(final, settings=self.settings, require_run_artifacts=True)
        if not validation.valid:
            issues.extend(validation.issues)
            score -= 40

        width = _number(qa.get("width") or _nested(qa, "youtube_short_checks", "width"))
        height = _number(qa.get("height") or _nested(qa, "youtube_short_checks", "height"))
        duration = _number(
            qa.get("duration_sec")
            or qa.get("duration")
            or _nested(qa, "youtube_short_checks", "duration_sec")
        )
        has_audio_raw = qa.get("has_audio")
        if has_audio_raw is None:
            has_audio_raw = _nested(qa, "youtube_short_checks", "has_audio")
        has_audio = bool(has_audio_raw) if has_audio_raw is not None else None

        explicit_ready = qa.get("youtube_shorts_ready")
        if explicit_ready is False:
            issues.append("render_qa marks youtube_shorts_ready=false")
            score -= 35
        if width is not None and height is not None:
            if height < width:
                issues.append(f"video is not vertical or square: {int(width)}x{int(height)}")
                score -= 30
            if int(width) != 1080 or int(height) != 1920:
                score -= 8
        elif qa:
            issues.append("render_qa has no width/height")
            score -= 15
        if has_audio is False:
            issues.append("video has no audio track")
            score -= 35
        elif has_audio is None and qa:
            issues.append("render_qa has no audio status")
            score -= 10
        if duration is not None:
            if duration <= 0 or duration > 180:
                issues.append(f"duration is outside YouTube Shorts technical bounds: {duration:.1f}s")
                score -= 30
            elif duration < 8:
                issues.append(f"duration is too short for useful review: {duration:.1f}s")
                score -= 20
            elif duration > 60:
                issues.append(f"duration is longer than preferred shorts range: {duration:.1f}s")
                score -= 8
        elif qa:
            issues.append("render_qa has no duration")
            score -= 15
        if not qa:
            issues.append("render_qa.json is missing")
            score -= 30

        score = max(0, min(100, score))
        return {
            "ready": not issues and score >= 80,
            "status": "ready" if not issues and score >= 80 else "needs_repair",
            "score": score,
            "issues": issues,
            "width": int(width) if width is not None else None,
            "height": int(height) if height is not None else None,
            "duration_sec": duration,
            "has_audio": has_audio,
        }

    def _candidate_title(self, *, run_dir: Path, package: dict[str, Any], qa: dict[str, Any]) -> str:
        packaged_title = package.get("title")
        if isinstance(packaged_title, str) and packaged_title.strip():
            return packaged_title.strip()[:100]
        qa_title = qa.get("title")
        if isinstance(qa_title, str) and qa_title.strip():
            return qa_title.strip()[:100]
        report_path = run_dir / "production_report.md"
        if report_path.exists():
            try:
                report = report_path.read_text(encoding="utf-8")
            except OSError:
                report = ""
            for line in report.splitlines():
                normalized = line.strip()
                lower = normalized.lower()
                if lower.startswith("- title:") or lower.startswith("- topic:"):
                    title = normalized.split(":", maxsplit=1)[1].strip(" `")
                    if title:
                        return title[:100]
        return run_dir.name

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_run_state(self, run_dir: Path, update: dict[str, Any]) -> None:
        state_path = run_dir / "youtube_automation_state.json"
        state = self._read_json(state_path)
        state.update(update)
        state["updated_at"] = datetime.now(UTC).isoformat()
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, event: str, payload: dict[str, Any]) -> None:
        record = {"timestamp": datetime.now(UTC).isoformat(), "event": event, **payload}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
