from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from atnos.ai_workers.browser_ai_worker import BROWSER_WORKERS
from atnos.ai_workers.redaction import redact_payload, redact_text


@dataclass(frozen=True, slots=True)
class PlannedSubtask:
    task_type: str
    task_role: str
    title: str
    prompt: str
    worker_key: str
    privacy_mode: str
    sequence_index: int
    requires_review: bool = False
    context: dict[str, Any] = field(default_factory=dict)
    required_capabilities: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "task_role": self.task_role,
            "title": self.title,
            "prompt": self.prompt,
            "worker_key": self.worker_key,
            "privacy_mode": self.privacy_mode,
            "sequence_index": self.sequence_index,
            "requires_review": self.requires_review,
            "context": self.context,
            "required_capabilities": list(self.required_capabilities),
        }


@dataclass(frozen=True, slots=True)
class DelegationPlan:
    task_group: str
    title: str
    summary: str
    safety_notes: list[str]
    subtasks: list[PlannedSubtask]
    required_capabilities: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_group": self.task_group,
            "title": self.title,
            "summary": self.summary,
            "safety_notes": self.safety_notes,
            "subtasks": [subtask.to_dict() for subtask in self.subtasks],
            "required_capabilities": self.required_capabilities,
        }


class TaskPlanner:
    """Deterministyczny planner zadań dla AI Delegation.

    Ten planner nie udaje inteligencji i nie wykonuje akcji. Jego rola to
    bezpiecznie rozbić zlecenie na audytowalne kroki, które mogą później
    trafić do Ollamy albo kontrolowanego browser-AI.
    """

    def build_plan(
        self,
        *,
        task_type: str,
        prompt: str,
        context: dict[str, Any],
        privacy_mode: str,
        preferred_worker: str | None,
        allowed_workers: tuple[str, ...],
        max_steps: int = 6,
    ) -> DelegationPlan:
        redacted_prompt = redact_text(prompt)
        redacted_context = redact_payload(context)
        allowed = set(allowed_workers or ("ollama", *BROWSER_WORKERS.keys()))
        draft_worker = self._draft_worker(preferred_worker, allowed, privacy_mode)
        review_worker = self._review_worker(allowed, privacy_mode)
        normalized = f"{task_type}\n{redacted_prompt}".casefold()
        group = f"aiw-{uuid4().hex[:12]}"

        required_capabilities: list[dict[str, Any]] = []
        if any(token in normalized for token in ("short", "content", "script", "youtube", "reel", "thumbnail", "packaging", "production brief")):
            from atnos.integrations.capability_task_planner import plan_required_capabilities
            from atnos.integrations.capability_workflow_service import is_capability_content_task

            capability_reqs = [
                item.to_dict()
                for item in plan_required_capabilities(
                    task_type=task_type,
                    prompt=redacted_prompt,
                    context=redacted_context if isinstance(redacted_context, dict) else {},
                )
            ]
            if is_capability_content_task(task_type, redacted_prompt, redacted_context if isinstance(redacted_context, dict) else {}):
                subtasks = self._capability_content_plan(
                    redacted_prompt,
                    redacted_context,
                    privacy_mode,
                    capability_reqs,
                )
                title = "Capability-backed content review"
                summary = "Plan wybiera required_capabilities; runtime dobiera implementację bez nazw repo."
            else:
                subtasks = self._content_plan(redacted_prompt, redacted_context, draft_worker, review_worker, privacy_mode)
                title = "Content production delegation"
                summary = "Plan dla researchu, scenariusza, visual planu i QA contentu."
            required_capabilities = capability_reqs
        elif any(token in normalized for token in ("xtb", "investment", "portfolio", "paper", "market", "trading")):
            subtasks = self._investment_plan(redacted_prompt, redacted_context, draft_worker, review_worker, privacy_mode)
            title = "Investment cockpit delegation"
            summary = "Plan tylko dla read-only researchu i paper trading review; bez live zlecen."
        elif any(token in normalized for token in ("runtime", "repo", "test", "audit", "bug", "implementation")):
            subtasks = self._engineering_plan(redacted_prompt, redacted_context, draft_worker, review_worker, privacy_mode)
            title = "Engineering/runtime delegation"
            summary = "Plan dla diagnozy, implementacji, testow i review technicznego."
        else:
            subtasks = self._generic_plan(redacted_prompt, redacted_context, draft_worker, review_worker, privacy_mode)
            title = "General delegation"
            summary = "Plan ogolny: doprecyzowanie, draft, review i wynik koncowy."

        if max_steps > 0:
            subtasks = subtasks[:max_steps]

        return DelegationPlan(
            task_group=group,
            title=title,
            summary=summary,
            safety_notes=[
                "Browser AI dostaje tylko supervised handoff, bez auto-submit.",
                "Publikacja, zakupy, OAuth, 2FA i live trading musza przejsc przez action_broker.",
                "Sekrety sa redagowane przed zapisem do DB.",
                "Capability Runtime uzywa nazw capability, nigdy nazw repo w planie.",
            ],
            subtasks=subtasks,
            required_capabilities=required_capabilities,
        )

    def _capability_content_plan(
        self,
        prompt: str,
        context: Any,
        privacy_mode: str,
        capability_reqs: list[dict[str, Any]],
    ) -> list[PlannedSubtask]:
        ctx = context if isinstance(context, dict) else {"source_context": context}
        return [
            PlannedSubtask(
                task_type="capability_workflow",
                task_role="capability_runtime",
                title="Execute required capabilities and assemble production brief",
                prompt=(
                    "Run the Capability Runtime for this task. Choose implementations by capability id only. "
                    "Do not invent retention or social metrics. Preserve the original script.\n\n"
                    f"User task:\n{prompt}"
                ),
                worker_key="capability_runtime",
                privacy_mode=privacy_mode,
                sequence_index=1,
                requires_review=True,
                context={**ctx, "required_capabilities": capability_reqs},
                required_capabilities=tuple(capability_reqs),
            )
        ]

    def _content_plan(
        self,
        prompt: str,
        context: Any,
        draft_worker: str,
        review_worker: str,
        privacy_mode: str,
    ) -> list[PlannedSubtask]:
        return [
            self._step(1, "content_research_brief", "research", "Extract real constraints, target audience and success criteria.", prompt, context, draft_worker, privacy_mode),
            self._step(2, "content_hook_script_draft", "draft", "Draft an English hook/script outline based on the brief.", prompt, context, draft_worker, privacy_mode),
            self._step(3, "content_visual_plan", "visual_plan", "Map each script beat to concrete visual scenes and thumbnail direction.", prompt, context, draft_worker, privacy_mode),
            self._step(4, "content_quality_review", "review", "Review script, voice direction, visuals and QA blockers before production.", prompt, context, review_worker, privacy_mode, requires_review=True),
        ]

    def _investment_plan(
        self,
        prompt: str,
        context: Any,
        draft_worker: str,
        review_worker: str,
        privacy_mode: str,
    ) -> list[PlannedSubtask]:
        return [
            self._step(1, "market_data_requirements", "research", "List read-only market data needed for this paper strategy.", prompt, context, draft_worker, privacy_mode),
            self._step(2, "paper_strategy_draft", "draft", "Draft a paper-only strategy with entry/exit logic and invalidation conditions.", prompt, context, draft_worker, privacy_mode),
            self._step(3, "risk_guardrail_review", "risk_review", "Check max drawdown, position sizing, daily stop and live-trading blockers.", prompt, context, review_worker, privacy_mode, requires_review=True),
            self._step(4, "paper_report_plan", "report", "Define the daily paper-trading report and metrics.", prompt, context, draft_worker, privacy_mode),
        ]

    def _engineering_plan(
        self,
        prompt: str,
        context: Any,
        draft_worker: str,
        review_worker: str,
        privacy_mode: str,
    ) -> list[PlannedSubtask]:
        return [
            self._step(1, "engineering_diagnosis", "diagnosis", "Identify impacted files, dependencies and risks.", prompt, context, draft_worker, privacy_mode),
            self._step(2, "engineering_implementation_plan", "plan", "Produce a minimal implementation sequence and test plan.", prompt, context, draft_worker, privacy_mode),
            self._step(3, "engineering_review", "review", "Review the plan for architecture, security and regression risk.", prompt, context, review_worker, privacy_mode, requires_review=True),
        ]

    def _generic_plan(
        self,
        prompt: str,
        context: Any,
        draft_worker: str,
        review_worker: str,
        privacy_mode: str,
    ) -> list[PlannedSubtask]:
        return [
            self._step(1, "task_clarification", "analysis", "Extract objective, constraints, deliverables and unknowns.", prompt, context, draft_worker, privacy_mode),
            self._step(2, "task_draft", "draft", "Create the first useful draft or answer.", prompt, context, draft_worker, privacy_mode),
            self._step(3, "task_review", "review", "Review the draft and list corrections before final use.", prompt, context, review_worker, privacy_mode, requires_review=True),
        ]

    @staticmethod
    def _step(
        index: int,
        task_type: str,
        role: str,
        instruction: str,
        prompt: str,
        context: Any,
        worker_key: str,
        privacy_mode: str,
        *,
        requires_review: bool = False,
    ) -> PlannedSubtask:
        return PlannedSubtask(
            task_type=task_type,
            task_role=role,
            title=instruction,
            prompt=f"{instruction}\n\nUser task:\n{prompt}",
            worker_key=worker_key,
            privacy_mode=privacy_mode,
            sequence_index=index,
            requires_review=requires_review,
            context={"source_context": context},
        )

    @staticmethod
    def _draft_worker(preferred_worker: str | None, allowed: set[str], privacy_mode: str) -> str:
        if preferred_worker and preferred_worker in allowed:
            return preferred_worker
        if "ollama" in allowed:
            return "ollama"
        if privacy_mode != "strict_local":
            return next((worker for worker in BROWSER_WORKERS if worker in allowed), "ollama")
        return "ollama"

    @staticmethod
    def _review_worker(allowed: set[str], privacy_mode: str) -> str:
        if privacy_mode != "strict_local":
            for worker in ("browser_claude", "browser_chatgpt", "browser_kimi"):
                if worker in allowed:
                    return worker
        return "ollama"
