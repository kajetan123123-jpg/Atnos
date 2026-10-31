from __future__ import annotations

from pathlib import Path
import re
from typing import Any


MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000
MAX_PREVIEW_CHARS = 125
MAX_TAGS_CHARS = 500


def generate_yt_description(
    *,
    topic: str,
    hook: str,
    tags: list[str] | tuple[str, ...],
    affiliate_links: dict[str, str] | None = None,
    duration_seconds: float | None = None,
    timestamps: list[tuple[str, str]] | None = None,
    language: str = "en",
) -> str:
    """Build a minimal English YouTube Shorts description for private upload review."""
    keyword = _clean_line(topic) or "Atnos Short"
    hook_line = _preview_line(keyword=keyword, hook=hook)
    normalized_tags = _normalize_tags(tags)
    hashtags = _hashtags(normalized_tags)
    lines = [
        hook_line,
        "",
        _topic_line(keyword),
        "",
        _cta(),
    ]
    if _should_include_timestamps(duration_seconds=duration_seconds, timestamps=timestamps):
        lines.extend(["", "Timestamps:", *[f"{timecode} - {label}" for timecode, label in timestamps or []]])
    if affiliate_links:
        lines.extend(["", "Linki:", *[f"- {name}: {url}" for name, url in affiliate_links.items()]])
    lines.extend(["", hashtags])
    return "\n".join(lines).strip()[:MAX_DESCRIPTION_CHARS]


def build_upload_body(
    *,
    title: str,
    description: str,
    tags: list[str] | tuple[str, ...],
    category_id: str = "28",
    privacy: str = "private",
    default_language: str = "en",
    default_audio_language: str = "en",
) -> dict[str, Any]:
    """Zbuduj body dla youtube.videos().insert bez wykonywania requestu."""
    return {
        "snippet": {
            "title": _clean_line(title)[:MAX_TITLE_CHARS],
            "description": str(description or "")[:MAX_DESCRIPTION_CHARS],
            "tags": _tags_within_youtube_limit(tags),
            "categoryId": str(category_id or "28"),
            "defaultLanguage": default_language,
            "defaultAudioLanguage": default_audio_language,
        },
        "status": {
            "privacyStatus": _normalize_privacy(privacy),
            "selfDeclaredMadeForKids": False,
        },
    }


def validate_thumbnail_for_youtube(path: str | Path) -> dict[str, Any]:
    """Zweryfikuj miniaturę pod upload YouTube bez kontaktu z API."""
    thumbnail_path = Path(path)
    issues: list[str] = []
    details: dict[str, Any] = {
        "path": str(thumbnail_path),
        "exists": thumbnail_path.exists(),
        "valid": False,
        "issues": issues,
        "width": None,
        "height": None,
        "size_bytes": None,
    }
    if not thumbnail_path.exists():
        issues.append("thumbnail_missing")
        return details
    details["size_bytes"] = thumbnail_path.stat().st_size
    if thumbnail_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        issues.append("unsupported_extension")
    if thumbnail_path.stat().st_size > 2 * 1024 * 1024:
        issues.append("file_larger_than_2mb")
    try:
        from PIL import Image

        with Image.open(thumbnail_path) as image:
            width, height = image.size
    except Exception as exc:
        issues.append(f"image_open_failed:{type(exc).__name__}")
        return details
    details["width"] = width
    details["height"] = height
    is_vertical = width < height
    meets_short_native = is_vertical and width >= 1080 and height >= 1920
    meets_standard = width >= 1280 and height >= 720
    if not (meets_short_native or meets_standard):
        issues.append("resolution_below_youtube_minimum")
    details["valid"] = not issues
    return details


def build_thumbnail_upload_descriptor(video_id: str, thumb_path: str | Path) -> dict[str, Any]:
    """Zwróć strukturę walidującą wywołanie thumbnails().set()."""
    validation = validate_thumbnail_for_youtube(thumb_path)
    return {
        "video_id": str(video_id).strip(),
        "thumbnail_path": str(Path(thumb_path)),
        "valid": bool(str(video_id).strip()) and bool(validation["valid"]),
        "issues": ["missing_video_id"] if not str(video_id).strip() else list(validation["issues"]),
        "validation": validation,
    }


def _preview_line(*, keyword: str, hook: str) -> str:
    clean_hook = _clean_line(hook)
    if not clean_hook:
        return keyword[:MAX_PREVIEW_CHARS]
    if keyword.lower() not in clean_hook[:MAX_PREVIEW_CHARS].lower():
        clean_hook = f"{keyword}: {clean_hook}"
    return clean_hook[:MAX_PREVIEW_CHARS].rstrip()


def _topic_line(keyword: str) -> str:
    return f"Topic: {keyword}. Short-form review version prepared for quality, retention, and hook checks."


def _cta() -> str:
    return "Subscribe for more concise Shorts."


def _should_include_timestamps(
    *,
    duration_seconds: float | None,
    timestamps: list[tuple[str, str]] | None,
) -> bool:
    return bool(timestamps) and (duration_seconds or 0) > 60


def _hashtags(tags: list[str]) -> str:
    base = ["Shorts"]
    merged = [*base, *tags]
    seen: set[str] = set()
    result: list[str] = []
    for tag in merged:
        clean = re.sub(r"[^0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż_-]+", "", tag.replace(" ", ""))
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(f"#{clean}")
        if len(result) >= 5:
            break
    return " ".join(result)


def _tags_within_youtube_limit(tags: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    total = 0
    for tag in _normalize_tags(tags):
        addition = len(tag) + (1 if result else 0)
        if total + addition > MAX_TAGS_CHARS:
            break
        result.append(tag)
        total += addition
    return result


def _normalize_tags(tags: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags or ():
        tag = _clean_line(str(raw))
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag[:60])
    return result


def _normalize_privacy(value: str) -> str:
    normalized = str(value or "private").strip().lower()
    return normalized if normalized in {"private", "unlisted", "public"} else "private"


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
