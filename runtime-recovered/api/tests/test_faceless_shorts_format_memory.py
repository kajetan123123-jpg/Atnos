from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.faceless_recreation_batch_session import (
    FacelessRecreationBatchItem,
    FacelessRecreationBatchSessionResult,
)
from content.faceless_shorts_format_memory import FacelessShortsFormatMemoryService


def test_format_memory_aggregates_patterns_channels_and_recreations() -> None:
    with TemporaryDirectory(prefix="atnos-format-memory-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = FacelessShortsFormatMemoryService(settings)
        batch = _batch(root)
        benchmark = _benchmark()

        update = service.update_from_batch(batch=batch, benchmark_payload=benchmark)

        assert update["status"] == "updated"
        memory = json.loads(service.memory_path().read_text(encoding="utf-8"))
        assert memory["runs_total"] == 1
        assert memory["patterns"]["ai_animal_microstory"]["observed_count"] == 2
        assert memory["patterns"]["ai_animal_microstory"]["ready_count"] == 1
        assert memory["channels"]["Cute Cat"]["max_views"] == 4020
        assert memory["top_patterns"][0]["key"] == "ai_animal_microstory"
        assert memory["recreations"][0]["final_decision"] == "approve_mechanics"
        assert "repeat_format_pattern:ai_animal_microstory" in memory["recommendations"]
        assert service.report_path().exists()
        assert service.log_path().exists()


def test_format_memory_update_is_idempotent_for_same_batch_result() -> None:
    with TemporaryDirectory(prefix="atnos-format-memory-idempotent-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = FacelessShortsFormatMemoryService(settings)
        batch = _batch(root)

        first = service.update_from_batch(batch=batch, benchmark_payload=_benchmark())
        second = service.update_from_batch(batch=batch, benchmark_payload=_benchmark())

        memory = json.loads(service.memory_path().read_text(encoding="utf-8"))
        assert first["status"] == "updated"
        assert second["status"] == "already_ingested"
        assert memory["runs_total"] == 1
        assert memory["patterns"]["ai_animal_microstory"]["observed_count"] == 2


def test_format_memory_can_add_new_recreation_from_existing_batch_result() -> None:
    with TemporaryDirectory(prefix="atnos-format-memory-reingest-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = FacelessShortsFormatMemoryService(settings)
        first_batch = _batch(root)
        second_final = root / "final-2.mp4"
        second_final.write_bytes(b"fake-2")
        second_batch = FacelessRecreationBatchSessionResult(
            status="ready_for_owner_review",
            batch_dir=first_batch.batch_dir,
            benchmark_json=first_batch.benchmark_json,
            visual_plan_json=first_batch.visual_plan_json,
            reference_observation_summary_json=first_batch.reference_observation_summary_json,
            max_items=2,
            ready_count=2,
            failed_count=0,
            items=(
                *first_batch.items,
                FacelessRecreationBatchItem(
                    index=1,
                    status="ready_for_owner_review",
                    source_title="Sauce is not a crime",
                    source_channel="Cute Cat",
                    source_views=1794,
                    source_url="https://youtube.com/watch?v=cat2",
                    session_dir=str(root / "item_02"),
                    final_video=str(second_final),
                    final_score=9.9,
                    final_decision="approve_mechanics",
                    result_path=str(root / "item_02" / "result.json"),
                ),
            ),
            learning_report_path=first_batch.learning_report_path,
            result_path=first_batch.result_path,
            created_at=first_batch.created_at,
            safety_notes=first_batch.safety_notes,
        )

        first = service.update_from_batch(batch=first_batch, benchmark_payload=_benchmark())
        second = service.update_from_batch(batch=second_batch, benchmark_payload=_benchmark())

        memory = json.loads(service.memory_path().read_text(encoding="utf-8"))
        assert first["status"] == "updated"
        assert second["status"] == "updated_existing_source"
        assert memory["runs_total"] == 1
        assert memory["patterns"]["ai_animal_microstory"]["observed_count"] == 2
        assert memory["patterns"]["ai_animal_microstory"]["ready_count"] == 2
        assert memory["patterns"]["ai_animal_microstory"]["recreated_count"] == 2


def test_format_memory_updates_from_paths() -> None:
    with TemporaryDirectory(prefix="atnos-format-memory-paths-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        batch = _batch(root)
        benchmark_path = root / "benchmark.json"
        benchmark_path.write_text(json.dumps(_benchmark(), ensure_ascii=False, indent=2), encoding="utf-8")
        batch_path = root / "batch_result.json"
        payload = batch.to_dict()
        payload["benchmark_json"] = str(benchmark_path)
        batch_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        update = FacelessShortsFormatMemoryService(settings).update_from_paths(
            batch_result_json=batch_path,
        )

        assert update["status"] == "updated"
        assert Path(update["memory_path"]).exists()


def _batch(root: Path) -> FacelessRecreationBatchSessionResult:
    final = root / "final.mp4"
    final.write_bytes(b"fake")
    result_path = root / "batch_result.json"
    return FacelessRecreationBatchSessionResult(
        status="ready_for_owner_review",
        batch_dir=str(root),
        benchmark_json=str(root / "benchmark.json"),
        visual_plan_json=str(root / "visual_plans.json"),
        reference_observation_summary_json=str(root / "reference.json"),
        max_items=1,
        ready_count=1,
        failed_count=0,
        items=(
            FacelessRecreationBatchItem(
                index=0,
                status="ready_for_owner_review",
                source_title="Tears and Hope",
                source_channel="Cute Cat",
                source_views=4020,
                source_url="https://youtube.com/watch?v=cat1",
                session_dir=str(root / "item_01"),
                final_video=str(final),
                final_score=9.7,
                final_decision="approve_mechanics",
                result_path=str(root / "item_01" / "result.json"),
            ),
        ),
        learning_report_path=str(root / "learning.md"),
        result_path=str(result_path),
        created_at="2026-05-29T00:00:00+00:00",
        safety_notes=("format only",),
    )


def _benchmark() -> dict[str, object]:
    return {
        "top_candidates": [
            {
                "title": "Tears and Hope",
                "channel": "Cute Cat",
                "url": "https://youtube.com/watch?v=cat1",
                "video_id": "cat1",
                "view_count": 4020,
                "duration_s": 42,
                "pattern": "ai_animal_microstory",
                "channel_url": "https://youtube.com/channel/cute-cat",
                "thumbnail_url": "https://i.ytimg.com/vi/cat1/maxresdefault.jpg",
            },
            {
                "title": "Sauce is not a crime",
                "channel": "Cute Cat",
                "url": "https://youtube.com/watch?v=cat2",
                "video_id": "cat2",
                "view_count": 1794,
                "duration_s": 35,
                "pattern": "ai_animal_microstory",
                "channel_url": "https://youtube.com/channel/cute-cat",
                "thumbnail_url": "https://i.ytimg.com/vi/cat2/maxresdefault.jpg",
            },
        ]
    }
