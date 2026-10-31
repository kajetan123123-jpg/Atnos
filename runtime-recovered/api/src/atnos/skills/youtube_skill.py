from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from atnos.core.config import Settings
from youtube.youtube_models import YouTubeUploadMetadata, YouTubeUploadPreview, YouTubeUploadResult
from youtube.youtube_upload import prepare_upload, upload_private_short


def detects_youtube_private_upload_request(text: str) -> bool:
    normalized = _normalize(text)
    return "youtube" in normalized and any(token in normalized for token in ("wrzuc", "upload", "wyslij")) and (
        "short" in normalized or "film" in normalized or "video" in normalized
    )


def detects_youtube_upload_confirmation(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {"potwierdz", "tak potwierdzam", "uploaduj", "wrzuc", "zatwierdzam upload"}


def build_youtube_upload_preview(settings: Settings) -> YouTubeUploadPreview:
    return prepare_upload(settings=settings, privacy="private")


def execute_youtube_upload(settings: Settings, preview_payload: dict, *, youtube_service=None) -> YouTubeUploadResult:
    preview = YouTubeUploadPreview(
        local_file=Path(preview_payload["local_file"]),
        thumbnail_path=Path(preview_payload["thumbnail_path"]) if preview_payload.get("thumbnail_path") else None,
        captions_path=Path(preview_payload["captions_path"]) if preview_payload.get("captions_path") else None,
        requires_approval=bool(preview_payload.get("requires_approval", True)),
        metadata=YouTubeUploadMetadata(**preview_payload["metadata"]),
    )
    return upload_private_short(settings=settings, preview=preview, approved=True, youtube_service=youtube_service)


def format_upload_preview(preview: YouTubeUploadPreview) -> str:
    return (
        "Przygotowałem prywatny upload YouTube. Wymaga approval przed wysłaniem.\n"
        f"- plik: `{preview.local_file}`\n"
        f"- title: `{preview.metadata.title}`\n"
        f"- privacy: `{preview.metadata.privacy}`\n"
        "- napisz `potwierdz`, jeśli mam wrzucić jako PRIVATE."
    )


def format_upload_result(result: YouTubeUploadResult) -> str:
    return (
        "Short został wrzucony na YouTube jako PRIVATE.\n"
        f"- video_id: `{result.video_id}`\n"
        f"- link: {result.youtube_url}\n"
        f"- log: `data/logs/youtube_uploads.jsonl`"
    )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    collapsed = re.sub(r"[^a-z0-9 ]+", " ", ascii_only.casefold())
    return " ".join(collapsed.split())
