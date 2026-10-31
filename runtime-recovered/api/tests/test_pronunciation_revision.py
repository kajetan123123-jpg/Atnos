from __future__ import annotations

import pytest

from atnos.production.pronunciation_revision import alias_alignment


def timed(text):
    return {"characters":list(text),"character_start_times_seconds":[n*.02 for n in range(len(text))],
        "character_end_times_seconds":[(n+1)*.02 for n in range(len(text))]}


def test_currency_expansion_preserves_captions_and_does_not_invent_word_times():
    segment = {"id":"s05","text":"Five hundred zlotys monthly."}
    text = "Five hundred Polish zloty monthly."
    timings,words,spoken = alias_alignment(segment,timed(text),len(text)*.02+.1)
    assert spoken == text
    assert " ".join(w["text"] for w in words) == segment["text"]
    assert words[2]["start_sec"] == text.index("Polish")*.02
    assert words[2]["end_sec"] == (text.index("zloty")+len("zloty"))*.02
    assert timings[0]["text"] == segment["text"]
    with pytest.raises(ValueError,match="does not match"):
        alias_alignment(segment,timed("Five thousand Polish zloty monthly."),2)


def test_original_provider_alignment_is_supported_but_other_words_are_not_rewritten():
    segment = {"id":"s05","text":"Five hundred zlotys."}
    _,words,_ = alias_alignment(segment,timed(segment["text"]),2)
    assert words[-1]["text"] == "zlotys."
    with pytest.raises(ValueError,match="only supports"):
        alias_alignment({"id":"s05","text":"Five hundred dollars."},{},2)
