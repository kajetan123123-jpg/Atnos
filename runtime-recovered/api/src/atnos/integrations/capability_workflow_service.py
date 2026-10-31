"""Capability Runtime as a normal Atnos work cycle.

USER TASK → Project Brain → Plan → required_capabilities → Runtime → Evidence →
Brief → QA → Project Memory / PRODUCTION_BRIEF_READY.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from atnos.integrations.capability_runtime import execute_capability, record_last_execution
from atnos.integrations.capability_task_planner import (
    CapabilityRequirement,
    plan_fact_check_followups,
    plan_required_capabilities,
)
from atnos.integrations.capability_workflows import check_claim, persist_evidence

NON_FATAL = frozenset(
    {
        "REQUIRES_SETUP",
        "REQUIRES_DATA",
        "NOT_INSTALLED",
        "EMPTY_CONTENT",
        "REQUIRES_BROWSER",
        "ACCESS_DENIED",
        "DEGRADED",
    }
)
FATAL = frozenset({"FAILED", "BLOCKED"})
KNOWLEDGE_TYPES = frozenset({"OBSERVATION", "EVIDENCE", "HYPOTHESIS", "RECOMMENDATION", "DECISION"})

DEFAULT_SCRIPT = Path(
    r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint\prod-dc76431dac84\script.json"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_script_path(context: dict[str, Any] | None = None) -> Path | None:
    context = context or {}
    candidates = [
        context.get("script_path"),
        context.get("production_script_path"),
    ]
    production_id = context.get("production_id") or context.get("order_id")
    project_key = context.get("project_key") or context.get("channel") or "wealth_blueprint"
    if production_id:
        candidates.append(
            Path(r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production")
            / str(project_key)
            / str(production_id)
            / "script.json"
        )
        candidates.append(Path(r"E:\Atnos\App\data\production") / str(project_key) / str(production_id) / "script.json")
    if str(production_id or "") == "prod-dc76431dac84" or context.get("use_wb_pilot_script"):
        candidates.append(DEFAULT_SCRIPT)
    for item in candidates:
        if not item:
            continue
        path = Path(str(item))
        if path.is_file():
            return path
    return None


def load_script(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"text": path.read_text(encoding="utf-8", errors="replace"), "path": str(path)}
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.setdefault("path", str(path))
        return payload
    return {"text": str(payload), "path": str(path)}


class CapabilityWorkflowService:
    """Runs a planner-selected capability cycle for a real production task."""

    def __init__(
        self,
        *,
        settings: Any = None,
        session: Any = None,
        root: Path | None = None,
        brain_store: Any = None,
        execute_fn: Callable[..., dict[str, Any]] | None = None,
        public_data_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.root = root
        self.brain_store = brain_store
        self.execute_fn = execute_fn or execute_capability
        self.public_data_fn = public_data_fn or check_claim

    def run(
        self,
        *,
        prompt: str,
        task_type: str = "capability_workflow",
        context: dict[str, Any] | None = None,
        project_key: str | None = None,
        inject_status: dict[str, str] | None = None,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        context = dict(context or {})
        project = str(
            project_key
            or context.get("project_key")
            or context.get("channel")
            or context.get("project_name")
            or "wealth_blueprint"
        ).strip()
        if project.casefold() in {"wealth blueprint", "wb"}:
            project = "wealth_blueprint"
        production_id = str(context.get("production_id") or context.get("order_id") or "").strip() or None
        script_path = resolve_script_path({**context, "project_key": project})
        script = load_script(script_path)
        brain_context = self._load_brain_context(project)

        plan = plan_required_capabilities(task_type=task_type, prompt=prompt, context=context)
        trace: list[dict[str, Any]] = [
            {
                "stage": "user_task",
                "status": "SUCCESS",
                "detail": prompt[:500],
                "evidence": [],
            },
            {
                "stage": "project_brain_context",
                "status": "SUCCESS",
                "detail": f"{len(brain_context.get('insights') or [])} insights, {len(brain_context.get('decisions') or [])} decisions",
                "evidence": [{"kind": "brain_snapshot", "project": project}],
            },
            {
                "stage": "planner",
                "status": "SUCCESS",
                "detail": "required_capabilities inferred without repo names",
                "capabilities": [item.to_dict() for item in plan],
                "evidence": [],
            },
        ]

        executions: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        evidence_graph: list[dict[str, Any]] = []
        brain_writes: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        script_qa: dict[str, Any] | None = None
        packaging: dict[str, Any] | None = None
        required_failed = False
        blocked_reason: str | None = None

        params_base = self._base_params(script=script, script_path=script_path, context=context)

        for requirement in plan:
            result, attempts = self._execute_with_policy(
                requirement,
                params_base,
                project=project,
                inject_status=inject_status,
                max_retries=max_retries,
            )
            executions.append(result)
            duration_ms = int((time.perf_counter() - started) * 1000)
            status = str(result.get("status") or "FAILED")
            trace.append(
                {
                    "stage": "capability_execute",
                    "capability": requirement.capability,
                    "status": status,
                    "execution_id": result.get("execution_id"),
                    "duration_ms": duration_ms,
                    "required": requirement.required,
                    "optional": requirement.optional,
                    "attempts": attempts,
                    "evidence": result.get("evidence") or [],
                }
            )
            try:
                record_last_execution(self.root, result)
            except Exception:
                pass

            evidence_graph.append(
                {
                    "recommendation_scope": "production_brief",
                    "capability": requirement.capability,
                    "execution_id": result.get("execution_id"),
                    "status": status,
                    "source_input": {
                        "script_path": str(script_path) if script_path else None,
                        "production_id": production_id,
                        "project": project,
                    },
                    "output_present": result.get("output") is not None,
                    "provenance": result.get("provenance"),
                }
            )

            if status == "SUCCESS":
                output = result.get("output") if isinstance(result.get("output"), dict) else {}
                if requirement.capability == "YOUTUBE_SCRIPT_QA":
                    script_qa = output
                    brain_writes.extend(self._write_script_qa_knowledge(project, output, result))
                    followups = plan_fact_check_followups(output)
                    for follow in followups:
                        fact = self._run_fact_check(follow, project=project)
                        executions.append(fact)
                        trace.append(
                            {
                                "stage": "fact_check_bridge",
                                "capability": follow.capability,
                                "status": fact.get("status"),
                                "execution_id": fact.get("execution_id"),
                                "evidence": fact.get("evidence") or [],
                            }
                        )
                        if fact.get("status") in {"UNVERIFIED", "NO_CAPABILITY"} or fact.get("claim_status") == "UNVERIFIED":
                            unresolved.append(
                                {
                                    "kind": "UNVERIFIED_CLAIM",
                                    "capability": follow.capability,
                                    "detail": follow.reason,
                                    "action": "weaken_or_remove_or_block",
                                }
                            )
                elif requirement.capability == "YOUTUBE_PACKAGING":
                    packaging = output
                    brain_writes.extend(self._write_packaging_knowledge(project, output, result))
                continue

            if status in NON_FATAL or (requirement.optional and status in FATAL | NON_FATAL):
                message = self._skip_message(requirement.capability, status, result)
                skipped.append(
                    {
                        "capability": requirement.capability,
                        "status": status,
                        "message": message,
                        "execution_id": result.get("execution_id"),
                    }
                )
                if status == "REQUIRES_DATA":
                    unresolved.append(
                        {
                            "kind": "UNRESOLVED_DATA_REQUIREMENT",
                            "capability": requirement.capability,
                            "detail": message,
                            "required_data": requirement.required_data,
                        }
                    )
                if status == "REQUIRES_SETUP":
                    unresolved.append(
                        {
                            "kind": "REQUIRES_SETUP",
                            "capability": requirement.capability,
                            "detail": message,
                        }
                    )
                if status == "NOT_INSTALLED":
                    unresolved.append(
                        {
                            "kind": "NOT_INSTALLED",
                            "capability": requirement.capability,
                            "detail": message,
                        }
                    )
                continue

            # Required capability hard failure after retries.
            if requirement.required:
                required_failed = True
                blocked_reason = (
                    f"Required capability {requirement.capability} ended as {status}; "
                    "no invented fallback output."
                )
                break

        brief = self._build_production_brief(
            prompt=prompt,
            project=project,
            production_id=production_id,
            script=script,
            script_path=script_path,
            script_qa=script_qa,
            packaging=packaging,
            unresolved=unresolved,
            executions=executions,
        )

        claim_gate = None
        claim_gate = self._run_claim_evidence_gate(
            project=project,
            production_id=production_id or "unknown",
            script=script,
            context=context,
        )
        if claim_gate:
            trace.append(
                {
                    "stage": "claim_evidence_gate",
                    "status": claim_gate.get("gate", {}).get("decision"),
                    "execution_id": claim_gate.get("ledger_path"),
                    "evidence": [
                        {
                            "kind": "claim_coverage",
                            **(claim_gate.get("claim_coverage") or {}),
                        }
                    ],
                }
            )
            blocker = claim_gate.get("production_blocker") or {}
            if blocker.get("blocked"):
                required_failed = True
                blocked_reason = f"FACT_CHECK_BLOCKED: {blocker.get('detail')}"
                unresolved.append(
                    {
                        "kind": "FACT_CHECK_BLOCKED",
                        "detail": blocker.get("detail"),
                        "blocked_claims": blocker.get("blocked_claims") or [],
                    }
                )
            for repair in claim_gate.get("repairs") or []:
                unresolved.append(
                    {
                        "kind": "CLAIM_REPAIR_CANDIDATE",
                        "claim_id": repair.get("claim_id"),
                        "before": repair.get("before"),
                        "options": repair.get("options"),
                    }
                )
            brief["claim_ledger"] = {
                "path": claim_gate.get("ledger_path"),
                "coverage": claim_gate.get("claim_coverage"),
                "gate": claim_gate.get("gate"),
                "claims_summary": _claim_summary(claim_gate.get("claims") or []),
            }

        decision = self._maybe_decision(project, production_id, packaging, executions)
        if decision:
            decisions.append(decision)
            brain_writes.append(self._persist_decision(project, decision))

        qc = self._quality_council(
            brief=brief,
            executions=executions,
            skipped=skipped,
            packaging=packaging,
            script_qa=script_qa,
        )
        trace.append(
            {
                "stage": "quality_council",
                "status": qc.get("status") or "COMPLETED",
                "execution_id": qc.get("run_id"),
                "evidence": qc.get("findings") or [],
            }
        )
        trace.append(
            {
                "stage": "production_brief",
                "status": brief.get("status") or "PRODUCTION_BRIEF_READY",
                "evidence": [{"kind": "brief_keys", "keys": sorted(brief.keys())}],
            }
        )

        if required_failed:
            workflow_status = "BLOCKED"
        elif skipped and script_qa and packaging:
            workflow_status = "PARTIAL"
        elif script_qa and packaging:
            workflow_status = "COMPLETED"
        elif script_qa or packaging:
            workflow_status = "PARTIAL"
        else:
            workflow_status = "FAILED" if required_failed else "PARTIAL"

        capabilities_used = [
            {
                "capability": item.get("capability"),
                "status": item.get("status"),
                "execution_id": item.get("execution_id"),
                "optional": next(
                    (req.optional for req in plan if req.capability == item.get("capability")),
                    False,
                ),
            }
            for item in executions
            if item.get("capability")
        ]

        payload = {
            "status": workflow_status,
            "workflow_status": workflow_status,
            "production_status": "PRODUCTION_BRIEF_READY"
            if workflow_status in {"COMPLETED", "PARTIAL"} and not required_failed
            else ("BLOCKED" if required_failed else workflow_status),
            "published": False,
            "production_started": False,
            "project": project,
            "production_id": production_id,
            "user_task": prompt,
            "plan": [item.to_dict() for item in plan],
            "required_capabilities": [item.to_dict() for item in plan],
            "capabilities_used": capabilities_used,
            "capabilities_executed": executions,
            "skipped": skipped,
            "unresolved_data_requirements": unresolved,
            "evidence_graph": evidence_graph,
            "execution_trace": trace,
            "script_qa": script_qa,
            "packaging": packaging,
            "production_brief": brief,
            "project_brain_writes": [item for item in brain_writes if item],
            "decisions": decisions,
            "quality_council": qc,
            "blocked_reason": blocked_reason,
            "script_path": str(script_path) if script_path else None,
            "claim_evidence_gate": {
                "decision": (claim_gate or {}).get("gate", {}).get("decision") if claim_gate else None,
                "coverage": (claim_gate or {}).get("claim_coverage") if claim_gate else None,
                "ledger_path": (claim_gate or {}).get("ledger_path") if claim_gate else None,
                "production_blocker": (claim_gate or {}).get("production_blocker") if claim_gate else None,
                "repairs": (claim_gate or {}).get("repairs") if claim_gate else [],
            }
            if claim_gate
            else None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "repo_names_in_plan": False,
        }
        self._close_owned_brain()
        return payload

    def _base_params(
        self,
        *,
        script: dict[str, Any],
        script_path: Path | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if script_path is not None:
            params["script_path"] = str(script_path)
        if script.get("text"):
            params["script"] = script["text"]
        elif script.get("segments"):
            params["script"] = " ".join(
                str(seg.get("text") or "") for seg in script["segments"] if isinstance(seg, dict)
            )
        if context.get("rows") is not None:
            params["rows"] = context["rows"]
        if context.get("retention") is not None:
            params["retention"] = context["retention"]
        if context.get("query"):
            params["query"] = context["query"]
        params["platform_id"] = context.get("platform_id") or "youtube"
        return params

    def _execute_with_policy(
        self,
        requirement: CapabilityRequirement,
        params: dict[str, Any],
        *,
        project: str,
        inject_status: dict[str, str] | None,
        max_retries: int,
    ) -> tuple[dict[str, Any], int]:
        attempts = 0
        last: dict[str, Any] = {}
        while attempts <= max_retries:
            attempts += 1
            if inject_status and requirement.capability in inject_status:
                forced = inject_status[requirement.capability]
                last = {
                    "execution_id": f"inject-{requirement.capability.lower()}-{attempts}",
                    "capability": requirement.capability,
                    "status": forced,
                    "output": None,
                    "evidence": [],
                    "errors": [f"Injected status {forced} for failure/policy test."],
                    "verification": {"ok": False},
                    "provenance": {"source_type": "test_inject", "capability": requirement.capability},
                    "mock_or_live": "LOCAL VERIFIED",
                }
            else:
                last = self.execute_fn(
                    requirement.capability,
                    parameters=dict(params),
                    project_id=project,
                    root=self.root,
                    settings=self.settings,
                    session=self.session,
                )
            status = str(last.get("status") or "FAILED")
            if status == "SUCCESS" or status in NON_FATAL:
                return last, attempts
            if status in FATAL and requirement.required and attempts <= max_retries:
                continue
            return last, attempts
        return last, attempts

    def _skip_message(self, capability: str, status: str, result: dict[str, Any]) -> str:
        detail = ""
        errors = result.get("errors") or []
        if errors:
            detail = str(errors[0])
        output = result.get("output")
        if isinstance(output, dict) and output.get("detail"):
            detail = str(output["detail"])
        if capability == "YOUTUBE_RETENTION_ANALYSIS" and status == "REQUIRES_DATA":
            return "Retention analysis skipped because no observed retention data is available."
        if capability == "SOCIAL_READ" and status == "REQUIRES_SETUP":
            return "Social performance data unavailable because the account is not connected."
        if capability == "ANIMATION_2D_RENDER" and status in {"NOT_INSTALLED", "REQUIRES_SETUP"}:
            return "ANIMATION_2D_RENDER is NOT_INSTALLED; Remotion was not used as a fake substitute."
        return detail or f"{capability} → {status}"

    def _load_brain_context(self, project: str) -> dict[str, Any]:
        store = self._brain()
        if store is None:
            return {"insights": [], "decisions": []}
        try:
            insights = store.list_insights(project) if hasattr(store, "list_insights") else []
            decisions = store.list_decisions(project) if hasattr(store, "list_decisions") else []
            return {"insights": insights, "decisions": decisions}
        except Exception:
            return {"insights": [], "decisions": []}

    def _brain(self) -> Any:
        if self.brain_store is not None:
            return self.brain_store
        if getattr(self, "_owned_brain", None) is not None:
            return self._owned_brain
        if self.settings is None:
            return None
        try:
            import sqlite3

            from atnos.intelligence.storage import intelligence_db_path
            from atnos.production.project_content_brain.content_intelligence import ContentIntelligenceStore

            path = intelligence_db_path(self.settings)
            path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(path), check_same_thread=False)
            db.row_factory = sqlite3.Row
            store = ContentIntelligenceStore(db)
            store._atnos_close_on_done = db  # noqa: SLF001
            self._owned_brain = store
            return store
        except Exception:
            return None

    def _close_owned_brain(self) -> None:
        store = getattr(self, "_owned_brain", None)
        if store is None:
            return
        conn = getattr(store, "_atnos_close_on_done", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._owned_brain = None

    def _close_brain_if_owned(self, writes: list[dict[str, Any]] | None = None) -> None:
        self._close_owned_brain()

    def _write_script_qa_knowledge(
        self,
        project: str,
        output: dict[str, Any],
        execution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        writes = []
        word_count = output.get("word_count")
        if isinstance(word_count, int):
            writes.append(
                self._persist_insight(
                    project,
                    {
                        "kind": "OBSERVATION",
                        "knowledge_type": "OBSERVATION",
                        "source": "CAPABILITY_RUNTIME",
                        "topic": "script_qa",
                        "statement": f"The script contains {word_count} words.",
                        "capability": "YOUTUBE_SCRIPT_QA",
                        "execution_id": execution.get("execution_id"),
                        "confidence": "source_data",
                        "invented_value": False,
                    },
                )
            )
        hook = output.get("hook_issues") or []
        if hook:
            writes.append(
                self._persist_insight(
                    project,
                    {
                        "kind": "RECOMMENDATION",
                        "knowledge_type": "RECOMMENDATION",
                        "source": "CAPABILITY_RUNTIME",
                        "topic": "script_hook",
                        "statement": str(hook[0].get("detail") if isinstance(hook[0], dict) else hook[0]),
                        "capability": "YOUTUBE_SCRIPT_QA",
                        "execution_id": execution.get("execution_id"),
                        "confidence": "heuristic",
                        "invented_value": False,
                    },
                )
            )
        return [item for item in writes if item]

    def _write_packaging_knowledge(
        self,
        project: str,
        output: dict[str, Any],
        execution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        titles = output.get("title_concepts") or output.get("titles") or []
        if not titles:
            return []
        first = titles[0]
        text = first.get("text") if isinstance(first, dict) else str(first)
        status = first.get("status") if isinstance(first, dict) else "CANDIDATE"
        return [
            self._persist_insight(
                project,
                {
                    "kind": "HYPOTHESIS",
                    "knowledge_type": "HYPOTHESIS",
                    "source": "CAPABILITY_RUNTIME",
                    "topic": "packaging_title",
                    "statement": f"Title concept ({status}): {text}",
                    "capability": "YOUTUBE_PACKAGING",
                    "execution_id": execution.get("execution_id"),
                    "confidence": "candidate_not_proven",
                    "invented_value": False,
                    "performance_claimed": False,
                },
            )
        ]

    def _persist_insight(self, project: str, insight: dict[str, Any]) -> dict[str, Any] | None:
        store = self._brain()
        if store is None or not hasattr(store, "save_insight"):
            return {"stored": False, "store_reason": "no_brain_store", **insight}
        # Temporary packaging candidates stay out of durable brain — only observations / decisions.
        if insight.get("knowledge_type") == "HYPOTHESIS" and insight.get("topic") == "packaging_title":
            return {
                "stored": False,
                "store_reason": "temporary_candidate_kept_in_execution_history",
                **insight,
            }
        try:
            insight_id = store.save_insight(project, insight)
            return {"stored": True, "insight_id": insight_id, "project": project, **insight}
        except Exception as exc:  # noqa: BLE001
            return {"stored": False, "store_reason": str(exc), **insight}

    def _persist_decision(self, project: str, decision: dict[str, Any]) -> dict[str, Any] | None:
        store = self._brain()
        if store is None or not hasattr(store, "save_decision"):
            return {"stored": False, "store_reason": "no_brain_store", **decision}
        try:
            decision_id = store.save_decision(project, decision)
            return {"stored": True, "decision_id": decision_id, "project": project, **decision}
        except Exception as exc:  # noqa: BLE001
            return {"stored": False, "store_reason": str(exc), **decision}

    def _maybe_decision(
        self,
        project: str,
        production_id: str | None,
        packaging: dict[str, Any] | None,
        executions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not packaging:
            return None
        titles = packaging.get("title_concepts") or []
        candidate = None
        for item in titles:
            if isinstance(item, dict) and item.get("status") == "CANDIDATE":
                candidate = item
                break
        if candidate is None and titles:
            candidate = titles[0] if isinstance(titles[0], dict) else {"text": titles[0], "status": "CANDIDATE"}
        if candidate is None:
            return None
        exec_ids = [item.get("execution_id") for item in executions if item.get("capability") == "YOUTUBE_PACKAGING"]
        return {
            "kind": "DECISION",
            "knowledge_type": "DECISION",
            "decision": f"Use title candidate for preview: {candidate.get('text')}",
            "reason": "Selected as packaging CANDIDATE from YOUTUBE_PACKAGING; not a proven winner.",
            "evidence": {
                "candidate_status": candidate.get("status") or "CANDIDATE",
                "ranking_basis": packaging.get("ranking_basis") or "heuristic_lint_not_performance",
            },
            "capability_executions": exec_ids,
            "project_id": project,
            "production_id": production_id,
            "timestamp": _now(),
            "proven_winner": False,
        }

    def _run_fact_check(self, requirement: CapabilityRequirement, *, project: str) -> dict[str, Any]:
        text = requirement.reason
        if requirement.capability == "WEB_PAGE_EXTRACT":
            import re

            from atnos.integrations.web_page_extract import fact_check_claim_against_url

            url_match = re.search(r"https?://[^\s)\]\"'|]+", text or "")
            if not url_match:
                return {
                    "execution_id": "fact-web_page_extract",
                    "capability": "WEB_PAGE_EXTRACT",
                    "status": "REQUIRES_DATA",
                    "claim_status": "UNVERIFIED",
                    "output": None,
                    "evidence": [],
                    "errors": ["WEB_PAGE_EXTRACT fact-check needs a source URL."],
                    "invented_value": False,
                }
            claim = re.sub(r"https?://[^\s)\]\"'|]+", " ", text).replace("|", " ").strip()
            page_check = fact_check_claim_against_url(claim, url_match.group(0), root=self.root)
            store = self._brain()
            if store is not None and hasattr(store, "save_insight") and page_check.get("blocks"):
                try:
                    persist_evidence(
                        store,
                        project,
                        {
                            "capability": "WEB_PAGE_EXTRACT",
                            "status": "SUCCESS",
                            "output": {
                                "title": page_check.get("page_title"),
                                "blocks": [
                                    {"id": b.get("block_id"), "text": b.get("text"), "type": b.get("type")}
                                    for b in page_check.get("blocks") or []
                                ],
                                "content_hash": page_check.get("content_hash"),
                                "final_url": page_check.get("final_url"),
                                "url": page_check.get("url"),
                            },
                            "provenance": page_check.get("provenance") or {},
                            "brain_class": page_check.get("brain_class") or "EVIDENCE",
                            "claim": claim,
                            "curated": True,
                        },
                    )
                except Exception:
                    pass
            return {
                "execution_id": "fact-web_page_extract",
                "capability": "WEB_PAGE_EXTRACT",
                "status": "SUCCESS" if page_check.get("verdict") != "NOT_FOUND" or page_check.get("status") == "SUCCESS" else page_check.get("status"),
                "claim_status": page_check.get("claim_status"),
                "verdict": page_check.get("verdict"),
                "output": {
                    "verdict": page_check.get("verdict"),
                    "blocks": page_check.get("blocks"),
                    "block_ids": page_check.get("block_ids"),
                    "page_title": page_check.get("page_title"),
                    "content_hash": page_check.get("content_hash"),
                },
                "evidence": [
                    {
                        "kind": "web_page_block",
                        "url": page_check.get("final_url") or page_check.get("url"),
                        "block_ids": page_check.get("block_ids") or [],
                        "content_hash": page_check.get("content_hash"),
                    }
                ],
                "errors": [] if page_check.get("blocks") else [page_check.get("detail") or "No supporting blocks found."],
                "invented_value": False,
                "provenance": page_check.get("provenance"),
                "source_quality": page_check.get("source_quality"),
                "extraction_quality": page_check.get("extraction_quality"),
            }

        result = self.public_data_fn(text)
        status = str(result.get("status") or "UNVERIFIED")
        if status != "HEALTHY":
            return {
                "execution_id": f"fact-{requirement.capability.lower()}",
                "capability": requirement.capability,
                "status": "UNVERIFIED" if status in {"NO_CAPABILITY", "UNAVAILABLE", "ERROR"} else status,
                "claim_status": "UNVERIFIED",
                "output": None,
                "evidence": [],
                "errors": [result.get("detail") or "Claim could not be confirmed; no invented number."],
                "invented_value": False,
                "provenance": result.get("provenance"),
            }
        store = self._brain()
        if store is not None and hasattr(store, "save_insight"):
            try:
                # Adapt router HEALTHY shape into persist_evidence if possible.
                persist_evidence(
                    store,
                    project,
                    {
                        "status": "HEALTHY",
                        "capability": requirement.capability,
                        "value": result.get("value"),
                        "provider": result.get("provider"),
                        "provenance": result.get("provenance") or {},
                        "brain_provenance": result.get("brain_provenance") or {},
                        "routing": {"selected_provider": result.get("provider"), "fallback_used": False},
                    },
                )
            except Exception:
                pass
        return {
            "execution_id": f"fact-{requirement.capability.lower()}",
            "capability": requirement.capability,
            "status": "SUCCESS",
            "claim_status": "VERIFIED",
            "output": result.get("value"),
            "evidence": [{"kind": "public_data", "provider": result.get("provider")}],
            "errors": [],
            "invented_value": False,
            "provenance": result.get("provenance"),
        }

    def _build_production_brief(
        self,
        *,
        prompt: str,
        project: str,
        production_id: str | None,
        script: dict[str, Any],
        script_path: Path | None,
        script_qa: dict[str, Any] | None,
        packaging: dict[str, Any] | None,
        unresolved: list[dict[str, Any]],
        executions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        segments = script.get("segments") if isinstance(script.get("segments"), list) else []
        hook_seg = next((s for s in segments if isinstance(s, dict) and s.get("type") == "hook"), None)
        cta_seg = next((s for s in segments if isinstance(s, dict) and s.get("type") == "cta"), None)
        hook_text = (hook_seg or {}).get("text") or (script.get("text") or "").split(".")[0]
        core = script.get("description") or script.get("title") or ""
        factual_claims = []
        for claim in (script_qa or {}).get("unsupported_claims") or []:
            if isinstance(claim, dict):
                factual_claims.append(
                    {
                        "text": claim.get("text") or claim.get("claim"),
                        "status": "UNVERIFIED",
                        "segment_id": claim.get("segment_id"),
                        "action": "remove_weaken_or_block",
                    }
                )
        packaging_candidates = {
            "titles": (packaging or {}).get("title_concepts") or [],
            "thumbnails": (packaging or {}).get("thumbnail_concepts") or [],
            "status": "CANDIDATE",
            "not": "WINNER",
        }
        return {
            "status": "PRODUCTION_BRIEF_READY",
            "project": project,
            "production_id": production_id,
            "topic": script.get("title") or "Wealth Blueprint short",
            "audience_promise": core,
            "hook": hook_text,
            "core_takeaway": core,
            "concrete_viewer_action": (cta_seg or {}).get("text")
            or "Start moving a fixed amount to savings on a set day each week.",
            "factual_claims": factual_claims,
            "evidence_requirements": [
                item for item in unresolved if item.get("kind") == "UNRESOLVED_DATA_REQUIREMENT"
            ],
            "visual_requirements": [
                str(seg.get("visual_intent"))
                for seg in segments
                if isinstance(seg, dict) and seg.get("visual_intent")
            ],
            "packaging_candidates": packaging_candidates,
            "unresolved_data_requirements": unresolved,
            "script_path": str(script_path) if script_path else None,
            "script_original_preserved": True,
            "capability_execution_ids": [item.get("execution_id") for item in executions],
            "user_task": prompt,
            "published": False,
            "zeros_for_missing_data": False,
        }

    def _quality_council(
        self,
        *,
        brief: dict[str, Any],
        executions: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
        packaging: dict[str, Any] | None,
        script_qa: dict[str, Any] | None,
    ) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        # factual grounding
        for claim in brief.get("factual_claims") or []:
            if claim.get("status") == "UNVERIFIED":
                findings.append(
                    {
                        "area": "factual_grounding",
                        "severity": "warn",
                        "detail": f"Unverified claim kept explicit: {claim.get('text')}",
                    }
                )
        # capability failures ignored?
        for item in executions:
            status = item.get("status")
            if status in FATAL and item.get("capability") in {
                "YOUTUBE_SCRIPT_QA",
                "YOUTUBE_PACKAGING",
            }:
                # If brief still claims success packaging without output — flag
                if not packaging and item.get("capability") == "YOUTUBE_PACKAGING":
                    findings.append(
                        {
                            "area": "capability_failures",
                            "severity": "error",
                            "detail": "Packaging FAILED but brief would lack candidates.",
                        }
                    )
        for item in skipped:
            findings.append(
                {
                    "area": "capability_skipped",
                    "severity": "info",
                    "detail": item.get("message"),
                    "status": item.get("status"),
                    "capability": item.get("capability"),
                }
            )
        # unsupported confidence / packaging consistency
        titles = (packaging or {}).get("title_concepts") or []
        for title in titles:
            if isinstance(title, dict) and title.get("status") == "WINNER":
                findings.append(
                    {
                        "area": "unsupported_confidence",
                        "severity": "error",
                        "detail": "Packaging declared WINNER without performance evidence.",
                    }
                )
            if isinstance(title, dict) and title.get("status") != "CANDIDATE":
                findings.append(
                    {
                        "area": "packaging_consistency",
                        "severity": "warn",
                        "detail": f"Unexpected title status: {title.get('status')}",
                    }
                )
        if packaging and packaging.get("ranking_basis") == "fictional_performance":
            findings.append(
                {
                    "area": "unsupported_confidence",
                    "severity": "error",
                    "detail": "Packaging used fictional performance metrics.",
                }
            )
        contradictions = []
        if script_qa and packaging:
            hook_issues = script_qa.get("hook_issues") or []
            if hook_issues and not (packaging.get("hook_alignment") or {}).get("notes"):
                contradictions.append("Script QA flagged hook issues but packaging hook_alignment notes missing.")
        for item in contradictions:
            findings.append({"area": "contradictions", "severity": "warn", "detail": item})

        errors = [f for f in findings if f.get("severity") == "error"]
        production_ready = not errors and brief.get("status") == "PRODUCTION_BRIEF_READY"
        return {
            "run_id": f"qc-cap-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "council": "existing_quality_checks_capability_workflow",
            "status": "FAIL" if errors else "PASS_WITH_LIMITS",
            "production_readiness": "ready_for_approval" if production_ready else "blocked_or_partial",
            "findings": findings,
            "checks": [
                "factual_grounding",
                "contradictions",
                "unsupported_confidence",
                "capability_failures_ignored",
                "packaging_consistency",
                "production_readiness",
            ],
            "note": "Test pass count is not used as content-quality proof.",
        }

    def _run_claim_evidence_gate(
        self,
        *,
        project: str,
        production_id: str,
        script: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if context.get("skip_claim_evidence_gate") is True:
            return None
        # Wealth Blueprint is the primary gate target; other projects opt in.
        if project not in {"wealth_blueprint"} and not context.get("force_claim_evidence_gate"):
            return None
        from atnos.integrations.claim_evidence_gate import (
            build_ledger,
            production_blocker_from_ledger,
        )

        extra = context.get("extra_claims") if isinstance(context.get("extra_claims"), list) else None
        live = context.get("live_claim_research", True) is not False
        try:
            ledger = build_ledger(
                project=project,
                production_id=production_id,
                script=script,
                root=self.root,
                live_research=bool(live),
                extra_claims=extra,
            )
        except Exception as exc:
            return {
                "error": str(exc),
                "gate": {"decision": "WARN", "production_allowed": True},
                "claims": [],
                "claim_coverage": {},
                "repairs": [],
                "production_blocker": {"blocked": False, "detail": f"Claim gate error: {exc}"},
            }
        blocker = production_blocker_from_ledger(ledger)
        # Persist only verified brain writes with provenance.
        store = self._brain()
        if store is not None and hasattr(store, "save_insight"):
            for row in ledger.get("brain_writes") or []:
                if row.get("knowledge_type") == "EVIDENCE" and row.get("provenance"):
                    try:
                        store.save_insight(project, row)
                    except Exception:
                        pass
        return {
            **ledger,
            "production_blocker": blocker,
        }


def _claim_summary(claims: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(claims),
        "supported": 0,
        "not_required": 0,
        "blocked_status": 0,
        "partial": 0,
        "other": 0,
    }
    for claim in claims:
        status = str(claim.get("fact_check_status") or "")
        if status == "SUPPORTED":
            summary["supported"] += 1
        elif status == "NOT_REQUIRED":
            summary["not_required"] += 1
        elif status == "PARTIALLY_SUPPORTED":
            summary["partial"] += 1
        elif status in {"CONTRADICTED", "UNVERIFIED", "NOT_FOUND", "UNREVIEWED"}:
            summary["blocked_status"] += 1
        else:
            summary["other"] += 1
    return summary


def is_capability_content_task(task_type: str, prompt: str, context: dict[str, Any] | None = None) -> bool:
    text = f"{task_type}\n{prompt}".casefold()
    if task_type.strip() in {"capability_workflow", "content_capability_review", "production_brief_review"}:
        return True
    context = context or {}
    if context.get("run_capability_workflow") is True:
        return True
    return any(
        token in text
        for token in (
            "production brief",
            "packaging recommendation",
            "improve packaging",
            "youtube packaging",
            "script qa",
            "using available capabilities",
        )
    )
