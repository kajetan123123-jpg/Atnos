from __future__ import annotations

"""Idempotentna migracja dawnych list kont do wiązań projekt-platforma.

Migrator nie dotyka vaulta ani rekordów OAuth. Tworzy wyłącznie brakujące,
jednoznaczne ``ProjectAppLink`` i pozostawia stare pola CreatorProject jako
tymczasowe źródło kompatybilności do czasu ręcznego wycofania legacy.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.account_credential import AccountCredential
from atnos.db.models.creator_project import CreatorProject
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.creator_project_repository import CreatorProjectRepository
from atnos.repositories.project_app_link_repository import ProjectAppLinkRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.integrations_registry import IntegrationsRegistryService
from atnos.services.project_app_links_service import (
    ALLOWED_APP_LINK_CAPABILITIES,
    ProjectAppLinksService,
)


CONTENT_PROJECT_TYPES = frozenset({"shorts_channel", "youtube_channel", "creator_channel", "synthetic_influencer", "synthetic_model"})


@dataclass(frozen=True, slots=True)
class LegacyBindingMigrationItem:
    creator_project_id: int | None
    project_id: int | None
    integration_key: str | None
    account_id: int | None
    status: str
    reason: str
    link_id: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "creator_project_id": self.creator_project_id,
            "project_id": self.project_id,
            "integration_key": self.integration_key,
            "account_id": self.account_id,
            "status": self.status,
            "reason": self.reason,
            "link_id": self.link_id,
        }


class LegacyPlatformBindingMigrationService:
    """Zmienia tylko bezpieczne, potwierdzone relacje legacy na ProjectAppLink."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.creator_projects = CreatorProjectRepository(session)
        self.projects = ProjectRepository(session)
        self.accounts = AccountCredentialRepository(session)
        self.links = ProjectAppLinkRepository(session)
        self.app_links = ProjectAppLinksService(session, settings)
        self.integration_keys = {
            item.key for item in IntegrationsRegistryService(session, settings).list_integrations()
        }

    def migrate(self, *, apply: bool = False, project_ids: tuple[int, ...] = ()) -> dict[str, object]:
        """Zwraca plan lub zapisuje wyłącznie relacje jednoznaczne i odwracalne."""
        requested = {item for item in project_ids if item > 0}
        creator_projects = self.creator_projects.list_projects()
        canonical_project_ids = {
            item.project_id for item in creator_projects if item.project_id is not None
        }
        outcomes: list[LegacyBindingMigrationItem] = []
        for creator_project in creator_projects:
            if requested and creator_project.project_id not in requested:
                continue
            outcomes.extend(self._migrate_creator_project(creator_project, apply=apply))

        # Some early content projects predate CreatorProject. Their only durable
        # source is AccountCredential.linked_project_id, so migrate that relation
        # when (and only when) one integration maps to one account.
        for project in self.projects.list_all():
            if project.id in canonical_project_ids:
                continue
            if requested and project.id not in requested:
                continue
            outcomes.extend(self._migrate_historical_project(project.id, apply=apply))

        counts = {status: sum(item.status == status for item in outcomes) for status in ("planned", "created", "existing", "skipped", "failed")}
        return {
            "mode": "apply" if apply else "dry_run",
            "migration_safe": True,
            "mutates_credentials": False,
            "legacy_fields_retained": True,
            "summary": {
                "creator_projects_considered": len(
                    [item for item in creator_projects if not requested or item.project_id in requested]
                ),
                "historical_projects_considered": len(
                    [
                        item
                        for item in self.projects.list_all()
                        if item.id not in canonical_project_ids and (not requested or item.id in requested)
                    ]
                ),
                **counts,
            },
            "items": [item.to_dict() for item in outcomes],
        }

    def _migrate_creator_project(
        self,
        creator_project: CreatorProject,
        *,
        apply: bool,
    ) -> list[LegacyBindingMigrationItem]:
        if creator_project.project_id is None:
            return [
                LegacyBindingMigrationItem(
                    creator_project_id=creator_project.id,
                    project_id=None,
                    integration_key=None,
                    account_id=None,
                    status="skipped",
                    reason="creator_project_has_no_canonical_project",
                )
            ]

        integrations = self._normalized_integrations(creator_project.linked_integrations_json)
        account_ids = self._candidate_account_ids(creator_project)
        if not integrations:
            return [
                LegacyBindingMigrationItem(
                    creator_project_id=creator_project.id,
                    project_id=creator_project.project_id,
                    integration_key=None,
                    account_id=None,
                    status="skipped",
                    reason="legacy_project_has_no_declared_integrations",
                )
            ]

        outcomes: list[LegacyBindingMigrationItem] = []
        for account_id in account_ids:
            account = self.accounts.get_by_id(account_id)
            if account is None:
                outcomes.append(
                    LegacyBindingMigrationItem(
                        creator_project_id=creator_project.id,
                        project_id=creator_project.project_id,
                        integration_key=None,
                        account_id=account_id,
                        status="skipped",
                        reason="legacy_account_record_missing",
                    )
                )
                continue
            integration_key = self._matching_integration(account, integrations)
            if integration_key is None:
                outcomes.append(
                    LegacyBindingMigrationItem(
                        creator_project_id=creator_project.id,
                        project_id=creator_project.project_id,
                        integration_key=None,
                        account_id=account.id,
                        status="skipped",
                        reason="account_integration_unverified",
                    )
                )
                continue
            outcomes.append(
                self._migrate_account(
                    project_id=creator_project.project_id,
                    creator_project_id=creator_project.id,
                    project_type=creator_project.project_type,
                    account=account,
                    integration_key=integration_key,
                    apply=apply,
                )
            )

        if not account_ids:
            for integration_key in integrations:
                outcomes.append(
                    LegacyBindingMigrationItem(
                        creator_project_id=creator_project.id,
                        project_id=creator_project.project_id,
                        integration_key=integration_key,
                        account_id=None,
                        status="skipped",
                        reason="integration_has_no_legacy_account",
                    )
                )
        return outcomes

    def _migrate_historical_project(
        self,
        project_id: int,
        *,
        apply: bool,
    ) -> list[LegacyBindingMigrationItem]:
        """Migrates pre-CreatorProject records without inferring channel identity."""
        candidates: dict[str, list[AccountCredential]] = {}
        for account in self.accounts.list_all():
            if account.linked_project_id != project_id:
                continue
            integration_key = (account.linked_integration_key or "").strip().lower()
            if integration_key and integration_key in self.integration_keys:
                candidates.setdefault(integration_key, []).append(account)

        if not candidates:
            return [
                LegacyBindingMigrationItem(
                    creator_project_id=None,
                    project_id=project_id,
                    integration_key=None,
                    account_id=None,
                    status="skipped",
                    reason="historical_project_has_no_confirmed_account",
                )
            ]

        outcomes: list[LegacyBindingMigrationItem] = []
        for integration_key, accounts in sorted(candidates.items()):
            configured_accounts = [account for account in accounts if account.status == "configured"]
            if len(accounts) == 1:
                selected = accounts[0]
                ignored: list[AccountCredential] = []
            elif len(configured_accounts) == 1:
                # A historical metadata record is not a usable account binding.
                # Selecting the only configured account is deterministic, while
                # retaining every ignored record in the evidence report.
                selected = configured_accounts[0]
                ignored = [account for account in accounts if account.id != selected.id]
            else:
                outcomes.extend(
                    LegacyBindingMigrationItem(
                        creator_project_id=None,
                        project_id=project_id,
                        integration_key=integration_key,
                        account_id=account.id,
                        status="skipped",
                        reason="ambiguous_multiple_accounts_for_integration",
                    )
                    for account in accounts
                )
                continue
            outcomes.append(
                self._migrate_account(
                    project_id=project_id,
                    creator_project_id=None,
                    project_type=None,
                    account=selected,
                    integration_key=integration_key,
                    apply=apply,
                )
            )
            outcomes.extend(
                LegacyBindingMigrationItem(
                    creator_project_id=None,
                    project_id=project_id,
                    integration_key=integration_key,
                    account_id=account.id,
                    status="skipped",
                    reason="metadata_only_duplicate_preserved",
                )
                for account in ignored
            )
        return outcomes

    def _migrate_account(
        self,
        *,
        project_id: int,
        creator_project_id: int | None,
        project_type: str | None,
        account: AccountCredential,
        integration_key: str,
        apply: bool,
    ) -> LegacyBindingMigrationItem:
        existing = next(
            (
                item
                for item in self.links.list_for_project(project_id)
                if item.integration_key == integration_key and item.account_id == account.id
            ),
            None,
        )
        if existing is not None:
            return LegacyBindingMigrationItem(
                creator_project_id=creator_project_id,
                project_id=project_id,
                integration_key=integration_key,
                account_id=account.id,
                status="existing",
                reason="binding_already_exists",
                link_id=existing.id,
            )

        capabilities = self._supported_capabilities(account)
        if not apply:
            return LegacyBindingMigrationItem(
                creator_project_id=creator_project_id,
                project_id=project_id,
                integration_key=integration_key,
                account_id=account.id,
                status="planned",
                reason="confirmed_account_integration_match",
            )
        try:
            created = self.app_links.create_link(
                project_id,
                integration_key=integration_key,
                custom_integration_id=None,
                account_id=account.id,
                label=f"Migrated {integration_key} account: {account.label}",
                purpose=(
                    "Migrated from the legacy creator project account list."
                    if creator_project_id is not None
                    else "Migrated from the historical project account relation."
                ),
                # CreatorProject carries the typed policy needed for an active
                # default. Generic historical projects remain reviewable until a
                # user confirms the intended project role and platform resource.
                status=(
                    "configured"
                    if creator_project_id is not None and account.status == "configured"
                    else "needs_review"
                ),
                permission_profile=self._permission_profile(capabilities),
                allowed_capabilities=capabilities,
                safety_notes="Account data stays at its existing secure reference; this migration stores no sensitive value.",
                is_default_for_project=(
                    creator_project_id is not None and not self._has_default(project_id, "project")
                ),
                is_default_for_content=(
                    project_type in CONTENT_PROJECT_TYPES
                    and not self._has_default(project_id, "content")
                    and bool({"read_content", "draft_write", "publish_later"}.intersection(capabilities))
                ),
                is_default_for_analytics=(
                    creator_project_id is not None
                    and not self._has_default(project_id, "analytics")
                    and bool({"read_metadata", "read_content", "financial_read_later"}.intersection(capabilities))
                ),
                is_default_for_storage=False,
                is_default_for_publish_preview=(
                    project_type in CONTENT_PROJECT_TYPES
                    and "publish_later" in capabilities
                    and not self._has_default(project_id, "publish_preview")
                ),
                notes=(
                    f"legacy_creator_project_id={creator_project_id}"
                    if creator_project_id is not None
                    else "legacy_project_account_relation"
                ),
                # Running the migration with apply=True is the explicit operator
                # decision required before a historical account may be reused.
                allow_shared_account=True,
            )
        except ValueError as exc:
            return LegacyBindingMigrationItem(
                creator_project_id=creator_project_id,
                project_id=project_id,
                integration_key=integration_key,
                account_id=account.id,
                status="failed",
                reason=str(exc),
            )
        return LegacyBindingMigrationItem(
            creator_project_id=creator_project_id,
            project_id=project_id,
            integration_key=integration_key,
            account_id=account.id,
            status="created",
            reason="created_without_copying_credentials",
            link_id=created.id,
        )

    def _matching_integration(self, account: AccountCredential, integrations: tuple[str, ...]) -> str | None:
        linked_key = (account.linked_integration_key or "").strip().lower()
        if linked_key in integrations and linked_key in self.integration_keys:
            return linked_key
        return None

    @staticmethod
    def _normalized_integrations(values: Any) -> tuple[str, ...]:
        if not isinstance(values, list):
            return ()
        return tuple(dict.fromkeys(item.strip().lower() for item in values if isinstance(item, str) and item.strip()))

    @staticmethod
    def _normalized_account_ids(values: Any) -> tuple[int, ...]:
        if not isinstance(values, list):
            return ()
        return tuple(dict.fromkeys(item for item in values if isinstance(item, int) and item > 0))

    def _candidate_account_ids(self, creator_project: CreatorProject) -> tuple[int, ...]:
        """Łączy dawną listę z trwałym, jednoznacznym FK po stronie konta."""
        declared = self._normalized_account_ids(creator_project.linked_accounts_json)
        if creator_project.project_id is None:
            return declared
        reverse_linked = tuple(
            account.id
            for account in self.accounts.list_all()
            if account.linked_project_id == creator_project.project_id
        )
        return tuple(dict.fromkeys((*declared, *reverse_linked)))

    @staticmethod
    def _supported_capabilities(account: AccountCredential) -> list[str]:
        return [
            item
            for item in (account.allowed_capabilities_json or [])
            if isinstance(item, str) and item in ALLOWED_APP_LINK_CAPABILITIES
        ]

    @staticmethod
    def _permission_profile(capabilities: list[str]) -> str:
        if "trading_later" in capabilities:
            return "trading_preview_later"
        if "financial_read_later" in capabilities:
            return "financial_read_later"
        if "publish_later" in capabilities:
            return "publish_preview_later"
        if "draft_write" in capabilities:
            return "draft_write"
        if capabilities:
            return "read_only"
        return "metadata_only"

    def _has_default(self, project_id: int, role: str) -> bool:
        field_name = f"is_default_for_{role}"
        return any(bool(getattr(item, field_name, False)) for item in self.links.list_for_project(project_id))
