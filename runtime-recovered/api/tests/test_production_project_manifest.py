from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.project_manifest import (
    MANIFEST_FILENAME,
    ProjectManifest,
    QAAction,
    SceneType,
    TimeRange,
    build_project_manifest,
    canonical_json,
    compute_manifest_payload_hash,
    deterministic_hash,
    load_project_manifest,
    migrate_legacy_artifacts,
    save_project_manifest,
    infer_scene_type,
    infer_semantic_overlay_kind,
    normalize_semantic_overlay_kind,
    _normalize_legacy_manifest_payload,
    _source_number_value,
    _source_backed_chart_values,
    _scene_qa,
    _overall_qa,
)


def test_v1_exposes_exactly_five_scene_types() -> None:
    assert {scene_type.value for scene_type in SceneType} == {
        "HookScene",
        "ImageScene",
        "NumberScene",
        "ChartScene",
        "CTAScene",
    }


def test_legacy_comparison_scene_migrates_to_chart_scene() -> None:
    payload = {
        "scenes": [
            {
                "scene_id": "s01",
                "scene_type": "ComparisonScene",
                "comparison_left": "CONSISTENT CONTRIBUTIONS",
                "comparison_right": "FINANCIAL HYPE",
            }
        ]
    }

    normalized = _normalize_legacy_manifest_payload(payload)

    assert normalized["scenes"][0]["scene_type"] == "ChartScene"
    assert normalized["scenes"][0]["comparison_left"] == "CONSISTENT CONTRIBUTIONS"


@pytest.mark.parametrize(
    ("narration", "expected"),
    [
        ("The real gap starts with how your money behaves after you earn it.", "income_split"),
        ("Your paycheck lands, then convenience gets louder.", "payday_arrival"),
        ("Then convenience gets louder.", "convenience_chain"),
        ("Each purchase feels too small to matter.", "cumulative_spending"),
        ("By Friday, every dollar is assigned without a plan.", "allocation_sorter"),
        ("Move one decision out of autopilot.", "auto_invest"),
        ("The boring pause protects the paycheck.", "purchase_pause"),
        ("Compound interest uses starting money and prior interest.", "compound_layers"),
        ("The next calculation begins from a larger base.", "compound_reinvestment"),
        ("Time matters more than a one-day move.", "compound_time"),
        ("Starting earlier gives the process more periods.", "compound_early_start"),
        ("Calculate one long-term contribution scenario.", "compound_scenario"),
        ("Build an emergency fund first.", None),
    ],
)
def test_semantic_overlay_inference_is_source_backed(narration: str, expected: str | None) -> None:
    assert infer_semantic_overlay_kind(narration) == expected


def test_semantic_overlay_inference_does_not_invent_a_finance_action() -> None:
    assert infer_semantic_overlay_kind("A quiet desk with a leather wallet.") is None


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("spending_routes", "allocation_sorter"),
        ("decision_fork", "auto_invest"),
        ("purchase_pause", "purchase_pause"),
        ("compound_reinvestment", "compound_reinvestment"),
        ("unknown_overlay", None),
        (None, None),
    ],
)
def test_semantic_overlay_normalizes_legacy_values(stored: object, expected: str | None) -> None:
    assert normalize_semantic_overlay_kind(stored) == expected


def test_load_manifest_normalizes_legacy_overlay_after_hash_verification(tmp_path: Path) -> None:
    context = _context(tmp_path)
    path = context.artifact_dir / MANIFEST_FILENAME
    save_project_manifest(path, build_project_manifest(context, current_step="video", status="running"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scenes"][0]["semantic_overlay_kind"] = "spending_routes"
    payload["content_hash"] = compute_manifest_payload_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project_manifest(path)

    assert loaded.scenes[0].semantic_overlay_kind == "allocation_sorter"


def test_load_manifest_rejects_tampered_legacy_overlay(tmp_path: Path) -> None:
    context = _context(tmp_path)
    path = context.artifact_dir / MANIFEST_FILENAME
    save_project_manifest(path, build_project_manifest(context, current_step="video", status="running"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scenes"][0]["semantic_overlay_kind"] = "spending_routes"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        load_project_manifest(path)


def test_photography_prompt_is_not_misclassified_as_chart_scene() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": "A daily purchase quietly drains annual savings."},
        {
            "headline": "THE DAILY LEAK",
            "visual_prompt": "Mature documentary photography of a wallet beside loose coins.",
        },
    )

    assert scene_type is SceneType.IMAGE


def test_graph_prompt_without_source_values_remains_image_scene() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": "Spending rises over time."},
        {"headline": "SPENDING TREND", "visual_prompt": "A clean line graph with one rising axis."},
        require_source_backed_data=True,
    )

    assert scene_type is SceneType.IMAGE


def test_graph_prompt_with_two_source_values_is_chart_scene() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": "Savings moved from 20 percent to 30 percent."},
        {"headline": "SAVINGS TREND", "visual_prompt": "A clean line graph with one rising axis."},
        require_source_backed_data=True,
    )

    assert scene_type is SceneType.CHART


def test_enumerated_one_one_one_beat_is_deterministic_number_scene() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": "One delivery. One subscription. One tiny upgrade."},
        {
            "headline": "ONE DELIVERY. ONE",
            "visual_prompt": "Three unrelated consumer objects on a checkout conveyor.",
        },
    )

    assert scene_type is SceneType.NUMBER


def test_repeated_one_beat_has_source_backed_display_value() -> None:
    assert _source_number_value(
        "One delivery. One subscription. One tiny upgrade.",
        [],
    ) == "ONE"


def test_ordinal_comparison_has_source_backed_display_value() -> None:
    assert _source_number_value(
        "Create friction before the first purchase, not regret after the fifth.",
        [],
    ) == "FIRST / FIFTH"


def test_single_one_is_not_duplicated_by_matching_headline() -> None:
    scene_type = infer_scene_type(
        {
            "type": "body",
            "text": "Move one decision out of autopilot before you think about investing.",
        },
        {
            "headline": "MOVE ONE DECISION",
            "visual_prompt": "A cracked acrylic tube spilling coins beside a delivery bag.",
        },
    )

    assert scene_type is SceneType.IMAGE


def test_weekly_money_summary_without_values_remains_image_scene() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": ""},
        {
            "headline": "BY FRIDAY, THE",
            "visual_prompt": "A weekly money budget represented by consumer products on a checkout conveyor.",
        },
        require_source_backed_data=True,
    )

    assert scene_type is SceneType.IMAGE


def test_ordinal_purchase_comparison_is_deterministic_number_scene() -> None:
    scene_type = infer_scene_type(
        {
            "type": "body",
            "text": "Create friction before the first purchase, not regret after the fifth.",
        },
        {"headline": "CREATE FRICTION", "visual_prompt": "A wallet behind a barrier."},
    )

    assert scene_type is SceneType.NUMBER


def test_money_pause_without_values_remains_image_scene() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": ""},
        {
            "headline": "THE BORING PAUSE",
            "visual_prompt": "A deliberate pause keeps the paycheck alive before a purchase.",
        },
        require_source_backed_data=True,
    )

    assert scene_type is SceneType.IMAGE


def _context(tmp_path: Path) -> ProductionContext:
    artifact_dir = tmp_path / "job"
    video_dir = artifact_dir / "video"
    video_dir.mkdir(parents=True)
    clip_one = video_dir / "s01.mp4"
    clip_two = video_dir / "s02.mp4"
    final_video = artifact_dir / "final_short.mp4"
    clip_one.write_bytes(b"scene-one")
    clip_two.write_bytes(b"scene-two")
    final_video.write_bytes(b"final-video")
    return ProductionContext(
        job_id="prod-test-manifest",
        account_id="youtube:wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="youtube:wealth-blueprint",
            title="The Rule of 72",
            description="A short about compound growth.",
            total_score=91,
        ),
        artifact_dir=artifact_dir,
        artifacts={
            "operating_standards": {"version": "WEALTH_BLUEPRINT_STYLE_V1"},
            "script": {
                "language_code": "en",
                "channel_profile": {"key": "wealth_blueprint", "language_code": "en"},
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "Your money can double faster than you think.",
                        "duration_target_sec": 3,
                    },
                    {
                        "id": "s02",
                        "type": "body",
                        "text": "Divide 72 by the annual return.",
                        "duration_target_sec": 5,
                    },
                ],
            },
            "voice": {
                "primary": "f5_tts_single_pass",
                "voice_profile": {"id": "wealth-narrator-v1"},
                "timestamps": [
                    {"segment_id": "s01", "start_sec": 0, "end_sec": 3, "duration_sec": 3},
                    {"segment_id": "s02", "start_sec": 3, "end_sec": 8, "duration_sec": 5},
                ],
            },
            "visuals": {
                "scenes": [
                    {"scene_id": 0, "visual_prompt": "A timer beside a growing stack of coins."},
                    {"scene_id": 1, "visual_prompt": "A clean chart showing 72 and annual return."},
                ]
            },
            "video": {
                "workflow_template": "wealth-sdxl-v1.json",
                "clips": [
                    {
                        "scene_id": 0,
                        "status": "generated",
                        "backend": "comfyui",
                        "target_backend": "comfyui",
                        "prompt": "A timer beside a growing stack of coins.",
                        "file": str(clip_one),
                    },
                    {
                        "scene_id": 1,
                        "status": "generated",
                        "backend": "comfyui",
                        "target_backend": "comfyui",
                        "prompt": "A clean chart showing 72 and annual return.",
                        "file": str(clip_two),
                    },
                ],
            },
            "assembly": {"output_file": str(final_video)},
        },
    )


def test_manifest_v1_round_trip_is_lossless(tmp_path: Path) -> None:
    context = _context(tmp_path)
    manifest = build_project_manifest(context, current_step="video", status="running")
    saved = save_project_manifest(context.artifact_dir / MANIFEST_FILENAME, manifest)

    loaded = load_project_manifest(context.artifact_dir / MANIFEST_FILENAME)

    assert canonical_json(loaded.model_dump(mode="json")) == canonical_json(saved.model_dump(mode="json"))
    assert loaded.content_hash
    assert loaded.brand_profile_version.startswith("WEALTH_BLUEPRINT_STYLE_V1-")
    assert loaded.brand.wordmark == "WEALTH BLUEPRINT"
    assert loaded.brand.background == "#142633"
    assert loaded.brand.panel == "#1C3344"
    assert loaded.renderer_version == "legacy-ffmpeg-v1"
    assert [scene.scene_type for scene in loaded.scenes] == [SceneType.HOOK, SceneType.NUMBER]
    assert all(scene.assets[0].provenance.artifact_hash for scene in loaded.scenes)
    final_artifact = next(item for item in loaded.artifacts if item.role == "final_video")
    assert final_artifact.source_asset_ids == ["raw-video", "narration", "subtitles"]
    assert final_artifact.provenance.provider == "ffmpeg"
    assert final_artifact.provenance.artifact_hash == final_artifact.artifact_hash


def test_manifest_versions_change_when_style_guide_or_workflow_content_changes(tmp_path: Path) -> None:
    context = _context(tmp_path)
    workflow = tmp_path / "wealth-workflow.json"
    workflow.write_text('{"version":1}', encoding="utf-8")
    context.artifacts["video"]["workflow_template"] = str(workflow)
    context.artifacts["script"]["style_guide"] = {"visual_style": "editorial finance"}

    first = build_project_manifest(context, current_step="video", status="running")
    context.artifacts["script"]["style_guide"] = {"visual_style": "premium documentary finance"}
    second = build_project_manifest(context, current_step="video", status="running")
    workflow.write_text('{"version":2}', encoding="utf-8")
    third = build_project_manifest(context, current_step="video", status="running")

    assert first.brand_profile_version != second.brand_profile_version
    assert second.workflow_version != third.workflow_version
    assert third.workflow_version.startswith("wealth-workflow.json@")


def test_manifest_preserves_only_source_backed_chart_values_and_labels(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["script"]["segments"] = [
        {
            "id": "s01",
            "type": "body",
            "text": "Starting at $100, year 1 reaches $105 and year 2 reaches $110.25 as the larger base compounds.",
            "duration_target_sec": 5,
        }
    ]
    context.artifacts["voice"]["timestamps"] = [
        {"segment_id": "s01", "start_sec": 0, "end_sec": 5, "duration_sec": 5}
    ]
    context.artifacts["visuals"]["scenes"] = [
        {
            "scene_id": 0,
            "renderer_scene_type": "ChartScene",
            "chart_values": [100, 105, 110.25],
            "chart_labels": ["START", "YEAR 1", "YEAR 2"],
            "visual_prompt": "A source-backed compound-interest chart.",
        }
    ]
    context.artifacts["video"]["clips"] = [context.artifacts["video"]["clips"][0]]

    scene = build_project_manifest(context, current_step="video", status="running").scenes[0]

    assert scene.scene_type is SceneType.CHART
    assert scene.chart_values == [100.0, 105.0, 110.25]
    assert scene.chart_labels == ["START", "YEAR 1", "YEAR 2"]


def test_manifest_preserves_compound_progression_sourced_across_script(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["script"]["segments"] = [
        {
            "id": "s01",
            "type": "body",
            "text": "Start with $100 at a hypothetical rate.",
            "duration_target_sec": 3,
        },
        {
            "id": "s02",
            "type": "body",
            "text": "After year one, the balance is $105.",
            "duration_target_sec": 3,
        },
        {
            "id": "s03",
            "type": "body",
            "text": "In year two, the $5.25 gain moves the balance to $110.25.",
            "duration_target_sec": 4,
        },
    ]
    context.artifacts["voice"]["timestamps"] = [
        {"segment_id": "s01", "start_sec": 0, "end_sec": 3, "duration_sec": 3},
        {"segment_id": "s02", "start_sec": 3, "end_sec": 6, "duration_sec": 3},
        {"segment_id": "s03", "start_sec": 6, "end_sec": 10, "duration_sec": 4},
    ]
    context.artifacts["visuals"]["scenes"] = [
        {"scene_id": 0, "renderer_scene_type": "ImageScene"},
        {"scene_id": 1, "renderer_scene_type": "NumberScene"},
        {
            "scene_id": 2,
            "renderer_scene_type": "ChartScene",
            "semantic_overlay_kind": "compound_reinvestment",
            "chart_values": [100, 105, 110.25],
            "chart_labels": ["START", "YEAR 1", "YEAR 2"],
        },
    ]
    context.artifacts["video"]["clips"] = [
        context.artifacts["video"]["clips"][0],
        context.artifacts["video"]["clips"][1],
        context.artifacts["video"]["clips"][0],
    ]

    scene = build_project_manifest(context, current_step="video", status="running").scenes[2]

    assert scene.scene_type is SceneType.CHART
    assert scene.chart_values == [100.0, 105.0, 110.25]
    assert scene.chart_labels == ["START", "YEAR 1", "YEAR 2"]


def test_unrelated_chart_cannot_borrow_values_from_other_script_segments(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["script"]["segments"] = [
        {"id": "s01", "type": "body", "text": "Start with $100.", "duration_target_sec": 3},
        {"id": "s02", "type": "body", "text": "The balance becomes $105.", "duration_target_sec": 3},
        {
            "id": "s03",
            "type": "body",
            "text": "This separate comparison shows $5.25 beside $110.25.",
            "duration_target_sec": 4,
        },
    ]
    context.artifacts["voice"]["timestamps"] = [
        {"segment_id": "s01", "start_sec": 0, "end_sec": 3, "duration_sec": 3},
        {"segment_id": "s02", "start_sec": 3, "end_sec": 6, "duration_sec": 3},
        {"segment_id": "s03", "start_sec": 6, "end_sec": 10, "duration_sec": 4},
    ]
    context.artifacts["visuals"]["scenes"] = [
        {"scene_id": 0, "renderer_scene_type": "ImageScene"},
        {"scene_id": 1, "renderer_scene_type": "NumberScene"},
        {
            "scene_id": 2,
            "renderer_scene_type": "ChartScene",
            "semantic_overlay_kind": "income_split",
            "chart_values": [100, 105, 110.25],
            "chart_labels": ["START", "MIDDLE", "END"],
        },
    ]
    context.artifacts["video"]["clips"] = [
        context.artifacts["video"]["clips"][0],
        context.artifacts["video"]["clips"][1],
        context.artifacts["video"]["clips"][0],
    ]

    scene = build_project_manifest(context, current_step="video", status="running").scenes[2]

    assert scene.scene_type is SceneType.CHART
    assert scene.chart_values == [5.25, 110.25]
    assert scene.chart_labels == []


def test_manifest_discards_planner_chart_values_not_present_in_narration(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["script"]["segments"] = [
        {
            "id": "s01",
            "type": "body",
            "text": "The verified values are $100, $105, and $110.25.",
            "duration_target_sec": 5,
        }
    ]
    context.artifacts["voice"]["timestamps"] = [
        {"segment_id": "s01", "start_sec": 0, "end_sec": 5, "duration_sec": 5}
    ]
    context.artifacts["visuals"]["scenes"] = [
        {
            "scene_id": 0,
            "renderer_scene_type": "ChartScene",
            "chart_values": [100, 999, 110.25],
            "chart_labels": ["START", "FABRICATED", "YEAR 2"],
        }
    ]
    context.artifacts["video"]["clips"] = [context.artifacts["video"]["clips"][0]]

    scene = build_project_manifest(context, current_step="video", status="running").scenes[0]

    assert scene.chart_values == [100.0, 105.0, 110.25]
    assert scene.chart_labels == []


def test_chart_values_cannot_repeat_source_evidence_more_times_than_narration() -> None:
    assert _source_backed_chart_values([100, 105, 100, 105], [100, 105]) == [100, 105]


def test_chart_values_cannot_reverse_narration_order() -> None:
    assert _source_backed_chart_values([110.25, 105, 100], [100, 105, 110.25]) == [100, 105, 110.25]


@pytest.mark.parametrize("malformed", [[100, "not-a-number", 105], [100, True, 105], [100, float("nan")], [100, float("inf")]])
def test_chart_values_reject_malformed_numbers(malformed: list[object]) -> None:
    with pytest.raises(ValueError, match="finite numeric values"):
        _source_backed_chart_values(malformed, [100, 105])


def test_manifest_discards_chart_labels_that_reverse_narration_order(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["script"]["segments"] = [
        {
            "id": "s01",
            "type": "body",
            "text": "Starting at $100, year 1 reaches $105 and year 2 reaches $110.25.",
            "duration_target_sec": 5,
        }
    ]
    context.artifacts["voice"]["timestamps"] = [
        {"segment_id": "s01", "start_sec": 0, "end_sec": 5, "duration_sec": 5}
    ]
    context.artifacts["visuals"]["scenes"] = [
        {
            "scene_id": 0,
            "renderer_scene_type": "ChartScene",
            "chart_values": [100, 105, 110.25],
            "chart_labels": ["YEAR 2", "YEAR 1", "START"],
        }
    ]
    context.artifacts["video"]["clips"] = [context.artifacts["video"]["clips"][0]]

    scene = build_project_manifest(context, current_step="video", status="running").scenes[0]

    assert scene.chart_values == [100.0, 105.0, 110.25]
    assert scene.chart_labels == []


def test_manifest_discards_fabricated_chart_labels_even_when_values_are_source_backed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["script"]["segments"] = [
        {
            "id": "s01",
            "type": "body",
            "text": "Starting at $100, year 1 reaches $105 and year 2 reaches $110.25.",
            "duration_target_sec": 5,
        }
    ]
    context.artifacts["voice"]["timestamps"] = [
        {"segment_id": "s01", "start_sec": 0, "end_sec": 5, "duration_sec": 5}
    ]
    context.artifacts["visuals"]["scenes"] = [
        {
            "scene_id": 0,
            "renderer_scene_type": "ChartScene",
            "chart_values": [100, 105, 110.25],
            "chart_labels": ["START", "GUARANTEED PROFIT", "YEAR 2"],
        }
    ]
    context.artifacts["video"]["clips"] = [context.artifacts["video"]["clips"][0]]

    scene = build_project_manifest(context, current_step="video", status="running").scenes[0]

    assert scene.chart_values == [100.0, 105.0, 110.25]
    assert scene.chart_labels == []


def test_manifest_preserves_candidate_specific_generation_provenance(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate_one = context.artifact_dir / "video" / "s01.candidate-01.png"
    candidate_two = context.artifact_dir / "video" / "s01.candidate-02.png"
    candidate_one.write_bytes(b"candidate-one")
    candidate_two.write_bytes(b"candidate-two")
    clip = context.artifacts["video"]["clips"][0]
    clip.update(
        {
            "generated_image_paths": [str(candidate_one), str(candidate_two)],
            "selected_image_path": str(candidate_two),
            "image_candidates": [
                {
                    "path": str(candidate_one),
                    "candidate_id": "s01-candidate-01",
                    "candidate_index": 1,
                    "generation_attempt": 2,
                    "prompt": "first candidate prompt",
                    "seed": 101,
                    "passed": False,
                    "score": 0.7,
                    "reasons": ["comparative_visual_integrity_rejected"],
                    "comparison_position": 1,
                    "comparative_vision": {
                        "status": "pass",
                        "model": "qwen3-vl:8b",
                        "ranked_candidate_indexes": [2, 1],
                        "rejected_candidate_indexes": [1],
                        "selection_confidence": 9.0,
                        "summary": "Candidate 2 is more coherent.",
                        "defects_by_candidate": {"1": ["distorted coin"], "2": []},
                    },
                    "generation_evidence": {
                        "status": "verified",
                        "backend": "z_image_turbo",
                        "models": ["z_image_turbo_bf16.safetensors", "qwen_3_4b.safetensors"],
                        "generation_parameters": {"width": 512, "height": 896, "seed": 101},
                        "workflow": {"1": {"class_type": "CheckpointLoaderSimple"}},
                    },
                },
                {
                    "path": str(candidate_two),
                    "candidate_id": "s01-candidate-02",
                    "candidate_index": 2,
                    "generation_attempt": 2,
                    "prompt": "selected candidate prompt",
                    "seed": 202,
                    "passed": True,
                    "selected": True,
                    "score": 0.9,
                    "comparison_position": 2,
                    "comparative_vision": {
                        "status": "pass",
                        "model": "qwen3-vl:8b",
                        "ranked_candidate_indexes": [2, 1],
                        "rejected_candidate_indexes": [1],
                        "selection_confidence": 9.0,
                        "summary": "Candidate 2 is more coherent.",
                        "defects_by_candidate": {"1": ["distorted coin"], "2": []},
                    },
                    "cross_scene_diversity": {
                        "minimum_normalized_difference": 0.18,
                        "threshold": 0.04,
                        "compared_scene_count": 1,
                        "passed": True,
                    },
                    "generation_evidence": {
                        "status": "verified",
                        "backend": "flux2_klein",
                        "models": ["flux-2-klein-base-4b.safetensors"],
                        "generation_parameters": {"width": 512, "height": 896, "seed": 202},
                        "workflow": {"1": {"class_type": "UNETLoader"}},
                    },
                },
            ],
        }
    )

    manifest = build_project_manifest(context, current_step="video", status="running")
    selected = next(asset for asset in manifest.scenes[0].assets if asset.role == "selected_image")
    rejected = next(asset for asset in manifest.scenes[0].assets if asset.role == "image_candidate")

    assert selected.provenance.provider == "comfyui"
    assert selected.provenance.workflow == "flux2_klein"
    assert selected.provenance.model == "flux-2-klein-base-4b.safetensors"
    assert selected.provenance.prompt == "selected candidate prompt"
    assert selected.provenance.seed == 202
    assert selected.provenance.generation_parameters["seed"] == 202
    selection = selected.provenance.generation_parameters["selection_evidence"]
    assert selection["decision"] == "selected_by_comparative_vision"
    assert selection["selected"] is True
    assert selection["comparison"]["model"] == "qwen3-vl:8b"
    assert selection["comparison"]["ranked_candidate_indexes"] == [2, 1]
    assert selection["cross_scene_diversity"]["passed"] is True
    assert "path" not in canonical_json(selection).lower()
    assert selected.provenance.workflow_hash == deterministic_hash(selected.provenance.workflow_payload)
    assert rejected.provenance.prompt == "first candidate prompt"
    assert rejected.provenance.seed == 101
    assert rejected.provenance.generation_parameters["selection_evidence"]["decision"] == (
        "rejected_by_comparative_vision"
    )
    assert selected.provenance.input_hash != rejected.provenance.input_hash


def test_raw_video_provenance_binds_real_scene_clip_hashes(tmp_path: Path) -> None:
    context = _context(tmp_path)
    raw_path = context.artifact_dir / "raw_video.mp4"
    raw_path.write_bytes(b"raw-video")
    context.artifacts["video"]["video_raw_path"] = str(raw_path)
    manifest = build_project_manifest(context, current_step="video", status="running")
    raw_video = next(artifact for artifact in manifest.artifacts if artifact.role == "raw_video")
    scene_videos = {
        asset.asset_id: asset.provenance.artifact_hash
        for scene in manifest.scenes
        for asset in scene.assets
        if asset.role == "scene_video"
    }

    assert raw_video.source_asset_ids == list(scene_videos)
    expected_input_hash = deterministic_hash(
        {
            "artifact_id": "raw-video",
            "role": "raw_video",
            "source_asset_ids": raw_video.source_asset_ids,
            "source_artifact_hashes": scene_videos,
            "provenance": {
                "provider": raw_video.provenance.provider,
                "model": raw_video.provenance.model,
                "workflow": raw_video.provenance.workflow,
                "workflow_payload": raw_video.provenance.workflow_payload,
                "prompt": raw_video.provenance.prompt,
                "seed": raw_video.provenance.seed,
                "generation_parameters": raw_video.provenance.generation_parameters,
            },
        }
    )
    assert raw_video.provenance.input_hash == expected_input_hash


def test_global_artifacts_preserve_sanitized_production_provenance(tmp_path: Path) -> None:
    context = _context(tmp_path)
    audio = context.artifact_dir / "narration.wav"
    subtitles = context.artifact_dir / "subtitles.srt"
    raw_video = context.artifact_dir / "renderer_silent.mp4"
    thumbnail = context.artifact_dir / "thumbnail.png"
    audio.write_bytes(b"audio")
    subtitles.write_text("approved narration", encoding="utf-8")
    raw_video.write_bytes(b"raw-render")
    thumbnail.write_bytes(b"thumbnail")
    context.artifacts["voice"].update(
        {
            "output_file": str(audio),
            "subtitle_path": str(subtitles),
            "quality_tier": "primary",
            "voice_backend_grade": "premium",
            "production_ready": True,
            "normalization": "ffmpeg loudnorm",
            "voice_identity": {
                "provider_family": "f5_tts",
                "identity_hash": "a" * 64,
                "reference_audio_sha256": "b" * 64,
                "speed": 1.15,
            },
            "engine_attempts": [
                {
                    "segment_id": "s01",
                    "engine": "f5_tts_http",
                    "status": "generated",
                    "single_pass": True,
                    "attempts": [
                        {
                            "engine": "f5_tts_http",
                            "mode": "gradio_queue",
                            "api_name": "basic_tts",
                            "ok": True,
                            "base_url": "http://secret-local-url",
                            "reference_audio": "C:/private/reference.wav",
                        }
                    ],
                }
            ],
            "subtitle_status": {
                "engine": "whisper_cpp",
                "source": "whisper_cpp",
                "status": "generated",
                "quality_checked": True,
                "script_word_overlap": 42,
                "expected_script_words": 43,
            },
        }
    )
    context.artifacts["assembly"].update(
        {
            "remotion_render_path": str(raw_video),
            "renderer_version": "wealth-blueprint-reference-v0.9",
            "renderer": {
                "manifest_content_hash": "c" * 64,
                "renderer": {
                    "renderer_source_fingerprint": "d" * 64,
                    "renderer_source_hashes": {"src/scenes.tsx": "e" * 64},
                    "font_assets": {"extra_bold": {"sha256": "f" * 64}},
                    "width": 1080,
                    "height": 1920,
                    "fps": 30,
                    "duration_in_frames": 1593,
                },
            },
            "render": {
                "command": ["ffmpeg", "-i", "raw.mp4", "-af", "loudnorm", "final.mp4"],
                "audio_filter": "loudnorm=I=-14:TP=-1.5:LRA=11",
                "video_mode": "stream_copy",
                "probe": {"width": 1080, "height": 1920, "fps": 30},
                "video_stream_copy_verified": True,
            },
            "ffmpeg_plan": {"strategy": "remotion_visual_ffmpeg_audio_mux", "filters": ["loudnorm"]},
            "assembly_provenance": {"strategy": "remotion_visual_ffmpeg_audio_mux", "chain_hash": "1" * 64},
        }
    )
    context.artifacts["thumbnail"] = {
        "thumbnail_path": str(thumbnail),
        "thumbnail_generation_backend": "comfyui_scene_reframe",
        "thumbnail_model": {"checkpoint": "sdxl.safetensors"},
        "workflow_template": "thumbnail_default.json",
        "prompt_inputs": {"positive_prompt": "premium finance desk", "negative_prompt": "text"},
        "comfyui_generated": True,
        "fallback_used": False,
        "thumbnail_quality": {"passed": True},
    }

    manifest = build_project_manifest(context, current_step="qa", status="qa_failed")
    by_role = {artifact.role: artifact for artifact in manifest.artifacts}

    narration = by_role["narration"].provenance
    assert narration.provider == "f5_tts"
    assert narration.workflow == "f5_tts_single_pass"
    assert narration.workflow_payload["voice_identity"]["reference_audio_sha256"] == "b" * 64
    serialized_narration = canonical_json(narration.model_dump(mode="json"))
    assert "secret-local-url" not in serialized_narration
    assert "private/reference.wav" not in serialized_narration

    subtitle = by_role["subtitles"].provenance
    assert subtitle.provider == "whisper_cpp"
    assert subtitle.generation_parameters["script_word_overlap"] == 42

    raw = by_role["raw_video"].provenance
    assert raw.provider == "remotion"
    assert raw.workflow_payload["renderer_source_fingerprint"] == "d" * 64
    assert raw.workflow_hash == deterministic_hash(raw.workflow_payload)

    final = by_role["final_video"].provenance
    assert final.workflow_payload["command"][0] == "ffmpeg"
    assert final.workflow_payload["audio_filter"].startswith("loudnorm")
    assert final.generation_parameters["video_stream_copy_verified"] is True
    assert by_role["final_video"].source_asset_ids == ["raw-video", "narration"]

    thumb = by_role["thumbnail"].provenance
    assert thumb.provider == "comfyui_scene_reframe"
    assert thumb.model == "sdxl.safetensors"
    assert thumb.prompt == "premium finance desk"
    assert thumb.generation_parameters["fallback_used"] is False


def test_scene_inference_ignores_technical_prompt_numbers() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": "A disciplined savings habit compounds."},
        {"visual_prompt": "Premium editorial image, 4k, vertical 9:16."},
    )

    assert scene_type is SceneType.IMAGE


def test_scene_inference_honors_valid_explicit_renderer_scene_type() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": "Consistency beats financial hype."},
        {"renderer_scene_type": "ComparisonScene"},
        require_source_backed_data=True,
    )

    assert scene_type is SceneType.CHART


def test_scene_inference_ignores_unknown_explicit_renderer_scene_type() -> None:
    scene_type = infer_scene_type(
        {"type": "body", "text": "Consistency beats financial hype."},
        {"renderer_scene_type": "UnknownScene"},
        require_source_backed_data=True,
    )

    assert scene_type is SceneType.IMAGE


def test_scene_qa_rejects_risk_status_without_explicit_passed_flag() -> None:
    result = _scene_qa(
        {
            "status": "generated",
            "image_text_audit": {
                "status": "risk",
                "risk_tokens": ["FAKE"],
                "reason": "pseudotext detected",
            },
        }
    )

    assert result.status == "failed"
    assert result.action == QAAction.REGENERATE_SCENE
    assert "pseudotext detected" in result.reasons


def test_scene_qa_rejects_unavailable_semantic_audit() -> None:
    result = _scene_qa(
        {
            "status": "generated",
            "image_visual_audit": {
                "status": "unavailable",
                "blockers": ["semantic_visual_audit_unavailable"],
            },
        }
    )

    assert result.status == "failed"
    assert result.action == QAAction.REGENERATE_SCENE
    assert result.reasons == ["semantic_visual_audit_unavailable"]


def test_manifest_normalizes_wealth_channel_without_embedded_profile(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["script"].pop("channel_profile")

    manifest = build_project_manifest(context, current_step="video", status="running")

    assert manifest.channel == "wealth_blueprint"


def test_manifest_hash_is_canonical_and_material_changes_are_detected(tmp_path: Path) -> None:
    assert deterministic_hash({"b": 2, "a": 1}) == deterministic_hash({"a": 1, "b": 2})
    context = _context(tmp_path)
    first = build_project_manifest(context, current_step="video", status="running")
    first_saved = save_project_manifest(context.artifact_dir / MANIFEST_FILENAME, first)

    context.artifacts["visuals"]["scenes"][0]["visual_prompt"] = "A materially different visual prompt."
    changed = build_project_manifest(context, current_step="video", status="running", created_at=first_saved.created_at)
    changed_saved = save_project_manifest(context.artifact_dir / MANIFEST_FILENAME, changed)

    assert first_saved.content_hash != changed_saved.content_hash
    assert first_saved.scenes[0].input_hash != changed_saved.scenes[0].input_hash


def test_manifest_hash_detects_timestamp_tampering(tmp_path: Path) -> None:
    context = _context(tmp_path)
    path = context.artifact_dir / MANIFEST_FILENAME
    save_project_manifest(path, build_project_manifest(context, current_step="video", status="running"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["updated_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        load_project_manifest(path)


def test_load_accepts_legacy_manifest_hash_that_excluded_timestamps(tmp_path: Path) -> None:
    context = _context(tmp_path)
    path = context.artifact_dir / MANIFEST_FILENAME
    saved = save_project_manifest(path, build_project_manifest(context, current_step="video", status="running"))
    payload = saved.model_dump(mode="json")
    legacy_payload = dict(payload)
    legacy_payload.pop("content_hash", None)
    legacy_payload.pop("created_at", None)
    legacy_payload.pop("updated_at", None)
    payload["content_hash"] = deterministic_hash(legacy_payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_project_manifest(path).job_id == context.job_id


def test_owner_approval_gates_block_final_without_recommending_regeneration() -> None:
    result = _overall_qa(
        {
            "auto_uploadable": False,
            "blocking_failures": ["Production voice approval", "Visual regression baseline"],
        }
    )

    assert result.status == "failed"
    assert result.action == QAAction.BLOCK_FINAL


def test_real_audio_failure_still_recommends_audio_regeneration_with_owner_gate() -> None:
    result = _overall_qa(
        {
            "auto_uploadable": False,
            "blocking_failures": ["Audio format", "Production voice approval"],
        }
    )

    assert result.action == QAAction.REGENERATE_AUDIO


def test_manifest_rejects_invalid_contract(tmp_path: Path) -> None:
    context = _context(tmp_path)
    manifest = build_project_manifest(context, current_step="video", status="running")
    payload = manifest.model_dump(mode="json")

    bad_version = {**payload, "schema_version": "0"}
    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(bad_version)

    duplicate = json.loads(json.dumps(payload))
    duplicate["scenes"][1]["scene_id"] = duplicate["scenes"][0]["scene_id"]
    with pytest.raises(ValidationError, match="scene_id values must be unique"):
        ProjectManifest.model_validate(duplicate)

    bad_hash = json.loads(json.dumps(payload))
    bad_hash["scenes"][0]["input_hash"] = "not-a-sha256"
    with pytest.raises(ValidationError):
        ProjectManifest.model_validate(bad_hash)

    with pytest.raises(ValidationError, match="end_sec must be greater"):
        TimeRange(start_sec=2, end_sec=2)


def test_load_rejects_tampered_manifest(tmp_path: Path) -> None:
    context = _context(tmp_path)
    path = context.artifact_dir / MANIFEST_FILENAME
    save_project_manifest(path, build_project_manifest(context, current_step="video", status="running"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["topic_title"] = "Tampered after persistence"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        load_project_manifest(path)


def test_load_accepts_integral_manifest_from_before_optional_field_extension(tmp_path: Path) -> None:
    context = _context(tmp_path)
    path = context.artifact_dir / MANIFEST_FILENAME
    save_project_manifest(path, build_project_manifest(context, current_step="video", status="running"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    for scene in payload["scenes"]:
        scene.pop("semantic_overlay_kind", None)
    payload["content_hash"] = compute_manifest_payload_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project_manifest(path)

    assert all(scene.semantic_overlay_kind is None for scene in loaded.scenes)


def test_legacy_split_artifacts_migrate_to_manifest_v1(tmp_path: Path) -> None:
    source = _context(tmp_path)
    for key, filename in (
        ("script", "script.json"),
        ("voice", "audio_timestamps.json"),
        ("visuals", "visual_plan.json"),
        ("video", "video_manifest.json"),
    ):
        (source.artifact_dir / filename).write_text(
            json.dumps(source.artifacts[key], ensure_ascii=False),
            encoding="utf-8",
        )

    migrated = migrate_legacy_artifacts(
        source.artifact_dir,
        job_id=source.job_id,
        account_id=source.account_id,
        topic_id=source.topic.id,
        topic_title=source.topic.title,
    )

    assert migrated.schema_version == "1.0"
    assert migrated.status == "legacy_migrated"
    assert migrated.current_step == "migrated"
    assert len(migrated.scenes) == 2
    assert load_project_manifest(source.artifact_dir / MANIFEST_FILENAME) == migrated
