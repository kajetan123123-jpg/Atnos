from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class LearningHandoffResult:
    status: str
    handoff_path: Path
    report_path: Path
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "handoff_path": str(self.handoff_path),
            "report_path": str(self.report_path),
            "message": self.message,
        }


class MonetizationLearningHandoff:
    """Creates reviewable self-improvement payloads after monetization batches.

    This intentionally writes proposals only. It never edits source code.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.handoff_dir = settings.data_dir / "monetization" / "learning_handoff"
        self.log_path = settings.data_dir / "logs" / "monetization_learning.jsonl"

    def create(
        self,
        *,
        batch_summary: dict[str, Any],
        qa_failures: list[str] | None = None,
        runtime_errors: list[str] | None = None,
        analytics_feedback: dict[str, Any] | None = None,
    ) -> LearningHandoffResult:
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        run_id = f"monetization-learning-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        report_path = self.handoff_dir / f"{run_id}.json"
        handoff_path = self.handoff_dir / f"{run_id}_codex_handoff.md"
        payload = {
            "run_id": run_id,
            "status": "codex_review_required",
            "created_at": datetime.now(UTC).isoformat(),
            "batch_summary": batch_summary,
            "qa_failures": qa_failures or [],
            "runtime_errors": runtime_errors or [],
            "analytics_feedback": analytics_feedback or {},
            "source_mutations_allowed": False,
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        handoff_path.write_text(self._markdown(payload), encoding="utf-8")
        result = LearningHandoffResult(
            status="codex_review_required",
            handoff_path=handoff_path.resolve(),
            report_path=report_path.resolve(),
            message="Learning handoff created. Codex review is required before any source change.",
        )
        self._log({"event": "learning_handoff_created", **result.to_dict()})
        return result

    @staticmethod
    def _markdown(payload: dict[str, Any]) -> str:
        lines = [
            "# Monetization Learning Handoff",
            "",
            "## Governance",
            "- Atnos may propose improvements.",
            "- Atnos must not self-modify active source code.",
            "- Codex must review and apply only focused patches that improve Shorts quality or safety.",
            "",
            "## Batch summary",
            "```json",
            json.dumps(payload.get("batch_summary", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## QA failures",
        ]
        failures = payload.get("qa_failures") or []
        lines.extend(f"- {item}" for item in failures) if failures else lines.append("- none")
        lines.extend(["", "## Runtime errors"])
        errors = payload.get("runtime_errors") or []
        lines.extend(f"- {item}" for item in errors) if errors else lines.append("- none")
        lines.extend(
            [
                "",
                "## Requested Codex action",
                "Review the evidence. If it fits Atnos product direction, implement a small tested patch.",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def _log(self, payload: dict[str, object]) -> None:
        event = {"timestamp": datetime.now(UTC).isoformat(), **payload}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
