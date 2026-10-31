from __future__ import annotations

import csv
import json
import mimetypes
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from atnos.db.models.enums import AttachmentKind


@dataclass(slots=True, frozen=True)
class AttachmentUnderstandingResult:
    attachment_id: int | None
    filename: str
    content_type: str | None
    detected_kind: str
    status: str
    preview_text: str | None
    extracted_text: str | None
    status_reason: str | None
    truncated: bool
    attachment_kind: AttachmentKind

    def to_metadata(self) -> dict[str, str | bool | None]:
        return {
            "detected_kind": self.detected_kind,
            "understanding_status": self.status,
            "understanding_truncated": self.truncated,
            "understanding_reason": self.status_reason,
        }


class AttachmentUnderstandingService:
    SUPPORTED_TEXT_EXTENSIONS = {
        ".txt": "plain_text",
        ".md": "markdown",
        ".markdown": "markdown",
        ".json": "json",
        ".csv": "csv",
    }
    SUPPORTED_DOCUMENT_EXTENSIONS = {
        ".docx": "docx",
        ".pdf": "pdf",
    }
    SUPPORTED_IMAGE_EXTENSIONS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
    }
    SUPPORTED_TEXT_MEDIA_TYPES = {
        "text/plain": "plain_text",
        "text/markdown": "markdown",
        "application/json": "json",
        "text/csv": "csv",
        "application/csv": "csv",
    }
    DOCX_MEDIA_TYPE = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    PDF_MEDIA_TYPE = "application/pdf"

    def __init__(
        self,
        *,
        max_raw_read_bytes: int = 64 * 1024,
        max_preview_text_chars: int = 8_000,
        max_inline_metadata_preview_chars: int = 240,
    ) -> None:
        self.max_raw_read_bytes = max_raw_read_bytes
        self.max_preview_text_chars = max_preview_text_chars
        self.max_inline_metadata_preview_chars = max_inline_metadata_preview_chars

    def understand(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
        attachment_id: int | None = None,
    ) -> AttachmentUnderstandingResult:
        normalized_content_type = content_type or mimetypes.guess_type(filename)[0]
        detected_kind, attachment_kind = self._detect_kind(
            filename=filename,
            content_type=normalized_content_type,
        )
        return self._understand_from_bytes(
            attachment_id=attachment_id,
            filename=filename,
            content=content,
            content_type=normalized_content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
        )

    def understand_existing(
        self,
        *,
        filename: str,
        content_type: str | None,
        storage_path: str,
        extracted_text: str | None,
        attachment_id: int | None = None,
        app_root: Path | None = None,
        uploads_dir: Path | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> AttachmentUnderstandingResult:
        normalized_content_type = content_type or mimetypes.guess_type(filename)[0]
        detected_kind, attachment_kind = self._detect_kind(
            filename=filename,
            content_type=normalized_content_type,
        )

        if extracted_text and extracted_text.strip():
            return self._understand_from_text(
                attachment_id=attachment_id,
                filename=filename,
                text=extracted_text,
                content_type=normalized_content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                raw_truncated=False,
            )

        safe_path = self._resolve_safe_storage_path(
            storage_path=storage_path,
            app_root=app_root,
            uploads_dir=uploads_dir,
            allowed_roots=allowed_roots,
        )
        if safe_path is not None and safe_path.exists() and safe_path.is_file():
            if detected_kind == "docx":
                try:
                    size_bytes = safe_path.stat().st_size
                except OSError:
                    size_bytes = None
                if size_bytes is not None and size_bytes > self.max_raw_read_bytes:
                    return self._build_result(
                        attachment_id=attachment_id,
                        filename=filename,
                        content_type=normalized_content_type,
                        detected_kind=detected_kind,
                        attachment_kind=attachment_kind,
                        status="too_large",
                        preview_text=None,
                        status_reason=(
                            f"Preview was skipped because the file exceeds the {self.max_raw_read_bytes} byte safe-read limit."
                        ),
                        truncated=False,
                    )

            try:
                content = safe_path.read_bytes()
            except OSError:
                content = None

            if content is not None:
                return self._understand_from_bytes(
                    attachment_id=attachment_id,
                    filename=filename,
                    content=content,
                    content_type=normalized_content_type,
                    detected_kind=detected_kind,
                    attachment_kind=attachment_kind,
                )

        if detected_kind in {"plain_text", "markdown", "json", "csv", "docx", "pdf"}:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=normalized_content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="metadata_only",
                preview_text=None,
                status_reason=(
                    "No extracted text was available and no safe local file preview could be generated."
                ),
                truncated=False,
            )

        if detected_kind == "image" or attachment_kind == AttachmentKind.IMAGE:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=normalized_content_type,
                detected_kind="image",
                attachment_kind=AttachmentKind.IMAGE,
                status="vision_ready",
                preview_text=None,
                status_reason="Image attachment is available for vision interpretation (no text extraction).",
                truncated=False,
            )

        return self._build_result(
            attachment_id=attachment_id,
            filename=filename,
            content_type=normalized_content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            status="unsupported",
            preview_text=None,
            status_reason="Unsupported file type.",
            truncated=False,
        )

    def build_metadata(self, result: AttachmentUnderstandingResult) -> dict[str, str | bool | None]:
        return result.to_metadata()

    def build_inline_preview(self, value: str | None) -> str | None:
        normalized = self._normalize_preview_text(value)
        if normalized is None:
            return None
        if len(normalized) <= self.max_inline_metadata_preview_chars:
            return normalized
        return normalized[: self.max_inline_metadata_preview_chars].rstrip() + "..."

    def _understand_from_bytes(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        content: bytes,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
    ) -> AttachmentUnderstandingResult:
        if detected_kind == "image" or attachment_kind == AttachmentKind.IMAGE:
            if not content:
                return self._build_result(
                    attachment_id=attachment_id,
                    filename=filename,
                    content_type=content_type,
                    detected_kind="image",
                    attachment_kind=AttachmentKind.IMAGE,
                    status="empty",
                    preview_text=None,
                    status_reason="Image file is empty.",
                    truncated=False,
                )
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind="image",
                attachment_kind=AttachmentKind.IMAGE,
                status="vision_ready",
                preview_text=None,
                status_reason="Image attachment is available for vision interpretation (no text extraction).",
                truncated=False,
            )

        if detected_kind in {"audio", "video"} or attachment_kind in {
            AttachmentKind.AUDIO,
            AttachmentKind.VIDEO,
        }:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="binary_ready",
                preview_text=None,
                status_reason="Binary media attachment; text extraction is not applicable.",
                truncated=False,
            )

        if detected_kind not in {"plain_text", "markdown", "json", "csv", "docx", "pdf"}:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="unsupported",
                preview_text=None,
                status_reason="Unsupported file type.",
                truncated=False,
            )

        if not content:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="empty",
                preview_text=None,
                status_reason="File is empty.",
                truncated=False,
            )

        if detected_kind == "docx" and len(content) > self.max_raw_read_bytes:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="too_large",
                preview_text=None,
                status_reason=(
                    f"Preview was skipped because the file exceeds the {self.max_raw_read_bytes} byte safe-read limit."
                ),
                truncated=False,
            )

        raw_truncated = False
        content_for_parse = content
        if detected_kind not in {"docx", "pdf"} and len(content) > self.max_raw_read_bytes:
            content_for_parse = content[: self.max_raw_read_bytes]
            raw_truncated = True

        if detected_kind == "docx":
            return self._understand_docx_bytes(
                attachment_id=attachment_id,
                filename=filename,
                content=content_for_parse,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
            )
        if detected_kind == "pdf":
            return self._understand_pdf_bytes(
                attachment_id=attachment_id,
                filename=filename,
                content=content_for_parse,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
            )

        decoded_text = self._decode_text_bytes(content_for_parse)
        if decoded_text is None:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="decode_error",
                preview_text=None,
                status_reason="File content could not be decoded as UTF-8 text.",
                truncated=False,
            )

        return self._understand_from_text(
            attachment_id=attachment_id,
            filename=filename,
            text=decoded_text,
            content_type=content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            raw_truncated=raw_truncated,
        )

    def _understand_from_text(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        text: str,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
        raw_truncated: bool,
    ) -> AttachmentUnderstandingResult:
        if detected_kind == "json":
            return self._understand_json_text(
                attachment_id=attachment_id,
                filename=filename,
                text=text,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                raw_truncated=raw_truncated,
            )

        if detected_kind == "csv":
            return self._understand_csv_text(
                attachment_id=attachment_id,
                filename=filename,
                text=text,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                raw_truncated=raw_truncated,
            )

        normalized = self._normalize_document_text(text)
        if normalized is None:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="empty",
                preview_text=None,
                status_reason="File content is empty after normalization.",
                truncated=False,
            )

        return self._build_ready_result(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            preview_text=normalized,
            raw_truncated=raw_truncated,
        )

    def _understand_json_text(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        text: str,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
        raw_truncated: bool,
    ) -> AttachmentUnderstandingResult:
        normalized = self._normalize_document_text(text)
        if normalized is None:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="empty",
                preview_text=None,
                status_reason="JSON file is empty after normalization.",
                truncated=False,
            )

        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError as exc:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="decode_error",
                preview_text=None,
                status_reason=f"Invalid JSON: {exc.msg}.",
                truncated=False,
            )

        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
        return self._build_ready_result(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            preview_text=pretty,
            raw_truncated=raw_truncated,
        )

    def _understand_csv_text(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        text: str,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
        raw_truncated: bool,
    ) -> AttachmentUnderstandingResult:
        normalized = self._normalize_document_text(text)
        if normalized is None:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="empty",
                preview_text=None,
                status_reason="CSV file is empty after normalization.",
                truncated=False,
            )

        reader = csv.reader(StringIO(normalized))
        rows = [
            [cell.strip() for cell in row]
            for row in reader
            if any(cell.strip() for cell in row)
        ]
        if not rows:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="empty",
                preview_text=None,
                status_reason="CSV file has no readable rows.",
                truncated=False,
            )

        headers = rows[0]
        data_rows = rows[1:5]
        preview_lines = [f"Headers: {', '.join(headers)}"]
        if data_rows:
            preview_lines.append("Rows:")
            preview_lines.extend(f"- {', '.join(row)}" for row in data_rows)
        else:
            preview_lines.append("Rows: [no data rows]")

        return self._build_ready_result(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            preview_text="\n".join(preview_lines),
            raw_truncated=raw_truncated,
        )

    def _understand_docx_bytes(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        content: bytes,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
    ) -> AttachmentUnderstandingResult:
        extracted = self._extract_docx_text_content(content)
        if extracted is None:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="metadata_only",
                preview_text=None,
                status_reason="DOCX metadata is available, but no readable text preview could be extracted.",
                truncated=False,
            )

        return self._build_ready_result(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            preview_text=extracted,
            raw_truncated=False,
        )

    def _understand_pdf_bytes(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        content: bytes,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
    ) -> AttachmentUnderstandingResult:
        extracted = self._extract_pdf_text_content(content)
        if extracted is None:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="metadata_only",
                preview_text=None,
                status_reason="PDF metadata is available, but no readable text preview could be extracted.",
                truncated=False,
            )

        return self._build_ready_result(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            preview_text=extracted,
            raw_truncated=False,
        )

    def _build_ready_result(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
        preview_text: str,
        raw_truncated: bool,
    ) -> AttachmentUnderstandingResult:
        normalized_preview = self._normalize_document_text(preview_text)
        if normalized_preview is None:
            return self._build_result(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                detected_kind=detected_kind,
                attachment_kind=attachment_kind,
                status="empty",
                preview_text=None,
                status_reason="File content is empty after normalization.",
                truncated=False,
            )

        preview = normalized_preview[: self.max_preview_text_chars]
        preview_truncated = len(normalized_preview) > self.max_preview_text_chars
        truncated = raw_truncated or preview_truncated
        reason = None
        if truncated:
            reason = (
                f"Preview was truncated to the first {self.max_preview_text_chars} characters."
            )

        return self._build_result(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            detected_kind=detected_kind,
            attachment_kind=attachment_kind,
            status="ready",
            preview_text=preview,
            status_reason=reason,
            truncated=truncated,
        )

    def _build_result(
        self,
        *,
        attachment_id: int | None,
        filename: str,
        content_type: str | None,
        detected_kind: str,
        attachment_kind: AttachmentKind,
        status: str,
        preview_text: str | None,
        status_reason: str | None,
        truncated: bool,
    ) -> AttachmentUnderstandingResult:
        normalized_preview = self._normalize_document_text(preview_text)
        return AttachmentUnderstandingResult(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            detected_kind=detected_kind,
            status=status,
            preview_text=normalized_preview,
            extracted_text=normalized_preview,
            status_reason=status_reason,
            truncated=truncated,
            attachment_kind=attachment_kind,
        )

    def _detect_kind(
        self,
        *,
        filename: str,
        content_type: str | None,
    ) -> tuple[str, AttachmentKind]:
        normalized_content_type = (content_type or "").lower()
        extension = Path(filename).suffix.lower()

        if normalized_content_type.startswith("image/") or extension in self.SUPPORTED_IMAGE_EXTENSIONS:
            return "image", AttachmentKind.IMAGE
        if normalized_content_type.startswith("audio/"):
            return "audio", AttachmentKind.AUDIO
        if normalized_content_type.startswith("video/"):
            return "video", AttachmentKind.VIDEO

        if extension in self.SUPPORTED_TEXT_EXTENSIONS:
            return self.SUPPORTED_TEXT_EXTENSIONS[extension], AttachmentKind.TEXT
        if extension in self.SUPPORTED_DOCUMENT_EXTENSIONS:
            return self.SUPPORTED_DOCUMENT_EXTENSIONS[extension], AttachmentKind.DOCUMENT
        if normalized_content_type in self.SUPPORTED_TEXT_MEDIA_TYPES:
            return self.SUPPORTED_TEXT_MEDIA_TYPES[normalized_content_type], AttachmentKind.TEXT
        if normalized_content_type == self.DOCX_MEDIA_TYPE:
            return "docx", AttachmentKind.DOCUMENT
        if normalized_content_type == self.PDF_MEDIA_TYPE:
            return "pdf", AttachmentKind.DOCUMENT
        if normalized_content_type.startswith("text/"):
            return "plain_text", AttachmentKind.TEXT

        return "other", AttachmentKind.OTHER

    @staticmethod
    def _normalize_document_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        stripped = normalized.strip()
        return stripped or None

    @staticmethod
    def _decode_text_bytes(content: bytes) -> str | None:
        for encoding in ("utf-8", "utf-8-sig"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    def _resolve_safe_storage_path(
        self,
        *,
        storage_path: str,
        app_root: Path | None,
        uploads_dir: Path | None,
        allowed_roots: tuple[Path, ...] | None,
    ) -> Path | None:
        normalized_storage_path = storage_path.strip()
        if not normalized_storage_path or app_root is None or uploads_dir is None:
            return None

        requested_path = Path(normalized_storage_path)
        candidate_path = (
            requested_path.resolve()
            if requested_path.is_absolute()
            else (app_root / requested_path).resolve()
        )
        managed_roots = [uploads_dir.resolve()]
        for root in allowed_roots or ():
            try:
                managed_roots.append(root.resolve())
            except OSError:
                continue

        for managed_root in managed_roots:
            try:
                candidate_path.relative_to(managed_root)
                return candidate_path
            except ValueError:
                continue

        return None

    @staticmethod
    def _extract_docx_text_content(content: bytes) -> str | None:
        try:
            with ZipFile(BytesIO(content)) as archive:
                document_xml = archive.read("word/document.xml")
        except (BadZipFile, KeyError, OSError):
            return None

        try:
            root = ET.fromstring(document_xml)
        except ET.ParseError:
            return None

        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: list[str] = []

        for paragraph in root.findall(".//w:body/w:p", namespace):
            text_fragments = [
                text_node.text or ""
                for text_node in paragraph.findall(".//w:t", namespace)
                if text_node.text
            ]
            normalized_paragraph = "".join(text_fragments).replace("\x00", "").strip()
            if normalized_paragraph:
                paragraphs.append(normalized_paragraph)

        normalized = "\n\n".join(paragraphs).strip()
        return normalized or None

    @staticmethod
    def _extract_pdf_text_content(content: bytes) -> str | None:
        try:
            reader = PdfReader(BytesIO(content))
        except (PdfReadError, ValueError, OSError):
            return None

        pages: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            normalized = text.replace("\x00", "").strip()
            if normalized:
                pages.append(f"[Page {index}]\n{normalized}")

        joined = "\n\n".join(pages).strip()
        return joined or None
