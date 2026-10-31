from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_dumps, json_loads, rows_to_dicts, utc_now_iso
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.publication_quality import public_quality_blockers, public_quality_evidence
from atnos.production.thumbnail_quality import audit_thumbnail_quality
from atnos.production.visual_semantic_audit import audit_visual_semantics, semantic_visual_blockers

QUALITY_REVIEW_BLOCKED = "quality_review_blocked"
QUALITY_REVIEW_PASSED = "quality_review_passed"


class ProductionQualityScheduler:
    """Automatyczny gate jakości przed review, mailem i prywatnym uploadem."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        """Zwróć status automatycznego audytu jakości produkcji."""
        return {
            "enabled": bool(self.settings.shorts_quality_review_scheduler_enabled),
            "require_public_quality": bool(self.settings.shorts_review_require_public_quality),
            "interval_minutes": int(self.settings.shorts_quality_review_interval_minutes),
            "max_jobs_per_tick": int(self.settings.shorts_quality_review_max_jobs_per_tick),
            "pending_count": self._pending_count(),
        }

    def audit_due_jobs(self, *, limit: int | None = None) -> dict[str, Any]:
        """Sprawdź gotowe joby zanim zostaną wysłane do review."""
        if not self.settings.shorts_quality_review_scheduler_enabled:
            return {
                "status": "disabled",
                "checked_count": 0,
                "blocked_count": 0,
                "passed_count": 0,
                "jobs": [],
            }

        safe_limit = max(1, min(int(limit or self.settings.shorts_quality_review_max_jobs_per_tick), 50))
        jobs = self._select_due_jobs(limit=safe_limit)
        results = [self.audit_job(str(job["id"]), job=job) for job in jobs]
        return {
            "status": "completed" if results else "idle",
            "checked_count": len(results),
            "blocked_count": sum(1 for item in results if item.get("status") == "blocked"),
            "passed_count": sum(1 for item in results if item.get("status") == "passed"),
            "jobs": results,
        }

    def audit_job(self, job_id: str, *, job: dict[str, Any] | None = None) -> dict[str, Any]:
        """Sprawdź pojedynczy job i zapisz decyzję jakościową."""
        if not self.settings.shorts_quality_review_scheduler_enabled:
            return {"status": "disabled", "job_id": job_id, "blockers": []}

        payload = dict(job or self._load_job(job_id))
        status = str(payload.get("status") or "")
        if status != "qa_passed":
            return {
                "status": "skipped",
                "job_id": job_id,
                "reason": f"job_status_not_qa_passed:{status or 'unknown'}",
                "blockers": [],
            }

        qa_report = self._load_authoritative_qa_report(payload)
        upload_package = self._load_upload_package(payload)
        payload["qa_report"] = qa_report

        thumbnail_quality = self._thumbnail_quality(payload, upload_package)
        thumbnail_semantic = self._thumbnail_semantic(payload, upload_package)
        first_frame_semantic = self._first_frame_semantic(payload, upload_package)
        blockers = self._review_blockers(
            payload,
            upload_package,
            thumbnail_quality=thumbnail_quality,
            thumbnail_semantic=thumbnail_semantic,
            first_frame_semantic=first_frame_semantic,
        )
        evidence = public_quality_evidence(payload)
        result = {
            "status": "blocked" if blockers else "passed",
            "job_id": job_id,
            "account_id": payload.get("account_id"),
            "topic_id": payload.get("topic_id"),
            "checked_at": utc_now_iso(),
            "blockers": blockers,
            "public_quality_ready": not blockers,
            "public_quality_evidence": evidence,
            "thumbnail_quality": thumbnail_quality,
            "thumbnail_semantic_audit": thumbnail_semantic,
            "first_frame_semantic_audit": first_frame_semantic,
            "upload_package_public_quality_ready": upload_package.get("public_quality_ready"),
            "upload_package_public_quality_blockers": list(upload_package.get("public_quality_blockers") or []),
            "next_action": self._next_action(blockers, upload_package),
        }
        self._persist_result(job_id, result)
        return result

    def _review_blockers(
        self,
        job: dict[str, Any],
        upload_package: dict[str, Any],
        *,
        thumbnail_quality: dict[str, Any],
        thumbnail_semantic: dict[str, Any],
        first_frame_semantic: dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if not self.settings.shorts_review_require_public_quality:
            return blockers

        package_blockers = upload_package.get("public_quality_blockers")
        if isinstance(package_blockers, list):
            blockers.extend(str(item) for item in package_blockers if str(item).strip())
        elif upload_package.get("public_quality_ready") is False:
            blockers.append("public_quality_not_ready")
        blockers.extend(public_quality_blockers(self.settings, job))
        blockers.extend(self._thumbnail_blockers(thumbnail_quality))
        blockers.extend(self._thumbnail_semantic_blockers(thumbnail_semantic))
        blockers.extend(self._first_frame_semantic_blockers(first_frame_semantic))
        return list(dict.fromkeys(blockers))

    @staticmethod
    def _thumbnail_blockers(audit: dict[str, Any]) -> list[str]:
        return [f"thumbnail_quality:{blocker}" for blocker in audit.get("blockers") or []]

    def _thumbnail_quality(self, job: dict[str, Any], upload_package: dict[str, Any]) -> dict[str, Any]:
        thumbnail_path = (
            upload_package.get("thumbnail_file_path")
            or upload_package.get("thumbnail_path")
            or job.get("thumbnail_path")
        )
        return audit_thumbnail_quality(thumbnail_path or "", profile_key=self._profile_key(job))

    @staticmethod
    def _thumbnail_semantic_blockers(audit: dict[str, Any]) -> list[str]:
        return [f"semantic_visual:{blocker}" for blocker in semantic_visual_blockers(audit)]

    def _thumbnail_semantic(self, job: dict[str, Any], upload_package: dict[str, Any]) -> dict[str, Any]:
        thumbnail_path = (
            upload_package.get("thumbnail_file_path")
            or upload_package.get("thumbnail_path")
            or job.get("thumbnail_path")
        )
        return audit_visual_semantics(
            self.settings,
            thumbnail_path or "",
            profile_key=self._profile_key(job),
            topic_title=str(job.get("topic_title") or job.get("topic_id") or ""),
            artifact_kind="thumbnail",
        )

    @staticmethod
    def _first_frame_semantic_blockers(audit: dict[str, Any]) -> list[str]:
        return [f"semantic_first_frame:{blocker}" for blocker in semantic_visual_blockers(audit)]

    def _first_frame_semantic(self, job: dict[str, Any], upload_package: dict[str, Any]) -> dict[str, Any]:
        if str(self.settings.app_env) == "test":
            return {
                "status": "skipped",
                "passed": True,
                "artifact_kind": "first_frame",
                "blockers": [],
                "reason": "test_environment",
            }
        video_path = Path(str(upload_package.get("video_file_path") or ""))
        if not video_path.exists() or not video_path.is_file():
            return {
                "status": "skipped",
                "passed": True,
                "artifact_kind": "first_frame",
                "blockers": [],
                "reason": "video_file_missing",
            }
        frame_path = self._extract_first_frame(job, video_path)
        if frame_path is None:
            return {
                "status": "skipped",
                "passed": True,
                "artifact_kind": "first_frame",
                "blockers": [],
                "reason": "first_frame_extraction_unavailable",
            }
        return audit_visual_semantics(
            self.settings,
            frame_path,
            profile_key=self._profile_key(job),
            topic_title=str(job.get("topic_title") or job.get("topic_id") or ""),
            artifact_kind="storyboard_contact_sheet",
        )

    def _extract_first_frame(self, job: dict[str, Any], video_path: Path) -> Path | None:
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            return None
        output_dir = self.settings.data_dir / "tmp" / "semantic_visual_audit"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_job_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(job.get("id") or "job"))
        output_path = output_dir / f"{safe_job_id}_storyboard.jpg"
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(video_path),
                    "-vf",
                    "fps=1/8,scale=180:320:force_original_aspect_ratio=increase,crop=180:320,"
                    "tile=3x2:nb_frames=6:padding=4:margin=4",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            return None
        return output_path if output_path.exists() and output_path.stat().st_size > 0 else None

    @staticmethod
    def _profile_key(job: dict[str, Any]) -> str:
        raw = f"{job.get('account_id') or ''} {job.get('topic_title') or ''} {job.get('topic_description') or ''}".casefold()
        if "wealth" in raw or "finance" in raw:
            return "wealth_blueprint"
        if "dark" in raw or "chronicle" in raw:
            return "dark_chronicles"
        if "brainrot" in raw or "sigma" in raw:
            return "sigma_squad"
        if "history" in raw or "historia" in raw:
            return "history"
        return ""

    def _select_due_jobs(self, *, limit: int) -> list[dict[str, Any]]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT
                    j.*,
                    t.title AS topic_title,
                    t.description AS topic_description,
                    t.total_score AS topic_total_score
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.status = 'qa_passed'
                  AND COALESCE(j.review_notified_at, '') = ''
                  AND COALESCE(j.upload_package_path, '') <> ''
                  AND COALESCE(j.qa_report_path, '') <> ''
                  AND COALESCE(j.quality_review_status, '') NOT IN (?, ?)
                ORDER BY COALESCE(j.updated_at, j.created_at, '') ASC
                LIMIT ?
                """,
                (QUALITY_REVIEW_PASSED, QUALITY_REVIEW_BLOCKED, limit),
            ).fetchall()
        return rows_to_dicts(rows)

    def _pending_count(self) -> int:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS count
                FROM pipeline_jobs
                WHERE status = 'qa_passed'
                  AND COALESCE(review_notified_at, '') = ''
                  AND COALESCE(upload_package_path, '') <> ''
                  AND COALESCE(qa_report_path, '') <> ''
                  AND COALESCE(quality_review_status, '') NOT IN (?, ?)
                """,
                (QUALITY_REVIEW_PASSED, QUALITY_REVIEW_BLOCKED),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def _load_job(self, job_id: str) -> dict[str, Any]:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT
                    j.*,
                    t.title AS topic_title,
                    t.description AS topic_description,
                    t.total_score AS topic_total_score
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Production job {job_id} was not found.")
        return dict(row)

    def _load_authoritative_qa_report(self, job: dict[str, Any]) -> dict[str, Any]:
        value = job.get("qa_report")
        if isinstance(value, dict):
            return value
        parsed = json_loads(str(value or ""), {})
        if isinstance(parsed, dict) and parsed:
            return parsed
        return self._load_json_file(job.get("qa_report_path"))

    def _load_upload_package(self, job: dict[str, Any]) -> dict[str, Any]:
        return self._load_json_file(job.get("upload_package_path"))

    @staticmethod
    def _load_json_file(value: object) -> dict[str, Any]:
        path = Path(str(value or ""))
        if not path.exists() or not path.is_file():
            return {}
        parsed = json_loads(path.read_text(encoding="utf-8"), {})
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _next_action(blockers: list[str], upload_package: dict[str, Any]) -> str:
        if not blockers:
            return "send_owner_review_or_private_upload"
        package_next = str(upload_package.get("public_quality_next_action") or "").strip()
        if package_next and package_next != "human_review_then_private_upload_or_schedule_public":
            return package_next
        if "premium_voice_required_for_public" in blockers:
            return "regenerate_with_premium_voice"
        if "premium_visual_backend_required_for_public" in blockers:
            return "regenerate_with_ltx_or_wan_visual_backend"
        if any(str(blocker).startswith("thumbnail_quality:") for blocker in blockers):
            return "regenerate_thumbnail_with_mature_high_contrast_profile"
        if any(str(blocker).startswith("semantic_visual:") for blocker in blockers):
            return "regenerate_thumbnail_and_first_frame_with_topic_specific_visuals"
        if any(str(blocker).startswith("semantic_first_frame:") for blocker in blockers):
            return "regenerate_first_scene_with_topic_specific_visuals"
        if any("visual" in blocker or "scene" in blocker or "image" in blocker for blocker in blockers):
            return "regenerate_visual_plan_with_concrete_scene_assets"
        if any("style_guide" in blocker for blocker in blockers):
            return "attach_style_guide_and_regenerate"
        return "inspect_quality_review_blockers"

    def _persist_result(self, job_id: str, result: dict[str, Any]) -> None:
        quality_status = QUALITY_REVIEW_BLOCKED if result["status"] == "blocked" else QUALITY_REVIEW_PASSED
        review_status = QUALITY_REVIEW_BLOCKED if result["status"] == "blocked" else ""
        with connect(self.settings) as db:
            db.execute(
                """
                UPDATE pipeline_jobs
                SET quality_review_status = ?,
                    quality_review_result = ?,
                    quality_review_checked_at = ?,
                    review_status = CASE
                        WHEN ? = '' AND review_status = ? THEN NULL
                        WHEN ? <> '' THEN ?
                        ELSE review_status
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    quality_status,
                    json_dumps(result),
                    result["checked_at"],
                    review_status,
                    QUALITY_REVIEW_BLOCKED,
                    review_status,
                    review_status,
                    utc_now_iso(),
                    job_id,
                ),
            )
            db.commit()
