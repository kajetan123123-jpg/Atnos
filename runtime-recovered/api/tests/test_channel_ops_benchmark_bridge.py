from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from channel_ops import ChannelOpsService, ContentStatus


def test_enqueue_benchmark_briefs_writes_channel_queue_and_brief_files() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = ChannelOpsService(settings)
        service.register_channel(
            channel_id="faceless-ai-pl",
            display_name="Faceless AI PL",
            niche="faceless AI shorts",
            language="pl",
            audience="tworcy solo",
            daily_target_count=1,
        )
        benchmark_path = root / "benchmark_run.json"
        benchmark_path.write_text(json.dumps(_benchmark_payload(), ensure_ascii=False, indent=2), encoding="utf-8")

        items = service.enqueue_benchmark_briefs("faceless-ai-pl", benchmark_path, limit=1)

        assert len(items) == 1
        item = items[0]
        assert item.status == ContentStatus.IDEA
        assert item.source == "faceless_shorts_benchmark"
        assert item.source_ref == "benchmark-brief-01-ai_animal_microstory"
        assert item.brief_path is not None
        assert Path(item.brief_path).exists()
        assert item.metadata is not None
        assert item.metadata["source_views"] == 243924
        assert item.metadata["visual_recreation_plan_path"] is not None
        assert Path(item.metadata["visual_recreation_plan_path"]).exists()
        queue = service.list_queue("faceless-ai-pl")
        assert queue[0].topic.startswith("Odtworz format ai_animal_microstory")
        assert "Nie kopiuj zrodla" in queue[0].topic
        markdown_path = Path(item.metadata["markdown_brief_path"])
        assert markdown_path.exists()
        assert "Forbidden copying" in markdown_path.read_text(encoding="utf-8")


def test_enqueue_benchmark_briefs_skips_duplicate_topics() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        service = ChannelOpsService(settings)
        service.register_channel(
            channel_id="faceless-ai-pl",
            display_name="Faceless AI PL",
            niche="faceless AI shorts",
            language="pl",
            audience="tworcy solo",
            daily_target_count=1,
        )
        benchmark_path = root / "benchmark_run.json"
        benchmark_path.write_text(json.dumps(_benchmark_payload(), ensure_ascii=False, indent=2), encoding="utf-8")

        first = service.enqueue_benchmark_briefs("faceless-ai-pl", benchmark_path, limit=2)
        second = service.enqueue_benchmark_briefs("faceless-ai-pl", benchmark_path, limit=2)

        assert len(first) == 1
        assert second == []
        assert len(service.list_queue("faceless-ai-pl")) == 1


def _benchmark_payload() -> dict[str, object]:
    return {
        "run_id": "test_benchmark",
        "replication_briefs": [
            {
                "brief_id": "benchmark-brief-01-ai_animal_microstory",
                "source_pattern": "ai_animal_microstory",
                "source_title": "Poor cat became a fashion designer #catshorts",
                "source_channel": "mini meownmeow",
                "source_url": "https://www.youtube.com/watch?v=abc123",
                "source_video_id": "abc123",
                "source_views": 243924,
                "legal_recreation_topic": (
                    "Oryginalny kot-agent odkrywa błąd w automatycznym short pipeline "
                    "i naprawia go przez QA."
                ),
                "hook_template": "Ten kot zatrzymał słabego shorta.",
                "beat_timeline": ["0-2s: hook", "2-8s: problem"],
                "visual_rules": ["oryginalna postać", "safe zone"],
                "qa_rules": ["private/manual only"],
                "forbidden_copying": ["Nie kopiuj exact script, audio, footage ani postaci."],
            }
        ],
        "visual_recreation_plans": [
            {
                "plan_id": "visual-plan-01-ai_animal_microstory",
                "brief_id": "benchmark-brief-01-ai_animal_microstory",
                "source_pattern": "ai_animal_microstory",
                "source_title": "Poor cat became a fashion designer #catshorts",
                "source_channel": "mini meownmeow",
                "source_url": "https://www.youtube.com/watch?v=abc123",
                "source_video_id": "abc123",
                "source_thumbnail_path": None,
                "reference_visual_summary": "cat transformation visual",
                "safe_similarity_target": "format mechanics only",
                "character_sheet_prompt": "original cat character sheet",
                "scene_prompts": [
                    {
                        "scene_id": "scene_01_hook",
                        "timing": "0-2s",
                        "goal": "hook",
                        "image_prompt": "original cat in vertical frame",
                        "motion_prompt": "push in",
                        "overlay_text": "HOOK",
                        "camera": "close-up",
                        "negative_prompt": "watermark",
                    }
                ],
                "thumbnail_prompt": "original thumbnail prompt",
                "caption_style": "large Polish captions",
                "color_palette": ["orange", "teal"],
                "camera_style": "cinematic",
                "repair_checklist": ["first frame readable"],
                "forbidden_copying": ["Nie kopiuj exact script, audio, footage ani postaci."],
            }
        ],
    }
