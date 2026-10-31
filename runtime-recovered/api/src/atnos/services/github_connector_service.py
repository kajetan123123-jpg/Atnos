from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.oauth_connection_runtime_service import OAuthConnectionRuntimeService
from atnos.services.oauth_provider_registry import OAuthProviderDescriptor, OAuthProviderRegistry


@dataclass(frozen=True, slots=True)
class GitHubRepositorySummary:
    name: str
    owner: str
    private: bool
    default_branch: str | None
    html_url: str | None


class GitHubConnectorService:
    PRIMARY_CONNECTOR_KEY = "github"
    FALLBACK_CONNECTOR_KEY = "github-readonly"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.registry = OAuthProviderRegistry(settings)
        self.connections = OAuthConnectionRepository(session)
        self.runtime = OAuthConnectionRuntimeService(session, settings)
        self.action_broker = AccountActionBrokerService(session, settings)

    def is_connected(self) -> bool:
        try:
            self._authorized_headers()
            return True
        except ValueError:
            return False

    def list_user_repos(self) -> dict[str, object]:
        payload = self._github_get("https://api.github.com/user/repos", params={"per_page": 100, "sort": "updated"})
        items: list[dict[str, object]] = []
        for repo in payload:
            if not isinstance(repo, dict):
                continue
            owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
            items.append(
                {
                    "name": str(repo.get("name") or "").strip(),
                    "owner": str((owner or {}).get("login") or "").strip(),
                    "private": bool(repo.get("private")),
                    "default_branch": str(repo.get("default_branch") or "").strip() or None,
                    "html_url": str(repo.get("html_url") or "").strip() or None,
                }
            )
        return {"status": "ready", "items": items}

    def list_open_prs(self, *, filter: str = "requested-review") -> dict[str, object]:
        normalized_filter = str(filter or "requested-review").strip().lower()
        if normalized_filter == "requested-review":
            query = "is:open is:pr review-requested:@me"
        elif normalized_filter == "mentioned":
            query = "is:open is:pr mentions:@me"
        elif normalized_filter == "created":
            query = "is:open is:pr author:@me"
        else:
            raise ValueError(f"Unsupported GitHub PR filter '{filter}'.")
        payload = self._github_get(
            "https://api.github.com/search/issues",
            params={
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": 20,
            },
        )
        items_raw = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items_raw, list):
            items_raw = []
        items: list[dict[str, object]] = []
        for entry in items_raw:
            if not isinstance(entry, dict):
                continue
            repository_url = self._optional_text(entry.get("repository_url"))
            owner = None
            repo = None
            if repository_url:
                parts = repository_url.rstrip("/").split("/")
                if len(parts) >= 2:
                    owner = parts[-2]
                    repo = parts[-1]
            items.append(
                {
                    "number": entry.get("number"),
                    "title": self._optional_text(entry.get("title")),
                    "html_url": self._optional_text(entry.get("html_url")),
                    "updated_at": self._optional_text(entry.get("updated_at")),
                    "repository_full_name": f"{owner}/{repo}" if owner and repo else None,
                    "author": self._optional_text(
                        (entry.get("user") or {}).get("login")
                        if isinstance(entry.get("user"), dict)
                        else None
                    ),
                }
            )
        return {"status": "ready", "items": items}

    def list_mentions(self, *, since_hours: int = 24) -> dict[str, object]:
        since = (self._utcnow() - timedelta(hours=max(1, since_hours))).isoformat().replace("+00:00", "Z")
        payload = self._github_get(
            "https://api.github.com/notifications",
            params={
                "all": "false",
                "participating": "false",
                "since": since,
                "per_page": 50,
            },
        )
        items_raw = payload if isinstance(payload, list) else []
        items: list[dict[str, object]] = []
        for entry in items_raw:
            if not isinstance(entry, dict):
                continue
            if self._optional_text(entry.get("reason")) != "mention":
                continue
            subject = entry.get("subject") if isinstance(entry.get("subject"), dict) else {}
            repository = entry.get("repository") if isinstance(entry.get("repository"), dict) else {}
            repository_full_name = self._optional_text((repository or {}).get("full_name"))
            subject_type = self._optional_text((subject or {}).get("type"))
            subject_url = self._optional_text((subject or {}).get("url"))
            items.append(
                {
                    "reason": "mention",
                    "title": self._optional_text((subject or {}).get("title")),
                    "type": subject_type,
                    "updated_at": self._optional_text(entry.get("updated_at")),
                    "repository_full_name": repository_full_name,
                    "subject_url": self._subject_api_url_to_html_url(
                        repository_full_name=repository_full_name,
                        subject_type=subject_type,
                        subject_url=subject_url,
                    ),
                }
            )
        return {"status": "ready", "items": items}

    def list_repo_issues(self, *, owner: str, repo: str, state: str = "open") -> dict[str, object]:
        payload = self._github_get(
            f"https://api.github.com/repos/{owner.strip()}/{repo.strip()}/issues",
            params={"state": state.strip() or "open", "per_page": 100},
        )
        items: list[dict[str, object]] = []
        for issue in payload:
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue
            items.append(
                {
                    "number": issue.get("number"),
                    "title": str(issue.get("title") or "").strip() or None,
                    "state": str(issue.get("state") or "").strip() or None,
                    "html_url": str(issue.get("html_url") or "").strip() or None,
                }
            )
        return {"status": "ready", "items": items}

    def read_file_content(self, *, owner: str, repo: str, path: str, ref: str = "main") -> dict[str, object]:
        payload = self._github_get(
            f"https://api.github.com/repos/{owner.strip()}/{repo.strip()}/contents/{path.strip()}",
            params={"ref": ref.strip() or "main"},
        )
        encoded = str(payload.get("content") or "")
        content = None
        if encoded:
            content = base64.b64decode(encoded.encode("utf-8")).decode("utf-8", errors="replace")
        return {
            "status": "ready",
            "path": str(payload.get("path") or path.strip()),
            "sha": str(payload.get("sha") or "").strip() or None,
            "encoding": str(payload.get("encoding") or "").strip() or None,
            "content": content,
        }

    def create_issue_preview(
        self,
        *,
        account_id: int,
        project_id: int | None,
        owner: str,
        repo: str,
        title: str,
        body: str,
    ):
        return self.action_broker.create_action_draft(
            account_id=account_id,
            integration_key="github",
            project_id=project_id,
            action_type="github_write_preview",
            action_title="Create GitHub issue",
            action_summary="Creating a GitHub issue requires supervised approval.",
            preview_payload={
                "platform": "github",
                "action": "create_issue",
                "owner": owner.strip(),
                "repo": repo.strip(),
                "title": title.strip(),
                "body": body.strip(),
                "approval_level": "single",
            },
            approval_notes="GitHub write actions remain approval-gated in v1.",
            created_by="atnos",
        )

    def create_pull_request_preview(
        self,
        *,
        account_id: int,
        project_id: int | None,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ):
        return self.action_broker.create_action_draft(
            account_id=account_id,
            integration_key="github",
            project_id=project_id,
            action_type="github_write_preview",
            action_title="Create GitHub pull request",
            action_summary="Creating a GitHub pull request requires supervised approval.",
            preview_payload={
                "platform": "github",
                "action": "create_pull_request",
                "owner": owner.strip(),
                "repo": repo.strip(),
                "title": title.strip(),
                "body": body.strip(),
                "head": head.strip(),
                "base": base.strip(),
                "approval_level": "single",
            },
            approval_notes="GitHub write actions remain approval-gated in v1.",
            created_by="atnos",
        )

    def create_issue(self, *, owner: str, repo: str, title: str, body: str) -> dict[str, object]:
        payload = self._github_post(
            f"https://api.github.com/repos/{owner.strip()}/{repo.strip()}/issues",
            json={"title": title.strip(), "body": body.strip()},
        )
        return {
            "status": "created",
            "number": payload.get("number"),
            "html_url": str(payload.get("html_url") or "").strip() or None,
            "title": str(payload.get("title") or "").strip() or None,
        }

    def create_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, object]:
        payload = self._github_post(
            f"https://api.github.com/repos/{owner.strip()}/{repo.strip()}/pulls",
            json={
                "title": title.strip(),
                "body": body.strip(),
                "head": head.strip(),
                "base": base.strip(),
            },
        )
        return {
            "status": "created",
            "number": payload.get("number"),
            "html_url": str(payload.get("html_url") or "").strip() or None,
            "title": str(payload.get("title") or "").strip() or None,
        }

    def _resolve_descriptor_and_connection(self) -> tuple[OAuthProviderDescriptor, OAuthConnection | None]:
        primary = self.registry.get_connector(self.PRIMARY_CONNECTOR_KEY)
        primary_connection = self.connections.get_latest_for_connector(self.PRIMARY_CONNECTOR_KEY)
        if primary_connection is not None or primary.is_configured:
            return primary, primary_connection
        fallback = self.registry.get_connector(self.FALLBACK_CONNECTOR_KEY)
        fallback_connection = self.connections.get_latest_for_connector(self.FALLBACK_CONNECTOR_KEY)
        return fallback, fallback_connection

    def _authorized_headers(self) -> dict[str, str]:
        descriptor, connection = self._resolve_descriptor_and_connection()
        if connection is None or connection.status != "connected":
            raise ValueError("Connect GitHub before using this action.")
        token_bundle = self.runtime.load_token_bundle(connection, descriptor=descriptor, refresh_if_needed=True)
        if token_bundle is None or not token_bundle.access_token:
            raise ValueError("Connected GitHub token is unavailable.")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token_bundle.access_token}",
            "User-Agent": f"Atnos/{self.settings.app_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _github_get(self, url: str, *, params: dict[str, object]) -> list[dict[str, object]] | dict[str, object]:
        response = httpx.get(url, headers=self._authorized_headers(), params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return payload if isinstance(payload, dict) else {}

    def _github_post(self, url: str, *, json: dict[str, object]) -> dict[str, object]:
        response = httpx.post(url, headers=self._authorized_headers(), json=json, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _subject_api_url_to_html_url(
        *,
        repository_full_name: str | None,
        subject_type: str | None,
        subject_url: str | None,
    ) -> str | None:
        if not repository_full_name or not subject_url:
            return None
        segments = [segment for segment in subject_url.rstrip("/").split("/") if segment]
        if not segments:
            return None
        trailing_id = segments[-1]
        if subject_type == "PullRequest" and trailing_id.isdigit():
            return f"https://github.com/{repository_full_name}/pull/{trailing_id}"
        if subject_type == "Issue" and trailing_id.isdigit():
            return f"https://github.com/{repository_full_name}/issues/{trailing_id}"
        return None

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None
