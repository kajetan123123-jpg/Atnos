from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import Settings


class ReferenceLearningService:
    """Runs the reference-learning worker as a durable Atnos-owned process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_root = Path(__file__).resolve().parents[3]
        self.worker = self.api_root / "scripts" / "run_active_reference_learning_session.py"
        self.root = settings.data_dir / "learning" / "active-reference-shorts"
        self.state_path = self.root / "session_state.json"
        self.log_path = self.root / "reference_learning_worker.log"

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        if not state:
            state = {
                "status": "not_started",
                "phase": "idle",
                "target_count": 40,
                "selected_count": 0,
                "transcript_count": 0,
                "visual_count": 0,
                "failure_count": 0,
            }
        if state.get("status") == "running" and not self._pid_alive(int(state.get("pid") or 0)):
            state = {
                **state,
                "status": "failed",
                "phase": "worker_stopped",
                "message": "Proces nauki zakończył się bez końcowego checkpointu.",
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._write_state(state)
        target_count = int(state.get("target_count") or 40)
        selected_count = int(state.get("selected_count") or 0)
        if state.get("status") == "completed" and selected_count != target_count:
            state = {
                **state,
                "status": "failed",
                "phase": "insufficient_sample",
                "message": f"Sesja niekompletna: {selected_count} z wymaganych {target_count} materialow.",
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._write_state(state)
        channel_distribution = self._read_channel_distribution(state)
        return {
            **state,
            "channel_distribution": channel_distribution,
            "state_path": str(self.state_path),
            "log_path": str(self.log_path),
            "editorial_profile_path": str(self.root / "latest_editorial_profile_wealth_blueprint.json"),
            "reference_profile_path": str(self.root / "latest_reference_profile.json"),
            "language_profile_path": str(self.root / "latest_script_language_profile_wealth_blueprint.json"),
            "policy": "aggregate_patterns_only_no_source_copy",
        }

    @staticmethod
    def _read_channel_distribution(state: dict[str, Any]) -> dict[str, int]:
        evidence_path = str(state.get("evidence_path") or "").strip()
        if not evidence_path:
            return {}
        try:
            payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        selected = payload.get("selected_videos") if isinstance(payload, dict) else None
        if not isinstance(selected, list):
            return {}
        distribution: dict[str, int] = {}
        for item in selected:
            if not isinstance(item, dict):
                continue
            channel = str(item.get("configured_channel") or item.get("channel") or "unknown").strip()
            distribution[channel] = distribution.get(channel, 0) + 1
        return distribution

    def start(self, *, profile_key: str = "wealth_blueprint", target_count: int = 40) -> dict[str, Any]:
        profile_key = profile_key.strip().casefold().replace("-", "_")
        if profile_key != "wealth_blueprint":
            raise ValueError("Ta sekcja obsługuje obecnie wyłącznie profil wealth_blueprint.")
        if target_count != 40:
            raise ValueError("Scientific batch V1 wymaga dokładnie 40 materiałów.")
        current = self.status()
        if current.get("status") == "running":
            return current
        if not self.worker.exists():
            raise FileNotFoundError(f"Brak workera nauki: {self.worker}")

        self.root.mkdir(parents=True, exist_ok=True)
        channels = 5
        per_channel = target_count // channels
        initial = {
            "schema_version": "1.0",
            "status": "starting",
            "phase": "launching_worker",
            "profile_key": profile_key,
            "target_count": target_count,
            "per_channel": per_channel,
            "selected_count": 0,
            "transcript_count": 0,
            "visual_count": 0,
            "failure_count": 0,
            "started_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "message": "Atnos uruchamia analizę 40 materiałów.",
        }
        self._write_state(initial)
        command = [
            sys.executable,
            str(self.worker),
            "--profile",
            profile_key,
            "--per-channel",
            str(per_channel),
            "--scan-limit",
            "40",
            "--state-file",
            str(self.state_path),
            "--target-total",
            str(target_count),
        ]
        with self.log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=str(self.api_root),
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
        running = {**initial, "status": "running", "phase": "collecting", "pid": process.pid}
        self._write_state(running)
        return self.status()

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_state(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
