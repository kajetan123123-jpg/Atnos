from __future__ import annotations

from pathlib import Path


DEFAULT_ERROR_MARKERS = (
    "error",
    "failed",
    "invalid",
    "something went wrong",
    "blad",
    "błąd",
    "niepowodzenie",
)


def verify_url_contains(current_url: str, fragment: str) -> bool:
    return fragment.casefold() in (current_url or "").casefold()


def verify_text_visible(visible_text: str, text: str) -> bool:
    return text.casefold() in (visible_text or "").casefold()


def verify_download_exists(path: str | Path) -> bool:
    return Path(path).expanduser().exists()


def verify_no_error_message(visible_text: str, *, markers: tuple[str, ...] = DEFAULT_ERROR_MARKERS) -> bool:
    lowered = (visible_text or "").casefold()
    return not any(marker.casefold() in lowered for marker in markers)


def verify_prompt_pasted(observed_text: str, expected_text: str) -> bool:
    return expected_text.casefold() in (observed_text or "").casefold()
