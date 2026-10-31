from scripts.render_wealth_compound_golden import (
    BROLL_SOURCES,
    DESCRIPTION,
    MATH_CARD_COPY,
    THUMBNAIL_HORIZON,
    WEALTH_VOICE_ID,
    YOUTUBE_DESCRIPTION,
    _frame_aligned_duration,
    _relocate_paths,
    _assert_voice_matches_script,
    script_payload,
)


def test_golden_script_is_personal_and_tts_safe() -> None:
    script = script_payload()
    spoken = " ".join(segment["text"] for segment in script["segments"])

    assert len(script["segments"]) == 15
    assert 115 <= script["total_words"] <= 135
    assert spoken.lower().count("you") >= 8
    assert "$" not in spoken
    assert "%" not in spoken
    assert "seven hundred forty-nine dollars" in spoken.lower()
    assert "one hundred forty-nine thousand seven hundred forty-five dollars" in spoken.lower()
    assert "second year" in spoken.lower()
    assert "year two" not in spoken.lower()
    assert any("$" in segment["caption_text"] for segment in script["segments"])
    visible_copy = " ".join(segment["caption_text"] for segment in script["segments"])
    assert "SECOND YEAR" in visible_copy
    assert "YEAR 2" not in visible_copy
    assert "YEAR TWO" not in visible_copy
    assert "second year" in DESCRIPTION.lower()
    assert "second year" in YOUTUBE_DESCRIPTION.lower()
    assert "forty years" in spoken.lower()
    assert "thirty years" not in spoken.lower()
    assert THUMBNAIL_HORIZON == "40 YEARS"
    assert script["language_reference"]["sample_count"] == 40


def test_golden_uses_a_dedicated_voice_and_traceable_broll() -> None:
    assert WEALTH_VOICE_ID
    assert len(BROLL_SOURCES) >= 7
    assert len({source["id"] for source in BROLL_SOURCES}) == len(BROLL_SOURCES)
    assert all(source["page"].startswith("https://www.pexels.com/video/") for source in BROLL_SOURCES)
    assert all(source["credit"].endswith("/ Pexels") for source in BROLL_SOURCES)


def test_math_cards_address_the_viewer_and_show_their_result() -> None:
    copy = " ".join(part for card in MATH_CARD_COPY.values() for part in card)

    assert set(MATH_CARD_COPY) == {3, 7, 10}
    assert "YOU EARN SEVEN HUNDRED" in copy
    assert "YOUR NEW BASE → $10,700" in copy
    assert "YOUR SECOND YEAR" in copy
    assert "$700 → $749" in copy


def test_render_duration_is_aligned_to_complete_thirty_fps_frames() -> None:
    duration = _frame_aligned_duration(39.381043)

    assert duration == 39.36666666666667
    assert duration * 30 == int(duration * 30)


def test_reused_voice_paths_are_relocated_to_new_artifact_root(tmp_path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    payload = {"output_file": str(old_root / "audio" / "narration.wav"), "timestamps": [{"start_sec": 0.0}]}

    relocated = _relocate_paths(payload, old_root=old_root, new_root=new_root)

    assert relocated["output_file"] == str(new_root / "audio" / "narration.wav")
    assert relocated["timestamps"] == payload["timestamps"]


def test_cached_voice_must_match_current_narration():
    import pytest
    script = {"segments": [{"id": "s1", "text": "After forty years."}]}
    _assert_voice_matches_script({"timestamps": [{"segment_id": "s1", "text": "After forty years."}]}, script)
    with pytest.raises(ValueError, match="does not match"):
        _assert_voice_matches_script({"timestamps": [{"segment_id": "s1", "text": "After thirty years."}]}, script)
