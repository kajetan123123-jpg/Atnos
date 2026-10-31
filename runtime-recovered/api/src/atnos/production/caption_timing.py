"""Shared caption/subtitle timing helpers for ProjectManifest + Remotion.

Uses the existing captions stage (word_timestamps → caption_groups).
Does not invent a second caption generator.
"""

from __future__ import annotations

from typing import Any

from atnos.production.editorial_film import caption_groups
from atnos.production.project_manifest import CaptionWord, ProjectManifest, SceneManifest


def flatten_caption_words(
    *,
    captions: dict[str, Any] | None = None,
    voice: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Prefer approved captions.groups; fall back to voice.word_timestamps."""
    captions = captions if isinstance(captions, dict) else {}
    voice = voice if isinstance(voice, dict) else {}
    words: list[dict[str, Any]] = []
    for group in captions.get("groups") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                words.append(item)
    if words:
        return words
    for item in voice.get("word_timestamps") or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            words.append(item)
    return words


def words_for_scene(
    words: list[dict[str, Any]],
    *,
    scene_id: str,
    start_sec: float,
    end_sec: float,
    narration_segment_ids: list[str] | None = None,
) -> list[CaptionWord]:
    """Select word timings for one scene, including every explicitly merged segment."""
    segment_ids = set(narration_segment_ids or [scene_id])
    by_segment = [
        item
        for item in words
        if str(item.get("segment_id") or "") in segment_ids
    ]
    selected = by_segment
    if not selected:
        selected = [
            item
            for item in words
            if float(item.get("end_sec") or 0) > start_sec - 1e-3
            and float(item.get("start_sec") or 0) < end_sec + 1e-3
        ]
    output: list[CaptionWord] = []
    for item in selected:
        text = str(item.get("text") or "").strip()
        start = float(item.get("start_sec") or 0.0)
        end = float(item.get("end_sec") or 0.0)
        if not text or end <= start:
            continue
        output.append(
            CaptionWord(
                text=text,
                start_sec=round(start, 3),
                end_sec=round(end, 3),
                segment_id=str(item.get("segment_id") or scene_id),
            )
        )
    return output


def subtitle_text_from_words(words: list[CaptionWord] | list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in words:
        if isinstance(item, CaptionWord):
            text = item.text.strip()
        else:
            text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def normalize_spoken(text: str) -> str:
    return " ".join(str(text or "").split())


def validate_scene_caption_consistency(scene: SceneManifest, *, require_words: bool = False) -> None:
    """Fail closed when subtitle text/timings disagree with narration."""
    caption = normalize_spoken(scene.caption)
    narration = normalize_spoken(scene.narration)
    if not caption:
        raise ValueError(f"Scene {scene.scene_id}: caption/subtitle text is empty")
    if require_words and not scene.caption_words:
        raise ValueError(f"Scene {scene.scene_id}: caption_words missing (cannot prove timed subtitles)")
    if scene.caption_words:
        joined = normalize_spoken(subtitle_text_from_words(scene.caption_words))
        if joined != caption:
            raise ValueError(
                f"Scene {scene.scene_id}: caption text does not match caption_words "
                f"({joined!r} != {caption!r})"
            )
        if narration and joined != narration and narration not in joined and joined not in narration:
            # Allow chart/number adapters to append spoken backing values to narration.
            raise ValueError(
                f"Scene {scene.scene_id}: caption_words disagree with narration"
            )
        prev_end = scene.timing.start_sec - 0.05
        for index, word in enumerate(scene.caption_words):
            if word.end_sec <= word.start_sec:
                raise ValueError(f"Scene {scene.scene_id}: invalid word interval at {index}")
            if word.start_sec + 1e-3 < prev_end:
                raise ValueError(f"Scene {scene.scene_id}: overlapping/out-of-order word at {index}")
            if word.start_sec < scene.timing.start_sec - 0.05:
                raise ValueError(f"Scene {scene.scene_id}: word starts before scene")
            if word.end_sec > scene.timing.end_sec + 0.05:
                raise ValueError(f"Scene {scene.scene_id}: word ends after scene")
            prev_end = word.end_sec


def validate_manifest_captions(
    manifest: ProjectManifest,
    *,
    require_words: bool = False,
) -> None:
    for scene in manifest.scenes:
        validate_scene_caption_consistency(scene, require_words=require_words)


def caption_cues_from_words(
    words: list[CaptionWord] | list[dict[str, Any]],
    *,
    max_words: int = 3,
    max_characters: int = 32,
) -> list[dict[str, Any]]:
    """Readable mobile cues with real start/end from word timestamps."""
    raw = [
        {
            "text": w.text if isinstance(w, CaptionWord) else str(w.get("text") or ""),
            "start_sec": w.start_sec if isinstance(w, CaptionWord) else float(w["start_sec"]),
            "end_sec": w.end_sec if isinstance(w, CaptionWord) else float(w["end_sec"]),
            "segment_id": (
                w.segment_id
                if isinstance(w, CaptionWord)
                else str(w.get("segment_id") or "scene")
            ),
        }
        for w in words
        if (w.text if isinstance(w, CaptionWord) else str(w.get("text") or "")).strip()
    ]
    if not raw:
        return []
    groups = caption_groups(raw, max_words=max_words, max_characters=max_characters)
    return [
        {
            "text": " ".join(item["text"] for item in group),
            "start_sec": float(group[0]["start_sec"]),
            "end_sec": float(group[-1]["end_sec"]),
            "words": group,
        }
        for group in groups
    ]


def active_caption_text(cues: list[dict[str, Any]], time_sec: float) -> str:
    """Same half-open interval rule as editorial draw_captions."""
    for cue in cues:
        if float(cue["start_sec"]) <= time_sec < float(cue["end_sec"]):
            return str(cue["text"])
    return ""
