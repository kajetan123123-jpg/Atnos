from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from atnos.production.steps import script_writer, visual_planner


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_active_reference_learning_session.py"
SPEC = importlib.util.spec_from_file_location("active_reference_learning", SCRIPT_PATH)
assert SPEC and SPEC.loader
learning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learning)


def _reference(name: str) -> dict[str, str]:
    return {"name": name, "url": f"https://www.youtube.com/@{name}"}


def _videos(prefix: str, count: int) -> list[dict[str, object]]:
    return [
        {"id": f"{prefix}-{index}", "view_count": count - index}
        for index in range(count)
    ]


def test_wealth_profile_loads_only_its_configured_reference_channels(tmp_path: Path) -> None:
    references = learning._load_reference_urls(SimpleNamespace(data_dir=tmp_path), profile_key="wealth_blueprint")

    assert len(references) == 5
    assert {item["url"] for item in references} == {
        "https://www.youtube.com/@humphrey",
        "https://www.youtube.com/@GrahamStephan",
        "https://www.youtube.com/@AndreiJikh",
        "https://www.youtube.com/@AliAbdaal",
        "https://www.youtube.com/@ThePlainBagel",
    }


def test_balanced_selection_fills_channel_shortage_to_exact_batch_size() -> None:
    available = [
        (_reference("a"), _videos("a", 12)),
        (_reference("b"), _videos("b", 12)),
        (_reference("c"), _videos("c", 8)),
        (_reference("d"), _videos("d", 8)),
        (_reference("e"), _videos("e", 4)),
    ]

    selected = learning._select_balanced_candidates(available, per_channel=8, target_total=40)

    assert len(selected) == 40
    assert len({item["id"] for item in selected}) == 40
    assert sum(item["configured_channel"] == "e" for item in selected) == 4
    assert sum(item["configured_channel"] == "a" for item in selected) == 10
    assert sum(item["configured_channel"] == "b" for item in selected) == 10


def test_balanced_selection_does_not_fake_missing_samples() -> None:
    available = [
        (_reference("a"), _videos("a", 8)),
        (_reference("b"), _videos("b", 8)),
    ]

    selected = learning._select_balanced_candidates(available, per_channel=8, target_total=40)

    assert len(selected) == 16


def test_vtt_parser_removes_timing_tags_and_consecutive_rollup_duplicates() -> None:
    payload = """WEBVTT

00:00:00.000 --> 00:00:01.000
<c>Most people save what is left.</c>

00:00:01.000 --> 00:00:02.000
But that order makes saving harder.

00:00:02.000 --> 00:00:03.000
But that order makes saving harder.
"""

    transcript = learning._parse_vtt(payload)

    assert "-->" not in transcript
    assert "<c>" not in transcript
    assert transcript.count("But that order") == 1


def test_vtt_parser_removes_youtube_rolling_caption_overlap() -> None:
    payload = """WEBVTT

00:00:00.000 --> 00:00:01.750
I'm<00:00:00.280><c> looking</c><00:00:00.560><c> at</c><00:00:00.680><c> buying</c>

00:00:01.750 --> 00:00:01.760
I'm looking at buying

00:00:01.760 --> 00:00:03.270
I'm looking at buying
a<00:00:01.880><c> house</c><00:00:02.160><c> with</c><00:00:02.400><c> cash.</c>
"""

    transcript = learning._parse_vtt(payload)

    assert transcript == "I'm looking at buying a house with cash."


def test_caption_duration_reads_latest_vtt_end_time(tmp_path: Path) -> None:
    (tmp_path / "sample.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:12.750\nWords\n",
        encoding="utf-8",
    )

    assert learning._caption_duration(tmp_path, "sample") == 12.75


def test_language_profile_contains_measurements_but_not_source_copy() -> None:
    source_sentence = "A creator-specific sentence that must never enter the generation profile."
    transcripts = [
        {
            "video_id": "abc",
            "channel": "Reference",
            "status": "transcribed",
            "source": "youtube_captions",
            "transcript": source_sentence,
            "metrics": learning._language_metrics(
                "Most people wait. But you can change the order, because timing matters.",
                duration_sec=8.0,
            ),
        }
    ]

    profile = learning._build_script_language_profile("wealth_blueprint", transcripts)
    serialized = json.dumps(profile)

    assert profile["sample_count"] == 1
    assert profile["metrics"]["transition_types"]["but"] == 1
    assert profile["metrics"]["median_transitions_per_100_words"] > 0
    assert "median_fragment_sentence_ratio" in profile["metrics"]
    assert source_sentence not in serialized
    assert "Never copy" in profile["source_policy"]


def test_transcript_collection_uses_captions_without_downloaded_video(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        learning,
        "_download_caption_text",
        lambda *_args, **_kwargs: (
            "You think saving comes last. But that order is the problem, because every bill gets first choice. "
            "Move savings first, then decide what the rest of your paycheck can safely cover this week."
        ),
    )

    result = learning._collect_transcript(
        SimpleNamespace(),
        {"id": "caption-only", "configured_channel": "Reference", "duration": 12},
        None,
        tmp_path,
    )

    assert result["status"] == "transcribed"
    assert result["source"] == "youtube_captions"
    assert Path(result["transcript_path"]).exists()
    assert result["metrics"]["estimated_wpm"] == 165.0


def test_transcript_collection_reports_unavailable_without_captions_or_video(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(learning, "_download_caption_text", lambda *_args, **_kwargs: "")

    result = learning._collect_transcript(
        SimpleNamespace(),
        {"id": "missing", "configured_channel": "Reference"},
        None,
        tmp_path,
    )

    assert result["status"] == "unavailable"
    assert "No readable English captions" in result["reason"]


def test_script_prompt_uses_aggregate_reference_metrics_only() -> None:
    block = script_writer._reference_language_prompt_block(
        {
            "sample_count": 4,
            "metrics": {
                "median_sentence_words": 9.0,
                "median_estimated_wpm": 151.0,
                "opening_patterns": {"direct_address": 3},
                "transition_types": {"but": 4, "because": 2},
            },
            "generation_rules": ["Vary sentence length for spoken rhythm."],
        }
    )

    assert "Measured transcript samples: 4" in block
    assert "151.0 WPM" in block
    assert "never source copy" in block.casefold()
    assert "signature phrase" in block


def test_editorial_profile_packages_language_and_motion_without_source_copy() -> None:
    source_sentence = "A protected creator sentence must not enter the derived profile."
    language = learning._build_script_language_profile(
        "wealth_blueprint",
        [
            {
                "video_id": "abc",
                "channel": "Reference",
                "status": "transcribed",
                "source": "youtube_captions",
                "transcript": source_sentence,
                "metrics": learning._language_metrics(
                    "Your paycheck lands. But one quiet purchase changes the week because the next choice feels smaller.",
                    duration_sec=8.0,
                ),
            }
        ],
    )
    editorial = learning._build_editorial_profile(
        "wealth_blueprint",
        {
            "sample_count": 3,
            "median_first_cut_sec": 0.5,
            "median_avg_cut_interval_sec": 1.5,
            "median_flash_events_per_10s": 0.0,
            "production_rules_for_atnos": ["Use one meaningful visual change at a time."],
        },
        language,
        [{"hook_type": "specific_claim"}],
    )

    serialized = json.dumps(editorial)
    assert editorial["visual_sample_count"] == 3
    assert editorial["motion_metrics"]["median_first_cut_sec"] == 0.5
    assert any("white flashes" in rule for rule in editorial["motion_rules"])
    assert source_sentence not in serialized


def test_visual_prompt_uses_motion_contract_and_rejects_source_copy() -> None:
    block = visual_planner._reference_motion_prompt_block(
        {"sample_count": 3, "median_first_cut_sec": 0.5, "rules": []},
        {
            "visual_sample_count": 3,
            "motion_metrics": {"median_first_cut_sec": 0.5, "median_avg_cut_interval_sec": 1.5},
            "motion_rules": ["Use one semantic motion per scene."],
        },
    )

    assert "Measured visual samples: 3" in block
    assert "one semantic motion" in block
    assert "do not reuse source frames" in block


def test_visual_prompt_omits_unverified_empty_motion_profile() -> None:
    assert visual_planner._reference_motion_prompt_block({}, {}) == ""


def test_human_copy_gate_rejects_robotic_finance_explainer() -> None:
    script = {
        "segments": [
            {"text": "It is important to understand your paycheck, receipt, wallet, and bank app before investing."},
            {"text": "This demonstrates that every spending decision can influence your long term financial outcome."},
            {"text": "The key takeaway is to understand the mechanism and evaluate the available information carefully."},
            {"text": "It is worth noting that this behavior can affect the balance inside your account."},
            {"text": "In conclusion, understanding this is crucial for every responsible financial decision you make."},
            {"text": "Review the information and consider the implications for your personal financial situation."},
        ]
    }

    markers = script_writer._script_quality_markers(script, SimpleNamespace(key="wealth_blueprint"))

    assert "robotic_explainer_phrase" in markers


def test_human_copy_gate_accepts_spoken_connectors_and_varied_rhythm() -> None:
    script = {
        "segments": [
            {"text": "Your paycheck lands."},
            {"text": "But the first quiet purchase changes what happens next."},
            {"text": "A receipt looks harmless, so you tap the card again."},
            {"text": "By Friday, your wallet is lighter because convenience made every choice feel separate."},
            {"text": "Instead, move one decision out of autopilot before the week begins."},
            {"text": "Now check the bank app and choose where that money goes."},
        ]
    }

    markers = script_writer._script_quality_markers(script, SimpleNamespace(key="wealth_blueprint"))

    assert "robotic_explainer_phrase" not in markers
    assert "finance_copy_missing_spoken_connectors" not in markers
    assert "finance_copy_monotone_sentence_rhythm" not in markers


def test_fast_hook_does_not_end_on_incomplete_phrase() -> None:
    fitted = script_writer._fit_fast_hook(
        "Your money is gone. Here's how to stop it.",
        SimpleNamespace(key="wealth_blueprint"),
    )

    assert fitted == "Your money is gone."


def test_fallback_opening_replaces_repeated_hook() -> None:
    opening = script_writer._non_repeating_opening_line(
        {
            "text": "Your paycheck disappears by Friday",
            "opening_line": "By Friday, your paycheck has already disappeared.",
        },
        SimpleNamespace(key="wealth_blueprint"),
    )

    assert opening == "You feel paid, so every small yes suddenly feels harmless."


def test_wealth_fallback_titles_do_not_inject_unrelated_rule_of_72() -> None:
    context = SimpleNamespace(
        topic=SimpleNamespace(
            title="Why Your Paycheck Disappears by Friday",
            description="Small convenience spending through the week.",
        )
    )

    titles = script_writer._profile_title_options(context, SimpleNamespace(key="wealth_blueprint"))

    assert all("72" not in title for title in titles)
    assert any("Paycheck" in title or "Spending" in title for title in titles)
