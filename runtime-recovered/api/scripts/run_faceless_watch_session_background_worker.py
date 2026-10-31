from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import threading
import traceback
from typing import Protocol


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.faceless_shorts_watch_session import FacelessShortsWatchSessionService  # noqa: E402


class WatchSessionService(Protocol):
    def run_once(
        self,
        *,
        config_path: Path,
        output_dir: Path,
        generate_images: bool | None,
        render_video: bool | None,
        progress_callback,
    ):
        ...


def main() -> int:
    parser = argparse.ArgumentParser(description="Background worker for faceless Shorts watch sessions.")
    parser.add_argument("--config-json", type=Path, required=True)
    parser.add_argument("--status-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stdout-log", type=Path, default=None)
    parser.add_argument("--stderr-log", type=Path, default=None)
    parser.add_argument("--prompt-only", action="store_true")
    parser.add_argument("--no-render-video", action="store_true")
    args = parser.parse_args()

    return run_worker(
        config_json=args.config_json,
        status_json=args.status_json,
        output_dir=args.output_dir,
        stdout_log=args.stdout_log,
        stderr_log=args.stderr_log,
        prompt_only=args.prompt_only,
        no_render_video=args.no_render_video,
    )


def run_worker(
    *,
    config_json: Path,
    status_json: Path,
    output_dir: Path,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
    prompt_only: bool = False,
    no_render_video: bool = False,
    service: WatchSessionService | None = None,
) -> int:
    settings = Settings()
    status_path = status_json.resolve()
    resolved_config = config_json.resolve()
    resolved_output_dir = output_dir.resolve()
    started_at = _now()
    base_payload = {
        "started_at": started_at,
        "config_json": str(resolved_config),
        "output_dir": str(resolved_output_dir),
        "stdout_log": str(stdout_log.resolve()) if stdout_log else None,
        "stderr_log": str(stderr_log.resolve()) if stderr_log else None,
        "pid": _pid(),
    }
    _write_status(
        status_path,
        {
            **base_payload,
            "status": "running",
            "updated_at": _now(),
            "completed_at": None,
            "result": None,
            "error": None,
        },
    )
    heartbeat_stop = _start_heartbeat(status_path=status_path, base_payload=base_payload)
    try:
        runner = service or FacelessShortsWatchSessionService(settings)
        result = runner.run_once(
            config_path=config_json,
            output_dir=resolved_output_dir,
            generate_images=False if prompt_only else None,
            render_video=False if no_render_video else None,
            progress_callback=_build_progress_writer(status_path=status_path, base_payload=base_payload),
        )
    except Exception as exc:
        _stop_heartbeat(heartbeat_stop)
        _write_status(
            status_path,
            {
                **base_payload,
                "status": "failed",
                "updated_at": _now(),
                "completed_at": _now(),
                "result": None,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            },
        )
        return 1

    _stop_heartbeat(heartbeat_stop)
    result_dict = result.to_dict()
    has_outputs = bool(result_dict.get("ready_count") or result_dict.get("final_videos"))
    _write_status(
        status_path,
        {
            **base_payload,
            "status": "completed" if has_outputs else "needs_human_review",
            "updated_at": _now(),
            "completed_at": _now(),
            "result": result_dict,
            "error": None,
        },
    )
    return 0 if has_outputs else 2


def _write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _start_heartbeat(*, status_path: Path, base_payload: dict[str, object]) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def _loop() -> None:
        counter = 0
        while not stop_event.wait(30):
            counter += 1
            previous = _read_status(status_path)
            _write_status(
                status_path,
                {
                    **base_payload,
                    "status": "running",
                    "phase": previous.get("phase"),
                    "message": previous.get("message"),
                    "updated_at": _now(),
                    "completed_at": None,
                    "heartbeat_count": counter,
                    "progress": previous.get("progress"),
                    "result": None,
                    "error": None,
                },
            )

    thread = threading.Thread(target=_loop, name="faceless-watch-heartbeat", daemon=True)
    thread.start()
    return stop_event, thread


def _build_progress_writer(*, status_path: Path, base_payload: dict[str, object]):
    def _write_progress(event: dict[str, object]) -> None:
        _write_status(
            status_path,
            {
                **base_payload,
                "status": "running",
                "phase": event.get("phase"),
                "message": event.get("message"),
                "updated_at": _now(),
                "completed_at": None,
                "progress": event,
                "result": None,
                "error": None,
            },
        )

    return _write_progress


def _stop_heartbeat(heartbeat: tuple[threading.Event, threading.Thread]) -> None:
    stop_event, thread = heartbeat
    stop_event.set()
    thread.join(timeout=2)


def _read_status(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _pid() -> int:
    return os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
