from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production.channel_profile import FINANCE_PROFILE, WEALTH_BLUEPRINT_PROFILE, resolve_channel_profile
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import script_writer, visual_planner


def test_compact_headline_drops_a_trailing_connector() -> None:
    assert visual_planner._compact_headline("Save this and test it today") == "SAVE THIS AND TEST IT"


def test_compact_headline_preserves_a_complete_three_word_headline() -> None:
    assert visual_planner._compact_headline("Year two starts from the full balance") == "YEAR TWO STARTS"


def test_normalize_scene_preserves_explicit_segment_type() -> None:
    scene = visual_planner._normalize_scene(
        0,
        {
            "scene_id": 4,
            "type": "hook",
            "script_lines": [0],
            "visual_prompt": "A clock beside a growing stack of coins.",
        },
    )

    assert scene["segment_type"] == "hook"


def test_normalize_scene_defaults_missing_segment_type_to_body() -> None:
    scene = visual_planner._normalize_scene(
        0,
        {"scene_id": 1, "visual_prompt": "A wallet beside a savings jar."},
    )

    assert scene["segment_type"] == "body"


def test_normalize_scene_keeps_complete_short_overlay_as_headline() -> None:
    scene = visual_planner._normalize_scene(
        0,
        {
            "headline": "RAISE DOESN'T MEAN",
            "overlay_text": "RAISE DOESN'T MEAN WEALTH",
            "script_lines": [0],
        },
    )

    assert scene["headline"] == "RAISE DOESN'T MEAN WEALTH"


def test_visual_prompt_limit_keeps_sentence_or_word_boundary() -> None:
    complete_sentences = "Clear money transfer. " * 12
    truncated = visual_planner._truncate_visual_prompt(complete_sentences, 90)
    assert len(truncated) <= 90
    assert truncated.endswith(".")

    unpunctuated = "visible action " * 12
    truncated = visual_planner._truncate_visual_prompt(unpunctuated, 53)
    assert len(truncated) <= 53
    assert truncated.endswith("action") or truncated.endswith("visible")


def test_wealth_blueprint_fallback_prompt_uses_illustration_style() -> None:
    prompt = visual_planner._compose_visual_prompt(
        WEALTH_BLUEPRINT_PROFILE,
        "Where a Pay Raise Goes",
        "A new expense absorbs the raise.",
        "a hand moving coins toward a new bill envelope",
    ).casefold()
    assert "hand-drawn" in prompt
    assert "charcoal navy" in prompt
    assert "cream paper" in prompt
    assert "warm gold" in prompt
    assert "documentary scene" not in prompt


def test_short_script_does_not_create_scenes_for_nonexistent_lines(tmp_path: Path) -> None:
    settings = SimpleNamespace(shorts_visual_min_real_clips=6, shorts_visual_max_generated_scenes=8)
    segments = [{"id": f"s{i}", "type": "body", "text": f"Finance beat {i}."} for i in range(4)]
    context = ProductionContext(
        job_id="short-script-boundary",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="short-script-boundary",
            account_id="yt-wealth-blueprint",
            title="Where a Pay Raise Goes",
            description="A short example",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={"script": {"segments": segments}},
    )

    scenes = visual_planner._ensure_minimum_scenes(
        settings,
        context,
        [
            {"scene_id": 0, "script_lines": [0], "headline": "INCOME ARRIVES"},
            {"scene_id": 1, "script_lines": [8], "headline": "INVALID"},
        ],
        profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert len(scenes) == 4
    assert {line for scene in scenes for line in scene["script_lines"]} == {0, 1, 2, 3}
    assert [scene["script_lines"] for scene in scenes] == [[0], [1], [2], [3]]


def test_named_wealth_blueprint_account_keeps_its_visual_profile(tmp_path: Path) -> None:
    topic = ApprovedTopic(
        id="raise-profile",
        account_id="yt-wealth-blueprint",
        title="Where a Pay Raise Goes",
        description="A short example",
        total_score=80,
        content_profile="finance",
    )
    context = ProductionContext(
        job_id="raise-profile",
        account_id="yt-wealth-blueprint",
        topic=topic,
        artifact_dir=tmp_path,
    )
    assert resolve_channel_profile(context) is WEALTH_BLUEPRINT_PROFILE
    context.account_id = "yt-generic-finance"
    assert resolve_channel_profile(context) is FINANCE_PROFILE


def test_invalid_scene_repair_preserves_spoken_order(tmp_path: Path) -> None:
    settings = SimpleNamespace(shorts_visual_min_real_clips=4, shorts_visual_max_generated_scenes=4)
    context = ProductionContext(
        job_id="scene-order",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="scene-order",
            account_id="yt-wealth-blueprint",
            title="Where a Pay Raise Goes",
            description="A short example",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={"script": {"segments": [{"type": "body", "text": f"Beat {index}"} for index in range(4)]}},
    )
    scenes = visual_planner._ensure_minimum_scenes(
        settings,
        context,
        [
            {"scene_id": 0, "script_lines": [0]},
            {"scene_id": 1, "script_lines": [1]},
            {"scene_id": 2, "script_lines": [8]},
            {"scene_id": 3, "script_lines": [3]},
        ],
        profile=WEALTH_BLUEPRINT_PROFILE,
    )
    assert [scene["script_lines"] for scene in scenes] == [[0], [1], [2], [3]]


def test_raise_allocation_scenes_follow_the_narrated_money_direction(tmp_path: Path) -> None:
    lines = [
        "A new recurring expense can absorb the extra cash each month.",
        "Choose a specific amount for a savings goal before adding a new bill.",
        "What remains is available for everyday choices.",
    ]
    context = ProductionContext(
        job_id="money-direction",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="money-direction",
            account_id="yt-wealth-blueprint",
            title="Where a Pay Raise Goes",
            description="A non-numeric allocation example",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={"script": {"segments": [{"type": "body", "text": line} for line in lines]}},
    )
    scenes = visual_planner._strengthen_scene_prompts(
        context,
        WEALTH_BLUEPRINT_PROFILE,
        [
            {"scene_id": index, "script_lines": [index], "visual_subject": "coins and savings jar", "visual_prompt": "coins going into savings jar"}
            for index in range(len(lines))
        ],
    )

    assert "recurring-bill envelope" in scenes[0]["visual_prompt"]
    assert "stays untouched" in scenes[0]["visual_prompt"]
    assert "clear goal jar" in scenes[1]["visual_prompt"]
    assert "remaining coins" in scenes[2]["visual_prompt"]


def test_compound_interest_early_start_seed_uses_simple_distinct_timeline() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "Starting earlier gives the process more periods to repeat.",
        6,
    ).casefold()

    assert "mechanical timing rings" in seed
    assert "growth stages" in seed
    assert "transparent value blocks" in seed
    assert "hourglass" not in seed
    assert "savings bowl" not in seed


def test_compound_interest_consistency_seed_avoids_ambiguous_coin_tower() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "A consistent contribution is easier to test than financial hype.",
        7,
    ).casefold()

    assert "adult hands" in seed
    assert "contribution blocks" in seed
    assert "larger base" in seed
    assert "coin" not in seed
    assert "tower" not in seed
    assert "hourglass" not in seed


@pytest.mark.parametrize(
    "narration",
    [
        "$100 at 5% becomes $105 after one year.",
        "At 5%, year one ends at $105.",
        "From $100 to $105 to $110.25, each period compounds the larger base.",
        "The full $105 becomes the larger base for the next calculation.",
        "Prior interest joins the base, so interest can earn interest.",
        "Time allows the same process to repeat across more periods.",
        "This example explains the mechanism, not a guaranteed return.",
        "Calculate one hypothetical long-term contribution scenario.",
        "Save this and test one money decision.",
        "Generic compound interest beat.",
    ],
)
def test_compound_interest_seed_never_contains_text_prone_surface(narration: str) -> None:
    seed = visual_planner._compound_interest_visual_seed(narration, 0)

    assert not visual_planner.finance_prompt_has_text_surface(seed), seed


def test_compound_interest_on_interest_seed_uses_simple_reinvestment_action() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "This is compound interest: interest can earn interest across repeated periods.",
        6,
    ).casefold()

    assert "adult hand" in seed
    assert "earned plain metal coins" in seed
    assert "brass dish" in seed
    assert "same larger coin stack" in seed
    assert "adding earnings back" in seed
    assert "gear" not in seed


def test_compound_interest_not_guaranteed_seed_is_finance_specific() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "This example explains the mechanism, not a guaranteed return.",
        7,
    ).casefold()

    assert "balance scale" in seed
    assert "stack" in seed
    assert "coins" in seed
    assert "padlock" in seed
    assert "deep green felt" in seed
    assert visual_planner._visual_relevance_score(WEALTH_BLUEPRINT_PROFILE, seed) >= 0.58


def test_compound_interest_scenario_seed_wins_over_timeline_substring() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "Calculate one long-term contribution scenario before making an investment decision. "
        "Move one contribution token onto a long blank timeline.",
        8,
    ).casefold()

    assert "coin" in seed
    assert "row" in seed
    assert "hourglass" in seed
    assert "pale stone shelf" in seed
    assert "timing rings" not in seed
    assert visual_planner._visual_relevance_score(WEALTH_BLUEPRINT_PROFILE, seed) >= 0.58


def test_compound_interest_hook_is_distinct_from_starting_principal() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "How $100 becomes $110.25 without another deposit.",
        0,
    ).casefold()

    assert "rolling one brass coin" in seed
    assert "circular brass track" in seed
    assert "taller stack" in seed
    assert "dark walnut desk" in seed


def test_compound_interest_larger_base_seed_is_simple_and_visually_auditable() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "Year two starts from the full $105, not the original $100.",
        4,
    ).casefold()

    assert "adult hand" in seed
    assert "three progressively higher coin stacks" in seed
    assert "full larger stack" in seed
    assert "flywheel" not in seed
    assert "belt" not in seed


def test_compound_interest_starting_principal_has_no_decorative_marker_requirement() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "Start with $100 and a hypothetical 5% annual rate.",
        2,
    ).casefold()

    assert "ten matching smooth unmarked metal coins" in seed
    assert "calculation tray" in seed
    assert "emerald marker" not in seed


def test_finance_composed_prompt_does_not_name_forbidden_repeated_motifs() -> None:
    prompt = visual_planner._compose_visual_prompt(
        WEALTH_BLUEPRINT_PROFILE,
        "Why Compound Interest Rewards Time More Than Hype",
        "Start with $100 and a hypothetical 5% annual rate.",
        "ten matching plain metal coins in a black calculation tray on pale limestone",
    ).casefold()

    assert "dark walnut" not in prompt
    assert "transparent block" not in prompt
    assert "setting and primary material visibly distinct" in prompt


def test_compound_interest_alternate_transfer_scene_uses_one_renderable_action() -> None:
    seed = visual_planner._compound_interest_alternate_visual_seed(3).casefold()

    assert "adult hand" in seed
    assert "earned plain coins" in seed
    assert "small brass dish" in seed
    assert "larger coin stack" in seed
    assert "gear" not in seed
    assert "flywheel" not in seed


@pytest.mark.parametrize(
    "narration",
    [
        "How $100 becomes $110.25 without another deposit.",
        "At 5%, year one ends at $105. Year two compounds that larger balance.",
        "Start with $100 and a hypothetical 5% annual rate.",
        "The full $105 becomes the larger base for the next calculation.",
        "This is compound interest: interest can earn interest across repeated periods.",
        "This example explains the mechanism, not a guaranteed return.",
        "Calculate one long-term contribution scenario before making an investment decision.",
        "Save this and test it on one money decision today.",
    ],
)
def test_compound_interest_seeds_meet_finance_relevance_floor(narration: str) -> None:
    seed = visual_planner._compound_interest_visual_seed(narration, 0)

    assert visual_planner._visual_relevance_score(WEALTH_BLUEPRINT_PROFILE, seed) >= 0.58, seed


def test_compound_interest_consistency_uses_deterministic_comparison() -> None:
    assert visual_planner._is_consistent_contribution_beat(
        "A consistent contribution is easier to test than financial hype."
    )


@pytest.mark.parametrize(
    ("narration", "expected"),
    [
        ("Compound interest uses starting money and prior interest.", "compound_layers"),
        ("This is compound interest: interest can earn interest across repeated periods.", "compound_reinvestment"),
        ("This example explains the mechanism, not a guaranteed return.", "compound_layers"),
        ("The next calculation starts from a larger base.", "compound_reinvestment"),
        ("Time matters more than a dramatic one-day move.", "compound_time"),
        ("Starting earlier gives the same process more periods.", "compound_early_start"),
        ("Calculate one long-term contribution scenario.", "compound_scenario"),
        ("A generic sentence about money.", ""),
    ],
)
def test_compound_interest_overlay_kind_is_bound_to_narration(narration: str, expected: str) -> None:
    assert visual_planner._compound_interest_overlay_kind(narration) == expected


def test_compound_interest_planner_builds_source_backed_progression_chart(tmp_path: Path) -> None:
    narration = "From $100 to $105 to $110.25, each period compounds the larger base."
    context = ProductionContext(
        job_id="job-comparison",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-v1-stability-compound-interest",
            account_id="yt-wealth-blueprint",
            title="How Compound Interest Works",
            description="A source-backed compound-interest explainer.",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "script": {
                "segments": [
                    {"id": "s01", "type": "body", "text": narration, "visual_note": "three-period progression"}
                ]
            }
        },
    )

    scene = visual_planner._strengthen_scene_prompts(
        context,
        WEALTH_BLUEPRINT_PROFILE,
        [{"scene_id": 1, "script_lines": [0], "segment_type": "body"}],
    )[0]

    assert scene["renderer_scene_type"] == "ChartScene"
    assert scene["chart_values"] == [100.0, 105.0, 110.25]
    assert scene["chart_labels"] == ["START", "YEAR 1", "YEAR 2"]
    assert scene["semantic_overlay_kind"] == "compound_reinvestment"
    assert scene["comparison_left"] == ""
    assert scene["comparison_right"] == ""


def test_compound_interest_planner_deduplicates_repeated_number_scenes(tmp_path: Path) -> None:
    segments = [
        {"id": "s01", "type": "body", "text": "At 5%, year one ends at $105.", "visual_note": "show the result"},
        {
            "id": "s02",
            "type": "body",
            "text": "Start with $100 and a hypothetical annual rate.",
            "visual_note": "show the starting principal",
        },
        {
            "id": "s03",
            "type": "body",
            "text": "Year two starts from the full $105, not the original base.",
            "visual_note": "reuse the larger base",
        },
    ]
    context = ProductionContext(
        job_id="job-number-dedupe",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-number-dedupe",
            account_id="yt-wealth-blueprint",
            title="How Compound Interest Works",
            description="A source-backed compound-interest explainer.",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={"script": {"segments": segments}},
    )

    scenes = visual_planner._strengthen_scene_prompts(
        context,
        WEALTH_BLUEPRINT_PROFILE,
        [
            {
                "scene_id": index,
                "script_lines": [index],
                "segment_type": "body",
            }
            for index in range(3)
        ],
    )

    assert scenes[0]["renderer_scene_type"] == "NumberScene"
    assert scenes[0]["number_value"] == "$105"
    assert scenes[1]["renderer_scene_type"] == "ImageScene"
    assert "number_value" not in scenes[1]
    assert scenes[2]["renderer_scene_type"] == "ImageScene"
    assert "number_value" not in scenes[2]


def test_compound_interest_split_source_progression_ends_with_one_chart(tmp_path: Path) -> None:
    segments = [
        {
            "id": "s01",
            "type": "body",
            "text": "Start with $100 at a hypothetical 5% annual rate.",
            "visual_note": "show the starting balance",
        },
        {
            "id": "s02",
            "type": "body",
            "text": "After year one, you have $105.",
            "visual_note": "show one growth layer",
        },
        {
            "id": "s03",
            "type": "body",
            "text": "Year two applies 5% to the full $105, not the original $100.",
            "visual_note": "show the larger base",
        },
        {
            "id": "s04",
            "type": "body",
            "text": "The second gain is $5.25, taking the balance to $110.25.",
            "visual_note": "show the verified progression",
        },
    ]
    context = ProductionContext(
        job_id="job-split-progression",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-split-progression",
            account_id="yt-wealth-blueprint",
            title="Why Compound Interest Rewards Time More Than Hype",
            description="Use only $100 at 5%: $105 after year one and $110.25 after year two.",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "script": {"segments": segments},
            "production_brief": {
                "image_subject": "Mature finance desk; no coins, piggy banks, or fantasy wealth imagery."
            },
        },
    )

    scenes = visual_planner._strengthen_scene_prompts(
        context,
        WEALTH_BLUEPRINT_PROFILE,
        [
            {"scene_id": index, "script_lines": [index], "segment_type": "body"}
            for index in range(len(segments))
        ],
    )

    assert [scene.get("renderer_scene_type") for scene in scenes] == [
        "NumberScene",
        "NumberScene",
        "ImageScene",
        "ChartScene",
    ]
    assert scenes[3]["chart_values"] == [100.0, 105.0, 110.25]
    assert scenes[3]["chart_labels"] == ["START", "YEAR 1", "YEAR 2"]
    assert scenes[3]["semantic_overlay_kind"] == "compound_reinvestment"
    assert sum(scene.get("renderer_scene_type") == "ImageScene" for scene in scenes) == 1
    assert all(
        not visual_planner.finance_prompt_has_text_surface(str(scene.get("visual_prompt") or ""))
        for scene in scenes
    )


def test_compound_interest_fallback_plan_uses_two_documentary_images(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="job-compound-v1",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-v1-stability-compound-interest",
            account_id="yt-wealth-blueprint",
            title="Why Compound Interest Rewards Time More Than Hype",
            description="Use only $100 at 5%: $105 after year one and $110.25 after year two.",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "hook": {
                "selected": {
                    "text": "How $100 becomes $110.25 without another deposit",
                    "opening_line": "Year two earns $5.25, even though the rate never changed.",
                }
            },
            "production_brief": {
                "image_subject": "Mature finance desk; no coins, piggy banks, or fantasy wealth imagery."
            },
        },
    )
    script = script_writer._safe_fallback_script(
        context,
        context.artifacts["hook"]["selected"],
        WEALTH_BLUEPRINT_PROFILE,
    )
    context.artifacts["script"] = script

    scenes = visual_planner._strengthen_scene_prompts(
        context,
        WEALTH_BLUEPRINT_PROFILE,
        [
            {
                "scene_id": index,
                "script_lines": [index],
                "segment_type": segment["type"],
            }
            for index, segment in enumerate(script["segments"])
        ],
    )
    scene_types = [scene.get("renderer_scene_type") for scene in scenes]

    assert len(script["segments"]) == 11
    assert scene_types.count("ImageScene") == 2
    assert scene_types.count("ChartScene") == 1
    assert scene_types.count("NumberScene") == 3
    assert scenes[1]["number_value"] == "$5.25"
    assert scenes[1]["headline"] == "YEAR TWO GAIN"
    assert scenes[-2]["headline"] == "MECHANISM, NOT PROMISE"
    assert scenes[-2]["renderer_scene_type"] == "HookScene"
    image_scene = next(scene for scene in scenes if scene.get("renderer_scene_type") == "ImageScene")
    image_description = f"{image_scene.get('visual_subject', '')} {image_scene.get('visual_prompt', '')}".casefold()
    assert "calculator" not in image_description
    assert "leather wallet" in image_description
    assert "blank unbranded bank card" in image_description
    assert {
        "plant",
        "flower",
        "flower pot",
        "potted plant",
        "calculator",
        "split screen",
        "diptych",
        "collage",
        "duplicated subject",
    }.issubset(set(image_scene["forbidden_visual_terms"]))


def test_compound_interest_legacy_padding_stays_on_topic(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="job-compound-padding",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-v1-stability-compound-interest",
            account_id="yt-wealth-blueprint",
            title="Why Compound Interest Rewards Time More Than Hype",
            description="Use only $100 at 5%: $105 after year one and $110.25 after year two.",
            total_score=80,
        ),
        artifact_dir=tmp_path,
    )

    payload = script_writer._legacy_body_to_segments(
        context,
        {"text": "How $100 becomes $110.25", "opening_line": "The base changes after year one."},
        [
            "Compound interest uses the starting balance.",
            "Prior interest joins the next base.",
            "The next period begins from more.",
            "Time lets that process repeat.",
            "The mechanism is not a guarantee.",
            "Test one realistic scenario.",
        ],
        "Save this example.",
        WEALTH_BLUEPRINT_PROFILE,
    )

    spoken = " ".join(segment["text"] for segment in payload["segments"])
    assert "changes how the whole scene feels" not in spoken
    assert "interest can begin earning interest" in spoken


def test_compound_mechanism_conclusion_uses_deterministic_editorial_scene(tmp_path: Path) -> None:
    segment = {
        "id": "s10",
        "type": "body",
        "text": "This is a mechanism, not a promise. Test it against one realistic long-term plan.",
        "duration_target_sec": 4,
        "visual_note": "A planning scenario.",
    }
    context = ProductionContext(
        job_id="job-compound-conclusion",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-v1-stability-compound-interest",
            account_id="yt-wealth-blueprint",
            title="Why Compound Interest Rewards Time More Than Hype",
            description="A source-backed compound-interest example.",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={"script": {"segments": [segment]}},
    )

    scenes = visual_planner._strengthen_scene_prompts(
        context,
        WEALTH_BLUEPRINT_PROFILE,
        [{"scene_id": 0, "script_lines": [0], "segment_type": "body"}],
    )

    assert scenes[0]["renderer_scene_type"] == "HookScene"
    assert scenes[0]["headline"] == "MECHANISM, NOT PROMISE"
    assert scenes[0]["semantic_overlay_kind"] == "compound_scenario"


def test_no_coin_compound_starting_scene_uses_one_simple_finance_pair() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "Start with $100 at a hypothetical 5% annual rate.",
        2,
        production_brief={"image_subject": "Mature finance desk; no coins or piggy banks."},
    ).casefold()

    assert "adult hands" in seed
    assert "leather wallet" in seed
    assert "blank unbranded bank card" in seed
    assert "calculator" not in seed
    assert "plant" not in seed
    assert "folder" not in seed
    assert "coin" not in seed


def test_no_coin_compound_full_base_scene_uses_specific_route_before_generic_rate_route() -> None:
    seed = visual_planner._compound_interest_visual_seed(
        "Year two applies 5% to the full $105, not the original $100.",
        4,
        production_brief={"image_subject": "Mature finance desk; no coins or piggy banks."},
    ).casefold()

    assert "two unlabelled budgeting envelopes" in seed
    assert "one open leather wallet" in seed
    assert "blank unbranded bank card" not in seed
    assert "coin" not in seed


def test_compound_interest_planner_honors_source_brief_that_forbids_coins(tmp_path: Path) -> None:
    inspired_by = json.dumps(
        {
            "production_brief": {
                "image_subject": (
                    "A mature editorial desk with a calendar, calculator, and handwritten ledger; "
                    "no coins, piggy banks, or fantasy wealth imagery."
                ),
                "expected_scene_evidence": [
                    "analog calculator",
                    "time passing",
                    "larger calculation base",
                ],
            }
        }
    )
    segments = [
        {
            "id": "s01",
            "type": "body",
            "text": "Start with $100 and a hypothetical 5% annual rate.",
            "visual_note": "show the starting calculation",
        },
        {
            "id": "s02",
            "type": "body",
            "text": "Year two starts from the full $105, not the original $100.",
            "visual_note": "show the larger calculation base",
        },
        {
            "id": "s03",
            "type": "body",
            "text": "From $100 to $105 to $110.25, each period compounds the larger base.",
            "visual_note": "show the three-period progression",
        },
    ]
    context = ProductionContext(
        job_id="job-source-brief-no-coins",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-source-brief-no-coins",
            account_id="yt-wealth-blueprint",
            title="How Compound Interest Works",
            description="A source-backed compound-interest explainer.",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "script": {
                "segments": segments,
                "topic_intelligence": {"inspired_by": inspired_by},
            }
        },
    )

    scenes = visual_planner._strengthen_scene_prompts(
        context,
        WEALTH_BLUEPRINT_PROFILE,
        [
            {"scene_id": index, "script_lines": [index], "segment_type": "body"}
            for index in range(len(segments))
        ],
    )

    combined = " ".join(
        f"{scene.get('visual_subject', '')} {scene.get('visual_prompt', '')}" for scene in scenes
    ).casefold()
    assert "coin" not in combined
    assert "calculator" not in combined
    assert "blank unbranded bank card" in combined
    assert "budgeting envelope" in combined
    assert "hourglass" in combined
    base_forbidden = [
        "coin",
        "coins",
        "piggy bank",
        "piggy banks",
        "fantasy wealth imagery",
    ]
    assert all(
        scene["forbidden_visual_terms"] == base_forbidden
        for scene in scenes
        if scene["renderer_scene_type"] != "ImageScene"
    )
    generated_scene = next(scene for scene in scenes if scene["renderer_scene_type"] == "ImageScene")
    assert generated_scene["forbidden_visual_terms"][: len(base_forbidden)] == base_forbidden
    assert {
        "plant",
        "flower",
        "flower pot",
        "potted plant",
        "calculator",
        "split screen",
        "diptych",
        "collage",
        "duplicated subject",
    }.issubset(set(generated_scene["forbidden_visual_terms"]))
    assert scenes[2]["renderer_scene_type"] == "ChartScene"
