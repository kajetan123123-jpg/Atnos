from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from numbers import Real

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.account_credentials_service import AccountCredentialsService


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


@dataclass(frozen=True, slots=True)
class LinearTaskItem:
    task_id: str
    identifier: str
    title: str
    state: str
    state_type: str
    due_date: date | None
    priority: int | None
    url: str | None
    team_name: str | None
    cycle_id: str | None
    cycle_name: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "identifier": self.identifier,
            "title": self.title,
            "state": self.state,
            "state_type": self.state_type,
            "due_date": self.due_date.isoformat() if self.due_date is not None else None,
            "priority": self.priority,
            "url": self.url,
            "team_name": self.team_name,
            "cycle_id": self.cycle_id,
            "cycle_name": self.cycle_name,
        }


@dataclass(frozen=True, slots=True)
class LinearSprintSummary:
    cycle_id: str | None
    cycle_name: str | None
    total_tasks: int
    completed_tasks: int
    open_tasks: int

    def to_dict(self) -> dict[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "cycle_name": self.cycle_name,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "open_tasks": self.open_tasks,
        }


class LinearConnectorService:
    PROVIDER_KEY = "linear"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.accounts = AccountCredentialsService(session, settings)

    def is_connected(self) -> bool:
        return self._resolve_api_key() is not None

    def list_my_tasks(self, *, state: str = "open") -> list[LinearTaskItem]:
        nodes = self._viewer_assigned_issue_nodes()
        tasks = [self._coerce_task(node) for node in nodes]
        filtered = [item for item in tasks if item is not None]
        normalized_state = str(state or "open").strip().lower()
        if normalized_state == "in_progress":
            return [item for item in filtered if item.state_type == "started"]
        if normalized_state == "open":
            return [item for item in filtered if item.state_type not in {"completed", "canceled"}]
        return filtered

    def list_overdue_tasks(self) -> list[LinearTaskItem]:
        today = self._utc_today()
        return [
            item
            for item in self.list_my_tasks(state="open")
            if item.due_date is not None and item.due_date < today
        ]

    def list_today_due(self) -> list[LinearTaskItem]:
        today = self._utc_today()
        return [item for item in self.list_my_tasks(state="open") if item.due_date == today]

    def get_active_sprint_summary(self) -> LinearSprintSummary | None:
        payload = self._graphql(
            """
            query ActiveLinearCycle {
              cycles(first: 1, filter: { isActive: { eq: true } }) {
                nodes {
                  id
                  name
                }
              }
              viewer {
                assignedIssues(first: 100) {
                  nodes {
                    state {
                      type
                    }
                    cycle {
                      id
                    }
                  }
                }
              }
            }
            """
        )
        cycles = self._extract_nodes(payload, "cycles")
        if not cycles:
            return None
        active_cycle = cycles[0]
        cycle_id = self._optional_text(active_cycle.get("id"))
        cycle_name = self._optional_text(active_cycle.get("name"))
        issue_nodes = self._extract_nodes(payload.get("viewer"), "assignedIssues")
        total_tasks = 0
        completed_tasks = 0
        for node in issue_nodes:
            cycle = node.get("cycle") if isinstance(node.get("cycle"), dict) else {}
            if self._optional_text((cycle or {}).get("id")) != cycle_id:
                continue
            total_tasks += 1
            state = node.get("state") if isinstance(node.get("state"), dict) else {}
            if self._optional_text((state or {}).get("type")) == "completed":
                completed_tasks += 1
        return LinearSprintSummary(
            cycle_id=cycle_id,
            cycle_name=cycle_name,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            open_tasks=max(total_tasks - completed_tasks, 0),
        )

    def _viewer_assigned_issue_nodes(self) -> list[dict[str, object]]:
        payload = self._graphql(
            """
            query ViewerAssignedIssues {
              viewer {
                assignedIssues(first: 100) {
                  nodes {
                    id
                    identifier
                    title
                    url
                    dueDate
                    priority
                    state {
                      name
                      type
                    }
                    team {
                      name
                    }
                    cycle {
                      id
                      name
                    }
                  }
                }
              }
            }
            """
        )
        viewer = payload.get("viewer")
        return self._extract_nodes(viewer, "assignedIssues")

    def _graphql(
        self,
        query: str,
        variables: dict[str, object] | None = None,
    ) -> dict[str, object]:
        api_key = self._resolve_api_key()
        if api_key is None:
            raise ValueError("Linear account is not configured.")
        response = httpx.post(
            LINEAR_GRAPHQL_URL,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables or {}},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            message = "; ".join(
                str(item.get("message") or "Linear GraphQL error")
                for item in errors
                if isinstance(item, dict)
            )
            raise ValueError(message or "Linear GraphQL error.")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _resolve_api_key(self) -> str | None:
        for account in self.accounts.list_accounts():
            if account.status != "configured":
                continue
            if account.provider_key != self.PROVIDER_KEY and account.linked_integration_key != self.PROVIDER_KEY:
                continue
            return self.accounts.resolve_secret_for_adapter(account.id)
        return None

    @classmethod
    def _extract_nodes(
        cls,
        payload: object,
        key: str,
    ) -> list[dict[str, object]]:
        if not isinstance(payload, dict):
            return []
        parent = payload.get(key)
        if not isinstance(parent, dict):
            return []
        nodes = parent.get("nodes")
        if not isinstance(nodes, list):
            return []
        return [item for item in nodes if isinstance(item, dict)]

    @classmethod
    def _coerce_task(cls, payload: dict[str, object]) -> LinearTaskItem | None:
        task_id = cls._optional_text(payload.get("id"))
        identifier = cls._optional_text(payload.get("identifier"))
        title = cls._optional_text(payload.get("title"))
        if task_id is None or identifier is None or title is None:
            return None
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
        cycle = payload.get("cycle") if isinstance(payload.get("cycle"), dict) else {}
        due_date_raw = cls._optional_text(payload.get("dueDate"))
        due_date: date | None = None
        if due_date_raw:
            try:
                due_date = date.fromisoformat(due_date_raw)
            except ValueError:
                due_date = None
        priority = payload.get("priority")
        return LinearTaskItem(
            task_id=task_id,
            identifier=identifier,
            title=title,
            state=cls._optional_text((state or {}).get("name")) or "Unknown",
            state_type=cls._optional_text((state or {}).get("type")) or "unknown",
            due_date=due_date,
            priority=int(priority) if isinstance(priority, Real) else None,
            url=cls._optional_text(payload.get("url")),
            team_name=cls._optional_text((team or {}).get("name")),
            cycle_id=cls._optional_text((cycle or {}).get("id")),
            cycle_name=cls._optional_text((cycle or {}).get("name")),
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @staticmethod
    def _utc_today() -> date:
        return datetime.now(UTC).date()
