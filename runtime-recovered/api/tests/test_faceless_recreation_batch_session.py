from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.faceless_recreation_batch_session import FacelessRecreationBatchSessionService
from content.faceless_recreation_production_session import FacelessRecreationProductionSessionResult


def test_batch_session_runs_multiple_plan_indexes_and_writes_learning_report() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-batch-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json = _write_inputs(root)
        runner = FakeProductionRunner()
        service = FacelessRecreationBatchSessionService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            production_runner=runner,
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            output_dir=root / "batch",
            max_items=2,
            generate_images=False,
        )

        assert result.status == "ready_for_owner_review"
        assert result.ready_count == 2
        assert [call["plan_index"] for call in runner.calls] == [0, 1]
        assert (root / "batch" / "faceless_recreation_batch_session_result.json").exists()
        report = (root / "batch" / "faceless_recreation_batch_learning_report.md").read_text(encoding="utf-8")
        assert "Highest-View References" in report
        assert "Do not copy source footage" in report


def test_batch_session_records_failed_item_without_stopping_batch() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-batch-fail-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json = _write_inputs(root)
        runner = FakeProductionRunner(fail_on_index=1)
        service = FacelessRecreationBatchSessionService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            production_runner=runner,
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            output_dir=root / "batch",
            max_items=2,
            generate_images=False,
        )

        assert result.ready_count == 1
        assert result.failed_count == 1
        assert result.items[1].status == "failed"
        assert result.items[1].error is not None


def test_batch_session_writes_running_checkpoint_after_each_completed_item() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-batch-checkpoint-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json = _write_inputs(root)
        runner = FakeProductionRunner(inspect_checkpoint_on_index=1)
        service = FacelessRecreationBatchSessionService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            production_runner=runner,
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            output_dir=root / "batch",
            max_items=2,
            generate_images=False,
        )

        assert result.ready_count == 2
        assert runner.checkpoint_snapshot is not None
        assert runner.checkpoint_snapshot["status"] == "running_checkpoint"
        assert runner.checkpoint_snapshot["ready_count"] == 1
        assert len(runner.checkpoint_snapshot["items"]) == 1


def test_batch_session_resume_skips_ready_existing_items() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-batch-resume-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json = _write_inputs(root)
        output_dir = root / "batch"
        existing_final = output_dir / "item_01" / "final.mp4"
        existing_final.parent.mkdir(parents=True, exist_ok=True)
        existing_final.write_bytes(b"existing-video")
        (output_dir / "faceless_recreation_batch_session_result.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "index": 0,
                            "status": "ready_for_owner_review",
                            "source_title": "AI cat saves friend",
                            "source_channel": "A",
                            "source_views": 1_000_000,
                            "source_url": "https://youtube.com/shorts/a",
                            "session_dir": str(existing_final.parent),
                            "final_video": str(existing_final),
                            "final_score": 9.8,
                            "final_decision": "approve_mechanics",
                            "result_path": str(existing_final.parent / "result.json"),
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        runner = FakeProductionRunner()
        service = FacelessRecreationBatchSessionService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            production_runner=runner,
        )

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            output_dir=output_dir,
            max_items=2,
            resume_existing=True,
            generate_images=False,
        )

        assert result.ready_count == 2
        assert [call["plan_index"] for call in runner.calls] == [1]
        assert result.items[0].final_video == str(existing_final)


def test_batch_session_emits_progress_events_for_observable_background_runs() -> None:
    with TemporaryDirectory(prefix="atnos-faceless-batch-progress-") as tmp:
        root = Path(tmp)
        benchmark_json, visual_plan_json, reference_json = _write_inputs(root)
        runner = FakeProductionRunner()
        service = FacelessRecreationBatchSessionService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            production_runner=runner,
        )
        events: list[dict[str, object]] = []

        result = service.run(
            benchmark_json=benchmark_json,
            visual_plan_json=visual_plan_json,
            reference_observation_json=reference_json,
            output_dir=root / "batch",
            max_items=2,
            generate_images=False,
            progress_callback=events.append,
        )

        phases = [event["phase"] for event in events]
        assert result.ready_count == 2
        assert "benchmark" in phases
        assert "observe_references" in phases
        assert phases.count("item_recreate") == 2
        assert phases.count("item_checkpoint") == 2
        assert phases[-1] == "completed"
        assert events[-1]["result_path"] == result.result_path


class FakeProductionRunner:
    def __init__(
        self,
        *,
        fail_on_index: int | None = None,
        inspect_checkpoint_on_index: int | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_on_index = fail_on_index
        self.inspect_checkpoint_on_index = inspect_checkpoint_on_index
        self.checkpoint_snapshot: dict[str, object] | None = None

    def run(self, **kwargs) -> FacelessRecreationProductionSessionResult:
        plan_index = int(kwargs["plan_index"])
        self.calls.append(kwargs)
        if self.inspect_checkpoint_on_index == plan_index:
            checkpoint_path = Path(kwargs["output_dir"]).parent / "faceless_recreation_batch_session_result.json"
            self.checkpoint_snapshot = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if self.fail_on_index == plan_index:
            raise RuntimeError("mock production failure")
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        final = output_dir / "final.mp4"
        final.write_bytes(b"fake-video")
        result = FacelessRecreationProductionSessionResult(
            status="ready_for_owner_review",
            session_dir=str(output_dir),
            benchmark_json=str(kwargs["benchmark_json"]),
            visual_plan_json=str(kwargs["visual_plan_json"]),
            reference_observation_json=str(kwargs["reference_observation_json"]),
            source_short_dir=str(output_dir / "source"),
            asset_pack_dir=str(output_dir / "visual_asset_pack"),
            asset_pack_video=str(output_dir / "visual_asset_pack" / "final.mp4"),
            initial_review_path=str(output_dir / "initial_format_review.json"),
            initial_decision="approve_mechanics",
            initial_score=9.0 + plan_index / 10,
            repair_result_path=None,
            repaired_video=None,
            final_review_path=str(output_dir / "initial_format_review.json"),
            final_decision="approve_mechanics",
            final_score=9.0 + plan_index / 10,
            final_video=str(final),
            steps=(),
            safety_notes=("format only",),
            created_at="2026-05-29T00:00:00+00:00",
        )
        (output_dir / "faceless_recreation_production_session_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result


def _write_inputs(root: Path) -> tuple[Path, Path, Path]:
    visual_plan_json = root / "visual_recreation_plans.json"
    visual_plan_json.write_text(json.dumps([{}, {}], ensure_ascii=False), encoding="utf-8")
    benchmark_json = root / "benchmark_run.json"
    benchmark_json.write_text(
        json.dumps(
            {
                "visual_recreation_plans_path": str(visual_plan_json),
                "top_candidates": [
                    {
                        "title": "AI cat saves friend",
                        "channel": "A",
                        "view_count": 1_000_000,
                        "url": "https://youtube.com/shorts/a",
                        "pattern": "ai_animal_microstory",
                    },
                    {
                        "title": "AI dog escape story",
                        "channel": "B",
                        "view_count": 750_000,
                        "url": "https://youtube.com/shorts/b",
                        "pattern": "ai_animal_microstory",
                    },
                ],
                "replication_briefs": [
                    {"legal_recreation_topic": "topic one"},
                    {"legal_recreation_topic": "topic two"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    reference_json = root / "public_player_observation_summary.json"
    reference_json.write_text(
        json.dumps(
            [
                {"observation_package": {"source_ref": "a", "frames": []}},
                {"observation_package": {"source_ref": "b", "frames": []}},
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return benchmark_json, visual_plan_json, reference_json
