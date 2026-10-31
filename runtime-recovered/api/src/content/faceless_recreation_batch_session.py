from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence

from atnos.core.config import Settings
from content.faceless_recreation_production_session import (
    FacelessRecreationProductionSessionResult,
    FacelessRecreationProductionSessionService,
)
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES, FacelessShortsBenchmarkService
from content.public_short_frame_observer import PublicShortFrameObservationRunner


class ProductionSessionRunner(Protocol):
    def run(
        self,
        *,
        benchmark_json: str | Path | None = ...,
        reference_observation_json: str | Path | None = ...,
        source_short_dir: str | Path | None = ...,
        visual_plan_json: str | Path | None = ...,
        output_dir: str | Path | None = ...,
        queries: Iterable[str] = ...,
        per_query_limit: int = ...,
        top_limit: int = ...,
        brief_limit: int = ...,
        sample_times_sec: Sequence[float] = ...,
        keep_public_frames: bool = ...,
        allow_duration_anomalies: bool = ...,
        max_scenes: int = ...,
        plan_index: int = ...,
        width: int = ...,
        height: int = ...,
        steps: int | None = ...,
        generate_images: bool = ...,
        render_video: bool = ...,
        progress_callback: Callable[[dict[str, Any]], None] | None = ...,
    ) -> FacelessRecreationProductionSessionResult:
        ...


@dataclass(frozen=True, slots=True)
class FacelessRecreationBatchItem:
    index: int
    status: str
    source_title: str
    source_channel: str
    source_views: int | None
    source_url: str | None
    session_dir: str | None
    final_video: str | None
    final_score: float | None
    final_decision: str | None
    result_path: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FacelessRecreationBatchSessionResult:
    status: str
    batch_dir: str
    benchmark_json: str
    visual_plan_json: str
    reference_observation_summary_json: str
    max_items: int
    ready_count: int
    failed_count: int
    items: tuple[FacelessRecreationBatchItem, ...]
    learning_report_path: str
    result_path: str
    created_at: str
    safety_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [item.to_dict() for item in self.items]
        return payload


class FacelessRecreationBatchSessionService:
    """Runs multiple safe recreation attempts from the highest-view formats."""

    def __init__(
        self,
        settings: Settings,
        *,
        production_runner: ProductionSessionRunner | None = None,
    ) -> None:
        self.settings = settings
        self.production_runner = production_runner or FacelessRecreationProductionSessionService(settings)

    def run(
        self,
        *,
        benchmark_json: str | Path | None = None,
        reference_observation_json: str | Path | None = None,
        visual_plan_json: str | Path | None = None,
        source_short_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        queries: Iterable[str] = DEFAULT_FACELESS_AI_QUERIES,
        max_items: int = 3,
        per_query_limit: int = 8,
        top_limit: int = 12,
        brief_limit: int = 3,
        sample_times_sec: Sequence[float] = (0.5, 2.0, 5.0, 10.0, 20.0, 35.0),
        keep_public_frames: bool = False,
        allow_duration_anomalies: bool = False,
        max_scenes: int = 5,
        width: int = 768,
        height: int = 1344,
        steps: int | None = 8,
        generate_images: bool = True,
        render_video: bool = True,
        resume_existing: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> FacelessRecreationBatchSessionResult:
        created_at = datetime.now(UTC)
        batch_dir = Path(output_dir).resolve() if output_dir else self._default_output_dir(created_at)
        batch_dir.mkdir(parents=True, exist_ok=True)
        item_count = max(1, int(max_items))

        _emit_progress(
            progress_callback,
            phase="benchmark",
            message="Searching public faceless AI Shorts and ranking candidates.",
            batch_dir=str(batch_dir),
            max_items=item_count,
        )
        benchmark_path = Path(benchmark_json).resolve() if benchmark_json else self._run_benchmark(
            queries=queries,
            per_query_limit=per_query_limit,
            top_limit=max(top_limit, item_count),
            brief_limit=max(brief_limit, item_count),
            allow_duration_anomalies=allow_duration_anomalies,
            progress_callback=progress_callback,
        )
        _emit_progress(
            progress_callback,
            phase="benchmark_complete",
            message="Public Shorts benchmark finished.",
            benchmark_json=str(benchmark_path),
        )
        benchmark_payload = _load_object(benchmark_path)
        plan_path = Path(visual_plan_json).resolve() if visual_plan_json else _resolve_visual_plan_path(
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
        )
        _emit_progress(
            progress_callback,
            phase="observe_references",
            message="Observing reference Shorts frames and format mechanics.",
            visual_plan_json=str(plan_path),
        )
        reference_summary_path = Path(reference_observation_json).resolve() if reference_observation_json else (
            self._observe_references(
                benchmark_path=benchmark_path,
                output_dir=batch_dir / "public_player_observations",
                top_limit=item_count,
                sample_times_sec=sample_times_sec,
                keep_public_frames=keep_public_frames,
            )
        )
        _emit_progress(
            progress_callback,
            phase="observe_references_complete",
            message="Reference frame observation finished.",
            reference_observation_summary_json=str(reference_summary_path),
        )
        references = _load_reference_items(reference_summary_path)
        candidates = _candidate_items(benchmark_payload)
        plans_count = _visual_plan_count(plan_path)
        runnable_count = min(item_count, max(len(candidates), len(references), plans_count or item_count))
        learning_report_path = batch_dir / "faceless_recreation_batch_learning_report.md"
        result_path = batch_dir / "faceless_recreation_batch_session_result.json"
        existing_items = _load_existing_items(result_path) if resume_existing else {}

        items: list[FacelessRecreationBatchItem] = []
        for index in range(runnable_count):
            candidate = candidates[index] if index < len(candidates) else {}
            existing_item = existing_items.get(index)
            if existing_item is not None and _is_resume_reusable(existing_item):
                _emit_progress(
                    progress_callback,
                    phase="item_skipped_existing",
                    message="Reusing existing ready recreation.",
                    item_index=index + 1,
                    item_total=runnable_count,
                    final_video=existing_item.final_video,
                )
                items.append(existing_item)
                self._write_checkpoint(
                    batch_dir=batch_dir,
                    benchmark_path=benchmark_path,
                    plan_path=plan_path,
                    reference_summary_path=reference_summary_path,
                    item_count=item_count,
                    items=items,
                    learning_report_path=learning_report_path,
                    result_path=result_path,
                    created_at=created_at,
                    benchmark_payload=benchmark_payload,
                )
                continue
            try:
                _emit_progress(
                    progress_callback,
                    phase="item_recreate",
                    message="Recreating public Short mechanics into a safe local output.",
                    item_index=index + 1,
                    item_total=runnable_count,
                    source_title=str(candidate.get("title") or ""),
                    source_channel=str(candidate.get("channel") or ""),
                    source_views=_optional_int(candidate.get("view_count")),
                    source_url=str(candidate.get("url") or "") or None,
                )
                reference_path = _write_reference_item(
                    reference=references[index] if index < len(references) else {},
                    output_dir=batch_dir / "reference_items",
                    index=index,
                )
                item_source_short_dir = Path(source_short_dir).resolve() if source_short_dir and runnable_count == 1 else None
                item_result = self.production_runner.run(
                    benchmark_json=benchmark_path,
                    visual_plan_json=plan_path,
                    reference_observation_json=reference_path,
                    source_short_dir=item_source_short_dir,
                    output_dir=batch_dir / f"item_{index + 1:02d}",
                    queries=queries,
                    per_query_limit=per_query_limit,
                    top_limit=top_limit,
                    brief_limit=brief_limit,
                    sample_times_sec=sample_times_sec,
                    keep_public_frames=keep_public_frames,
                    allow_duration_anomalies=allow_duration_anomalies,
                    max_scenes=max_scenes,
                    plan_index=index,
                    width=width,
                    height=height,
                    steps=steps,
                    generate_images=generate_images,
                    render_video=render_video,
                    progress_callback=progress_callback,
                )
                item = _item_from_result(index=index, candidate=candidate, result=item_result)
            except Exception as exc:
                item = _failed_item(index=index, candidate=candidate, error=f"{type(exc).__name__}: {exc}")
            items.append(item)
            _emit_progress(
                progress_callback,
                phase="item_checkpoint",
                message="Batch item checkpoint written.",
                item_index=index + 1,
                item_total=runnable_count,
                status=item.status,
                final_video=item.final_video,
                error=item.error,
            )
            self._write_checkpoint(
                batch_dir=batch_dir,
                benchmark_path=benchmark_path,
                plan_path=plan_path,
                reference_summary_path=reference_summary_path,
                item_count=item_count,
                items=items,
                learning_report_path=learning_report_path,
                result_path=result_path,
                created_at=created_at,
                benchmark_payload=benchmark_payload,
            )

        _emit_progress(
            progress_callback,
            phase="finalize",
            message="Finalizing learning report and format memory update.",
            ready_count=sum(1 for item in items if item.status == "ready_for_owner_review"),
            failed_count=sum(1 for item in items if item.error or item.status == "failed"),
        )
        ready_count = sum(1 for item in items if item.status == "ready_for_owner_review")
        failed_count = sum(1 for item in items if item.error or item.status == "failed")
        result_status = "ready_for_owner_review" if ready_count else "needs_human_review"
        result = FacelessRecreationBatchSessionResult(
            status=result_status,
            batch_dir=str(batch_dir),
            benchmark_json=str(benchmark_path),
            visual_plan_json=str(plan_path),
            reference_observation_summary_json=str(reference_summary_path),
            max_items=item_count,
            ready_count=ready_count,
            failed_count=failed_count,
            items=tuple(items),
            learning_report_path=str(learning_report_path),
            result_path=str(result_path),
            created_at=created_at.isoformat(),
            safety_notes=(
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
        self._write_obsidian_note(result)
        self._append_log(result)
        _emit_progress(
            progress_callback,
            phase="completed",
            message="Faceless recreation batch completed.",
            status=result.status,
            ready_count=result.ready_count,
            failed_count=result.failed_count,
            result_path=result.result_path,
            learning_report_path=result.learning_report_path,
        )
        return result

    def _write_checkpoint(
        self,
        *,
        batch_dir: Path,
        benchmark_path: Path,
        plan_path: Path,
        reference_summary_path: Path,
        item_count: int,
        items: list[FacelessRecreationBatchItem],
        learning_report_path: Path,
        result_path: Path,
        created_at: datetime,
        benchmark_payload: dict[str, Any],
    ) -> None:
        ready_count = sum(1 for item in items if item.status == "ready_for_owner_review")
        failed_count = sum(1 for item in items if item.error or item.status == "failed")
        checkpoint = FacelessRecreationBatchSessionResult(
            status="running_checkpoint",
            batch_dir=str(batch_dir),
            benchmark_json=str(benchmark_path),
            visual_plan_json=str(plan_path),
            reference_observation_summary_json=str(reference_summary_path),
            max_items=item_count,
            ready_count=ready_count,
            failed_count=failed_count,
            items=tuple(items),
            learning_report_path=str(learning_report_path),
            result_path=str(result_path),
            created_at=created_at.isoformat(),
            safety_notes=(
                "Batch checkpoint: run may still be in progress.",
                "Batch learns format mechanics only from public Shorts.",
                "Do not copy source footage, voice, exact scripts, watermarks, channel branding or character identity.",
                "Generated outputs stop at owner review/private/manual state; no public upload.",
            ),
        )
        result_path.write_text(json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        learning_report_path.write_text(
            render_faceless_recreation_batch_learning_report(checkpoint, benchmark_payload=benchmark_payload),
            encoding="utf-8",
        )

    def _default_output_dir(self, created_at: datetime) -> Path:
        return (
            self.settings.data_dir
            / "learning"
            / "faceless-ai-shorts"
            / f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_faceless_recreation_batch_session"
        )

    def _run_benchmark(
        self,
        *,
        queries: Iterable[str],
        per_query_limit: int,
        top_limit: int,
        brief_limit: int,
        allow_duration_anomalies: bool,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path:
        run = FacelessShortsBenchmarkService(self.settings).run(
            queries=queries,
            per_query_limit=per_query_limit,
            top_limit=top_limit,
            brief_limit=brief_limit,
            allow_duration_anomalies=allow_duration_anomalies,
            progress_callback=progress_callback,
        )
        return Path(run.json_path).resolve()

    def _observe_references(
        self,
        *,
        benchmark_path: Path,
        output_dir: Path,
        top_limit: int,
        sample_times_sec: Sequence[float],
        keep_public_frames: bool,
    ) -> Path:
        PublicShortFrameObservationRunner(self.settings).observe_benchmark(
            benchmark_json_path=benchmark_path,
            output_dir=output_dir,
            top_limit=top_limit,
            sample_times_sec=sample_times_sec,
            keep_frames=keep_public_frames,
        )
        return (output_dir / "public_player_observation_summary.json").resolve()

    def _write_obsidian_note(self, result: FacelessRecreationBatchSessionResult) -> None:
        vault_path = getattr(self.settings, "obsidian_vault_path", None)
        if not bool(getattr(self.settings, "obsidian_export_enabled", False)) or not vault_path:
            return
        vault = Path(vault_path)
        if not vault.exists():
            return
        target = vault / "Atnos" / "Shorts" / f"batch-{Path(result.batch_dir).name}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(Path(result.learning_report_path).read_text(encoding="utf-8"), encoding="utf-8")

    def _append_log(self, result: FacelessRecreationBatchSessionResult) -> None:
        log_path = self.settings.data_dir / "logs" / "faceless_recreation_batch.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "event": "faceless_recreation_batch_completed",
                        "status": result.status,
                        "batch_dir": result.batch_dir,
                        "ready_count": result.ready_count,
                        "failed_count": result.failed_count,
                        "learning_report_path": result.learning_report_path,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def render_faceless_recreation_batch_learning_report(
    result: FacelessRecreationBatchSessionResult,
    *,
    benchmark_payload: dict[str, Any],
) -> str:
    top_candidates = _candidate_items(benchmark_payload)[: result.max_items]
    candidate_lines = "\n".join(
        "- "
        + " | ".join(
            (
                str(item.get("title") or "untitled"),
                str(item.get("channel") or "unknown"),
                f"views={int(item.get('view_count') or 0)}",
                str(item.get("pattern") or "unknown"),
            )
        )
        for item in top_candidates
    )
    output_lines = "\n".join(
        "- "
        + " | ".join(
            (
                f"#{item.index + 1}",
                item.status,
                f"score={item.final_score if item.final_score is not None else 'n/a'}",
                item.final_decision or "n/a",
                item.final_video or "no_video",
            )
        )
        for item in result.items
    )
    next_actions = _next_actions(result)
    return "\n".join(
        [
            "# Faceless Shorts Batch Learning Report",
            "",
            f"- status: `{result.status}`",
            f"- ready_count: `{result.ready_count}`",
            f"- failed_count: `{result.failed_count}`",
            f"- benchmark_json: `{result.benchmark_json}`",
            "",
            "## Highest-View References",
            candidate_lines or "- none",
            "",
            "## Recreated Outputs",
            output_lines or "- none",
            "",
            "## Learning Conclusions",
            "- Najmocniejszy sygnal w tej probce: prosta mikrohistoria z natychmiastowym konfliktem, duzy tekst i szybkie beat changes.",
            "- Atnos powinien kopiowac mechanike: hook shape, pacing, kontrast, caption density i payoff loop.",
            "- Atnos nie powinien kopiowac: zrodlowych kadrow, postaci, glosu, watermarkow, nazwy kanalu ani dokladnego scenariusza.",
            "",
            "## Next Actions",
            next_actions,
            "",
            "## Safety",
            "\n".join(f"- {item}" for item in result.safety_notes),
            "",
        ]
    )


def _next_actions(result: FacelessRecreationBatchSessionResult) -> str:
    actions = []
    if result.ready_count:
        actions.append("- Pokaz gotowe MP4 wlascicielowi do oceny, bez public uploadu.")
    if result.ready_count < result.max_items:
        actions.append("- Dla itemow bez approval uruchom repair albo zmien visual prompts.")
    actions.append("- Nastepna sesja: zwieksz top_limit i porownaj, czy animal microstory nadal dominuje.")
    return "\n".join(actions)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return payload


def _emit_progress(callback: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if callback is None:
        return
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }
    try:
        callback(event)
    except Exception:
        return


def _resolve_visual_plan_path(*, benchmark_path: Path, benchmark_payload: dict[str, Any]) -> Path:
    explicit = str(benchmark_payload.get("visual_recreation_plans_path") or "").strip()
    if explicit and Path(explicit).exists():
        return Path(explicit).resolve()
    fallback = benchmark_path.parent / "visual_recreation_plans.json"
    if fallback.exists():
        return fallback.resolve()
    raise FileNotFoundError(f"Missing visual_recreation_plans.json near {benchmark_path}")


def _load_reference_items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _write_reference_item(*, reference: dict[str, Any], output_dir: Path, index: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"reference_{index + 1:02d}.json"
    path.write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _candidate_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("top_candidates")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _visual_plan_count(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(payload) if isinstance(payload, list) else 1 if isinstance(payload, dict) else 0


def _load_existing_items(path: Path) -> dict[int, FacelessRecreationBatchItem]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return {}
    items: dict[int, FacelessRecreationBatchItem] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        items[index] = FacelessRecreationBatchItem(
            index=index,
            status=str(raw.get("status") or ""),
            source_title=str(raw.get("source_title") or ""),
            source_channel=str(raw.get("source_channel") or ""),
            source_views=_optional_int(raw.get("source_views")),
            source_url=str(raw.get("source_url") or "") or None,
            session_dir=str(raw.get("session_dir") or "") or None,
            final_video=str(raw.get("final_video") or "") or None,
            final_score=_optional_float(raw.get("final_score")),
            final_decision=str(raw.get("final_decision") or "") or None,
            result_path=str(raw.get("result_path") or "") or None,
            error=str(raw.get("error") or "") or None,
        )
    return items


def _is_resume_reusable(item: FacelessRecreationBatchItem) -> bool:
    if item.status != "ready_for_owner_review":
        return False
    if not item.final_video:
        return False
    video_path = Path(item.final_video)
    return video_path.exists() and video_path.stat().st_size > 0


def _item_from_result(
    *,
    index: int,
    candidate: dict[str, Any],
    result: FacelessRecreationProductionSessionResult,
) -> FacelessRecreationBatchItem:
    return FacelessRecreationBatchItem(
        index=index,
        status=result.status,
        source_title=str(candidate.get("title") or ""),
        source_channel=str(candidate.get("channel") or ""),
        source_views=_optional_int(candidate.get("view_count")),
        source_url=str(candidate.get("url") or "") or None,
        session_dir=result.session_dir,
        final_video=result.final_video,
        final_score=result.final_score,
        final_decision=result.final_decision,
        result_path=str(Path(result.session_dir) / "faceless_recreation_production_session_result.json"),
    )


def _failed_item(*, index: int, candidate: dict[str, Any], error: str) -> FacelessRecreationBatchItem:
    return FacelessRecreationBatchItem(
        index=index,
        status="failed",
        source_title=str(candidate.get("title") or ""),
        source_channel=str(candidate.get("channel") or ""),
        source_views=_optional_int(candidate.get("view_count")),
        source_url=str(candidate.get("url") or "") or None,
        session_dir=None,
        final_video=None,
        final_score=None,
        final_decision=None,
        result_path=None,
        error=error,
    )


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
