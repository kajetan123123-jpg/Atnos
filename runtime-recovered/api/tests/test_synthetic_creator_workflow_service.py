from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings
from atnos.services.creator_ops_service import CreatorProjectView
from atnos.services.integration_config_service import ObsidianEffectiveConfig
from atnos.services.output_artifact_service import ManagedArtifactDescriptor
from atnos.services.render_service import RenderArtifactQa, RenderArtifactResult
from atnos.services.synthetic_broll_planner_service import SyntheticBrollPlanResult, SyntheticCutawayAsset
from atnos.services.synthetic_creator_workflow_service import (
    SyntheticCreatorWorkflowService,
    SyntheticScriptBundle,
)
from atnos.services.synthetic_variant_refinement_service import (
    SyntheticPreviewHardControls,
    SyntheticVariantRefinementPlan,
)
from atnos.services.wav2lip_runtime_service import Wav2LipArtifactResult, Wav2LipRuntimeError


class SyntheticCreatorWorkflowServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.service = SyntheticCreatorWorkflowService(
            Mock(),
            settings=self.settings,
        )
        self.service.integration_config.get_obsidian_config = Mock(
            return_value=ObsidianEffectiveConfig(
                vault_path=None,
                export_enabled=False,
                source="test",
                path_exists=False,
                warnings=("disabled in tests",),
            )
        )
        self.service._resolve_youtube_connector_status = Mock(
            return_value={
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": False,
                "connected": False,
                "ready": False,
                "warnings": ["connector disabled in tests"],
                "account_id": None,
                "channel_title": None,
                "granted_scopes": [],
            }
        )
        self.service.analytics_learning.build_calibration = Mock(
            return_value={
                "sample_count": 0,
                "baseline_average_view_percentage": None,
                "baseline_views": None,
                "style_feedback": {},
                "style_sample_counts": {},
                "reel_feedback": {},
                "reel_sample_counts": {},
                "strongest_style_key": None,
                "strongest_reel_key": None,
                "insights": [],
            }
        )
        self.trend_watch_patcher = patch(
            "atnos.services.synthetic_creator_workflow_service.TrendWatchService.latest_payload",
            return_value=None,
        )
        self.trend_watch_patcher.start()
        self.addCleanup(self.trend_watch_patcher.stop)
        self.project = CreatorProjectView(
            id=99,
            project_id=199,
            name="ATNOS Synthetic Creator",
            project_type="synthetic_influencer",
            status="active",
            persona_type="synthetic_original_persona",
            disclosure_policy="platform_dependent",
            niche="premium synthetic creator",
            target_audience="adult short-form audience",
            monetization_goal="validate local preview",
            content_boundaries="non-explicit",
            safety_boundaries="no impersonation",
            platforms=("instagram", "youtube"),
            linked_account_ids=(),
            linked_integrations=(),
            operating_mode="supervised_actions",
            approval_policy="always_require_approval",
            notes=None,
            strategy_profile={},
            linked_accounts_summary=(),
            project_app_links_summary=(),
            project_app_links_defaults_summary={},
            created_at="2026-05-10T00:00:00Z",
            updated_at="2026-05-10T00:00:00Z",
        )
        self.bundle = SyntheticScriptBundle(
            title="Synthetic preview",
            hook="To jest hook.",
            body=("Pierwsze zdanie.", "Drugie zdanie."),
            outro="To jest outro.",
            portrait_prompt="ignored raw llm prompt",
            visual_direction="clean editorial portrait",
            engine="test",
        )
        self.style = self.service.STYLE_PRESETS["editorial_premium"]
        self.reel = self.service.REEL_PRESETS["talking_hook"]

    def test_generate_validated_portrait_artifact_retries_with_stronger_prompt(self) -> None:
        prompts: list[str] = []

        def fake_generate_background(*, prompt: str, output_path: Path, **kwargs) -> None:
            prompts.append(prompt)
            output_path.write_bytes(f"portrait-{len(prompts)}".encode("utf-8"))

        with (
            patch.object(self.service.comfyui, "generate_background", side_effect=fake_generate_background),
            patch.object(
                self.service.wav2lip,
                "verify_face_detectable",
                side_effect=[
                    Wav2LipRuntimeError("Face not detected in portrait image."),
                    None,
                ],
            ) as mocked_verify,
        ):
            descriptor = self.service._generate_validated_portrait_artifact(
                project=self.project,
                bundle=self.bundle,
                style=self.style,
                reel=self.reel,
                conversation_id=None,
            )

        self.assertEqual(mocked_verify.call_count, 2)
        self.assertEqual(len(prompts), 2)
        self.assertIn("front-facing and looking directly into camera", prompts[0])
        self.assertIn("passport-style centered composition", prompts[1])
        self.assertTrue(descriptor.absolute_path.exists())

    def test_generate_validated_portrait_with_reflection_retries_with_reduced_comfyui_load(self) -> None:
        calls: list[tuple[int | None, float | None]] = []

        def fake_generate_background(*, output_path: Path, steps_override=None, cfg_scale_override=None, **kwargs) -> None:
            calls.append((steps_override, cfg_scale_override))
            if len(calls) == 1:
                raise RuntimeError("ComfyUI generation timed out after 180 seconds.")
            output_path.write_bytes(b"portrait")

        with (
            patch.object(self.service.comfyui, "generate_background", side_effect=fake_generate_background),
            patch.object(self.service.wav2lip, "verify_face_detectable", return_value=None),
            patch.object(
                self.service.hardware_guard,
                "release_gpu_pressure",
                return_value={"status": "released", "message": "Stopped Ollama models.", "stopped_models": ["qwen3:8b"]},
            ),
        ):
            descriptor = self.service._generate_validated_portrait_artifact_with_reflection(
                project=self.project,
                bundle=self.bundle,
                style=self.style,
                reel=self.reel,
                conversation_id=None,
                execution_run=None,
            )

        self.assertEqual(calls[0], (None, None))
        self.assertIsNotNone(calls[1][0])
        self.assertIsNotNone(calls[1][1])
        self.assertTrue(descriptor.absolute_path.exists())

    def test_compose_motion_reel_with_reflection_retries_with_balanced_profile_first(self) -> None:
        calls: list[tuple[str, int | None, float | None]] = []
        fake_result = RenderArtifactResult(
            descriptor=ManagedArtifactDescriptor(
                filename="reel.mp4",
                storage_path="atnos-outputs/global/reel.mp4",
                absolute_path=self.root / "reel.mp4",
                media_type="video/mp4",
                file_extension=".mp4",
                size_bytes=4096,
            ),
            qa=RenderArtifactQa(
                duration_sec=16.2,
                width=1080,
                height=1920,
                has_audio=True,
                size_bytes=4096,
                warnings=(),
            ),
            watchdog_timeout_s=420,
            render_duration_sec=22.4,
            engine="moviepy-synthetic-motion",
        )

        def fake_compose(*, render_profile, max_cutaways, cutaway_display_duration_sec, **kwargs):
            calls.append((render_profile, max_cutaways, cutaway_display_duration_sec))
            if len(calls) == 1:
                raise RuntimeError("Synthetic motion watchdog stopped a stuck render after 420 seconds.")
            return fake_result

        controls = SyntheticPreviewHardControls(
            target_duration_window_sec=(12.0, 18.0),
            target_duration_cap_sec=18.5,
            max_hook_words=8,
            max_body_lines=2,
            drop_outro=False,
            voice_speed_multiplier=1.0,
            hook_duration_cap_sec=2.8,
            max_cutaways=3,
            cutaway_display_duration_sec=1.0,
        )
        cutaway_one = self.root / "cutaway-1.png"
        cutaway_two = self.root / "cutaway-2.png"
        cutaway_three = self.root / "cutaway-3.png"
        for item in (cutaway_one, cutaway_two, cutaway_three):
            item.write_bytes(b"cutaway")

        with (
            patch.object(self.service.motion_reel, "compose", side_effect=fake_compose),
            patch.object(
                self.service.hardware_guard,
                "release_gpu_pressure",
                return_value={"status": "released", "message": "Stopped Ollama models.", "stopped_models": ["qwen3:8b"]},
            ),
        ):
            result = self.service._compose_motion_reel_with_reflection(
                portrait_path=self.root / "portrait.png",
                talking_head_path=self.root / "talking-head.mp4",
                audio_path=self.root / "audio.wav",
                subtitles_path=self.root / "subtitles.srt",
                cutaway_paths=(cutaway_one, cutaway_two, cutaway_three),
                conversation_id=None,
                style_key=self.style.key,
                style_label=self.style.label,
                reel_label=self.reel.label,
                hook_text=self.bundle.hook,
                hard_controls=controls,
                quality_profile="premium",
                execution_run=None,
            )

        self.assertEqual(calls[0][0], "quality")
        self.assertEqual(calls[1][0], "balanced")
        self.assertEqual(calls[1][1], 2)
        self.assertLess(calls[1][2], calls[0][2])
        self.assertEqual(result.descriptor.filename, "reel.mp4")

    def test_resolve_project_default_hard_controls_reads_strategy_profile(self) -> None:
        self.project = replace(
            self.project,
            strategy_profile={
                "initial_hard_controls_by_reel": {
                    "talking_hook": {
                        "target_duration_window_sec": [12.0, 18.0],
                        "target_duration_cap_sec": 16.4,
                        "max_hook_words": 7,
                        "max_body_lines": 1,
                        "drop_outro": True,
                        "voice_speed_multiplier": 1.07,
                        "hook_duration_cap_sec": 2.9,
                        "max_cutaways": 2,
                        "cutaway_display_duration_sec": 0.8,
                    }
                }
            },
        )

        controls = self.service._resolve_project_default_hard_controls(
            project=self.project,
            reel_key="talking_hook",
        )

        self.assertIsNotNone(controls)
        assert controls is not None
        self.assertEqual(controls.max_hook_words, 7)
        self.assertTrue(controls.drop_outro)

    def test_build_script_prompt_v2_includes_project_strategy_rules(self) -> None:
        self.project = replace(
            self.project,
            strategy_profile={
                "hook_template": {
                    "strategy_key": "faster_hook",
                    "template_key": "direct_payoff",
                    "instruction": "Hook ma wchodzic szybciej i bez miekkiego rozgrzewania intro.",
                },
                "pacing_policy": {
                    "strategy_key": "tighter_intro",
                    "template_key": "tight_intro",
                    "instruction": "Intro ma byc ciasniejsze, a payoff ma wejsc szybciej.",
                },
                "title_policy": {
                    "strategy_key": "hook_first_title",
                    "template_key": "hook_first",
                    "instruction": "Domyslnie zaczynaj tytul od hooka bez dlugiego wstepu.",
                },
            },
        )

        prompt = self.service._build_script_prompt_v2(
            project=self.project,
            goal="Test project defaults.",
            style=self.style,
            reel=self.reel,
            hard_controls=None,
        )

        self.assertIn("Hook ma wchodzic szybciej", prompt)
        self.assertIn("Intro ma byc ciasniejsze", prompt)
        self.assertIn("Domyslnie zaczynaj tytul od hooka", prompt)

    def test_resolve_project_default_goal_uses_strategy_brief_when_present(self) -> None:
        self.project = replace(
            self.project,
            strategy_profile={
                "next_generation_brief": (
                    "Priorytetem kolejnego batcha jest styl cozy_girlfriend i typ rolki micro_story. "
                    "Wejdz szybciej w hook i nie rozgrzewaj intro."
                )
            },
        )

        goal = self.service._resolve_project_default_goal(
            project=self.project,
            style=self.style,
            reel=self.reel,
        )

        self.assertIn("Priorytetem kolejnego batcha", goal)
        self.assertIn("Prepare one automated synthetic creator talking hook preview", goal)

    def test_generate_preview_batch_expands_distinct_style_and_reel_variants(self) -> None:
        self.project = replace(
            self.project,
            strategy_profile={
                "title_policy": {
                    "strategy_key": "hook_first_title",
                    "template_key": "hook_first",
                    "instruction": "Domyslnie zaczynaj tytul od hooka bez dlugiego wstepu.",
                },
                "hook_template": {
                    "strategy_key": "faster_hook",
                    "template_key": "direct_payoff",
                    "instruction": "Hook ma wchodzic szybciej i bez miekkiego rozgrzewania intro.",
                },
            },
        )
        calls: list[tuple[str | None, str | None, str | None, dict[str, object] | None]] = []

        def fake_generate_preview(*, style_key=None, reel_type=None, goal=None, hard_controls=None, **kwargs):
            calls.append((style_key, reel_type, goal, hard_controls.to_dict() if hard_controls is not None else None))
            body = ["A", "B"] if hard_controls is not None else ["A", "B", "C"]
            return {
                "status": "completed",
                "style": {"key": style_key},
                "reel_type": {"key": reel_type},
                "artifacts": {
                    "portrait": {"storage_path": "portrait.png"},
                    "cutaways": {"assets": [{}, {}, {}], "warnings": [], "engine": "comfyui-cutaway"},
                    "talking_head": {"descriptor": {"storage_path": "video.mp4"}},
                    "motion_reel": {
                        "descriptor": {"storage_path": "reel.mp4"},
                        "qa": {"duration_sec": 22.0, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                    },
                },
                "script_bundle": {"hook": "To jest mocniejszy hook.", "body": body},
            }

        batch_artifact = Mock(
            filename="batch.md",
            storage_path="outputs/batch.md",
            media_type="text/markdown",
            file_extension=".md",
            size_bytes=128,
        )
        refinement_plan = SyntheticVariantRefinementPlan(
            source_variant_index=1,
            style_key="editorial_premium",
            reel_key="micro_story",
            goal="Refine micro story winner.",
            directives=("Skróć hook.", "Przestaw pacing cutawayów."),
            target_duration_window_sec=(18.0, 24.0),
            score_before=84.5,
            confidence_before="high",
            hard_controls=SyntheticPreviewHardControls(
                target_duration_window_sec=(18.0, 24.0),
                target_duration_cap_sec=23.65,
                max_hook_words=10,
                max_body_lines=2,
                drop_outro=False,
                voice_speed_multiplier=1.03,
                hook_duration_cap_sec=4.0,
                max_cutaways=3,
                cutaway_display_duration_sec=1.0,
            ),
            historical_feedback_score=None,
            historical_strengths=(),
            historical_risks=(),
        )

        with (
            patch.object(self.service, "_resolve_project", return_value=self.project),
            patch.object(self.service, "generate_preview", side_effect=fake_generate_preview),
            patch.object(
                self.service.variant_scoring,
                "rank_variants",
                return_value={
                    "best_variant_index": 1,
                    "best_score": 84.5,
                    "best_confidence": "high",
                    "ranked_variants": [
                        {"variant_index": 1, "score": 84.5, "confidence": "high", "strengths": ["top"], "risks": []},
                        {"variant_index": 0, "score": 73.1, "confidence": "medium", "strengths": ["ok"], "risks": []},
                        {"variant_index": 2, "score": 69.0, "confidence": "medium", "strengths": ["ok"], "risks": []},
                    ],
                },
            ) as mocked_rank_variants,
            patch.object(self.service.variant_refinement, "build_plan", return_value=refinement_plan),
            patch.object(
                self.service,
                "_write_batch_report_bundle",
                return_value={"artifact": batch_artifact, "vault_note_path": None},
            ),
        ):
            result = self.service.generate_preview_batch(
                creator_project_id=self.project.id,
                style_keys=["editorial_premium", "cozy_girlfriend"],
                reel_types=["talking_hook", "micro_story"],
                max_items=3,
            )

        mocked_rank_variants.assert_called_once()
        self.assertEqual(
            mocked_rank_variants.call_args.kwargs["calibration"]["sample_count"],
            0,
        )
        self.assertEqual(
            calls,
            [
                ("editorial_premium", "talking_hook", "Validate synthetic creator preview variant Editorial Premium / Talking Hook for ATNOS Synthetic Creator.", None),
                ("editorial_premium", "micro_story", "Validate synthetic creator preview variant Editorial Premium / Micro Story for ATNOS Synthetic Creator.", None),
                ("cozy_girlfriend", "talking_hook", "Validate synthetic creator preview variant Cozy Girlfriend / Talking Hook for ATNOS Synthetic Creator.", None),
                ("editorial_premium", "micro_story", "Refine micro story winner.", refinement_plan.hard_controls.to_dict()),
            ],
        )
        self.assertEqual(len(result["variants"]), 3)
        self.assertEqual(result["selection"]["best_variant_index"], 1)
        self.assertEqual(result["best_variant"]["reel_type"]["key"], "micro_story")
        self.assertEqual(result["refinement_plan"]["goal"], "Refine micro story winner.")
        self.assertEqual(result["refined_best_variant"]["reel_type"]["key"], "micro_story")
        self.assertEqual(result["refined_score"]["confidence"], "high")
        self.assertEqual(len(result["refinement_attempts"]), 1)
        self.assertEqual(result["release_pack"]["platform_key"], "youtube")
        self.assertEqual(result["release_pack"]["publish_path"], "manual_upload_ready")
        self.assertTrue(
            any(
                "Domyslnie zaczynaj tytul od hooka" in item
                for item in list(dict(result["release_pack"]["metadata"]).get("project_strategy_policy") or [])
            )
        )
        self.assertIsNotNone(result["release_pack_artifact"])
        self.assertIsNotNone(result["release_pack_json_artifact"])

    def test_generate_preview_batch_retries_refinement_when_duration_stays_out_of_target(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_generate_preview(*, style_key=None, reel_type=None, goal=None, hard_controls=None, **kwargs):
            controls_dict = hard_controls.to_dict() if hard_controls is not None else None
            calls.append(
                {
                    "style_key": style_key,
                    "reel_type": reel_type,
                    "goal": goal,
                    "hard_controls": controls_dict,
                }
            )
            if hard_controls is None:
                duration = 20.8
                body = ["A", "B"]
            elif len(calls) == 2:
                duration = 19.4
                body = ["A", "B"]
            else:
                duration = 16.2
                body = ["A"]
            return {
                "status": "completed",
                "style": {"key": style_key},
                "reel_type": {"key": reel_type},
                "artifacts": {
                    "portrait": {"storage_path": "portrait.png"},
                    "cutaways": {"assets": [{}, {}], "warnings": [], "engine": "comfyui-cutaway"},
                    "talking_head": {"descriptor": {"storage_path": "video.mp4"}},
                    "motion_reel": {
                        "descriptor": {"storage_path": "reel.mp4"},
                        "qa": {"duration_sec": duration, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                    },
                },
                "script_bundle": {"hook": "To jest mocniejszy hook.", "body": body},
            }

        refinement_plan = SyntheticVariantRefinementPlan(
            source_variant_index=0,
            style_key="editorial_premium",
            reel_key="talking_hook",
            goal="Refine talking hook winner.",
            directives=("Skróć timing.",),
            target_duration_window_sec=(12.0, 18.0),
            score_before=83.2,
            confidence_before="high",
            hard_controls=SyntheticPreviewHardControls(
                target_duration_window_sec=(12.0, 18.0),
                target_duration_cap_sec=17.65,
                max_hook_words=8,
                max_body_lines=1,
                drop_outro=True,
                voice_speed_multiplier=1.08,
                hook_duration_cap_sec=3.1,
                max_cutaways=2,
                cutaway_display_duration_sec=0.82,
            ),
            historical_feedback_score=None,
            historical_strengths=(),
            historical_risks=(),
        )
        batch_artifact = Mock(
            filename="batch.md",
            storage_path="outputs/batch.md",
            media_type="text/markdown",
            file_extension=".md",
            size_bytes=128,
        )

        with (
            patch.object(self.service, "_resolve_project", return_value=self.project),
            patch.object(self.service, "generate_preview", side_effect=fake_generate_preview),
            patch.object(
                self.service.variant_scoring,
                "rank_variants",
                return_value={
                    "best_variant_index": 0,
                    "best_score": 83.2,
                    "best_confidence": "high",
                    "ranked_variants": [{"variant_index": 0, "score": 83.2, "confidence": "high", "strengths": ["top"], "risks": []}],
                },
            ),
            patch.object(self.service.variant_refinement, "build_plan", return_value=refinement_plan),
            patch.object(
                self.service,
                "_write_batch_report_bundle",
                return_value={"artifact": batch_artifact, "vault_note_path": None},
            ),
        ):
            result = self.service.generate_preview_batch(
                creator_project_id=self.project.id,
                style_keys=["editorial_premium"],
                reel_types=["talking_hook"],
                max_items=1,
            )

        self.assertEqual(len(result["refinement_attempts"]), 2)
        self.assertFalse(result["refinement_attempts"][0]["target_met"])
        self.assertTrue(result["refinement_attempts"][1]["target_met"])
        self.assertEqual(calls[1]["hard_controls"]["max_body_lines"], 1)
        self.assertLess(calls[2]["hard_controls"]["target_duration_cap_sec"], calls[1]["hard_controls"]["target_duration_cap_sec"])
        self.assertEqual(result["release_pack"]["platform_key"], "youtube")

    def test_generate_preview_batch_exposes_historical_learning_profile(self) -> None:
        preview_payload = {
            "status": "completed",
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
            "artifacts": {
                "portrait": {"storage_path": "portrait.png"},
                "cutaways": {"assets": [{}, {}], "warnings": [], "engine": "comfyui-cutaway"},
                "talking_head": {"descriptor": {"storage_path": "video.mp4"}},
                "motion_reel": {
                    "descriptor": {"storage_path": "reel.mp4"},
                    "qa": {"duration_sec": 16.0, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                },
            },
            "script_bundle": {"hook": "To jest hook pod retention.", "body": ["Linia 1", "Linia 2"]},
        }
        learning_profile = {
            "sample_count": 3,
            "baseline_average_view_percentage": 74.5,
            "baseline_views": 820.0,
            "style_feedback": {"editorial_premium": 3.2},
            "style_sample_counts": {"editorial_premium": 2},
            "reel_feedback": {"talking_hook": 1.4},
            "reel_sample_counts": {"talking_hook": 3},
            "strongest_style_key": "editorial_premium",
            "strongest_reel_key": "talking_hook",
            "insights": ["Gotowe analytics sample: 3."],
        }

        with (
            patch.object(self.service, "_resolve_project", return_value=self.project),
            patch.object(self.service, "generate_preview", return_value=preview_payload),
            patch.object(self.service.analytics_learning, "build_calibration", return_value=learning_profile),
        ):
            result = self.service.generate_preview_batch(
                creator_project_id=self.project.id,
                style_keys=["editorial_premium"],
                reel_types=["talking_hook"],
                max_items=1,
            )

        self.assertEqual(result["analytics_learning_profile"]["sample_count"], 3)
        self.assertEqual(result["analytics_learning_profile"]["strongest_style_key"], "editorial_premium")

    def test_generate_preview_batch_uses_history_to_prioritize_variant_generation_order(self) -> None:
        calls: list[tuple[str | None, str | None, str | None, dict[str, object] | None]] = []
        self.project = replace(
            self.project,
            strategy_profile={
                "next_generation_brief": (
                    "Priorytetem kolejnego batcha jest styl cozy_girlfriend i typ rolki micro_story. "
                    "Wejdz szybciej w hook i nie rozgrzewaj intro."
                )
            },
        )

        def fake_generate_preview(*, style_key=None, reel_type=None, goal=None, hard_controls=None, **kwargs):
            calls.append((style_key, reel_type, goal, hard_controls.to_dict() if hard_controls is not None else None))
            return {
                "status": "completed",
                "style": {"key": style_key, "label": style_key},
                "reel_type": {"key": reel_type, "label": reel_type},
                "artifacts": {
                    "portrait": {"storage_path": "portrait.png"},
                    "cutaways": {"assets": [{}, {}], "warnings": [], "engine": "comfyui-cutaway"},
                    "talking_head": {"descriptor": {"storage_path": "video.mp4"}},
                    "motion_reel": {
                        "descriptor": {"storage_path": "reel.mp4"},
                        "qa": {"duration_sec": 16.0, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                    },
                },
                "script_bundle": {"hook": "To jest hook pod retention.", "body": ["Linia 1", "Linia 2"]},
            }

        with (
            patch.object(self.service, "_resolve_project", return_value=self.project),
            patch.object(self.service, "generate_preview", side_effect=fake_generate_preview),
            patch.object(
                self.service.analytics_learning,
                "build_calibration",
                return_value={
                    "sample_count": 4,
                    "baseline_average_view_percentage": 75.0,
                    "baseline_views": 910.0,
                    "style_feedback": {"editorial_premium": -1.3, "cozy_girlfriend": 3.2},
                    "style_sample_counts": {"editorial_premium": 2, "cozy_girlfriend": 2},
                    "reel_feedback": {"talking_hook": -0.8, "micro_story": 2.4},
                    "reel_sample_counts": {"talking_hook": 2, "micro_story": 2},
                    "strongest_style_key": "cozy_girlfriend",
                    "strongest_reel_key": "micro_story",
                    "hook_strategy_counts": {"faster_hook": 3},
                    "pacing_strategy_counts": {"tighter_intro": 2},
                    "cutaway_strategy_counts": {"add_midbeat_cutaway": 2},
                    "title_strategy_counts": {"hook_first_title": 2},
                    "strongest_hook_strategy": "faster_hook",
                    "strongest_pacing_strategy": "tighter_intro",
                    "strongest_cutaway_strategy": "add_midbeat_cutaway",
                    "strongest_title_strategy": "hook_first_title",
                    "insights": ["history"],
                },
            ),
        ):
            self.service.generate_preview_batch(
                creator_project_id=self.project.id,
                style_keys=["editorial_premium", "cozy_girlfriend"],
                reel_types=["talking_hook", "micro_story"],
                max_items=2,
            )

        self.assertEqual(calls[0][0], "cozy_girlfriend")
        self.assertEqual(calls[0][1], "micro_story")
        self.assertIn("Priorytetem kolejnego batcha", calls[0][2] or "")
        self.assertIn("historycznie ten kierunek dowozil lepsze retention", (calls[0][2] or "").lower())
        self.assertIsNotNone(calls[0][3])
        self.assertLessEqual(int(calls[0][3]["max_hook_words"]), 8)

    def test_generate_preview_batch_waits_for_approval_when_youtube_connector_is_ready(self) -> None:
        batch_artifact = Mock(
            filename="batch.md",
            storage_path="outputs/batch.md",
            media_type="text/markdown",
            file_extension=".md",
            size_bytes=128,
        )
        release_pack_artifact = Mock(
            filename="release-pack.md",
            storage_path="outputs/release-pack.md",
            media_type="text/markdown",
            file_extension=".md",
            size_bytes=256,
        )
        release_pack_json_artifact = Mock(
            filename="release-pack.json",
            storage_path="outputs/release-pack.json",
            media_type="application/json",
            file_extension=".json",
            size_bytes=256,
        )
        preview_payload = {
            "status": "completed",
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
            "artifacts": {
                "portrait": {"storage_path": "portrait.png"},
                "subtitles": {"storage_path": "subtitles.srt"},
                "motion_reel": {
                    "descriptor": {"storage_path": "reel.mp4"},
                    "qa": {"duration_sec": 16.2, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                },
            },
            "script_bundle": {"hook": "To jest hook.", "body": ["Linia 1"]},
        }
        release_pack = self.service.release_pack.build_release_pack(
            project_name=self.project.name,
            preview=preview_payload,
            selection={"best_variant_index": 0},
            score_payload={"score": 91.2, "confidence": "high", "risks": []},
            connector_status={
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": True,
                "connected": True,
                "ready": True,
                "warnings": [],
                "account_id": None,
                "channel_title": "ATNOS Channel",
                "granted_scopes": ["youtube.upload"],
            },
        )
        refinement_plan = SyntheticVariantRefinementPlan(
            source_variant_index=0,
            style_key="editorial_premium",
            reel_key="talking_hook",
            goal="Refine talking hook winner.",
            directives=("Dopnij pacing.",),
            target_duration_window_sec=(12.0, 18.0),
            score_before=91.2,
            confidence_before="high",
            hard_controls=SyntheticPreviewHardControls(
                target_duration_window_sec=(12.0, 18.0),
                target_duration_cap_sec=17.2,
                max_hook_words=8,
                max_body_lines=1,
                drop_outro=True,
                voice_speed_multiplier=1.05,
                hook_duration_cap_sec=3.0,
                max_cutaways=2,
                cutaway_display_duration_sec=0.8,
            ),
            historical_feedback_score=None,
            historical_strengths=(),
            historical_risks=(),
        )

        with (
            patch.object(self.service, "_resolve_project", return_value=self.project),
            patch.object(self.service, "generate_preview", return_value=preview_payload),
            patch.object(
                self.service.variant_scoring,
                "rank_variants",
                return_value={
                    "best_variant_index": 0,
                    "best_score": 91.2,
                    "best_confidence": "high",
                    "ranked_variants": [{"variant_index": 0, "score": 91.2, "confidence": "high", "strengths": ["top"], "risks": []}],
                },
            ),
            patch.object(
                self.service.variant_refinement,
                "build_plan",
                return_value=refinement_plan,
            ),
            patch.object(
                self.service,
                "_execute_refinement_loop",
                return_value=(
                    preview_payload,
                    {"score": 91.2, "confidence": "high", "strengths": ["top"], "risks": []},
                    [],
                ),
            ),
            patch.object(
                self.service,
                "_write_release_pack_bundle",
                return_value={
                    "pack": release_pack,
                    "artifact": release_pack_artifact,
                    "json_artifact": release_pack_json_artifact,
                    "vault_note_path": "Atnos/Social/ReleasePacks/release-pack.md",
                },
            ),
            patch.object(
                self.service,
                "_write_batch_report_bundle",
                return_value={"artifact": batch_artifact, "vault_note_path": None},
            ),
        ):
            result = self.service.generate_preview_batch(
                creator_project_id=self.project.id,
                style_keys=["editorial_premium"],
                reel_types=["talking_hook"],
                max_items=1,
            )

        self.assertEqual(result["status"], "awaiting_approval")
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["publish_result"]["status"], "awaiting_approval")
        self.assertEqual(result["analytics_summary"]["status"], "waiting_for_publish")
        self.assertEqual(result["workflow_state"]["status"], "awaiting_human")
        self.assertEqual(result["workflow_state"]["current_node"], "graph.synthetic_release_pack")
        self.assertEqual(result["approval_package"]["platform_key"], "youtube")

    def test_publish_approved_batch_uploads_winner_and_fetches_analytics(self) -> None:
        portrait_output = self.service.artifacts.create_output_path(conversation_id=None, extension=".png")
        portrait_output.write_bytes(b"png")
        portrait_descriptor = self.service.artifacts.finalize_output(
            absolute_path=portrait_output,
            filename="portrait.png",
            media_type="image/png",
        )
        subtitles_output = self.service.artifacts.create_output_path(conversation_id=None, extension=".srt")
        subtitles_output.write_text("1\n00:00:00,000 --> 00:00:01,000\nHej", encoding="utf-8")
        subtitles_descriptor = self.service.artifacts.finalize_output(
            absolute_path=subtitles_output,
            filename="subtitles.srt",
            media_type="application/x-subrip",
        )
        winner_output = self.service.artifacts.create_output_path(conversation_id=None, extension=".mp4")
        winner_output.write_bytes(b"mp4")
        winner_descriptor = self.service.artifacts.finalize_output(
            absolute_path=winner_output,
            filename="winner.mp4",
            media_type="video/mp4",
        )
        source_run = SimpleNamespace(
            id=44,
            goal="Publish synthetic winner",
            input_json={},
            output_json={
                "project_name": self.project.name,
                "selection": {"best_variant_index": 0},
                "best_variant": {
                    "artifacts": {
                        "portrait": {
                            "filename": portrait_descriptor.filename,
                            "storage_path": portrait_descriptor.storage_path,
                            "media_type": portrait_descriptor.media_type,
                            "file_extension": portrait_descriptor.file_extension,
                            "size_bytes": portrait_descriptor.size_bytes,
                        },
                        "subtitles": {
                            "filename": subtitles_descriptor.filename,
                            "storage_path": subtitles_descriptor.storage_path,
                            "media_type": subtitles_descriptor.media_type,
                            "file_extension": subtitles_descriptor.file_extension,
                            "size_bytes": subtitles_descriptor.size_bytes,
                        },
                        "motion_reel": {
                            "descriptor": {
                                "filename": winner_descriptor.filename,
                                "storage_path": winner_descriptor.storage_path,
                                "media_type": winner_descriptor.media_type,
                                "file_extension": winner_descriptor.file_extension,
                                "size_bytes": winner_descriptor.size_bytes,
                            },
                            "qa": {"duration_sec": 16.2, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                        },
                    }
                },
                "release_pack": {
                    "platform_key": "youtube",
                    "connector_key": "youtube-studio",
                    "publish_path": "official_youtube_ready",
                    "connector_status": {"ready": True, "channel_title": "ATNOS Channel"},
                    "metadata": {
                        "title": "ATNOS winner",
                        "description": "Opis",
                        "tags": ["atnos", "short"],
                        "privacy_status": "private",
                    },
                    "analytics_followup": {"recommended_review_window_hours": 48},
                    "approval_package": {
                        "title": "ATNOS winner",
                        "platform_key": "youtube",
                        "connector_key": "youtube-studio",
                    },
                },
            },
            artifacts_json={},
        )
        publish_run = SimpleNamespace(id=55)
        controls = SimpleNamespace(publish_automation_enabled=True)

        with (
            patch.object(self.service, "_run_step", side_effect=lambda **kwargs: kwargs["fn"]()),
            patch.object(
                self.service.youtube,
                "probe",
                return_value=SimpleNamespace(
                    ready=True,
                    warnings=(),
                    to_dict=lambda: {"ready": True, "channel_title": "ATNOS Channel"},
                ),
            ),
            patch.object(
                self.service.youtube,
                "upload_short",
                return_value={"video_id": "abc123", "privacy_status": "private", "warnings": [], "status": {"processing_status": "succeeded"}},
            ),
            patch.object(
                self.service.youtube,
                "fetch_analytics",
                return_value={"status": "ready", "video_id": "abc123", "views": 1200, "average_view_percentage": 88.1},
            ),
        ):
            result = self.service.publish_approved_batch(
                source_run=source_run,
                publish_run=publish_run,
                controls=controls,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["workflow"], "synthetic_creator_publish")
        self.assertEqual(result["publish_result"]["status"], "uploaded")
        self.assertEqual(result["publish_result"]["video_id"], "abc123")
        self.assertEqual(result["analytics_summary"]["status"], "ready")
        self.assertIn("feedback_loop", result["artifacts"])
        self.assertEqual(result["approval_package"]["platform_key"], "youtube")

    def test_finalize_script_bundle_rejects_low_quality_marketing_copy(self) -> None:
        noisy = SyntheticScriptBundle(
            title="ATNOS Live Smoke 3 â€“ Nowa Era Tworzenia",
            hook="Czy moĹĽesz wyobraziÄ‡ sobie perfekcjÄ™ w kaĹĽdej scenie?",
            body=(
                "Z ATNOS Live Smoke 3 tworzysz realne scenariusze z elementami sztucznych realiĂłw.",
                "Kontroluj detale, jakbyĹ› byĹ‚ w rzeczywistoĹ›ci â€“ bez ograniczeĹ„.",
            ),
            outro="Dowiedz siÄ™, jak zmienia to twĂłj styl.",
            portrait_prompt="ignored",
            visual_direction="ignored",
            engine="workflow-llm",
        )

        result = self.service._finalize_script_bundle(
            project=self.project,
            style=self.style,
            reel=self.reel,
            bundle=noisy,
        )

        self.assertEqual(result.engine, "fallback-template")
        self.assertIn("zero plastikowego vibe'u", result.hook)

    def test_apply_hard_controls_to_script_bundle_shortens_body_and_outro(self) -> None:
        controls = SyntheticPreviewHardControls(
            target_duration_window_sec=(12.0, 18.0),
            target_duration_cap_sec=16.0,
            max_hook_words=8,
            max_body_lines=1,
            drop_outro=True,
            voice_speed_multiplier=1.12,
            hook_duration_cap_sec=3.1,
            max_cutaways=2,
            cutaway_display_duration_sec=0.82,
        )

        result = self.service._apply_hard_controls_to_script_bundle(
            bundle=SyntheticScriptBundle(
                title="Synthetic preview",
                hook="To jest bardzo długi hook który trzeba skrócić pod retention proxy tej rolki.",
                body=(
                    "Pierwsze zdanie body jest jeszcze okej.",
                    "Drugie zdanie body powinno zniknąć przy twardym trimie.",
                ),
                outro="To jest outro do wycięcia.",
                portrait_prompt="portrait",
                visual_direction="direction",
                engine="test",
            ),
            reel=self.reel,
            hard_controls=controls,
            base_voice_speed=self.style.voice_speed,
        )

        self.assertLessEqual(len(result.hook.split()), 8)
        self.assertEqual(len(result.body), 1)
        self.assertEqual(result.outro, "")

    def test_generate_preview_returns_motion_reel_artifact(self) -> None:
        portrait_path = self.root / "portrait.png"
        cutaway_path = self.root / "cutaway.png"
        audio_path = self.root / "voice.wav"
        subtitles_path = self.root / "subs.srt"
        talking_path = self.root / "talk.mp4"
        reel_path = self.root / "reel.mp4"
        report_path = self.root / "report.md"
        for path in (portrait_path, cutaway_path, audio_path, subtitles_path, talking_path, reel_path, report_path):
            path.write_bytes(b"x")

        portrait = ManagedArtifactDescriptor(
            filename="portrait.png",
            storage_path=portrait_path.as_posix(),
            absolute_path=portrait_path,
            media_type="image/png",
            file_extension=".png",
            size_bytes=1,
        )
        audio = ManagedArtifactDescriptor(
            filename="voice.wav",
            storage_path=audio_path.as_posix(),
            absolute_path=audio_path,
            media_type="audio/wav",
            file_extension=".wav",
            size_bytes=1,
        )
        subtitles = ManagedArtifactDescriptor(
            filename="subs.srt",
            storage_path=subtitles_path.as_posix(),
            absolute_path=subtitles_path,
            media_type="application/x-subrip",
            file_extension=".srt",
            size_bytes=1,
        )
        talking_head = Wav2LipArtifactResult(
            descriptor=ManagedArtifactDescriptor(
                filename="talk.mp4",
                storage_path=talking_path.as_posix(),
                absolute_path=talking_path,
                media_type="video/mp4",
                file_extension=".mp4",
                size_bytes=1,
            ),
            fps=30,
            face_detect_batch=4,
            mode="repetitive",
        )
        cutaways = SyntheticBrollPlanResult(
            assets=(
                SyntheticCutawayAsset(
                    descriptor=ManagedArtifactDescriptor(
                        filename="cutaway-01.png",
                        storage_path=cutaway_path.as_posix(),
                        absolute_path=cutaway_path,
                        media_type="image/png",
                        file_extension=".png",
                        size_bytes=1,
                    ),
                    label="Hook cutaway",
                    source_line="To jest hook.",
                    prompt="cutaway prompt",
                ),
            ),
            engine="comfyui-cutaway",
            warnings=(),
        )
        motion_reel = RenderArtifactResult(
            descriptor=ManagedArtifactDescriptor(
                filename="reel.mp4",
                storage_path=reel_path.as_posix(),
                absolute_path=reel_path,
                media_type="video/mp4",
                file_extension=".mp4",
                size_bytes=1,
            ),
            qa=RenderArtifactQa(duration_sec=12.0, width=1080, height=1920, has_audio=True, size_bytes=1, warnings=()),
            watchdog_timeout_s=240,
            render_duration_sec=2.3,
            engine="moviepy-synthetic-motion",
        )
        report = ManagedArtifactDescriptor(
            filename="report.md",
            storage_path=report_path.as_posix(),
            absolute_path=report_path,
            media_type="text/markdown",
            file_extension=".md",
            size_bytes=1,
        )

        with (
            patch.object(self.service, "_resolve_project", return_value=self.project),
            patch.object(self.service, "_draft_script_bundle", return_value=self.bundle),
            patch.object(self.service, "_generate_validated_portrait_artifact", return_value=portrait),
            patch.object(self.service.broll, "generate_cutaways", return_value=cutaways),
            patch.object(self.service.voice, "synthesize", return_value=audio),
            patch.object(self.service.subtitles, "generate", return_value=subtitles),
            patch.object(self.service.wav2lip, "generate_talking_head", return_value=talking_head),
            patch.object(self.service.motion_reel, "compose", return_value=motion_reel),
            patch.object(self.service, "_write_report_bundle", return_value={"artifact": report, "vault_note_path": None}),
        ):
            result = self.service.generate_preview(
                creator_project_id=self.project.id,
                goal="test preview",
                style_key="editorial_premium",
                reel_type="micro_story",
                attach_message=False,
            )

        self.assertEqual(result["artifacts"]["motion_reel"]["descriptor"]["filename"], "reel.mp4")
        self.assertEqual(result["runtime_stack"]["motion_reel"], "moviepy-synthetic-motion")
        self.assertEqual(result["artifacts"]["cutaways"]["assets"][0]["label"], "Hook cutaway")
        self.assertEqual(result["runtime_stack"]["broll"], "comfyui-cutaway")

    def test_generate_preview_fast_turnaround_propagates_to_broll_and_balanced_render(self) -> None:
        portrait_path = self.root / "portrait-fast.png"
        portrait_path.write_bytes(b"portrait")
        portrait = ManagedArtifactDescriptor(
            filename="portrait-fast.png",
            storage_path=portrait_path.as_posix(),
            absolute_path=portrait_path,
            media_type="image/png",
            file_extension=".png",
            size_bytes=1,
        )
        audio_path = self.root / "audio-fast.wav"
        audio_path.write_bytes(b"audio")
        audio = ManagedArtifactDescriptor(
            filename="audio-fast.wav",
            storage_path=audio_path.as_posix(),
            absolute_path=audio_path,
            media_type="audio/wav",
            file_extension=".wav",
            size_bytes=1,
        )
        subtitles_path = self.root / "subs-fast.srt"
        subtitles_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")
        subtitles = ManagedArtifactDescriptor(
            filename="subs-fast.srt",
            storage_path=subtitles_path.as_posix(),
            absolute_path=subtitles_path,
            media_type="application/x-subrip",
            file_extension=".srt",
            size_bytes=1,
        )
        talking_head_path = self.root / "talk-fast.mp4"
        talking_head_path.write_bytes(b"video")
        talking_head = Wav2LipArtifactResult(
            descriptor=ManagedArtifactDescriptor(
                filename="talk-fast.mp4",
                storage_path=talking_head_path.as_posix(),
                absolute_path=talking_head_path,
                media_type="video/mp4",
                file_extension=".mp4",
                size_bytes=1,
            ),
            fps=30,
            face_detect_batch=4,
            mode="repetitive",
        )
        cutaway_path = self.root / "cutaway-fast.png"
        cutaway_path.write_bytes(b"png")
        cutaways = SyntheticBrollPlanResult(
            assets=(
                SyntheticCutawayAsset(
                    descriptor=ManagedArtifactDescriptor(
                        filename="cutaway-fast.png",
                        storage_path=cutaway_path.as_posix(),
                        absolute_path=cutaway_path,
                        media_type="image/png",
                        file_extension=".png",
                        size_bytes=1,
                    ),
                    label="Hook cutaway",
                    source_line=self.bundle.hook,
                    prompt="prompt",
                ),
            ),
            engine="comfyui-cutaway",
        )
        motion_reel = RenderArtifactResult(
            descriptor=ManagedArtifactDescriptor(
                filename="reel-fast.mp4",
                storage_path=(self.root / "reel-fast.mp4").as_posix(),
                absolute_path=self.root / "reel-fast.mp4",
                media_type="video/mp4",
                file_extension=".mp4",
                size_bytes=1,
            ),
            qa=RenderArtifactQa(duration_sec=12.0, width=1080, height=1920, has_audio=True, size_bytes=1, warnings=()),
            watchdog_timeout_s=240,
            render_duration_sec=2.1,
            engine="moviepy-synthetic-motion",
        )
        report = ManagedArtifactDescriptor(
            filename="report-fast.md",
            storage_path=(self.root / "report-fast.md").as_posix(),
            absolute_path=self.root / "report-fast.md",
            media_type="text/markdown",
            file_extension=".md",
            size_bytes=1,
        )

        with (
            patch.object(self.service, "_resolve_project", return_value=self.project),
            patch.object(self.service, "_draft_script_bundle", return_value=self.bundle),
            patch.object(self.service, "_generate_validated_portrait_artifact_with_reflection", return_value=portrait),
            patch.object(self.service.broll, "generate_cutaways", return_value=cutaways) as mocked_cutaways,
            patch.object(self.service.voice, "synthesize", return_value=audio),
            patch.object(self.service.subtitles, "generate", return_value=subtitles),
            patch.object(self.service.wav2lip, "generate_talking_head", return_value=talking_head),
            patch.object(self.service.motion_reel, "compose", return_value=motion_reel) as mocked_compose,
            patch.object(self.service, "_write_report_bundle", return_value={"artifact": report, "vault_note_path": None}),
        ):
            result = self.service.generate_preview(
                creator_project_id=self.project.id,
                goal="test fast preview",
                style_key="editorial_premium",
                reel_type="micro_story",
                quality_profile="fast-turnaround",
                attach_message=False,
            )

        self.assertEqual(mocked_cutaways.call_args.kwargs["quality_profile"], "fast-turnaround")
        self.assertEqual(mocked_compose.call_args.kwargs["render_profile"], "balanced")
        self.assertEqual(result["quality_profile"], "fast-turnaround")
        self.assertEqual(result["runtime_stack"]["render_profile"], "balanced")

    def test_build_report_markdown_includes_cutaways_and_warnings(self) -> None:
        portrait = ManagedArtifactDescriptor(
            filename="portrait.png",
            storage_path="outputs/portrait.png",
            absolute_path=self.root / "portrait.png",
            media_type="image/png",
            file_extension=".png",
            size_bytes=1,
        )
        audio = ManagedArtifactDescriptor(
            filename="voice.wav",
            storage_path="outputs/voice.wav",
            absolute_path=self.root / "voice.wav",
            media_type="audio/wav",
            file_extension=".wav",
            size_bytes=1,
        )
        subtitles = ManagedArtifactDescriptor(
            filename="subs.srt",
            storage_path="outputs/subs.srt",
            absolute_path=self.root / "subs.srt",
            media_type="application/x-subrip",
            file_extension=".srt",
            size_bytes=1,
        )
        talking_head = Wav2LipArtifactResult(
            descriptor=ManagedArtifactDescriptor(
                filename="talk.mp4",
                storage_path="outputs/talk.mp4",
                absolute_path=self.root / "talk.mp4",
                media_type="video/mp4",
                file_extension=".mp4",
                size_bytes=1,
            ),
            fps=30,
            face_detect_batch=4,
            mode="repetitive",
        )
        motion_reel = RenderArtifactResult(
            descriptor=ManagedArtifactDescriptor(
                filename="reel.mp4",
                storage_path="outputs/reel.mp4",
                absolute_path=self.root / "reel.mp4",
                media_type="video/mp4",
                file_extension=".mp4",
                size_bytes=1,
            ),
            qa=RenderArtifactQa(duration_sec=15.0, width=1080, height=1920, has_audio=True, size_bytes=1, warnings=()),
            watchdog_timeout_s=240,
            render_duration_sec=4.4,
            engine="moviepy-synthetic-motion",
        )
        cutaways = SyntheticBrollPlanResult(
            assets=(
                SyntheticCutawayAsset(
                    descriptor=ManagedArtifactDescriptor(
                        filename="cutaway-01.png",
                        storage_path="outputs/cutaway-01.png",
                        absolute_path=self.root / "cutaway-01.png",
                        media_type="image/png",
                        file_extension=".png",
                        size_bytes=1,
                    ),
                    label="Hook cutaway",
                    source_line="To jest hook.",
                    prompt="prompt",
                ),
            ),
            engine="comfyui-cutaway",
            warnings=("Hook cutaway: timeout recovery.",),
        )

        markdown = self.service._build_report_markdown(
            project=self.project,
            goal="smoke",
            style=self.style,
            reel=self.reel,
            script_bundle=self.bundle,
            portrait=portrait,
            cutaways=cutaways,
            audio=audio,
            subtitles=subtitles,
            talking_head=talking_head,
            motion_reel=motion_reel,
        )

        self.assertIn("engine_broll: comfyui-cutaway", markdown)
        self.assertIn("## Cutaways", markdown)
        self.assertIn("Hook cutaway", markdown)
        self.assertIn("## B-roll warnings", markdown)


def build_test_settings(root: Path) -> Settings:
    data_dir = root / "data"
    uploads_dir = data_dir / "uploads"
    sqlite_file = data_dir / "atnos-test.db"
    settings = Settings(
        app_env="test",
        app_root=root,
        data_dir=data_dir,
        uploads_dir=uploads_dir,
        sqlite_file=sqlite_file,
        comfyui_install_dir=root / "ComfyUI_windows_portable",
        kokoro_model_path=data_dir / "models" / "kokoro" / "model.onnx",
        kokoro_voices_path=data_dir / "models" / "kokoro" / "voices-v1.0.bin",
        scheduled_tasks_enabled=False,
    )
    settings.ensure_directories()
    return settings


def make_temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="atnos-api-tests-")


if __name__ == "__main__":
    unittest.main()
