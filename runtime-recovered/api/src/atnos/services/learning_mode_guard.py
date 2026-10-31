from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from atnos.core.config import Settings


LEARNING_MODE_BLOCK_REASON = "Write operations are disabled in LEARNING_MODE"
YOUTUBE_WRITE_ACTION = "youtube_write_operation"


def learning_mode_write_block_active(settings: Settings) -> bool:
    """Zwraca True tylko dla jawnie aktywnego, twardego trybu nauki."""
    return bool(settings.learning_mode and settings.youtube_write_block)


def youtube_write_block_response(settings: Settings) -> dict[str, Any] | None:
    """Buduje jednolity wynik dry-run dla zablokowanej operacji YouTube."""
    if not learning_mode_write_block_active(settings):
        return None
    return {
        "mode": "learning",
        "allowed": False,
        "action": YOUTUBE_WRITE_ACTION,
        "reason": LEARNING_MODE_BLOCK_REASON,
        "timestamp": datetime.now(ZoneInfo("Europe/Warsaw")).isoformat(),
    }


def classify_blocked_http_action(method: str, path: str, settings: Settings) -> str | None:
    """Rozpoznaje mutacje YouTube/OAuth, zanim żądanie dotrze do routera."""
    if not learning_mode_write_block_active(settings):
        return None

    normalized_method = method.strip().upper()
    normalized_path = "/" + path.strip().casefold().lstrip("/")

    # Callback OAuth zapisuje nowy token mimo metody GET, dlatego jest jawnie blokowany.
    if normalized_path.startswith("/oauth/youtube-studio/callback"):
        return "oauth_callback_mutation"

    if normalized_method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    if normalized_path.startswith("/oauth/youtube-studio/"):
        return "oauth_config_or_scope_mutation"

    explicit_publish_paths = (
        "/upload-private",
        "/schedule-public",
        "/approve-publish",
        "/external-publish",
        "/money-machine/run-publication",
    )
    if any(marker in normalized_path for marker in explicit_publish_paths):
        return "youtube_publish_or_upload"

    youtube_markers = (
        "/youtube",
        "youtube-",
        "youtube_",
        "/channel-ops",
        "/distribution",
    )
    write_markers = (
        "upload",
        "publish",
        "promote",
        "thumbnail",
        "caption",
        "comment",
        "playlist",
        "subscription",
        "rating",
        "delete",
        "metadata",
        "approve",
        "disconnect",
        "runtime-config",
        "client-secret",
        "oauth",
    )
    if any(marker in normalized_path for marker in youtube_markers) and any(
        marker in normalized_path for marker in write_markers
    ):
        return "youtube_http_write"
    return None
