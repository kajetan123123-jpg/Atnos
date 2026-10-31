from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence

from atnos.core.config import Settings
from content.faceless_recreation_batch_session import (
    FacelessRecreationBatchSessionResult,
    FacelessRecreationBatchSessionService,
)
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES
from content.faceless_shorts_format_memory import FacelessShortsFormatMemoryService
from content.faceless_shorts_next_session_planner import FacelessShortsNextSessionPlan, FacelessShortsNextSessionPlanner


class BatchSessionRunner(Protocol):
    def run(
        self,
        *,
        benchmark_json: str | Path | None = ...,
        reference_observation_json: str | Path | None = ...,
        visual_plan_json: str | Path | None = ...,
        source_short_dir: str | Path | None = ...,
        output_dir: str | Path | None = ...,
        queries: Iterable[str] = ...,
        max_items: int = ...,
        per_query_limit: int = ...,
        top_limit: int = ...,
        brief_limit: int = ...,
        sample_times_sec: Sequence[float] = ...,
        keep_public_frames: bool = ...,
        allow_duration_anomalies: bool = ...,
        max_scenes: int = ...,
        width: int = ...,
        height: int = ...,
        steps: int | None = ...,
        generate_images: bool = ...,
        render_video: bool = ...,
        progress_callback: Callable[[dict[str, Any]], None] | None = ...,
    ) -> FacelessRecreationBatchSessionResult:
        ...


@dataclass(frozen=True, slots=True)
class FacelessShortsWatchSessionConfig:
    enabled: bool = True
    memory_guided: bool = False
    queries: tuple[str, ...] = DEFAULT_FACELESS_AI_QUERIES
    max_items: int = 3
    per_query_limit: int = 8
    top_limit: int = 12
    brief_limit: int = 3
    sample_times_sec: tuple[float, ...] = (0.5, 2.0, 5.0, 10.0, 20.0, 35.0)
    keep_public_frames: bool = False
    allow_duration_anomalies: bool = False
    max_scenes: int = 5
    width: int = 768
    height: int = 1344
    steps: int = 8
    generate_images: bool = True
    render_video: bool = True
    notes: tuple[str, ...] = field(
        default=(
            "Watch public Shorts for format mechanics only.",
            "Do not copy source footage, voices, exact scripts, watermarks, branding or character identity.",
            "Generated outputs stop at owner review; no public upload.",
        )
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FacelessShortsWatchSessionConfig":
        return cls(
            enabled=bool(payload.get("enabled", True)),
            memory_guided=bool(payload.get("memory_guided", False)),
            queries=tuple(str(item).strip() for item in payload.get("queries", DEFAULT_FACELESS_AI_QUERIES) if str(item).strip()),
            max_items=max(1, int(payload.get("max_items", 3))),
            per_query_limit=max(1, int(payload.get("per_query_limit", 8))),
            top_limit=max(1, int(payload.get("top_limit", 12))),
            brief_limit=max(1, int(payload.get("brief_limit", 3))),
            sample_times_sec=tuple(float(item) for item in payload.get("sample_times_sec", (0.5, 2.0, 5.0, 10.0, 20.0, 35.0))),
            keep_public_frames=bool(payload.get("keep_public_frames", False)),
            allow_duration_anomalies=bool(payload.get("allow_duration_anomalies", False)),
            max_scenes=max(1, int(payload.get("max_scenes", 5))),
            width=max(256, int(payload.get("width", 768))),
            height=max(256, int(payload.get("height", 1344))),
            steps=max(1, int(payload.get("steps", 8))),
            generate_images=bool(payload.get("generate_images", True)),
            render_video=bool(payload.get("render_video", True)),
            notes=tuple(str(item) for item in payload.get("notes", ()) if str(item).strip()) or cls().notes,
        )


@dataclass(frozen=True, slots=True)
class FacelessShortsWatchSessionResult:
    status: str
    run_id: str
    output_dir: str
    config_path: str
    state_path: str
    log_path: str
    batch_result_path: str | None
    batch_learning_report_path: str | None
    format_memory_path: str | None
    format_memory_report_path: str | None
    memory_guided_plan_path: str | None
    memory_guided_plan_report_path: str | None
    ready_count: int
    failed_count: int
    top_source_title: str | None
    top_source_views: int | None
    final_videos: tuple[str, ...]
    report_path: str
    created_at: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FacelessShortsWatchSessionService:
    """Automation-ready entrypoint for recurring faceless Shorts learning."""

    def __init__(
        self,
        settings: Settings,
        *,
        batch_runner: BatchSessionRunner | None = None,
    ) -> None:
        self.settings = settings
        self.batch_runner = batch_runner or FacelessRecreationBatchSessionService(settings)

    def default_config_path(self) -> Path:
        return self.settings.data_dir / "learning" / "faceless-ai-shorts" / "watch_session_config.json"

    def state_path(self) -> Path:
        return self.settings.data_dir / "learning" / "faceless-ai-shorts" / "watch_session_state.json"

    def log_path(self) -> Path:
        return self.settings.data_dir / "logs" / "faceless_shorts_watch_session.jsonl"

    def write_default_config(self, path: str | Path | None = None, *, overwrite: bool = False) -> Path:
        target = Path(path).resolve() if path else self.default_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            return target
        target.write_text(
            json.dumps(FacelessShortsWatchSessionConfig().to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def load_config(self, path: str | Path | None = None) -> tuple[FacelessShortsWatchSessionConfig, Path]:
        target = Path(path).resolve() if path else self.write_default_config()
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"Watch session config must be a JSON object: {target}")
        return FacelessShortsWatchSessionConfig.from_dict(payload), target

    def run_once(
        self,
        *,
        config_path: str | Path | None = None,
        output_dir: str | Path | None = None,
        benchmark_json: str | Path | None = None,
        reference_observation_json: str | Path | None = None,
        visual_plan_json: str | Path | None = None,
        source_short_dir: str | Path | None = None,
        generate_images: bool | None = None,
        render_video: bool | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> FacelessShortsWatchSessionResult:
        config, resolved_config_path = self.load_config(config_path)
        created_at = datetime.now(UTC)
        run_id = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_faceless_watch_session"
        output_path = Path(output_dir).resolve() if output_dir else (
            self.settings.data_dir / "learning" / "faceless-ai-shorts" / run_id
        )
        output_path.mkdir(parents=True, exist_ok=True)

        if not config.enabled:
            result = self._build_result(
                status="disabled",
                run_id=run_id,
                output_dir=output_path,
                config_path=resolved_config_path,
                batch=None,
                created_at=created_at,
                notes=("Watch session config is disabled.",),
            )
            self._write_result(result)
            return result

        memory_guided_plan: FacelessShortsNextSessionPlan | None = None
        if config.memory_guided:
            _emit_progress(
                progress_callback,
                phase="memory_guided_plan",
                message="Building memory-guided query plan from previous faceless Shorts learning.",
                output_dir=str(output_path),
            )
            memory_guided_plan = FacelessShortsNextSessionPlanner(self.settings).write(
                max_items=config.max_items,
                config_path=output_path / "memory_guided_watch_config.json",
            )
            config = FacelessShortsWatchSessionConfig.from_dict(memory_guided_plan.config_payload)
            resolved_config_path = Path(memory_guided_plan.config_path)

        _emit_progress(
            progress_callback,
            phase="batch_start",
            message="Starting public Shorts watch/recreate batch.",
            config_path=str(resolved_config_path),
            output_dir=str(output_path / "batch"),
            max_items=config.max_items,
        )
        batch = self.batch_runner.run(
            benchmark_json=benchmark_json,
            reference_observation_json=reference_observation_json,
            visual_plan_json=visual_plan_json,
            source_short_dir=source_short_dir,
            output_dir=output_path / "batch",
            queries=config.queries,
            max_items=config.max_items,
            per_query_limit=config.per_query_limit,
            top_limit=config.top_limit,
            brief_limit=config.brief_limit,
            sample_times_sec=config.sample_times_sec,
            keep_public_frames=config.keep_public_frames,
            allow_duration_anomalies=config.allow_duration_anomalies,
            max_scenes=config.max_scenes,
            width=config.width,
            height=config.height,
            steps=config.steps,
            generate_images=config.generate_images if generate_images is None else bool(generate_images),
            render_video=config.render_video if render_video is None else bool(render_video),
            progress_callback=progress_callback,
        )
        _emit_progress(
            progress_callback,
            phase="format_memory_update",
            message="Updating cumulative faceless Shorts format memory.",
            batch_result_path=batch.result_path,
        )
        memory_update = self._update_format_memory(batch)
        result = self._build_result(
            status=batch.status,
            run_id=run_id,
            output_dir=output_path,
            config_path=resolved_config_path,
            batch=batch,
            memory_update=memory_update,
            memory_guided_plan=memory_guided_plan,
            created_at=created_at,
            notes=tuple(config.notes),
        )
        self._write_result(result)
        self._update_state(result)
        self._append_log(result)
        _emit_progress(
            progress_callback,
            phase="watch_session_completed",
            message="Watch session completed.",
            status=result.status,
            ready_count=result.ready_count,
            failed_count=result.failed_count,
            report_path=result.report_path,
        )
        return result

    def _build_result(
        self,
        *,
        status: str,
        run_id: str,
        output_dir: Path,
        config_path: Path,
        batch: FacelessRecreationBatchSessionResult | None,
        memory_update: dict[str, Any] | None = None,
        memory_guided_plan: FacelessShortsNextSessionPlan | None = None,
        created_at: datetime,
        notes: tuple[str, ...],
    ) -> FacelessShortsWatchSessionResult:
        final_videos = tuple(item.final_video for item in batch.items if item.final_video) if batch else ()
        top_item = batch.items[0] if batch and batch.items else None
        report_path = output_dir / "faceless_watch_session_report.md"
        return FacelessShortsWatchSessionResult(
            status=status,
            run_id=run_id,
            output_dir=str(output_dir),
            config_path=str(config_path),
            state_path=str(self.state_path()),
            log_path=str(self.log_path()),
            batch_result_path=batch.result_path if batch else None,
            batch_learning_report_path=batch.learning_report_path if batch else None,
            format_memory_path=str(memory_update.get("memory_path")) if memory_update and memory_update.get("memory_path") else None,
            format_memory_report_path=str(memory_update.get("report_path")) if memory_update and memory_update.get("report_path") else None,
            memory_guided_plan_path=memory_guided_plan.plan_path if memory_guided_plan else None,
            memory_guided_plan_report_path=memory_guided_plan.report_path if memory_guided_plan else None,
            ready_count=batch.ready_count if batch else 0,
            failed_count=batch.failed_count if batch else 0,
            top_source_title=top_item.source_title if top_item else None,
            top_source_views=top_item.source_views if top_item else None,
            final_videos=final_videos,
            report_path=str(report_path),
            created_at=created_at.isoformat(),
            notes=notes,
        )

    def _write_result(self, result: FacelessShortsWatchSessionResult) -> None:
        output_dir = Path(result.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "faceless_watch_session_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        Path(result.report_path).write_text(render_faceless_watch_session_report(result), encoding="utf-8")

    def _update_state(self, result: FacelessShortsWatchSessionResult) -> None:
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = _load_state(path)
        runs_total = int(previous.get("runs_total") or 0) + 1
        state = {
            "runs_total": runs_total,
            "last_run_at": result.created_at,
            "last_status": result.status,
            "last_output_dir": result.output_dir,
            "last_ready_count": result.ready_count,
            "last_failed_count": result.failed_count,
            "last_top_source_title": result.top_source_title,
            "last_top_source_views": result.top_source_views,
            "last_report_path": result.report_path,
            "last_final_videos": list(result.final_videos),
            "format_memory_path": result.format_memory_path,
            "format_memory_report_path": result.format_memory_report_path,
            "memory_guided_plan_path": result.memory_guided_plan_path,
            "memory_guided_plan_report_path": result.memory_guided_plan_report_path,
        }
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_log(self, result: FacelessShortsWatchSessionResult) -> None:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "event": "faceless_shorts_watch_session_completed",
                        "run_id": result.run_id,
                        "status": result.status,
                        "ready_count": result.ready_count,
                        "failed_count": result.failed_count,
                        "top_source_title": result.top_source_title,
                        "top_source_views": result.top_source_views,
                        "report_path": result.report_path,
                        "format_memory_path": result.format_memory_path,
                        "memory_guided_plan_path": result.memory_guided_plan_path,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def _update_format_memory(self, batch: FacelessRecreationBatchSessionResult) -> dict[str, Any] | None:
        try:
            benchmark_path = Path(batch.benchmark_json)
            benchmark_payload = json.loads(benchmark_path.read_text(encoding="utf-8-sig"))
            if not isinstance(benchmark_payload, dict):
                return None
            return FacelessShortsFormatMemoryService(self.settings).update_from_batch(
                batch=batch,
                benchmark_payload=benchmark_payload,
            )
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "memory_path": str(FacelessShortsFormatMemoryService(self.settings).memory_path()),
                "report_path": str(FacelessShortsFormatMemoryService(self.settings).report_path()),
            }


def render_faceless_watch_session_report(result: FacelessShortsWatchSessionResult) -> str:
    videos = "\n".join(f"- `{path}`" for path in result.final_videos) or "- none"
    notes = "\n".join(f"- {item}" for item in result.notes) or "- none"
    return "\n".join(
        [
            "# Faceless Shorts Watch Session",
            "",
            f"- status: `{result.status}`",
            f"- ready_count: `{result.ready_count}`",
            f"- failed_count: `{result.failed_count}`",
            f"- top_source_title: `{result.top_source_title or 'n/a'}`",
            f"- top_source_views: `{result.top_source_views if result.top_source_views is not None else 'n/a'}`",
            f"- batch_learning_report: `{result.batch_learning_report_path or 'n/a'}`",
            f"- format_memory: `{result.format_memory_path or 'n/a'}`",
            f"- format_memory_report: `{result.format_memory_report_path or 'n/a'}`",
            f"- memory_guided_plan: `{result.memory_guided_plan_path or 'n/a'}`",
            f"- memory_guided_plan_report: `{result.memory_guided_plan_report_path or 'n/a'}`",
            "",
            "## Final Videos",
            videos,
            "",
            "## Notes",
            notes,
            "",
            "## Next Run",
            "- Increase max_items for a longer watch/recreate batch.",
            "- Keep output private/manual until owner approval.",
            "- Compare next run against this run in watch_session_state.json.",
            "",
        ]
    )


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
