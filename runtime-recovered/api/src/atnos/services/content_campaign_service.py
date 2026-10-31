from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
import json
import re
from pathlib import Path
from typing import Any
import unicodedata

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import AttachmentKind, AttachmentSource, AttachmentStatus, MessageRole, MessageStatus, ModelTask, ProviderPosture
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.services.asset_generation_service import AssetGenerationService
from atnos.services.agent_quality_runtime_service import AgentQualityRuntimeService, QualityCheck
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.hardware_guard_service import HardwareGuardService
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeProbeService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor
from atnos.services.peer_consultation_service import PeerConsultationService
from atnos.services.piper_tts_service import PiperTtsService
from atnos.services.render_service import RenderArtifactResult, RenderService
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService
from atnos.services.workflow_reflection_service import WorkflowReflectionService
from atnos.services.workflow_llm_service import WorkflowLLMService
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService


@dataclass(frozen=True, slots=True)
class CampaignArtifact:
    kind: str
    descriptor: ManagedArtifactDescriptor
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "filename": self.descriptor.filename,
            "storage_path": self.descriptor.storage_path,
            "media_type": self.descriptor.media_type,
            "file_extension": self.descriptor.file_extension,
            "size_bytes": self.descriptor.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class CampaignApprovalPackage:
    title: str
    connector_key: str
    platform_key: str
    summary: str
    artifacts: dict[str, dict[str, Any]]
    metadata: dict[str, Any]
    render_qa: dict[str, Any]
    publish_mode: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "connector_key": self.connector_key,
            "platform_key": self.platform_key,
            "summary": self.summary,
            "artifacts": dict(self.artifacts),
            "metadata": dict(self.metadata),
            "render_qa": dict(self.render_qa),
            "publish_mode": self.publish_mode,
            "reason": self.reason,
        }


class ContentCampaignService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry
        self.execution_runs = ExecutionRunRepository(session)
        self.messages = MessageRepository(session)
        self.attachments = AttachmentRepository(session)
        self.browser_runtime = BrowserRuntimeService(settings)
        self.hardware_guard = HardwareGuardService(session, settings)
        self.reflector = WorkflowReflectionService(settings, self.hardware_guard)
        self.workflow_llm = WorkflowLLMService(
            session,
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
        )
        self.peer_consultation = PeerConsultationService(
            session,
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
        )
        self.local_short_runtime = LocalShortRuntimeProbeService(settings)
        self.quality_runtime = AgentQualityRuntimeService(settings)
        self.voice = PiperTtsService(settings)
        self.assets = AssetGenerationService(settings)
        self.subtitles = WhisperCppSubtitleService(settings)
        self.render = RenderService(settings)
        self.youtube = YouTubeConnectorService(session, settings)

    def run_youtube_short_campaign(
        self,
        *,
        run=None,
        job,
        controls,
        publish_allowed: bool,
        attach_message: bool = True,
    ) -> dict[str, Any]:
        payload = dict(job.payload_json or {})
        quality_profile = self._normalize_quality_profile(payload.get("quality_profile"))
        conversation_id = payload.get("conversation_id") if isinstance(payload.get("conversation_id"), int) else None
        brief = str(payload.get("brief") or job.goal or "").strip()
        project_slug = str(payload.get("project_slug") or f"project-{job.project_id}").strip()
        topic = str(payload.get("topic") or brief or job.goal).strip() or f"Projekt {project_slug}"
        publish_mode = self._normalize_publish_mode(payload.get("publish_mode"))
        local_render_only = publish_mode == "local_only"
        approved_for_publish = bool(payload.get("approved_for_publish"))
        peer_consultation_enabled = payload.get("peer_consultation", True) is not False
        artifacts: list[CampaignArtifact] = []
        fallback_used: dict[str, Any] | None = None
        peer_consultation_bundle: dict[str, Any] | None = None
        advisor_summary: str | None = None
        runtime_snapshot = self.local_short_runtime.snapshot()

        if not runtime_snapshot.ready:
            publish_result = {
                "status": "runtime_not_ready",
                "publish_path": "manual_upload_ready" if local_render_only else "blocked",
                "message": runtime_snapshot.reason,
            }
            analytics_summary = {
                "status": "skipped",
                "message": "Render nie wystartowal, bo lokalny runtime shorta nie jest gotowy.",
            }
            quality_run = self._record_runtime_not_ready_quality_run(
                topic=topic,
                runtime_snapshot=runtime_snapshot.to_dict(),
            )
            if attach_message:
                self._attach_runtime_not_ready_message(
                    conversation_id=conversation_id,
                    run=run,
                    topic=topic,
                    quality_profile=quality_profile,
                    runtime_snapshot=runtime_snapshot.to_dict(),
                    quality_run=quality_run,
                )
            return {
                "status": "runtime_not_ready",
                "workflow": "youtube_short_campaign",
                "topic": topic,
                "provider_key": "local-short-runtime",
                "model_key": (
                    self.settings.ollama_synthesis_model
                    or self.settings.ollama_chat_model
                    or "ollama-local"
                ),
                "routing_reason": "Local short workflow requires ready Windows-local media runtime and local Ollama.",
                "publish_result": publish_result,
                "analytics_summary": analytics_summary,
                "fallback_used": None,
                "peer_consultation": peer_consultation_bundle,
                "approval_required": False,
                "approval_package": None,
                "quality_profile": quality_profile,
                "workflow_state": self._build_workflow_state(
                    current_node="graph.runtime_probe",
                    status="runtime_not_ready",
                    total_steps=9,
                    completed_steps=0,
                ),
                "quality_run": quality_run,
                "runtime_readiness": runtime_snapshot.to_dict(),
                "artifacts": {
                    "browser_runtime": self.browser_runtime.describe().to_dict(),
                },
            }

        if peer_consultation_enabled:
            peer_consultation_bundle = self._run_step(
                run=run,
                step_type="graph.peer_consult",
                step_name="Peer consultation",
                input_json={"project_id": job.project_id, "topic": topic},
                fn=lambda: self.peer_consultation.run_project_consultation(
                    project_id=job.project_id,
                    conversation_id=conversation_id,
                    goal=brief or job.goal or topic,
                    topic_hint=topic,
                ),
            )
            peer_artifact = peer_consultation_bundle.get("artifact")
            if isinstance(peer_artifact, dict):
                artifacts.append(
                    CampaignArtifact(
                        "peer_consultation",
                        ManagedArtifactDescriptor(
                            filename=str(peer_artifact["filename"]),
                            storage_path=str(peer_artifact["storage_path"]),
                            absolute_path=self.render.artifacts.resolve_download_path(str(peer_artifact["storage_path"])),
                            media_type=str(peer_artifact["media_type"]),
                            file_extension=str(peer_artifact["file_extension"]),
                            size_bytes=int(peer_artifact["size_bytes"]),
                        ),
                        "Peer consultation",
                    )
                )
            action_priority = peer_consultation_bundle.get("merged_recommendations", {}).get("action_priority")
            if isinstance(action_priority, list) and action_priority:
                advisor_summary = "\n".join(f"- {str(item).strip()}" for item in action_priority if str(item).strip())

        research_bundle = self._run_step(
            run=run,
            step_type="graph.research",
            step_name="Research",
            input_json={"topic": topic, "brief": brief},
            fn=lambda: self._generate_research(topic=topic, brief=brief, advisor_summary=advisor_summary),
        )
        research_text = str(research_bundle["content"])
        research_artifact = self._write_text_artifact(
            conversation_id=conversation_id,
            kind="research",
            filename="atnos-research.md",
            media_type="text/markdown",
            content=research_text,
        )
        artifacts.append(CampaignArtifact("research", research_artifact, "Research notes"))
        fallback_used = self._fallback_from_provider(str(research_bundle["provider_key"]))

        script_bundle = self._run_step(
            run=run,
            step_type="graph.script",
            step_name="Script",
            input_json={"topic": topic},
            fn=lambda: self._generate_script(
                topic=topic,
                brief=brief,
                research=research_text,
                advisor_summary=advisor_summary,
            ),
        )
        if fallback_used is None:
            fallback_used = self._fallback_from_provider(str(script_bundle["provider_key"]))
        script_artifact = self._write_text_artifact(
            conversation_id=conversation_id,
            kind="script",
            filename="atnos-script.json",
            media_type="application/json",
            content=json.dumps(script_bundle["script"], ensure_ascii=False, indent=2),
        )
        artifacts.append(CampaignArtifact("script", script_artifact, "Structured script"))

        narration_descriptor = self._run_step(
            run=run,
            step_type="graph.tts",
            step_name="Piper TTS",
            input_json={"voiceover_chars": len(script_bundle["script"]["voiceover"])},
            fn=lambda: self.voice.synthesize(
                text=script_bundle["script"]["voiceover"],
                conversation_id=conversation_id,
            ),
        )
        artifacts.append(CampaignArtifact("audio", narration_descriptor, "Narration WAV"))

        generated_assets = self._run_step(
            run=run,
            step_type="graph.asset_gen",
            step_name="Asset generation",
            input_json={
                "scene_count": len(script_bundle["script"]["scenes"]),
                "quality_profile": quality_profile,
            },
            fn=lambda: self.assets.generate_story_assets(
                title=script_bundle["script"]["title"],
                topic=topic,
                scenes=script_bundle["script"]["scenes"],
                conversation_id=conversation_id,
                quality_profile=quality_profile,
            ),
        )
        for index, frame in enumerate(generated_assets.frames, start=1):
            artifacts.append(CampaignArtifact("frame", frame, f"Frame {index}"))
        artifacts.append(CampaignArtifact("thumbnail", generated_assets.thumbnail, "Thumbnail"))

        subtitles_descriptor = self._run_step(
            run=run,
            step_type="graph.subtitles",
            step_name="Subtitles",
            input_json={"subtitle_mode": "whisper_cpp_timed"},
            fn=lambda: self.subtitles.generate(
                audio_path=narration_descriptor.absolute_path,
                conversation_id=conversation_id,
            ),
        )
        artifacts.append(CampaignArtifact("subtitles", subtitles_descriptor, "Subtitles SRT"))

        render_result = self._render_campaign_with_reflection(
            run=run,
            frame_paths=[item.absolute_path for item in generated_assets.frames],
            audio_path=narration_descriptor.absolute_path,
            conversation_id=conversation_id,
            subtitles_path=subtitles_descriptor.absolute_path,
            quality_profile=quality_profile,
        )
        video_descriptor = render_result.descriptor
        artifacts.append(CampaignArtifact("video", video_descriptor, "YouTube Short MP4"))
        render_qa_artifact = self._write_text_artifact(
            conversation_id=conversation_id,
            kind="render_qa",
            filename="render_qa.json",
            media_type="application/json",
            content=json.dumps(render_result.qa.to_dict(), ensure_ascii=False, indent=2),
        )
        artifacts.append(CampaignArtifact("render_qa", render_qa_artifact, "Render QA"))

        metadata_bundle = self._run_step(
            run=run,
            step_type="graph.metadata",
            step_name="Metadata package",
            input_json={"topic": topic},
            fn=lambda: self._build_metadata(
                script_bundle=script_bundle["script"],
                topic=topic,
                research_text=research_text,
            ),
        )
        metadata_artifact = self._write_text_artifact(
            conversation_id=conversation_id,
            kind="metadata",
            filename="atnos-metadata.json",
            media_type="application/json",
            content=json.dumps(metadata_bundle, ensure_ascii=False, indent=2),
        )
        artifacts.append(CampaignArtifact("metadata", metadata_artifact, "Metadata package"))

        approval_package = self._run_step(
            run=run,
            step_type="graph.approval_gate",
            step_name="Approval gate",
            input_json={
                "publish_mode": publish_mode,
                "approved_for_publish": approved_for_publish,
            },
            fn=lambda: self._build_approval_package(
                script_bundle=script_bundle["script"],
                metadata_bundle=metadata_bundle,
                artifacts=artifacts,
                render_result=render_result,
                connector_key=job.connector_key or "youtube",
                platform_key=job.platform_key or "youtube",
                publish_mode=publish_mode,
            ),
        )

        if local_render_only:
            publish_result = {
                "status": "local_render_complete",
                "publish_path": "manual_upload_ready",
                "message": "Lokalny render jest gotowy. Upload i analytics sa poza tym workflowem.",
            }
            analytics_summary = {
                "status": "skipped",
                "message": "Analytics nie naleza do lokalnego workflowu shorta.",
            }
            artifact_payload = {item.kind: item.to_dict() for item in artifacts}
            artifact_payload["browser_runtime"] = self.browser_runtime.describe().to_dict()
            quality_run = self._record_local_render_quality_run(
                topic=topic,
                script_bundle=script_bundle["script"],
                render_qa=render_result.qa.to_dict(),
                artifacts=artifacts,
                publish_result=publish_result,
                quality_profile=quality_profile,
            )
            if attach_message:
                self._attach_run_summary_message(
                    conversation_id=conversation_id,
                    run=run,
                    script_bundle=script_bundle,
                    artifacts=artifacts,
                    publish_result=publish_result,
                    analytics_summary=analytics_summary,
                    quality_run=quality_run,
                )
            return {
                "status": "manual_upload_ready",
                "workflow": "youtube_short_campaign",
                "topic": topic,
                "provider_key": str(script_bundle["provider_key"]),
                "model_key": str(script_bundle["model_key"]),
                "routing_reason": str(script_bundle["routing_reason"]),
                "publish_result": publish_result,
                "analytics_summary": analytics_summary,
                "fallback_used": fallback_used,
                "peer_consultation": peer_consultation_bundle,
                "quality_profile": quality_profile,
                "approval_required": False,
                "approval_package": approval_package.to_dict(),
                "workflow_state": self._build_workflow_state(
                    current_node="graph.local_delivery",
                    status="completed",
                    total_steps=9,
                    completed_steps=9,
                ),
                "quality_run": quality_run,
                "runtime_readiness": runtime_snapshot.to_dict(),
                "artifacts": artifact_payload,
            }

        if publish_mode == "approval_required" and not approved_for_publish:
            publish_result = {
                "status": "awaiting_approval",
                "reason": "human_review_required",
                "message": "Campaign is ready for publish, but explicit human approval is still required.",
            }
            analytics_summary = {
                "status": "skipped",
                "message": "Analytics will run after publish approval and upload.",
            }
            if attach_message:
                self._attach_run_summary_message(
                    conversation_id=conversation_id,
                    run=run,
                    script_bundle=script_bundle,
                    artifacts=artifacts,
                    publish_result=publish_result,
                    analytics_summary=analytics_summary,
                )
            artifact_payload = {item.kind: item.to_dict() for item in artifacts}
            artifact_payload["browser_runtime"] = self.browser_runtime.describe().to_dict()
            return {
                "status": "awaiting_approval",
                "workflow": "youtube_short_campaign",
                "topic": topic,
                "provider_key": str(script_bundle["provider_key"]),
                "model_key": str(script_bundle["model_key"]),
                "routing_reason": str(script_bundle["routing_reason"]),
                "publish_result": publish_result,
                "analytics_summary": analytics_summary,
                "fallback_used": fallback_used,
                "peer_consultation": peer_consultation_bundle,
                "approval_required": True,
                "approval_package": approval_package.to_dict(),
                "quality_profile": quality_profile,
                "workflow_state": self._build_workflow_state(
                    current_node="graph.approval_gate",
                    status="awaiting_human",
                    total_steps=10,
                    completed_steps=8,
                ),
                "runtime_readiness": runtime_snapshot.to_dict(),
                "artifacts": artifact_payload,
            }

        publish_result = self._run_step(
            run=run,
            step_type="graph.upload",
            step_name="Upload",
            input_json={"connector_key": job.connector_key, "platform_key": job.platform_key},
            fn=lambda: self._publish_if_possible(
                job=job,
                controls=controls,
                publish_allowed=publish_allowed,
                video_descriptor=video_descriptor,
                thumbnail_descriptor=generated_assets.thumbnail,
                subtitles_descriptor=subtitles_descriptor,
                metadata_bundle=metadata_bundle,
            ),
        )

        analytics_summary = self._run_step(
            run=run,
            step_type="graph.analytics_sync",
            step_name="Analytics",
            input_json={"video_id": publish_result.get("video_id")},
            fn=lambda: self._analytics_if_possible(publish_result),
        )

        memory_summary = self._run_step(
            run=run,
            step_type="graph.memory_update",
            step_name="Memory update",
            input_json={"topic": topic},
            fn=lambda: self._build_memory_summary(
                topic=topic,
                metadata_bundle=metadata_bundle,
                publish_result=publish_result,
                analytics_summary=analytics_summary,
            ),
        )
        memory_artifact = self._write_text_artifact(
            conversation_id=conversation_id,
            kind="memory",
            filename="atnos-memory-update.md",
            media_type="text/markdown",
            content=memory_summary,
        )
        artifacts.append(CampaignArtifact("memory", memory_artifact, "Memory update"))

        if attach_message:
            self._attach_run_summary_message(
                conversation_id=conversation_id,
                run=run,
                script_bundle=script_bundle,
                artifacts=artifacts,
                publish_result=publish_result,
                analytics_summary=analytics_summary,
            )

        artifact_payload = {item.kind: item.to_dict() for item in artifacts}
        artifact_payload["browser_runtime"] = self.browser_runtime.describe().to_dict()
        return {
            "status": "completed",
            "workflow": "youtube_short_campaign",
            "topic": topic,
            "provider_key": str(script_bundle["provider_key"]),
            "model_key": str(script_bundle["model_key"]),
            "routing_reason": str(script_bundle["routing_reason"]),
            "publish_result": publish_result,
            "analytics_summary": analytics_summary,
            "fallback_used": fallback_used,
            "peer_consultation": peer_consultation_bundle,
            "quality_profile": quality_profile,
            "approval_required": False,
            "approval_package": approval_package.to_dict(),
            "workflow_state": self._build_workflow_state(
                current_node="graph.memory_update",
                status="done",
                total_steps=11,
                completed_steps=11,
            ),
            "runtime_readiness": runtime_snapshot.to_dict(),
            "artifacts": artifact_payload,
        }

    def publish_approved_campaign(
        self,
        *,
        source_run,
        publish_run,
        controls,
        publish_allowed: bool,
    ) -> dict[str, Any]:
        source_output = dict(source_run.output_json or {})
        approval_package = source_output.get("approval_package")
        if not isinstance(approval_package, dict):
            raise RuntimeError("Source campaign run has no approval package to publish.")

        source_artifacts = dict(source_run.artifacts_json or {})
        video_descriptor = self._descriptor_from_artifact(source_artifacts.get("video"))
        thumbnail_descriptor = self._descriptor_from_artifact(source_artifacts.get("thumbnail"))
        subtitles_descriptor = self._descriptor_from_artifact(source_artifacts.get("subtitles"))
        metadata_bundle = approval_package.get("metadata")
        if video_descriptor is None or thumbnail_descriptor is None or subtitles_descriptor is None:
            raise RuntimeError("Approved campaign run is missing publish artifacts.")
        if not isinstance(metadata_bundle, dict):
            raise RuntimeError("Approved campaign run is missing publish metadata.")

        self._run_step(
            run=publish_run,
            step_type="graph.approval_gate",
            step_name="Approval gate",
            input_json={"source_run_id": source_run.id},
            fn=lambda: {
                "status": "approved",
                "source_run_id": source_run.id,
                "message": "Human approval confirmed for publish.",
            },
        )
        publish_result = self._run_step(
            run=publish_run,
            step_type="graph.upload",
            step_name="Upload",
            input_json={
                "connector_key": source_run.connector_key,
                "platform_key": source_run.platform_key,
                "source_run_id": source_run.id,
            },
            fn=lambda: self._publish_if_possible(
                job=source_run,
                controls=controls,
                publish_allowed=publish_allowed,
                video_descriptor=video_descriptor,
                thumbnail_descriptor=thumbnail_descriptor,
                subtitles_descriptor=subtitles_descriptor,
                metadata_bundle=metadata_bundle,
            ),
        )
        analytics_summary = self._run_step(
            run=publish_run,
            step_type="graph.analytics_sync",
            step_name="Analytics",
            input_json={"video_id": publish_result.get("video_id")},
            fn=lambda: self._analytics_if_possible(publish_result),
        )
        memory_summary = self._run_step(
            run=publish_run,
            step_type="graph.memory_update",
            step_name="Memory update",
            input_json={"topic": source_output.get("topic") or source_run.goal},
            fn=lambda: self._build_memory_summary(
                topic=str(source_output.get("topic") or source_run.goal),
                metadata_bundle=metadata_bundle,
                publish_result=publish_result,
                analytics_summary=analytics_summary,
            ),
        )
        conversation_id = (
            source_run.input_json.get("conversation_id")
            if isinstance(source_run.input_json, dict) and isinstance(source_run.input_json.get("conversation_id"), int)
            else None
        )
        memory_artifact = self._write_text_artifact(
            conversation_id=conversation_id,
            kind="memory",
            filename="atnos-memory-update.md",
            media_type="text/markdown",
            content=memory_summary,
        )
        artifact_payload = dict(source_artifacts)
        artifact_payload["memory"] = {
            "filename": memory_artifact.filename,
            "storage_path": memory_artifact.storage_path,
            "media_type": memory_artifact.media_type,
            "file_extension": memory_artifact.file_extension,
            "size_bytes": memory_artifact.size_bytes,
        }
        artifact_payload["browser_runtime"] = self.browser_runtime.describe().to_dict()
        return {
            "status": "completed",
            "workflow": "youtube_short_campaign_publish",
            "source_run_id": source_run.id,
            "topic": source_output.get("topic") or source_run.goal,
            "publish_result": publish_result,
            "analytics_summary": analytics_summary,
            "approval_required": False,
            "approval_package": approval_package,
            "workflow_state": self._build_workflow_state(
                current_node="graph.memory_update",
                status="done",
                total_steps=3,
                completed_steps=3,
            ),
            "artifacts": artifact_payload,
        }

    def _generate_research(self, *, topic: str, brief: str, advisor_summary: str | None = None) -> dict[str, Any]:
        prompt = (
            "Jestes Atnosem i przygotowujesz research pod dopracowany polski YouTube Short.\n"
            "Zwracaj konkret, obraz, rytm i sensoryke. Bez korpomowy, bez pustych sloganow.\n\n"
            f"Temat: {topic}\n"
            f"Brief projektu: {brief or 'brak dodatkowego briefu'}\n\n"
            f"Konsultacja innych AI:\n{advisor_summary or '- brak dodatkowych wskazowek'}\n\n"
            "Zwroc:\n"
            "1. jeden najmocniejszy hook na pierwsze 2 sekundy,\n"
            "2. cztery konkretne momenty wizualne do pokazania w kadrze,\n"
            "3. jedno ryzyko nudy, ktore trzeba uciac,\n"
            "4. jedna sugestie dzwiekowa lub rytmiczna.\n"
            "Pisz po polsku bez polskich znakow diakrytycznych. Nie pisz o wiralnosci, emocjonalnym wyzwaniu ani zaangazowaniu. Ma byc konkret pod dobry short.\n"
        )
        try:
            result = self.workflow_llm.generate_text(
                prompt=prompt,
                task=ModelTask.SYNTHESIS,
                provider_posture=ProviderPosture.PREFER_LOCAL,
            )
            content = result.content
            provider_key = result.provider_key
            model_key = result.model_key
            routing_reason = result.routing_reason
        except Exception:
            fallback = self._manual_local_text(prompt=prompt, max_tokens=640)
            content = fallback["content"]
            provider_key = fallback["provider_key"]
            model_key = fallback["model_key"]
            routing_reason = fallback["routing_reason"]
        return {
            "content": content,
            "provider_key": provider_key,
            "model_key": model_key,
            "routing_reason": routing_reason,
        }

    def _generate_script(
        self,
        *,
        topic: str,
        brief: str,
        research: str,
        advisor_summary: str | None = None,
    ) -> dict[str, Any]:
        prompt = (
            "Napisz skrypt premium polskiego YouTube Shorta na okolo 28-40 sekund.\n"
            "Styl: prosty, konkretny, naturalny, z rytmem i mocnym obrazem.\n"
            "Zwracaj TYLKO poprawny JSON.\n"
            'Schemat: {"title":"", "description":"", "tags":[""], "voiceover":"", "thumbnail_text":"", "scenes":[{"headline":"","caption":"","visual":"","fruit":"","action":""}]}\n\n'
            f"Temat: {topic}\n"
            f"Brief: {brief or 'brak'}\n"
            f"Research:\n{research}\n"
            f"Konsultacja innych AI:\n{advisor_summary or '- brak dodatkowych wskazowek'}\n"
            "Reguly:\n"
            "- pisz po polsku bez polskich znakow diakrytycznych,\n"
            "- zero emoji,\n"
            "- zero hashtagow w voiceoverze,\n"
            "- zero tekstow typu: to wyzwanie, viral, zaangazowanie, emocjonalne, sztuka, ekspresja,\n"
            "- voiceover: 55-80 slow, zdania maja opisywac tylko to co widac lub slychac,\n"
            "- title: 3-7 slow,\n"
            "- thumbnail_text: 2-4 slowa,\n"
            "- sceny: 4 konkretne kadry, kazdy z widocznym obiektem, ruchem i tekstura,\n"
            "- headline na scene maks 3 slowa,\n"
            "- caption ma brzmiec jak profesjonalny opis kadru, nie jak prompt do modelu,\n"
            "- dla tematow typu niszczenie owocow uzywaj slow: trzask, sok, skorka, miazsz, rozprysk, ciecie, zblizenie, slow motion.\n"
        )
        try:
            result = self.workflow_llm.generate_text(
                prompt=prompt,
                task=ModelTask.SYNTHESIS,
                provider_posture=ProviderPosture.PREFER_LOCAL,
            )
            content = result.content
            provider_key = result.provider_key
            model_key = result.model_key
            routing_reason = result.routing_reason
        except Exception:
            fallback = self._manual_local_text(prompt=prompt, max_tokens=1024)
            content = fallback["content"]
            provider_key = fallback["provider_key"]
            model_key = fallback["model_key"]
            routing_reason = fallback["routing_reason"]
        bundle = self._parse_script_bundle(content, topic=topic)
        return {
            "script": bundle,
            "provider_key": provider_key,
            "model_key": model_key,
            "routing_reason": routing_reason,
        }

    @staticmethod
    def _parse_script_bundle(raw: str, *, topic: str) -> dict[str, Any]:
        cleaned = raw.strip()
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                payload = json.loads(json_match.group(0))
                if isinstance(payload, dict):
                    title = ContentCampaignService._sanitize_copy(str(payload.get("title") or topic).strip() or topic)
                    description = ContentCampaignService._sanitize_copy(str(payload.get("description") or title).strip())
                    voiceover = ContentCampaignService._limit_words(
                        ContentCampaignService._sanitize_voiceover(str(payload.get("voiceover") or description).strip()),
                        95,
                    )
                    tags = [
                        ContentCampaignService._sanitize_copy(str(item).strip())
                        for item in (payload.get("tags") or [])
                        if str(item).strip()
                    ]
                    raw_scenes = payload.get("scenes") or []
                    if isinstance(raw_scenes, list):
                        scenes = ContentCampaignService._normalize_scene_payload(raw_scenes)
                    elif isinstance(raw_scenes, str):
                        scenes = ContentCampaignService._parse_scene_blob(raw_scenes)
                    else:
                        scenes = []
                    if not scenes:
                        scenes = [
                            {
                                "headline": "Mocny start",
                                "caption": voiceover[:120],
                                "visual": voiceover[:120],
                                "fruit": "apple",
                                "action": "impact",
                            }
                        ]
                    bundle = {
                        "title": title,
                        "description": description,
                        "tags": tags[:12] or ["atnos", "shorts", "ai"],
                        "voiceover": voiceover or title,
                        "thumbnail_text": ContentCampaignService._sanitize_copy(str(payload.get("thumbnail_text") or title))[:42],
                        "scenes": scenes[:6],
                    }
                    if not ContentCampaignService._bundle_looks_broken(bundle):
                        return bundle
            except json.JSONDecodeError:
                pass

        labeled_bundle = ContentCampaignService._parse_labeled_bundle(cleaned, topic=topic)
        if labeled_bundle is not None:
            return labeled_bundle

        if ContentCampaignService._looks_like_fruit_topic(f"{topic} {cleaned}"):
            return ContentCampaignService._build_fruit_destruction_bundle(topic)

        fallback_lines = [line.strip("-• \n") for line in cleaned.splitlines() if line.strip()]
        voiceover = ContentCampaignService._limit_words(
            ContentCampaignService._sanitize_voiceover(" ".join(fallback_lines) or topic),
            95,
        )
        scenes = [
            {
                "headline": f"Kadr {index}",
                "caption": line,
                "visual": line,
                "fruit": "apple",
                "action": "impact",
            }
            for index, line in enumerate(fallback_lines[:4] or [voiceover], start=1)
        ]
        return {
            "title": ContentCampaignService._sanitize_copy(topic)[:100],
            "description": ContentCampaignService._sanitize_copy(voiceover)[:500],
            "tags": ["atnos", "shorts", "youtube"],
            "voiceover": voiceover,
            "scenes": scenes,
            "thumbnail_text": ContentCampaignService._sanitize_copy(topic)[:42],
        }

    @staticmethod
    def _parse_labeled_bundle(raw: str, *, topic: str) -> dict[str, Any] | None:
        compact = ContentCampaignService._sanitize_copy(ContentCampaignService._repair_text(raw))
        compact = re.sub(r"\bthumbnail\s+text\b", "thumbnail_text", compact, flags=re.IGNORECASE)
        match = re.search(
            r"['\"]?title['\"]?\s*[:,-]?\s*(.*?)\s+['\"]?description['\"]?\s*[:,-]?\s*(.*?)\s+['\"]?tags['\"]?\s*[:,-]?\s*(.*?)\s+['\"]?voiceover['\"]?\s*[:,-]?\s*(.*?)\s+['\"]?thumbnail_text['\"]?\s*[:,-]?\s*(.*?)\s+['\"]?scenes?['\"]?\s*[:,-]?\s*(.*)",
            compact,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            return None

        title, description, tags_blob, voiceover_blob, thumbnail_text, scenes_blob = (
            (match.group(index) or "").strip()
            for index in range(1, 7)
        )
        scenes = ContentCampaignService._parse_scene_blob(scenes_blob)
        if not scenes:
            return None
        bundle = {
            "title": ContentCampaignService._sanitize_copy(title or topic).strip(" ,;:-")[:100],
            "description": ContentCampaignService._sanitize_copy(description or title or topic)[:500],
            "tags": [
                ContentCampaignService._sanitize_copy(item).strip(" ,;:-")[:48]
                for item in re.split(r"[;,]", tags_blob)
                if ContentCampaignService._sanitize_copy(item).strip(" ,;:-")
            ][:12]
            or ["atnos", "shorts", "youtube"],
            "voiceover": ContentCampaignService._limit_words(
                ContentCampaignService._sanitize_voiceover(voiceover_blob or description or title or topic),
                95,
            ),
            "thumbnail_text": ContentCampaignService._sanitize_copy(thumbnail_text or title or topic).strip(" ,;:-")[:42],
            "scenes": scenes[:6],
        }
        if ContentCampaignService._bundle_looks_broken(bundle):
            return None
        return bundle

    @staticmethod
    def _bundle_looks_broken(bundle: dict[str, Any]) -> bool:
        title = str(bundle.get("title") or "")
        description = str(bundle.get("description") or "")
        voiceover = str(bundle.get("voiceover") or "")
        combined = " ".join(
            [
                title,
                description,
                voiceover,
                " ".join(str(scene.get("caption") or "") for scene in bundle.get("scenes", [])),
            ]
        ).lower()
        markers = (
            '"title"',
            '"description"',
            '"voiceover"',
            "{",
            "}",
            "[",
            "]",
            "thumbnail text",
            "thumbnail_text",
            "scenes",
            "voiceover ",
            "description ",
            "tags ",
        )
        if any(marker in combined for marker in markers):
            return True
        if title.lower().startswith(("title ", "description ", "voiceover ")):
            return True
        if description.lower().startswith(("title ", "description ", "voiceover ")):
            return True
        if voiceover.lower().startswith(("title ", "description ", "voiceover ")):
            return True
        if len(title.split()) > 8:
            return True
        if len(voiceover.split()) > 95:
            return True
        scenes = bundle.get("scenes", [])
        if not isinstance(scenes, list) or len(scenes) < 4:
            return True
        allowed_fruits = {"apple", "watermelon", "orange", "strawberry", "banana", "mango", "kiwi", "lemon"}
        allowed_actions = {"slice", "burst", "crush", "impact"}
        for scene in scenes:
            if not isinstance(scene, dict):
                return True
            fruit = str(scene.get("fruit") or "").lower()
            action = str(scene.get("action") or "").lower()
            if fruit not in allowed_fruits or action not in allowed_actions:
                return True
        return False

    @staticmethod
    def _looks_like_fruit_topic(value: str) -> bool:
        normalized = unicodedata.normalize("NFKD", value.lower()).encode("ascii", "ignore").decode("ascii")
        return any(
            keyword in normalized
            for keyword in (
                "owoc",
                "jabl",
                "arbuz",
                "pomarancz",
                "truskawk",
                "mango",
                "kiwi",
                "cytryn",
                "banana",
                "jelly",
                "galaret",
                "warstw",
                "reveal",
            )
        )

    @staticmethod
    def _build_fruit_destruction_bundle(topic: str) -> dict[str, Any]:
        normalized = unicodedata.normalize("NFKD", topic.lower()).encode("ascii", "ignore").decode("ascii")
        if any(keyword in normalized for keyword in ("mroz", "frozen", "ice", "lod")):
            return {
                "title": "Lod i trzask",
                "description": "Mrozone owoce pekaja twardo, glosno i czysto, z krystalicznym pyleniem i suchym trzaskiem.",
                "tags": ["mrozone", "owoce", "crack", "slowmotion", "shorts", "satisfying"],
                "voiceover": (
                    "Tu nie ma miekkiego miazszu na start. Najpierw slychac twardy, suchy trzask i dopiero potem "
                    "pojawia sie zimny pył, sok i ostre krawedzie pekniecia. Mrozony plaster pomaranczy strzela "
                    "jak szklo, truskawka kruszy sie pod naciskiem, a cytryna zostawia jasny slad lodowego soku. "
                    "Kazdy ruch jest chlodny, czysty i bardzo glosny."
                ),
                "thumbnail_text": "Lod i trzask",
                "scenes": [
                    {
                        "headline": "Pierwsze pekniecie",
                        "caption": "Zimna skorka pomaranczy peka ostro i wyrzuca drobny lodowy pyl.",
                        "visual": "Makro na mrozona pomarancze, suchy crack i lodowe drobiny w swietle.",
                        "fruit": "orange",
                        "action": "burst",
                    },
                    {
                        "headline": "Krucha truskawka",
                        "caption": "Mrozona truskawka lamie sie szorstko i rozsypuje czerwone drobinki.",
                        "visual": "Bardzo bliskie ujecie truskawki pod naciskiem, krysztalki lodu i kruszenie.",
                        "fruit": "strawberry",
                        "action": "impact",
                    },
                    {
                        "headline": "Cytryna pod nozem",
                        "caption": "Ostrze przecina lodowa cytryne i wypuszcza jasny, zimny slad soku.",
                        "visual": "Noz przechodzi przez mrozona cytryne w czystym, chlodnym swietle.",
                        "fruit": "lemon",
                        "action": "slice",
                    },
                    {
                        "headline": "Koncowy odlam",
                        "caption": "Ostatni kawalek peka glosno i zostawia na blacie lodowy proszek i mokry polysk.",
                        "visual": "Finalny crack, drobiny lodu i slow motion opadajacych kawalkow.",
                        "fruit": "apple",
                        "action": "crush",
                    },
                ],
            }
        if any(keyword in normalized for keyword in ("galaret", "jelly", "zel", "reveal", "warstw")):
            return {
                "title": "Warstwy i nacisk",
                "description": "Galaretka, owoce i nacisk buduja hipnotyczny reveal z mokrym połyskiem i czystymi warstwami.",
                "tags": ["galaretka", "reveal", "owoce", "press", "shorts", "satisfying"],
                "voiceover": (
                    "Najpierw widac tylko gladka, spokojna warstwe. Potem nacisk wchodzi wolno i wszystko zaczyna "
                    "falowac. Galaretka ugina sie jak szklo w wodzie, owoce przesuwaja sie pod powierzchnia, a "
                    "na koncu przychodzi mokry reveal z sokiem i rozlaniem koloru. To nie jest szybki cios. To jest "
                    "powolne, hipnotyczne zgniatanie, od ktorego trudno oderwac wzrok."
                ),
                "thumbnail_text": "Warstwy i nacisk",
                "scenes": [
                    {
                        "headline": "Gladki start",
                        "caption": "Idealnie gladka warstwa galaretki trzyma w srodku zamkniete owoce i odbija swiatlo.",
                        "visual": "Czyste premium ujecie warstwowej galaretki z owocami pod powierzchnia.",
                        "fruit": "strawberry",
                        "action": "impact",
                    },
                    {
                        "headline": "Pierwsze ugiecie",
                        "caption": "Nacisk schodzi powoli, a powierzchnia zaczyna falowac i rozchodzic sie na boki.",
                        "visual": "Makro nacisku na galaretke, fale i blysk mokrej tekstury.",
                        "fruit": "orange",
                        "action": "crush",
                    },
                    {
                        "headline": "Owoc pod warstwa",
                        "caption": "Owoc przesuwa sie pod naporem i rozmazuje kolor wewnatrz przezroczystej warstwy.",
                        "visual": "Bliskie ujecie owocu przesuwanego pod polprzezroczysta masa.",
                        "fruit": "mango",
                        "action": "burst",
                    },
                    {
                        "headline": "Mokry reveal",
                        "caption": "Na koncu warstwy pekaja i zostawiaja mokry slad, sok i czysty kolorystyczny payoff.",
                        "visual": "Finalne rozejscie warstw, polysk soku i kontrolowany rozprysk.",
                        "fruit": "watermelon",
                        "action": "impact",
                    },
                ],
            }
        return {
            "title": "Trzask i sok",
            "description": "Bliskie kadry pekajacej skorki, rozbryzgu soku i miazszu w mocnym slow motion.",
            "tags": ["owoce", "slowmotion", "trzask", "sok", "satisfying", "shorts"],
            "voiceover": (
                "Najpierw skorka napina sie pod ostrzem. Po chwili wchodzi suchy trzask i pierwszy rozprysk soku. "
                "Jablko peka na dwie czesci, pomarancza oddaje miazsz, a arbuz zostawia mokry slad na blacie. "
                "Kazdy kadr jest blisko, glosny i czysty. Tutaj liczy sie faktura, ciezar ciecia i ten moment, "
                "kiedy owoc doslownie eksploduje w slow motion."
            ),
            "thumbnail_text": "Trzask i sok",
            "scenes": [
                {
                    "headline": "Pierwszy trzask",
                    "caption": "Ostrze wbija sie w napieta skorke jablka i otwiera pierwszy jasny slad soku.",
                    "visual": "Close-up na jablko, blysk ostrza i suchy trzask skorki.",
                    "fruit": "apple",
                    "action": "slice",
                },
                {
                    "headline": "Rozprysk soku",
                    "caption": "Pomarancza oddaje sok szerokim lukiem, a krople lapia ostre swiatlo nad blatem.",
                    "visual": "Slow motion rozprysku z pomaranczy, gesty sok i mokra faktura miazszu.",
                    "fruit": "orange",
                    "action": "burst",
                },
                {
                    "headline": "Miazsz blisko",
                    "caption": "Czerwony arbuz peka szeroko i pokazuje ciezki miazsz, ktory rozchodzi sie od ciecia.",
                    "visual": "Bardzo bliskie ujecie miaszu arbuza i pekajacej linii skorki.",
                    "fruit": "watermelon",
                    "action": "crush",
                },
                {
                    "headline": "Czyste slowmo",
                    "caption": "Ostatnie krople opadaja wolno, a blat zostaje pokryty szklistym sladem soku i pestek.",
                    "visual": "Wolne opadanie kropli, pestki i odbicia swiatla na mokrej powierzchni.",
                    "fruit": "strawberry",
                    "action": "impact",
                },
            ],
        }

    def _manual_local_text(self, *, prompt: str, max_tokens: int) -> dict[str, str]:
        import requests

        model_name = (
            self.settings.ollama_document_model
            or self.settings.ollama_synthesis_model
            or self.settings.ollama_chat_model
            or "qwen3:8b"
        )
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=self.settings.ollama_timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, dict):
            content = str(message.get("content") or "").strip()
        else:
            content = str(payload.get("response") or "").strip() if isinstance(payload, dict) else ""
        return {
            "content": content,
            "provider_key": "ollama-local",
            "model_key": str(model_name),
            "routing_reason": "Direct requests fallback for local content generation.",
        }

    @staticmethod
    def _sanitize_copy(value: str) -> str:
        cleaned = re.sub(r"[#*`_]+", " ", ContentCampaignService._repair_text(value))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _sanitize_voiceover(value: str) -> str:
        cleaned = ContentCampaignService._sanitize_copy(value)
        cleaned = re.sub(r"#[^\s]+", "", cleaned)
        cleaned = re.sub(r"[^\w\s.,!?-]", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
        return cleaned

    @staticmethod
    def _limit_words(value: str, max_words: int) -> str:
        words = str(value or "").split()
        if max_words <= 0 or len(words) <= max_words:
            return str(value or "").strip()
        return " ".join(words[:max_words]).strip()

    @staticmethod
    def _repair_text(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        repaired = text
        suspicious_tokens = tuple(chr(value) for value in (194, 195, 196, 197, 226, 258, 313))
        if any(token in text for token in suspicious_tokens):
            for encoding in ("cp1252", "latin1"):
                try:
                    candidate = text.encode(encoding, errors="ignore").decode("utf-8", errors="ignore")
                except Exception:
                    continue
                if ContentCampaignService._text_noise_score(candidate) < ContentCampaignService._text_noise_score(repaired):
                    repaired = candidate
        repaired = repaired.replace("\u00e2\u20ac\u201c", "-").replace("\u00e2\u20ac\u201d", "-").replace("\u00e2\u20ac\u00a6", "...")
        return unicodedata.normalize("NFC", repaired)

    @staticmethod
    def _text_noise_score(value: str) -> int:
        penalties = tuple(chr(value) for value in (194, 195, 196, 197, 226, 258, 313, 65533, 63))
        return sum(value.count(token) for token in penalties)

    @staticmethod
    def _parse_scene_blob(raw: str) -> list[dict[str, str]]:
        scene_matches = list(
            re.finditer(
                r"['\"]?headline['\"]?\s*[:,-]?\s*(.*?)\s+['\"]?caption['\"]?\s*[:,-]?\s*(.*?)\s+['\"]?visual['\"]?\s*[:,-]?\s*(.*?)(?:\s+['\"]?fruit['\"]?\s*[:,-]?\s*(.*?))?(?:\s+['\"]?action['\"]?\s*[:,-]?\s*(.*?))?(?=\s+['\"]?headline['\"]?\s*[:,-]?|\s*$)",
                str(raw or ""),
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        if not scene_matches:
            return []
        return ContentCampaignService._normalize_scene_payload(
            [
                {
                    "headline": matched.group(1),
                    "caption": matched.group(2),
                    "visual": matched.group(3),
                    "fruit": matched.group(4) or "",
                    "action": matched.group(5) or "",
                }
                for matched in scene_matches
            ]
        )

    @staticmethod
    def _normalize_scene_payload(raw_scenes: list[Any]) -> list[dict[str, str]]:
        allowed_fruits = {"apple", "watermelon", "orange", "strawberry", "banana", "mango", "kiwi", "lemon"}
        allowed_actions = {"slice", "burst", "crush", "impact"}
        normalized: list[dict[str, str]] = []
        for index, item in enumerate(raw_scenes, start=1):
            if isinstance(item, dict):
                headline = ContentCampaignService._sanitize_copy(
                    str(item.get("headline") or item.get("caption") or f"Kadr {index}")
                ).strip(" ,;:-")[:42]
                caption = ContentCampaignService._sanitize_copy(str(item.get("caption") or item.get("visual") or headline))[:180]
                visual = ContentCampaignService._sanitize_copy(str(item.get("visual") or caption))[:220]
                fruit = ContentCampaignService._sanitize_copy(str(item.get("fruit") or "apple")).lower()[:32]
                action = ContentCampaignService._sanitize_copy(str(item.get("action") or "impact")).lower()[:32]
            else:
                text = ContentCampaignService._sanitize_copy(str(item))
                headline = text.strip(" ,;:-")[:42] or f"Kadr {index}"
                caption = text[:180] or headline
                visual = text[:220] or caption
                fruit = "apple"
                action = "impact"
            if fruit not in allowed_fruits:
                fruit = "apple"
            if action not in allowed_actions:
                action = "impact"
            normalized.append(
                {
                    "headline": headline,
                    "caption": caption,
                    "visual": visual,
                    "fruit": fruit,
                    "action": action,
                }
            )
        return normalized

    def _build_metadata(self, *, script_bundle: dict[str, Any], topic: str, research_text: str) -> dict[str, Any]:
        title = str(script_bundle["title"])[:100]
        description = (
            str(script_bundle["description"])[:4000]
            + "\n\n"
            + "Wyprodukowane lokalnie przez ATNOS."
        )[:5000]
        tags = [str(item)[:30] for item in script_bundle.get("tags", []) if str(item).strip()][:12]
        return {
            "title": title,
            "description": description,
            "tags": tags or ["atnos", "youtube", "shorts"],
            "topic": topic,
            "research_excerpt": research_text[:600],
            "privacy_status": "private",
            "publish_requested_at": datetime.now(tz=UTC).isoformat(),
        }

    def _publish_if_possible(
        self,
        *,
        job,
        controls,
        publish_allowed: bool,
        video_descriptor: ManagedArtifactDescriptor,
        thumbnail_descriptor: ManagedArtifactDescriptor,
        subtitles_descriptor: ManagedArtifactDescriptor,
        metadata_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        if not controls.publish_automation_enabled:
            return {
                "status": "skipped",
                "reason": "publish_automation_disabled",
                "message": "Publish automation is disabled in runtime controls.",
            }
        if not publish_allowed:
            return {
                "status": "skipped",
                "reason": "trust_grant_publish_blocked",
                "message": "Trust grant does not allow publish.",
            }
        normalized_connector = str(job.connector_key or "youtube-studio").strip().lower()
        if normalized_connector not in {"youtube", "youtube-studio"}:
            return {
                "status": "skipped",
                "reason": "unsupported_connector",
                "message": f"Campaign publish currently supports only YouTube, not {job.connector_key}.",
            }
        try:
            status = self.youtube.probe()
        except Exception as exc:
            return {
                "status": "skipped",
                "reason": "youtube_probe_failed",
                "message": str(exc),
            }
        if not status.ready:
            return {
                "status": "skipped",
                "reason": "youtube_not_ready",
                "message": "; ".join(status.warnings) or "YouTube connector is not ready.",
                "connector": status.to_dict(),
            }
        try:
            upload = self.youtube.upload_short(
                video_path=video_descriptor.absolute_path,
                title=str(metadata_bundle["title"]),
                description=str(metadata_bundle["description"]),
                tags=[str(item) for item in metadata_bundle.get("tags", [])],
                thumbnail_path=thumbnail_descriptor.absolute_path,
                captions_path=subtitles_descriptor.absolute_path,
                privacy_status=str(metadata_bundle.get("privacy_status") or "private"),
            )
        except YouTubeConnectorError as exc:
            return {
                "status": "failed",
                "reason": "youtube_upload_failed",
                "message": str(exc),
            }
        upload_payload = dict(upload)
        video_status = upload_payload.pop("status", None)
        return {
            "status": "uploaded",
            **upload_payload,
            "video_status": video_status,
            "connector": status.to_dict(),
        }

    def _analytics_if_possible(self, publish_result: dict[str, Any]) -> dict[str, Any]:
        video_id = publish_result.get("video_id")
        if not isinstance(video_id, str) or not video_id.strip():
            return {
                "status": "skipped",
                "message": "Analytics skipped because no video_id is available.",
            }
        try:
            return self.youtube.fetch_analytics(video_id)
        except Exception as exc:
            return {
                "status": "unavailable",
                "message": str(exc),
                "video_id": video_id,
            }

    def _build_approval_package(
        self,
        *,
        script_bundle: dict[str, Any],
        metadata_bundle: dict[str, Any],
        artifacts: list[CampaignArtifact],
        render_result: RenderArtifactResult,
        connector_key: str,
        platform_key: str,
        publish_mode: str,
    ) -> CampaignApprovalPackage:
        artifact_payload = {item.kind: item.to_dict() for item in artifacts}
        title = str(script_bundle.get("title") or metadata_bundle.get("title") or "ATNOS short")
        summary = (
            f"Tytul: {title}\n"
            f"Hook: {str(script_bundle.get('hook') or '').strip()}\n"
            f"CTA: {str(script_bundle.get('cta') or '').strip()}\n"
            f"Tagi: {', '.join(str(item) for item in metadata_bundle.get('tags', []))}"
        ).strip()
        return CampaignApprovalPackage(
            title=title,
            connector_key=connector_key,
            platform_key=platform_key,
            summary=summary,
            artifacts=artifact_payload,
            metadata=metadata_bundle,
            render_qa=render_result.qa.to_dict(),
            publish_mode=publish_mode,
            reason=(
                "Local render is complete. Review the assets and upload manually when ready."
                if publish_mode == "local_only"
                else "Explicit human approval is required before official YouTube upload."
            ),
        )

    @staticmethod
    def _normalize_publish_mode(value: Any) -> str:
        normalized = str(value or "approval_required").strip().lower()
        if normalized in {"trusted_auto", "approval_required", "local_only"}:
            return normalized
        return "approval_required"

    @staticmethod
    def _build_workflow_state(
        *,
        current_node: str,
        status: str,
        total_steps: int,
        completed_steps: int,
    ) -> dict[str, Any]:
        return {
            "current_node": current_node,
            "status": status,
            "total_steps": total_steps,
            "completed_steps": completed_steps,
            "eta_seconds": None,
        }

    def _render_campaign_with_reflection(
        self,
        *,
        run,
        frame_paths: list[Path],
        audio_path: Path,
        conversation_id: int | None,
        subtitles_path: Path | None,
        quality_profile: str,
    ) -> RenderArtifactResult:
        render_profile = self._render_profile_for_quality(quality_profile)
        for attempt in range(1, 4):
            try:
                return self._run_step(
                    run=run,
                    step_type="graph.render",
                    step_name="Render",
                    input_json={
                        "frame_count": len(frame_paths),
                        "attempt": attempt,
                        "render_profile": render_profile,
                    },
                    fn=lambda: self.render.render_short(
                        frame_paths=frame_paths,
                        audio_path=audio_path,
                        conversation_id=conversation_id,
                        subtitles_path=subtitles_path,
                        render_profile=render_profile,
                    ),
                )
            except Exception as exc:
                plan = self.reflector.reflect_render_failure(
                    stage_key="campaign_render",
                    error_message=str(exc),
                    attempt=attempt,
                    current_render_profile=render_profile,
                )
                if plan is None or not plan.retry_allowed:
                    raise
                self._run_step(
                    run=run,
                    step_type="graph.reflector",
                    step_name="Reflector",
                    input_json={"source_step": "graph.render", "attempt": attempt},
                    fn=lambda: plan.to_dict(),
                )
                render_profile = str(plan.adjustments.get("render_profile") or render_profile)
        raise RuntimeError("Campaign render reflector exhausted its retry budget.")

    def _record_runtime_not_ready_quality_run(
        self,
        *,
        topic: str,
        runtime_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return self.quality_runtime.record_run(
            task=f"Content campaign short: {topic}",
            area="content_campaign",
            checks=[
                QualityCheck(
                    name="runtime_readiness",
                    status="fail",
                    evidence=str(runtime_snapshot.get("reason") or "Local short runtime is not ready."),
                    score=0.0,
                    required=True,
                )
            ],
            artifacts=[],
            notes="Content campaign stopped before generation because local media runtime was not ready.",
            min_score=float(self.settings.shorts_min_quality_score),
        )

    def _record_local_render_quality_run(
        self,
        *,
        topic: str,
        script_bundle: dict[str, Any],
        render_qa: dict[str, Any],
        artifacts: list[CampaignArtifact],
        publish_result: dict[str, Any],
        quality_profile: str,
    ) -> dict[str, Any]:
        checks = [
            self._script_bundle_quality_check(script_bundle),
            self._render_artifact_quality_check(render_qa),
            QualityCheck(
                name="local_delivery",
                status="pass" if publish_result.get("publish_path") == "manual_upload_ready" else "fail",
                evidence=str(publish_result.get("message") or publish_result.get("status") or ""),
                score=10.0 if publish_result.get("publish_path") == "manual_upload_ready" else 0.0,
                required=True,
            ),
        ]
        artifact_paths = [
            artifact.descriptor.absolute_path.as_posix()
            for artifact in artifacts
            if artifact.descriptor.absolute_path is not None
        ]
        return self.quality_runtime.record_run(
            task=f"Content campaign short: {topic}",
            area="content_campaign",
            checks=checks,
            artifacts=artifact_paths,
            notes=f"quality_profile={quality_profile}; local render completed without upload.",
            min_score=float(self.settings.shorts_min_quality_score),
        )

    @staticmethod
    def _script_bundle_quality_check(script_bundle: dict[str, Any]) -> QualityCheck:
        title = str(script_bundle.get("title") or "").strip()
        voiceover = str(script_bundle.get("voiceover") or "").strip()
        scenes = script_bundle.get("scenes")
        scene_count = len(scenes) if isinstance(scenes, list) else 0
        word_count = len(re.findall(r"\w+", voiceover, flags=re.UNICODE))
        issues: list[str] = []
        if not title:
            issues.append("missing title")
        if word_count < 25:
            issues.append("voiceover too short")
        if word_count > 150:
            issues.append("voiceover too long")
        if scene_count < 4:
            issues.append("less than four scenes")
        score = 10.0 - len(issues) * 2.5
        return QualityCheck(
            name="script_package",
            status="pass" if not issues else "warn" if score >= 7 else "fail",
            evidence=(
                f"title={title or 'n/a'}; voiceover_words={word_count}; scenes={scene_count}"
                if not issues
                else "Script package issues: " + ", ".join(issues)
            ),
            score=max(0.0, score),
            required=True,
        )

    @staticmethod
    def _render_artifact_quality_check(render_qa: dict[str, Any]) -> QualityCheck:
        warnings = [str(item) for item in (render_qa.get("warnings") or [])]
        width = int(render_qa.get("width") or 0)
        height = int(render_qa.get("height") or 0)
        has_audio = bool(render_qa.get("has_audio"))
        size_bytes = int(render_qa.get("size_bytes") or 0)
        issues: list[str] = []
        if width != 1080 or height != 1920:
            issues.append(f"unexpected resolution {width}x{height}")
        if not has_audio:
            issues.append("missing audio")
        if size_bytes <= 0:
            issues.append("empty mp4")
        issues.extend(warnings)
        score = 10.0
        if width != 1080 or height != 1920:
            score -= 2.5
        if not has_audio:
            score -= 4.0
        if size_bytes <= 0:
            score -= 4.0
        if warnings:
            score -= min(3.0, len(warnings) * 1.0)
        score = max(0.0, round(score, 2))
        return QualityCheck(
            name="render_quality",
            status="pass" if not issues else "warn" if score >= 7 else "fail",
            evidence=(
                f"resolution={width}x{height}; audio={has_audio}; size_bytes={size_bytes}"
                if not issues
                else "Render QA issues: " + "; ".join(issues[:8])
            ),
            score=score,
            required=True,
        )

    @staticmethod
    def _normalize_quality_profile(value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"premium", "fast-turnaround", "standard"}:
            return normalized
        return "standard"

    @staticmethod
    def _render_profile_for_quality(quality_profile: str) -> str:
        normalized = ContentCampaignService._normalize_quality_profile(quality_profile)
        if normalized == "fast-turnaround":
            return "balanced"
        return "quality"

    def _descriptor_from_artifact(self, payload: Any) -> ManagedArtifactDescriptor | None:
        if not isinstance(payload, dict):
            return None
        storage_path = payload.get("storage_path")
        filename = payload.get("filename")
        media_type = payload.get("media_type")
        file_extension = payload.get("file_extension")
        size_bytes = payload.get("size_bytes")
        if not all(isinstance(item, str) for item in (storage_path, filename, media_type, file_extension)):
            return None
        if not isinstance(size_bytes, int):
            return None
        return ManagedArtifactDescriptor(
            filename=filename,
            storage_path=storage_path,
            absolute_path=self.render.artifacts.resolve_download_path(storage_path),
            media_type=media_type,
            file_extension=file_extension,
            size_bytes=size_bytes,
        )

    @staticmethod
    def _build_memory_summary(
        *,
        topic: str,
        metadata_bundle: dict[str, Any],
        publish_result: dict[str, Any],
        analytics_summary: dict[str, Any],
    ) -> str:
        return (
            f"# ATNOS campaign memory\n\n"
            f"- topic: {topic}\n"
            f"- title: {metadata_bundle.get('title')}\n"
            f"- publish_status: {publish_result.get('status')}\n"
            f"- video_id: {publish_result.get('video_id') or 'n/a'}\n"
            f"- analytics_status: {analytics_summary.get('status')}\n"
            f"- recommendation: "
            f"{'trzymaj ten format i sprawdz wyniki po 48h' if analytics_summary.get('status') != 'ready' else 'zapisz wyniki do feedback loop i porownaj hook z następnym shortem'}\n"
        )

    def _run_step(
        self,
        *,
        run,
        step_type: str,
        step_name: str,
        input_json: dict[str, Any],
        fn,
    ):
        if run is None:
            return fn()
        steps = self.execution_runs.list_steps(run.id)
        step = self.execution_runs.create_step(
            execution_run_id=run.id,
            step_index=len(steps),
            step_type=step_type,
            step_name=step_name,
            status="running",
            selector_hint=None,
            input_json=input_json,
            output_json={},
            error_message=None,
            screenshot_path=None,
            started_at=datetime.now(tz=UTC),
            finished_at=None,
        )
        self.session.commit()
        try:
            result = fn()
        except Exception as exc:
            step.status = "failed"
            step.error_message = str(exc)
            step.finished_at = datetime.now(tz=UTC)
            self.execution_runs.save_step(step)
            self.session.commit()
            raise
        try:
            output = self._serialize_step_output(result)
            step.status = "done"
            step.output_json = output if isinstance(output, dict) else {"value": output}
            step.finished_at = datetime.now(tz=UTC)
            self.execution_runs.save_step(step)
            self.session.commit()
        except Exception as exc:
            step.status = "failed"
            step.output_json = {}
            step.error_message = str(exc)
            step.finished_at = datetime.now(tz=UTC)
            self.execution_runs.save_step(step)
            self.session.commit()
            raise
        return result

    def _serialize_step_output(self, value: Any) -> Any:
        if isinstance(value, ManagedArtifactDescriptor):
            return {
                "filename": value.filename,
                "storage_path": value.storage_path,
                "media_type": value.media_type,
                "file_extension": value.file_extension,
                "size_bytes": value.size_bytes,
            }
        if isinstance(value, Path):
            return value.as_posix()
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "to_dict"):
            return self._serialize_step_output(value.to_dict())
        if is_dataclass(value):
            return {
                field.name: self._serialize_step_output(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, dict):
            return {str(key): self._serialize_step_output(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._serialize_step_output(item) for item in value]
        if hasattr(value, "__dict__"):
            return {
                str(key): self._serialize_step_output(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return value

    def _write_text_artifact(
        self,
        *,
        conversation_id: int | None,
        kind: str,
        filename: str,
        media_type: str,
        content: str,
    ) -> ManagedArtifactDescriptor:
        extension = Path(filename).suffix.lower() or ".txt"
        output_path = self.render.artifacts.create_output_path(conversation_id=conversation_id, extension=extension)
        output_path.write_text(content, encoding="utf-8")
        return self.render.artifacts.finalize_output(
            absolute_path=output_path,
            filename=filename,
            media_type=media_type,
        )

    def _attach_run_summary_message(
        self,
        *,
        conversation_id: int | None,
        run,
        script_bundle: dict[str, Any],
        artifacts: list[CampaignArtifact],
        publish_result: dict[str, Any],
        analytics_summary: dict[str, Any],
        quality_run: dict[str, Any] | None = None,
    ) -> None:
        if conversation_id is None:
            return
        script = script_bundle["script"]
        publish_status = str(publish_result.get("status") or "")
        publish_path = str(publish_result.get("publish_path") or "")
        awaiting_approval = publish_status == "awaiting_approval"
        local_delivery_ready = publish_status == "local_render_complete" or publish_path == "manual_upload_ready"
        assistant_message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=(
                f"Przygotowalem short `{script['title']}`.\n\n"
                f"- render: {publish_status}\n"
                f"- publish_path: {publish_path or 'n/a'}\n"
                f"- analytics: {analytics_summary.get('status')}\n"
                f"- artefakty: {len(artifacts)}"
            ),
            metadata_json={
                "triggered_by_run_id": getattr(run, "id", None),
                "campaign_title": script["title"],
                "publish_result": publish_result,
                "analytics_summary": analytics_summary,
                "creative_video_operator": {
                    "workflow_key": "content_campaign",
                    "platform_hint": getattr(run, "platform_key", None) or "youtube",
                    "quality_profile": (
                        str(run.input_json.get("quality_profile") or "standard")
                        if hasattr(run, "input_json") and isinstance(run.input_json, dict)
                        else "standard"
                    ),
                    "workflow_state": {
                        "current_node": (
                            "graph.approval_gate"
                            if awaiting_approval
                            else "graph.local_delivery"
                            if local_delivery_ready
                            else "graph.memory_update"
                        ),
                        "status": (
                            "awaiting_human"
                            if awaiting_approval
                            else "completed"
                        ),
                    },
                    "approval_required": awaiting_approval,
                    "artifact_count": len(artifacts),
                    "publish_path": publish_path or None,
                    "quality_run": quality_run,
                    "next_step": (
                        "Sprawdz material i zdecyduj, czy publikowac."
                        if awaiting_approval
                        else "Artefakty sa gotowe lokalnie. Zrob review i wrzuc recznie, kiedy bedziesz gotowy."
                        if local_delivery_ready
                        else "Artefakty sa gotowe. Mozesz przejsc do review albo publikacji."
                    ),
                },
            },
        )
        for artifact in artifacts:
            attachment_kind = self._attachment_kind_for_extension(artifact.descriptor.file_extension)
            self.attachments.create(
                filename=artifact.descriptor.filename,
                storage_path=artifact.descriptor.storage_path,
                kind=attachment_kind,
                conversation_id=conversation_id,
                message_id=assistant_message.id,
                source=AttachmentSource.GENERATED,
                status=AttachmentStatus.AVAILABLE,
                media_type=artifact.descriptor.media_type,
                file_extension=artifact.descriptor.file_extension,
                size_bytes=artifact.descriptor.size_bytes,
                extracted_text=None,
                metadata_json={"generated_by": "campaign", "label": artifact.label},
            )
        self.session.commit()

    def _attach_runtime_not_ready_message(
        self,
        *,
        conversation_id: int | None,
        run,
        topic: str,
        quality_profile: str,
        runtime_snapshot: dict[str, Any],
        quality_run: dict[str, Any] | None = None,
    ) -> None:
        if conversation_id is None:
            return
        reason = str(runtime_snapshot.get("reason") or "Local short runtime is not ready.")
        assistant_message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=(
                f"Nie uruchomilem shorta `{topic}`.\n\n"
                f"Blokada runtime: {reason}\n"
                "Najpierw przygotuj Piper, whisper.cpp, Ollame i FFmpeg, potem uruchom short ponownie."
            ),
            metadata_json={
                "triggered_by_run_id": getattr(run, "id", None),
                "creative_video_operator": {
                    "workflow_key": "content_campaign",
                    "platform_hint": getattr(run, "platform_key", None) or "youtube",
                    "quality_profile": quality_profile,
                    "workflow_state": {
                        "current_node": "graph.runtime_probe",
                        "status": "runtime_not_ready",
                    },
                    "blocked": True,
                    "approval_required": False,
                    "artifact_count": 0,
                    "quality_run": quality_run,
                    "blocker": reason,
                    "next_step": "Skonfiguruj lokalny runtime shorta i sprobuj ponownie.",
                    "runtime_readiness": runtime_snapshot,
                },
            },
        )
        self.session.commit()

    @staticmethod
    def _attachment_kind_for_extension(extension: str) -> AttachmentKind:
        normalized = extension.lower()
        if normalized in {".png", ".jpg", ".jpeg", ".webp"}:
            return AttachmentKind.IMAGE
        if normalized in {".wav", ".mp3"}:
            return AttachmentKind.AUDIO
        if normalized in {".mp4", ".mov"}:
            return AttachmentKind.VIDEO
        if normalized in {".md", ".txt", ".json", ".srt"}:
            return AttachmentKind.TEXT
        return AttachmentKind.DOCUMENT

    @staticmethod
    def _fallback_from_provider(provider_key: str) -> dict[str, Any] | None:
        if provider_key.startswith("ollama") or provider_key.startswith("llamacpp"):
            return None
        return {
            "type": "cloud_llm",
            "provider_key": provider_key,
            "message": f"Workflow used non-local provider {provider_key} as fallback.",
        }
