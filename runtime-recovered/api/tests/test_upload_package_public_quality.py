from __future__ import annotations

import asyncio
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import upload_package
from test_support import create_test_app


def test_upload_package_marks_motion_graphic_as_private_only_not_public_quality(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    context = _context(tmp_path)

    result = asyncio.run(upload_package.run(app.state.settings, context))

    assert result.payload["auto_uploadable"] is True
    assert result.payload["upload_decision"]["status"] == "ready_for_private_upload"
    assert result.payload["public_quality_ready"] is False
    assert result.payload["public_quality_blockers"] == ["premium_visual_backend_required_for_public"]
    assert result.payload["public_quality_evidence"]["video_backend"] == "motion_graphic"
    assert result.payload["public_quality_evidence"]["public_ready_visual_backend"] is False
    assert result.payload["public_quality_next_action"] == "configure_premium_visual_backend_and_regenerate"


def _context(tmp_path: Path) -> ProductionContext:
    artifact_dir = tmp_path / "job"
    artifact_dir.mkdir()
    final_video = artifact_dir / "final.mp4"
    thumbnail = artifact_dir / "thumbnail.png"
    subtitles = artifact_dir / "subtitles.srt"
    final_video.write_bytes(b"video")
    thumbnail.write_bytes(b"thumbnail")
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nHook\n", encoding="utf-8")
    style = {"used": True, "status": "test", "source": "test-style-guide.md"}
    return ProductionContext(
        job_id="prod-package-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="The One Money Rule Most Beginners Skip",
            description="",
            total_score=84,
        ),
        artifact_dir=artifact_dir,
        artifacts={
            "hook": {"style_guide": style},
            "script": {
                "style_guide": style,
                "hook": "Do this before investing",
                "opening_line": "Most beginners skip one rule.",
                "body": ["Check the habit before the return."],
                "cta": "Save this.",
                "tags": ["finance", "shorts"],
            },
            "visuals": {"style_guide": style},
            "voice": {
                "quality_tier": "primary",
                "voice_backend_grade": "premium",
                "voice_profile": {"language_code": "en"},
            },
            "video": {
                "backend": {"mode": "motion_graphic"},
                "clips": [
                    {"status": "generated", "backend": "motion_graphic"},
                    {"status": "generated", "backend": "motion_graphic"},
                    {"status": "generated", "backend": "motion_graphic"},
                ],
                "quality_plan": {
                    "generated_clip_count": 6,
                    "unique_composition_count": 6,
                    "composition_diversity_ratio": 1.0,
                    "passes_scene_concreteness_floor": True,
                    "scene_concreteness_risk_count": 0,
                    "passes_visual_relevance_floor": True,
                    "passes_topic_anchor_floor": True,
                    "topic_anchor_risk_count": 0,
                    "passes_generated_image_text_floor": True,
                    "generated_image_text_risk_count": 0,
                    "passes_generated_image_visual_floor": True,
                    "generated_image_visual_risk_count": 0,
                },
            },
            "qa": {
                "overall": "passed",
                "auto_uploadable": True,
                "blocking_failures": [],
                "blocking_failure_details": [],
                "required_checks": [
                    {
                        "name": "Visual variety",
                        "status": "pass",
                        "value": {"actual_visual_variety": {"status": "pass"}},
                    }
                ],
                "upload_decision": {
                    "status": "ready_for_private_upload",
                    "auto_uploadable": True,
                    "private_upload_allowed": True,
                    "required_failures": [],
                    "reason": "Test QA passed.",
                },
            },
            "assembly": {
                "output_file": str(final_video),
                "subtitle_file": str(subtitles),
            },
            "thumbnail": {"thumbnail_path": str(thumbnail)},
            "operating_standards": {"status": "ready"},
            "faceless_playbook": {"used": True, "template_key": "science_explainer"},
        },
    )
