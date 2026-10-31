from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.decision_log_service import DecisionLogRecall, DecisionLogService
from atnos.services.github_connector_service import GitHubConnectorService
from atnos.services.linear_connector_service import LinearConnectorService
from atnos.services.summary_cards_service import SummaryCardsNotFoundError, SummaryCardsService


BRIEFING_SECTION_ORDER = ("decisions", "github", "linear", "project_memory")


@dataclass(frozen=True, slots=True)
class BriefingItem:
    label: str
    detail: str
    urgency: str
    action_url: str | None
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "detail": self.detail,
            "urgency": self.urgency,
            "action_url": self.action_url,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class BriefingSection:
    kind: str
    title: str
    items: tuple[BriefingItem, ...]
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "title": self.title,
            "items": [item.to_dict() for item in self.items],
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class BriefingPayload:
    generated_at: datetime
    project_id: int | None
    sections: tuple[BriefingSection, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "project_id": self.project_id,
            "sections": [section.to_dict() for section in self.sections],
        }


class BriefingService:
    MAX_SECTION_ITEMS = 5

    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.projects = ProjectRepository(session)
        self.conversations = ConversationRepository(session)
        self.decisions = DecisionLogService(session)
        self.github = GitHubConnectorService(session, settings)
        self.linear = LinearConnectorService(session, settings)
        self.summary_cards = SummaryCardsService(session, settings)

    def generate(
        self,
        *,
        project_id: int | None = None,
        sections: str | Iterable[str] = "all",
    ) -> BriefingPayload:
        resolved_project = self.projects.get(project_id) if project_id is not None else self._resolve_active_project()
        resolved_project_id = resolved_project.id if resolved_project is not None else None
        requested_sections = self._normalize_sections(sections)
        collected: list[BriefingSection] = []

        if "decisions" in requested_sections:
            decisions_section = self._build_decisions_section(project_id=resolved_project_id)
            if decisions_section is not None:
                collected.append(decisions_section)

        if "github" in requested_sections:
            github_section = self._build_github_section()
            if github_section is not None:
                collected.append(github_section)

        if "linear" in requested_sections:
            linear_section = self._build_linear_section()
            if linear_section is not None:
                collected.append(linear_section)

        if "project_memory" in requested_sections:
            memory_section = self._build_project_memory_section(project_id=resolved_project_id)
            if memory_section is not None:
                collected.append(memory_section)

        ordered_sections = tuple(
            section
            for kind in BRIEFING_SECTION_ORDER
            for section in collected
            if section.kind == kind
        )
        return BriefingPayload(
            generated_at=datetime.now(UTC),
            project_id=resolved_project_id,
            sections=ordered_sections,
        )

    def render_message_content(self, payload: BriefingPayload) -> str:
        if not payload.sections:
            return "Brak pilnych sygnalow. Mozesz zaczac od czystej rozmowy."
        lines: list[str] = []
        for section in payload.sections:
            lines.append(f"{section.title}: {section.summary}")
            for item in section.items[:3]:
                lines.append(f"- [{item.urgency.upper()}] {item.label}: {item.detail}")
        return "\n".join(lines)

    def _build_decisions_section(self, *, project_id: int | None) -> BriefingSection | None:
        recall = self.decisions.recall_active(project_id)
        if not recall.entries:
            return None
        items: list[BriefingItem] = []
        overdue_ids = {item.id for item in recall.overdue_entries}
        for entry in recall.entries[: self.MAX_SECTION_ITEMS]:
            urgency = "high" if entry.id in overdue_ids else ("medium" if entry.due_at is not None else "low")
            due_label = f" Termin: {entry.due_at.date().isoformat()}." if entry.due_at is not None else ""
            items.append(
                BriefingItem(
                    label=self._format_decision_label(entry.kind.value),
                    detail=f"{entry.summary}.{due_label}".strip(),
                    urgency=urgency,
                    action_url=None,
                    metadata={
                        "decision_id": entry.id,
                        "kind": entry.kind.value,
                        "conversation_id": entry.conversation_id,
                        "project_id": entry.project_id,
                        "due_at": entry.due_at.isoformat() if entry.due_at is not None else None,
                        "status": entry.status.value,
                    },
                )
            )
        return BriefingSection(
            kind="decisions",
            title="Decyzje i commitments",
            items=tuple(items),
            summary=self._summarize_decisions(recall),
        )

    def _build_github_section(self) -> BriefingSection | None:
        if not self.github.is_connected():
            return None
        requested_review_items = self.github.list_open_prs(filter="requested-review").get("items") or []
        mention_items = self.github.list_mentions(since_hours=24).get("items") or []
        items: list[BriefingItem] = []

        for entry in list(requested_review_items)[: self.MAX_SECTION_ITEMS]:
            if not isinstance(entry, dict):
                continue
            repo_name = self._optional_text(entry.get("repository_full_name")) or "GitHub"
            title = self._optional_text(entry.get("title")) or "PR czeka na review"
            items.append(
                BriefingItem(
                    label=f"Requested review - {repo_name}",
                    detail=title,
                    urgency="high",
                    action_url=self._optional_text(entry.get("html_url")),
                    metadata={"source": "requested_review"},
                )
            )

        remaining_slots = max(self.MAX_SECTION_ITEMS - len(items), 0)
        for entry in list(mention_items)[:remaining_slots]:
            if not isinstance(entry, dict):
                continue
            repo_name = self._optional_text(entry.get("repository_full_name")) or "GitHub"
            title = self._optional_text(entry.get("title")) or "Nowa wzmianka"
            items.append(
                BriefingItem(
                    label=f"Wzmianka - {repo_name}",
                    detail=title,
                    urgency="medium",
                    action_url=self._optional_text(entry.get("subject_url")),
                    metadata={"source": "mention"},
                )
            )

        if not items:
            return None

        summary_parts: list[str] = []
        if isinstance(requested_review_items, list) and requested_review_items:
            summary_parts.append(f"{len(requested_review_items)} PR-ow czeka na review")
        if isinstance(mention_items, list) and mention_items:
            summary_parts.append(f"{len(mention_items)} wzmianek z ostatniej doby")
        summary = ", ".join(summary_parts) or "GitHub nie wymaga dzis uwagi."
        return BriefingSection(
            kind="github",
            title="GitHub",
            items=tuple(items[: self.MAX_SECTION_ITEMS]),
            summary=summary,
        )

    def _build_linear_section(self) -> BriefingSection | None:
        if not self.linear.is_connected():
            return None
        overdue = self.linear.list_overdue_tasks()
        today_due = self.linear.list_today_due()
        sprint = self.linear.get_active_sprint_summary()

        items: list[BriefingItem] = []
        for task in overdue[: self.MAX_SECTION_ITEMS]:
            items.append(
                BriefingItem(
                    label=f"{task.identifier} - overdue",
                    detail=task.title,
                    urgency="high",
                    action_url=task.url,
                    metadata=task.to_dict(),
                )
            )
        remaining_slots = max(self.MAX_SECTION_ITEMS - len(items), 0)
        for task in today_due[:remaining_slots]:
            items.append(
                BriefingItem(
                    label=f"{task.identifier} - na dzis",
                    detail=task.title,
                    urgency="medium",
                    action_url=task.url,
                    metadata=task.to_dict(),
                )
            )
        if sprint is not None and sprint.total_tasks > 0 and len(items) < self.MAX_SECTION_ITEMS:
            items.append(
                BriefingItem(
                    label=sprint.cycle_name or "Aktywny sprint",
                    detail=(
                        f"{sprint.completed_tasks}/{sprint.total_tasks} zamknietych, "
                        f"{sprint.open_tasks} otwartych."
                    ),
                    urgency="low",
                    action_url=None,
                    metadata=sprint.to_dict(),
                )
            )

        if not items:
            return None

        summary_parts: list[str] = []
        if overdue:
            summary_parts.append(f"{len(overdue)} zaleglych taskow")
        if today_due:
            summary_parts.append(f"{len(today_due)} taskow na dzis")
        if sprint is not None and sprint.total_tasks > 0:
            summary_parts.append(f"sprint {sprint.cycle_name or 'aktywny'} trwa")
        summary = ", ".join(summary_parts) or "Linear nie ma dzis nic pilnego."
        return BriefingSection(
            kind="linear",
            title="Linear",
            items=tuple(items[: self.MAX_SECTION_ITEMS]),
            summary=summary,
        )

    def _build_project_memory_section(self, *, project_id: int | None) -> BriefingSection | None:
        if project_id is None:
            return None
        conversations = self.conversations.list_by_project(project_id)
        if not conversations:
            return None
        latest_conversation = conversations[0]
        try:
            summary_cards = self.summary_cards.get_for_conversation(latest_conversation.id)
        except SummaryCardsNotFoundError:
            return None

        items: list[BriefingItem] = []
        if summary_cards.project is not None and summary_cards.project.has_project_summary:
            items.append(
                BriefingItem(
                    label="Pamiec projektu",
                    detail=summary_cards.project.project_summary or "Projekt ma zapisane summary.",
                    urgency="medium",
                    action_url=None,
                    metadata={
                        "project_id": summary_cards.project.project_id,
                        "source": "project_summary",
                    },
                )
            )
        if summary_cards.conversation.has_conversation_summary:
            items.append(
                BriefingItem(
                    label="Ostatnia rozmowa",
                    detail=summary_cards.conversation.conversation_summary or latest_conversation.title,
                    urgency="low",
                    action_url=None,
                    metadata={
                        "conversation_id": summary_cards.conversation.conversation_id,
                        "source": "conversation_summary",
                    },
                )
            )
        if summary_cards.obsidian_memory is not None:
            items.append(
                BriefingItem(
                    label="Pamiec Obsidian",
                    detail=summary_cards.obsidian_memory.helper_text,
                    urgency="low",
                    action_url=None,
                    metadata={
                        "source": "obsidian_memory",
                        "vault_name": summary_cards.obsidian_memory.vault_name,
                        "ready_for_confirm": summary_cards.obsidian_memory.ready_for_confirm,
                    },
                )
            )

        if not items:
            return None

        return BriefingSection(
            kind="project_memory",
            title="Pamiec projektu",
            items=tuple(items[: self.MAX_SECTION_ITEMS]),
            summary="Projekt ma follow-upy i zapisany kontekst do szybkiego wejscia w prace.",
        )

    @staticmethod
    def _normalize_sections(sections: str | Iterable[str]) -> set[str]:
        if isinstance(sections, str):
            normalized = {item.strip().lower() for item in sections.split(",") if item.strip()}
        else:
            normalized = {str(item).strip().lower() for item in sections if str(item).strip()}
        if not normalized or "all" in normalized:
            return set(BRIEFING_SECTION_ORDER)
        return normalized

    def _resolve_active_project(self):
        projects = self.projects.list_all()
        return projects[0] if projects else None

    @staticmethod
    def _summarize_decisions(recall: DecisionLogRecall) -> str:
        if recall.overdue_entries:
            return (
                f"{len(recall.overdue_entries)} zaleglych commitments, "
                f"{len(recall.entries)} aktywnych wpisow lacznie."
            )
        with_due = [item for item in recall.entries if item.due_at is not None]
        if with_due:
            return f"{len(with_due)} commitments ma termin, {len(recall.entries)} wpisow jest nadal aktywnych."
        return f"{len(recall.entries)} aktywnych decyzji i commitments."

    @staticmethod
    def _format_decision_label(kind: str) -> str:
        if kind == "commitment":
            return "Commitment"
        if kind == "decision":
            return "Decyzja"
        if kind == "open_question":
            return "Otwarte pytanie"
        if kind == "pending_action":
            return "Pending action"
        return "Wpis"

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None
