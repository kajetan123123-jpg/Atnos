from __future__ import annotations

import hashlib
from pathlib import Path
from dataclasses import replace
import json
import math
import struct
import sys
import wave
from types import SimpleNamespace

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.qa_gate import (
    REQUIRED_QA_CHECK_NAMES,
    _check_audio_clipping,
    _check_audio_levels,
    _check_caption_safe_area,
    _check_excessive_silence,
    _check_final_audio_stream,
    _check_hook,
    _check_audio_format,
    _check_black_frames,
    _check_ollama_review,
    _check_operating_standards,
    _check_duration,
    _check_semantic_storyboard_quality,
    _check_visual_variety,
    _check_video_encoding,
    _check_visual_regression,
    _measure_prompt_motif_diversity,
    build_qa_report,
)
from atnos.production.steps import quality_gate
from atnos.production.steps import assembler as assembler_module
from atnos.production.steps.assembler import _render_final_video
from atnos.production.steps.video_generator import _write_placeholder_clip
from atnos.production.steps.voice_synthesis import _write_silent_wav
from test_support import create_test_app


def test_hook_gate_rejects_concise_but_generic_compound_interest_hook() -> None:
    check = _check_hook(
        {"hook": "Compound interest rewards time, not hype"},
        topic_title="Why Compound Interest Rewards Time More Than Hype",
        channel_profile_key="wealth_blueprint",
    )

    assert check["status"] == "fail"
    assert "generic" in check["detail"].casefold()


def test_hook_gate_accepts_concrete_source_backed_compound_interest_hook() -> None:
    check = _check_hook(
        {"hook": "How $100 becomes $110.25 without another deposit"},
        topic_title="Why Compound Interest Rewards Time More Than Hype",
        channel_profile_key="wealth_blueprint",
    )

    assert check["status"] == "pass"


def test_wealth_qa_fails_closed_when_visual_variety_is_not_measured() -> None:
    check = _check_visual_variety(
        _verified_video_manifest(),
        actual_visual_variety={"status": "not_measured", "reason": "ffmpeg_unavailable"},
        require_measurement=True,
    )

    assert check["status"] == "fail"
    assert "not measured" in check["detail"].lower()


def test_wealth_qa_rejects_visual_variety_pass_without_measurements() -> None:
    check = _check_visual_variety(
        _verified_video_manifest(),
        actual_visual_variety={"status": "pass"},
        require_measurement=True,
    )

    assert check["status"] == "fail"


def test_wealth_qa_fails_closed_when_video_manifest_is_missing() -> None:
    check = _check_visual_variety({}, require_measurement=True)

    assert check["status"] == "fail"
    assert check["value"] == "required_manifest_missing"


def test_wealth_qa_rejects_rearranged_versions_of_same_image_motif() -> None:
    prompts = [
        "transparent starting block and brass tokens in a tray",
        "transparent green value block inside a measurement frame",
        "translucent layer returning into a larger glass base",
        "coin on a vertical stack of transparent green blocks",
        "balance scale with coins and a padlock",
        "hourglass beside a row of coins",
    ]
    clips = [
        {
            "segment": index,
            "scene_type": "ImageScene",
            "status": "generated",
            "backend": "comfy_image_motion",
            "prompt": prompt,
        }
        for index, prompt in enumerate(prompts, start=1)
    ]
    check = _measure_prompt_motif_diversity(clips)

    assert check["status"] == "fail"
    assert check["dominant_motif"] == "transparent_growth_structure"
    assert check["dominant_ratio"] > check["max_dominant_ratio"]


def test_wealth_qa_accepts_distinct_image_motif_families() -> None:
    prompts = [
        "matching coins aligned inside one black precision tray",
        "two plain coin towers on a charcoal stone plinth",
        "brass flywheel linked to a taller stack",
        "small gear driving a larger gear on a machine plate",
        "balance scale with coins and a padlock",
        "hourglass beside a curved row of coins",
    ]
    clips = [
        {
            "segment": index,
            "scene_type": "ImageScene",
            "status": "generated",
            "backend": "comfy_image_motion",
            "prompt": prompt,
        }
        for index, prompt in enumerate(prompts, start=1)
    ]
    check = _measure_prompt_motif_diversity(clips)

    assert check["status"] == "pass"
    assert check["dominant_ratio"] <= check["max_dominant_ratio"]


def test_wealth_qa_fails_closed_when_semantic_audit_is_disabled(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    settings.shorts_semantic_visual_audit_enabled = False

    check = _check_semantic_storyboard_quality({}, settings=settings, required=True)

    assert check["status"] == "fail"
    assert check["value"] == "disabled"


def test_wealth_qa_rejects_semantic_audit_not_bound_to_storyboard(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True
    storyboard = tmp_path / "qa_storyboard_contact_sheet.jpg"
    storyboard.write_bytes(b"actual-storyboard")

    check = _check_semantic_storyboard_quality(
        {
            "status": "pass",
            "passed": True,
            "blockers": [],
            "storyboard_path": str(storyboard),
            "audit_input": {
                "source_sha256": hashlib.sha256(b"different-storyboard").hexdigest(),
                "source_size_bytes": storyboard.stat().st_size,
            },
        },
        settings=settings,
        required=True,
    )

    assert check["status"] == "fail"
    assert "semantic_storyboard_evidence_unverified" in check["value"]["blockers"]
    assert check["value"]["evidence"]["authenticated"] is False


def test_wealth_qa_rejects_semantic_pass_without_required_scores(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True
    storyboard = tmp_path / "qa_storyboard_contact_sheet.jpg"
    storyboard.write_bytes(b"actual-storyboard")

    check = _check_semantic_storyboard_quality(
        {
            "status": "pass",
            "passed": True,
            "blockers": [],
            "storyboard_path": str(storyboard),
            "audit_input": {
                "source_sha256": hashlib.sha256(storyboard.read_bytes()).hexdigest(),
                "source_size_bytes": storyboard.stat().st_size,
            },
        },
        settings=settings,
        required=True,
    )

    assert check["status"] == "fail"
    assert "topic_relevance_score_missing" in check["value"]["blockers"]
    assert "semantic_score_threshold_unverified" in check["value"]["blockers"]


def test_wealth_qa_rejects_semantic_scores_outside_ten_point_scale(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True
    storyboard = tmp_path / "qa_storyboard_contact_sheet.jpg"
    storyboard.write_bytes(b"actual-storyboard")

    check = _check_semantic_storyboard_quality(
        {
            "status": "pass",
            "passed": True,
            "blockers": [],
            "scores": {
                "topic_relevance_score": 100.0,
                "maturity_score": 8.0,
                "premium_style_score": 8.0,
                "clarity_score": 8.0,
            },
            "min_score": 7.0,
            "storyboard_path": str(storyboard),
            "audit_input": {
                "source_sha256": hashlib.sha256(storyboard.read_bytes()).hexdigest(),
                "source_size_bytes": storyboard.stat().st_size,
            },
        },
        settings=settings,
        required=True,
    )

    assert check["status"] == "fail"
    assert "topic_relevance_score_out_of_range" in check["value"]["blockers"]


def test_wealth_duration_contract_is_30_to_under_60_seconds() -> None:
    assert _check_duration({}, {"duration_s": 30}, strict_shorts_v1=True)["status"] == "pass"
    assert _check_duration({}, {"duration_s": 59.99}, strict_shorts_v1=True)["status"] == "pass"
    assert _check_duration({}, {"duration_s": 29.99}, strict_shorts_v1=True)["status"] == "fail"
    assert _check_duration({}, {"duration_s": 60}, strict_shorts_v1=True)["status"] == "fail"


def test_wealth_black_frame_measurement_unavailable_is_blocking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("atnos.production.qa_gate._ffmpeg_exe", lambda: None)

    check = _check_black_frames(tmp_path / "missing.mp4", required=True)

    assert check["status"] == "fail"
    assert check["value"] == "not_checked"


def test_wealth_ollama_review_unavailable_is_blocking(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)
    settings = create_test_app(tmp_path).state.settings
    monkeypatch.setattr(
        "atnos.production.qa_gate.httpx.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ollama down")),
    )

    check = _check_ollama_review(settings, context, context.artifacts["script"], required=True)

    assert check["status"] == "fail"
    assert check["value"] == "unavailable"


def test_audio_levels_uses_measured_final_media(monkeypatch, tmp_path: Path) -> None:
    media = tmp_path / "final.mp4"
    media.write_bytes(b"media")
    monkeypatch.setattr(
        "atnos.production.qa_gate._measure_media_loudness",
        lambda path: {"status": "measured", "input_i": -13.93, "input_tp": -1.47, "input_lra": 2.9},
    )

    check = _check_audio_levels(
        {"voice_profile": {"target_loudness_lufs": -14.0}, "normalization": "verified cache"},
        media_path=media,
    )

    assert check["status"] == "pass"
    assert check["value"]["measured"]["input_i"] == -13.93


def test_audio_levels_warns_when_measurement_and_evidence_are_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "atnos.production.qa_gate._measure_media_loudness",
        lambda path: {"status": "unavailable", "reason": "no audio stream"},
    )

    check = _check_audio_levels({}, media_path=tmp_path / "missing.mp4")

    assert check["status"] == "warn"


def test_qa_gate_passes_complete_short_package(tmp_path: Path, monkeypatch) -> None:
    subtitle = tmp_path / "subtitles.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:03,000\nHook\n", encoding="utf-8")
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"x" * 60000)
    audio = tmp_path / "narration.wav"
    _write_tone_wav(audio, 4)
    video = _write_test_final_video(tmp_path, audio)
    context = ProductionContext(
        job_id="prod-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Dlaczego inflacja niszczy budzet",
            description="",
            total_score=88,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "script": {
                "hook": "Dlaczego inflacja zjada twoje pieniadze?",
                "estimated_duration_s": 45,
                "description_draft": "finanse pieniadze budzet inflacja",
            },
            "voice": {
                "output_file": str(audio),
                "status": "generated",
                "quality_tier": "primary",
                "production_ready": True,
                "normalization": "target loudnorm I=-14 TP=-1.5 LRA=11",
                "timestamps": [{"segment_id": "s01", "start_sec": 0, "end_sec": 3, "text": "Hook"}],
            },
            "video": _verified_video_manifest(),
            "assembly": _safe_assembly(video, subtitle),
            "thumbnail": {"thumbnail_path": str(thumbnail)},
            "operating_standards": _operating_standards(),
        },
    )

    from atnos.production import qa_gate as module

    monkeypatch.setattr(module, "_measure_timeline_visual_change", _passing_timeline_measurement)
    monkeypatch.setattr(
        module,
        "_measure_media_loudness",
        lambda path: {"status": "measured", "input_i": -14.0, "input_tp": -1.5, "input_lra": 3.0},
    )
    report = build_qa_report(context)

    assert report["overall"] == "passed"
    assert report["auto_uploadable"] is True
    assert report["upload_decision"]["status"] == "ready_for_private_upload"
    assert report["upload_decision"]["private_upload_allowed"] is True
    assert report["upload_decision"]["required_failures"] == []
    assert report["required_check_count"] == len(REQUIRED_QA_CHECK_NAMES)
    assert [check["name"] for check in report["required_checks"]] == list(REQUIRED_QA_CHECK_NAMES)
    assert not report["blocking_failures"]
    assert report["operating_standards"]["used"] is True
    assert report["operating_standards"]["blocked_until_configured_count"] == 0


def test_qa_gate_blocks_when_required_visual_evidence_is_missing(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts.pop("video")

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert {
        "Visual topic relevance",
        "Scene concreteness",
        "Generated image text risk",
        "Generated image topic match",
    }.issubset(set(report["blocking_failures"]))


def test_qa_gate_blocks_missing_operating_standards_evidence(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts.pop("operating_standards")

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Operating standards evidence" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Operating standards evidence")
    assert check["status"] == "fail"
    assert check["value"]["used"] is False
    assert "missing" in check["detail"].lower()


def test_operating_standards_reports_built_in_baseline_truthfully() -> None:
    payload = _operating_standards()
    payload["status"] = "baseline"
    payload["source"] = {"path": None, "exists": False}

    check = _check_operating_standards(payload)

    assert check["status"] == "pass"
    assert check["value"]["source_kind"] == "built_in_baseline"
    assert "built-in" in check["detail"].lower()
    assert "attached" not in check["detail"].lower()


def test_visual_regression_blocks_until_reference_is_accepted(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    context = _complete_context(tmp_path)
    context.topic = replace(context.topic, content_profile="wealth_blueprint")
    (tmp_path / "project_manifest.json").write_text(
        json.dumps({"channel": "wealth_blueprint"}),
        encoding="utf-8",
    )

    check = _check_visual_regression(
        app.state.settings,
        context,
        Path(context.artifacts["assembly"]["output_file"]),
        context.artifacts["assembly"],
    )

    assert check["status"] == "fail"
    assert check["value"]["status"] == "not_configured"
    assert "blocked" in check["detail"]


def test_visual_regression_blocks_final_after_baseline_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    context = _complete_context(tmp_path)
    context.topic = replace(context.topic, content_profile="wealth_blueprint")
    (tmp_path / "project_manifest.json").write_text(
        json.dumps({"channel": "wealth_blueprint"}),
        encoding="utf-8",
    )
    baseline = (
        app.state.settings.data_dir
        / "production"
        / "styles"
        / "WEALTH_BLUEPRINT_STYLE_V1"
        / "visual_regression_baseline.json"
    )
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "atnos.production.qa_gate.load_project_manifest",
        lambda *_args, **_kwargs: type("Manifest", (), {"channel": "wealth_blueprint"})(),
    )
    monkeypatch.setattr(
        "atnos.production.qa_gate.compare_visual_regression",
        lambda *_args, **_kwargs: {
            "status": "fail",
            "passed": False,
            "action": "BLOCK_FINAL",
            "reasons": ["visual style drift: font_assets"],
        },
    )
    monkeypatch.setattr(
        "atnos.production.qa_gate.verify_remotion_assembly_provenance",
        lambda *_args, **_kwargs: {"verified": True, "checks": {}},
    )

    check = _check_visual_regression(
        app.state.settings,
        context,
        Path(context.artifacts["assembly"]["output_file"]),
        context.artifacts["assembly"],
    )

    assert check["status"] == "fail"
    assert check["value"]["action"] == "BLOCK_FINAL"


def test_visual_regression_rejects_manifest_channel_bypass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    context = _complete_context(tmp_path)
    context.topic = replace(context.topic, content_profile="wealth_blueprint")
    (tmp_path / "project_manifest.json").write_text("{}", encoding="utf-8")
    baseline = (
        app.state.settings.data_dir
        / "production"
        / "styles"
        / "WEALTH_BLUEPRINT_STYLE_V1"
        / "visual_regression_baseline.json"
    )
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "atnos.production.qa_gate.load_project_manifest",
        lambda *_args, **_kwargs: type("Manifest", (), {"channel": "dark_chronicles"})(),
    )

    check = _check_visual_regression(
        app.state.settings,
        context,
        Path(context.artifacts["assembly"]["output_file"]),
        context.artifacts["assembly"],
    )

    assert check["status"] == "fail"
    assert check["value"]["status"] == "channel_mismatch"
    assert check["value"]["profile_channel"] == "wealth_blueprint"
    assert check["value"]["manifest_channel"] == "dark_chronicles"


def test_visual_regression_blocks_tampered_final_assembly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    context = _complete_context(tmp_path)
    context.topic = replace(context.topic, content_profile="wealth_blueprint")
    (tmp_path / "project_manifest.json").write_text("{}", encoding="utf-8")
    baseline = (
        app.state.settings.data_dir
        / "production"
        / "styles"
        / "WEALTH_BLUEPRINT_STYLE_V1"
        / "visual_regression_baseline.json"
    )
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "atnos.production.qa_gate.load_project_manifest",
        lambda *_args, **_kwargs: type("Manifest", (), {"channel": "wealth_blueprint"})(),
    )
    monkeypatch.setattr(
        "atnos.production.qa_gate.verify_remotion_assembly_provenance",
        lambda *_args, **_kwargs: {
            "verified": False,
            "reason": "failed checks: final_output_file",
            "checks": {"final_output_file": False},
        },
    )
    monkeypatch.setattr(
        "atnos.production.qa_gate.compare_visual_regression",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Pixel comparison must not run after provenance failure")
        ),
    )

    check = _check_visual_regression(
        app.state.settings,
        context,
        Path(context.artifacts["assembly"]["output_file"]),
        context.artifacts["assembly"],
    )

    assert check["status"] == "fail"
    assert check["value"]["status"] == "assembly_provenance_failed"
    assert check["value"]["verification"]["checks"]["final_output_file"] is False


def test_qa_gate_blocks_failed_semantic_storyboard_audit(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True
    context = _complete_context(tmp_path)
    context.artifacts["semantic_storyboard_audit"] = {
        "status": "fail",
        "passed": False,
        "scores": {
            "topic_relevance_score": 3.0,
            "maturity_score": 6.0,
            "premium_style_score": 4.0,
            "clarity_score": 5.0,
        },
        "blockers": ["semantic_off_topic", "semantic_repetitive"],
        "storyboard_path": str(tmp_path / "qa_storyboard_contact_sheet.jpg"),
    }

    report = build_qa_report(context, settings=settings)

    assert "Semantic storyboard quality" in report["blocking_failures"]
    check = next(
        item for item in report["required_checks"] if item["name"] == "Semantic storyboard quality"
    )
    assert check["status"] == "fail"
    assert set(check["value"]["blockers"]) == {
        "semantic_off_topic",
        "semantic_repetitive",
        "topic_relevance_score_below_floor",
        "maturity_score_below_floor",
        "premium_style_score_below_floor",
        "clarity_score_below_floor",
        "semantic_score_threshold_unverified",
        "semantic_storyboard_evidence_unverified",
    }


def test_qa_gate_blocks_unresolved_operating_standards_items(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["operating_standards"] = _operating_standards(
        project_type="investment",
        blocked=True,
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Operating standards evidence" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Operating standards evidence")
    assert check["value"]["blocked_until_configured_count"] == 1
    assert "unresolved blocked items" in check["detail"]


def test_qa_gate_blocks_missing_thumbnail(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="prod-test",
        account_id="acct-1",
        topic=ApprovedTopic("topic-1", "acct-1", "Temat", "", 50),
        artifact_dir=tmp_path,
        artifacts={
            "script": {"hook": "Dlaczego?", "estimated_duration_s": 45},
            "voice": {"timestamps": []},
            "assembly": {"subtitle_file": str(tmp_path / "missing.srt")},
            "thumbnail": {"thumbnail_path": str(tmp_path / "missing.png")},
        },
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert report["upload_decision"]["status"] == "blocked"
    assert report["upload_decision"]["private_upload_allowed"] is False
    assert "Thumbnail present" in report["upload_decision"]["required_failures"]
    assert report["blocking_failure_details"][0]["name"] == "Final MP4 present"
    assert "Thumbnail present" in report["blocking_failures"]


def test_qa_gate_blocks_placeholder_thumbnail_manifest(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["thumbnail"].update(
        {
            "status": "placeholder_ready",
            "fallback_used": True,
            "comfyui_generated": False,
        }
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Thumbnail present" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Thumbnail present")
    assert "placeholder" in check["detail"].lower()


def test_qa_gate_blocks_failed_thumbnail_quality_audit(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["thumbnail"]["thumbnail_quality"] = {
        "status": "fail",
        "passed": False,
        "blockers": ["thumbnail_blurry_or_flat", "thumbnail_low_contrast"],
        "metrics": {"contrast_range": 8, "edge_strength": 0.4},
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Thumbnail present" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Thumbnail present")
    assert check["value"]["quality_audit_present"] is True
    assert "thumbnail_blurry_or_flat" in check["value"]["quality_blockers"]
    assert "quality audit failed" in check["detail"].lower()


def test_qa_gate_blocks_local_template_thumbnail_for_review(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["thumbnail"].update(
        {
            "status": "generated",
            "primary": "edge_tts",
            "fallback_used": False,
            "local_generated": True,
            "thumbnail_generation_backend": "local_pillow_thumbnail",
            "thumbnail_quality": {"status": "pass", "passed": True, "blockers": [], "metrics": {}},
        }
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Thumbnail present" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Thumbnail present")
    assert check["value"]["template_thumbnail"] is True
    assert "local template fallback" in check["detail"].lower()


def test_qa_gate_blocks_cached_thumbnail_for_review(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["thumbnail"].update(
        {
            "status": "generated",
            "fallback_used": False,
            "comfyui_generated": True,
            "copied_cached_thumbnail": True,
            "thumbnail_generation_backend": "cached_comfyui_thumbnail",
            "thumbnail_quality": {"status": "pass", "passed": True, "blockers": [], "metrics": {}},
        }
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Thumbnail present" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Thumbnail present")
    assert check["value"]["copied_cached_thumbnail"] is True
    assert "cached comfyui" in check["detail"].lower()


def test_qa_gate_blocks_placeholder_video_manifest(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "placeholder_ready",
        "fallback_used": True,
        "backend": {"mode": "placeholder"},
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Final MP4 present" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Final MP4 present")
    assert "placeholder" in check["detail"].lower()


def test_qa_gate_blocks_single_looped_generated_clip(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [{"segment": 1, "status": "generated", "backend": "ltx"}],
        "quality_plan": {
            "generated_clip_count": 1,
            "placeholder_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": False,
        },
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Visual variety" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Visual variety")
    assert check["value"]["generated_clip_count"] == 1
    assert check["value"]["required_real_clips"] == 3


def test_qa_gate_blocks_repeated_visual_composition(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {"segment": 1, "status": "generated", "backend": "motion_graphic"},
            {"segment": 2, "status": "generated", "backend": "motion_graphic"},
            {"segment": 3, "status": "generated", "backend": "motion_graphic"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "composition_signatures": [
                "wealth:finance:budget:receipt_phone_card",
                "wealth:finance:budget:receipt_phone_card",
                "wealth:finance:budget:receipt_phone_card",
            ],
            "unique_composition_count": 1,
            "required_unique_compositions": 3,
            "composition_diversity_ratio": 0.333,
            "passes_composition_diversity_floor": False,
        },
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Visual variety" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Visual variety")
    assert check["value"]["unique_composition_count"] == 1
    assert check["value"]["required_unique_compositions"] == 3
    assert "compositionally similar" in check["detail"]


def test_qa_gate_blocks_actual_rendered_clip_similarity(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import qa_gate as module

    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {"segment": 1, "status": "generated", "backend": "wan", "file": str(tmp_path / "s01.mp4")},
            {"segment": 2, "status": "generated", "backend": "wan", "file": str(tmp_path / "s02.mp4")},
            {"segment": 3, "status": "generated", "backend": "wan", "file": str(tmp_path / "s03.mp4")},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "composition_signatures": [
                "wealth:finance:budget:receipt_phone_card",
                "wealth:finance:budget:split_chart_phone",
                "wealth:finance:budget:cash_leak_closeup",
            ],
            "unique_composition_count": 3,
            "required_unique_compositions": 3,
            "composition_diversity_ratio": 1.0,
            "passes_composition_diversity_floor": True,
        },
    }
    monkeypatch.setattr(
        module,
        "_measure_actual_clip_visual_variety",
        lambda _manifest: {
            "status": "fail",
            "mean_hash_distance": 0.138,
            "mean_top_crop_pixel_diff": 0.087,
            "thresholds": {
                "min_mean_hash_distance": 0.16,
                "min_mean_top_crop_pixel_diff": 0.10,
            },
            "reason": "clip_frames_are_too_similar_after_sampling_actual_rendered_media",
        },
    )

    report = module.build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Visual variety" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Visual variety")
    assert check["value"]["unique_composition_count"] == 3
    assert check["value"]["actual_visual_variety"]["status"] == "fail"
    assert "actual rendered clips" in check["detail"].lower()


def test_qa_gate_blocks_low_timeline_visual_change(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import qa_gate as module

    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {"segment": 1, "status": "generated", "backend": "motion_graphic", "file": str(tmp_path / "s01.mp4")},
            {"segment": 2, "status": "generated", "backend": "motion_graphic", "file": str(tmp_path / "s02.mp4")},
            {"segment": 3, "status": "generated", "backend": "motion_graphic", "file": str(tmp_path / "s03.mp4")},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "composition_signatures": [
                "wealth:finance:budget:receipt_phone_card",
                "wealth:finance:budget:split_chart_phone",
                "wealth:finance:budget:cash_leak_closeup",
            ],
            "unique_composition_count": 3,
            "required_unique_compositions": 3,
            "composition_diversity_ratio": 1.0,
            "passes_composition_diversity_floor": True,
        },
    }
    monkeypatch.setattr(
        module,
        "_measure_actual_clip_visual_variety",
        lambda _manifest: {"status": "pass", "reason": "test"},
    )
    monkeypatch.setattr(
        module,
        "_measure_timeline_visual_change",
        lambda *_args: {
            "status": "fail",
            "mean_consecutive_frame_diff": 0.0805,
            "low_change_ratio": 0.667,
            "thresholds": {
                "min_mean_consecutive_frame_diff": 0.11,
                "max_low_change_ratio": 0.4,
            },
            "reason": "timeline_frames_are_too_similar_for_short_form_pacing",
        },
    )

    report = module.build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Timeline visual change" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Timeline visual change")
    assert check["value"]["status"] == "fail"
    assert "too visually static" in check["detail"]


def test_qa_gate_blocks_low_visual_topic_relevance(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {"segment": 1, "status": "generated", "backend": "motion_graphic"},
            {"segment": 2, "status": "generated", "backend": "motion_graphic"},
            {"segment": 3, "status": "generated", "backend": "motion_graphic"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "visual_relevance_scores": [0.0, 0.22, 0.36],
            "avg_visual_relevance_score": 0.193,
            "passes_visual_relevance_floor": False,
        },
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Visual topic relevance" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Visual topic relevance")
    assert check["value"]["passes_visual_relevance_floor"] is False
    assert "too generic" in check["detail"]


def test_qa_gate_blocks_render_below_measured_reference_pacing(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)
    settings = create_test_app(tmp_path).state.settings
    profile_path = settings.data_dir / "learning" / "active-reference-shorts" / "latest_reference_profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        '{"source":"active_app_reference_channels","sample_count":4,'
        '"median_first_cut_sec":2.0,"median_avg_cut_interval_sec":1.5,"median_cuts_per_10s":3.0}',
        encoding="utf-8",
    )
    from atnos.production import qa_gate as module

    monkeypatch.setattr(
        module,
        "analyze_video_visual_profile",
        lambda *_args, **_kwargs: {
            "frame_analysis_status": "analyzed",
            "first_cut_sec": 4.0,
            "avg_cut_interval_sec": 5.0,
            "cuts_per_10s": 0.8,
        },
    )

    report = module.build_qa_report(context, settings=settings)

    assert report["overall"] == "failed"
    assert "Reference pacing floor" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Reference pacing floor")
    assert check["value"]["checks"] == {
        "first_cut_ok": False,
        "avg_cut_interval_ok": False,
        "cut_density_ok": False,
        "no_isolated_flashes": True,
    }


def test_reference_pacing_rejects_isolated_flash_even_when_cut_density_passes(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import qa_gate as module

    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(
        module,
        "analyze_video_visual_profile",
        lambda *_args, **_kwargs: {
            "frame_analysis_status": "analyzed",
            "first_cut_sec": 0.5,
            "avg_cut_interval_sec": 1.5,
            "cuts_per_10s": 4.0,
            "flash_events_per_10s": 0.5,
        },
    )
    profile = SimpleNamespace(
        available=True,
        median_cuts_per_10s=3.0,
        median_avg_cut_interval_sec=1.5,
        hook_target_sec=lambda: 1.0,
        source="active_app_reference_channels",
        sample_count=3,
    )

    result = module._measure_reference_pacing(video, profile)

    assert result["status"] == "fail"
    assert result["checks"]["first_cut_ok"] is True
    assert result["checks"]["cut_density_ok"] is True
    assert result["checks"]["no_isolated_flashes"] is False


def test_qa_gate_blocks_readable_text_in_generated_images(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {
                "segment": 1,
                "status": "generated",
                "backend": "openai_image_motion",
                "image_text_audit": {
                    "status": "risk",
                    "text": "SIGMA",
                    "risk_tokens": ["SIGMA"],
                },
            },
            {"segment": 2, "status": "generated", "backend": "openai_image_motion"},
            {"segment": 3, "status": "generated", "backend": "openai_image_motion"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "passes_visual_relevance_floor": True,
            "generated_image_text_audits": [
                {
                    "status": "risk",
                    "text": "SIGMA",
                    "risk_tokens": ["SIGMA"],
                }
            ],
            "generated_image_text_risk_count": 1,
            "passes_generated_image_text_floor": False,
        },
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Generated image text risk" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Generated image text risk")
    assert check["value"]["generated_image_text_risk_count"] == 1
    assert "readable text" in check["detail"]


def test_qa_gate_blocks_off_profile_generated_images(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {"segment": 1, "status": "generated", "backend": "openai_image_motion"},
            {"segment": 2, "status": "generated", "backend": "openai_image_motion"},
            {"segment": 3, "status": "generated", "backend": "openai_image_motion"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "passes_visual_relevance_floor": True,
            "passes_generated_image_text_floor": True,
            "generated_image_text_risk_count": 0,
            "generated_image_visual_audits": [
                {
                    "status": "fail",
                    "channel_profile": "wealth_blueprint",
                    "prompt_relevance_score": 0.0,
                    "failures": ["prompt_not_topic_specific", "finance_visual_too_dark"],
                }
            ],
            "generated_image_visual_risk_count": 1,
            "passes_generated_image_visual_floor": False,
        },
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Generated image topic match" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Generated image topic match")
    assert check["value"]["generated_image_visual_risk_count"] == 1
    assert "off-profile" in check["detail"]


def test_qa_gate_blocks_abstract_scene_concreteness_failure(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["video"] = {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {"segment": 1, "status": "generated", "backend": "openai_image_motion"},
            {"segment": 2, "status": "generated", "backend": "openai_image_motion"},
            {"segment": 3, "status": "generated", "backend": "openai_image_motion"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "passes_visual_relevance_floor": True,
            "passes_scene_concreteness_floor": False,
            "scene_concreteness_risk_count": 1,
            "scene_concreteness_audits": [
                {
                    "status": "fail",
                    "failures": ["abstract_or_background_only", "not_enough_physical_objects"],
                }
            ],
            "passes_generated_image_text_floor": True,
            "generated_image_text_risk_count": 0,
            "passes_generated_image_visual_floor": True,
            "generated_image_visual_risk_count": 0,
        },
    }

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Scene concreteness" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Scene concreteness")
    assert check["value"]["scene_concreteness_risk_count"] == 1
    assert "too abstract" in check["detail"]


def test_qa_gate_blocks_silent_voice_fallback_manifest(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["voice"].update(
        {
            "status": "generated_fallback_silence",
            "quality_tier": "placeholder_silence",
            "production_ready": False,
        }
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Audio format" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Audio format")
    assert "placeholder" in check["detail"].lower()


def test_qa_gate_blocks_partial_silent_voice_fallback_manifest(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["voice"].update(
        {
            "status": "generated_partial_fallback_silence",
            "quality_tier": "primary",
            "production_ready": False,
            "silent_segment_ids": ["s02"],
        }
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Audio format" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Audio format")
    assert "silence" in check["detail"].lower()


def test_qa_gate_blocks_pyttsx3_system_voice_fallback_manifest(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["voice"].update(
        {
            "status": "generated",
            "quality_tier": "offline_system_fallback",
            "production_ready": False,
        }
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Audio format" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Audio format")
    assert "pyttsx3" in check["detail"].lower()


def test_qa_gate_blocks_legacy_voice_manifest_without_production_ready(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["voice"].pop("production_ready", None)

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert "Audio format" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Audio format")
    assert "production_ready" in check["detail"]


def test_qa_gate_labels_edge_tts_as_neural_fallback_without_blocking(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)
    context.artifacts["voice"].update(
        {
            "status": "generated",
            "quality_tier": "cloud_neural_fallback",
            "voice_backend_grade": "acceptable_neural_fallback",
            "production_ready": True,
            "voice_profile": {
                "language_code": "en",
                "edge_tts_voice": "en-US-ChristopherNeural",
                "edge_tts_rate": "+2%",
            },
        }
    )

    from atnos.production import qa_gate as module

    monkeypatch.setattr(module, "_measure_timeline_visual_change", _passing_timeline_measurement)
    monkeypatch.setattr(
        module,
        "_measure_media_loudness",
        lambda path: {"status": "measured", "input_i": -14.0, "input_tp": -1.5, "input_lra": 3.0},
    )
    report = build_qa_report(context)

    assert report["overall"] == "passed"
    assert report["auto_uploadable"] is True
    check = next(item for item in report["required_checks"] if item["name"] == "Audio format")
    assert check["status"] == "pass"
    assert check["value"]["quality_tier"] == "cloud_neural_fallback"
    assert check["value"]["voice_backend_grade"] == "acceptable_neural_fallback"
    assert "Edge neural fallback" in check["detail"]


def test_audio_format_reports_actual_f5_identity_without_edge_fallback_metadata(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    audio_path = Path(context.artifacts["voice"]["output_file"])
    check = _check_audio_format(
        audio_path,
        {
            "status": "generated",
            "primary": "f5_tts_single_pass",
            "quality_tier": "primary",
            "voice_backend_grade": "premium",
            "production_ready": True,
            "voice_profile": {
                "language_code": "en",
                "edge_tts_voice": "en-US-ChristopherNeural",
                "edge_tts_rate": "+2%",
            },
            "voice_identity": {
                "provider_family": "f5_tts",
                "identity_hash": "a" * 64,
                "speed": 1.15,
            },
        },
    )

    assert check["status"] == "pass"
    assert check["value"]["engine"] == "f5_tts_single_pass"
    assert check["value"]["voice"] == "f5_tts"
    assert check["value"]["rate"] == 1.15
    assert check["value"]["voice_identity_hash"] == "a" * 64
    assert "ChristopherNeural" not in json.dumps(check)


def test_qa_gate_uses_ollama_review_when_available(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)
    settings = create_test_app(tmp_path).state.settings

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "response": (
                    '{"passed": true, "score": 8.5, "issues": [], '
                    '"copyright_risk": "low", "brand_fit": "high"}'
                )
            }

    captured = {}

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    from atnos.production import qa_gate as module

    monkeypatch.setattr(module.httpx, "post", fake_post)

    report = build_qa_report(context, settings=settings)

    check = next(item for item in report["checks"] if item["name"] == "Ollama QA review")
    assert check["status"] == "pass"
    assert check["value"]["score"] == 8.5
    assert captured["url"].endswith("/api/generate")
    assert "format" in captured["json"]
    assert captured["json"]["keep_alive"] == "0s"


def test_qa_gate_blocks_when_ollama_review_rejects(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)
    settings = create_test_app(tmp_path).state.settings

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "response": (
                    '{"passed": false, "score": 3, "issues": ["clickbait"], '
                    '"copyright_risk": "medium", "brand_fit": "low"}'
                )
            }

    from atnos.production import qa_gate as module

    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: FakeResponse())

    report = build_qa_report(context, settings=settings)

    assert report["overall"] == "failed"
    assert "Ollama QA review" in report["blocking_failures"]


def test_qa_gate_warns_when_ollama_unavailable_in_runtime(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)
    settings = create_test_app(tmp_path).state.settings

    from atnos.production import qa_gate as module

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(module.httpx, "post", unavailable)
    monkeypatch.setattr(module, "_measure_timeline_visual_change", _passing_timeline_measurement)

    report = build_qa_report(context, settings=settings)

    check = next(item for item in report["required_checks"] if item["name"] == "Ollama QA review")
    assert check["status"] == "warn"
    assert report["overall"] == "warning"
    assert report["auto_uploadable"] is True
    assert "Ollama QA review" in report["warnings"]
    assert "Ollama QA review" not in report["blocking_failures"]


def test_assembler_render_requires_upload_ready_ffprobe_result(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "narration.wav"
    _write_silent_wav(audio, 2)
    final_video = tmp_path / "final_short.mp4"

    def fake_run(*_args, **_kwargs):
        final_video.write_bytes(b"x" * 8192)
        return assembler_module.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(assembler_module, "_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(assembler_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        assembler_module,
        "_probe_final_video",
        lambda _path: {"ok": False, "reason": "synthetic probe failure"},
    )

    render = _render_final_video(
        raw_video_path=str(tmp_path / "raw_video.mp4"),
        audio_path=str(audio),
        output_path=final_video,
    )

    assert final_video.exists()
    assert render["ok"] is False
    assert render["reason"] == "synthetic probe failure"


def test_assembler_resamples_final_upload_audio_to_48khz(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "narration.wav"
    _write_silent_wav(audio, 2)
    final_video = tmp_path / "final_short.mp4"
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        final_video.write_bytes(b"x" * 8192)
        return assembler_module.subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(assembler_module, "_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(assembler_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        assembler_module,
        "_probe_final_video",
        lambda _path: {"ok": True, "sample_rate": 48000, "channels": 2},
    )

    render = _render_final_video(
        raw_video_path=str(tmp_path / "raw_video.mp4"),
        audio_path=str(audio),
        output_path=final_video,
    )

    command = captured["command"]
    assert isinstance(command, list)
    sample_rate_index = command.index("-ar")
    assert command[sample_rate_index + 1] == "48000"
    assert render["ok"] is True


def test_final_audio_gate_rejects_44100hz_and_accepts_48000hz() -> None:
    rejected = _check_final_audio_stream({"audio_codec": "aac", "sample_rate": 44100, "channels": 2})
    accepted = _check_final_audio_stream({"audio_codec": "aac", "sample_rate": 48000, "channels": 2})

    assert rejected["status"] == "fail"
    assert accepted["status"] == "pass"


def test_video_encoding_requires_exact_upload_contract() -> None:
    accepted = _check_video_encoding(
        {"width": 1080, "height": 1920, "fps": 30.0, "video_codec": "h264", "pixel_format": "yuv420p"}
    )
    rejected = _check_video_encoding(
        {"width": 720, "height": 1280, "fps": 29.97, "video_codec": "hevc", "pixel_format": "yuv444p"}
    )

    assert accepted["status"] == "pass"
    assert rejected["status"] == "fail"
    assert not all(rejected["value"]["checks"].values())


def test_audio_clipping_uses_measured_pcm_peak(tmp_path: Path) -> None:
    safe_audio = tmp_path / "safe.wav"
    clipped_audio = tmp_path / "clipped.wav"
    _write_tone_wav(safe_audio, 0.25, amplitude=0.5)
    _write_tone_wav(clipped_audio, 0.25, amplitude=1.0)

    assert _check_audio_clipping(safe_audio)["status"] == "pass"
    clipped = _check_audio_clipping(clipped_audio)
    assert clipped["status"] == "fail"
    assert clipped["value"]["peak_dbfs"] > -0.5


def test_excessive_silence_blocks_empty_narration(tmp_path: Path) -> None:
    spoken = tmp_path / "spoken.wav"
    silent = tmp_path / "silent.wav"
    _write_tone_wav(spoken, 0.25)
    _write_silent_wav(silent, 1)

    assert _check_excessive_silence(spoken)["status"] == "pass"
    assert _check_excessive_silence(silent)["status"] == "fail"


def test_caption_safe_area_requires_versioned_layout_metadata() -> None:
    accepted = _check_caption_safe_area(_safe_assembly(Path("final.mp4"), Path("subtitles.srt")))
    rejected = _check_caption_safe_area(
        {
            "subtitle_style": {
                "position": "lower_third_center",
                "frame_px": {"width": 1080, "height": 1920},
                "safe_area_px": {"left": 20, "right": 20, "top": 20, "bottom": 20},
            }
        }
    )

    assert accepted["status"] == "pass"
    assert rejected["status"] == "fail"


def test_quality_gate_step_allows_private_review_when_runtime_ollama_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)
    settings = create_test_app(tmp_path).state.settings

    from atnos.production import qa_gate as module

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(module.httpx, "post", unavailable)
    monkeypatch.setattr(module, "_measure_timeline_visual_change", _passing_timeline_measurement)

    import asyncio

    result = asyncio.run(quality_gate.run(settings, context))

    assert result.artifact_path.exists()
    assert result.payload["overall"] == "warning"
    assert result.payload["auto_uploadable"] is True
    assert "Ollama QA review" in result.payload["warnings"]
    assert "Ollama QA review" not in result.payload["blocking_failures"]


def test_qa_gate_blocks_private_upload_when_black_frames_are_detected(tmp_path: Path, monkeypatch) -> None:
    context = _complete_context(tmp_path)

    from atnos.production import qa_gate as module

    monkeypatch.setattr(
        module,
        "_check_black_frames",
        lambda _path, **_kwargs: {
            "name": "Black frames",
            "status": "fail",
            "value": "detected",
            "detail": "synthetic blocking failure",
        },
    )

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert report["auto_uploadable"] is False
    assert report["upload_decision"]["status"] == "blocked"
    assert report["upload_decision"]["private_upload_allowed"] is False
    assert "Black frames" in report["upload_decision"]["required_failures"]
    assert "Black frames" in report["blocking_failures"]
    assert "Black frames" not in report["advisory_failures"]


def test_visual_reference_match_warns_when_brightness_is_far_from_profile(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import qa_gate as module
    from atnos.production import reference_profile_builder

    class FakeReferenceProfile:
        available = True
        median_brightness = 0.15
        median_saturation = 0.2

    monkeypatch.setattr(
        reference_profile_builder,
        "analyze_video_visual_profile",
        lambda *_args, **_kwargs: {
            "frame_analysis_status": "analyzed",
            "brightness": 0.72,
            "saturation": 0.25,
            "frame_sample_count": 12,
        },
    )

    check = module._check_visual_reference_match(tmp_path / "final.mp4", FakeReferenceProfile())

    assert check["name"] == "Visual reference match"
    assert check["status"] == "warn"
    assert check["value"]["brightness_delta"] > 0.22


def test_visual_reference_match_measures_frames_without_reference_profile(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import qa_gate as module
    from atnos.production import reference_profile_builder
    from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE

    monkeypatch.setattr(
        reference_profile_builder,
        "analyze_video_visual_profile",
        lambda *_args, **_kwargs: {
            "frame_analysis_status": "analyzed",
            "brightness": 0.31,
            "saturation": 0.48,
            "frame_sample_count": 12,
        },
    )

    check = module._check_visual_reference_match(
        tmp_path / "final.mp4",
        None,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert check["status"] == "pass"
    assert check["value"]["reference_profile"] == "missing"
    assert check["value"]["measured_brightness"] == 0.31
    assert check["value"]["measured_saturation"] == 0.48


def test_channel_visual_floor_blocks_wealth_blueprint_near_black_neon_render() -> None:
    from atnos.production import qa_gate as module
    from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE

    check = module._check_channel_visual_floor(
        {
            "name": "Visual reference match",
            "status": "warn",
            "value": {
                "measured_brightness": 0.042,
                "measured_saturation": 0.9738,
            },
        },
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert check["name"] == "Channel visual floor"
    assert check["status"] == "fail"
    assert check["value"]["min_brightness"] == 0.18
    assert check["value"]["max_saturation"] == 0.72


def test_channel_visual_floor_passes_wealth_blueprint_readable_fintech_render() -> None:
    from atnos.production import qa_gate as module
    from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE

    check = module._check_channel_visual_floor(
        {
            "name": "Visual reference match",
            "status": "pass",
            "value": {
                "measured_brightness": 0.31,
                "measured_saturation": 0.48,
            },
        },
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert check["status"] == "pass"


def test_visual_reference_match_allows_sigma_high_saturation_profile(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import qa_gate as module
    from atnos.production import reference_profile_builder
    from atnos.production.channel_profile import SIGMA_SQUAD_PROFILE

    class FakeReferenceProfile:
        available = True
        median_brightness = 0.43
        median_saturation = 0.2

    monkeypatch.setattr(
        reference_profile_builder,
        "analyze_video_visual_profile",
        lambda *_args, **_kwargs: {
            "frame_analysis_status": "analyzed",
            "brightness": 0.33,
            "saturation": 0.82,
            "frame_sample_count": 12,
        },
    )

    check = module._check_visual_reference_match(
        tmp_path / "final.mp4",
        FakeReferenceProfile(),
        channel_profile=SIGMA_SQUAD_PROFILE,
    )

    assert check["name"] == "Visual reference match"
    assert check["status"] == "pass"
    assert check["value"]["profile_override"] == "high_saturation_profile"


def test_visual_reference_match_allows_dark_documentary_profile(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import qa_gate as module
    from atnos.production import reference_profile_builder
    from atnos.production.channel_profile import DARK_CHRONICLES_PROFILE

    class FakeReferenceProfile:
        available = True
        median_brightness = 0.43
        median_saturation = 0.2

    monkeypatch.setattr(
        reference_profile_builder,
        "analyze_video_visual_profile",
        lambda *_args, **_kwargs: {
            "frame_analysis_status": "analyzed",
            "brightness": 0.16,
            "saturation": 0.62,
            "frame_sample_count": 12,
        },
    )

    check = module._check_visual_reference_match(
        tmp_path / "final.mp4",
        FakeReferenceProfile(),
        channel_profile=DARK_CHRONICLES_PROFILE,
    )

    assert check["name"] == "Visual reference match"
    assert check["status"] == "pass"
    assert check["value"]["profile_override"] == "dark_documentary_profile"


def test_qa_gate_blocks_hallucinated_subtitles_even_when_timing_exists(tmp_path: Path) -> None:
    context = _complete_context(tmp_path)
    subtitle = Path(context.artifacts["assembly"]["subtitle_file"])
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nW 1518, wsrod ludziom ktorzy wciaz stadzaja sie.\n",
        encoding="utf-8",
    )
    context.artifacts["voice"]["timestamps"] = [
        {
            "segment_id": "s01",
            "start_sec": 0,
            "end_sec": 3,
            "text": "They danced until they died in Strasbourg.",
        }
    ]

    report = build_qa_report(context)

    assert report["overall"] == "failed"
    assert "Subtitle sync" in report["blocking_failures"]
    check = next(item for item in report["required_checks"] if item["name"] == "Subtitle sync")
    assert check["value"]["overlap_ratio"] < 0.72


def _complete_context(tmp_path: Path) -> ProductionContext:
    subtitle = tmp_path / "subtitles.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:03,000\nHook\n", encoding="utf-8")
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"x" * 60000)
    audio = tmp_path / "narration.wav"
    _write_tone_wav(audio, 4)
    video = _write_test_final_video(tmp_path, audio)
    return ProductionContext(
        job_id="prod-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Dlaczego inflacja niszczy budzet",
            description="",
            total_score=88,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "script": {
                "hook": "Dlaczego inflacja zjada twoje pieniadze?",
                "estimated_duration_s": 45,
                "description_draft": "finanse pieniadze budzet inflacja",
            },
            "voice": {
                "output_file": str(audio),
                "status": "generated",
                "quality_tier": "primary",
                "production_ready": True,
                "normalization": "target loudnorm I=-14 TP=-1.5 LRA=11",
                "timestamps": [{"segment_id": "s01", "start_sec": 0, "end_sec": 3, "text": "Hook"}],
            },
            "video": _verified_video_manifest(),
            "assembly": _safe_assembly(video, subtitle),
            "thumbnail": {"thumbnail_path": str(thumbnail)},
            "operating_standards": _operating_standards(),
        },
    )


def _verified_video_manifest() -> dict:
    return {
        "status": "ready",
        "fallback_used": False,
        "clips": [
            {"segment": 1, "status": "generated", "backend": "comfyui"},
            {"segment": 2, "status": "generated", "backend": "comfyui"},
            {"segment": 3, "status": "generated", "backend": "comfyui"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "placeholder_clip_count": 0,
            "cached_fallback_clip_count": 0,
            "non_generated_clip_count": 0,
            "min_real_clips": 3,
            "passes_visual_variety_floor": True,
            "passes_visual_relevance_floor": True,
            "avg_visual_relevance_score": 0.9,
            "visual_relevance_scores": [0.9, 0.9, 0.9],
            "passes_scene_concreteness_floor": True,
            "scene_concreteness_risk_count": 0,
            "scene_concreteness_audits": [],
            "passes_generated_image_text_floor": True,
            "generated_image_text_risk_count": 0,
            "generated_image_text_audits": [],
            "passes_generated_image_visual_floor": True,
            "generated_image_visual_risk_count": 0,
            "generated_image_visual_audits": [],
        },
    }


def _passing_timeline_measurement(*_args, **_kwargs) -> dict:
    return {
        "status": "pass",
        "sampled_frame_count": 6,
        "mean_hash_distance": 0.4,
        "mean_top_crop_pixel_diff": 0.3,
        "reason": "test_fixture_with_verified_visual_change",
    }


def _operating_standards(*, project_type: str = "shorts_channel", blocked: bool = False) -> dict:
    items = [
        {
            "key": "real_signal_research",
            "label": "Real signal research",
            "purpose": "Topic selection must come from real signals.",
            "status": "active",
            "gate": "topic_generation",
            "required": True,
        },
        {
            "key": "style_guide_required",
            "label": "Channel style guide",
            "purpose": "Each production job must use the channel style guide.",
            "status": "active",
            "gate": "pre_render",
            "required": True,
        },
        {
            "key": "private_qa_before_public",
            "label": "Private QA before public",
            "purpose": "Public upload is blocked until QA evidence exists.",
            "status": "active",
            "gate": "publication",
            "required": True,
        },
        {
            "key": "analytics_learning_loop",
            "label": "Analytics learning loop",
            "purpose": "Analytics feedback updates future production choices.",
            "status": "active",
            "gate": "post_publish",
            "required": True,
        },
    ]
    if blocked:
        items.append(
            {
                "key": "risk_policy_required",
                "label": "Risk policy required",
                "purpose": "No real action until risk policy exists.",
                "status": "blocked_until_configured",
                "gate": "execution",
                "required": True,
            }
        )
    return {
        "status": "ready",
        "project_type": project_type,
        "source": {"path": "F:/AtnosSystem/AppData/Roaming/Atnos/data/learning/side_research/report.md", "exists": True},
        "sections": [
            {
                "key": "shorts_quality",
                "title": "Shorts quality system",
                "items": items,
            }
        ],
    }


def _write_test_final_video(tmp_path: Path, audio_path: Path) -> Path:
    raw_video = tmp_path / "raw_video.mp4"
    _write_placeholder_clip(raw_video)
    final_video = tmp_path / "final_short.mp4"
    render = _render_final_video(
        raw_video_path=str(raw_video),
        audio_path=str(audio_path),
        output_path=final_video,
    )
    if not render["ok"]:
        raise AssertionError(f"Unable to create QA test video with audio: {render}")
    return final_video


def _safe_assembly(video: Path, subtitle: Path) -> dict:
    return {
        "output_file": str(video),
        "subtitle_file": str(subtitle),
        "subtitle_style": {
            "position": "lower_third_center",
            "frame_px": {"width": 1080, "height": 1920},
            "safe_area_px": {"left": 72, "right": 72, "top": 96, "bottom": 220},
        },
    }


def _write_tone_wav(path: Path, duration_s: float, *, amplitude: float = 0.5) -> None:
    sample_rate = 44100
    frame_count = int(duration_s * sample_rate)
    peak = min(32767, max(0, int(32767 * amplitude)))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            sample = int(peak * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(struct.pack("<hh", sample, sample))
        wav.writeframes(bytes(frames))
