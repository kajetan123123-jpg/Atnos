from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from atnos.db.models.account_credential import AccountCredential
from atnos.db.models.creator_project import ContentCalendarItem, CreatorProject, CreatorWorkItem
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.creator_project_repository import CreatorProjectRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.profit_os_service import ProfitOsService
from atnos.services.project_app_links_service import ProjectAppLinksService

ALLOWED_PROJECT_TYPES: Final = {
    "youtube_channel",
    "shorts_channel",
    "instagram_brand",
    "synthetic_influencer",
    "synthetic_model",
    "creator_monetization",
    "market_lab",
    "growth_experiment",
    "self_improvement",
}
ALLOWED_PROJECT_STATUSES: Final = {"planned", "active", "paused", "archived"}
ALLOWED_PERSONA_TYPES: Final = {"none", "human_user", "brand", "synthetic_original_persona"}
ALLOWED_DISCLOSURE_POLICIES: Final = {
    "not_required",
    "synthetic_disclosed",
    "brand_account",
    "platform_dependent",
}
ALLOWED_OPERATING_MODES: Final = {
    "planning_only",
    "draft_only",
    "supervised_actions",
    "autonomous_later",
}
ALLOWED_APPROVAL_POLICIES: Final = {
    "always_require_approval",
    "allow_low_risk_drafts",
    "future_policy",
}
ALLOWED_WORK_TYPES: Final = {
    "research",
    "content_idea",
    "script",
    "storyboard",
    "local_preview",
    "thumbnail_brief",
    "asset_brief",
    "quality_review",
    "publish_package",
    "analytics_review",
    "growth_experiment",
    "action_draft_preview",
}
ALLOWED_WORK_STATUSES: Final = {
    "backlog",
    "drafted",
    "needs_review",
    "approved",
    "blocked",
    "done",
}
ALLOWED_RISK_LEVELS: Final = {"low", "medium", "high", "critical"}
ALLOWED_WORK_ITEM_SORTS: Final = {"queue_default", "risk", "status", "updated_at", "created_at"}
ALLOWED_REVIEW_ACTIONS: Final = {
    "approve_review",
    "request_changes",
    "block",
    "defer",
    "mark_done",
}
ALLOWED_CONTENT_CALENDAR_STATUSES: Final = {
    "idea",
    "planned",
    "drafted",
    "needs_review",
    "approved",
    "scheduled_later",
    "done",
    "blocked",
}
ALLOWED_CONTENT_CALENDAR_CADENCES: Final = {"daily", "three_times_weekly", "weekly"}
IMPERSONATION_PHRASES: Final = (
    "podszywaj się",
    "podszywaj sie",
    "udawaj prawdziwą osobę",
    "udawaj prawdziwa osobe",
    "make it look like",
    "deepfake",
    "use celebrity face",
    "impersonate",
    "real person likeness",
    "use [real person]",
)
_UNSET = object()


@dataclass(frozen=True, slots=True)
class CreatorProjectView:
    id: int
    project_id: int | None
    name: str
    project_type: str
    status: str
    persona_type: str
    disclosure_policy: str
    niche: str | None
    target_audience: str | None
    monetization_goal: str | None
    content_boundaries: str | None
    safety_boundaries: str | None
    platforms: tuple[str, ...]
    linked_account_ids: tuple[int, ...]
    linked_integrations: tuple[str, ...]
    operating_mode: str
    approval_policy: str
    notes: str | None
    strategy_profile: dict[str, object]
    linked_accounts_summary: tuple[dict[str, object], ...]
    project_app_links_summary: tuple[dict[str, object], ...]
    project_app_links_defaults_summary: dict[str, object]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CreatorWorkItemView:
    id: int | None
    creator_project_id: int
    title: str
    work_type: str
    status: str
    risk_level: str
    approval_required: bool
    platform: str | None
    draft_payload: dict[str, object] | None
    review_notes: str | None
    next_action: str | None
    safety_flags: tuple[str, ...]
    created_at: str | None
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class CreatorWorkItemApprovalPackageView:
    work_item: dict[str, object]
    project: dict[str, object]
    linked_apps_defaults: dict[str, object]
    linked_apps_summary: tuple[dict[str, object], ...]
    risk: str
    safety_flags: tuple[str, ...]
    review_notes: str | None
    proposed_next_action: str
    action_broker_payload_preview: dict[str, object]


@dataclass(frozen=True, slots=True)
class ContentCalendarItemView:
    id: int | None
    creator_project_id: int
    work_item_id: int | None
    work_item_title: str | None
    title: str
    platform: str | None
    content_type: str
    planned_date: str
    status: str
    risk_level: str
    approval_required: bool
    linked_app_link_id: int | None
    linked_app_label: str | None
    notes: str | None
    created_at: str | None
    updated_at: str | None


class CreatorOpsService:
    def __init__(self, session: Session, settings=None) -> None:
        self.session = session
        self.repository = CreatorProjectRepository(session)
        self.account_repository = AccountCredentialRepository(session)
        self.project_repository = ProjectRepository(session)
        self.profit_os = ProfitOsService()
        self.project_app_links = ProjectAppLinksService(session, settings) if settings is not None else None

    def list_projects(self) -> list[CreatorProjectView]:
        accounts_by_id = self._accounts_by_id()
        return [self._build_project_view(item, accounts_by_id) for item in self.repository.list_projects()]

    def get_project(self, project_id: int) -> CreatorProjectView | None:
        item = self.repository.get_project_by_id(project_id)
        if item is None:
            return None
        return self._build_project_view(item, self._accounts_by_id())

    def get_project_by_canonical_project_id(self, project_id: int) -> CreatorProjectView | None:
        item = self.repository.get_project_by_canonical_project_id(project_id)
        if item is None:
            return None
        return self._build_project_view(item, self._accounts_by_id())

    def create_project(
        self,
        *,
        name: str,
        project_type: str,
        niche: str | None,
        target_audience: str | None,
        monetization_goal: str | None,
        content_boundaries: str | None,
        platforms: list[str],
        persona_type: str,
        disclosure_policy: str,
        operating_mode: str,
        linked_account_ids: list[int] | None = None,
        notes: str | None = None,
        safety_boundaries: str | None = None,
        status: str = "active",
        approval_policy: str = "always_require_approval",
    ) -> CreatorProjectView:
        normalized = self._normalize_project_payload(
            name=name,
            project_type=project_type,
            status=status,
            persona_type=persona_type,
            disclosure_policy=disclosure_policy,
            niche=niche,
            target_audience=target_audience,
            monetization_goal=monetization_goal,
            content_boundaries=content_boundaries,
            safety_boundaries=safety_boundaries,
            platforms=platforms,
            linked_account_ids=linked_account_ids or [],
            operating_mode=operating_mode,
            approval_policy=approval_policy,
            notes=notes,
        )
        canonical_project = self.project_repository.create(normalized["name"])
        item = self.repository.create_project(project_id=canonical_project.id, **normalized)
        return self._build_project_view(item, self._accounts_by_id())

    def update_project(
        self,
        project_id: int,
        *,
        name: str | None = None,
        niche: str | None = None,
        target_audience: str | None = None,
        monetization_goal: str | None = None,
        content_boundaries: str | None = None,
        safety_boundaries: str | None = None,
        platforms: list[str] | None = None,
        persona_type: str | None = None,
        disclosure_policy: str | None = None,
        operating_mode: str | None = None,
        linked_account_ids: list[int] | None = None,
        notes: str | None = None,
        status: str | None = None,
        approval_policy: str | None = None,
    ) -> CreatorProjectView | None:
        item = self.repository.get_project_by_id(project_id)
        if item is None:
            return None

        normalized = self._normalize_project_payload(
            name=name if name is not None else item.name,
            project_type=item.project_type,
            status=status if status is not None else item.status,
            persona_type=persona_type if persona_type is not None else item.persona_type,
            disclosure_policy=disclosure_policy if disclosure_policy is not None else item.disclosure_policy,
            niche=niche if niche is not None else item.niche,
            target_audience=target_audience if target_audience is not None else item.target_audience,
            monetization_goal=monetization_goal if monetization_goal is not None else item.monetization_goal,
            content_boundaries=content_boundaries if content_boundaries is not None else item.content_boundaries,
            safety_boundaries=safety_boundaries if safety_boundaries is not None else item.safety_boundaries,
            platforms=platforms if platforms is not None else list(item.platforms_json or []),
            linked_account_ids=linked_account_ids if linked_account_ids is not None else list(item.linked_accounts_json or []),
            operating_mode=operating_mode if operating_mode is not None else item.operating_mode,
            approval_policy=approval_policy if approval_policy is not None else item.approval_policy,
            notes=notes if notes is not None else item.notes,
        )
        item.name = normalized["name"]
        if item.project_id is not None:
            project = self.project_repository.get(item.project_id)
            if project is not None and project.name != normalized["name"]:
                self.project_repository.update_name(project, normalized["name"])
        item.status = normalized["status"]
        item.persona_type = normalized["persona_type"]
        item.disclosure_policy = normalized["disclosure_policy"]
        item.niche = normalized["niche"]
        item.target_audience = normalized["target_audience"]
        item.monetization_goal = normalized["monetization_goal"]
        item.content_boundaries = normalized["content_boundaries"]
        item.safety_boundaries = normalized["safety_boundaries"]
        item.platforms_json = normalized["platforms_json"]
        item.linked_accounts_json = normalized["linked_accounts_json"]
        item.linked_integrations_json = normalized["linked_integrations_json"]
        item.operating_mode = normalized["operating_mode"]
        item.approval_policy = normalized["approval_policy"]
        item.notes = normalized["notes"]
        self.repository.save_project(item)
        return self._build_project_view(item, self._accounts_by_id())

    def update_strategy_profile_by_canonical_project_id(
        self,
        canonical_project_id: int,
        *,
        strategy_profile: dict[str, object],
    ) -> CreatorProjectView | None:
        item = self.repository.get_project_by_canonical_project_id(canonical_project_id)
        if item is None:
            return None
        item.strategy_profile_json = dict(strategy_profile or {})
        self.repository.save_project(item)
        return self._build_project_view(item, self._accounts_by_id())

    def generate_operating_plan(self, project_id: int) -> dict[str, object] | None:
        item = self.repository.get_project_by_id(project_id)
        if item is None:
            return None

        project = self._build_project_view(item, self._accounts_by_id())
        return self._build_operating_plan(project)

    def generate_work_items(
        self,
        project_id: int,
        *,
        persist: bool = False,
    ) -> dict[str, object] | None:
        item = self.repository.get_project_by_id(project_id)
        if item is None:
            return None

        project = self._build_project_view(item, self._accounts_by_id())
        draft_items = self._build_work_item_drafts(project)
        if not persist:
            return {
                "creator_project_id": project.id,
                "preview_only": True,
                "persisted": False,
                "work_items": [self._build_preview_work_item_view(project.id, draft) for draft in draft_items],
            }

        persisted_views: list[CreatorWorkItemView] = []
        for draft in draft_items:
            stored = self.repository.create_work_item(
                creator_project_id=project.id,
                title=str(draft["title"]),
                work_type=str(draft["work_type"]),
                status="backlog",
                risk_level=str(draft["risk_level"]),
                approval_required=bool(draft["approval_required"]),
                platform=draft.get("platform") if isinstance(draft.get("platform"), str) else None,
                draft_payload_json=draft.get("draft_payload") if isinstance(draft.get("draft_payload"), dict) else None,
                review_notes=None,
                next_action=str(draft["next_action"]) if draft.get("next_action") is not None else None,
                safety_flags_json=list(draft.get("safety_flags", [])),
            )
            persisted_views.append(self._build_work_item_view(stored))

        return {
            "creator_project_id": project.id,
            "preview_only": False,
            "persisted": True,
            "work_items": [self._serialize_work_item_view(item) for item in persisted_views],
        }

    def list_work_items(
        self,
        project_id: int,
        *,
        status: str | None = None,
        risk_level: str | None = None,
        work_type: str | None = None,
        platform: str | None = None,
        needs_review: bool | None = None,
        needs_approval: bool | None = None,
        sort: str = "queue_default",
    ) -> list[CreatorWorkItemView] | None:
        project = self.repository.get_project_by_id(project_id)
        if project is None:
            return None
        normalized_status = self._normalize_work_item_status(status) if status is not None else None
        normalized_risk_level = self._normalize_risk_level(risk_level) if risk_level is not None else None
        normalized_work_type = self._normalize_work_type(work_type) if work_type is not None else None
        normalized_platform = self._normalize_optional_text(platform)
        normalized_sort = self._normalize_work_item_sort(sort)
        items = [self._build_work_item_view(item) for item in self.repository.list_work_items(project_id)]
        filtered_items = [
            item
            for item in items
            if self._matches_work_item_filters(
                item,
                status=normalized_status,
                risk_level=normalized_risk_level,
                work_type=normalized_work_type,
                platform=normalized_platform,
                needs_review=needs_review,
                needs_approval=needs_approval,
            )
        ]
        return self._sort_work_items(filtered_items, normalized_sort)

    def update_work_item(
        self,
        work_item_id: int,
        *,
        status: str | None = None,
        review_action: str | None = None,
        review_notes: str | None = None,
        next_action: str | None = None,
    ) -> CreatorWorkItemView | None:
        item = self.repository.get_work_item_by_id(work_item_id)
        if item is None:
            return None
        if status is not None and review_action is not None:
            raise ValueError("Choose status or review_action, not both.")
        if review_action is not None:
            item.status = self._status_for_review_action(review_action)
        if status is not None:
            item.status = self._normalize_work_item_status(status)
        if review_notes is not None:
            item.review_notes = self._normalize_optional_text(review_notes)
        if next_action is not None:
            item.next_action = self._normalize_optional_text(next_action)
        self.repository.save_work_item(item)
        return self._build_work_item_view(item)

    def get_work_item_approval_package(
        self,
        work_item_id: int,
    ) -> CreatorWorkItemApprovalPackageView | None:
        item = self.repository.get_work_item_by_id(work_item_id)
        if item is None:
            return None
        project_item = self.repository.get_project_by_id(item.creator_project_id)
        if project_item is None:
            return None
        project = self._build_project_view(project_item, self._accounts_by_id())
        work_item = self._build_work_item_view(item)
        preferred_role = self._preferred_default_role_for_work_item(project, work_item)
        preferred_link = (
            self._get_project_app_link_by_default_role(project, preferred_role)
            or self._get_project_app_link_by_default_role(project, "project")
            or next(iter(project.project_app_links_summary), None)
        )
        return CreatorWorkItemApprovalPackageView(
            work_item={
                "id": work_item.id,
                "title": work_item.title,
                "work_type": work_item.work_type,
                "status": work_item.status,
                "risk_level": work_item.risk_level,
                "approval_required": work_item.approval_required,
                "platform": work_item.platform,
            },
            project={
                "id": project.id,
                "project_id": project.project_id,
                "name": project.name,
                "project_type": project.project_type,
                "status": project.status,
                "operating_mode": project.operating_mode,
                "approval_policy": project.approval_policy,
            },
            linked_apps_defaults=dict(project.project_app_links_defaults_summary),
            linked_apps_summary=tuple(project.project_app_links_summary),
            risk=work_item.risk_level,
            safety_flags=work_item.safety_flags,
            review_notes=work_item.review_notes,
            proposed_next_action=work_item.next_action or "Prepare the next supervised step.",
            action_broker_payload_preview={
                "preview_only": True,
                "execution_blocked": True,
                "creator_project_id": project.id,
                "project_id": project.project_id,
                "creator_project_name": project.name,
                "preferred_default_role": preferred_role,
                "project_app_link_id": preferred_link.get("id") if preferred_link is not None else None,
                "integration_key": preferred_link.get("integration_key") if preferred_link is not None else None,
                "account_id": preferred_link.get("account_id") if preferred_link is not None else None,
                "work_type": work_item.work_type,
                "platform": work_item.platform,
                "risk_level": work_item.risk_level,
                "approval_policy": project.approval_policy,
                "operating_mode": project.operating_mode,
                "draft_payload": work_item.draft_payload or {},
            },
        )

    def list_content_calendar_items(self, creator_project_id: int) -> list[ContentCalendarItemView] | None:
        project_item = self.repository.get_project_by_id(creator_project_id)
        if project_item is None:
            return None
        project = self._build_project_view(project_item, self._accounts_by_id())
        return [
            self._build_content_calendar_item_view(project, item)
            for item in self.repository.list_content_calendar_items(creator_project_id)
        ]

    def create_content_calendar_item(
        self,
        creator_project_id: int,
        *,
        work_item_id: int | None,
        title: str,
        platform: str | None,
        content_type: str,
        planned_date: date,
        status: str = "planned",
        risk_level: str = "medium",
        approval_required: bool = False,
        linked_app_link_id: int | None = None,
        notes: str | None = None,
    ) -> ContentCalendarItemView | None:
        project_item = self.repository.get_project_by_id(creator_project_id)
        if project_item is None:
            return None
        project = self._build_project_view(project_item, self._accounts_by_id())
        normalized = self._normalize_content_calendar_payload(
            project=project,
            work_item_id=work_item_id,
            title=title,
            platform=platform,
            content_type=content_type,
            planned_date=planned_date,
            status=status,
            risk_level=risk_level,
            approval_required=approval_required,
            linked_app_link_id=linked_app_link_id,
            notes=notes,
        )
        item = self.repository.create_content_calendar_item(
            creator_project_id=creator_project_id,
            **normalized,
        )
        return self._build_content_calendar_item_view(project, item)

    def update_content_calendar_item(
        self,
        item_id: int,
        *,
        work_item_id: int | None | object = _UNSET,
        title: str | None | object = _UNSET,
        platform: str | None | object = _UNSET,
        content_type: str | None | object = _UNSET,
        planned_date: date | None | object = _UNSET,
        status: str | None | object = _UNSET,
        risk_level: str | None | object = _UNSET,
        approval_required: bool | None | object = _UNSET,
        linked_app_link_id: int | None | object = _UNSET,
        notes: str | None | object = _UNSET,
    ) -> ContentCalendarItemView | None:
        item = self.repository.get_content_calendar_item_by_id(item_id)
        if item is None:
            return None
        project_item = self.repository.get_project_by_id(item.creator_project_id)
        if project_item is None:
            return None
        project = self._build_project_view(project_item, self._accounts_by_id())
        normalized = self._normalize_content_calendar_payload(
            project=project,
            work_item_id=item.work_item_id if work_item_id is _UNSET else work_item_id,
            title=item.title if title is _UNSET else title,
            platform=item.platform if platform is _UNSET else platform,
            content_type=item.content_type if content_type is _UNSET else content_type,
            planned_date=item.planned_date if planned_date is _UNSET else planned_date,
            status=item.status if status is _UNSET else status,
            risk_level=item.risk_level if risk_level is _UNSET else risk_level,
            approval_required=item.approval_required if approval_required is _UNSET else approval_required,
            linked_app_link_id=item.linked_app_link_id if linked_app_link_id is _UNSET else linked_app_link_id,
            notes=item.notes if notes is _UNSET else notes,
        )
        item.work_item_id = normalized["work_item_id"]
        item.title = normalized["title"]
        item.platform = normalized["platform"]
        item.content_type = normalized["content_type"]
        item.planned_date = normalized["planned_date"]
        item.status = normalized["status"]
        item.risk_level = normalized["risk_level"]
        item.approval_required = normalized["approval_required"]
        item.linked_app_link_id = normalized["linked_app_link_id"]
        item.notes = normalized["notes"]
        self.repository.save_content_calendar_item(item)
        return self._build_content_calendar_item_view(project, item)

    def generate_content_calendar_preview(
        self,
        creator_project_id: int,
        *,
        cadence: str = "three_times_weekly",
    ) -> dict[str, object] | None:
        project_item = self.repository.get_project_by_id(creator_project_id)
        if project_item is None:
            return None
        project = self._build_project_view(project_item, self._accounts_by_id())
        normalized_cadence = self._normalize_content_calendar_cadence(cadence)
        operating_plan = self._build_operating_plan(project)
        stored_work_items = [self._build_work_item_view(item) for item in self.repository.list_work_items(project.id)]
        preview_source = stored_work_items or [
            CreatorWorkItemView(
                id=None,
                creator_project_id=project.id,
                title=str(draft["title"]),
                work_type=str(draft["work_type"]),
                status="drafted",
                risk_level=str(draft["risk_level"]),
                approval_required=bool(draft["approval_required"]),
                platform=draft.get("platform") if isinstance(draft.get("platform"), str) else None,
                draft_payload=draft.get("draft_payload") if isinstance(draft.get("draft_payload"), dict) else None,
                review_notes=None,
                next_action=str(draft["next_action"]) if draft.get("next_action") is not None else None,
                safety_flags=tuple(str(flag) for flag in draft.get("safety_flags", [])),
                created_at=None,
                updated_at=None,
            )
            for draft in self._build_work_item_drafts(project)
        ]
        preview_items: list[ContentCalendarItemView] = []
        for index, work_item in enumerate(preview_source[: len(self._calendar_offsets_for_cadence(normalized_cadence))]):
            offset_days = self._calendar_offsets_for_cadence(normalized_cadence)[index]
            preferred_role = self._preferred_default_role_for_work_item(project, work_item)
            preferred_link = (
                self._get_project_app_link_by_default_role(project, preferred_role)
                or self._get_project_app_link_by_default_role(project, "project")
                or next(iter(project.project_app_links_summary), None)
            )
            preview_items.append(
                ContentCalendarItemView(
                    id=None,
                    creator_project_id=project.id,
                    work_item_id=work_item.id,
                    work_item_title=work_item.title,
                    title=self._calendar_title_for_work_item(work_item),
                    platform=work_item.platform,
                    content_type=self._content_type_for_work_item(work_item),
                    planned_date=(date.today() + timedelta(days=offset_days)).isoformat(),
                    status=self._calendar_status_for_work_item(work_item),
                    risk_level=work_item.risk_level,
                    approval_required=work_item.approval_required,
                    linked_app_link_id=preferred_link.get("id") if preferred_link is not None else None,
                    linked_app_label=preferred_link.get("integration_label") if preferred_link is not None else None,
                    notes=(
                        f"Cadence: {operating_plan['weekly_cadence']} "
                        f"Next step: {work_item.next_action or 'Review and refine before approval.'}"
                    ),
                    created_at=None,
                    updated_at=None,
                )
            )
        return {
            "creator_project_id": project.id,
            "cadence": normalized_cadence,
            "items": [self._serialize_content_calendar_item_view(item) for item in preview_items],
        }

    def _normalize_project_payload(
        self,
        *,
        name: str,
        project_type: str,
        status: str,
        persona_type: str,
        disclosure_policy: str,
        niche: str | None,
        target_audience: str | None,
        monetization_goal: str | None,
        content_boundaries: str | None,
        safety_boundaries: str | None,
        platforms: list[str],
        linked_account_ids: list[int],
        operating_mode: str,
        approval_policy: str,
        notes: str | None,
    ) -> dict[str, object]:
        normalized_name = self._normalize_required_text(name, "name")
        normalized_project_type = self._normalize_project_type(project_type)
        normalized_status = self._normalize_project_status(status)
        normalized_persona_type = self._normalize_persona_type(persona_type)
        normalized_disclosure_policy = self._normalize_disclosure_policy(disclosure_policy)
        normalized_platforms = self._normalize_platforms(platforms)
        normalized_operating_mode = self._normalize_operating_mode(operating_mode)
        normalized_approval_policy = self._normalize_approval_policy(approval_policy)
        normalized_niche = self._normalize_optional_text(niche)
        normalized_target_audience = self._normalize_optional_text(target_audience)
        normalized_monetization_goal = self._normalize_optional_text(monetization_goal)
        normalized_content_boundaries = self._normalize_optional_text(content_boundaries)
        normalized_notes = self._normalize_optional_text(notes)
        normalized_safety_boundaries = self._build_safety_boundaries(
            project_type=normalized_project_type,
            content_boundaries=normalized_content_boundaries,
            safety_boundaries=safety_boundaries,
        )
        self._ensure_no_impersonation(
            normalized_name,
            normalized_niche,
            normalized_target_audience,
            normalized_notes,
            normalized_content_boundaries,
        )
        self._validate_project_requirements(
            project_type=normalized_project_type,
            persona_type=normalized_persona_type,
            disclosure_policy=normalized_disclosure_policy,
            content_boundaries=normalized_content_boundaries,
        )
        normalized_linked_account_ids = self._normalize_linked_account_ids(linked_account_ids)
        normalized_linked_integrations = self._derive_integrations(
            project_type=normalized_project_type,
            platforms=normalized_platforms,
        )
        return {
            "name": normalized_name,
            "project_type": normalized_project_type,
            "status": normalized_status,
            "persona_type": normalized_persona_type,
            "disclosure_policy": normalized_disclosure_policy,
            "niche": normalized_niche,
            "target_audience": normalized_target_audience,
            "monetization_goal": normalized_monetization_goal,
            "content_boundaries": normalized_content_boundaries,
            "safety_boundaries": normalized_safety_boundaries,
            "platforms_json": normalized_platforms,
            "linked_accounts_json": normalized_linked_account_ids,
            "linked_integrations_json": normalized_linked_integrations,
            "operating_mode": normalized_operating_mode,
            "approval_policy": normalized_approval_policy,
            "notes": normalized_notes,
        }

    def _validate_project_requirements(
        self,
        *,
        project_type: str,
        persona_type: str,
        disclosure_policy: str,
        content_boundaries: str | None,
    ) -> None:
        if project_type in {"synthetic_influencer", "synthetic_model"}:
            if persona_type != "synthetic_original_persona":
                raise ValueError(
                    "Synthetic creator projects must use persona_type `synthetic_original_persona`."
                )
            if not content_boundaries:
                raise ValueError("Synthetic creator projects require content boundaries.")
            if disclosure_policy not in {"synthetic_disclosed", "brand_account", "platform_dependent"}:
                raise ValueError("Synthetic creator projects require a disclosure policy.")
        if project_type == "creator_monetization" and not content_boundaries:
            raise ValueError("Creator monetization projects require content boundaries.")

    def _build_operating_plan(self, project: CreatorProjectView) -> dict[str, object]:
        if project.project_type == "market_lab":
            market_plan = self.profit_os.build_market_research_plan(
                market=self._primary_market(project.platforms),
                strategy_hypothesis=project.niche or "Operator-defined research hypothesis",
                timeframe="7-day review cycle",
                risk_budget=project.monetization_goal or "paper trading only",
                project_id=None,
            )
            return {
                "positioning": f"Research-first Market Lab project for {project.niche or 'experimental strategy validation'}.",
                "content_pillars": [
                    "Hypothesis tracking",
                    "Risk review",
                    "Paper-trading observations",
                    "Post-review learning loop",
                ],
                "first_10_ideas": list(market_plan["data_to_collect"]) + list(market_plan["weekly_review_template"]),
                "weekly_cadence": "Two research reviews and one paper-trading journal review per week.",
                "quality_checklist": list(market_plan["risk_checklist"]) + list(market_plan["entry_checklist"]),
                "required_integrations": list(project.linked_integrations),
                "linked_apps": list(project.project_app_links_summary),
                "account_requirements": self._build_account_requirements(project),
                "risks": [
                    "Research drift without clear invalidation rules.",
                    "Turning paper-trading output into execution pressure.",
                    "Any broker connection remains out of scope in v0.",
                ],
                "next_7_days_plan": market_plan["paper_trading_plan"],
                "approval_gates": self._default_approval_gates(project),
            }

        if project.project_type == "creator_monetization":
            creator_plan = self.profit_os.build_creator_monetization_plan(
                platform=self._primary_platform(project.platforms),
                creator_positioning=project.name,
                audience=project.target_audience or "premium subscribers",
                content_boundaries=project.content_boundaries or "legal, consent-based boundaries",
                monetization_goal=project.monetization_goal or "safe recurring revenue",
                posting_frequency="3x weekly planning cadence",
                project_id=None,
            )
            return {
                "positioning": f"{project.name} as a policy-compliant creator monetization project for {project.target_audience or 'adult subscribers'}.",
                "content_pillars": [
                    "Offer clarity",
                    "Retention-friendly content",
                    "Compliance-safe engagement",
                    "Privacy and workflow discipline",
                ],
                "first_10_ideas": list(creator_plan["content_ideas"][:10]),
                "weekly_cadence": "Three planning blocks per week with manual review before any public action.",
                "quality_checklist": creator_plan["compliance_checklist"] + creator_plan["privacy_checklist"],
                "required_integrations": list(project.linked_integrations),
                "linked_apps": list(project.project_app_links_summary),
                "account_requirements": self._build_account_requirements(project),
                "risks": list(creator_plan["risks"]),
                "next_7_days_plan": list(creator_plan["next_7_days_plan"]),
                "approval_gates": self._default_approval_gates(project),
            }

        if project.project_type in {"synthetic_influencer", "synthetic_model"}:
            synthetic_plan = self.profit_os.build_synthetic_creator_plan(
                primary_platform=self._plan_platform_for_project(project),
                creator_concept=project.name,
                audience=project.target_audience or "adult viewers who opt into premium creator funnels",
                visual_goal=project.niche or "identity-stable premium short-form visuals",
                content_boundaries=project.content_boundaries or "legal, consent-based, non-impersonating boundaries",
                identity_mode="original_persona",
                runtime_preference="windows_native",
                project_id=None,
            )
            quality_checklist = [
                "Identity sheet stays stable before motion work starts.",
                "No real-person likeness or minor-coded styling.",
                "Voice, subtitles, and visual hook are aligned before publish packaging.",
                "Human approval is required before external distribution.",
            ]
            return {
                "positioning": f"{project.name} as a synthetic creator studio with a local-first short-form pipeline and strict approval gates.",
                "content_pillars": list(synthetic_plan["content_tracks"]),
                "first_10_ideas": list(synthetic_plan["launch_sequence"]) + list(synthetic_plan["media_pipeline"][:6]),
                "weekly_cadence": "Two identity and asset review blocks, one teaser batch block, and one approval review block per week.",
                "quality_checklist": quality_checklist,
                "required_integrations": list(project.linked_integrations),
                "linked_apps": list(project.project_app_links_summary),
                "account_requirements": self._build_account_requirements(project),
                "risks": list(synthetic_plan["risks"]),
                "next_7_days_plan": list(synthetic_plan["next_14_days_plan"][:7]),
                "approval_gates": self._default_approval_gates(project),
                "runtime_stack": synthetic_plan["runtime_stack"],
                "media_pipeline": list(synthetic_plan["media_pipeline"]),
                "vram_policy": synthetic_plan["vram_policy"],
                "approval_flow": synthetic_plan["approval_flow"],
                "identity_guardrail": synthetic_plan["identity_guardrail"],
            }

        channel_plan = self.profit_os.build_channel_plan(
            platform=self._plan_platform_for_project(project),
            niche=project.niche or project.name,
            audience=project.target_audience or "target audience to validate",
            monetization_goal=project.monetization_goal or "build audience trust first",
            posting_frequency="4-5 short-form posts per week"
            if project.project_type in {"shorts_channel", "instagram_brand", "synthetic_influencer", "synthetic_model"}
            else "2 long-form videos per week",
            language=None,
            project_id=None,
        )
        risks = list(channel_plan["risks"])
        if project.project_type in {"synthetic_influencer", "synthetic_model"}:
            risks.extend(
                [
                    "Synthetic persona must stay clearly original, not an impersonation.",
                    "No real-person likeness or minor-coded presentation is allowed.",
                ]
            )
        return {
            "positioning": channel_plan["positioning"],
            "content_pillars": list(channel_plan["content_pillars"]),
            "first_10_ideas": list(channel_plan["first_10_video_ideas"]),
            "weekly_cadence": channel_plan["publishing_cadence"],
            "quality_checklist": list(channel_plan["quality_bar"]),
            "required_integrations": list(project.linked_integrations),
            "linked_apps": list(project.project_app_links_summary),
            "account_requirements": self._build_account_requirements(project),
            "risks": risks,
            "next_7_days_plan": list(channel_plan["next_7_days_plan"]),
            "approval_gates": self._default_approval_gates(project),
        }

    def _build_work_item_drafts(self, project: CreatorProjectView) -> list[dict[str, object]]:
        platform = self._primary_platform(project.platforms)
        common_flags = ["no_execution", "approval_gate_required", "no_account_use"]
        drafts: list[dict[str, object]] = [
            {
                "title": f"{project.name}: research brief",
                "work_type": "research",
                "risk_level": "low",
                "approval_required": False,
                "platform": platform,
                "draft_payload": {
                    "focus": project.niche or project.name,
                    "target_audience": project.target_audience,
                },
                "next_action": "Review research notes and choose the strongest angle.",
                "safety_flags": common_flags,
            },
            {
                "title": f"{project.name}: next content idea batch",
                "work_type": "content_idea",
                "risk_level": "low",
                "approval_required": False,
                "platform": platform,
                "draft_payload": {
                    "idea_count": 5,
                    "pillar_source": project.niche or project.name,
                },
                "next_action": "Pick one idea and expand it into a script draft.",
                "safety_flags": common_flags,
            },
            {
                "title": f"{project.name}: script draft",
                "work_type": "script",
                "risk_level": "medium",
                "approval_required": False,
                "platform": platform,
                "draft_payload": {"format": "script-outline"},
                "next_action": "Check clarity, hook strength, and compliance before review.",
                "safety_flags": common_flags,
            },
            {
                "title": f"{project.name}: storyboard and visual beats",
                "work_type": "storyboard",
                "risk_level": "medium",
                "approval_required": False,
                "platform": platform,
                "draft_payload": {"format": "storyboard"},
                "next_action": "Align visuals to the script before asset preparation.",
                "safety_flags": common_flags,
            },
            {
                "title": f"{project.name}: thumbnail or asset brief",
                "work_type": "thumbnail_brief" if project.project_type != "market_lab" else "asset_brief",
                "risk_level": "medium",
                "approval_required": False,
                "platform": platform,
                "draft_payload": {"focus": "clarity and promise alignment"},
                "next_action": "Prepare the asset brief with one clear promise and one contrast element.",
                "safety_flags": common_flags,
            },
            {
                "title": f"{project.name}: quality review",
                "work_type": "quality_review",
                "risk_level": "medium",
                "approval_required": True,
                "platform": platform,
                "draft_payload": {"review_scope": "quality and safety"},
                "next_action": "Mark issues, request fixes, or approve for draft package assembly.",
                "safety_flags": [*common_flags, "manual_review_required"],
            },
            {
                "title": f"{project.name}: publish package preview",
                "work_type": "publish_package",
                "risk_level": "high",
                "approval_required": True,
                "platform": platform,
                "draft_payload": {"scope": "title, description, asset checklist"},
                "next_action": "Keep this as preview only until an explicit confirmed action flow exists.",
                "safety_flags": [*common_flags, "public_action_blocked"],
            },
        ]
        if project.project_type in {"synthetic_influencer", "synthetic_model"}:
            drafts.insert(
                5,
                {
                    "title": f"{project.name}: local synthetic preview",
                    "work_type": "local_preview",
                    "risk_level": "medium",
                    "approval_required": False,
                    "platform": platform,
                    "draft_payload": {
                        "format": "synthetic_creator_preview",
                        "runtime_stack": ["comfyui", "kokoro-local", "wav2lip", "ffmpeg-amf"],
                        "external_publish_blocked": True,
                    },
                    "next_action": "Wygeneruj jeden lokalny talking-head preview przed wiekszym batchem teaserow.",
                    "safety_flags": [
                        "local_runtime_only",
                        "no_external_publish",
                        "approval_gate_required",
                    ],
                },
            )
        if project.project_type == "market_lab":
            drafts = [
                {
                    "title": f"{project.name}: market hypothesis review",
                    "work_type": "research",
                    "risk_level": "low",
                    "approval_required": False,
                    "platform": platform,
                    "draft_payload": {"mode": "research_only"},
                    "next_action": "Collect observations before any conclusion.",
                    "safety_flags": ["paper_trading_only", "no_broker_connection", "no_execution"],
                },
                {
                    "title": f"{project.name}: analytics review",
                    "work_type": "analytics_review",
                    "risk_level": "medium",
                    "approval_required": True,
                    "platform": platform,
                    "draft_payload": {"scope": "journal and outcomes"},
                    "next_action": "Review invalidation rules and no-trade conditions.",
                    "safety_flags": ["paper_trading_only", "no_broker_connection", "manual_review_required"],
                },
                {
                    "title": f"{project.name}: growth experiment log",
                    "work_type": "growth_experiment",
                    "risk_level": "medium",
                    "approval_required": True,
                    "platform": platform,
                    "draft_payload": {"scope": "research iteration"},
                    "next_action": "Promote only data-backed experiments to the next review cycle.",
                    "safety_flags": ["paper_trading_only", "no_execution"],
                },
            ]
        action_preview_suggestion = self._build_action_draft_preview_item(project, platform)
        if action_preview_suggestion is not None:
            drafts.append(action_preview_suggestion)
        return drafts

    def _build_action_draft_preview_item(
        self,
        project: CreatorProjectView,
        platform: str,
    ) -> dict[str, object] | None:
        if project.project_type == "market_lab":
            return None

        recommended_action_type, required_capability = self._recommended_action_preview_for_project(
            project.project_type
        )
        defaults_summary = project.project_app_links_defaults_summary
        publish_default = defaults_summary.get("publish_preview")
        content_default = defaults_summary.get("content")
        project_default = defaults_summary.get("project")
        app_link = next(
            (
                candidate
                for candidate in (publish_default, content_default, project_default)
                if isinstance(candidate, dict)
                and required_capability in candidate.get("allowed_capabilities", [])
            ),
            None,
        )
        if app_link is None:
            app_link = next(
                (
                    entry
                    for entry in project.project_app_links_summary
                    if required_capability in entry.get("allowed_capabilities", [])
                ),
                None,
            )
        if app_link is None and not project.linked_integrations:
            return None
        if app_link is None and not project.linked_account_ids:
            return None
        integration_key = (
            str(app_link["integration_key"])
            if app_link is not None
            else project.linked_integrations[0]
        )
        return {
            "title": f"{project.name}: Action Broker preview suggestion",
            "work_type": "action_draft_preview",
            "risk_level": "high" if recommended_action_type != "export_preview" else "medium",
            "approval_required": True,
            "platform": platform,
            "draft_payload": {
                "recommended_action_type": recommended_action_type,
                "required_capability": required_capability,
                "integration_key": integration_key,
                "linked_account_ids": list(project.linked_account_ids),
                "project_app_link_id": app_link.get("id") if app_link is not None else None,
                "linked_accounts_metadata_only": True,
                "execution_blocked_in_v0": True,
            },
            "next_action": (
                "Create an Action Broker preview only after manual review confirms "
                "the project is ready for a supervised next step."
            ),
            "safety_flags": [
                "no_execution",
                "approval_gate_required",
                "no_account_use",
                "action_broker_preview_only",
            ],
        }

    @staticmethod
    def _recommended_action_preview_for_project(project_type: str) -> tuple[str, str]:
        if project_type in {"self_improvement", "growth_experiment"}:
            return ("export_preview", "draft_write")
        if project_type == "creator_monetization":
            return ("content_upload_preview", "publish_later")
        return ("publish_preview", "publish_later")

    def _default_approval_gates(self, project: CreatorProjectView) -> list[str]:
        gates = [
            "No publish, upload, login, trade, or message action is allowed in v0.",
            "Any future public action requires explicit user approval.",
            "Accounts may be linked as metadata only; secrets are not resolved by this slice.",
        ]
        if project.project_type == "market_lab":
            gates.append("Paper trading first. No broker connection or order execution.")
        if project.project_type in {"synthetic_influencer", "synthetic_model"}:
            gates.append("Synthetic personas must remain original and disclosed according to policy.")
        return gates

    def _build_account_requirements(self, project: CreatorProjectView) -> list[str]:
        if project.linked_account_ids:
            return [
                f"{len(project.linked_account_ids)} linked account metadata entr{'y' if len(project.linked_account_ids) == 1 else 'ies'} registered for future approved actions.",
                f"{len(project.project_app_links_summary)} project app link entr{'y' if len(project.project_app_links_summary) == 1 else 'ies'} available for supervised routing."
                if project.project_app_links_summary
                else "No project app links configured yet for supervised routing.",
                "No linked account is used by this slice.",
            ]
        return [
            f"Prepare account metadata later for: {', '.join(project.linked_integrations) if project.linked_integrations else 'future integrations'}.",
            "Project app links can be configured later without resolving secrets.",
            "Accounts & Secrets can be linked later without resolving secrets here.",
        ]

    def _build_project_view(
        self,
        item: CreatorProject,
        accounts_by_id: dict[int, AccountCredential],
    ) -> CreatorProjectView:
        linked_accounts_summary: list[dict[str, object]] = []
        for account_id in item.linked_accounts_json or []:
            account = accounts_by_id.get(account_id)
            if account is None:
                continue
            linked_accounts_summary.append(
                {
                    "account_id": account.id,
                    "label": account.label,
                    "provider_key": account.provider_key,
                    "account_identifier": account.account_identifier,
                    "status": account.status,
                }
            )
        return CreatorProjectView(
            id=item.id,
            project_id=item.project_id,
            name=item.name,
            project_type=item.project_type,
            status=item.status,
            persona_type=item.persona_type,
            disclosure_policy=item.disclosure_policy,
            niche=item.niche,
            target_audience=item.target_audience,
            monetization_goal=item.monetization_goal,
            content_boundaries=item.content_boundaries,
            safety_boundaries=item.safety_boundaries,
            platforms=tuple(item.platforms_json or []),
            linked_account_ids=tuple(item.linked_accounts_json or []),
            linked_integrations=tuple(item.linked_integrations_json or []),
            operating_mode=item.operating_mode,
            approval_policy=item.approval_policy,
            notes=item.notes,
            strategy_profile=dict(item.strategy_profile_json or {}),
            linked_accounts_summary=tuple(linked_accounts_summary),
            project_app_links_summary=(
                self.project_app_links.summarize_links(item.project_id)
                if self.project_app_links is not None
                else ()
            ),
            project_app_links_defaults_summary=(
                self.project_app_links.summarize_defaults(item.project_id)
                if self.project_app_links is not None
                else {
                    "linked_app_count": 0,
                    "project": None,
                    "content": None,
                    "analytics": None,
                    "storage": None,
                    "publish_preview": None,
                }
            ),
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
        )

    @staticmethod
    def _build_work_item_view(item: CreatorWorkItem) -> CreatorWorkItemView:
        return CreatorWorkItemView(
            id=item.id,
            creator_project_id=item.creator_project_id,
            title=item.title,
            work_type=item.work_type,
            status=item.status,
            risk_level=item.risk_level,
            approval_required=item.approval_required,
            platform=item.platform,
            draft_payload=item.draft_payload_json,
            review_notes=item.review_notes,
            next_action=item.next_action,
            safety_flags=tuple(item.safety_flags_json or []),
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
        )

    @staticmethod
    def _matches_work_item_filters(
        item: CreatorWorkItemView,
        *,
        status: str | None,
        risk_level: str | None,
        work_type: str | None,
        platform: str | None,
        needs_review: bool | None,
        needs_approval: bool | None,
    ) -> bool:
        if status is not None and item.status != status:
            return False
        if risk_level is not None and item.risk_level != risk_level:
            return False
        if work_type is not None and item.work_type != work_type:
            return False
        if platform is not None and (item.platform or "") != platform:
            return False
        if needs_review is not None and (item.status == "needs_review") is not needs_review:
            return False
        if needs_approval is not None and item.approval_required is not needs_approval:
            return False
        return True

    @staticmethod
    def _sort_work_items(items: list[CreatorWorkItemView], sort: str) -> list[CreatorWorkItemView]:
        risk_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        status_rank = {
            "needs_review": 0,
            "blocked": 1,
            "drafted": 2,
            "backlog": 3,
            "approved": 4,
            "done": 5,
        }
        if sort == "risk":
            return sorted(
                items,
                key=lambda item: (
                    risk_rank.get(item.risk_level, 9),
                    -(CreatorOpsService._timestamp_value(item.updated_at) or 0),
                    -(CreatorOpsService._timestamp_value(item.created_at) or 0),
                ),
            )
        if sort == "status":
            return sorted(
                items,
                key=lambda item: (
                    status_rank.get(item.status, 9),
                    risk_rank.get(item.risk_level, 9),
                    -(CreatorOpsService._timestamp_value(item.updated_at) or 0),
                ),
            )
        if sort == "updated_at":
            return sorted(
                items,
                key=lambda item: (
                    -(CreatorOpsService._timestamp_value(item.updated_at) or 0),
                    -(CreatorOpsService._timestamp_value(item.created_at) or 0),
                ),
            )
        if sort == "created_at":
            return sorted(
                items,
                key=lambda item: (
                    -(CreatorOpsService._timestamp_value(item.created_at) or 0),
                    -(CreatorOpsService._timestamp_value(item.updated_at) or 0),
                ),
            )
        return sorted(
            items,
            key=lambda item: (
                0 if item.status == "needs_review" else 1,
                0 if item.approval_required else 1,
                risk_rank.get(item.risk_level, 9),
                -(CreatorOpsService._timestamp_value(item.updated_at) or 0),
                -(CreatorOpsService._timestamp_value(item.created_at) or 0),
            ),
        )

    @staticmethod
    def _timestamp_value(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return __import__("datetime").datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _preferred_default_role_for_work_item(
        project: CreatorProjectView,
        item: CreatorWorkItemView,
    ) -> str:
        if project.project_type == "market_lab" or item.work_type == "analytics_review":
            return "analytics"
        if item.work_type in {"asset_brief", "storyboard"}:
            return "storage"
        if item.work_type in {"publish_package", "action_draft_preview"}:
            return "publish_preview"
        return "content"

    @staticmethod
    def _get_project_app_link_by_default_role(
        project: CreatorProjectView,
        role: str,
    ) -> dict[str, object] | None:
        summary = project.project_app_links_defaults_summary.get(role)
        if not isinstance(summary, dict):
            return None
        for link in project.project_app_links_summary:
            if link.get("id") == summary.get("id"):
                return link
        return None

    @staticmethod
    def _serialize_work_item_view(item: CreatorWorkItemView) -> dict[str, object]:
        return {
            "id": item.id,
            "creator_project_id": item.creator_project_id,
            "title": item.title,
            "work_type": item.work_type,
            "status": item.status,
            "risk_level": item.risk_level,
            "approval_required": item.approval_required,
            "platform": item.platform,
            "draft_payload": item.draft_payload,
            "review_notes": item.review_notes,
            "next_action": item.next_action,
            "safety_flags": list(item.safety_flags),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    @staticmethod
    def _build_preview_work_item_view(
        creator_project_id: int,
        draft: dict[str, object],
    ) -> dict[str, object]:
        return {
            "id": None,
            "creator_project_id": creator_project_id,
            "title": draft["title"],
            "work_type": draft["work_type"],
            "status": "drafted",
            "risk_level": draft["risk_level"],
            "approval_required": draft["approval_required"],
            "platform": draft.get("platform"),
            "draft_payload": draft.get("draft_payload"),
            "review_notes": None,
            "next_action": draft.get("next_action"),
            "safety_flags": list(draft.get("safety_flags", [])),
            "created_at": None,
            "updated_at": None,
        }

    def _normalize_linked_account_ids(self, account_ids: list[int]) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for item in account_ids:
            if item <= 0 or item in seen:
                continue
            account = self.account_repository.get_by_id(item)
            if account is None:
                raise ValueError(f"Linked account {item} does not exist.")
            normalized.append(item)
            seen.add(item)
        return normalized

    @staticmethod
    def _normalize_project_type(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_PROJECT_TYPES:
            raise ValueError("Unsupported creator project type.")
        return normalized

    @staticmethod
    def _normalize_project_status(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_PROJECT_STATUSES:
            raise ValueError("Unsupported creator project status.")
        return normalized

    @staticmethod
    def _normalize_persona_type(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_PERSONA_TYPES:
            raise ValueError("Unsupported persona type.")
        return normalized

    @staticmethod
    def _normalize_disclosure_policy(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_DISCLOSURE_POLICIES:
            raise ValueError("Unsupported disclosure policy.")
        return normalized

    @staticmethod
    def _normalize_operating_mode(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_OPERATING_MODES:
            raise ValueError("Unsupported operating mode.")
        return normalized

    @staticmethod
    def _normalize_approval_policy(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_APPROVAL_POLICIES:
            raise ValueError("Unsupported approval policy.")
        return normalized

    @staticmethod
    def _normalize_work_item_status(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_WORK_STATUSES:
            raise ValueError("Unsupported work item status.")
        return normalized

    @staticmethod
    def _normalize_work_type(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_WORK_TYPES:
            raise ValueError("Unsupported work type.")
        return normalized

    @staticmethod
    def _normalize_risk_level(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_RISK_LEVELS:
            raise ValueError("Unsupported risk level.")
        return normalized

    @staticmethod
    def _normalize_work_item_sort(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_WORK_ITEM_SORTS:
            raise ValueError("Unsupported work item sort.")
        return normalized

    @staticmethod
    def _status_for_review_action(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_REVIEW_ACTIONS:
            raise ValueError("Unsupported review action.")
        return {
            "approve_review": "approved",
            "request_changes": "drafted",
            "block": "blocked",
            "defer": "backlog",
            "mark_done": "done",
        }[normalized]

    @staticmethod
    def _normalize_platforms(platforms: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in platforms:
            value = item.strip().lower()
            if not value or value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return normalized

    @staticmethod
    def _normalize_required_text(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be empty.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_content_calendar_status(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_CONTENT_CALENDAR_STATUSES:
            raise ValueError("Unsupported content calendar status.")
        return normalized

    @staticmethod
    def _normalize_content_calendar_cadence(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_CONTENT_CALENDAR_CADENCES:
            raise ValueError("Unsupported content calendar cadence.")
        return normalized

    @staticmethod
    def _build_safety_boundaries(
        *,
        project_type: str,
        content_boundaries: str | None,
        safety_boundaries: str | None,
    ) -> str:
        base = [
            "No publishing, upload, login, or account action in v0.",
            "No browser/computer use.",
            "No secret resolution.",
        ]
        if project_type in {"synthetic_influencer", "synthetic_model"}:
            base.extend(
                [
                    "No impersonation of real people.",
                    "No real-person likeness without consent.",
                    "No minors.",
                    "No explicit adult content generation in v0.",
                ]
            )
        if project_type == "creator_monetization":
            base.extend(
                [
                    "Legal, adult-only where relevant, consent-based, privacy-safe, platform-compliant.",
                    "No automated fan DMs in v0.",
                ]
            )
        if project_type == "market_lab":
            base.extend(
                [
                    "Research only.",
                    "Paper trading first.",
                    "No broker connection or trade execution.",
                ]
            )
        if content_boundaries:
            base.append(f"Creator boundaries: {content_boundaries}")
        if safety_boundaries and safety_boundaries.strip():
            base.append(safety_boundaries.strip())
        return " ".join(base)

    def _normalize_content_calendar_payload(
        self,
        *,
        project: CreatorProjectView,
        work_item_id: int | None,
        title: str | None,
        platform: str | None,
        content_type: str | None,
        planned_date: date,
        status: str | None,
        risk_level: str | None,
        approval_required: bool,
        linked_app_link_id: int | None,
        notes: str | None,
    ) -> dict[str, object]:
        normalized_status = self._normalize_content_calendar_status(status or "planned")
        normalized_risk = self._normalize_risk_level(risk_level or "medium")
        normalized_title = self._normalize_required_text(title or "", "title")
        normalized_content_type = self._normalize_required_text(content_type or "", "content_type")
        normalized_platform = self._normalize_optional_text(platform)
        normalized_notes = self._normalize_optional_text(notes)
        normalized_work_item_id, derived_work_item = self._resolve_calendar_work_item(project.id, work_item_id)
        normalized_linked_app_link_id, linked_app_label = self._resolve_calendar_project_app_link(
            project,
            linked_app_link_id,
        )
        if normalized_status == "done" and approval_required:
            raise ValueError("Calendar items marked done cannot still require approval.")
        if linked_app_label is not None and normalized_notes is None:
            normalized_notes = (
                f"Planned with {linked_app_label} metadata only. "
                "No publish, upload, or execution path exists here."
            )
        return {
            "work_item_id": normalized_work_item_id,
            "title": normalized_title,
            "platform": normalized_platform or (derived_work_item.platform if derived_work_item is not None else None),
            "content_type": normalized_content_type,
            "planned_date": planned_date,
            "status": normalized_status,
            "risk_level": normalized_risk,
            "approval_required": bool(approval_required),
            "linked_app_link_id": normalized_linked_app_link_id,
            "notes": normalized_notes,
        }

    @staticmethod
    def _ensure_no_impersonation(*fields: str | None) -> None:
        haystack = " ".join(field.lower() for field in fields if field).strip()
        for phrase in IMPERSONATION_PHRASES:
            if phrase in haystack:
                raise ValueError(
                    "Create an original synthetic persona or brand instead. Impersonation and real-person likeness requests are not allowed."
                )

    @staticmethod
    def _derive_integrations(project_type: str, platforms: list[str]) -> list[str]:
        derived: list[str] = []
        for platform in platforms:
            if platform == "youtube_short":
                derived.extend(["youtube", "video-editor", "tts", "analytics-generic"])
            elif platform == "instagram_reel":
                derived.extend(["instagram", "canva", "image-generator", "analytics-generic"])
            elif platform == "youtube":
                derived.extend(["youtube", "video-editor", "canva", "analytics-generic"])
            elif platform == "tiktok":
                derived.extend(["tiktok", "video-editor", "tts", "analytics-generic"])
            else:
                derived.append(platform)
        if project_type in {"synthetic_influencer", "synthetic_model"}:
            derived.extend(
                [
                    "image-generator",
                    "canva",
                    "video-editor",
                    "tts",
                    "ollama-local",
                    "kokoro-local",
                    "comfyui",
                    "pulid-identity",
                    "ltx-video",
                    "wan-video",
                    "wav2lip",
                    "ffmpeg-amf",
                    "slack",
                    "discord-webhook",
                ]
            )
        if project_type == "creator_monetization":
            derived.extend(["onlyfans", "creator-platform-generic", "analytics-generic"])
        if project_type == "market_lab":
            derived.extend(["tradingview", "xtb", "broker-generic", "market-data-generic"])
        if project_type == "self_improvement":
            derived.extend(["obsidian", "github"])
        normalized: list[str] = []
        seen: set[str] = set()
        for item in derived:
            if item not in seen:
                normalized.append(item)
                seen.add(item)
        return normalized

    def _accounts_by_id(self) -> dict[int, AccountCredential]:
        return {item.id: item for item in self.account_repository.list_all()}

    def _resolve_calendar_work_item(
        self,
        creator_project_id: int,
        work_item_id: int | None,
    ) -> tuple[int | None, CreatorWorkItemView | None]:
        if work_item_id is None:
            return None, None
        item = self.repository.get_work_item_by_id(work_item_id)
        if item is None or item.creator_project_id != creator_project_id:
            raise ValueError("Linked work item must belong to the same creator project.")
        view = self._build_work_item_view(item)
        return view.id, view

    def _resolve_calendar_project_app_link(
        self,
        project: CreatorProjectView,
        linked_app_link_id: int | None,
    ) -> tuple[int | None, str | None]:
        if linked_app_link_id is None:
            return None, None
        if project.project_id is None or self.project_app_links is None:
            raise ValueError("This creator project has no canonical project app links yet.")
        link = self.project_app_links.get_link(project.project_id, linked_app_link_id)
        if link is None:
            raise ValueError("Linked app link must belong to the same project.")
        return link.id, link.integration_label

    def _build_content_calendar_item_view(
        self,
        project: CreatorProjectView,
        item: ContentCalendarItem,
    ) -> ContentCalendarItemView:
        linked_work_item = (
            self.repository.get_work_item_by_id(item.work_item_id)
            if item.work_item_id is not None
            else None
        )
        linked_app = (
            self.project_app_links.get_link(project.project_id, item.linked_app_link_id)
            if item.linked_app_link_id is not None
            and project.project_id is not None
            and self.project_app_links is not None
            else None
        )
        return ContentCalendarItemView(
            id=item.id,
            creator_project_id=item.creator_project_id,
            work_item_id=item.work_item_id,
            work_item_title=linked_work_item.title if linked_work_item is not None else None,
            title=item.title,
            platform=item.platform,
            content_type=item.content_type,
            planned_date=item.planned_date.isoformat(),
            status=item.status,
            risk_level=item.risk_level,
            approval_required=item.approval_required,
            linked_app_link_id=item.linked_app_link_id,
            linked_app_label=linked_app.integration_label if linked_app is not None else None,
            notes=item.notes,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
        )

    @staticmethod
    def _serialize_content_calendar_item_view(item: ContentCalendarItemView) -> dict[str, object]:
        return {
            "id": item.id,
            "creator_project_id": item.creator_project_id,
            "work_item_id": item.work_item_id,
            "work_item_title": item.work_item_title,
            "title": item.title,
            "platform": item.platform,
            "content_type": item.content_type,
            "planned_date": item.planned_date,
            "status": item.status,
            "risk_level": item.risk_level,
            "approval_required": item.approval_required,
            "linked_app_link_id": item.linked_app_link_id,
            "linked_app_label": item.linked_app_label,
            "notes": item.notes,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    @staticmethod
    def _calendar_offsets_for_cadence(cadence: str) -> list[int]:
        if cadence == "daily":
            return [1, 2, 3, 4, 5]
        if cadence == "weekly":
            return [1, 8, 15, 22]
        return [1, 3, 5, 8, 10]

    @staticmethod
    def _content_type_for_work_item(item: CreatorWorkItemView) -> str:
        return {
            "research": "research_brief",
            "content_idea": "idea_batch",
            "script": "script",
            "storyboard": "storyboard",
            "thumbnail_brief": "thumbnail_brief",
            "asset_brief": "asset_brief",
            "quality_review": "quality_review",
            "publish_package": "publish_preview_package",
            "analytics_review": "analytics_review",
            "growth_experiment": "growth_experiment",
            "action_draft_preview": "action_preview",
        }.get(item.work_type, "content_item")

    @staticmethod
    def _calendar_title_for_work_item(item: CreatorWorkItemView) -> str:
        return item.title.strip() or f"Planned {item.work_type.replace('_', ' ')}"

    @staticmethod
    def _calendar_status_for_work_item(item: CreatorWorkItemView) -> str:
        return {
            "backlog": "idea",
            "drafted": "drafted",
            "needs_review": "needs_review",
            "approved": "approved",
            "blocked": "blocked",
            "done": "done",
        }.get(item.status, "planned")

    @staticmethod
    def _plan_platform_for_project(project: CreatorProjectView) -> str:
        if project.project_type == "youtube_channel":
            return "youtube"
        if project.project_type in {"shorts_channel", "synthetic_influencer", "synthetic_model"}:
            if "instagram_reel" in project.platforms:
                return "instagram_reel"
            if "tiktok" in project.platforms:
                return "tiktok"
            return "youtube_short"
        if project.project_type == "instagram_brand":
            return "instagram_reel"
        if project.project_type == "growth_experiment":
            if "instagram_reel" in project.platforms:
                return "instagram_reel"
            return "youtube_short"
        return "youtube"

    @staticmethod
    def _primary_platform(platforms: tuple[str, ...]) -> str:
        return platforms[0] if platforms else "youtube"

    @staticmethod
    def _primary_market(platforms: tuple[str, ...]) -> str:
        if "crypto" in platforms:
            return "crypto"
        if "forex" in platforms:
            return "forex"
        return "stocks"
