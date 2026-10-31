from __future__ import annotations

from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_dumps, json_loads, utc_now_iso
from atnos.production.publication import public_promotion_blockers
from atnos.production.publication_quality import public_quality_blockers, public_quality_evidence


class DistributionPlanError(RuntimeError):
    """Blad budowania planu dystrybucji produkcyjnej."""


def build_distribution_plan(settings: Settings, *, job_id: str) -> dict[str, Any]:
    """Zbuduj bezpieczny plan dystrybucji jednego Shorta na platformy docelowe."""
    job = _load_job(settings, job_id)
    package = _load_json_path(job.get("upload_package_path"))
    qa_report = _authoritative_qa_report(job, package)
    upload_metadata = package.get("upload_metadata") if isinstance(package.get("upload_metadata"), dict) else {}
    video_path = _video_path(job, package)
    thumbnail_path = _existing_path(package.get("thumbnail_file_path") or job.get("thumbnail_path"))
    subtitle_path = _existing_path(package.get("subtitle_file_path") or job.get("subtitle_path"))

    artifact_blockers = _artifact_blockers(job, package, qa_report, video_path)
    public_blockers = public_quality_blockers(settings, {**job, "qa_report": qa_report})
    private_blockers = _private_upload_blockers(job, package, qa_report, video_path)
    export_ready = not artifact_blockers
    public_quality_ready = export_ready and not public_blockers

    base_artifacts = {
        "video_file_path": str(video_path) if video_path else "",
        "thumbnail_file_path": str(thumbnail_path) if thumbnail_path else "",
        "subtitle_file_path": str(subtitle_path) if subtitle_path else "",
        "qa_report_path": str(_existing_path(job.get("qa_report_path")) or ""),
        "upload_package_path": str(_existing_path(job.get("upload_package_path")) or ""),
    }
    base_metadata = {
        "title": str(upload_metadata.get("title") or job.get("topic_title") or job.get("topic_id") or "")[:100],
        "description": str(upload_metadata.get("description") or "")[:5000],
        "tags": [str(tag) for tag in (upload_metadata.get("tags") or [])][:15],
        "language": str(upload_metadata.get("language") or upload_metadata.get("default_language") or ""),
        "made_for_kids": bool(upload_metadata.get("made_for_kids", False)),
    }

    youtube_public_blockers = public_promotion_blockers(settings, {**job, "qa_report": qa_report})
    platforms = [
        _youtube_plan(
            job=job,
            export_ready=export_ready,
            public_quality_ready=public_quality_ready,
            artifact_blockers=artifact_blockers,
            private_blockers=private_blockers,
            public_blockers=youtube_public_blockers,
            artifacts=base_artifacts,
            metadata=base_metadata,
        ),
        _planned_platform_export_plan(
            platform="tiktok",
            label="TikTok",
            connector_key="tiktok",
            job_id=job_id,
            export_ready=export_ready,
            public_quality_ready=public_quality_ready,
            artifact_blockers=artifact_blockers,
            public_blockers=public_blockers,
            artifacts=base_artifacts,
            metadata={
                **base_metadata,
                "caption": _short_caption(base_metadata),
                "recommended_visibility": "public_after_manual_review",
            },
        ),
        _planned_platform_export_plan(
            platform="instagram_reels",
            label="Instagram Reels",
            connector_key="instagram",
            job_id=job_id,
            export_ready=export_ready,
            public_quality_ready=public_quality_ready,
            artifact_blockers=artifact_blockers,
            public_blockers=public_blockers,
            artifacts=base_artifacts,
            metadata={
                **base_metadata,
                "caption": _short_caption(base_metadata),
                "recommended_visibility": "public_after_manual_review",
            },
        ),
    ]

    return {
        "job_id": job_id,
        "account_id": job.get("account_id"),
        "topic_id": job.get("topic_id"),
        "status": _overall_status(platforms),
        "export_ready": export_ready,
        "public_quality_ready": public_quality_ready,
        "artifact_blockers": artifact_blockers,
        "public_quality_blockers": public_blockers,
        "quality_evidence": public_quality_evidence({**job, "qa_report": qa_report}),
        "platforms": platforms,
        "next_actions": _next_actions(platforms, artifact_blockers, public_blockers),
    }


def build_distribution_release_package(
    settings: Settings,
    *,
    job_id: str,
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """Zapisz paczki release dla platform, ktore sa gotowe do eksportu lub prywatnego uploadu."""
    job = _load_job(settings, job_id)
    plan = build_distribution_plan(settings, job_id=job_id)
    requested = {str(item).strip() for item in (platforms or []) if str(item).strip()}
    platform_plans = [
        platform
        for platform in plan["platforms"]
        if not requested or str(platform.get("platform") or "") in requested
    ]
    if requested and len(platform_plans) != len(requested):
        known = {str(platform.get("platform") or "") for platform in plan["platforms"]}
        missing = sorted(requested - known)
        raise DistributionPlanError(f"Unknown distribution platform(s): {', '.join(missing)}")

    package_root = _distribution_package_root(job)
    packages: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for platform in platform_plans:
        if not _can_write_release_package(platform):
            blocked.append(
                {
                    "platform": platform.get("platform"),
                    "label": platform.get("label"),
                    "status": "blocked",
                    "blockers": list(platform.get("blockers") or []),
                    "public_blockers": list(platform.get("public_blockers") or []),
                }
            )
            continue
        packages.append(_write_platform_release_package(package_root, job, platform, plan))

    status = "created" if packages and not blocked else "partial" if packages else "blocked"
    return {
        "job_id": job_id,
        "account_id": job.get("account_id"),
        "topic_id": job.get("topic_id"),
        "status": status,
        "package_root": str(package_root),
        "created_count": len(packages),
        "blocked_count": len(blocked),
        "packages": packages,
        "blocked": blocked,
        "plan": plan,
    }


def _load_job(settings: Settings, job_id: str) -> dict[str, Any]:
    with connect(settings) as db:
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
        raise DistributionPlanError(f"Job {job_id} was not found.")
    payload = dict(row)
    for field in (
        "hooks",
        "script",
        "audio_timestamps",
        "asset_plan",
        "video_manifest",
        "qa_report",
        "upload_result",
        "public_publish_result",
    ):
        payload[field] = _json_field(payload.get(field), {})
    return payload


def _youtube_plan(
    *,
    job: dict[str, Any],
    export_ready: bool,
    public_quality_ready: bool,
    artifact_blockers: list[str],
    private_blockers: list[str],
    public_blockers: list[str],
    artifacts: dict[str, str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    status = str(job.get("status") or "")
    uploaded_private = status in {"uploaded_private", "scheduled_public", "published_public"}
    if status == "published_public":
        platform_status = "published_public"
    elif status == "scheduled_public":
        platform_status = "scheduled_public"
    elif uploaded_private:
        platform_status = "uploaded_private"
    elif not export_ready:
        platform_status = "blocked"
    elif private_blockers:
        platform_status = "private_upload_blocked"
    else:
        platform_status = "api_private_ready"
    return {
        "platform": "youtube_shorts",
        "label": "YouTube Shorts",
        "connector_key": "youtube-studio",
        "status": platform_status,
        "export_ready": export_ready,
        "upload_ready": export_ready and not private_blockers,
        "private_upload_ready": export_ready and not private_blockers,
        "public_upload_ready": uploaded_private and public_quality_ready and not public_blockers,
        "requires_manual_upload": False,
        "requires_owner_approval": True,
        "connector_status": "implemented",
        "blockers": list(dict.fromkeys([*artifact_blockers, *private_blockers])),
        "public_blockers": public_blockers,
        "warnings": _warnings_for_public_quality(public_quality_ready, public_blockers),
        "artifact_paths": artifacts,
        "metadata": metadata,
        "next_actions": _youtube_next_actions(job, private_blockers, public_blockers),
        "endpoints": {
            "private_upload": f"/api/production/jobs/{job['id']}/upload-private",
            "public_promotion": f"/api/production/jobs/{job['id']}/schedule-public",
            "release_package": f"/api/production/jobs/{job['id']}/distribution-package",
        },
    }


def _planned_platform_export_plan(
    *,
    platform: str,
    label: str,
    connector_key: str,
    job_id: str,
    export_ready: bool,
    public_quality_ready: bool,
    artifact_blockers: list[str],
    public_blockers: list[str],
    artifacts: dict[str, str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    connector_blocker = f"{connector_key}_connector_not_configured"
    quality_blockers = [] if public_quality_ready else public_blockers
    blockers = list(dict.fromkeys([*artifact_blockers, *quality_blockers, connector_blocker]))
    status = "manual_upload_required" if export_ready and public_quality_ready else "blocked"
    return {
        "platform": platform,
        "label": label,
        "connector_key": connector_key,
        "status": status,
        "export_ready": export_ready and public_quality_ready,
        "upload_ready": False,
        "private_upload_ready": False,
        "public_upload_ready": False,
        "requires_manual_upload": export_ready and public_quality_ready,
        "requires_owner_approval": True,
        "connector_status": "planned",
        "blockers": blockers,
        "public_blockers": quality_blockers,
        "warnings": [
            "Official API upload connector is not implemented yet; use this as an export package only."
        ]
        if export_ready and public_quality_ready
        else _warnings_for_public_quality(public_quality_ready, quality_blockers),
        "artifact_paths": artifacts,
        "metadata": metadata,
        "next_actions": _planned_platform_next_actions(label, export_ready, public_quality_ready, quality_blockers),
        "endpoints": {
            "release_package": f"/api/production/jobs/{job_id}/distribution-package",
        },
    }


def _artifact_blockers(
    job: dict[str, Any],
    package: dict[str, Any],
    qa_report: dict[str, Any],
    video_path: Path | None,
) -> list[str]:
    blockers: list[str] = []
    package_path = _existing_path(job.get("upload_package_path"))
    qa_path = _existing_path(job.get("qa_report_path") or package.get("qa_report_path"))
    if not package_path:
        blockers.append("upload_package_missing")
    if not qa_path and not qa_report:
        blockers.append("qa_report_missing")
    if not video_path:
        blockers.append("final_video_missing")
    if str(qa_report.get("overall") or "").strip().lower() != "passed":
        blockers.append("qa_not_passed")
    if not bool(qa_report.get("auto_uploadable")):
        blockers.append("qa_not_auto_uploadable")
    if list(qa_report.get("blocking_failures") or []):
        blockers.append("qa_blocking_failures")
    return list(dict.fromkeys(blockers))


def _private_upload_blockers(
    job: dict[str, Any],
    package: dict[str, Any],
    qa_report: dict[str, Any],
    video_path: Path | None,
) -> list[str]:
    blockers = _artifact_blockers(job, package, qa_report, video_path)
    visibility = str((package.get("upload_metadata") or {}).get("visibility") or "private").strip().lower()
    if visibility != "private":
        blockers.append("youtube_private_visibility_required")
    if str(job.get("status") or "") not in {"qa_passed", "upload_connection_required", "private_upload_failed"}:
        if str(job.get("status") or "") not in {"uploaded_private", "scheduled_public", "published_public"}:
            blockers.append("job_not_ready_for_private_upload")
    return list(dict.fromkeys(blockers))


def _youtube_next_actions(job: dict[str, Any], private_blockers: list[str], public_blockers: list[str]) -> list[str]:
    status = str(job.get("status") or "")
    if private_blockers:
        return ["Fix QA/artifact blockers before attempting YouTube private upload."]
    if status not in {"uploaded_private", "scheduled_public", "published_public"}:
        return [f"POST /api/production/jobs/{job['id']}/upload-private after owner review."]
    if public_blockers:
        return ["Resolve public promotion blockers before scheduling the private upload publicly."]
    return [f"POST /api/production/jobs/{job['id']}/schedule-public with explicit approval and optional publish_at."]


def _planned_platform_next_actions(
    label: str,
    export_ready: bool,
    public_quality_ready: bool,
    public_blockers: list[str],
) -> list[str]:
    if not export_ready:
        return ["Generate a complete upload package with final MP4, thumbnail, subtitles and QA report."]
    if not public_quality_ready:
        return [f"Resolve public quality blockers before exporting to {label}: {', '.join(public_blockers)}."]
    return [
        f"Use artifact_paths.video_file_path for manual {label} upload after owner review.",
        f"Implement official {label} connector before enabling automatic upload.",
    ]


def _next_actions(platforms: list[dict[str, Any]], artifact_blockers: list[str], public_blockers: list[str]) -> list[str]:
    if artifact_blockers:
        return ["Fix production artifacts first: " + ", ".join(artifact_blockers)]
    if public_blockers:
        return ["Fix public quality blockers before multi-platform release: " + ", ".join(public_blockers)]
    ready_exports = [platform["label"] for platform in platforms if platform.get("export_ready")]
    if ready_exports:
        return ["Review exports for: " + ", ".join(ready_exports)]
    return ["No distribution action is ready."]


def _overall_status(platforms: list[dict[str, Any]]) -> str:
    if any(platform.get("public_upload_ready") for platform in platforms):
        return "public_upload_ready"
    if any(platform.get("upload_ready") for platform in platforms):
        return "private_upload_ready"
    if any(platform.get("export_ready") for platform in platforms):
        return "export_ready"
    return "blocked"


def _warnings_for_public_quality(public_quality_ready: bool, blockers: list[str]) -> list[str]:
    if public_quality_ready:
        return []
    if blockers:
        return ["Public-quality release is blocked: " + ", ".join(blockers)]
    return ["Public-quality release is not proven."]


def _video_path(job: dict[str, Any], package: dict[str, Any]) -> Path | None:
    candidates = [
        package.get("video_file_path"),
        job.get("video_path"),
        Path(str(job.get("artifact_dir") or "")) / "assets" / "final_short.mp4" if job.get("artifact_dir") else None,
    ]
    for candidate in candidates:
        path = _existing_path(candidate)
        if path:
            return path
    return None


def _authoritative_qa_report(job: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    inline = job.get("qa_report") if isinstance(job.get("qa_report"), dict) else {}
    path_payload = _load_json_path(job.get("qa_report_path") or package.get("qa_report_path"))
    if path_payload:
        return path_payload
    return inline if isinstance(inline, dict) else {}


def _load_json_path(value: Any) -> dict[str, Any]:
    path = _existing_path(value)
    if path is None:
        return {}
    parsed = json_loads(path.read_text(encoding="utf-8"), {})
    return parsed if isinstance(parsed, dict) else {}


def _existing_path(value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    return path if path.exists() else None


def _json_field(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    return json_loads(str(value or ""), fallback)


def _short_caption(metadata: dict[str, Any]) -> str:
    title = str(metadata.get("title") or "").strip()
    tags = [str(tag).strip().replace(" ", "") for tag in (metadata.get("tags") or []) if str(tag).strip()]
    hashtags = " ".join(f"#{tag.lstrip('#')}" for tag in tags[:5])
    return " ".join(part for part in (title, hashtags) if part).strip()[:2200]


def _distribution_package_root(job: dict[str, Any]) -> Path:
    raw_artifact_dir = str(job.get("artifact_dir") or "").strip()
    if not raw_artifact_dir:
        raise DistributionPlanError("Job artifact_dir is missing.")
    artifact_dir = Path(raw_artifact_dir).expanduser()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    root = artifact_dir / "distribution"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _can_write_release_package(platform: dict[str, Any]) -> bool:
    if platform.get("platform") == "youtube_shorts":
        return bool(platform.get("private_upload_ready") or platform.get("export_ready"))
    return bool(platform.get("export_ready"))


def _write_platform_release_package(
    package_root: Path,
    job: dict[str, Any],
    platform: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    platform_key = str(platform.get("platform") or "unknown")
    target_dir = package_root / _safe_path_segment(platform_key)
    target_dir.mkdir(parents=True, exist_ok=True)
    metadata = platform.get("metadata") if isinstance(platform.get("metadata"), dict) else {}
    artifacts = platform.get("artifact_paths") if isinstance(platform.get("artifact_paths"), dict) else {}
    caption = str(metadata.get("caption") or _short_caption(metadata))
    hashtags = _hashtags(metadata)
    manifest = {
        "created_at": utc_now_iso(),
        "job_id": job.get("id"),
        "account_id": job.get("account_id"),
        "topic_id": job.get("topic_id"),
        "platform": platform_key,
        "label": platform.get("label"),
        "status": _release_package_status(platform),
        "connector_status": platform.get("connector_status"),
        "requires_manual_upload": bool(platform.get("requires_manual_upload")),
        "requires_owner_approval": bool(platform.get("requires_owner_approval", True)),
        "video_file_path": artifacts.get("video_file_path") or "",
        "thumbnail_file_path": artifacts.get("thumbnail_file_path") or "",
        "subtitle_file_path": artifacts.get("subtitle_file_path") or "",
        "qa_report_path": artifacts.get("qa_report_path") or "",
        "upload_package_path": artifacts.get("upload_package_path") or "",
        "metadata": {
            **metadata,
            "caption": caption,
            "hashtags": hashtags,
        },
        "quality": {
            "public_quality_ready": bool(plan.get("public_quality_ready")),
            "quality_evidence": plan.get("quality_evidence") or {},
        },
        "blockers": list(platform.get("blockers") or []),
        "public_blockers": list(platform.get("public_blockers") or []),
        "next_actions": list(platform.get("next_actions") or []),
    }
    manifest_path = target_dir / "release_manifest.json"
    manifest_path.write_text(json_dumps(manifest), encoding="utf-8")
    _write_text(target_dir / "title.txt", str(metadata.get("title") or ""))
    _write_text(target_dir / "description.txt", str(metadata.get("description") or ""))
    _write_text(target_dir / "caption.txt", caption)
    _write_text(target_dir / "hashtags.txt", " ".join(hashtags))
    _write_text(target_dir / "artifact_paths.md", _artifact_paths_markdown(manifest))
    readme_path = target_dir / "README.md"
    _write_text(readme_path, _release_readme(manifest))
    return {
        "platform": platform_key,
        "label": platform.get("label"),
        "status": manifest["status"],
        "package_dir": str(target_dir),
        "manifest_path": str(manifest_path),
        "readme_path": str(readme_path),
        "caption_path": str(target_dir / "caption.txt"),
        "title_path": str(target_dir / "title.txt"),
        "description_path": str(target_dir / "description.txt"),
        "hashtags_path": str(target_dir / "hashtags.txt"),
        "video_file_path": manifest["video_file_path"],
    }


def _release_package_status(platform: dict[str, Any]) -> str:
    if platform.get("public_upload_ready"):
        return "public_upload_ready"
    if platform.get("upload_ready") or platform.get("private_upload_ready"):
        return "private_upload_ready"
    if platform.get("requires_manual_upload"):
        return "manual_upload_package_ready"
    return "export_package_ready"


def _hashtags(metadata: dict[str, Any]) -> list[str]:
    tags = [str(tag).strip().replace(" ", "") for tag in (metadata.get("tags") or []) if str(tag).strip()]
    hashtags: list[str] = []
    for tag in tags:
        value = f"#{tag.lstrip('#')}"
        if value not in hashtags:
            hashtags.append(value)
    return hashtags[:8]


def _artifact_paths_markdown(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Artifact paths",
            "",
            f"- video: `{manifest.get('video_file_path') or ''}`",
            f"- thumbnail: `{manifest.get('thumbnail_file_path') or ''}`",
            f"- subtitles: `{manifest.get('subtitle_file_path') or ''}`",
            f"- QA: `{manifest.get('qa_report_path') or ''}`",
            f"- upload package: `{manifest.get('upload_package_path') or ''}`",
            "",
        ]
    )


def _release_readme(manifest: dict[str, Any]) -> str:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    next_actions = manifest.get("next_actions") if isinstance(manifest.get("next_actions"), list) else []
    return "\n".join(
        [
            f"# {manifest.get('label') or manifest.get('platform')} release package",
            "",
            f"- status: `{manifest.get('status')}`",
            f"- connector_status: `{manifest.get('connector_status')}`",
            f"- manual_upload: `{manifest.get('requires_manual_upload')}`",
            f"- owner_approval_required: `{manifest.get('requires_owner_approval')}`",
            "",
            "## Metadata",
            "",
            f"- title: {metadata.get('title') or ''}",
            f"- language: {metadata.get('language') or ''}",
            f"- made_for_kids: {metadata.get('made_for_kids')}",
            f"- hashtags: {' '.join(metadata.get('hashtags') or [])}",
            "",
            "## Caption",
            "",
            str(metadata.get("caption") or ""),
            "",
            "## Next actions",
            "",
            *[f"- {action}" for action in next_actions],
            "",
        ]
    )


def _write_text(path: Path, value: str) -> None:
    path.write_text(str(value or "").strip() + "\n", encoding="utf-8")


def _safe_path_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip().lower())
    return cleaned.strip("-") or "platform"
