from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from logging import Logger
import os
from pathlib import Path
from time import perf_counter
from typing import Iterator

from fastapi import FastAPI

from atnos.core.config import Settings


@dataclass(slots=True)
class StartupTraceSnapshot:
    status: str
    startup_phase: str
    startup_last_checkpoint: str | None
    startup_error: str | None
    startup_ready: bool
    startup_started_at: str
    startup_updated_at: str
    startup_elapsed_ms: int
    pid: int


@dataclass(slots=True)
class StartupTraceEvent:
    timestamp: str
    phase: str
    checkpoint: str
    outcome: str
    status: str
    ready: bool
    elapsed_ms: int
    pid: int
    error: str | None = None


class StartupTraceRecorder:
    def __init__(self, settings: Settings, logger: Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.status_path = settings.startup_status_file
        self.events_path = settings.startup_events_file
        self.started_at = datetime.now(UTC)
        self._started_perf = perf_counter()
        self._pid = os.getpid()
        self._last_snapshot: StartupTraceSnapshot | None = None

    @property
    def started_at_iso(self) -> str:
        return self.started_at.isoformat()

    def initialize(
        self,
        app: FastAPI | None = None,
        *,
        phase: str = "bootstrapping",
        checkpoint: str = "bootstrapping",
        reset_events: bool = True,
    ) -> None:
        self.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        if reset_events:
            self.events_path.write_text("", encoding="utf-8")
        self._apply_snapshot(
            app,
            StartupTraceSnapshot(
                status="starting",
                startup_phase=phase,
                startup_last_checkpoint=f"{checkpoint}:start",
                startup_error=None,
                startup_ready=False,
                startup_started_at=self.started_at_iso,
                startup_updated_at=self.started_at_iso,
                startup_elapsed_ms=0,
                pid=self._pid,
            ),
        )
        self._append_event(
            StartupTraceEvent(
                timestamp=self.started_at_iso,
                phase=phase,
                checkpoint=checkpoint,
                outcome="start",
                status="starting",
                ready=False,
                elapsed_ms=0,
                pid=self._pid,
            )
        )
        if app is not None:
            app.state.startup_status_path = str(self.status_path)
            app.state.startup_events_path = str(self.events_path)

    def mark_phase(
        self,
        app: FastAPI | None = None,
        *,
        phase: str,
        checkpoint: str | None = None,
        outcome: str = "start",
        status: str = "starting",
        ready: bool = False,
        error: str | None = None,
    ) -> None:
        checkpoint_name = checkpoint or phase
        elapsed_ms = self._elapsed_ms()
        updated_at = datetime.now(UTC).isoformat()
        last_checkpoint = f"{checkpoint_name}:{outcome}"
        snapshot = StartupTraceSnapshot(
            status=status,
            startup_phase=phase,
            startup_last_checkpoint=last_checkpoint,
            startup_error=error,
            startup_ready=ready,
            startup_started_at=self.started_at_iso,
            startup_updated_at=updated_at,
            startup_elapsed_ms=elapsed_ms,
            pid=self._pid,
        )
        self._apply_snapshot(app, snapshot)
        self._append_event(
            StartupTraceEvent(
                timestamp=updated_at,
                phase=phase,
                checkpoint=checkpoint_name,
                outcome=outcome,
                status=status,
                ready=ready,
                elapsed_ms=elapsed_ms,
                pid=self._pid,
                error=error,
            )
        )
        message = (
            "Startup checkpoint | phase=%s | checkpoint=%s | outcome=%s | status=%s | "
            "elapsed_ms=%s"
        )
        if error:
            self.logger.info(
                message + " | error=%s",
                phase,
                checkpoint_name,
                outcome,
                status,
                elapsed_ms,
                error,
            )
        else:
            self.logger.info(
                message,
                phase,
                checkpoint_name,
                outcome,
                status,
                elapsed_ms,
            )

    @contextmanager
    def phase_scope(
        self,
        app: FastAPI | None = None,
        *,
        phase: str,
        checkpoint: str | None = None,
    ) -> Iterator[None]:
        checkpoint_name = checkpoint or phase
        self.mark_phase(app, phase=phase, checkpoint=checkpoint_name, outcome="start")
        try:
            yield
        except Exception as exc:
            self.mark_phase(
                app,
                phase=phase,
                checkpoint=checkpoint_name,
                outcome="failed",
                status="failed",
                error=str(exc),
            )
            raise
        else:
            self.mark_phase(app, phase=phase, checkpoint=checkpoint_name, outcome="ok")

    def fail(self, app: FastAPI | None = None, *, phase: str, error: str) -> None:
        self.mark_phase(
            app,
            phase=phase,
            checkpoint=phase,
            outcome="failed",
            status="failed",
            error=error,
        )

    def ready(self, app: FastAPI | None = None) -> None:
        self.mark_phase(
            app,
            phase="ready",
            checkpoint="ready",
            outcome="ok",
            status="ready",
            ready=True,
        )

    def stopping(self, app: FastAPI | None = None) -> None:
        self.mark_phase(
            app,
            phase="stopping",
            checkpoint="stopping",
            outcome="start",
            status="stopping",
        )

    def stopped(self, app: FastAPI | None = None) -> None:
        self.mark_phase(
            app,
            phase="stopped",
            checkpoint="stopped",
            outcome="ok",
            status="stopped",
        )

    def attach_app(self, app: FastAPI) -> None:
        if self._last_snapshot is None:
            return
        self._apply_snapshot(app, self._last_snapshot)
        app.state.startup_status_path = str(self.status_path)
        app.state.startup_events_path = str(self.events_path)

    def _apply_snapshot(self, app: FastAPI | None, snapshot: StartupTraceSnapshot) -> None:
        self._last_snapshot = snapshot
        if app is not None:
            app.state.startup_ready = snapshot.startup_ready
            app.state.startup_phase = snapshot.startup_phase
            app.state.startup_error = snapshot.startup_error
            app.state.startup_last_checkpoint = snapshot.startup_last_checkpoint
            app.state.startup_started_at = snapshot.startup_started_at
            app.state.startup_elapsed_ms = snapshot.startup_elapsed_ms
        self._write_json(self.status_path, asdict(snapshot))

    def _append_event(self, event: StartupTraceEvent) -> None:
        serialized = json.dumps(asdict(event), ensure_ascii=False)
        try:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(serialized + "\n")
        except OSError as exc:
            self.logger.warning("Failed to append startup trace event: %s", exc)

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self.logger.warning("Failed to persist startup trace snapshot: %s", exc)

    def _elapsed_ms(self) -> int:
        return max(0, int((perf_counter() - self._started_perf) * 1000))
