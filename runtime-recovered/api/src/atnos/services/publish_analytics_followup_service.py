from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import AttachmentKind, AttachmentSource, AttachmentStatus, MessageRole, MessageStatus
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService
from atnos.services.youtube_connector_service import YouTubeConnectorService


class PublishAnalyticsFollowupService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.messages = MessageRepository(session)
        self.attachments = AttachmentRepository(session)
        self.integration_config = IntegrationConfigService(session, settings)
        self.artifacts = OutputArtifactService(settings)
        self.youtube = YouTubeConnectorService(session, settings)

    def run_followup(
        self,
        *,
        project_name: str,
        conversation_id: int | None,
        source_run_id: int | None,
        video_id: str,
        title: str | None,
        publish_result: dict[str, Any],
        review_window_hours: int,
    ) -> dict[str, Any]:
        analytics_summary = self._fetch_analytics(video_id=video_id)
        strategy_snapshot = self._build_strategy_snapshot(
            analytics_summary=analytics_summary,
            review_window_hours=review_window_hours,
        )
        note_content = self._build_note_content(
            project_name=project_name,
            title=title,
            video_id=video_id,
            publish_result=publish_result,
            analytics_summary=analytics_summary,
            review_window_hours=review_window_hours,
            strategy_snapshot=strategy_snapshot,
        )
        note_artifact = self._write_markdown_artifact(
            conversation_id=conversation_id,
            filename="atnos-analytics-followup.md",
            content=note_content,
        )
        vault_note_path = self._write_obsidian_note(
            project_name=project_name,
            content=note_content,
        )
        if conversation_id is not None:
            self._attach_summary_message(
                conversation_id=conversation_id,
                project_name=project_name,
                title=title,
                video_id=video_id,
                analytics_summary=analytics_summary,
                strategy_snapshot=strategy_snapshot,
                note_artifact=note_artifact,
                vault_note_path=vault_note_path,
            )
        return {
            "status": "completed",
            "workflow": "youtube_publish_analytics_followup",
            "source_run_id": source_run_id,
            "video_id": video_id,
            "analytics_summary": analytics_summary,
            "strategy_snapshot": strategy_snapshot,
            "feedback_artifact": self._serialize_artifact(note_artifact),
            "vault_note_path": vault_note_path,
            "workflow_state": {
                "current_node": "graph.analytics_sync",
                "status": "done",
            },
        }

    def _fetch_analytics(self, *, video_id: str) -> dict[str, Any]:
        try:
            return self.youtube.fetch_analytics(video_id)
        except Exception as exc:
            return {
                "status": "unavailable",
                "video_id": video_id,
                "message": str(exc),
            }

    def _build_note_content(
        self,
        *,
        project_name: str,
        title: str | None,
        video_id: str,
        publish_result: dict[str, Any],
        analytics_summary: dict[str, Any],
        review_window_hours: int,
        strategy_snapshot: dict[str, Any],
    ) -> str:
        recommendation = self._build_recommendation(
            analytics_summary=analytics_summary,
            review_window_hours=review_window_hours,
        )
        return (
            f"# ATNOS analytics follow-up - {project_name}\n\n"
            f"- title: {title or 'brak'}\n"
            f"- video_id: {video_id}\n"
            f"- publish_status: {publish_result.get('status') or 'brak'}\n"
            f"- analytics_status: {analytics_summary.get('status') or 'brak'}\n"
            f"- recommendation: {recommendation}\n\n"
            "## Strategy snapshot\n\n"
            f"```json\n{json.dumps(strategy_snapshot, ensure_ascii=False, indent=2)}\n```\n\n"
            "## Analytics payload\n\n"
            f"```json\n{json.dumps(analytics_summary, ensure_ascii=False, indent=2)}\n```\n"
        )

    @staticmethod
    def _build_recommendation(
        *,
        analytics_summary: dict[str, Any],
        review_window_hours: int,
    ) -> str:
        status = str(analytics_summary.get("status") or "").strip().lower()
        if status == "ready":
            avp = analytics_summary.get("average_view_percentage")
            if isinstance(avp, (int, float)):
                if avp >= 85:
                    return "Retention jest mocny. Zachowaj hook i rytm, potem porownaj z kolejnym wariantem."
                if avp >= 65:
                    return "Retention jest sredni. Zostaw temat, ale skroc intro i podbij payoff w pierwszych sekundach."
            return "Metryki sa gotowe. Zapisz wynik do feedback loopa i porownaj hook z kolejnym shortem."
        if status == "pending":
            return f"Metryki jeszcze nie sa gotowe. Sprawdz ponownie po oknie {review_window_hours}h."
        return "Analytics nie sa jeszcze stabilne. Zachowaj pakiet publikacji i zrob kolejny follow-up."

    @staticmethod
    def _build_strategy_snapshot(
        *,
        analytics_summary: dict[str, Any],
        review_window_hours: int,
    ) -> dict[str, Any]:
        status = str(analytics_summary.get("status") or "").strip().lower()
        average_view_percentage = _coerce_float(analytics_summary.get("average_view_percentage"))
        views = _coerce_float(analytics_summary.get("views"))
        snapshot = {
            "status": status or "unknown",
            "hook_strategy": "wait_for_more_data",
            "pacing_strategy": "wait_for_more_data",
            "cutaway_strategy": "wait_for_more_data",
            "title_strategy": "wait_for_more_data",
            "next_batch_brief": f"Metryki nie sa gotowe. Poczekaj na okno {review_window_hours}h przed przepisywaniem batcha.",
        }
        if status == "ready":
            if isinstance(average_view_percentage, float):
                if average_view_percentage >= 85:
                    snapshot.update(
                        {
                            "hook_strategy": "keep_hook_profile",
                            "pacing_strategy": "maintain_pacing",
                            "cutaway_strategy": "maintain_cutaways",
                            "title_strategy": "keep_title_direction",
                            "next_batch_brief": "Retention jest mocny. Zachowaj hook i ogolny rytm, testuj tylko male wariacje stylu.",
                        }
                    )
                elif average_view_percentage >= 65:
                    snapshot.update(
                        {
                            "hook_strategy": "faster_hook",
                            "pacing_strategy": "tighter_intro",
                            "cutaway_strategy": "add_midbeat_cutaway",
                            "title_strategy": "test_hook_first_title",
                            "next_batch_brief": "Retention jest sredni. Skroc intro, wejdz szybciej w payoff i testuj bardziej front-loaded tytuly.",
                        }
                    )
                else:
                    snapshot.update(
                        {
                            "hook_strategy": "sharper_hook",
                            "pacing_strategy": "compress_body",
                            "cutaway_strategy": "stronger_cutaway_contrast",
                            "title_strategy": "hook_first_title",
                            "next_batch_brief": "Retention jest slaby. Dociagnij hook, tnij body i buduj mocniejszy kontrast cutawayow.",
                        }
                    )
            if isinstance(views, float) and views < 600:
                snapshot["title_strategy"] = "hook_first_title"
            snapshot["average_view_percentage"] = average_view_percentage
            snapshot["views"] = views
        elif status == "pending":
            snapshot["next_batch_brief"] = (
                f"Metryki sa jeszcze pending. Nie przepinaj plannerow na twardo przed follow-upem po {review_window_hours}h."
            )
        return snapshot

    def _write_markdown_artifact(
        self,
        *,
        conversation_id: int | None,
        filename: str,
        content: str,
    ) -> ManagedArtifactDescriptor:
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".md")
        output_path.write_text(content, encoding="utf-8")
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=filename,
            media_type="text/markdown",
        )

    def _write_obsidian_note(self, *, project_name: str, content: str) -> str | None:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists or not config.export_enabled:
            return None
        safe_project = self._slugify(project_name)
        stamp = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        relative = Path("Atnos") / "Social" / "Analytics" / f"{stamp}-analytics-followup-{safe_project}.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return relative.as_posix()

    def _attach_summary_message(
        self,
        *,
        conversation_id: int,
        project_name: str,
        title: str | None,
        video_id: str,
        analytics_summary: dict[str, Any],
        strategy_snapshot: dict[str, Any],
        note_artifact: ManagedArtifactDescriptor,
        vault_note_path: str | None,
    ) -> None:
        assistant_message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=(
                f"Sprawdzilem follow-up analytics dla `{project_name}`.\n\n"
                f"- title: {title or 'brak'}\n"
                f"- video_id: {video_id}\n"
                f"- analytics: {analytics_summary.get('status') or 'brak'}\n"
                f"- hook_strategy: {strategy_snapshot.get('hook_strategy') or 'brak'}\n"
                f"- vault_note: {vault_note_path or 'brak'}"
            ),
            metadata_json={
                "publish_analytics_followup": True,
                "video_id": video_id,
                "analytics_summary": analytics_summary,
                "strategy_snapshot": strategy_snapshot,
                "vault_note_path": vault_note_path,
            },
        )
        self.attachments.create(
            filename=note_artifact.filename,
            storage_path=note_artifact.storage_path,
            kind=AttachmentKind.TEXT,
            conversation_id=conversation_id,
            message_id=assistant_message.id,
            source=AttachmentSource.GENERATED,
            status=AttachmentStatus.AVAILABLE,
            media_type=note_artifact.media_type,
            file_extension=note_artifact.file_extension,
            size_bytes=note_artifact.size_bytes,
            extracted_text=None,
            metadata_json={"generated_by": "publish_analytics_followup", "project_name": project_name},
        )
        self.session.commit()

    @staticmethod
    def _serialize_artifact(artifact: ManagedArtifactDescriptor) -> dict[str, object]:
        return {
            "filename": artifact.filename,
            "storage_path": artifact.storage_path,
            "media_type": artifact.media_type,
            "file_extension": artifact.file_extension,
            "size_bytes": artifact.size_bytes,
        }

    @staticmethod
    def _slugify(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
        while "--" in cleaned:
            cleaned = cleaned.replace("--", "-")
        return cleaned.strip("-") or "project"


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None
