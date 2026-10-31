from __future__ import annotations

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.providers.registry import ProviderRegistry
from atnos.services.account_action_broker_service import (
    AccountActionBrokerService,
    AccountActionDraftNotFoundError,
    AccountActionDraftTransitionError,
)
from atnos.services.gmail_connector_service import GmailConnectorService
from atnos.services.github_connector_service import GitHubConnectorService
from atnos.services.google_calendar_connector_service import GoogleCalendarConnectorService
from atnos.services.self_coding_draft_service import SelfCodingDraftService
from atnos.services.youtube_connector_service import YouTubeConnectorService


class ActionDraftExecutionError(ValueError):
    pass


class ActionDraftExecutionService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.broker = AccountActionBrokerService(session, settings)
        self.youtube = YouTubeConnectorService(session, settings)
        self.calendar = GoogleCalendarConnectorService(session, settings)
        self.gmail = GmailConnectorService(session, settings)
        self.github = GitHubConnectorService(session, settings)

    def execute(self, *, draft_id: int, confirm: bool = False) -> dict[str, object]:
        draft = self.broker.get_action_draft(draft_id)
        if draft is None:
            raise AccountActionDraftNotFoundError(f"Action draft {draft_id} was not found.")
        if draft.status != "approved":
            raise AccountActionDraftTransitionError(
                f"Action draft {draft_id} must be approved before execution."
            )
        payload = draft.preview_payload
        if draft.action_type == "code_apply_preview":
            return SelfCodingDraftService(
                self.session,
                settings=self.settings,
                provider_registry=self.provider_registry,
            ).execute_code_apply(draft_id=draft_id)
        if not isinstance(payload, dict):
            raise ActionDraftExecutionError("Action draft payload is invalid.")

        result: dict[str, object]
        if draft.integration_key == "youtube":
            result = self._execute_youtube(payload)
        elif draft.integration_key == "google-calendar":
            result = self._execute_calendar(payload)
        elif draft.integration_key == "gmail":
            result = self._execute_gmail(payload, confirm=confirm)
        elif draft.integration_key == "github":
            result = self._execute_github(payload)
        else:
            raise ActionDraftExecutionError(
                f"Integration '{draft.integration_key}' does not have an execution adapter."
            )
        self.broker.mark_executed(draft_id, actor="atnos", detail=result)
        return result

    def _execute_youtube(self, payload: dict[str, object]) -> dict[str, object]:
        if str(payload.get("action") or "") != "post_comment":
            raise ActionDraftExecutionError("Unsupported YouTube action preview.")
        video_id = str(payload.get("video_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not video_id or not text:
            raise ActionDraftExecutionError("YouTube comment preview is missing video_id or text.")
        return self.youtube.post_comment(video_id=video_id, text=text)

    def _execute_calendar(self, payload: dict[str, object]) -> dict[str, object]:
        action = str(payload.get("action") or "").strip()
        if action == "create_event":
            event_payload = {key: value for key, value in payload.items() if key not in {"platform", "action"}}
            return self.calendar.create_event(event_payload)
        if action == "delete_event":
            event_id = str(payload.get("event_id") or "").strip()
            if not event_id:
                raise ActionDraftExecutionError("Calendar delete preview is missing event_id.")
            return self.calendar.delete_event(event_id)
        raise ActionDraftExecutionError("Unsupported Google Calendar action preview.")

    def _execute_gmail(self, payload: dict[str, object], *, confirm: bool) -> dict[str, object]:
        if str(payload.get("action") or "") != "send_message":
            raise ActionDraftExecutionError("Unsupported Gmail action preview.")
        approval_level = str(payload.get("approval_level") or "single").strip().lower()
        if approval_level == "double" and not confirm:
            raise ActionDraftExecutionError(
                "Gmail send requires explicit second confirmation before execution."
            )
        draft_id = str(payload.get("draft_id") or "").strip()
        if not draft_id:
            raise ActionDraftExecutionError("Gmail send preview is missing draft_id.")
        return self.gmail.send_message(draft_id=draft_id)

    def _execute_github(self, payload: dict[str, object]) -> dict[str, object]:
        action = str(payload.get("action") or "").strip()
        owner = str(payload.get("owner") or "").strip()
        repo = str(payload.get("repo") or "").strip()
        if not owner or not repo:
            raise ActionDraftExecutionError("GitHub preview is missing owner or repo.")
        if action == "create_issue":
            title = str(payload.get("title") or "").strip()
            body = str(payload.get("body") or "").strip()
            if not title:
                raise ActionDraftExecutionError("GitHub issue preview is missing title.")
            return self.github.create_issue(owner=owner, repo=repo, title=title, body=body)
        if action == "create_pull_request":
            title = str(payload.get("title") or "").strip()
            body = str(payload.get("body") or "").strip()
            head = str(payload.get("head") or "").strip()
            base = str(payload.get("base") or "").strip()
            if not title or not head or not base:
                raise ActionDraftExecutionError(
                    "GitHub pull request preview is missing title, head or base."
                )
            return self.github.create_pull_request(
                owner=owner,
                repo=repo,
                title=title,
                body=body,
                head=head,
                base=base,
            )
        raise ActionDraftExecutionError("Unsupported GitHub action preview.")
