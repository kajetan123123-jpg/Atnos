"""Deterministic ProjectManifest adapters for Wealth Blueprint Remotion."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from atnos.production.channels.wealth_blueprint.layout_registry import (
    LAYOUT_TO_SCENE_TYPE,
    assert_all_layouts_mapped,
    layout_to_scene_type,
)
from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_IDS
from atnos.production.channels.wealth_blueprint.manifest_adapters import (
    order_artifacts_to_project_manifest,
    scene_plan_to_project_manifest,
)
from atnos.production.project_manifest import ProjectManifest, SceneType


def _still(tmp_path: Path) -> Path:
    path = tmp_path / "still.png"
    Image.new("RGB", (1080, 1920), (20, 28, 36)).save(path)
    return path


def test_all_layout_ids_mapped():
    assert_all_layouts_mapped()
    assert set(LAYOUT_TO_SCENE_TYPE) >= set(LAYOUT_IDS)


def test_layout_to_scene_type_deterministic():
    assert layout_to_scene_type("big_number", has_number=True) == SceneType.NUMBER
    assert layout_to_scene_type("chart_focus", has_chart=True) == SceneType.CHART
    assert layout_to_scene_type("fullscreen_statement", index=0, total=3) == SceneType.HOOK
    assert layout_to_scene_type("fullscreen_statement", index=2, total=3) == SceneType.CTA
    assert layout_to_scene_type("character_left_text_right") == SceneType.IMAGE


def test_scene_plan_adapter_builds_valid_manifest(tmp_path: Path):
    still = _still(tmp_path)
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Keep more of your raise",
                "narration_excerpt": "Your paycheck feels bigger this year.",
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 7.0,
                "layout_id": "big_number",
                "visual_focus": "number",
                "on_screen_text": "Save first",
                "narration_excerpt": "Save first, then spend what remains.",
                "number_value": "10%",
                "number_label": "of income",
            },
            {
                "scene_id": "s03",
                "start_sec": 7.0,
                "end_sec": 12.0,
                "layout_id": "chart_focus",
                "visual_focus": "chart",
                "on_screen_text": "Compound growth",
                "narration_excerpt": "Invest consistently over time from $100 to $110.25.",
                "chart": {
                    "values": [100.0, 105.0, 110.25],
                    "labels": ["start", "mid", "later"],
                    "claim_ids": ["c_fixture_growth"],
                    "unit": "dollars",
                },
            },
        ]
    }
    manifest = scene_plan_to_project_manifest(
        job_id="adapter-test-001",
        topic="Keep more of your raise",
        script="Your paycheck feels bigger this year. Save first. Invest consistently.",
        plan=plan,
        selected_image=still,
        duration_hint=12.0,
    )
    assert isinstance(manifest, ProjectManifest)
    assert manifest.channel == "wealth_blueprint"
    assert len(manifest.scenes) == 3
    assert manifest.scenes[0].scene_type == SceneType.HOOK
    assert manifest.scenes[1].scene_type == SceneType.NUMBER
    assert manifest.scenes[2].scene_type == SceneType.CHART
    assert manifest.scenes[2].chart_values == [100.0, 105.0, 110.25]
    assert manifest.scenes[2].claim_ids == ["c_fixture_growth"]
    assert manifest.scenes[2].chart_unit == "dollars"
    assert all(scene.timing.end_sec > scene.timing.start_sec for scene in manifest.scenes)
    assert manifest.scenes[0].headline == "Keep more of your raise"
    assert manifest.scenes[0].caption == "Your paycheck feels bigger this year."
    assert manifest.scenes[0].caption != manifest.scenes[0].headline


def test_order_artifacts_adapter_builds_valid_manifest(tmp_path: Path):
    still = _still(tmp_path)
    script = {
        "segments": [
            {"id": "seg-a", "text": "Your paycheck feels bigger this year."},
            {"id": "seg-b", "text": "But wealth is what you keep."},
            {"id": "seg-c", "text": "Save first, then spend without guilt."},
        ]
    }
    voice = {
        "timestamps": [
            {"segment_id": "seg-a", "start_sec": 0.0, "end_sec": 2.5},
            {"segment_id": "seg-b", "start_sec": 2.5, "end_sec": 5.0},
            {"segment_id": "seg-c", "start_sec": 5.0, "end_sec": 8.0},
        ],
        "duration_seconds": 8.0,
    }
    visuals = {
        "scenes": [
            {"layout_id": "fullscreen_statement", "headline": "Raise"},
            {"layout_id": "quote_or_fact", "headline": "Keep"},
            {"layout_id": "fullscreen_statement", "headline": "Save"},
        ]
    }
    manifest = order_artifacts_to_project_manifest(
        job_id="order-adapter-001",
        topic_title="Keep more of your raise",
        script=script,
        voice=voice,
        visuals=visuals,
        selected_image=still,
    )
    assert isinstance(manifest, ProjectManifest)
    assert len(manifest.scenes) == 3
    assert manifest.scenes[0].scene_type == SceneType.HOOK
    assert manifest.scenes[-1].scene_type == SceneType.CTA
    assert abs(manifest.scenes[-1].timing.end_sec - 8.0) < 0.01
    assert manifest.scenes[0].headline == "Raise"
    assert manifest.scenes[0].caption == "Your paycheck feels bigger this year."
    assert manifest.scenes[0].caption != manifest.scenes[0].headline


def test_order_adapter_wires_caption_words_from_captions_stage(tmp_path: Path):
    still = _still(tmp_path)
    text = "Your paycheck feels bigger this year."
    words = [
        {
            "text": token,
            "segment_id": "seg-a",
            "start_sec": i * 0.3,
            "end_sec": (i + 1) * 0.3,
        }
        for i, token in enumerate(text.split())
    ]
    script = {"segments": [{"id": "seg-a", "text": text}]}
    voice = {
        "timestamps": [{"segment_id": "seg-a", "start_sec": 0.0, "end_sec": 2.5}],
        "duration_seconds": 2.5,
        "word_timestamps": words,
    }
    visuals = {"scenes": [{"layout_id": "fullscreen_statement", "headline": "Raise"}]}
    manifest = order_artifacts_to_project_manifest(
        job_id="order-adapter-captions",
        topic_title="Keep more of your raise",
        script=script,
        voice=voice,
        visuals=visuals,
        selected_image=still,
        captions={"groups": [words]},
        require_caption_words=True,
    )
    assert manifest.scenes[0].caption == text
    assert len(manifest.scenes[0].caption_words) == len(words)
    assert manifest.scenes[0].headline == "Raise"


def test_adapter_preserves_narration_text_without_inventing_chart_claims(tmp_path: Path):
    still = _still(tmp_path)
    narration = "Invest consistently over decades."
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Start",
                "narration_excerpt": "Start with the habit.",
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 7.0,
                "layout_id": "chart_focus",
                "visual_focus": "chart",
                "on_screen_text": "Growth",
                "narration_excerpt": narration,
                "chart": {
                    "values": [100.0, 110.25],
                    "labels": ["start", "later"],
                    "claim_ids": ["c_growth"],
                    "unit": "dollars",
                },
            },
            {
                "scene_id": "s03",
                "start_sec": 7.0,
                "end_sec": 11.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Act",
                "narration_excerpt": "Automate the transfer.",
            },
        ]
    }
    manifest = scene_plan_to_project_manifest(
        job_id="narration-preserve",
        topic="Growth",
        script=narration,
        plan=plan,
        selected_image=still,
        duration_hint=11.0,
    )
    chart_scene = manifest.scenes[1]
    assert chart_scene.scene_type == SceneType.CHART
    assert chart_scene.narration == narration
    assert "A 1" not in chart_scene.narration
    assert "B 0.6" not in chart_scene.narration
    assert chart_scene.chart_values == [100.0, 110.25]
    assert chart_scene.claim_ids == ["c_growth"]
    assert chart_scene.chart_unit == "dollars"


def test_adapter_blocks_quantitative_chart_without_claim_source(tmp_path: Path):
    still = _still(tmp_path)
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Hook",
                "narration_excerpt": "Your paycheck feels bigger.",
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 7.0,
                "layout_id": "chart_focus",
                "visual_focus": "chart",
                "on_screen_text": "Fake series",
                "narration_excerpt": "Compound growth rewards consistency.",
                # Values present but missing claim_ids → must not become ChartScene.
                "chart": {"values": [1.0, 2.4], "labels": ["Now", "Later"]},
            },
            {
                "scene_id": "s03",
                "start_sec": 7.0,
                "end_sec": 11.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "CTA",
                "narration_excerpt": "Start today.",
            },
        ]
    }
    manifest = scene_plan_to_project_manifest(
        job_id="block-no-claim",
        topic="No claim chart",
        script="x",
        plan=plan,
        selected_image=still,
        duration_hint=11.0,
    )
    mid = manifest.scenes[1]
    assert mid.scene_type != SceneType.CHART
    assert mid.chart_values == []
    assert mid.claim_ids == []
    assert mid.narration == "Compound growth rewards consistency."


def test_adapter_does_not_invent_default_chart_series(tmp_path: Path):
    still = _still(tmp_path)
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Hook",
                "narration_excerpt": "Open with a question.",
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 7.0,
                "layout_id": "comparison",
                "visual_focus": "comparison",
                "on_screen_text": "Compare",
                "narration_excerpt": "Compare keeping money versus spending it.",
                "chart": {"chart_type": "COMPARISON", "title": "Compare", "labels": ["A", "B"], "values": []},
            },
            {
                "scene_id": "s03",
                "start_sec": 7.0,
                "end_sec": 11.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Done",
                "narration_excerpt": "Close the loop.",
            },
        ]
    }
    manifest = scene_plan_to_project_manifest(
        job_id="no-invent",
        topic="No invent",
        script="x",
        plan=plan,
        selected_image=still,
        duration_hint=11.0,
    )
    mid = manifest.scenes[1]
    assert mid.scene_type != SceneType.CHART
    assert mid.chart_values == []
    assert mid.chart_labels == []
    assert "A 1" not in mid.narration
    assert "0.6" not in mid.narration
