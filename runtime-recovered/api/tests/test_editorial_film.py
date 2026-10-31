import json
from pathlib import Path

import pytest

from atnos.production.editorial_film import balance, caption_groups, frame_ranges
from scripts.render_wealth_editorial import savings_coverage, validate_brief


def test_timeline_rounds_shared_boundaries_not_individual_durations():
    ranges = frame_ranges([{"start_sec": 0, "end_sec": 1.018}, {"start_sec": 1.018, "end_sec": 2.031},
                           {"start_sec": 2.031, "end_sec": 40.015}])
    assert sum(end - start for start, end in ranges) == round(40.015 * 30)
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))


def test_timeline_rejects_gaps():
    with pytest.raises(ValueError, match="gap"):
        frame_ranges([{"start_sec": 0, "end_sec": 1}, {"start_sec": 1.1, "end_sec": 2}])


def test_captions_preserve_all_spoken_words_and_scene_boundaries():
    words = [{"text": word, "start_sec": i, "end_sec": i + .9, "segment_id": "a" if i < 4 else "b"}
             for i, word in enumerate("Your money has time. Let it work for you.".split())]
    groups = caption_groups(words)
    assert [w for group in groups for w in group] == words
    assert all(len({w["segment_id"] for w in group}) == 1 for group in groups)


def test_reviewed_premise_and_decade_comparison_are_mathematically_consistent():
    value = lambda t: balance(10000, .07, t)
    assert value(40) == pytest.approx(149744.578392, abs=.0001)
    early, late = value(10) - value(0), value(40) - value(30)
    assert late / early == pytest.approx(7.612255, abs=.0001)
    brief = json.loads((Path(__file__).parents[1] / "config" / "wealth_blueprint_forty_year_edit.json").read_text())
    assert 95 <= validate_brief(brief) <= 130
    brief["premise"]["annual_rate"] = .20
    with pytest.raises(ValueError, match="premise"):
        validate_brief(brief)


def saving_brief():
    return json.loads((Path(__file__).parents[1] / "config" / "wealth_blueprint_save_first_edit.json").read_text())


def coverage_voice(brief, savings_duration=2, other_duration=2):
    timestamps, words, cursor = [], [], 0.0
    for scene in brief["scenes"]:
        duration = savings_duration if scene["kind"].startswith("save_") else other_duration
        timestamps.append({"segment_id": scene["id"], "start_sec": cursor, "end_sec": cursor + duration})
        words.append({"segment_id": scene["id"], "start_sec": cursor, "end_sec": cursor + duration - .1})
        cursor += duration
    return {"timestamps": timestamps, "word_timestamps": words, "timing_source": "provider_character_alignment"}, cursor


def test_savings_brief_has_an_honest_affordable_plan_and_enough_instruction():
    brief = saving_brief()
    assert 95 <= validate_brief(brief) <= 130
    plan = brief["saving_plan"]
    assert plan["weekly_amount"] * plan["weeks"] == plan["target"] == brief["premise"]["principal"]
    brief["saving_plan"]["weeks"] = 52
    with pytest.raises(ValueError, match="hundred-week"):
        validate_brief(brief)


@pytest.mark.parametrize("share", [0.1, float("nan"), 1.1])
def test_savings_requirement_cannot_be_weakened(share):
    brief = saving_brief()
    brief["saving_plan"]["minimum_time_share"] = share
    with pytest.raises(ValueError, match="one third"):
        validate_brief(brief)


def test_savings_coverage_uses_measured_time_not_only_word_counts():
    brief = saving_brief()
    voice, duration = coverage_voice(brief)
    evidence = savings_coverage(brief, voice, duration)
    assert evidence["status"] == "passed"
    assert evidence["time_share"] == pytest.approx(5 / 11)
    voice, duration = coverage_voice(brief, savings_duration=.5, other_duration=3)
    assert savings_coverage(brief, voice, duration)["status"] == "failed"


@pytest.mark.parametrize("defect", ["ids", "gap", "duration", "estimated"])
def test_savings_coverage_rejects_invalid_evidence(defect):
    brief = saving_brief()
    voice, duration = coverage_voice(brief)
    if defect == "ids":
        voice["timestamps"][0]["segment_id"] = "different_script"
    elif defect == "gap":
        voice["timestamps"][1]["start_sec"] += .1
    elif defect == "duration":
        duration += 10
    else:
        voice["timing_source"] = "estimated"
    with pytest.raises(ValueError):
        savings_coverage(brief, voice, duration)


def test_savings_scenes_render_at_start_middle_and_end():
    from atnos.production.editorial_film import draw_scene

    brief = saving_brief()
    for index, scene in enumerate(brief["scenes"]):
        for progress in (0, .15, 1):
            frame = draw_scene(brief, scene, progress, index)
            assert frame.size == (1080, 1920)
            assert frame.mode == "RGB"
