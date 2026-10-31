from __future__ import annotations

from dataclasses import dataclass, field
import re
from types import SimpleNamespace
import unicodedata

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry
from atnos.services.content_campaign_service import ContentCampaignService
from atnos.services.local_video_generation_service import (
    LocalVideoGenerationService,
    VideoGenerationError,
)
from atnos.services.output_artifact_service import OutputArtifactService
from atnos.services.trusted_auto_service import TrustedAutoService
from atnos.skills.content_short_skill import (
    detect_local_polish_short_request,
    extract_short_topic,
    format_short_agent_response,
)
from content.short_agent import ShortAgent


@dataclass(frozen=True, slots=True)
class CreativeVideoRequest:
    raw_content: str
    brief: str
    workflow_key: str
    platform_hint: str


@dataclass(frozen=True, slots=True)
class CreativeVideoOperationResult:
    activated: bool
    assistant_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    selected_provider_key: str | None = None
    selected_model_key: str | None = None
    routing_reason: str | None = None
    artifacts: tuple[dict[str, object], ...] = ()
    kick_scheduler: bool = False


class CreativeVideoOperatorService:
    ACTION_HINTS = (
        "zlec",
        "zrob",
        "stworz",
        "wygeneruj",
        "przygotuj",
        "nagraj",
        "wyrenderuj",
        "make",
        "create",
        "generate",
        "render",
    )
    REQUEST_PHRASES = (
        "chce shorta",
        "chce wideo",
        "chce filmik",
        "potrzebuje shorta",
        "potrzebuje wideo",
        "potrzebuje filmiku",
        "poprosze shorta",
        "poprosze wideo",
        "poprosze filmik",
    )
    VIDEO_HINTS = (
        "wideo",
        "video",
        "filmik",
        "film",
        "short",
        "shorta",
        "shorty",
        "shorts",
        "rolka",
        "rolke",
        "reel",
        "reels",
        "klip",
        "clip",
        "teaser video",
        "youtube short",
    )
    SYNTHETIC_CREATOR_HINTS = (
        "talking head",
        "avatar",
        "synthetic creator",
        "synthetic influencer",
        "wirtualna modelka",
        "modelka ai",
    )
    LEADING_PHRASE_PREFIXES = (
        "atnos ",
        "atnos, ",
        "atnos: ",
    )
    GENERIC_BRIEF_VALUES = {
        "",
        "wideo",
        "video",
        "film",
        "filmik",
        "short",
        "shorta",
        "shorts",
        "rolka",
        "rolke",
        "reel",
        "reels",
        "klip",
        "clip",
    }

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry

    def maybe_handle(
        self,
        *,
        content: str,
        conversation_id: int | None,
        project_id: int | None,
        allow_local_file_write: bool,
        quality_profile: str = "standard",
        runtime_controls=None,
    ) -> CreativeVideoOperationResult:
        request = self._detect_request(content)
        if request is None:
            return CreativeVideoOperationResult(activated=False)
        # WB already has a canonical, guarded ProductionOrder intake. Never route
        # a named channel through legacy campaign defaults (random project/Piper).
        normalized = self._normalize_text(content)
        if "wealth blueprint" in normalized or re.search(r"\bwb\b", normalized):
            return CreativeVideoOperationResult(
                activated=True,
                assistant_content=(
                    "Przygotowanie rolki Wealth Blueprint wymaga normalnego zlecenia w Studiu. "
                    "Nie uruchomiłem starszego procesu content_campaign ani nie zmieniłem głosu na Piper.\n"
                    "[Otwórz produkcję WB](/create) — wybierz profil WB, grafikę i zatwierdzony głos. "
                    "Ryan B wymaga uprawnienia przypisanego do konkretnego zlecenia; nie zastępuję go płatnym głosem."
                ),
                metadata={"creative_video_operator": {"workflow_key": "production_order",
                    "status": "requires_production_intake", "project": "wealth_blueprint",
                    "brief": content, "handoff_url": "/create", "worker_started": False}},
                selected_provider_key="creative-video-operator", selected_model_key="production-order-intake",
                routing_reason="Named WB request uses canonical production intake, never legacy campaign defaults.",
            )
        if not allow_local_file_write:
            return self._build_blocked_result(
                request=request,
                blocker="Local file write jest wylaczony, wiec nie moge zapisac wygenerowanego wideo.",
                next_step="Wlacz local file write dla tej rozmowy albo globalnie.",
            )
        if request.brief in self.GENERIC_BRIEF_VALUES:
            return self._build_blocked_result(
                request=request,
                blocker="Brakuje konkretnego briefu do shorta lub wideo.",
                next_step="Napisz o czym ma byc short, jaki ma miec klimat i co ma pokazywac.",
            )
        if request.workflow_key != "content_campaign":
            if request.workflow_key == "local_video_generation":
                return self._run_local_video_generation_now(
                    request=request,
                    conversation_id=conversation_id,
                )
            return self._build_blocked_result(
                request=request,
                blocker="Ten typ wideo nie ma jeszcze gotowego lane z poziomu glownego Chatu.",
                next_step="Na razie popros o short lub reel oparty na zwyklym content campaign workflow.",
            )
        if detect_local_polish_short_request(content):
            return self._run_local_polish_short_now(request=request, content=content)

        if runtime_controls is not None:
            if getattr(runtime_controls, "scheduler_enabled", True) is False:
                return self._build_blocked_result(
                    request=request,
                    blocker="Scheduler runtime jest wylaczony, wiec nie moge uruchomic workflow shorta w tle.",
                    next_step="Wlacz scheduler runtime w ustawieniach i sprobuj ponownie.",
                )
            if getattr(runtime_controls, "emergency_stop_enabled", False) is True:
                return self._build_blocked_result(
                    request=request,
                    blocker="Runtime jest zatrzymany przez emergency stop.",
                    next_step="Zdejmij emergency stop, potem uruchom short ponownie.",
                )
            if getattr(runtime_controls, "pause_all_jobs", False) is True:
                return self._build_blocked_result(
                    request=request,
                    blocker="Wszystkie joby sa obecnie wstrzymane.",
                    next_step="Wznow joby runtime, potem uruchom short ponownie.",
                )

        normalized_quality = self._normalize_quality_profile(quality_profile)
        try:
            queued_job = self._queue_content_campaign(
                request=request,
                conversation_id=conversation_id,
                project_id=project_id,
                quality_profile=normalized_quality,
            )
        except Exception as exc:
            return self._build_blocked_result(
                request=request,
                blocker=str(exc),
                next_step="Sprobuj doprecyzowac brief albo sprawdz, czy scheduler workflow shortow jest gotowy.",
            )

        job_payload = dict(queued_job.get("job") or {})
        job_id = int(job_payload.get("job_id") or 0) or None
        assistant_lines = [
            f"Uruchomilem workflow shorta dla briefu `{request.brief}`.",
            f"Wybralem workflow: `{request.workflow_key}`.",
            f"Tryb jakosci: `{normalized_quality}`.",
            "Material generuje sie teraz lokalnie w tle. Po renderze dostaniesz MP4 i artefakty do recznego uploadu.",
        ]
        metadata = {
            "creative_video_operator": {
                "workflow_key": request.workflow_key,
                "platform_hint": request.platform_hint,
                "quality_profile": normalized_quality,
                "workflow_state": {
                    "current_node": "graph.research",
                    "status": "queued",
                },
                "approval_required": False,
                "artifact_count": 0,
                "publish_path": "manual_upload_ready",
                "next_step": "Poczekaj na zakonczenie lokalnego renderu, potem zrob review i wrzuc plik recznie.",
                "scheduler_job_id": job_id,
                "job_status": job_payload.get("status"),
            }
        }
        return CreativeVideoOperationResult(
            activated=True,
            assistant_content="\n".join(assistant_lines),
            metadata=metadata,
            selected_provider_key="creative-video-operator",
            selected_model_key="campaign-youtube-short",
            routing_reason="Queued into the local short-form video workflow.",
            artifacts=(),
            kick_scheduler=job_id is not None,
        )

    def _run_local_polish_short_now(
        self,
        *,
        request: CreativeVideoRequest,
        content: str,
    ) -> CreativeVideoOperationResult:
        topic = extract_short_topic(content) or request.brief
        try:
            result = ShortAgent(self.settings).run(topic)
        except Exception as exc:
            return self._build_blocked_result(
                request=request,
                blocker=f"Lokalny ShortAgent nie domknal renderu: {type(exc).__name__}: {exc}",
                next_step="Sprawdz gotowosc Ollama, Piper, whisper.cpp i FFmpeg, potem uruchom ponownie.",
            )
        metadata = {
            "creative_video_operator": {
                "workflow_key": "local_short_agent",
                "platform_hint": request.platform_hint,
                "workflow_state": {
                    "current_node": "local_delivery",
                    "status": result.status,
                },
                "approval_required": False,
                "publish_path": "manual_upload_ready",
                "quality_run": result.quality_run,
                "short_agent": result.to_dict(),
            }
        }
        return CreativeVideoOperationResult(
            activated=True,
            assistant_content=format_short_agent_response(result),
            metadata=metadata,
            selected_provider_key="local-short-agent",
            selected_model_key=self.settings.ollama_synthesis_model or self.settings.ollama_chat_model or "ollama-local",
            routing_reason="Rendered synchronously by the local offline-first ShortAgent.",
            artifacts=(),
            kick_scheduler=False,
        )

    def _run_local_video_generation_now(
        self,
        *,
        request: CreativeVideoRequest,
        conversation_id: int | None,
    ) -> CreativeVideoOperationResult:
        artifacts = OutputArtifactService(self.settings)
        output_path = artifacts.create_output_path(conversation_id=conversation_id, extension=".mp4")
        duration_sec = self._extract_duration_seconds(request.raw_content) or 8
        try:
            result = LocalVideoGenerationService(self.settings).generate_video(
                prompt=request.brief,
                output_path=output_path,
                duration_sec=duration_sec,
                fps=24,
                width=512,
                height=512,
            )
            descriptor = artifacts.finalize_output(
                absolute_path=result.output_path,
                filename="atnos-generated-video.mp4",
                media_type="video/mp4",
            )
        except VideoGenerationError as exc:
            return self._build_blocked_result(
                request=request,
                blocker=f"Local video generation failed: {exc}",
                next_step="Sprawdz log runtime wideo. Nie zwrocilem scenariusza tekstowego zamiast MP4.",
            )
        artifact = {
            "kind": "video",
            "filename": descriptor.filename,
            "storage_path": descriptor.storage_path,
            "mime": descriptor.media_type,
            "file_extension": descriptor.file_extension,
            "size_bytes": descriptor.size_bytes,
        }
        metadata = {
            "creative_video_operator": {
                "workflow_key": "local_video_generation",
                "platform_hint": request.platform_hint,
                "workflow_state": {
                    "current_node": "local_video_generation",
                    "status": "manual_upload_ready",
                },
                "approval_required": False,
                "artifact_count": 1,
                "publish_path": "manual_upload_ready",
                "video_generation": {
                    "backend": result.backend,
                    "width": result.width,
                    "height": result.height,
                    "duration_sec": result.duration_sec,
                    "fps": result.fps,
                    "frame_count": result.frame_count,
                    "vram_total_gb": result.vram_total_gb,
                },
            }
        }
        content = (
            "Wygenerowalem lokalne wideo MP4.\n"
            f"Backend: `{result.backend}`.\n"
            f"Plik: `{descriptor.filename}`.\n"
            "To jest artefakt do recznego review; nie wykonuje uploadu ani publikacji."
        )
        return CreativeVideoOperationResult(
            activated=True,
            assistant_content=content,
            metadata=metadata,
            selected_provider_key="creative-video-operator",
            selected_model_key=result.backend,
            routing_reason="Generated a local MP4 with the local video generation lane.",
            artifacts=(artifact,),
            kick_scheduler=False,
        )

    def _run_content_campaign(
        self,
        *,
        request: CreativeVideoRequest,
        conversation_id: int | None,
        project_id: int | None,
        quality_profile: str,
        runtime_controls,
    ) -> dict[str, object]:
        campaign = ContentCampaignService(
            self.session,
            settings=self.settings,
            provider_registry=self.provider_registry,
            plugin_registry=self.plugin_registry,
        )
        payload = {
            "conversation_id": conversation_id,
            "brief": request.brief,
            "topic": request.brief,
            "project_slug": f"chat-video-{conversation_id or 'global'}",
            "publish_mode": "local_only",
            "approved_for_publish": False,
            "peer_consultation": False,
            "quality_profile": quality_profile,
        }
        job = SimpleNamespace(
            payload_json=payload,
            goal=request.brief,
            project_id=int(project_id or 1),
            connector_key="youtube-studio",
            platform_key=request.platform_hint,
        )
        return campaign.run_youtube_short_campaign(
            run=None,
            job=job,
            controls=runtime_controls,
            publish_allowed=False,
            attach_message=False,
        )

    def _queue_content_campaign(
        self,
        *,
        request: CreativeVideoRequest,
        conversation_id: int | None,
        project_id: int | None,
        quality_profile: str,
    ) -> dict[str, object]:
        trusted_auto = TrustedAutoService(
            self.session,
            self.settings,
            provider_registry=self.provider_registry,
            plugin_registry=self.plugin_registry,
        )
        resolved_project_id = self._resolve_project_id(
            trusted_auto=trusted_auto,
            project_id=project_id,
        )
        return trusted_auto.run_job_now(
            project_id=resolved_project_id,
            job_type="campaign",
            goal=request.brief,
            connector_key="youtube-studio",
            platform_key=request.platform_hint,
            account_id=None,
            trust_grant_id=None,
            payload_json={
                "conversation_id": conversation_id,
                "brief": request.brief,
                "topic": request.brief,
                "project_slug": f"chat-video-{conversation_id or 'global'}",
                "publish_mode": "local_only",
                "approved_for_publish": False,
                "peer_consultation": False,
                "quality_profile": quality_profile,
            },
            requires_trusted_auto=False,
            execute_now=False,
        )

    def _extract_artifacts(self, workflow_output: dict[str, object]) -> tuple[dict[str, object], ...]:
        raw_artifacts = workflow_output.get("artifacts")
        if not isinstance(raw_artifacts, dict):
            return ()
        artifacts: list[dict[str, object]] = []
        for kind, payload in raw_artifacts.items():
            if not isinstance(payload, dict):
                continue
            if not all(key in payload for key in ("filename", "storage_path", "media_type", "file_extension", "size_bytes")):
                continue
            artifacts.append(
                {
                    "kind": str(kind),
                    "filename": str(payload["filename"]),
                    "storage_path": str(payload["storage_path"]),
                    "mime": str(payload["media_type"]),
                    "file_extension": str(payload["file_extension"]),
                    "size_bytes": int(payload["size_bytes"]),
                }
            )
        return tuple(artifacts)

    def _build_blocked_result(
        self,
        *,
        request: CreativeVideoRequest,
        blocker: str,
        next_step: str,
    ) -> CreativeVideoOperationResult:
        content = (
            f"Nie moge jeszcze przygotowac tego wideo.\n"
            f"Blokada: {blocker}\n"
            f"Nastepny krok: {next_step}"
        )
        return CreativeVideoOperationResult(
            activated=True,
            assistant_content=content,
            metadata={
                "creative_video_operator": {
                    "workflow_key": request.workflow_key,
                    "platform_hint": request.platform_hint,
                    "blocked": True,
                    "blocker": blocker,
                    "next_step": next_step,
                }
            },
            selected_provider_key="creative-video-operator",
            selected_model_key="blocked",
            routing_reason="Creative video operator could not execute the request.",
            artifacts=(),
        )

    def _detect_request(self, content: str) -> CreativeVideoRequest | None:
        normalized = self._normalize_text(content)
        if not any(hint in normalized for hint in self.VIDEO_HINTS):
            return None
        if not any(hint in normalized for hint in self.ACTION_HINTS) and not any(
            phrase in normalized for phrase in self.REQUEST_PHRASES
        ):
            return None
        if any(hint in normalized for hint in self.SYNTHETIC_CREATOR_HINTS):
            workflow_key = "synthetic_creator"
        elif self._is_short_form_campaign_request(normalized):
            workflow_key = "content_campaign"
        else:
            workflow_key = "local_video_generation"
        brief = self._extract_brief(content)
        return CreativeVideoRequest(
            raw_content=content,
            brief=brief,
            workflow_key=workflow_key,
            platform_hint="youtube",
        )

    def _extract_brief(self, content: str) -> str:
        working = str(content or "").strip()
        lowered = working.casefold()
        for prefix in self.LEADING_PHRASE_PREFIXES:
            if lowered.startswith(prefix):
                working = working[len(prefix):].strip()
                lowered = working.casefold()
                break
        normalized = self._normalize_text(working)
        removable_prefixes = (
            "zrob mi ",
            "zrob ",
            "stworz mi ",
            "stworz ",
            "wygeneruj mi ",
            "wygeneruj ",
            "przygotuj mi ",
            "przygotuj ",
            "nagraj mi ",
            "nagraj ",
        )
        for prefix in removable_prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):].strip()
                break
        for token in (
            "wideo",
            "video",
            "filmik",
            "film",
            "shorta",
            "short",
            "shorty",
            "shorts",
            "rolke",
            "rolka",
            "reel",
            "reels",
            "klip",
            "clip",
        ):
            normalized = normalized.replace(f" {token} ", " ")
        return " ".join(normalized.split())

    @staticmethod
    def _is_short_form_campaign_request(normalized: str) -> bool:
        return any(
            token in normalized
            for token in (
                "short",
                "shorta",
                "shorty",
                "shorts",
                "youtube short",
                "rolka",
                "rolke",
                "reel",
                "reels",
            )
        )

    @staticmethod
    def _extract_duration_seconds(content: str) -> int | None:
        normalized = CreativeVideoOperatorService._normalize_text(content)
        for token in normalized.split():
            if token.endswith("-sekundowe") or token.endswith("-sekundowy") or token.endswith("-sekundowy"):
                raw = token.split("-", 1)[0]
            elif token.endswith("s") and token[:-1].isdigit():
                raw = token[:-1]
            elif token.isdigit():
                raw = token
            else:
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            if 1 <= value <= 60:
                return value
        return None

    @staticmethod
    def _normalize_quality_profile(value: str | None) -> str:
        normalized = " ".join((value or "").split())
        if normalized not in {"fast-turnaround", "standard", "premium"}:
            return "standard"
        return normalized

    @staticmethod
    def _resolve_project_id(
        *,
        trusted_auto: TrustedAutoService,
        project_id: int | None,
    ) -> int:
        if isinstance(project_id, int) and project_id > 0:
            return project_id
        projects = trusted_auto.projects.list_all()
        if projects:
            return int(projects[0].id)
        return 1

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(value or ""))
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        lowered = ascii_only.casefold()
        return " ".join(lowered.split())
