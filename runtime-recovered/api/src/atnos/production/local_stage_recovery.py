"""Evidence checks for one safe, local production-stage recovery.

The normal editing stage runs FFmpeg locally and writes deterministic outputs.
An FFmpeg subprocess timeout can be transient, but ``TimeoutExpired`` currently
escapes StageExecutor's retry classifier. This module lets the autonomy monitor
recognize only that case after its required upstream artifacts are verified.
It never dispatches work itself; the normal dispatcher and StageExecutor remain
responsible for admission, budgets, checkpoint reuse, and quality gates.
"""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Any


_EDIT_OUTPUTS = {"renderer_silent.mp4", "final_short.mp4"}


def local_ffmpeg_timeout_message(exc: BaseException, stage: str, artifact_dir: Path) -> str | None:
    """Serialize a timeout only when it came from the installed E: FFmpeg mux."""
    import subprocess

    if stage != "editing" or not isinstance(exc, subprocess.TimeoutExpired):
        return None
    command = exc.cmd
    if not isinstance(command, (list, tuple)) or not command or not all(isinstance(arg, (str, Path)) for arg in command):
        return None
    from atnos.production.media_tools import resolve_ffmpeg

    actual = resolve_ffmpeg()
    if not actual:
        return None
    actual_path = Path(actual).resolve()
    command_path = Path(str(command[0])).resolve()
    if actual_path != command_path or actual_path.drive.casefold() != "e:":
        return None
    root = Path(artifact_dir).resolve()
    if root.drive.casefold() != "e:":
        return None
    target = Path(str(command[-1])).resolve()
    if target.parent != (root / "assets").resolve() or target.name not in _EDIT_OUTPUTS:
        return None
    outputs = [str((root / "assets" / name).resolve()) for name in sorted(_EDIT_OUTPUTS)]
    payload = {
        "local_tool_failure": {
            "exception_type": f"{type(exc).__module__}.{type(exc).__name__}",
            "stage": stage,
            "operation": "ffmpeg_mux",
            "command": [str(arg) for arg in command],
            "expected_outputs": outputs,
        }
    }
    return json.dumps(payload, sort_keys=True)


def local_stage_recovery_evidence(store: Any, state: dict[str, Any]) -> dict[str, Any] | None:
    """Return auditable evidence for retrying a timed-out local FFmpeg mux.

    This is deliberately limited to the exact ``editing`` stage. In particular,
    it excludes scene rendering, image generation, providers, render services,
    and generic transport failures where a POST outcome could be uncertain.
    """
    if state.get("status") != "blocked" or state.get("current_step") != "editing":
        return None
    error = state.get("error")
    if isinstance(error, str):
        try:
            error = json.loads(error)
        except (ValueError, TypeError):
            return None
    if not isinstance(error, dict) or error.get("category") != "local_tool_timeout":
        return None
    message = error.get("message")
    try:
        failure = json.loads(message).get("local_tool_failure") if isinstance(message, str) else None
    except (ValueError, TypeError):
        failure = None
    if not isinstance(failure, dict) or failure.get("exception_type") != "subprocess.TimeoutExpired":
        return None
    if failure.get("stage") != "editing" or failure.get("operation") != "ffmpeg_mux":
        return None
    command = failure.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        return None
    executable = PureWindowsPath(command[0])
    artifact_dir = PureWindowsPath(str(Path(state.get("artifact_dir") or "").resolve()))
    if executable.drive.casefold() != "e:" or executable.name.casefold() not in {"ffmpeg", "ffmpeg.exe"}:
        return None
    if artifact_dir.drive.casefold() != "e:":
        return None
    expected_output = artifact_dir / "assets" / PureWindowsPath(command[-1]).name
    if PureWindowsPath(command[-1]).name not in _EDIT_OUTPUTS:
        return None
    if str(PureWindowsPath(command[-1]).parent).casefold() != str(expected_output.parent).casefold():
        return None
    outputs = failure.get("expected_outputs")
    if not isinstance(outputs, list) or str(expected_output).casefold() not in {
            str(PureWindowsPath(item)).casefold() for item in outputs if isinstance(item, str)}:
        return None

    checkpoints = {str(row.get("stage")): row for row in state.get("checkpoints", [])}
    editing = checkpoints.get("editing")
    voice = checkpoints.get("voice")
    renders = [row for stage, row in checkpoints.items() if stage.startswith("render/")]
    if not editing or editing.get("status") != "failed":
        return None
    # TimeoutExpired bypasses StageExecutor's retry loop, so the only supported
    # case is its first recorded attempt. A second timeout stays blocked.
    if int(editing.get("attempts") or 0) != 1:
        return None
    if not voice or voice.get("status") != "done" or not _valid(store, state, voice):
        return None
    if not renders or any(row.get("status") != "done" or not _valid(store, state, row) for row in renders):
        return None

    return {
        "stage": "editing",
        "kind": "verified_local_ffmpeg_timeout",
        "attempts_preserved": int(editing.get("attempts") or 0),
        "verified_upstream_stages": ["voice", *sorted(row["stage"] for row in renders)],
        "dispatch_scope": "normal_stage_executor_only",
        "external_post_replay": False,
    }


def _valid(store: Any, state: dict[str, Any], checkpoint: dict[str, Any]) -> bool:
    try:
        return bool(store._valid_files(Path(state["artifact_dir"]).resolve(), checkpoint.get("files_json") or "[]"))
    except (KeyError, OSError, TypeError, ValueError):
        return False
