from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.faceless_recreation_batch_recovery import FacelessRecreationBatchRecoveryService


def test_recovery_rebuilds_batch_result_and_updates_memory() -> None:
    with TemporaryDirectory(prefix="atnos-batch-recovery-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        benchmark = root / "benchmark_run.json"
        visual_plans = root / "visual_recreation_plans.json"
        batch_dir = root / "watch" / "batch"
        item_dir = batch_dir / "item_01"
        item_dir.mkdir(parents=True)
        final_mp4 = item_dir / "final.mp4"
        final_mp4.write_bytes(b"fake-video")
        visual_plans.write_text(json.dumps([{}]), encoding="utf-8")
        benchmark.write_text(
            json.dumps(
                {
                    "visual_recreation_plans_path": str(visual_plans),
                    "top_candidates": [
                        {
                            "title": "AI cat saves friend",
                            "channel": "Cute Cat",
                            "view_count": 123456,
                            "url": "https://youtube.com/watch?v=test",
                            "pattern": "ai_animal_microstory",
                            "video_id": "test",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (item_dir / "faceless_recreation_production_session_result.json").write_text(
            json.dumps(
                {
                    "status": "ready_for_owner_review",
                    "session_dir": str(item_dir),
                    "final_video": str(final_mp4),
                    "final_score": 9.4,
                    "final_decision": "approve_mechanics",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = FacelessRecreationBatchRecoveryService(settings).recover(
            batch_dir=batch_dir,
            benchmark_json=benchmark,
        )

        assert result.status == "interrupted_recovered_ready_for_owner_review"
        assert result.recovered_count == 1
        assert result.ready_count == 1
        assert Path(result.batch_result_path).exists()
        payload = json.loads(Path(result.batch_result_path).read_text(encoding="utf-8"))
        assert payload["items"][0]["source_title"] == "AI cat saves friend"
        assert Path(result.format_memory_path or "").exists()
