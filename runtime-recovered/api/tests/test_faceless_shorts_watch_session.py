from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.faceless_recreation_batch_session import (
    FacelessRecreationBatchItem,
    FacelessRecreationBatchSessionResult,
)
from content.faceless_shorts_watch_session import (
    FacelessShortsWatchSessionConfig,
    FacelessShortsWatchSessionService,
)


def test_watch_session_writes_default_config_state_log_and_report() -> None:
    with TemporaryDirectory(prefix="atnos-watch-session-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = FacelessShortsWatchSessionService(settings, batch_runner=FakeBatchRunner())

        config_path = service.write_default_config()
        result = service.run_once(config_path=config_path, output_dir=root / "watch")

        assert result.status == "ready_for_owner_review"
        assert result.ready_count == 1
        assert Path(result.report_path).exists()
        assert service.state_path().exists()
        state = json.loads(service.state_path().read_text(encoding="utf-8"))
        assert state["runs_total"] == 1
        assert state["last_status"] == "ready_for_owner_review"
        assert state["format_memory_path"]
        assert Path(result.format_memory_path or "").exists()
        assert Path(result.format_memory_report_path or "").exists()
        assert service.log_path().exists()
        assert "faceless_shorts_watch_session_completed" in service.log_path().read_text(encoding="utf-8")


def test_watch_session_disabled_config_does_not_run_batch() -> None:
    with TemporaryDirectory(prefix="atnos-watch-session-disabled-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        runner = FakeBatchRunner()
        service = FacelessShortsWatchSessionService(settings, batch_runner=runner)
        config_path = root / "disabled.json"
        config = FacelessShortsWatchSessionConfig(enabled=False)
        config_path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        result = service.run_once(config_path=config_path, output_dir=root / "watch")

        assert result.status == "disabled"
        assert runner.calls == 0
        assert Path(result.report_path).exists()


def test_watch_session_accepts_utf8_bom_config_from_powershell() -> None:
    with TemporaryDirectory(prefix="atnos-watch-session-bom-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = FacelessShortsWatchSessionService(settings, batch_runner=FakeBatchRunner())
        config_path = root / "watch_config_bom.json"
        payload = json.dumps(FacelessShortsWatchSessionConfig(max_items=1).to_dict(), ensure_ascii=False)
        config_path.write_text(payload, encoding="utf-8-sig")

        result = service.run_once(config_path=config_path, output_dir=root / "watch")

        assert result.status == "ready_for_owner_review"
        assert result.ready_count == 1


def test_watch_session_memory_guided_config_overrides_queries_from_format_memory() -> None:
    with TemporaryDirectory(prefix="atnos-watch-session-memory-guided-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        memory_path = settings.data_dir / "learning" / "faceless-ai-shorts" / "format_memory.json"
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(
            json.dumps(
                {
                    "top_patterns": [{"key": "ai_animal_microstory"}],
                    "top_channels": [{"key": "Cute Cat"}],
                    "recommendations": ["generate_next_batch_from_pattern:ai_animal_microstory:max_items=3"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        runner = FakeBatchRunner()
        service = FacelessShortsWatchSessionService(settings, batch_runner=runner)
        config_path = root / "memory_guided.json"
        config_path.write_text(
            json.dumps(FacelessShortsWatchSessionConfig(memory_guided=True, max_items=1).to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )

        result = service.run_once(config_path=config_path, output_dir=root / "watch")

        assert result.status == "ready_for_owner_review"
        assert result.memory_guided_plan_path
        assert Path(result.memory_guided_plan_path).exists()
        assert runner.last_kwargs is not None
        assert "hashtag:aicatstory" in tuple(runner.last_kwargs["queries"])
        assert runner.last_kwargs["max_items"] == 1


class FakeBatchRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.last_kwargs = None

    def run(self, **kwargs) -> FacelessRecreationBatchSessionResult:
        self.calls += 1
        self.last_kwargs = dict(kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        final = output_dir / "item_01" / "final.mp4"
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"fake-video")
        report = output_dir / "faceless_recreation_batch_learning_report.md"
        report.write_text("# batch report", encoding="utf-8")
        benchmark = output_dir / "benchmark.json"
        benchmark.write_text(
            json.dumps(
                {
                    "top_candidates": [
                        {
                            "title": "AI cat reference",
                            "channel": "test channel",
                            "url": "https://youtube.com/shorts/test",
                            "video_id": "test",
                            "view_count": 123456,
                            "duration_s": 31,
                            "pattern": "ai_animal_microstory",
                            "channel_url": "https://youtube.com/channel/test",
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result_path = output_dir / "faceless_recreation_batch_session_result.json"
        result = FacelessRecreationBatchSessionResult(
            status="ready_for_owner_review",
            batch_dir=str(output_dir),
            benchmark_json=str(benchmark),
            visual_plan_json=str(output_dir / "visual_plans.json"),
            reference_observation_summary_json=str(output_dir / "reference.json"),
            max_items=1,
            ready_count=1,
            failed_count=0,
            items=(
                FacelessRecreationBatchItem(
                    index=0,
                    status="ready_for_owner_review",
                    source_title="AI cat reference",
                    source_channel="test channel",
                    source_views=123456,
                    source_url="https://youtube.com/shorts/test",
                    session_dir=str(output_dir / "item_01"),
                    final_video=str(final),
                    final_score=9.1,
                    final_decision="approve_mechanics",
                    result_path=str(output_dir / "item_01" / "result.json"),
                ),
            ),
            learning_report_path=str(report),
            result_path=str(result_path),
            created_at="2026-05-29T00:00:00+00:00",
            safety_notes=("format only",),
        )
        result_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result
