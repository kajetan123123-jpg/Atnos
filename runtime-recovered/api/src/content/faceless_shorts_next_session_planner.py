from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import re
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES
from content.faceless_shorts_format_memory import FacelessShortsFormatMemoryService


@dataclass(frozen=True, slots=True)
class FacelessShortsNextSessionPlan:
    plan_id: str
    created_at: str
    status: str
    source_memory_path: str
    selected_pattern: str | None
    selected_channel: str | None
    queries: tuple[str, ...]
    max_items: int
    per_query_limit: int
    top_limit: int
    brief_limit: int
    sample_times_sec: tuple[float, ...]
    rationale: tuple[str, ...]
    safety_notes: tuple[str, ...]
    config_payload: dict[str, Any]
    plan_path: str
    report_path: str
    config_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FacelessShortsNextSessionPlanner:
    """Builds the next watch/recreate session from cumulative format memory."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.memory = FacelessShortsFormatMemoryService(settings)

    def plan_path(self) -> Path:
        return self.settings.data_dir / "learning" / "faceless-ai-shorts" / "next_watch_session_plan.json"

    def report_path(self) -> Path:
        return self.settings.data_dir / "learning" / "faceless-ai-shorts" / "next_watch_session_plan.md"

    def config_path(self) -> Path:
        return self.settings.data_dir / "learning" / "faceless-ai-shorts" / "watch_session_memory_guided_config.json"

    def build(
        self,
        *,
        max_items: int | None = None,
        config_path: str | Path | None = None,
    ) -> FacelessShortsNextSessionPlan:
        memory = self._load_memory()
        created_at = datetime.now(UTC)
        plan_id = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_faceless_next_session_plan"
        selected_pattern = self._top_key(memory, "top_patterns")
        selected_channel = self._top_key(memory, "top_channels")
        parsed_max_items = max_items or self._recommended_max_items(memory) or 3
        parsed_max_items = max(1, min(int(parsed_max_items), 10))

        queries = self._build_queries(selected_pattern, selected_channel)
        top_limit = max(8, parsed_max_items * 5)
        brief_limit = parsed_max_items
        per_query_limit = 8
        sample_times_sec = (0.5, 2.0, 5.0, 10.0, 20.0, 35.0)
        safety_notes = tuple(memory.get("safety_notes") or _default_safety_notes())
        rationale = self._build_rationale(
            memory=memory,
            selected_pattern=selected_pattern,
            selected_channel=selected_channel,
            max_items=parsed_max_items,
        )
        target_config_path = Path(config_path).resolve() if config_path else self.config_path()
        config_payload = {
            "enabled": True,
            "memory_guided": True,
            "queries": list(queries),
            "max_items": parsed_max_items,
            "per_query_limit": per_query_limit,
            "top_limit": top_limit,
            "brief_limit": brief_limit,
            "sample_times_sec": list(sample_times_sec),
            "keep_public_frames": False,
            "allow_duration_anomalies": False,
            "max_scenes": 5,
            "width": 768,
            "height": 1344,
            "steps": 8,
            "generate_images": True,
            "render_video": True,
            "notes": [
                "Memory-guided run generated from cumulative faceless Shorts format memory.",
                "Watch public Shorts for format mechanics only.",
                "Do not copy source footage, voices, exact scripts, watermarks, branding or character identity.",
                "Generated outputs stop at owner review; no public upload.",
            ],
        }
        return FacelessShortsNextSessionPlan(
            plan_id=plan_id,
            created_at=created_at.isoformat(),
            status="memory_guided" if memory else "default_queries",
            source_memory_path=str(self.memory.memory_path()),
            selected_pattern=selected_pattern,
            selected_channel=selected_channel,
            queries=queries,
            max_items=parsed_max_items,
            per_query_limit=per_query_limit,
            top_limit=top_limit,
            brief_limit=brief_limit,
            sample_times_sec=sample_times_sec,
            rationale=rationale,
            safety_notes=safety_notes,
            config_payload=config_payload,
            plan_path=str(self.plan_path()),
            report_path=str(self.report_path()),
            config_path=str(target_config_path),
        )

    def write(
        self,
        *,
        max_items: int | None = None,
        config_path: str | Path | None = None,
    ) -> FacelessShortsNextSessionPlan:
        plan = self.build(max_items=max_items, config_path=config_path)
        plan_file = Path(plan.plan_path)
        report_file = Path(plan.report_path)
        config_file = Path(plan.config_path)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        report_file.write_text(render_next_session_plan_report(plan), encoding="utf-8")
        config_file.write_text(json.dumps(plan.config_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan

    def _load_memory(self) -> dict[str, Any]:
        path = self.memory.memory_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _top_key(memory: dict[str, Any], field: str) -> str | None:
        items = memory.get(field)
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        if not isinstance(first, dict):
            return None
        key = str(first.get("key") or "").strip()
        return key or None

    @staticmethod
    def _recommended_max_items(memory: dict[str, Any]) -> int | None:
        for item in memory.get("recommendations") or []:
            match = re.search(r":max_items=(\d+)\b", str(item))
            if match:
                return int(match.group(1))
        return None

    def _build_queries(self, selected_pattern: str | None, selected_channel: str | None) -> tuple[str, ...]:
        queries: list[str]
        if selected_pattern == "ai_animal_microstory":
            queries = [
                "hashtag:aicatstory",
                "hashtag:aicat",
                "hashtag:catshorts",
                '"#aicat" "#shorts" "story"',
                '"#catshorts" "#ai"',
                '"#aistory" "#shorts"',
            ]
        elif selected_pattern == "general_ai_faceless":
            queries = [
                '"#ai" "#shorts" "story"',
                '"#shorts" "AI generated"',
                '"#facelessshorts" "#ai"',
                "hashtag:aistory",
            ]
        elif selected_pattern:
            pattern_query = selected_pattern.replace("_", " ")
            queries = [
                f'"{pattern_query}" "#shorts"',
                '"#ai" "#shorts" "story"',
                '"#facelessshorts" "#ai"',
            ]
        else:
            queries = list(DEFAULT_FACELESS_AI_QUERIES)

        if selected_channel and selected_channel.casefold() not in {"unknown", "unknown_channel"}:
            queries.append(f'"{selected_channel}" "#shorts" "AI"')
        return tuple(dict.fromkeys(item for item in queries if item.strip()))

    @staticmethod
    def _build_rationale(
        *,
        memory: dict[str, Any],
        selected_pattern: str | None,
        selected_channel: str | None,
        max_items: int,
    ) -> tuple[str, ...]:
        if not memory:
            return (
                "No cumulative format memory exists yet, so the next run uses the broad faceless AI Shorts query set.",
                f"Generate up to {max_items} local recreations and let the run create the first memory baseline.",
            )
        lines = []
        if selected_pattern:
            lines.append(f"Repeat pattern `{selected_pattern}` because it is the strongest pattern in format_memory.")
        if selected_channel:
            lines.append(f"Watch more from channel `{selected_channel}` because it ranks highest in learned channel stats.")
        lines.append(f"Create up to {max_items} outputs, then stop at owner review with no public upload.")
        lines.append("Use public sources only for mechanics: pacing, framing, captions, contrast, and beat structure.")
        return tuple(lines)


def render_next_session_plan_report(plan: FacelessShortsNextSessionPlan) -> str:
    queries = "\n".join(f"- `{item}`" for item in plan.queries) or "- none"
    rationale = "\n".join(f"- {item}" for item in plan.rationale) or "- none"
    safety = "\n".join(f"- {item}" for item in plan.safety_notes) or "- none"
    return "\n".join(
        [
            "# Faceless Shorts Next Watch Session Plan",
            "",
            f"- status: `{plan.status}`",
            f"- selected_pattern: `{plan.selected_pattern or 'n/a'}`",
            f"- selected_channel: `{plan.selected_channel or 'n/a'}`",
            f"- max_items: `{plan.max_items}`",
            f"- per_query_limit: `{plan.per_query_limit}`",
            f"- top_limit: `{plan.top_limit}`",
            f"- config_path: `{plan.config_path}`",
            "",
            "## Queries",
            queries,
            "",
            "## Rationale",
            rationale,
            "",
            "## Safety",
            safety,
            "",
            "## How To Run",
            f"`python scripts/run_faceless_watch_session.py --config-json \"{plan.config_path}\"`",
            "",
        ]
    )


def _default_safety_notes() -> tuple[str, ...]:
    return (
        "Use public Shorts for format mechanics only.",
        "Do not copy source footage, exact scripts, watermarks, voices, branding or character identity.",
        "Generated outputs stay local/private until owner approval.",
    )
