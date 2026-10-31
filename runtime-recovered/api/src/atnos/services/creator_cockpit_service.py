from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.creator_ops_service import CreatorOpsService, CreatorProjectView


DEFAULT_CREATOR_NAME = "Synthetic Model Lab"
DEFAULT_CREATOR_BOUNDARIES = (
    "Non-explicit, adult-coded only, consent-safe, no real-person likeness, no minors, "
    "no impersonation, no pornographic automation, no public posting without human approval."
)
DEFAULT_CREATOR_PLATFORMS = ["youtube_short", "instagram_reels", "tiktok", "onlyfans_manual_review"]


class CreatorCockpitService:
    """Operator cockpit for the safe synthetic creator lane."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.creator_ops = CreatorOpsService(session, settings)

    def status(self) -> dict[str, Any]:
        project = self._default_project()
        if project is None:
            return {
                "status": "not_configured",
                "project": None,
                "work_items": [],
                "calendar": [],
                "asset_ledger": [],
                "counts": {
                    "work_items": 0,
                    "drafts": 0,
                    "needs_review": 0,
                    "approved": 0,
                    "calendar_items": 0,
                },
                "capabilities": self._capabilities(project=None),
                "blockers": [
                    "synthetic_creator_project_missing",
                    "creator_platform_account_missing",
                    "external_publish_blocked",
                ],
                "next_actions": [
                    "Initialize Synthetic Model Lab with safe boundaries.",
                    "Generate private draft work items.",
                    "Connect platform accounts only after review policy is clear.",
                ],
                "safety": self._safety(),
            }
        return self._build_status(project)

    def ensure_default_project(self) -> dict[str, Any]:
        project = self._default_project()
        if project is None:
            project = self.creator_ops.create_project(
                name=DEFAULT_CREATOR_NAME,
                project_type="synthetic_model",
                niche="safe fictional synthetic creator, lifestyle and short-form teaser tests",
                target_audience="adult viewers, no minor-coded styling",
                monetization_goal="validate non-explicit teaser funnels before any paid platform operations",
                content_boundaries=DEFAULT_CREATOR_BOUNDARIES,
                safety_boundaries=DEFAULT_CREATOR_BOUNDARIES,
                platforms=DEFAULT_CREATOR_PLATFORMS,
                persona_type="synthetic_original_persona",
                disclosure_policy="platform_dependent",
                operating_mode="draft_only",
                linked_account_ids=[],
                notes="Canonical Atnos synthetic creator project. External publication and paid offers stay blocked.",
                status="active",
                approval_policy="always_require_approval",
            )
        return self._build_status(project)

    def seed_work_items(self) -> dict[str, Any]:
        project = self._default_project()
        if project is None:
            project = self.ensure_default_project()["project"]
            project_id = int(project["id"])
        else:
            project_id = project.id
        result = self.creator_ops.generate_work_items(project_id, persist=True)
        return {
            "status": "seeded",
            "creator_project_id": project_id,
            "result": self._to_jsonable(result),
            "cockpit": self.status(),
        }

    def reject_external_publish(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": "Synthetic creator external publishing and paid platform actions require human approval, platform account setup, disclosure policy review and content safety review.",
            "external_publish_enabled": False,
            "paid_platform_actions_enabled": False,
            "received_payload_keys": sorted((payload or {}).keys()),
            "next_action": "Generate or review a private draft package instead.",
            "safety": self._safety(),
        }

    def _build_status(self, project: CreatorProjectView) -> dict[str, Any]:
        work_items = self.creator_ops.list_work_items(project.id) or []
        calendar = self.creator_ops.list_content_calendar_items(project.id) or []
        serialized_work = [self._to_jsonable(item) for item in work_items]
        serialized_calendar = [self._to_jsonable(item) for item in calendar]
        asset_ledger = self._asset_ledger(serialized_work)
        blockers = self._blockers(project=project)
        return {
            "status": "limited" if self._core_safe(project) else "blocked",
            "project": self._to_jsonable(project),
            "work_items": serialized_work,
            "calendar": serialized_calendar,
            "asset_ledger": asset_ledger,
            "counts": {
                "work_items": len(serialized_work),
                "drafts": sum(1 for item in serialized_work if item.get("status") in {"backlog", "drafted"}),
                "needs_review": sum(1 for item in serialized_work if item.get("status") == "needs_review"),
                "approved": sum(1 for item in serialized_work if item.get("status") == "approved"),
                "calendar_items": len(serialized_calendar),
                "asset_ledger": len(asset_ledger),
            },
            "capabilities": self._capabilities(project=project),
            "blockers": blockers,
            "next_actions": [
                "Seed private draft work items." if not serialized_work else "Review draft work items and generate preview batch.",
                "Keep publication manual until platform accounts and review policy are ready.",
                "Record every generated image/video in the asset ledger before release packaging.",
            ],
            "safety": self._safety(),
        }

    def _default_project(self) -> CreatorProjectView | None:
        synthetic = [
            project
            for project in self.creator_ops.list_projects()
            if project.project_type in {"synthetic_influencer", "synthetic_model", "creator_monetization"}
        ]
        for project in synthetic:
            if project.name == DEFAULT_CREATOR_NAME:
                return project
        return synthetic[0] if synthetic else None

    @staticmethod
    def _core_safe(project: CreatorProjectView) -> bool:
        return (
            project.persona_type == "synthetic_original_persona"
            and project.disclosure_policy != "not_required"
            and bool(project.content_boundaries)
            and bool(project.platforms)
        )

    def _capabilities(self, *, project: CreatorProjectView | None) -> dict[str, bool]:
        core_safe = bool(project and self._core_safe(project))
        has_linked_account = bool(project and project.linked_account_ids)
        return {
            "can_initialize": project is None,
            "can_plan": core_safe,
            "can_generate_private_drafts": core_safe,
            "can_generate_preview_batch": core_safe,
            "can_publish_external": False,
            "can_run_paid_platform": False,
            "has_linked_platform_account": has_linked_account,
        }

    @staticmethod
    def _blockers(*, project: CreatorProjectView) -> list[str]:
        blockers: list[str] = []
        if not project.linked_account_ids:
            blockers.append("creator_platform_account_missing")
        if project.disclosure_policy == "not_required":
            blockers.append("synthetic_disclosure_missing")
        if not project.content_boundaries:
            blockers.append("content_boundaries_missing")
        blockers.extend(["external_publish_blocked", "paid_platform_actions_blocked"])
        return blockers

    @staticmethod
    def _asset_ledger(work_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ledger: list[dict[str, Any]] = []
        for item in work_items:
            payload = item.get("draft_payload")
            if not isinstance(payload, dict):
                continue
            ledger.append(
                {
                    "source": "creator_work_item",
                    "work_item_id": item.get("id"),
                    "title": item.get("title"),
                    "work_type": item.get("work_type"),
                    "platform": item.get("platform"),
                    "status": item.get("status"),
                    "risk_level": item.get("risk_level"),
                    "approval_required": item.get("approval_required"),
                    "draft_payload": payload,
                    "license_status": "internal_draft",
                    "disclosure_required": True,
                }
            )
        return ledger

    @staticmethod
    def _safety() -> dict[str, Any]:
        return {
            "persona": "synthetic_original_only",
            "explicit_content": "blocked",
            "minor_content": "blocked",
            "real_person_likeness": "blocked",
            "impersonation": "blocked",
            "external_publish": "human_review_required",
            "paid_platform_actions": "blocked_until_policy_and_account_ready",
            "card_data_storage": "forbidden",
        }

    @classmethod
    def _to_jsonable(cls, value: Any) -> Any:
        if is_dataclass(value):
            return cls._to_jsonable(asdict(value))
        if isinstance(value, dict):
            return {str(key): cls._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._to_jsonable(item) for item in value]
        return value
