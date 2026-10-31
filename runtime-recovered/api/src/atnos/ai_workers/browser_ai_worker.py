from __future__ import annotations

from typing import Any

from atnos.ai_workers.models import WorkerExecutionResult, WorkerStatusSnapshot
from atnos.core.config import Settings
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.runtime_settings_service import RuntimeControlSnapshot


BROWSER_WORKERS = {
    "browser_claude": {
        "label": "Claude Browser",
        "url": "https://claude.ai/",
        "domains": ("claude.ai",),
    },
    "browser_kimi": {
        "label": "Kimi Browser",
        "url": "https://www.kimi.com/",
        "domains": ("kimi.com",),
    },
    "browser_chatgpt": {
        "label": "ChatGPT Browser",
        "url": "https://chatgpt.com/",
        "domains": ("chatgpt.com", "chat.openai.com"),
    },
}


class BrowserAiWorker:
    allowed_task_types = (
        "deep_research",
        "review",
        "style_guide",
        "script_quality_review",
        "visual_prompt_review",
        "thumbnail_review",
        "qa_explanation",
        "trend_synthesis",
        "competitor_pattern_summary",
        "topic_idea_review",
    )

    def __init__(
        self,
        settings: Settings,
        *,
        worker_key: str,
        runtime_controls: RuntimeControlSnapshot,
    ) -> None:
        if worker_key not in BROWSER_WORKERS:
            raise ValueError(f"Unknown browser AI worker: {worker_key}")
        self.settings = settings
        self.worker_key = worker_key
        self.definition = BROWSER_WORKERS[worker_key]
        self.runtime_controls = runtime_controls
        self.browser_runtime = BrowserRuntimeService(settings)

    def status(self) -> WorkerStatusSnapshot:
        state = self.browser_runtime.describe()
        if self.runtime_controls.emergency_stop_enabled or self.runtime_controls.pause_all_jobs:
            return self._snapshot(
                status="blocked_by_policy",
                ready=False,
                reason="Runtime emergency stop or pause-all is enabled.",
                browser_ready=state.ready,
            )
        if not self.runtime_controls.browser_control_enabled:
            return self._snapshot(
                status="blocked_by_policy",
                ready=False,
                reason="Browser control is disabled in runtime settings.",
                browser_ready=state.ready,
            )
        if not self.runtime_controls.external_actions_enabled:
            return self._snapshot(
                status="blocked_by_policy",
                ready=False,
                reason="External actions are disabled in runtime settings.",
                browser_ready=state.ready,
            )
        if not state.ready:
            return self._snapshot(
                status="runtime_unavailable",
                ready=False,
                reason=state.status_reason,
                browser_ready=False,
            )
        return self._snapshot(
            status="handoff_only",
            ready=False,
            reason="Browser session is available only for a supervised handoff; Atnos cannot claim an executed result.",
            browser_ready=True,
        )

    async def run(self, *, task_type: str, prompt: str, context: dict[str, Any] | None = None) -> WorkerExecutionResult:
        status = self.status()
        if not status.ready and status.status != "handoff_only":
            return WorkerExecutionResult(
                worker_key=self.worker_key,
                status="blocked",
                summary=status.reason,
                result=status.to_dict(),
                blocked_reason=status.reason,
            )
        # Intentional: v1 prepares a supervised browser handoff instead of submitting prompts
        # into third-party UIs without a stable consented recipe.
        package = {
            "handoff_mode": "supervised_browser_prompt",
            "target_url": self.definition["url"],
            "allowed_domains": list(self.definition["domains"]),
            "task_type": task_type,
            "prompt": prompt,
            "context": context or {},
            "safety_note": (
                "Open the browser worker manually or through an approved recipe. "
                "Do not submit OAuth, payment, trading, publishing or account-change actions."
            ),
        }
        return WorkerExecutionResult(
            worker_key=self.worker_key,
            status="pending_external",
            summary=f"Prepared supervised browser handoff for {self.definition['label']}.",
            result=package,
        )

    def _snapshot(self, *, status: str, ready: bool, reason: str, browser_ready: bool) -> WorkerStatusSnapshot:
        return WorkerStatusSnapshot(
            worker_key=self.worker_key,
            label=str(self.definition["label"]),
            status=status,  # type: ignore[arg-type]
            ready=ready,
            reason=reason,
            capabilities=("browser_ai", "text", "review", "research"),
            allowed_task_types=self.allowed_task_types,
            requires_browser=True,
            metadata={
                "target_url": self.definition["url"],
                "allowed_domains": list(self.definition["domains"]),
                "browser_ready": browser_ready,
                "can_prepare_handoff": status == "handoff_only",
                "can_execute": False,
            },
        )
