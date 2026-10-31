from __future__ import annotations

from dataclasses import dataclass

from atnos.db.models.enums import AttachmentKind
from atnos.services.conversation_service import AttachmentDraft


@dataclass(slots=True, frozen=True)
class DocumentActionResult:
    action: str
    source: str
    reason: str | None


class DocumentActionService:
    ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "summarize_document",
            ("streszcz", "podsumuj", "summary", "summarize"),
        ),
        (
            "extract_checklist",
            ("checklista", "checklist", "todo", "zadania", "action items"),
        ),
        (
            "extract_risks_decisions",
            ("ryzyka", "decyzje", "założenia", "zalozenia", "risks", "decisions", "assumptions"),
        ),
        (
            "make_action_plan",
            ("plan działania", "plan dzialania", "kolejne kroki", "roadmap", "next steps"),
        ),
    )

    def detect(
        self,
        *,
        content: str,
        attachments: tuple[AttachmentDraft, ...],
    ) -> DocumentActionResult | None:
        if not self._has_document_attachments(attachments):
            return None

        normalized = " ".join(content.lower().split())
        for action, keywords in self.ACTION_KEYWORDS:
            if any(keyword in normalized for keyword in keywords):
                return DocumentActionResult(
                    action=action,
                    source="keyword",
                    reason=f"Matched document action keywords for '{action}'.",
                )

        if not normalized:
            return DocumentActionResult(
                action="summarize_document",
                source="attachment_default",
                reason="Attachment-only document turn defaults to a summary workflow.",
            )

        return None

    @staticmethod
    def _has_document_attachments(attachments: tuple[AttachmentDraft, ...]) -> bool:
        attachment_kinds = {attachment.kind for attachment in attachments}
        return bool(attachment_kinds.intersection({AttachmentKind.DOCUMENT, AttachmentKind.TEXT}))
