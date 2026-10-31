import pytest

from atnos.production.narration_alignment import align_narration


def _alignment(text):
    return {"characters": list(text), "character_start_times_seconds": [i / 10 for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) / 10 for i in range(len(text))]}


def test_segment_and_word_times_come_from_audio_not_word_counts():
    segments = [{"id": "a", "text": "Your money."}, {"id": "b", "text": "Forty years."}]
    alignment = _alignment("Your money. Forty years.")
    timestamps, words = align_narration(segments, alignment, duration=2.5)
    assert timestamps[1]["start_sec"] == 1.2
    assert timestamps[0]["end_sec"] == timestamps[1]["start_sec"]
    assert timestamps[-1]["end_sec"] == 2.5
    assert [w["text"] for w in words] == ["Your", "money.", "Forty", "years."]
    assert words[2]["segment_id"] == "b"


@pytest.mark.parametrize("kind", ["mismatch", "missing", "reverse", "nan", "outside"])
def test_bad_alignment_is_rejected(kind):
    alignment = _alignment("Your money.")
    if kind == "mismatch":
        alignment["characters"][0] = "A"
    elif kind == "missing":
        alignment["character_end_times_seconds"].pop()
    elif kind == "reverse":
        alignment["character_start_times_seconds"][4] = -1
    elif kind == "nan":
        alignment["character_end_times_seconds"][4] = float("nan")
    else:
        alignment["character_end_times_seconds"][-1] = 10
    with pytest.raises(ValueError):
        align_narration([{"id": "a", "text": "Your money."}], alignment, duration=1.2)
