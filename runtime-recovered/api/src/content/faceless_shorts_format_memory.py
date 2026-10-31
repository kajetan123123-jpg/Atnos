from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from content.faceless_recreation_batch_session import FacelessRecreationBatchSessionResult


class FacelessShortsFormatMemoryService:
    """Cumulative memory for faceless Shorts format learning sessions."""

    VERSION = 1

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def memory_path(self) -> Path:
        return self.settings.data_dir / "learning" / "faceless-ai-shorts" / "format_memory.json"

    def report_path(self) -> Path:
        return self.settings.data_dir / "learning" / "faceless-ai-shorts" / "format_memory_report.md"

    def log_path(self) -> Path:
        return self.settings.data_dir / "logs" / "faceless_shorts_format_memory.jsonl"

    def update_from_paths(
        self,
        *,
        batch_result_json: str | Path,
        benchmark_json: str | Path | None = None,
    ) -> dict[str, Any]:
        batch_path = Path(batch_result_json).resolve()
        batch_payload = _load_object(batch_path)
        batch = _batch_from_payload(batch_payload)
        benchmark_path = Path(benchmark_json).resolve() if benchmark_json else Path(batch.benchmark_json).resolve()
        benchmark_payload = _load_object(benchmark_path)
        return self.update_from_batch(batch=batch, benchmark_payload=benchmark_payload)

    def update_from_batch(
        self,
        *,
        batch: FacelessRecreationBatchSessionResult,
        benchmark_payload: dict[str, Any],
    ) -> dict[str, Any]:
        memory = self._load_memory()
        source_key = str(batch.result_path or batch.batch_dir)
        memory.setdefault("sources_ingested", [])
        already_ingested = source_key in memory["sources_ingested"]
        if not already_ingested:
            memory["runs_total"] = int(memory.get("runs_total") or 0) + 1
            memory["sources_ingested"].append(source_key)
        memory["updated_at"] = datetime.now(UTC).isoformat()

        candidates = _candidate_items(benchmark_payload)
        if not already_ingested:
            for candidate in candidates:
                self._ingest_candidate(memory, candidate)

        before_recreations = len(memory.get("recreations") or [])
        for item in batch.items:
            candidate = candidates[item.index] if item.index < len(candidates) else {}
            self._ingest_recreation(memory, item.to_dict(), candidate)
        after_recreations = len(memory.get("recreations") or [])

        memory["top_patterns"] = _rank_stats(memory.get("patterns") or {})
        memory["top_channels"] = _rank_stats(memory.get("channels") or {})
        memory["recommendations"] = _recommendations(memory)

        self._write_memory(memory)
        self._append_log(batch=batch, memory=memory)
        status = "updated" if not already_ingested else (
            "updated_existing_source" if after_recreations > before_recreations else "already_ingested"
        )
        return {
            "status": status,
            "memory_path": str(self.memory_path()),
            "report_path": str(self.report_path()),
            "recommendations": list(memory.get("recommendations") or []),
        }

    def _load_memory(self) -> dict[str, Any]:
        path = self.memory_path()
        if not path.exists():
            now = datetime.now(UTC).isoformat()
            return {
                "version": self.VERSION,
                "created_at": now,
                "updated_at": now,
                "runs_total": 0,
                "sources_ingested": [],
                "patterns": {},
                "channels": {},
                "references": [],
                "recreations": [],
                "top_patterns": [],
                "top_channels": [],
                "recommendations": [],
                "safety_notes": [
                    "Use public Shorts for format mechanics only.",
                    "Do not copy source footage, exact scripts, watermarks, voices, branding or character identity.",
                    "Generated outputs stay local/private until owner approval.",
                ],
            }
        payload = _load_object(path)
        payload.setdefault("version", self.VERSION)
        payload.setdefault("patterns", {})
        payload.setdefault("channels", {})
        payload.setdefault("references", [])
        payload.setdefault("recreations", [])
        payload.setdefault("sources_ingested", [])
        return payload

    def _write_memory(self, memory: dict[str, Any]) -> None:
        path = self.memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
        self.report_path().write_text(render_format_memory_report(memory), encoding="utf-8")

    def _append_log(self, *, batch: FacelessRecreationBatchSessionResult, memory: dict[str, Any]) -> None:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "event": "faceless_shorts_format_memory_updated",
                        "batch_result_path": batch.result_path,
                        "runs_total": memory.get("runs_total"),
                        "top_patterns": memory.get("top_patterns", [])[:3],
                        "top_channels": memory.get("top_channels", [])[:3],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    @staticmethod
    def _ingest_candidate(memory: dict[str, Any], candidate: dict[str, Any]) -> None:
        pattern = _clean_key(candidate.get("pattern"), fallback="unknown_pattern")
        channel = _clean_key(candidate.get("channel"), fallback="unknown_channel")
        views = _optional_int(candidate.get("view_count")) or 0
        title = str(candidate.get("title") or "").strip()
        url = str(candidate.get("url") or "").strip()

        _update_observed_stats(memory["patterns"], pattern, views=views, title=title, url=url)
        _update_observed_stats(memory["channels"], channel, views=views, title=title, url=url)
        _append_unique_reference(memory, candidate)

    @staticmethod
    def _ingest_recreation(
        memory: dict[str, Any],
        item: dict[str, Any],
        candidate: dict[str, Any],
    ) -> None:
        if _recreation_exists(memory, item):
            return
        pattern = _clean_key(candidate.get("pattern"), fallback="unknown_pattern")
        channel = _clean_key(candidate.get("channel") or item.get("source_channel"), fallback="unknown_channel")
        score = _optional_float(item.get("final_score"))
        ready = item.get("status") == "ready_for_owner_review" or item.get("final_decision") == "approve_mechanics"
        for bucket, key in ((memory["patterns"], pattern), (memory["channels"], channel)):
            stats = bucket.setdefault(key, _empty_stats())
            stats["recreated_count"] = int(stats.get("recreated_count") or 0) + 1
            if ready:
                stats["ready_count"] = int(stats.get("ready_count") or 0) + 1
            if score is not None:
                stats["best_recreation_score"] = max(float(stats.get("best_recreation_score") or 0.0), score)
        _append_unique_recreation(memory, item=item, candidate=candidate, pattern=pattern, channel=channel)


def render_format_memory_report(memory: dict[str, Any]) -> str:
    pattern_lines = _render_ranked_lines(memory.get("top_patterns") or [])
    channel_lines = _render_ranked_lines(memory.get("top_channels") or [])
    references = "\n".join(
        f"- {item.get('title') or 'untitled'} | {item.get('channel') or 'unknown'} | views={item.get('views', 0)} | {item.get('pattern') or 'unknown'}"
        for item in list(memory.get("references") or [])[:10]
    ) or "- none"
    recreations = "\n".join(
        f"- {item.get('status')} | score={item.get('final_score', 'n/a')} | {item.get('pattern')} | `{item.get('final_video') or 'no_video'}`"
        for item in list(memory.get("recreations") or [])[:10]
    ) or "- none"
    recommendations = "\n".join(f"- {item}" for item in memory.get("recommendations") or []) or "- none"
    safety = "\n".join(f"- {item}" for item in memory.get("safety_notes") or []) or "- none"
    return "\n".join(
        [
            "# Faceless Shorts Format Memory",
            "",
            f"- runs_total: `{memory.get('runs_total', 0)}`",
            f"- updated_at: `{memory.get('updated_at', 'n/a')}`",
            "",
            "## Top Patterns",
            pattern_lines,
            "",
            "## Top Channels",
            channel_lines,
            "",
            "## Highest-View References",
            references,
            "",
            "## Recreated Outputs",
            recreations,
            "",
            "## Recommendations",
            recommendations,
            "",
            "## Safety",
            safety,
            "",
        ]
    )


def _render_ranked_lines(items: list[dict[str, Any]]) -> str:
    lines = []
    for item in items[:10]:
        lines.append(
            "- "
            + " | ".join(
                (
                    str(item.get("key") or "unknown"),
                    f"observed={item.get('observed_count', 0)}",
                    f"max_views={item.get('max_views', 0)}",
                    f"ready={item.get('ready_count', 0)}",
                    f"best_score={item.get('best_recreation_score', 0)}",
                )
            )
        )
    return "\n".join(lines) or "- none"


def _empty_stats() -> dict[str, Any]:
    return {
        "observed_count": 0,
        "total_views": 0,
        "max_views": 0,
        "ready_count": 0,
        "recreated_count": 0,
        "best_recreation_score": 0.0,
        "top_titles": [],
        "top_urls": [],
    }


def _update_observed_stats(
    bucket: dict[str, Any],
    key: str,
    *,
    views: int,
    title: str,
    url: str,
) -> None:
    stats = bucket.setdefault(key, _empty_stats())
    stats["observed_count"] = int(stats.get("observed_count") or 0) + 1
    stats["total_views"] = int(stats.get("total_views") or 0) + views
    stats["max_views"] = max(int(stats.get("max_views") or 0), views)
    if title and title not in stats["top_titles"]:
        stats["top_titles"] = [title, *list(stats.get("top_titles") or [])][:5]
    if url and url not in stats["top_urls"]:
        stats["top_urls"] = [url, *list(stats.get("top_urls") or [])][:5]


def _append_unique_reference(memory: dict[str, Any], candidate: dict[str, Any]) -> None:
    video_id = str(candidate.get("video_id") or candidate.get("url") or "").strip()
    if not video_id:
        return
    existing = {str(item.get("video_id") or item.get("url") or "") for item in memory.get("references") or []}
    if video_id in existing:
        return
    references = list(memory.get("references") or [])
    references.append(
        {
            "video_id": video_id,
            "title": str(candidate.get("title") or "").strip(),
            "channel": str(candidate.get("channel") or "").strip(),
            "channel_url": str(candidate.get("channel_url") or "").strip(),
            "url": str(candidate.get("url") or "").strip(),
            "views": _optional_int(candidate.get("view_count")) or 0,
            "duration_s": _optional_int(candidate.get("duration_s")),
            "pattern": str(candidate.get("pattern") or "").strip(),
            "thumbnail_url": str(candidate.get("thumbnail_url") or "").strip(),
        }
    )
    references.sort(key=lambda item: int(item.get("views") or 0), reverse=True)
    memory["references"] = references[:50]


def _append_unique_recreation(
    memory: dict[str, Any],
    *,
    item: dict[str, Any],
    candidate: dict[str, Any],
    pattern: str,
    channel: str,
) -> None:
    final_video = str(item.get("final_video") or "").strip()
    result_path = str(item.get("result_path") or final_video).strip()
    if not result_path:
        return
    existing = {str(entry.get("result_path") or entry.get("final_video") or "") for entry in memory.get("recreations") or []}
    if result_path in existing:
        return
    recreations = list(memory.get("recreations") or [])
    recreations.append(
        {
            "status": str(item.get("status") or "").strip(),
            "final_score": _optional_float(item.get("final_score")),
            "final_decision": str(item.get("final_decision") or "").strip(),
            "final_video": final_video,
            "result_path": result_path,
            "source_title": str(candidate.get("title") or item.get("source_title") or "").strip(),
            "source_url": str(candidate.get("url") or item.get("source_url") or "").strip(),
            "source_views": _optional_int(candidate.get("view_count") or item.get("source_views")),
            "pattern": pattern,
            "channel": channel,
        }
    )
    recreations.sort(key=lambda entry: float(entry.get("final_score") or 0.0), reverse=True)
    memory["recreations"] = recreations[:50]


def _recreation_exists(memory: dict[str, Any], item: dict[str, Any]) -> bool:
    final_video = str(item.get("final_video") or "").strip()
    result_path = str(item.get("result_path") or final_video).strip()
    if not result_path:
        return False
    existing = {str(entry.get("result_path") or entry.get("final_video") or "") for entry in memory.get("recreations") or []}
    return result_path in existing


def _rank_stats(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for key, stats in bucket.items():
        item = {"key": key, **dict(stats)}
        item["learning_score"] = (
            int(item.get("max_views") or 0) / 100000
            + int(item.get("ready_count") or 0) * 3
            + float(item.get("best_recreation_score") or 0.0)
        )
        items.append(item)
    items.sort(key=lambda item: (float(item.get("learning_score") or 0.0), int(item.get("total_views") or 0)), reverse=True)
    return items[:10]


def _recommendations(memory: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    top_patterns = list(memory.get("top_patterns") or [])
    top_channels = list(memory.get("top_channels") or [])
    if top_patterns:
        pattern = str(top_patterns[0].get("key") or "top_pattern")
        recommendations.append(f"repeat_format_pattern:{pattern}")
        recommendations.append(f"generate_next_batch_from_pattern:{pattern}:max_items=3")
    if top_channels:
        channel = str(top_channels[0].get("key") or "top_channel")
        recommendations.append(f"watch_more_from_channel:{channel}")
    recreations = list(memory.get("recreations") or [])
    ready = [item for item in recreations if item.get("status") == "ready_for_owner_review" or item.get("final_decision") == "approve_mechanics"]
    if ready:
        recommendations.append("show_ready_outputs_to_owner_before_any_upload")
    else:
        recommendations.append("run_repair_pass_before_generating_more_outputs")
    if any("repair" in str(item.get("final_decision") or item.get("status") or "") for item in recreations):
        recommendations.append("prioritize_adaptive_brightness_and_caption_readability_repair")
    recommendations.append("never_copy_source_footage_voice_script_watermark_or_channel_branding")
    return list(dict.fromkeys(recommendations))


def _batch_from_payload(payload: dict[str, Any]) -> FacelessRecreationBatchSessionResult:
    from content.faceless_recreation_batch_session import FacelessRecreationBatchItem

    return FacelessRecreationBatchSessionResult(
        status=str(payload.get("status") or ""),
        batch_dir=str(payload.get("batch_dir") or ""),
        benchmark_json=str(payload.get("benchmark_json") or ""),
        visual_plan_json=str(payload.get("visual_plan_json") or ""),
        reference_observation_summary_json=str(payload.get("reference_observation_summary_json") or ""),
        max_items=int(payload.get("max_items") or 0),
        ready_count=int(payload.get("ready_count") or 0),
        failed_count=int(payload.get("failed_count") or 0),
        items=tuple(
            FacelessRecreationBatchItem(
                index=int(item.get("index") or 0),
                status=str(item.get("status") or ""),
                source_title=str(item.get("source_title") or ""),
                source_channel=str(item.get("source_channel") or ""),
                source_views=_optional_int(item.get("source_views")),
                source_url=str(item.get("source_url") or "") or None,
                session_dir=str(item.get("session_dir") or "") or None,
                final_video=str(item.get("final_video") or "") or None,
                final_score=_optional_float(item.get("final_score")),
                final_decision=str(item.get("final_decision") or "") or None,
                result_path=str(item.get("result_path") or "") or None,
                error=str(item.get("error") or "") or None,
            )
            for item in payload.get("items") or []
            if isinstance(item, dict)
        ),
        learning_report_path=str(payload.get("learning_report_path") or ""),
        result_path=str(payload.get("result_path") or ""),
        created_at=str(payload.get("created_at") or ""),
        safety_notes=tuple(str(item) for item in payload.get("safety_notes") or []),
    )


def _candidate_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("top_candidates")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _clean_key(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
