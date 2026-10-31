from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import re
import unicodedata


ChannelOpsIntentKind = Literal[
    "prepare_shorts",
    "plan_channel",
    "private_upload",
    "approval_queue",
    "channel_analytics",
]


@dataclass(frozen=True, slots=True)
class ChannelOpsIntent:
    kind: ChannelOpsIntentKind
    channel_hint: str | None = None
    count: int | None = None


def detect_channel_ops_intent(text: str) -> ChannelOpsIntent | None:
    normalized = _normalize(text)
    if not normalized:
        return None

    channel_hint = _extract_channel_hint(normalized)
    count = _extract_count(normalized)
    has_channel_context = channel_hint is not None or any(
        marker in normalized
        for marker in (
            "kanal",
            "kana",
            "kanale",
            "kanalu",
            "channel",
            "approval",
            "upload queue",
            "kolejke upload",
            "kolejka upload",
        )
    )
    has_short_context = any(
        marker in normalized
        for marker in ("short", "shorty", "shorta", "shortow", "rolka", "rolki", "reels")
    )

    if any(marker in normalized for marker in ("kolejke approval", "kolejka approval", "upload approval")):
        return ChannelOpsIntent(kind="approval_queue", channel_hint=channel_hint, count=count)

    if "approval" in normalized and any(marker in normalized for marker in ("pokaz", "lista", "kolejka")):
        return ChannelOpsIntent(kind="approval_queue", channel_hint=channel_hint, count=count)

    if any(marker in normalized for marker in ("co dziala najlepiej", "analytics kanalu", "analityka kanalu", "wyniki kanalu")):
        return ChannelOpsIntent(kind="channel_analytics", channel_hint=channel_hint, count=count)

    if any(marker in normalized for marker in ("wrzuc prywatnie", "upload prywatnie", "private upload")) and has_channel_context:
        return ChannelOpsIntent(kind="private_upload", channel_hint=channel_hint, count=count)

    if any(marker in normalized for marker in ("zaplanuj kanal", "plan kanalu", "daily plan")):
        return ChannelOpsIntent(kind="plan_channel", channel_hint=channel_hint, count=count)

    if has_short_context and has_channel_context and any(
        marker in normalized
        for marker in ("przygotuj", "zaplanuj", "zrob", "stworz", "wygeneruj", "na jutro")
    ):
        return ChannelOpsIntent(kind="prepare_shorts", channel_hint=channel_hint, count=count or 1)

    return None


def _extract_count(normalized: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\s+(?:short|shorty|shorta|shortow|rolk|reel)", normalized)
    if not match:
        return None
    return max(1, min(20, int(match.group(1))))


def _extract_channel_hint(normalized: str) -> str | None:
    match = re.search(r"\b(?:kanal|kana|kanale|kanalu|channel)\s+([a-z0-9][a-z0-9_-]*)", normalized)
    if match:
        return match.group(1).strip()
    return None


def _normalize(text: str) -> str:
    value = _replace_polish_letters(text or "")
    value = unicodedata.normalize("NFKD", value)
    ascii_only = value.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.casefold().split())


def _replace_polish_letters(value: str) -> str:
    return value.translate(
        str.maketrans(
            {
                "ą": "a",
                "ć": "c",
                "ę": "e",
                "ł": "l",
                "ń": "n",
                "ó": "o",
                "ś": "s",
                "ź": "z",
                "ż": "z",
                "Ą": "A",
                "Ć": "C",
                "Ę": "E",
                "Ł": "L",
                "Ń": "N",
                "Ó": "O",
                "Ś": "S",
                "Ź": "Z",
                "Ż": "Z",
            }
        )
    )
