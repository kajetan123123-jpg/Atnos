from __future__ import annotations

"""Synchronizacja Project Brain z aktualnymi, deterministycznymi źródłami.

Serwis nie generuje stanu przez LLM. Normalizuje tylko dane przekazane przez
health-checki, readiness, pamięć operacyjną, indeks wiedzy i ślady wykonania.
"""

from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.project import Project
from atnos.db.models.project_brain import ProjectBlocker, ProjectEvidence, ProjectFact, ProjectGoal, ProjectState


READY_STATUSES = {"ready", "healthy", "connected", "available", "completed"}
FAILED_STATUSES = {"blocked", "missing", "offline", "degraded", "failed", "error"}


class ProjectBrainService:
    """Utrwala stan projektu wraz z pochodzeniem każdego istotnego wniosku."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def reconcile(
        self,
        *,
        project: Project,
        runtime: dict[str, Any],
        readiness: dict[str, Any] | None,
        knowledge: dict[str, Any],
        blockers: list[dict[str, Any]],
        next_step: dict[str, Any],
        evidence: list[dict[str, Any]],
        delegations: list[dict[str, Any]],
        traces: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Zapisuje obserwację i zwraca zserializowany widok Project Brain."""
        observed_at = datetime.now(UTC)
        self._sync_declared_goal(project, observed_at)
        self._sync_evidence(project.id, evidence, observed_at)
        self._sync_runtime_facts(project.id, runtime, observed_at)
        self._sync_readiness_fact(project.id, readiness, observed_at)
        self._sync_knowledge_fact(project.id, knowledge, observed_at)
        self._sync_delegation_fact(project.id, delegations, observed_at)
        self._sync_blockers(project.id, blockers, evidence, observed_at)
        state = self._sync_state(
            project_id=project.id,
            runtime=runtime,
            readiness=readiness,
            knowledge=knowledge,
            blockers=blockers,
            next_step=next_step,
            evidence=evidence,
            observed_at=observed_at,
        )
        self.session.flush()
        return self._serialize(project.id, state, traces)

    def _sync_declared_goal(self, project: Project, observed_at: datetime) -> None:
        """Przenosi deklarowany opis projektu do celu bez uznawania go za fakt runtime."""
        description = (project.summary or project.description or "").strip()
        if not description:
            return
        source_ref = f"project:{project.id}:metadata"
        goal = self.session.scalar(
            select(ProjectGoal).where(ProjectGoal.project_id == project.id, ProjectGoal.source_ref == source_ref)
        )
        values = {
            "title": f"Cel: {project.name}",
            "description": description,
            "status": "active",
            "source_type": "project_metadata",
            "source_ref": source_ref,
            "observed_at": observed_at,
            "confidence_score": 0.8,
            "verified": False,
            "evidence_json": {"project_name": project.name, "declared_description": description},
        }
        if goal is None:
            self.session.add(ProjectGoal(project_id=project.id, **values))
        else:
            for field, value in values.items():
                setattr(goal, field, value)

    def snapshot(self, project_id: int, *, trace_limit: int = 12) -> dict[str, Any]:
        state = self.session.scalar(select(ProjectState).where(ProjectState.project_id == project_id))
        return self._serialize(project_id, state, [], trace_limit=trace_limit)

    def _sync_evidence(self, project_id: int, evidence: list[dict[str, Any]], observed_at: datetime) -> None:
        for item in evidence:
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            stored = self.session.scalar(
                select(ProjectEvidence).where(
                    ProjectEvidence.project_id == project_id,
                    ProjectEvidence.evidence_key == key,
                )
            )
            payload = _json_object(item)
            values = {
                "evidence_type": "runtime_snapshot",
                "source_type": str(item.get("source") or "unknown"),
                "source_ref": str(item.get("source") or "") or None,
                "summary": str(item.get("summary") or "Brak opisu dowodu."),
                "collected_at": _datetime_or(observed_at, item.get("checked_at")),
                "verified": bool(item.get("verified")),
                "content_hash": _hash(payload),
                "payload_json": payload,
            }
            if stored is None:
                self.session.add(ProjectEvidence(project_id=project_id, evidence_key=key, **values))
            else:
                for field, value in values.items():
                    setattr(stored, field, value)

    def _sync_runtime_facts(self, project_id: int, runtime: dict[str, Any], observed_at: datetime) -> None:
        components = runtime.get("components") if isinstance(runtime.get("components"), list) else []
        for component in components:
            if not isinstance(component, dict):
                continue
            key = str(component.get("key") or "").strip()
            if not key:
                continue
            status = str(component.get("status") or "unknown")
            content = " | ".join(
                part for part in (
                    str(component.get("label") or key),
                    f"status={status}",
                    str(component.get("detail") or ""),
                ) if part
            )
            self._upsert_fact(
                project_id=project_id,
                fact_key=f"runtime:{key}",
                content=content,
                source_type="runtime_health_service",
                source_ref=key,
                observed_at=observed_at,
                confidence_score=1.0,
                verification_status="verified" if status in READY_STATUSES | FAILED_STATUSES else "observed",
                evidence={"component": _json_object(component), "runtime_status": runtime.get("status")},
            )

    def _sync_readiness_fact(self, project_id: int, readiness: dict[str, Any] | None, observed_at: datetime) -> None:
        if not readiness:
            return
        status = str(readiness.get("status") or "unknown")
        self._upsert_fact(
            project_id=project_id,
            fact_key="readiness:canonical",
            content=f"Canonical readiness status={status}.",
            source_type="project_readiness_service",
            source_ref=str(readiness.get("project_id") or "") or None,
            observed_at=_datetime_or(observed_at, readiness.get("generated_at")),
            confidence_score=1.0 if readiness.get("project_id") else 0.35,
            verification_status="verified" if readiness.get("project_id") else "observed",
            evidence=_json_object(readiness),
        )

    def _sync_knowledge_fact(self, project_id: int, knowledge: dict[str, Any], observed_at: datetime) -> None:
        library = knowledge.get("library_matches") if isinstance(knowledge.get("library_matches"), list) else []
        obsidian = knowledge.get("obsidian") if isinstance(knowledge.get("obsidian"), dict) else {}
        content = f"Knowledge lookup: library_matches={len(library)}, obsidian_readable={bool(obsidian.get('readable'))}."
        self._upsert_fact(
            project_id=project_id,
            fact_key="knowledge:availability",
            content=content,
            source_type="knowledge_library+obsidian",
            source_ref=str(knowledge.get("query") or "") or None,
            observed_at=observed_at,
            confidence_score=0.8 if library or obsidian.get("readable") else 0.4,
            verification_status="observed",
            evidence={
                "query": knowledge.get("query"),
                "library_match_count": len(library),
                "obsidian_readable": bool(obsidian.get("readable")),
                "library_error": knowledge.get("library_error"),
            },
        )

    def _sync_delegation_fact(self, project_id: int, delegations: list[dict[str, Any]], observed_at: datetime) -> None:
        if not delegations:
            return
        latest = delegations[0]
        self._upsert_fact(
            project_id=project_id,
            fact_key="delegation:latest",
            content=f"Latest delegation #{latest.get('id')} status={latest.get('status')} verification={latest.get('verification_status')}.",
            source_type="ai_worker_tasks",
            source_ref=str(latest.get("id") or "") or None,
            observed_at=_datetime_or(observed_at, latest.get("updated_at")),
            confidence_score=1.0,
            verification_status="verified" if latest.get("verification_status") == "completed" else "observed",
            evidence=_json_object(latest),
        )

    def _sync_blockers(
        self,
        project_id: int,
        blockers: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        observed_at: datetime,
    ) -> None:
        checked_sources = {str(item.get("source") or "") for item in evidence}
        observed_keys: set[str] = set()
        for blocker in blockers:
            source = str(blocker.get("source") or "unknown")
            source_key = f"{source}:{str(blocker.get('key') or blocker.get('title') or 'unknown')}"
            observed_keys.add(source_key)
            stored = self.session.scalar(
                select(ProjectBlocker).where(
                    ProjectBlocker.project_id == project_id,
                    ProjectBlocker.source_key == source_key,
                )
            )
            values = {
                "title": str(blocker.get("title") or source_key),
                "detail": _optional_text(blocker.get("detail")),
                "next_action": _optional_text(blocker.get("next_action")),
                "severity": _blocker_severity(blocker),
                "status": "open",
                "source_type": source,
                "source_ref": _optional_text(blocker.get("key")),
                "observed_at": observed_at,
                "confidence_score": 1.0 if blocker.get("verified") else 0.55,
                "verified": bool(blocker.get("verified")),
                "evidence_json": _json_object(blocker),
            }
            if stored is None:
                self.session.add(ProjectBlocker(project_id=project_id, source_key=source_key, **values))
            else:
                for field, value in values.items():
                    setattr(stored, field, value)

        open_items = self.session.scalars(
            select(ProjectBlocker).where(ProjectBlocker.project_id == project_id, ProjectBlocker.status == "open")
        ).all()
        for stored in open_items:
            if stored.source_key in observed_keys or stored.source_type not in checked_sources:
                continue
            stored.status = "resolved"
            stored.observed_at = observed_at

    def _sync_state(
        self,
        *,
        project_id: int,
        runtime: dict[str, Any],
        readiness: dict[str, Any] | None,
        knowledge: dict[str, Any],
        blockers: list[dict[str, Any]],
        next_step: dict[str, Any],
        evidence: list[dict[str, Any]],
        observed_at: datetime,
    ) -> ProjectState:
        components = runtime.get("components") if isinstance(runtime.get("components"), list) else []
        working = [_component_view(item) for item in components if isinstance(item, dict) and str(item.get("status") or "") in READY_STATUSES]
        failing = [_component_view(item) for item in components if isinstance(item, dict) and str(item.get("status") or "") in FAILED_STATUSES]
        questions = self._open_questions(readiness, knowledge)
        verified_evidence = [item for item in evidence if item.get("verified")]
        readiness_status = str((readiness or {}).get("status") or runtime.get("status") or "unknown")
        summary = (
            f"Runtime={runtime.get('status', 'unknown')}; readiness={readiness_status}; "
            f"verified_blockers={len(blockers)}; verified_evidence={len(verified_evidence)}."
        )
        state_payload = {
            "phase": self._phase(readiness),
            "readiness_status": readiness_status,
            "working": working,
            "failing": failing,
            "risks": [_risk_view(item) for item in blockers],
            "open_questions": questions,
            "next_step": _json_object(next_step),
            "evidence": [_json_object(item) for item in evidence],
        }
        stored = self.session.scalar(select(ProjectState).where(ProjectState.project_id == project_id))
        values = {
            "phase": str(state_payload["phase"]),
            "readiness_status": readiness_status,
            "summary": summary,
            "working_json": working,
            "failing_json": failing,
            "risks_json": state_payload["risks"],
            "open_questions_json": questions,
            "next_step_json": _json_object(next_step),
            "source_observed_at": observed_at,
            "source_hash": _hash(state_payload),
            "confidence_score": 1.0 if verified_evidence else 0.35,
            "verified": bool(verified_evidence),
            "evidence_json": {"evidence_keys": [str(item.get("key")) for item in evidence]},
        }
        if stored is None:
            stored = ProjectState(project_id=project_id, **values)
            self.session.add(stored)
        else:
            for field, value in values.items():
                setattr(stored, field, value)
        return stored

    def _upsert_fact(
        self,
        *,
        project_id: int,
        fact_key: str,
        content: str,
        source_type: str,
        source_ref: str | None,
        observed_at: datetime,
        confidence_score: float,
        verification_status: str,
        evidence: dict[str, object],
    ) -> None:
        stored = self.session.scalar(
            select(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.fact_key == fact_key)
        )
        values = {
            "content": content,
            "source_type": source_type,
            "source_ref": source_ref,
            "observed_at": observed_at,
            "expires_at": None,
            "confidence_score": confidence_score,
            "verification_status": verification_status,
            "source_hash": _hash(evidence),
            "evidence_json": evidence,
        }
        if stored is None:
            self.session.add(ProjectFact(project_id=project_id, fact_key=fact_key, **values))
        else:
            for field, value in values.items():
                setattr(stored, field, value)

    def _serialize(
        self,
        project_id: int,
        state: ProjectState | None,
        traces: list[dict[str, Any]],
        *,
        trace_limit: int = 12,
    ) -> dict[str, Any]:
        blockers = self.session.scalars(
            select(ProjectBlocker)
            .where(ProjectBlocker.project_id == project_id, ProjectBlocker.status == "open")
            .order_by(ProjectBlocker.verified.desc(), ProjectBlocker.updated_at.desc(), ProjectBlocker.id.desc())
            .limit(16)
        ).all()
        goals = self.session.scalars(
            select(ProjectGoal)
            .where(ProjectGoal.project_id == project_id)
            .order_by(ProjectGoal.updated_at.desc(), ProjectGoal.id.desc())
            .limit(12)
        ).all()
        facts = self.session.scalars(
            select(ProjectFact)
            .where(ProjectFact.project_id == project_id)
            .order_by(ProjectFact.observed_at.desc(), ProjectFact.id.desc())
            .limit(20)
        ).all()
        evidence = self.session.scalars(
            select(ProjectEvidence)
            .where(ProjectEvidence.project_id == project_id)
            .order_by(ProjectEvidence.collected_at.desc(), ProjectEvidence.id.desc())
            .limit(trace_limit)
        ).all()
        return {
            "state": None if state is None else {
                "phase": state.phase,
                "readiness_status": state.readiness_status,
                "summary": state.summary,
                "working": state.working_json,
                "failing": state.failing_json,
                "risks": state.risks_json,
                "open_questions": state.open_questions_json,
                "next_step": state.next_step_json,
                "observed_at": state.source_observed_at.isoformat(),
                "source_hash": state.source_hash,
                "confidence_score": state.confidence_score,
                "verified": state.verified,
            },
            "goals": [
                {
                    "id": item.id,
                    "title": item.title,
                    "description": item.description,
                    "status": item.status,
                    "source": item.source_type,
                    "source_ref": item.source_ref,
                    "observed_at": item.observed_at.isoformat(),
                    "confidence_score": item.confidence_score,
                    "verified": item.verified,
                }
                for item in goals
            ],
            "blockers": [
                {
                    "id": item.id,
                    "source_key": item.source_key,
                    "title": item.title,
                    "detail": item.detail,
                    "next_action": item.next_action,
                    "severity": item.severity,
                    "status": item.status,
                    "source": item.source_type,
                    "observed_at": item.observed_at.isoformat(),
                    "verified": item.verified,
                }
                for item in blockers
            ],
            "facts": [
                {
                    "key": item.fact_key,
                    "content": item.content,
                    "source": item.source_type,
                    "source_ref": item.source_ref,
                    "observed_at": item.observed_at.isoformat(),
                    "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                    "confidence_score": item.confidence_score,
                    "verification_status": item.verification_status,
                }
                for item in facts
            ],
            "evidence": [
                {
                    "key": item.evidence_key,
                    "type": item.evidence_type,
                    "source": item.source_type,
                    "source_ref": item.source_ref,
                    "summary": item.summary,
                    "collected_at": item.collected_at.isoformat(),
                    "verified": item.verified,
                    "content_hash": item.content_hash,
                }
                for item in evidence
            ],
            "recent_traces": traces,
        }

    @staticmethod
    def _phase(readiness: dict[str, Any] | None) -> str:
        decision = (readiness or {}).get("operator_decision")
        if isinstance(decision, dict) and decision.get("state"):
            return str(decision["state"])
        return str((readiness or {}).get("status") or "unknown")

    @staticmethod
    def _open_questions(readiness: dict[str, Any] | None, knowledge: dict[str, Any]) -> list[dict[str, object]]:
        questions: list[dict[str, object]] = []
        if not readiness or not readiness.get("project_id"):
            questions.append({"question": "Brak kanonicznego profilu gotowosci projektu.", "source": "project_readiness_service"})
        obsidian = knowledge.get("obsidian") if isinstance(knowledge.get("obsidian"), dict) else {}
        if not obsidian.get("readable"):
            questions.append({"question": "Obsidian nie dostarczyl odczytywalnych notatek do tego projektu.", "source": "obsidian"})
        if knowledge.get("library_error"):
            questions.append({"question": "Indeks wiedzy zwrocil blad i wymaga osobnej kontroli.", "source": "knowledge_library"})
        return questions


def _component_view(component: dict[str, Any]) -> dict[str, object]:
    return {
        "key": str(component.get("key") or "unknown"),
        "label": str(component.get("label") or component.get("key") or "unknown"),
        "status": str(component.get("status") or "unknown"),
        "detail": str(component.get("detail") or ""),
        "next_action": component.get("next_action"),
    }


def _risk_view(blocker: dict[str, Any]) -> dict[str, object]:
    return {
        "title": str(blocker.get("title") or "Unknown blocker"),
        "source": str(blocker.get("source") or "unknown"),
        "verified": bool(blocker.get("verified")),
        "next_action": blocker.get("next_action"),
    }


def _blocker_severity(blocker: dict[str, Any]) -> str:
    source = str(blocker.get("source") or "")
    if source == "runtime_health_service":
        return "high"
    if source == "ai_worker_tasks":
        return "medium"
    return "medium" if blocker.get("verified") else "low"


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _datetime_or(fallback: datetime, value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return fallback


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"value": str(value)}
    return json.loads(json.dumps(value, default=str, ensure_ascii=True))


def _hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
