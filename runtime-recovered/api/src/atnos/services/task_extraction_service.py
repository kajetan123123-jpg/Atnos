from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from atnos.services.context_builder_service import ProviderVisibleAttachment
from atnos.services.document_action_service import DocumentActionResult


@dataclass(slots=True, frozen=True)
class DraftTask:
    title: str
    kind: str
    source: str
    confidence: str
    requires_confirmation: bool

    def to_dict(self) -> dict:
        return asdict(self)


class TaskExtractionService:
    MESSAGE_ACTION_KEYWORDS = ("zrob", "zrób", "przygotuj", "napisz", "stworz", "stwórz", "dodaj", "popraw")
    RESEARCH_KEYWORDS = ("sprawdz", "sprawdź", "research", "znajdz", "znajdź")
    CODE_KEYWORDS = ("kod", "test", "testy", "endpoint", "component", "komponent", "api")
    REMINDER_KEYWORDS = ("przypomnij", "remind", "termin", "deadline")
    CHECKLIST_HINTS = ("checklist", "checklista", "todo", "zadania", "task", "tasks")
    ATTACHMENT_TASK_LINE_RE = re.compile(r"^(?:[-*]|\d+[.)]|\[[ xX]\])\s+(.+)$")

    def extract(
        self,
        *,
        content: str,
        document_action: DocumentActionResult | None,
        visible_attachments: tuple[ProviderVisibleAttachment, ...],
    ) -> tuple[DraftTask, ...]:
        tasks: list[DraftTask] = []
        tasks.extend(self._tasks_from_message(content))

        readable_attachments = tuple(
            attachment
            for attachment in visible_attachments
            if attachment.extracted_text is not None and attachment.extracted_text.strip()
        )
        tasks.extend(
            self._tasks_from_document_action(
                document_action=document_action,
                visible_attachments=readable_attachments,
            )
        )
        tasks.extend(self._tasks_from_attachment_preview(readable_attachments))

        unique: list[DraftTask] = []
        seen_titles: set[str] = set()
        for task in tasks:
            normalized = task.title.casefold()
            if normalized in seen_titles:
                continue
            seen_titles.add(normalized)
            unique.append(task)
            if len(unique) >= 3:
                break
        return tuple(unique)

    def _tasks_from_message(self, content: str) -> list[DraftTask]:
        normalized = " ".join(content.lower().split())
        if not normalized:
            return []

        has_task_signal = any(keyword in normalized for keyword in self.MESSAGE_ACTION_KEYWORDS) or any(
            keyword in normalized for keyword in self.RESEARCH_KEYWORDS
        )
        if not has_task_signal:
            return []

        kind = "follow_up"
        confidence = "medium"
        if any(keyword in normalized for keyword in self.RESEARCH_KEYWORDS):
            kind = "research"
            confidence = "high"
        elif any(keyword in normalized for keyword in self.CODE_KEYWORDS):
            kind = "code"
            confidence = "high"
        elif any(keyword in normalized for keyword in self.REMINDER_KEYWORDS):
            kind = "reminder"
        elif any(keyword in normalized for keyword in self.CHECKLIST_HINTS):
            kind = "document"

        title = self._title_from_text(content)
        if title is None:
            return []

        return [
            DraftTask(
                title=title,
                kind=kind,
                source="message_keyword",
                confidence=confidence,
                requires_confirmation=True,
            )
        ]

    def _tasks_from_document_action(
        self,
        *,
        document_action: DocumentActionResult | None,
        visible_attachments: tuple[ProviderVisibleAttachment, ...],
    ) -> list[DraftTask]:
        if document_action is None or not visible_attachments:
            return []

        first_attachment = visible_attachments[0]
        if document_action.action == "extract_checklist":
            extracted = self._extract_task_lines(first_attachment.extracted_text or "")
            if extracted:
                return [
                    DraftTask(
                        title=title,
                        kind="document",
                        source="document_action",
                        confidence="medium",
                        requires_confirmation=True,
                    )
                    for title in extracted[:2]
                ]
            return [
                DraftTask(
                    title=f"Wyciagnac checklistę z pliku {first_attachment.filename}",
                    kind="document",
                    source="document_action",
                    confidence="medium",
                    requires_confirmation=True,
                )
            ]

        if document_action.action == "make_action_plan":
            return [
                DraftTask(
                    title=f"Przygotowac plan dzialania na podstawie pliku {first_attachment.filename}",
                    kind="follow_up",
                    source="document_action",
                    confidence="medium",
                    requires_confirmation=True,
                )
            ]

        return []

    def _tasks_from_attachment_preview(
        self,
        visible_attachments: tuple[ProviderVisibleAttachment, ...],
    ) -> list[DraftTask]:
        if not visible_attachments:
            return []

        extracted = self._extract_task_lines(visible_attachments[0].extracted_text or "")
        return [
            DraftTask(
                title=title,
                kind="document",
                source="attachment_preview",
                confidence="low",
                requires_confirmation=True,
            )
            for title in extracted[:1]
        ]

    def _extract_task_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = self.ATTACHMENT_TASK_LINE_RE.match(line)
            candidate = match.group(1).strip() if match else None
            if candidate is None and any(hint in line.lower() for hint in self.CHECKLIST_HINTS):
                candidate = line
            if candidate is None:
                continue
            normalized = self._normalize_task_title(candidate)
            if normalized:
                lines.append(normalized)
        return lines

    @staticmethod
    def _normalize_task_title(value: str) -> str | None:
        normalized = " ".join(value.strip().split())
        normalized = normalized.rstrip(".:;,")
        return normalized or None

    def _title_from_text(self, content: str) -> str | None:
        first_sentence = re.split(r"[.!?\n]", content, maxsplit=1)[0]
        return self._normalize_task_title(first_sentence)
