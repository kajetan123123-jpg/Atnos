from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any

from docx import Document
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from pptx import Presentation
from pptx.util import Inches
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import AttachmentKind, AttachmentSource, AttachmentStatus
from atnos.db.models.enums import PluginPermissionPolicy
from atnos.plugins.base import PluginPermission
from atnos.plugins.registry import PluginRegistry, build_default_plugin_registry
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.plugin_repository import PluginRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.output_artifact_service import (
    ManagedArtifactDescriptor,
    ManagedArtifactPathError,
    OutputArtifactService,
)
from atnos.services.scheduled_task_service import ScheduledTaskPreview, ScheduledTaskService


CAPABILITY_ACTIONS = {
    "generate_docx_document",
    "generate_pptx_deck",
    "generate_xlsx_workbook",
    "generate_pdf_document",
    "extract_pdf_content",
    "schedule_recurring_task",
}

DOCUMENT_GENERATION_ACTIONS = {
    "generate_docx_document",
    "generate_pptx_deck",
    "generate_xlsx_workbook",
    "generate_pdf_document",
}


@dataclass(frozen=True, slots=True)
class CapabilityPluginRequirement:
    plugin_key: str
    permission: PluginPermission
    description: str


CAPABILITY_PLUGIN_REQUIREMENTS: dict[str, tuple[CapabilityPluginRequirement, ...]] = {
    "generate_docx_document": (
        CapabilityPluginRequirement("document-tools", PluginPermission.WRITE_FILES, "document generation"),
        CapabilityPluginRequirement("local-files", PluginPermission.WRITE_FILES, "controlled local output"),
    ),
    "generate_pptx_deck": (
        CapabilityPluginRequirement("document-tools", PluginPermission.WRITE_FILES, "document generation"),
        CapabilityPluginRequirement("local-files", PluginPermission.WRITE_FILES, "controlled local output"),
    ),
    "generate_xlsx_workbook": (
        CapabilityPluginRequirement("document-tools", PluginPermission.WRITE_FILES, "document generation"),
        CapabilityPluginRequirement("local-files", PluginPermission.WRITE_FILES, "controlled local output"),
    ),
    "generate_pdf_document": (
        CapabilityPluginRequirement("document-tools", PluginPermission.WRITE_FILES, "document generation"),
        CapabilityPluginRequirement("local-files", PluginPermission.WRITE_FILES, "controlled local output"),
    ),
    "extract_pdf_content": (
        CapabilityPluginRequirement("document-tools", PluginPermission.READ_FILES, "PDF extraction"),
        CapabilityPluginRequirement("local-files", PluginPermission.READ_FILES, "controlled local file access"),
    ),
}


class CapabilityActionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityPreview:
    action: str
    title: str
    summary: str
    payload: dict[str, Any]
    requires_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "title": self.title,
            "summary": self.summary,
            "payload": self.payload,
            "requires_confirmation": self.requires_confirmation,
        }


class CapabilityActionService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.plugin_registry = plugin_registry or build_default_plugin_registry()
        self.users = UserRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.plugins = PluginRepository(session)
        self.attachments = AttachmentRepository(session)
        self.artifacts = OutputArtifactService(settings)
        self.scheduled_tasks = ScheduledTaskService(session, settings)

    def preview(
        self,
        *,
        action: str,
        conversation_id: int | None,
        project_id: int | None,
        payload: dict[str, Any],
    ) -> CapabilityPreview:
        normalized_action = self._normalize_action(action)
        self._ensure_plugin_requirements(normalized_action)
        if normalized_action == "generate_docx_document":
            normalized_payload = self._normalize_docx_payload(payload)
            return CapabilityPreview(
                action=normalized_action,
                title=normalized_payload["title"],
                summary=f"Wygenerować dokument Word z {len(normalized_payload['sections'])} sekcjami.",
                payload=normalized_payload,
            )
        if normalized_action == "generate_pptx_deck":
            normalized_payload = self._normalize_pptx_payload(payload)
            return CapabilityPreview(
                action=normalized_action,
                title=normalized_payload["title"],
                summary=f"Wygenerować prezentację PowerPoint z {len(normalized_payload['slides'])} slajdami.",
                payload=normalized_payload,
            )
        if normalized_action == "generate_xlsx_workbook":
            normalized_payload = self._normalize_xlsx_payload(payload)
            return CapabilityPreview(
                action=normalized_action,
                title="Workbook Excel",
                summary=f"Wygenerować arkusz Excel z {len(normalized_payload['sheets'])} zakładkami.",
                payload=normalized_payload,
            )
        if normalized_action == "generate_pdf_document":
            normalized_payload = self._normalize_pdf_payload(payload)
            return CapabilityPreview(
                action=normalized_action,
                title=normalized_payload["title"],
                summary=f"Wygenerować raport PDF z {len(normalized_payload['sections'])} sekcjami.",
                payload=normalized_payload,
            )
        if normalized_action == "extract_pdf_content":
            storage_path = self._normalize_storage_path(payload.get("storage_path"))
            return CapabilityPreview(
                action=normalized_action,
                title="Ekstrakcja PDF",
                summary="Odczytać tekst z lokalnego PDF bez OCR.",
                payload={"storage_path": storage_path},
                requires_confirmation=False,
            )
        schedule_preview = self._preview_schedule(payload=payload)
        return CapabilityPreview(
            action=normalized_action,
            title="Zaplanowane zadanie",
            summary=f"Utworzyć zadanie cykliczne: {schedule_preview.human_label}.",
            payload={
                "conversation_id": conversation_id,
                "project_id": project_id,
                **schedule_preview.to_dict(),
            },
        )

    def execute(
        self,
        *,
        action: str,
        conversation_id: int | None,
        project_id: int | None,
        payload: dict[str, Any],
        enforce_local_write: bool = True,
    ) -> dict[str, Any]:
        normalized_action = self._normalize_action(action)
        self._ensure_plugin_requirements(normalized_action)
        if normalized_action in DOCUMENT_GENERATION_ACTIONS:
            if enforce_local_write:
                self._require_local_file_write_allowed()

        if normalized_action == "generate_docx_document":
            descriptor = self._generate_docx(conversation_id=conversation_id, payload=payload)
            return {"artifact": self._serialize_artifact(descriptor)}
        if normalized_action == "generate_pptx_deck":
            descriptor = self._generate_pptx(conversation_id=conversation_id, payload=payload)
            return {"artifact": self._serialize_artifact(descriptor)}
        if normalized_action == "generate_xlsx_workbook":
            descriptor = self._generate_xlsx(conversation_id=conversation_id, payload=payload)
            return {"artifact": self._serialize_artifact(descriptor)}
        if normalized_action == "generate_pdf_document":
            descriptor = self._generate_pdf(conversation_id=conversation_id, payload=payload)
            return {"artifact": self._serialize_artifact(descriptor)}
        if normalized_action == "extract_pdf_content":
            return self._extract_pdf(payload=payload)

        preview = self._preview_schedule(payload=payload)
        task = self.scheduled_tasks.create_task(
            conversation_id=conversation_id,
            project_id=project_id,
            user_id=payload.get("user_id") if isinstance(payload.get("user_id"), int) else None,
            prompt_template=preview.prompt_template,
            cron_spec=preview.cron_spec,
        )
        return {
            "scheduled_task": self.scheduled_tasks.serialize_task(task),
            "preview": preview.to_dict(),
        }

    def build_preview_from_chat(
        self,
        *,
        content: str,
    ) -> CapabilityPreview | None:
        normalized = self._normalize_text(content)
        if not normalized:
            return None
        if not self.might_build_preview_from_chat(content):
            return None

        schedule_keywords = normalized.startswith("kazdy ") or normalized.startswith("codziennie ")
        if schedule_keywords:
            return self.preview(
                action="schedule_recurring_task",
                conversation_id=None,
                project_id=None,
                payload={"request_text": content},
            )

        if "raport pdf" in normalized or "wygeneruj pdf" in normalized or "pdf raport" in normalized:
            payload = self._build_doc_payload_from_prompt(content, prefer_pdf=True)
            return self.preview(
                action="generate_pdf_document",
                conversation_id=None,
                project_id=None,
                payload=payload,
            )

        presentation_hit = any(keyword in normalized for keyword in ("prezentacja", "prezentacje", "slajd"))
        spreadsheet_hit = any(keyword in normalized for keyword in ("tabela", "arkusz", "excel", "porownanie"))
        document_hit = any(keyword in normalized for keyword in ("raport", "dokument", "word", "notatka"))

        matched = [hit for hit, present in (("pptx", presentation_hit), ("xlsx", spreadsheet_hit), ("docx", document_hit)) if present]
        if len(matched) > 1:
            raise CapabilityActionError(
                "Widzę kilka możliwych formatów dokumentu. Doprecyzuj, czy chodzi o Word, PowerPoint, Excel czy PDF."
            )
        if not matched:
            return None

        kind = matched[0]
        if kind == "pptx":
            return self.preview(
                action="generate_pptx_deck",
                conversation_id=None,
                project_id=None,
                payload=self._build_pptx_payload_from_prompt(content),
            )
        if kind == "xlsx":
            return self.preview(
                action="generate_xlsx_workbook",
                conversation_id=None,
                project_id=None,
                payload=self._build_xlsx_payload_from_prompt(content),
            )
        return self.preview(
            action="generate_docx_document",
            conversation_id=None,
            project_id=None,
            payload=self._build_doc_payload_from_prompt(content, prefer_pdf=False),
        )

    @classmethod
    def might_build_preview_from_chat(cls, content: str) -> bool:
        normalized = cls._normalize_text(content)
        if not normalized:
            return False
        if normalized.startswith(("kazdy ", "codziennie ")):
            return True

        output_terms = (
            "raport pdf|pdf raport|pdf|prezentacj[a-z]*|slajd[a-z]*|"
            "tabel[a-z]*|arkusz[a-z]*|excel|porownani[a-z]*|"
            "raport[a-z]*|dokument[a-z]*|word|notatk[a-z]*"
        )
        generation_verbs = "stworz|utworz|wygeneruj|przygotuj|zrob|napisz|opracuj|wyeksportuj"
        starts_with_output_request = bool(
            re.match(
                rf"^(?:prosze\s+o\s+)?(?:(?:{generation_verbs})\b.{{0,100}}\b(?:{output_terms})\b|"
                rf"(?:prezentacj[a-z]*|raport pdf|pdf raport|dokument word|arkusz excel)\b)",
                normalized,
            )
        )
        if len(normalized) > 1_200 and not starts_with_output_request:
            return False

        return starts_with_output_request or bool(
            re.search(
                rf"\b(?:{generation_verbs})\b[^.!?]{{0,120}}\b(?:{output_terms})\b",
                normalized,
            )
        )

    def attach_generated_artifact(
        self,
        *,
        conversation_id: int,
        message_id: int,
        artifact: dict[str, Any],
    ) -> None:
        self.attachments.create(
            filename=str(artifact["filename"]),
            storage_path=str(artifact["storage_path"]),
            kind=AttachmentKind.DOCUMENT,
            conversation_id=conversation_id,
            message_id=message_id,
            source=AttachmentSource.GENERATED,
            status=AttachmentStatus.AVAILABLE,
            media_type=str(artifact["mime"]),
            file_extension=str(artifact["file_extension"]),
            size_bytes=int(artifact["size_bytes"]),
            extracted_text=None,
            metadata_json={"generated": True},
        )

    def _require_local_file_write_allowed(self) -> None:
        owner = self.users.get_owner()
        if owner is None:
            raise CapabilityActionError("Brak użytkownika owner.")
        policy = self.execution_policies.get_for_user(owner.id)
        if policy is None or not policy.allow_local_file_write:
            raise CapabilityActionError("Local file write jest wyłączony w polityce wykonania.")

    def _ensure_plugin_requirements(self, action: str) -> None:
        for requirement in CAPABILITY_PLUGIN_REQUIREMENTS.get(action, ()):
            descriptor = self.plugin_registry.get(requirement.plugin_key)
            if descriptor is None:
                raise CapabilityActionError(
                    f"Capability '{action}' requires plugin '{requirement.plugin_key}', but it is not registered."
                )
            if requirement.permission not in descriptor.permissions:
                raise CapabilityActionError(
                    f"Plugin '{requirement.plugin_key}' does not expose required permission '{requirement.permission.value}'."
                )
            plugin = self.plugins.get_by_key(requirement.plugin_key)
            if plugin is None:
                raise CapabilityActionError(
                    f"Capability '{action}' requires plugin '{requirement.plugin_key}', but it is not configured."
                )
            if not plugin.is_enabled:
                raise CapabilityActionError(
                    f"Capability '{action}' requires plugin '{plugin.name}', but it is disabled in Settings."
                )
            if plugin.permission_policy == PluginPermissionPolicy.DISABLED:
                raise CapabilityActionError(
                    f"Capability '{action}' is blocked because plugin '{plugin.name}' permission policy is disabled."
                )

    def _generate_docx(self, *, conversation_id: int | None, payload: dict[str, Any]) -> ManagedArtifactDescriptor:
        normalized = self._normalize_docx_payload(payload)
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".docx")
        document = Document()
        document.add_heading(str(normalized["title"]), level=0)
        for section in normalized["sections"]:
            document.add_heading(str(section["heading"]), level=1)
            for paragraph in section["paragraphs"]:
                document.add_paragraph(str(paragraph))
        document.save(output_path)
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=self._build_filename(str(normalized["title"]), ".docx"),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    def _generate_pptx(self, *, conversation_id: int | None, payload: dict[str, Any]) -> ManagedArtifactDescriptor:
        normalized = self._normalize_pptx_payload(payload)
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".pptx")
        presentation = Presentation()
        for slide_payload in normalized["slides"]:
            layout = str(slide_payload["layout"])
            if layout == "title":
                slide = presentation.slides.add_slide(presentation.slide_layouts[0])
                slide.shapes.title.text = str(slide_payload["heading"])
                subtitle = slide.placeholders[1]
                subtitle.text = str(slide_payload.get("body") or "")
            elif layout == "bullets":
                slide = presentation.slides.add_slide(presentation.slide_layouts[1])
                slide.shapes.title.text = str(slide_payload["heading"])
                text_frame = slide.placeholders[1].text_frame
                bullets = slide_payload.get("bullets") or []
                if bullets:
                    text_frame.text = str(bullets[0])
                    for bullet in bullets[1:]:
                        paragraph = text_frame.add_paragraph()
                        paragraph.text = str(bullet)
                else:
                    text_frame.text = str(slide_payload.get("body") or "")
            else:
                slide = presentation.slides.add_slide(presentation.slide_layouts[5])
                slide.shapes.title.text = str(slide_payload["heading"])
                image_path = slide_payload.get("image_path")
                if isinstance(image_path, str) and image_path.strip():
                    try:
                        resolved_image = self.artifacts.resolve_download_path(image_path)
                    except ManagedArtifactPathError:
                        resolved_image = None
                    if resolved_image is not None:
                        slide.shapes.add_picture(str(resolved_image), Inches(1), Inches(1.5), width=Inches(8))
                textbox = slide.shapes.add_textbox(Inches(0.9), Inches(6.5), Inches(8.2), Inches(1.2))
                textbox.text_frame.text = str(slide_payload.get("body") or "")
        presentation.save(output_path)
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=self._build_filename(str(normalized["title"]), ".pptx"),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )

    def _generate_xlsx(self, *, conversation_id: int | None, payload: dict[str, Any]) -> ManagedArtifactDescriptor:
        normalized = self._normalize_xlsx_payload(payload)
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".xlsx")
        workbook = Workbook()
        first_sheet = True
        for sheet_index, sheet_payload in enumerate(normalized["sheets"], start=1):
            sheet = workbook.active if first_sheet else workbook.create_sheet()
            first_sheet = False
            sheet.title = str(sheet_payload["name"])[:31]
            columns = list(sheet_payload["columns"])
            sheet.append(columns)
            for row in sheet_payload["rows"]:
                sheet.append(list(row))
            if sheet_payload.get("freeze_top"):
                sheet.freeze_panes = "A2"
            self._format_xlsx_sheet(sheet, table_name=f"AtnosTable{sheet_index}")
        workbook.save(output_path)
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-workbook.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @staticmethod
    def _format_xlsx_sheet(sheet, *, table_name: str) -> None:
        original_max_row = sheet.max_row
        max_column = sheet.max_column
        if original_max_row < 1 or max_column < 1:
            return

        sheet.insert_rows(1, 3)
        max_row = sheet.max_row
        header_row = 4
        first_data_row = 5
        if max_row < 1 or max_column < 1:
            return

        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.tabColor = "2563EB"

        last_column_letter = get_column_letter(max_column)
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_column)
        sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_column)
        sheet["A1"] = sheet.title
        sheet["A2"] = "Raport wygenerowany lokalnie przez Atnos. Dane sa gotowe do przegladu i edycji."

        title_fill = PatternFill("solid", fgColor="111827")
        subtitle_fill = PatternFill("solid", fgColor="E5E7EB")
        header_fill = PatternFill("solid", fgColor="1F2937")
        title_font = Font(bold=True, color="FFFFFF", size=15)
        subtitle_font = Font(italic=True, color="374151")
        header_font = Font(bold=True, color="FFFFFF")
        body_alignment = Alignment(vertical="top", wrap_text=True)
        header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(bottom=Side(style="thin", color="D1D5DB"))

        sheet["A1"].fill = title_fill
        sheet["A1"].font = title_font
        sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")
        sheet["A2"].fill = subtitle_fill
        sheet["A2"].font = subtitle_font
        sheet["A2"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        sheet.row_dimensions[1].height = 30
        sheet.row_dimensions[2].height = 24
        sheet.row_dimensions[3].height = 8

        for cell in sheet[header_row]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = thin_border

        for row in sheet.iter_rows(min_row=first_data_row, max_row=max_row, max_col=max_column):
            for cell in row:
                cell.alignment = body_alignment
                cell.border = thin_border

        for column_index in range(1, max_column + 1):
            column_letter = get_column_letter(column_index)
            header = str(sheet.cell(row=header_row, column=column_index).value or "")
            longest = max(
                len(str(sheet.cell(row=row_index, column=column_index).value or ""))
                for row_index in range(header_row, max_row + 1)
            )
            if header.lower() in {"nr", "id"}:
                width = 8
            elif "priorytet" in header.lower() or "status" in header.lower():
                width = 14
            else:
                width = min(max(longest + 3, 18), 48)
            sheet.column_dimensions[column_letter].width = width

        sheet.row_dimensions[header_row].height = 32
        for row_index in range(first_data_row, max_row + 1):
            sheet.row_dimensions[row_index].height = 48

        sheet.freeze_panes = f"A{first_data_row}"

        if max_row >= first_data_row:
            table_ref = f"A{header_row}:{last_column_letter}{max_row}"
            table = Table(displayName=table_name, ref=table_ref)
            table.tableStyleInfo = TableStyleInfo(
                name="TableStyleMedium9",
                showFirstColumn=False,
                showLastColumn=False,
                showRowStripes=True,
                showColumnStripes=False,
            )
            sheet.add_table(table)

        priority_column = None
        for cell in sheet[header_row]:
            if str(cell.value or "").strip().lower() == "priorytet":
                priority_column = cell.column_letter
                break
        if priority_column and max_row >= first_data_row:
            priority_range = f"{priority_column}{first_data_row}:{priority_column}{max_row}"
            sheet.conditional_formatting.add(
                priority_range,
                CellIsRule(
                    operator="equal",
                    formula=['"P0"'],
                    fill=PatternFill("solid", fgColor="FEE2E2"),
                    font=Font(bold=True, color="991B1B"),
                ),
            )
            sheet.conditional_formatting.add(
                priority_range,
                CellIsRule(
                    operator="equal",
                    formula=['"P1"'],
                    fill=PatternFill("solid", fgColor="FEF3C7"),
                    font=Font(bold=True, color="92400E"),
                ),
            )
            sheet.conditional_formatting.add(
                priority_range,
                CellIsRule(
                    operator="equal",
                    formula=['"P2"'],
                    fill=PatternFill("solid", fgColor="DBEAFE"),
                    font=Font(bold=True, color="1E40AF"),
                ),
            )

    def _generate_pdf(self, *, conversation_id: int | None, payload: dict[str, Any]) -> ManagedArtifactDescriptor:
        normalized = self._normalize_pdf_payload(payload)
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".pdf")
        stylesheet = getSampleStyleSheet()
        story: list[Any] = [Paragraph(str(normalized["title"]), stylesheet["Title"]), Spacer(1, 16)]
        for section in normalized["sections"]:
            story.append(Paragraph(str(section["heading"]), stylesheet["Heading2"]))
            story.append(Spacer(1, 8))
            for paragraph in section["paragraphs"]:
                story.append(Paragraph(str(paragraph), stylesheet["BodyText"]))
                story.append(Spacer(1, 8))
            story.append(Spacer(1, 12))
        document = SimpleDocTemplate(str(output_path), pagesize=A4)
        document.build(story)
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=self._build_filename(str(normalized["title"]), ".pdf"),
            media_type="application/pdf",
        )

    def _extract_pdf(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        from atnos.services.attachment_understanding_service import AttachmentUnderstandingService

        storage_path = self._normalize_storage_path(payload.get("storage_path"))
        resolved = self.artifacts.resolve_download_path(storage_path)
        if resolved.suffix.lower() != ".pdf":
            raise CapabilityActionError("extract_pdf_content wymaga pliku PDF.")
        content = resolved.read_bytes()
        understanding = AttachmentUnderstandingService(max_raw_read_bytes=1024 * 1024, max_preview_text_chars=100_000)
        result = understanding.understand_existing(
            filename=resolved.name,
            content_type="application/pdf",
            storage_path=storage_path,
            extracted_text=None,
            app_root=self.settings.app_root,
            uploads_dir=self.settings.uploads_dir,
            allowed_roots=(
                self.settings.data_dir / "atnos-outputs",
                self.settings.browser_downloads_dir,
                self.settings.workspace_staging_dir,
            ),
        )
        extracted = result.extracted_text or ""
        page_chunks: list[dict[str, Any]] = []
        if extracted:
            split_pages = re.split(r"\n\n(?=\[Page \d+\])", extracted)
            for chunk in split_pages:
                match = re.match(r"^\[Page (\d+)\]\n?(.*)$", chunk, re.DOTALL)
                if match:
                    page_chunks.append(
                        {
                            "page_number": int(match.group(1)),
                            "text": match.group(2).strip(),
                        }
                    )
        return {
            "storage_path": storage_path,
            "pages": page_chunks,
            "total_pages": len(page_chunks),
            "extracted_chars": len(extracted),
            "extracted_text": extracted,
            "size_bytes": len(content),
        }

    def _preview_schedule(self, *, payload: dict[str, Any]) -> ScheduledTaskPreview:
        request_text = self._normalize_required_text(
            payload.get("request_text") or payload.get("schedule_text"),
            "request_text",
        )
        prompt_template = payload.get("prompt_template")
        if prompt_template is not None and not isinstance(prompt_template, str):
            raise CapabilityActionError("prompt_template musi być stringiem.")
        return self.scheduled_tasks.parse_natural_schedule(
            request_text=request_text,
            prompt_template=prompt_template,
        )

    def _serialize_artifact(self, descriptor: ManagedArtifactDescriptor) -> dict[str, Any]:
        return {
            "filename": descriptor.filename,
            "mime": descriptor.media_type,
            "storage_path": descriptor.storage_path,
            "size_bytes": descriptor.size_bytes,
            "file_extension": descriptor.file_extension,
        }

    @staticmethod
    def _normalize_action(value: str) -> str:
        normalized = " ".join((value or "").strip().split())
        if normalized not in CAPABILITY_ACTIONS:
            allowed = ", ".join(sorted(CAPABILITY_ACTIONS))
            raise CapabilityActionError(f"Unsupported action. Allowed actions: {allowed}.")
        return normalized

    @staticmethod
    def _normalize_storage_path(value: object) -> str:
        if not isinstance(value, str):
            raise CapabilityActionError("storage_path musi być stringiem.")
        normalized = value.strip()
        if not normalized or ".." in normalized.replace("\\", "/").split("/"):
            raise CapabilityActionError("storage_path jest niepoprawny.")
        return normalized

    @staticmethod
    def _normalize_required_text(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise CapabilityActionError(f"{field_name} musi być stringiem.")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise CapabilityActionError(f"{field_name} nie może być pusty.")
        return normalized

    @staticmethod
    def _normalize_optional_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise CapabilityActionError("Optional text value must be a string when present.")
        normalized = " ".join(value.strip().split())
        return normalized or None

    def _normalize_docx_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = self._normalize_required_text(payload.get("title"), "title")
        sections = payload.get("sections")
        if not isinstance(sections, list) or not sections:
            raise CapabilityActionError("sections musi być niepustą listą.")
        normalized_sections: list[dict[str, Any]] = []
        for index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                raise CapabilityActionError(f"sections[{index}] musi być obiektem.")
            heading = self._normalize_required_text(section.get("heading"), f"sections[{index}].heading")
            paragraphs = section.get("paragraphs")
            if not isinstance(paragraphs, list) or not paragraphs:
                raise CapabilityActionError(f"sections[{index}].paragraphs musi być niepustą listą.")
            normalized_sections.append(
                {
                    "heading": heading,
                    "paragraphs": [self._normalize_required_text(item, f"sections[{index}].paragraphs") for item in paragraphs],
                }
            )
        return {"title": title, "sections": normalized_sections, "style": self._normalize_optional_text(payload.get("style"))}

    def _normalize_pdf_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._normalize_docx_payload(payload)

    def _normalize_pptx_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = self._normalize_required_text(payload.get("title"), "title")
        slides = payload.get("slides")
        if not isinstance(slides, list) or not slides:
            raise CapabilityActionError("slides musi być niepustą listą.")
        normalized_slides: list[dict[str, Any]] = []
        for index, slide in enumerate(slides, start=1):
            if not isinstance(slide, dict):
                raise CapabilityActionError(f"slides[{index}] musi być obiektem.")
            layout = self._normalize_required_text(slide.get("layout"), f"slides[{index}].layout")
            if layout not in {"title", "bullets", "image_caption"}:
                raise CapabilityActionError(f"slides[{index}].layout jest nieobsługiwany.")
            normalized_slide = {
                "layout": layout,
                "heading": self._normalize_required_text(slide.get("heading"), f"slides[{index}].heading"),
                "body": self._normalize_optional_text(slide.get("body")) or "",
                "image_path": self._normalize_optional_text(slide.get("image_path")),
            }
            bullets = slide.get("bullets")
            if bullets is not None:
                if not isinstance(bullets, list):
                    raise CapabilityActionError(f"slides[{index}].bullets musi być listą.")
                normalized_slide["bullets"] = [self._normalize_required_text(item, f"slides[{index}].bullets") for item in bullets]
            normalized_slides.append(normalized_slide)
        return {"title": title, "slides": normalized_slides}

    def _normalize_xlsx_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sheets = payload.get("sheets")
        if not isinstance(sheets, list) or not sheets:
            raise CapabilityActionError("sheets musi być niepustą listą.")
        normalized_sheets: list[dict[str, Any]] = []
        for index, sheet in enumerate(sheets, start=1):
            if not isinstance(sheet, dict):
                raise CapabilityActionError(f"sheets[{index}] musi być obiektem.")
            name = self._normalize_required_text(sheet.get("name"), f"sheets[{index}].name")
            columns = sheet.get("columns")
            rows = sheet.get("rows")
            if not isinstance(columns, list) or not columns:
                raise CapabilityActionError(f"sheets[{index}].columns musi być niepustą listą.")
            if not isinstance(rows, list):
                raise CapabilityActionError(f"sheets[{index}].rows musi być listą.")
            normalized_rows: list[list[Any]] = []
            for row in rows:
                if not isinstance(row, list):
                    raise CapabilityActionError(f"sheets[{index}].rows zawiera niepoprawny wiersz.")
                normalized_rows.append(list(row))
            normalized_sheets.append(
                {
                    "name": name,
                    "columns": [self._normalize_required_text(item, f"sheets[{index}].columns") for item in columns],
                    "rows": normalized_rows,
                    "freeze_top": bool(sheet.get("freeze_top")),
                }
            )
        return {"sheets": normalized_sheets}

    def _build_doc_payload_from_prompt(self, prompt: str, *, prefer_pdf: bool) -> dict[str, Any]:
        topic = self._extract_topic(prompt)
        heading = "Decyzje" if "decyzj" in self._normalize_text(prompt) else "Treść"
        title = "Raport PDF" if prefer_pdf else "Raport Word"
        if topic:
            title = f"{title} — {topic}"
        return {
            "title": title,
            "sections": [
                {
                    "heading": heading,
                    "paragraphs": [
                        f"Dokument wygenerowany na podstawie prośby: {prompt.strip()}",
                        f"Temat roboczy: {topic or 'Bez doprecyzowanego tematu'}.",
                    ],
                }
            ],
        }

    def _build_pptx_payload_from_prompt(self, prompt: str) -> dict[str, Any]:
        topic = self._extract_topic(prompt) or "Temat prezentacji"
        slide_count_match = re.search(r"(\d+)\s+slajd", self._normalize_text(prompt))
        slide_count = max(2, min(12, int(slide_count_match.group(1)) if slide_count_match else 5))
        slides: list[dict[str, Any]] = [
            {
                "layout": "title",
                "heading": topic,
                "body": "Local-first AI / szkic prezentacji wygenerowany przez ATNOS.",
            }
        ]
        for index in range(2, slide_count + 1):
            slides.append(
                {
                    "layout": "bullets",
                    "heading": f"{topic} — slajd {index}",
                    "bullets": [
                        "Najważniejszy punkt",
                        "Konkretny przykład użycia",
                        "Wniosek lub kolejny krok",
                    ],
                }
            )
        return {"title": topic, "slides": slides}

    def _build_xlsx_payload_from_prompt(self, prompt: str) -> dict[str, Any]:
        structured_rows = self._extract_numbered_pipe_rows(prompt)
        if structured_rows:
            summary_rows: list[list[Any]] = []
            user_task_rows: list[list[Any]] = []
            for index, parts in enumerate(structured_rows, start=1):
                topic = parts[0] if len(parts) > 0 else f"Temat {index}"
                purpose = parts[1] if len(parts) > 1 else ""
                atnos_action = parts[2] if len(parts) > 2 else ""
                user_action = parts[3] if len(parts) > 3 else ""
                priority = parts[4] if len(parts) > 4 else ""
                summary_rows.append([index, topic, purpose, atnos_action, user_action, priority])
                if user_action:
                    user_task_rows.append([index, user_action, f"Dotyczy: {topic}", priority or "Do decyzji"])
            return {
                "sheets": [
                    {
                        "name": "Podsumowanie",
                        "columns": [
                            "Nr",
                            "Temat",
                            "Do czego",
                            "Co ma zrobić Atnos",
                            "Co ma zrobić użytkownik",
                            "Priorytet",
                        ],
                        "rows": summary_rows,
                        "freeze_top": True,
                    },
                    {
                        "name": "Zadania użytkownika",
                        "columns": ["Nr", "Zadanie", "Powiązany temat", "Priorytet"],
                        "rows": user_task_rows,
                        "freeze_top": True,
                    },
                ]
            }
        topic = self._extract_topic(prompt) or "Porównanie"
        return {
            "sheets": [
                {
                    "name": "Porównanie",
                    "columns": ["Element", "Opis"],
                    "rows": [
                        [topic, prompt.strip()],
                    ],
                    "freeze_top": True,
                }
            ]
        }

    @staticmethod
    def _extract_numbered_pipe_rows(prompt: str) -> list[list[str]]:
        rows: list[list[str]] = []
        current: str | None = None
        for raw_line in prompt.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if re.match(r"^\d+[\.\)]\s+", line):
                if current:
                    rows.append(CapabilityActionService._split_pipe_row(current))
                current = re.sub(r"^\d+[\.\)]\s+", "", line).strip()
            elif current is not None:
                current = f"{current} {line}".strip()
        if current:
            rows.append(CapabilityActionService._split_pipe_row(current))
        return [row for row in rows if len(row) >= 2]

    @staticmethod
    def _split_pipe_row(value: str) -> list[str]:
        return [" ".join(part.strip().split()) for part in value.split("|") if part.strip()]

    @staticmethod
    def _build_filename(title: str, extension: str) -> str:
        normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return f"{slug or 'atnos-output'}{extension}"

    @staticmethod
    def _normalize_text(value: str) -> str:
        collapsed = " ".join((value or "").strip().split())
        ascii_value = unicodedata.normalize("NFKD", collapsed).encode("ascii", "ignore").decode("ascii")
        return ascii_value.lower()

    def _extract_topic(self, prompt: str) -> str | None:
        raw = " ".join(prompt.strip().split())
        topic_match = re.search(r"\bo\s+(.+)$", raw, re.IGNORECASE)
        if topic_match:
            return topic_match.group(1).strip().rstrip(".")
        topic_match = re.search(r"\bna temat\s+(.+)$", raw, re.IGNORECASE)
        if topic_match:
            return topic_match.group(1).strip().rstrip(".")
        return None
