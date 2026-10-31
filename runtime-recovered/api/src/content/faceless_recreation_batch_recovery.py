from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from content.faceless_recreation_batch_session import (
    FacelessRecreationBatchItem,
    FacelessRecreationBatchSessionResult,
    render_faceless_recreation_batch_learning_report,
)
from content.faceless_shorts_format_memory import FacelessShortsFormatMemoryService


@dataclass(frozen=True, slots=True)
class FacelessBatchRecoveryResult:
    status: str
    batch_dir: str
    recovered_count: int
    ready_count: int
    failed_count: int
    batch_result_path: str
    learning_report_path: str
    format_memory_path: str | None
    format_memory_report_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FacelessRecreationBatchRecoveryService:
    """Recovers usable batch state after a long watch session is interrupted."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def recover(
        self,
        *,
        batch_dir: str | Path,
        benchmark_json: str | Path | None = None,
        visual_plan_json: str | Path | None = None,
        reference_observation_summary_json: str | Path | None = None,
        update_memory: bool = True,
    ) -> FacelessBatchRecoveryResult:
        batch_path = Path(batch_dir).resolve()
        benchmark_path = Path(benchmark_json).resolve() if benchmark_json else self._infer_benchmark_path(batch_path)
        benchmark_payload = _load_object(benchmark_path)
        candidates = _candidate_items(benchmark_payload)
        plan_path = Path(visual_plan_json).resolve() if visual_plan_json else self._infer_visual_plan_path(
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
        )
        reference_path = (
            Path(reference_observation_summary_json).resolve()
            if reference_observation_summary_json
            else self._infer_reference_summary_path(batch_path)
        )
        items = self._recover_items(batch_path=batch_path, candidates=candidates)
        planned_count = max(len(items), _reference_count(reference_path), 1)
        ready_count = sum(1 for item in items if item.status == "ready_for_owner_review")
        failed_count = sum(1 for item in items if item.error or item.status == "failed")
        result_status = (
            "interrupted_recovered_ready_for_owner_review"
            if ready_count
            else "interrupted_recovered_needs_human_review"
        )
        result_path = batch_path / "faceless_recreation_batch_session_result.json"
        learning_report_path = batch_path / "faceless_recreation_batch_learning_report.md"
        result = FacelessRecreationBatchSessionResult(
            status=result_status,
            batch_dir=str(batch_path),
            benchmark_json=str(benchmark_path),
            visual_plan_json=str(plan_path),
            reference_observation_summary_json=str(reference_path) if reference_path else "",
            max_items=planned_count,
            ready_count=ready_count,
            failed_count=failed_count,
            items=tuple(items),
            learning_report_path=str(learning_report_path),
            result_path=str(result_path),
            created_at=datetime.now(UTC).isoformat(),
            safety_notes=(
                "Recovered from an interrupted batch run.",
                "Batch learns format mechanics only from public Shorts.",
                "Do not copy source footage, voice, exact scripts, watermarks, channel branding or character identity.",
                "Generated outputs stop at owner review/private/manual state; no public upload.",
            ),
        )
        result_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        learning_report_path.write_text(
            render_faceless_recreation_batch_learning_report(result, benchmark_payload=benchmark_payload),
            encoding="utf-8",
        )
        memory_update = (
            FacelessShortsFormatMemoryService(self.settings).update_from_batch(
                batch=result,
                benchmark_payload=benchmark_payload,
            )
            if update_memory
            else None
        )
        return FacelessBatchRecoveryResult(
            status=result_status,
            batch_dir=str(batch_path),
            recovered_count=len(items),
            ready_count=ready_count,
            failed_count=failed_count,
            batch_result_path=str(result_path),
            learning_report_path=str(learning_report_path),
            format_memory_path=str(memory_update.get("memory_path")) if memory_update else None,
            format_memory_report_path=str(memory_update.get("report_path")) if memory_update else None,
        )

    @staticmethod
    def _recover_items(*, batch_path: Path, candidates: list[dict[str, Any]]) -> list[FacelessRecreationBatchItem]:
        items: list[FacelessRecreationBatchItem] = []
        for item_dir in sorted(batch_path.glob("item_*")):
            if not item_dir.is_dir():
                continue
            try:
                index = int(item_dir.name.split("_", maxsplit=1)[1]) - 1
            except (IndexError, ValueError):
                continue
            result_path = item_dir / "faceless_recreation_production_session_result.json"
            if not result_path.exists():
                continue
            payload = _load_object(result_path)
            candidate = candidates[index] if index < len(candidates) else {}
            items.append(
                FacelessRecreationBatchItem(
                    index=index,
                    status=str(payload.get("status") or ""),
                    source_title=str(candidate.get("title") or payload.get("source_title") or ""),
                    source_channel=str(candidate.get("channel") or payload.get("source_channel") or ""),
                    source_views=_optional_int(candidate.get("view_count") or payload.get("source_views")),
                    source_url=str(candidate.get("url") or payload.get("source_url") or "") or None,
                    session_dir=str(payload.get("session_dir") or item_dir),
                    final_video=str(payload.get("final_video") or "") or None,
                    final_score=_optional_float(payload.get("final_score")),
                    final_decision=str(payload.get("final_decision") or "") or None,
                    result_path=str(result_path),
                )
            )
        return items

    @staticmethod
    def _infer_benchmark_path(batch_path: Path) -> Path:
        parent = batch_path.parent
        run_name = parent.name
        if run_name.endswith("_faceless_watch_session"):
            benchmark_name = run_name.replace("_faceless_watch_session", "_faceless_ai_shorts_benchmark")
            candidate = parent.parent / benchmark_name / "benchmark_run.json"
            if candidate.exists():
                return candidate.resolve()
        local = batch_path / "benchmark.json"
        if local.exists():
            return local.resolve()
        raise FileNotFoundError(f"Cannot infer benchmark_run.json for interrupted batch: {batch_path}")

    @staticmethod
    def _infer_visual_plan_path(*, benchmark_path: Path, benchmark_payload: dict[str, Any]) -> Path:
        explicit = str(benchmark_payload.get("visual_recreation_plans_path") or "").strip()
        if explicit and Path(explicit).exists():
            return Path(explicit).resolve()
        fallback = benchmark_path.parent / "visual_recreation_plans.json"
        if fallback.exists():
            return fallback.resolve()
        raise FileNotFoundError(f"Cannot infer visual_recreation_plans.json for {benchmark_path}")

    @staticmethod
    def _infer_reference_summary_path(batch_path: Path) -> Path | None:
        candidate = batch_path / "public_player_observations" / "public_player_observation_summary.json"
        return candidate.resolve() if candidate.exists() else None


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return payload


def _candidate_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("top_candidates")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _reference_count(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(payload, list):
        return len([item for item in payload if isinstance(item, dict)])
    if isinstance(payload, dict):
        return 1
    return 0


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
