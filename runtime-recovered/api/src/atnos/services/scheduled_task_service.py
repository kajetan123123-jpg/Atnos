from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
import unicodedata
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.scheduled_task_repository import ScheduledTaskRepository
from atnos.repositories.user_repository import UserRepository


POLISH_WEEKDAYS = {
    "poniedzialek": 1,
    "wtorek": 2,
    "sroda": 3,
    "czwartek": 4,
    "piatek": 5,
    "sobota": 6,
    "niedziela": 0,
}


class ScheduledTaskError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScheduledTaskPreview:
    cron_spec: str
    human_label: str
    prompt_template: str
    schedule_text: str

    def to_dict(self) -> dict[str, str]:
        return {
            "cron_spec": self.cron_spec,
            "human_label": self.human_label,
            "prompt_template": self.prompt_template,
            "schedule_text": self.schedule_text,
        }


class ScheduledTaskService:
    MAX_ACTIVE_TASKS_PER_USER = 50

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.tasks = ScheduledTaskRepository(session)
        self.projects = ProjectRepository(session)
        self.users = UserRepository(session)

    def parse_natural_schedule(
        self,
        *,
        request_text: str,
        prompt_template: str | None = None,
    ) -> ScheduledTaskPreview:
        normalized = self._normalize(request_text)
        if not normalized:
            raise ScheduledTaskError("Brakuje treści zadania cyklicznego.")

        weekly_match = re.match(
            r"^kazdy\s+(poniedzialek|wtorek|sroda|czwartek|piatek|sobota|niedziela)\s+o\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$",
            normalized,
        )
        if weekly_match:
            weekday, hour_text, minute_text, remainder = weekly_match.groups()
            hour = self._parse_hour(hour_text)
            minute = self._parse_minute(minute_text)
            cron_spec = f"{minute} {hour} * * {POLISH_WEEKDAYS[weekday]}"
            prompt = self._normalize_prompt_template(prompt_template or remainder)
            return ScheduledTaskPreview(
                cron_spec=cron_spec,
                human_label=f"Każdy {weekday} {hour:02d}:{minute:02d}",
                prompt_template=prompt,
                schedule_text=request_text.strip(),
            )

        daily_match = re.match(
            r"^codziennie\s+o\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$",
            normalized,
        )
        if daily_match:
            hour_text, minute_text, remainder = daily_match.groups()
            hour = self._parse_hour(hour_text)
            minute = self._parse_minute(minute_text)
            cron_spec = f"{minute} {hour} * * *"
            prompt = self._normalize_prompt_template(prompt_template or remainder)
            return ScheduledTaskPreview(
                cron_spec=cron_spec,
                human_label=f"Codziennie {hour:02d}:{minute:02d}",
                prompt_template=prompt,
                schedule_text=request_text.strip(),
            )

        raise ScheduledTaskError(
            "Nie udało się jednoznacznie sparsować harmonogramu. Użyj formatu np. 'każdy poniedziałek o 9 zrób przegląd projektu'."
        )

    def create_task(
        self,
        *,
        conversation_id: int | None,
        project_id: int | None,
        user_id: int | None,
        prompt_template: str,
        cron_spec: str,
    ):
        owner = self._resolve_user_id(user_id)
        self._ensure_active_task_budget(owner)
        normalized_prompt = self._normalize_prompt_template(prompt_template)
        normalized_cron = self.validate_cron_spec(cron_spec)
        item = self.tasks.create(
            conversation_id=conversation_id,
            project_id=project_id,
            user_id=owner,
            prompt_template=normalized_prompt,
            cron_spec=normalized_cron,
            next_run_at=None,
            last_run_at=None,
            last_run_status=None,
            is_active=True,
        )
        return item

    def list_tasks(self, *, project_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.tasks.list_for_project(project_id) if project_id is not None else self.tasks.list_all()
        return [self.serialize_task(row) for row in rows]

    def set_active(self, task_id: int, *, is_active: bool):
        item = self._require_task(task_id)
        item.is_active = bool(is_active)
        self.tasks.save(item)
        return item

    def delete_task(self, task_id: int) -> None:
        item = self._require_task(task_id)
        self.tasks.delete(item)

    def update_runtime_state(
        self,
        task_id: int,
        *,
        next_run_at: datetime | None,
        last_run_at: datetime | None = None,
        last_run_status: str | None = None,
    ):
        item = self._require_task(task_id)
        item.next_run_at = next_run_at
        if last_run_at is not None:
            item.last_run_at = last_run_at
        if last_run_status is not None:
            item.last_run_status = last_run_status
        self.tasks.save(item)
        return item

    def validate_cron_spec(self, cron_spec: str) -> str:
        normalized = " ".join((cron_spec or "").split())
        if not normalized:
            raise ScheduledTaskError("cron_spec jest wymagany.")
        parts = normalized.split(" ")
        if len(parts) != 5:
            raise ScheduledTaskError("cron_spec musi mieć dokładnie 5 pól.")
        minute = parts[0]
        if minute == "*":
            raise ScheduledTaskError("Minimalny interwał dla zadań cyklicznych to 1 minuta.")
        try:
            CronTrigger.from_crontab(normalized, timezone="Europe/Warsaw")
        except Exception as exc:
            raise ScheduledTaskError(f"Niepoprawny cron_spec: {normalized}") from exc
        return normalized

    def serialize_task(self, item) -> dict[str, Any]:
        project = self.projects.get(item.project_id) if item.project_id is not None else None
        return {
            "scheduled_task_id": item.id,
            "conversation_id": item.conversation_id,
            "project_id": item.project_id,
            "project_name": project.name if project is not None else None,
            "user_id": item.user_id,
            "prompt_template": item.prompt_template,
            "cron_spec": item.cron_spec,
            "next_run_at": item.next_run_at.isoformat() if item.next_run_at is not None else None,
            "last_run_at": item.last_run_at.isoformat() if item.last_run_at is not None else None,
            "last_run_status": item.last_run_status,
            "is_active": item.is_active,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    def _ensure_active_task_budget(self, user_id: int) -> None:
        active_count = len(self.tasks.list_active_for_user(user_id))
        if active_count >= self.MAX_ACTIVE_TASKS_PER_USER:
            raise ScheduledTaskError(
                f"Przekroczono limit {self.MAX_ACTIVE_TASKS_PER_USER} aktywnych zadań cyklicznych dla użytkownika."
            )

    def _resolve_user_id(self, user_id: int | None) -> int:
        if user_id is not None:
            user = self.users.get(user_id)
            if user is None:
                raise ScheduledTaskError("user_id nie istnieje.")
            return user.id
        owner = self.users.get_owner()
        if owner is None:
            raise ScheduledTaskError("Brak użytkownika owner dla zadań cyklicznych.")
        return owner.id

    def _require_task(self, task_id: int):
        item = self.tasks.get_by_id(task_id)
        if item is None:
            raise ScheduledTaskError("Scheduled task nie istnieje.")
        return item

    @staticmethod
    def _normalize(value: str) -> str:
        collapsed = " ".join((value or "").strip().split())
        ascii_value = unicodedata.normalize("NFKD", collapsed).encode("ascii", "ignore").decode("ascii")
        return ascii_value.lower()

    @staticmethod
    def _normalize_prompt_template(value: str) -> str:
        normalized = " ".join((value or "").strip().split())
        if not normalized:
            raise ScheduledTaskError("prompt_template nie może być pusty.")
        return normalized

    @staticmethod
    def _parse_hour(value: str) -> int:
        hour = int(value)
        if hour < 0 or hour > 23:
            raise ScheduledTaskError("Godzina musi mieścić się w zakresie 0-23.")
        return hour

    @staticmethod
    def _parse_minute(value: str | None) -> int:
        minute = 0 if value in (None, "") else int(value)
        if minute < 0 or minute > 59:
            raise ScheduledTaskError("Minuta musi mieścić się w zakresie 0-59.")
        return minute

    @staticmethod
    def next_run_from_trigger(cron_spec: str, *, now: datetime | None = None) -> datetime | None:
        trigger = CronTrigger.from_crontab(cron_spec, timezone="Europe/Warsaw")
        base = now or datetime.now(UTC)
        return trigger.get_next_fire_time(None, base)
