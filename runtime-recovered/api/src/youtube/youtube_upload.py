from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload

from atnos.core.config import Settings
from youtube.youtube_auth import build_youtube_service
from youtube.youtube_metadata import build_upload_body, generate_yt_description
from youtube.youtube_models import YouTubeUploadMetadata, YouTubeUploadPreview, YouTubeUploadResult
from youtube.upload_guard import assert_upload_video_file, validate_upload_video_file


class YouTubeUploadError(RuntimeError):
    pass


def find_latest_short(settings: Settings) -> Path | None:
    root = settings.shorts_output_dir
    if not root.exists():
        return None
    candidates = sorted(root.glob("*/final.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        validation = validate_upload_video_file(candidate, settings=settings, require_run_artifacts=True)
        if validation.valid:
            return candidate
    return None


def prepare_upload(
    *,
    settings: Settings,
    video_path: Path | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    privacy: str | None = None,
) -> YouTubeUploadPreview:
    resolved_video = (video_path or find_latest_short(settings))
    if resolved_video is None or not resolved_video.exists():
        raise YouTubeUploadError("No upload-ready final.mp4 was found for YouTube upload.")
    try:
        resolved_video = assert_upload_video_file(
            resolved_video,
            settings=settings,
            require_run_artifacts=video_path is None,
        )
    except ValueError as exc:
        raise YouTubeUploadError(str(exc)) from exc
    run_dir = resolved_video.parent
    production_report = run_dir / "production_report.md"
    release_package = _load_release_package(run_dir / "youtube_upload_package.json")
    report_text = production_report.read_text(encoding="utf-8") if production_report.exists() else ""
    resolved_title = (
        title
        or _string_from_package(release_package, "title")
        or _title_from_report(report_text)
        or f"Atnos Short - {run_dir.name}"
    )[:100]
    resolved_privacy = (
        privacy
        or _string_from_package(release_package, "privacy")
        or settings.youtube_upload_privacy
        or "private"
    ).strip().lower()
    resolved_tags = tags or _tags_from_package(release_package) or _tags_from_topic(run_dir.name)
    resolved_description = (
        description
        or _string_from_package(release_package, "description")
        or _description_from_report(report_text)
        or generate_yt_description(
            topic=run_dir.name.replace("-", " "),
            hook=resolved_title,
            tags=resolved_tags,
            language=settings.youtube_default_language,
        )
    )
    _enforce_privacy_policy(settings, resolved_privacy)
    return YouTubeUploadPreview(
        local_file=resolved_video,
        thumbnail_path=_existing(run_dir / "thumbnail.png"),
        captions_path=_existing(run_dir / "subtitles.srt"),
        requires_approval=settings.require_upload_approval or _package_requires_approval(release_package),
        metadata=YouTubeUploadMetadata(
            title=resolved_title,
            description=resolved_description,
            tags=tuple(resolved_tags),
            privacy=resolved_privacy,
            category_id=settings.youtube_category_id,
            default_language=settings.youtube_default_language,
            default_audio_language=settings.youtube_default_audio_language,
        ),
    )


def upload_private_short(
    *,
    settings: Settings,
    preview: YouTubeUploadPreview,
    approved: bool,
    youtube_service=None,
) -> YouTubeUploadResult:
    if preview.requires_approval and not approved:
        raise YouTubeUploadError("Upload requires explicit human approval.")
    _enforce_privacy_policy(settings, preview.metadata.privacy)
    try:
        assert_upload_video_file(preview.local_file, settings=settings, require_run_artifacts=False)
    except ValueError as exc:
        raise YouTubeUploadError(str(exc)) from exc
    youtube = youtube_service or build_youtube_service(settings)
    body = build_upload_body(
        title=preview.metadata.title,
        description=preview.metadata.description,
        tags=preview.metadata.tags,
        category_id=preview.metadata.category_id,
        privacy=preview.metadata.privacy,
        default_language=preview.metadata.default_language,
        default_audio_language=preview.metadata.default_audio_language,
    )
    response = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(preview.local_file), mimetype="video/mp4", resumable=True),
    ).execute()
    video_id = str(response.get("id") or "").strip()
    if not video_id:
        raise YouTubeUploadError("YouTube upload returned no video_id.")
    warnings = []
    if preview.thumbnail_path and preview.thumbnail_path.exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(preview.thumbnail_path), mimetype="image/png"),
            ).execute()
        except Exception as exc:  # pragma: no cover - API edge
            warnings.append(f"thumbnail_upload_failed: {exc}")
    result = YouTubeUploadResult(
        video_id=video_id,
        title=preview.metadata.title,
        description=preview.metadata.description,
        privacy=preview.metadata.privacy,
        local_file=preview.local_file,
        youtube_url=f"https://www.youtube.com/watch?v={video_id}",
        timestamp=datetime.now(UTC).isoformat(),
        warnings=tuple(warnings),
    )
    _write_jsonl(settings.data_dir / "logs" / "youtube_uploads.jsonl", result.to_dict())
    return result


def _enforce_privacy_policy(settings: Settings, privacy: str) -> None:
    if privacy == "public" and not settings.allow_public_upload:
        raise YouTubeUploadError("Public YouTube upload is blocked because ALLOW_PUBLIC_UPLOAD=false.")


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _existing(path: Path) -> Path | None:
    return path.resolve() if path.exists() else None


def _load_release_package(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_from_package(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _tags_from_package(payload: dict[str, Any]) -> list[str] | None:
    raw_tags = payload.get("tags")
    if not isinstance(raw_tags, list):
        return None
    tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
    return tags[:15] or None


def _package_requires_approval(payload: dict[str, Any]) -> bool:
    return bool(payload.get("requires_upload_approval"))


def _title_from_report(report: str) -> str | None:
    for line in report.splitlines():
        normalized = line.strip()
        lower = normalized.lower()
        if lower.startswith("- title:") or lower.startswith("- topic:"):
            value = normalized.split(":", maxsplit=1)[1].strip(" `")
            return value[:100] if value else None
    for line in report.splitlines():
        normalized = line.strip()
        if normalized.startswith("# "):
            value = normalized.lstrip("#").strip()
            if value and value.lower() not in {"production report", "agent task log"}:
                return value[:100]
    return None


def _description_from_report(report: str) -> str | None:
    if not report.strip():
        return None
    return "Wersja prywatna do review.\n\n" + report[:1800]


def _tags_from_topic(value: str) -> list[str]:
    tokens = [token for token in value.replace("-", " ").split() if len(token) > 2]
    return ["Atnos", "shorts", "AI", *tokens[:8]]
