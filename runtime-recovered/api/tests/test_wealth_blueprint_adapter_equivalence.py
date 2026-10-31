"""Equivalence: legacy VideoJob converter vs shared scene_plan_to_project_manifest."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from atnos.production.channels.wealth_blueprint._legacy_scene_plan_adapter import (
    legacy_scene_plan_to_manifest,
)
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.project_manifest import SceneType


def _still(tmp_path: Path) -> Path:
    path = tmp_path / "still.png"
    Image.new("RGB", (1080, 1920), (18, 24, 32)).save(path)
    return path


def _classic_plan() -> dict:
    """Focus-aligned fixtures where legacy focus map and layout registry agree."""
    return {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 4.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Keep more of your raise",
                "narration_excerpt": "Your paycheck feels bigger this year.",
            },
            {
                "scene_id": "s02",
                "start_sec": 4.0,
                "end_sec": 8.0,
                "layout_id": "big_number",
                "visual_focus": "number",
                "on_screen_text": "Save first",
                "narration_excerpt": "Save first, then spend what remains.",
                "number_value": "10%",
            },
            {
                "scene_id": "s03",
                "start_sec": 8.0,
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
            {
                "scene_id": "s04",
                "start_sec": 12.0,
                "end_sec": 16.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Start today",
                "narration_excerpt": "Start with one automatic transfer.",
            },
        ]
    }


def _semantic(manifest) -> list[dict]:
    rows = []
    for scene in manifest.scenes:
        rows.append(
            {
                "scene_id": scene.scene_id,
                "scene_type": scene.scene_type,
                "start": scene.timing.start_sec,
                "end": scene.timing.end_sec,
                "headline": scene.headline,
                "number_value": scene.number_value,
                "chart_values": list(scene.chart_values or []),
            }
        )
    return rows


def test_shared_adapter_matches_legacy_on_classic_focus_fixtures(tmp_path: Path):
    still = _still(tmp_path)
    plan = _classic_plan()
    kwargs = dict(
        job_id="eq-001",
        topic="Keep more of your raise",
        script="Your paycheck feels bigger this year. Save first. Invest. Start today.",
        plan=plan,
        selected_image=still,
        duration_hint=16.0,
    )
    legacy = legacy_scene_plan_to_manifest(**kwargs)
    shared = scene_plan_to_project_manifest(**kwargs)

    left = _semantic(legacy)
    right = _semantic(shared)
    assert [row["scene_id"] for row in left] == [row["scene_id"] for row in right]
    assert [row["scene_type"] for row in left] == [row["scene_type"] for row in right]
    assert [row["start"] for row in left] == [row["start"] for row in right]
    assert [row["end"] for row in left] == [row["end"] for row in right]
    assert [row["number_value"] for row in left] == [row["number_value"] for row in right]
    assert [row["chart_values"] for row in left] == [row["chart_values"] for row in right]
    assert left[0]["scene_type"] == SceneType.HOOK
    assert left[1]["scene_type"] == SceneType.NUMBER
    assert left[2]["scene_type"] == SceneType.CHART
    assert left[3]["scene_type"] == SceneType.CTA


def test_shared_and_legacy_both_emit_valid_remotion_contract(tmp_path: Path):
    still = _still(tmp_path)
    plan = _classic_plan()
    shared = scene_plan_to_project_manifest(
        job_id="eq-002",
        topic="Keep more of your raise",
        script="x",
        plan=plan,
        selected_image=still,
        duration_hint=16.0,
    )
    legacy = legacy_scene_plan_to_manifest(
        job_id="eq-002",
        topic="Keep more of your raise",
        script="x",
        plan=plan,
        selected_image=still,
        duration_hint=16.0,
    )
    for manifest in (shared, legacy):
        assert manifest.channel == "wealth_blueprint"
        assert len(manifest.scenes) >= 3
        assert all(s.timing.end_sec > s.timing.start_sec for s in manifest.scenes)
        assert all(isinstance(s.scene_type, SceneType) for s in manifest.scenes)


def test_video_job_uses_shared_adapter_not_inline_duplicate():
    import atnos.production.channels.wealth_blueprint.video_job as video_job

    src = Path(video_job.__file__).read_text(encoding="utf-8")
    assert "scene_plan_to_project_manifest" in src
    assert "def _scene_plan_to_manifest" not in src
