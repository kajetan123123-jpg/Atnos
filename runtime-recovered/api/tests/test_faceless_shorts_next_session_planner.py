from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.faceless_shorts_next_session_planner import FacelessShortsNextSessionPlanner


def test_next_session_planner_uses_top_memory_pattern_and_recommended_count() -> None:
    with TemporaryDirectory(prefix="atnos-next-session-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        memory_path = settings.data_dir / "learning" / "faceless-ai-shorts" / "format_memory.json"
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(
            json.dumps(
                {
                    "top_patterns": [{"key": "ai_animal_microstory"}],
                    "top_channels": [{"key": "Cute Cat"}],
                    "recommendations": [
                        "repeat_format_pattern:ai_animal_microstory",
                        "generate_next_batch_from_pattern:ai_animal_microstory:max_items=3",
                        "watch_more_from_channel:Cute Cat",
                    ],
                    "safety_notes": ["format only"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        plan = FacelessShortsNextSessionPlanner(settings).write()

        assert plan.status == "memory_guided"
        assert plan.selected_pattern == "ai_animal_microstory"
        assert plan.selected_channel == "Cute Cat"
        assert plan.max_items == 3
        assert "hashtag:aicatstory" in plan.queries
        assert '"Cute Cat" "#shorts" "AI"' in plan.queries
        assert Path(plan.plan_path).exists()
        assert Path(plan.report_path).exists()
        assert Path(plan.config_path).exists()
        payload = json.loads(Path(plan.config_path).read_text(encoding="utf-8"))
        assert payload["memory_guided"] is True
        assert payload["max_items"] == 3


def test_next_session_planner_falls_back_to_default_queries_without_memory() -> None:
    with TemporaryDirectory(prefix="atnos-next-session-empty-") as tmp:
        root = Path(tmp)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")

        plan = FacelessShortsNextSessionPlanner(settings).write(max_items=2)

        assert plan.status == "default_queries"
        assert plan.selected_pattern is None
        assert plan.max_items == 2
        assert "hashtag:aicat" in plan.queries
        assert "No cumulative format memory exists yet" in plan.rationale[0]
