from __future__ import annotations

import json

from atnos.core.config import Settings
from channel_ops import ChannelOpsService
from youtube.youtube_readiness import YouTubeReadinessService

from .models import InitializationStatus, RevenueVector
from .shorts_contract import REQUIRED_SHORT_ARTIFACTS
from .state_machine import MonetizationStateMachine


class MonetizationInitializationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.output_dir = settings.data_dir / "monetization"
        self.status_path = self.output_dir / "initialization_status.json"
        self.log_path = settings.data_dir / "logs" / "monetization_init.jsonl"

    def initialize(self) -> InitializationStatus:
        self.settings.ensure_directories()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        missing: list[str] = []
        state_machine_ready = self._probe_state_machine(missing)
        channel_ops_ready = self._probe_channel_ops(missing)
        shorts_contract_ready = bool(REQUIRED_SHORT_ARTIFACTS)
        if not shorts_contract_ready:
            missing.append("shorts artifact contract")
        approval_gate_ready = True
        youtube_status = YouTubeReadinessService(self.settings).check(run_probe=False).status

        status_value = "initialized" if state_machine_ready and channel_ops_ready and shorts_contract_ready else "partial"
        status = InitializationStatus(
            status=status_value,
            revenue_vector=RevenueVector.AUTOMATED_SHORTS_FACTORY,
            state_machine_ready=state_machine_ready,
            channel_ops_ready=channel_ops_ready,
            shorts_contract_ready=shorts_contract_ready,
            approval_gate_ready=approval_gate_ready,
            youtube_readiness_status=str(youtube_status),
            private_upload_only=True,
            missing_requirements=tuple(missing),
            log_path=str(self.log_path.resolve()),
        )
        self.status_path.write_text(json.dumps(status.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._log({"event": "monetization_initialized", **status.to_dict()})
        return status

    def _probe_state_machine(self, missing: list[str]) -> bool:
        try:
            MonetizationStateMachine(self.settings)
        except Exception as exc:
            missing.append(f"state machine unavailable: {type(exc).__name__}: {exc}")
            return False
        return True

    def _probe_channel_ops(self, missing: list[str]) -> bool:
        try:
            ChannelOpsService(self.settings)
        except Exception as exc:
            missing.append(f"channel ops unavailable: {type(exc).__name__}: {exc}")
            return False
        return True

    def _log(self, payload: dict[str, object]) -> None:
        from datetime import UTC, datetime

        event = {"timestamp": datetime.now(UTC).isoformat(), **payload}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def initialize_monetization_framework(settings: Settings | None = None) -> InitializationStatus:
    return MonetizationInitializationService(settings or Settings()).initialize()
