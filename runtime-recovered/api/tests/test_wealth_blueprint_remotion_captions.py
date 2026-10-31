"""Captions integration on the Remotion ProjectManifest path."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from atnos.production.caption_timing import (
    active_caption_text,
    caption_cues_from_words,
    validate_manifest_captions,
)
from atnos.production.channels.wealth_blueprint.manifest_adapters import (
    order_artifacts_to_project_manifest,
    scene_plan_to_project_manifest,
)
from atnos.production.project_manifest import CaptionWord


def _still(tmp_path: Path) -> Path:
    path = tmp_path / "still.png"
    Image.new("RGB", (1080, 1920), (20, 28, 36)).save(path)
    return path


def _words_for(segment_id: str, text: str, start: float) -> list[dict]:
    words = []
    cursor = start
    for token in text.split():
        end = cursor + 0.28
        words.append(
            {
                "text": token,
                "segment_id": segment_id,
                "start_sec": round(cursor, 3),
                "end_sec": round(end, 3),
            }
        )
        cursor = end
    return words


def test_headline_different_from_narration_does_not_change_subtitle(tmp_path: Path):
    still = _still(tmp_path)
    narration = "Your paycheck feels bigger this year."
    words = _words_for("s01", narration, 0.0)
    script = {"segments": [{"id": "s01", "text": narration}]}
    voice = {
        "timestamps": [{"segment_id": "s01", "text": narration, "start_sec": 0.0, "end_sec": 2.0}],
        "duration_seconds": 2.0,
        "word_timestamps": words,
    }
    visuals = {"scenes": [{"layout_id": "fullscreen_statement", "headline": "Raise summary"}]}
    captions = {"groups": [words]}
    manifest = order_artifacts_to_project_manifest(
        job_id="caption-headline-split",
        topic_title="Raise",
        script=script,
        voice=voice,
        visuals=visuals,
        selected_image=still,
        captions=captions,
        require_caption_words=True,
    )
    scene = manifest.scenes[0]
    assert scene.headline == "Raise summary"
    assert scene.caption == narration
    assert scene.caption != scene.headline
    assert " ".join(w.text for w in scene.caption_words) == narration


def test_required_words_keep_correct_intervals(tmp_path: Path):
    still = _still(tmp_path)
    text = "Save first then spend without guilt."
    words = _words_for("s01", text, 0.1)
    script = {"segments": [{"id": "s01", "text": text}]}
    voice = {
        "timestamps": [{"segment_id": "s01", "text": text, "start_sec": 0.0, "end_sec": 2.5}],
        "duration_seconds": 2.5,
        "word_timestamps": words,
    }
    captions = {"groups": [words]}
    manifest = order_artifacts_to_project_manifest(
        job_id="caption-intervals",
        topic_title="Save",
        script=script,
        voice=voice,
        visuals={"scenes": [{"layout_id": "fullscreen_statement", "headline": "Save"}]},
        selected_image=still,
        captions=captions,
        require_caption_words=True,
    )
    scene = manifest.scenes[0]
    assert len(scene.caption_words) == len(words)
    for expected, actual in zip(words, scene.caption_words, strict=True):
        assert actual.text == expected["text"]
        assert abs(actual.start_sec - expected["start_sec"]) < 1e-6
        assert abs(actual.end_sec - expected["end_sec"]) < 1e-6
        assert actual.end_sec > actual.start_sec


def test_first_and_last_words_do_not_disappear():
    words = [
        CaptionWord(text="First", start_sec=0.05, end_sec=0.4, segment_id="s01"),
        CaptionWord(text="middle", start_sec=0.4, end_sec=0.9, segment_id="s01"),
        CaptionWord(text="last.", start_sec=0.9, end_sec=1.4, segment_id="s01"),
    ]
    cues = caption_cues_from_words(words, max_words=3, max_characters=32)
    assert active_caption_text(cues, 0.1).startswith("First")
    assert "last." in active_caption_text(cues, 1.2)
    assert active_caption_text(cues, 1.4) == ""


def test_pauses_and_scene_boundaries(tmp_path: Path):
    still = _still(tmp_path)
    a = "Keep more of your raise."
    b = "Wealth is what you keep."
    words_a = _words_for("s01", a, 0.0)
    # Pause between scenes: gap before s02 words
    words_b = _words_for("s02", b, 2.4)
    script = {
        "segments": [
            {"id": "s01", "text": a},
            {"id": "s02", "text": b},
        ]
    }
    voice = {
        "timestamps": [
            {"segment_id": "s01", "text": a, "start_sec": 0.0, "end_sec": 2.0},
            {"segment_id": "s02", "text": b, "start_sec": 2.2, "end_sec": 4.2},
        ],
        "duration_seconds": 4.2,
        "word_timestamps": words_a + words_b,
    }
    captions = {"groups": [words_a, words_b]}
    manifest = order_artifacts_to_project_manifest(
        job_id="caption-boundaries",
        topic_title="Boundaries",
        script=script,
        voice=voice,
        visuals={
            "scenes": [
                {"layout_id": "fullscreen_statement", "headline": "Raise"},
                {"layout_id": "fullscreen_statement", "headline": "Keep"},
            ]
        },
        selected_image=still,
        captions=captions,
        require_caption_words=True,
    )
    assert manifest.scenes[0].caption == a
    assert manifest.scenes[1].caption == b
    assert all(w.segment_id == "s01" for w in manifest.scenes[0].caption_words)
    assert all(w.segment_id == "s02" for w in manifest.scenes[1].caption_words)
    cues = caption_cues_from_words(manifest.scenes[0].caption_words)
    assert active_caption_text(cues, 2.1) == ""  # pause / past scene words


def test_scene_plan_adapter_keeps_headline_out_of_subtitles(tmp_path: Path):
    still = _still(tmp_path)
    narration = "Your paycheck feels bigger this year."
    words = _words_for("s01", narration, 0.0) + _words_for(
        "s02", "Save first then spend.", 3.0
    ) + _words_for("s03", "Invest consistently over time.", 6.0)
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Raise summary",
                "narration_excerpt": narration,
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 6.0,
                "layout_id": "big_number",
                "visual_focus": "number",
                "on_screen_text": "Save first",
                "narration_excerpt": "Save first then spend.",
                "number_value": "10%",
            },
            {
                "scene_id": "s03",
                "start_sec": 6.0,
                "end_sec": 9.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Invest",
                "narration_excerpt": "Invest consistently over time.",
            },
        ]
    }
    manifest = scene_plan_to_project_manifest(
        job_id="caption-scene-plan",
        topic="Raise",
        script=narration,
        plan=plan,
        selected_image=still,
        duration_hint=9.0,
        captions={"groups": [words]},
        voice={"word_timestamps": words, "duration_seconds": 9.0},
        require_caption_words=True,
    )
    assert manifest.scenes[0].headline == "Raise summary"
    assert manifest.scenes[0].caption == narration
    validate_manifest_captions(manifest, require_words=True)


def test_require_words_fails_closed_without_evidence(tmp_path: Path):
    still = _still(tmp_path)
    script = {"segments": [{"id": "s01", "text": "Hello world."}]}
    voice = {
        "timestamps": [{"segment_id": "s01", "text": "Hello world.", "start_sec": 0.0, "end_sec": 1.5}],
        "duration_seconds": 1.5,
        "word_timestamps": [],
    }
    with pytest.raises(ValueError, match="caption_words missing"):
        order_artifacts_to_project_manifest(
            job_id="caption-missing",
            topic_title="x",
            script=script,
            voice=voice,
            visuals={"scenes": [{"layout_id": "fullscreen_statement", "headline": "Hi"}]},
            selected_image=still,
            captions={"groups": []},
            require_caption_words=True,
        )
