from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content import faceless_recreation_production_session as production_module
from content.faceless_recreation_production_session import FacelessRecreationProductionSessionService
from content.format_recreation_review import FormatRecreationReview, FormatSignalScore
from content.visual_asset_pack_repair import VisualAssetPackBrightnessRepairResult
from content.visual_recreation_asset_pack import VisualPackSceneAsset, VisualRecreationAssetPackResult


def test_production_session_repairs_asset_pack_and_approves_final_review() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-production-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json, source_dir = _write_inputs(root)
        service = FacelessRecreationProductionSessionService(
            _settings(root),
            asset_pack_builder=FakeAssetPackBuilder(),
            review_runner=FakeReviewRunner(("repair", 8.1), ("approve_mechanics", 9.5)),
            repair_runner=FakeBrightnessRepairRunner(),
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            source_short_dir=source_dir,
            output_dir=root / "session",
            generate_images=False,
        )

        assert result.status == "ready_for_owner_review"
        assert result.initial_decision == "repair"
        assert result.final_decision == "approve_mechanics"
        assert result.final_score == 9.5
        assert result.repaired_video == str(root / "session" / "brightness_repair" / "final.mp4")
        assert result.final_video == result.repaired_video
        assert (root / "session" / "faceless_recreation_production_session_result.json").exists()
        assert (root / "session" / "faceless_recreation_production_session_report.md").exists()


def test_production_session_approves_without_repair_when_initial_review_passes() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-production-pass-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json, source_dir = _write_inputs(root)
        service = FacelessRecreationProductionSessionService(
            _settings(root),
            asset_pack_builder=FakeAssetPackBuilder(),
            review_runner=FakeReviewRunner(("approve_mechanics", 9.0)),
            repair_runner=FakeBrightnessRepairRunner(),
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            source_short_dir=source_dir,
            output_dir=root / "session",
            generate_images=False,
        )

        assert result.status == "ready_for_owner_review"
        assert result.repair_result_path is None
        assert result.final_decision == "approve_mechanics"
        assert result.final_video == str(root / "session" / "visual_asset_pack" / "final.mp4")


def test_production_session_builds_fast_source_short_when_none_is_provided() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-production-fast-source-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json, _source_dir = _write_inputs(root)
        service = FacelessRecreationProductionSessionService(
            _settings(root),
            asset_pack_builder=FakeAssetPackBuilder(),
            review_runner=FakeReviewRunner(("approve_mechanics", 8.0)),
            repair_runner=FakeBrightnessRepairRunner(),
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            output_dir=root / "session",
            generate_images=False,
        )

        source_dir = Path(result.source_short_dir)
        assert result.status == "ready_for_owner_review"
        assert (source_dir / "script.md").exists()
        assert (source_dir / "narration.wav").exists()
        assert (source_dir / "subtitles.srt").exists()
        assert "fast_source_ready" in {str(step.get("status")) for step in result.steps}


def test_production_session_uses_sapi_fallback_when_piper_fails(monkeypatch) -> None:
    with TemporaryDirectory(prefix="atnos-faceless-production-sapi-source-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json, _source_dir = _write_inputs(root)

        def _fail_piper(*args, **kwargs):
            raise TimeoutError("piper stalled")

        def _fake_sapi(*, text: str, output_path: Path, timeout_s: int) -> None:
            production_module._write_synthetic_audio(output_path, duration_sec=2.0)

        monkeypatch.setattr(production_module, "_write_piper_audio_fast", _fail_piper)
        monkeypatch.setattr(production_module, "_write_sapi_audio", _fake_sapi)
        service = FacelessRecreationProductionSessionService(
            Settings(app_env="development", app_root=root, data_dir=root / "data"),
            asset_pack_builder=FakeAssetPackBuilder(),
            review_runner=FakeReviewRunner(("approve_mechanics", 8.0)),
            repair_runner=FakeBrightnessRepairRunner(),
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            output_dir=root / "session",
            generate_images=False,
        )

        runtime = json.loads((Path(result.source_short_dir) / "source_short_runtime.json").read_text(encoding="utf-8"))
        assert runtime["tts_status"] == "sapi_fallback:TimeoutError"
        assert (Path(result.source_short_dir) / "narration.wav").exists()


def test_production_session_uses_adaptive_brightness_repair() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-production-adaptive-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json, source_dir = _write_inputs(root)
        repair_runner = FakeBrightnessRepairRunner()
        service = FacelessRecreationProductionSessionService(
            _settings(root),
            asset_pack_builder=FakeAssetPackBuilder(),
            review_runner=FakeReviewRunner(
                ("repair", 9.0, {"avg_brightness": 0.56}, {"avg_brightness": 0.14}),
                ("approve_mechanics", 9.8),
            ),
            repair_runner=repair_runner,
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            source_short_dir=source_dir,
            output_dir=root / "session",
            generate_images=False,
        )

        assert result.status == "ready_for_owner_review"
        assert repair_runner.calls[0]["brightness_factor"] > 2.5
        assert repair_runner.calls[0]["contrast_factor"] == 1.03


class FakeAssetPackBuilder:
    def build(
        self,
        *,
        visual_plan_json: Path,
        output_dir: Path,
        plan_index: int = 0,
        source_short_dir: Path | None = None,
        max_scenes: int = 5,
        generate_images: bool = True,
        render_video: bool = True,
        width: int = 768,
        height: int = 1344,
        steps: int | None = None,
    ) -> VisualRecreationAssetPackResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        final = output_dir / "final.mp4"
        final.write_bytes(b"fake-video")
        result = VisualRecreationAssetPackResult(
            status="video_rendered",
            output_dir=str(output_dir),
            source_plan_path=str(visual_plan_json),
            source_short_dir=str(source_short_dir) if source_short_dir else None,
            tool_sequence_path=str(output_dir / "tool_sequence.json"),
            comfyui_prompts_path=str(output_dir / "comfyui_prompts.json"),
            runway_prompts_path=str(output_dir / "runway_prompts.md"),
            capcut_edit_plan_path=str(output_dir / "capcut_edit_plan.md"),
            scene_assets=(
                VisualPackSceneAsset(
                    scene_id="scene_01_hook",
                    timing="0-2s",
                    overlay_text="HOOK",
                    prompt_path=str(output_dir / "scene_01_prompt.txt"),
                    image_path=str(output_dir / "scene_01.png"),
                    status="generated",
                ),
            ),
            final_mp4=str(final),
            render_qa_path=str(output_dir / "render_qa.json"),
            created_at="2026-05-29T00:00:00+00:00",
        )
        (output_dir / "visual_recreation_asset_pack_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result


class FakeReviewRunner:
    def __init__(self, *sequence: tuple) -> None:
        self.sequence = list(sequence)
        self.calls = 0

    def review_from_paths(
        self,
        *,
        reference_observation_json,
        candidate_video_path,
        output_dir,
        sample_times_sec=(0.5, 2.0, 5.0, 10.0, 20.0, 35.0),
    ) -> FormatRecreationReview:
        item = self.sequence[min(self.calls, len(self.sequence) - 1)]
        decision, score = item[0], item[1]
        reference_summary = item[2] if len(item) > 2 else {}
        candidate_summary = item[3] if len(item) > 3 else {}
        self.calls += 1
        review = FormatRecreationReview(
            review_id=f"fake-review-{self.calls}",
            reviewed_at="2026-05-29T00:00:00+00:00",
            reference_source_ref="ref",
            reference_title="Reference",
            candidate_source_ref="candidate",
            candidate_video_path=str(candidate_video_path),
            output_dir=str(output_dir),
            signal_scores=(FormatSignalScore(name="mock", score=score, evidence="mock"),),
            overall_score=score,
            decision=decision,
            repair_instructions=("repair brightness",) if decision == "repair" else (),
            safety_notes=("format only",),
            reference_summary=reference_summary,
            candidate_summary=candidate_summary,
        )
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "format_recreation_review.json").write_text(
            json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return review


class FakeBrightnessRepairRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, float]] = []

    def run(
        self,
        *,
        asset_pack_dir,
        output_dir,
        brightness_factor: float = 1.28,
        contrast_factor: float = 1.08,
        sharpness_factor: float = 1.04,
        render_video: bool = True,
    ) -> VisualAssetPackBrightnessRepairResult:
        self.calls.append(
            {
                "brightness_factor": brightness_factor,
                "contrast_factor": contrast_factor,
                "sharpness_factor": sharpness_factor,
            }
        )
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        final = output_path / "final.mp4"
        final.write_bytes(b"fake-repaired-video")
        result = VisualAssetPackBrightnessRepairResult(
            status="video_rendered",
            source_asset_pack_dir=str(asset_pack_dir),
            output_dir=str(output_path),
            repaired_scene_paths=(str(output_path / "scene_01.png"),),
            source_short_dir=None,
            final_mp4=str(final),
            render_qa_path=str(output_path / "render_qa.json"),
            brightness_factor=brightness_factor,
            contrast_factor=contrast_factor,
            sharpness_factor=sharpness_factor,
            created_at="2026-05-29T00:00:00+00:00",
            notes=("mock",),
        )
        (output_path / "visual_asset_pack_brightness_repair_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result


def _settings(root: Path) -> Settings:
    return Settings(app_env="test", app_root=root, data_dir=root / "data")


def _write_inputs(root: Path) -> tuple[Path, Path, Path, Path]:
    visual_plan_json = root / "visual_recreation_plans.json"
    visual_plan_json.write_text("[{}]", encoding="utf-8")
    benchmark_json = root / "benchmark_run.json"
    benchmark_json.write_text(
        json.dumps(
            {
                "visual_recreation_plans_path": str(visual_plan_json),
                "replication_briefs": [{"legal_recreation_topic": "safe topic"}],
                "top_candidates": [{"title": "top reference"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    reference_json = root / "reference.json"
    reference_json.write_text("{}", encoding="utf-8")
    source_dir = root / "source_short"
    source_dir.mkdir()
    return benchmark_json, visual_plan_json, reference_json, source_dir
