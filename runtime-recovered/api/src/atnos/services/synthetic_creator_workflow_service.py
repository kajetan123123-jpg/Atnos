from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import (
    AttachmentKind,
    AttachmentSource,
    AttachmentStatus,
    MessageRole,
    MessageStatus,
    ModelTask,
    ProviderPosture,
)
from atnos.plugins.registry import PluginRegistry, build_default_plugin_registry
from atnos.providers.registry import ProviderRegistry, build_default_provider_registry, build_stub_provider_registry
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
from atnos.services.creator_ops_service import CreatorOpsService, CreatorProjectView
from atnos.services.hardware_guard_service import HardwareGuardService
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService
from atnos.services.render_service import RenderArtifactResult
from atnos.services.synthetic_broll_planner_service import SyntheticBrollPlanResult, SyntheticBrollPlannerService
from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsCalibration, SyntheticAnalyticsLearningService
from atnos.services.subtitle_service import SubtitleService
from atnos.services.synthetic_motion_compositor_service import SyntheticMotionCompositorService
from atnos.services.synthetic_release_pack_service import SyntheticReleasePack, SyntheticReleasePackService
from atnos.services.synthetic_variant_planning_service import SyntheticVariantPlanningService
from atnos.services.synthetic_variant_refinement_service import (
    SyntheticPreviewHardControls,
    SyntheticVariantRefinementPlan,
    SyntheticVariantRefinementService,
)
from atnos.services.synthetic_variant_scoring_service import SyntheticVariantScoringService
from atnos.services.trend_watch_service import TrendWatchService
from atnos.services.voice_synthesis_service import VoiceSynthesisService
from atnos.services.wav2lip_runtime_service import Wav2LipArtifactResult, Wav2LipRuntimeService
from atnos.services.workflow_reflection_service import WorkflowReflectionService
from atnos.services.workflow_llm_service import WorkflowLLMService
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService


@dataclass(frozen=True, slots=True)
class SyntheticScriptBundle:
    title: str
    hook: str
    body: tuple[str, ...]
    outro: str
    portrait_prompt: str
    visual_direction: str
    engine: str

    @property
    def narration_text(self) -> str:
        return " ".join(part for part in (self.hook, *self.body, self.outro) if part).strip()

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "hook": self.hook,
            "body": list(self.body),
            "outro": self.outro,
            "portrait_prompt": self.portrait_prompt,
            "visual_direction": self.visual_direction,
            "engine": self.engine,
            "narration_text": self.narration_text,
        }


@dataclass(frozen=True, slots=True)
class SyntheticStylePreset:
    key: str
    label: str
    delivery_hint: str
    portrait_keywords: tuple[str, ...]
    visual_direction: str
    voice_name: str
    voice_speed: float


@dataclass(frozen=True, slots=True)
class SyntheticReelPreset:
    key: str
    label: str
    structure_hint: str
    timing_hint: str
    max_body_lines: int


class SyntheticCreatorWorkflowService:
    PORTRAIT_SIZE = (720, 1280)
    MAX_PORTRAIT_ATTEMPTS = 3
    MAX_REFINEMENT_ATTEMPTS = 2
    DEFAULT_STYLE_KEY = "editorial_premium"
    DEFAULT_REEL_KEY = "talking_hook"
    PORTRAIT_NEGATIVE_PROMPT = (
        "illustration, drawing, painting, sketch, engraving, manga, anime, monochrome, sepia, "
        "vintage print, newspaper, poster, text, letters, watermark, logo, border, frame, "
        "profile view, side view, looking away, closed eyes, occluded face, hair covering face, "
        "hat, helmet, mask, sunglasses, multiple people, crowd, full body, long shot, hands near face, "
        "cropped chin, cropped forehead, fantasy costume, armor"
    )
    LOW_QUALITY_SCRIPT_MARKERS = (
        "atnos live smoke",
        "atnos synthetic creator",
        "atnos to",
        "zapraszamy do świata",
        "witaj w nowej erze",
        "nowa era",
        "twoj kreatywny potencjal",
        "kreatywnosc",
        "dowiedz sie",
        "realne scenariusze",
        "bez ograniczen",
        "swiat czeka",
        "narzedzie do tworzenia",
        "rzeczywist",
        "fantazj",
        "piekn",
        "tworzenie",
        "przeksztalc",
    )
    STYLE_PRESETS: dict[str, SyntheticStylePreset] = {
        "editorial_premium": SyntheticStylePreset(
            key="editorial_premium",
            label="Editorial Premium",
            delivery_hint="spokojny, pewny, premium, bez przesadnej sprzedaży",
            portrait_keywords=("clean editorial light", "polished beauty campaign", "premium creator branding"),
            visual_direction="Luxury editorial portrait, clean soft contrast, direct eye contact, premium beauty campaign framing.",
            voice_name="pl_maja",
            voice_speed=0.94,
        ),
        "cozy_girlfriend": SyntheticStylePreset(
            key="cozy_girlfriend",
            label="Cozy Girlfriend",
            delivery_hint="ciepły, bliski, miękki, bardziej relacyjny niż sprzedażowy",
            portrait_keywords=("soft daylight", "cozy apartment mood", "warm natural smile"),
            visual_direction="Warm cozy portrait, soft daylight, approachable eye contact, intimate but fully clothed creator vibe.",
            voice_name="pl_maja",
            voice_speed=0.9,
        ),
        "late_night_egirl": SyntheticStylePreset(
            key="late_night_egirl",
            label="Late Night E-Girl",
            delivery_hint="nocny internetowy vibe, bardziej teasing, lekko szybszy rytm",
            portrait_keywords=("late night studio", "subtle neon spill", "internet glamour"),
            visual_direction="Late-night creator portrait, subtle neon edge light, internet-native glamour, direct camera connection.",
            voice_name="pl_maja",
            voice_speed=1.01,
        ),
        "energetic_expert": SyntheticStylePreset(
            key="energetic_expert",
            label="Energetic Expert",
            delivery_hint="krócej, konkretniej, bardziej performance-marketing niż flirt",
            portrait_keywords=("high clarity commercial light", "confident host energy", "sharp creator campaign"),
            visual_direction="Sharp host portrait, commercial lighting, confident direct address, high clarity creator promo.",
            voice_name="pl_piotr",
            voice_speed=1.0,
        ),
    }
    REEL_PRESETS: dict[str, SyntheticReelPreset] = {
        "talking_hook": SyntheticReelPreset(
            key="talking_hook",
            label="Talking Hook",
            structure_hint="mocny hook, jedno zdanie rozwinięcia, szybki payoff",
            timing_hint="12-18 sekund, bez długiego intro",
            max_body_lines=2,
        ),
        "micro_story": SyntheticReelPreset(
            key="micro_story",
            label="Micro Story",
            structure_hint="hook, krótka historia napięcia, mały reveal na końcu",
            timing_hint="18-24 sekundy, bardziej narracyjne niż sprzedażowe",
            max_body_lines=3,
        ),
        "cta_teaser": SyntheticReelPreset(
            key="cta_teaser",
            label="CTA Teaser",
            structure_hint="hook, pokazanie wartości, miękkie wezwanie do kolejnego kroku",
            timing_hint="14-20 sekund, wyraźny payoff i lekki CTA",
            max_body_lines=2,
        ),
        "behind_the_scene": SyntheticReelPreset(
            key="behind_the_scene",
            label="Behind The Scene",
            structure_hint="hook, kulisy procesu, krótki komentarz co dalej",
            timing_hint="16-22 sekundy, bardziej process-driven",
            max_body_lines=3,
        ),
    }

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry | None = None,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry or (
            build_stub_provider_registry()
            if settings.app_env == "test"
            else build_default_provider_registry(app_root=settings.app_root, settings=settings)
        )
        self.plugin_registry = plugin_registry or build_default_plugin_registry()
        self.creator_ops = CreatorOpsService(session, settings)
        self.execution_runs = ExecutionRunRepository(session)
        self.messages = MessageRepository(session)
        self.attachments = AttachmentRepository(session)
        self.integration_config = IntegrationConfigService(session, settings)
        self.artifacts = OutputArtifactService(settings)
        self.hardware_guard = HardwareGuardService(session, settings)
        self.reflector = WorkflowReflectionService(settings, self.hardware_guard)
        self.comfyui = ComfyUiImageGenerationService(settings)
        self.broll = SyntheticBrollPlannerService(settings)
        self.analytics_learning = SyntheticAnalyticsLearningService(session)
        self.voice = VoiceSynthesisService(settings)
        self.subtitles = SubtitleService(settings)
        self.wav2lip = Wav2LipRuntimeService(settings)
        self.motion_reel = SyntheticMotionCompositorService(settings)
        self.release_pack = SyntheticReleasePackService()
        self.variant_planning = SyntheticVariantPlanningService()
        self.variant_scoring = SyntheticVariantScoringService()
        self.variant_refinement = SyntheticVariantRefinementService()
        self.youtube = YouTubeConnectorService(session, settings)
        self.workflow_llm = WorkflowLLMService(
            session,
            settings=settings,
            provider_registry=self.provider_registry,
            plugin_registry=self.plugin_registry,
        )

    def generate_preview(
        self,
        *,
        creator_project_id: int | None = None,
        canonical_project_id: int | None = None,
        conversation_id: int | None = None,
        goal: str | None = None,
        style_key: str | None = None,
        reel_type: str | None = None,
        quality_profile: str = "standard",
        hard_controls: SyntheticPreviewHardControls | None = None,
        attach_message: bool = True,
        execution_run=None,
    ) -> dict[str, Any]:
        normalized_quality_profile = self._normalize_quality_profile(quality_profile)
        project = self._resolve_project(
            creator_project_id=creator_project_id,
            canonical_project_id=canonical_project_id,
        )
        style = self._resolve_style_preset(style_key)
        reel = self._resolve_reel_preset(reel_type)
        effective_hard_controls = hard_controls or self._resolve_project_default_hard_controls(
            project=project,
            reel_key=reel.key,
        )
        effective_goal = (
            (goal or "").strip()
            or self._resolve_project_default_goal(
                project=project,
                style=style,
                reel=reel,
            )
        )

        script_bundle = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_script",
            step_name="Synthetic script",
            input_json={"project": project.name, "goal": effective_goal, "style": style.key, "reel_type": reel.key},
            fn=lambda: self._draft_script_bundle(
                project=project,
                goal=effective_goal,
                style=style,
                reel=reel,
                hard_controls=effective_hard_controls,
            ),
        )
        portrait = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_identity",
            step_name="Identity portrait",
            input_json={"seed": self._portrait_seed(project, style_key=style.key, reel_type=reel.key)},
            fn=lambda: self._generate_validated_portrait_artifact_with_reflection(
                project=project,
                bundle=script_bundle,
                style=style,
                reel=reel,
                conversation_id=conversation_id,
                execution_run=execution_run,
            ),
        )
        cutaways = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_broll",
            step_name="Cutaway planner",
            input_json={
                "image_engine": "comfyui-cutaway",
                "style": style.key,
                "reel_type": reel.key,
                "quality_profile": normalized_quality_profile,
            },
            fn=lambda: self.broll.generate_cutaways(
                project=project,
                script_bundle=script_bundle,
                style=style,
                reel=reel,
                conversation_id=conversation_id,
                quality_profile=normalized_quality_profile,
            ),
        )
        cutaways = self._apply_hard_controls_to_cutaways(cutaways=cutaways, hard_controls=effective_hard_controls)
        audio = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_voice",
            step_name="Polish voice",
            input_json={
                "voice_engine": "kokoro-local",
                "voice_name": style.voice_name,
                "speed": self._resolve_voice_speed(style.voice_speed, effective_hard_controls),
                "chars": len(script_bundle.narration_text),
            },
            fn=lambda: self.voice.synthesize(
                text=script_bundle.narration_text,
                conversation_id=conversation_id,
                voice_name=style.voice_name,
                speed=self._resolve_voice_speed(style.voice_speed, effective_hard_controls),
            ),
        )
        subtitles = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_subtitles",
            step_name="Subtitles",
            input_json={"subtitle_engine": "local-srt"},
            fn=lambda: self.subtitles.generate(
                text=script_bundle.narration_text,
                audio_path=audio.absolute_path,
                conversation_id=conversation_id,
            ),
        )
        talking_head = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_talking_head",
            step_name="Talking-head preview",
            input_json={"lip_sync_engine": "wav2lip", "portrait": portrait.storage_path},
            fn=lambda: self.wav2lip.generate_talking_head(
                portrait_path=portrait.absolute_path,
                audio_path=audio.absolute_path,
                conversation_id=conversation_id,
            ),
        )
        motion_reel = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_motion_reel",
            step_name="Motion reel",
            input_json={
                "composer": "moviepy-synthetic-motion",
                "style": style.key,
                "reel_type": reel.key,
                "talking_head": talking_head.descriptor.storage_path,
                "quality_profile": normalized_quality_profile,
            },
            fn=lambda: self._compose_motion_reel_with_reflection(
                portrait_path=portrait.absolute_path,
                talking_head_path=talking_head.descriptor.absolute_path,
                audio_path=audio.absolute_path,
                subtitles_path=subtitles.absolute_path,
                cutaway_paths=tuple(asset.descriptor.absolute_path for asset in cutaways.assets),
                conversation_id=conversation_id,
                style_key=style.key,
                style_label=style.label,
                reel_label=reel.label,
                hook_text=script_bundle.hook,
                hard_controls=effective_hard_controls,
                quality_profile=normalized_quality_profile,
                execution_run=execution_run,
            ),
        )
        report_bundle = self._run_step(
            execution_run=execution_run,
            step_type="graph.memory_update",
            step_name="Synthetic creator note",
            input_json={"project": project.name, "conversation_id": conversation_id},
            fn=lambda: self._write_report_bundle(
                project=project,
                goal=effective_goal,
                conversation_id=conversation_id,
                style=style,
                reel=reel,
                script_bundle=script_bundle,
                portrait=portrait,
                cutaways=cutaways,
                audio=audio,
                subtitles=subtitles,
                talking_head=talking_head,
                motion_reel=motion_reel,
            ),
        )
        if conversation_id is not None and attach_message:
            self._attach_preview_message(
                conversation_id=conversation_id,
                project=project,
                style=style,
                reel=reel,
                script_bundle=script_bundle,
                portrait=portrait,
                cutaways=cutaways,
                audio=audio,
                subtitles=subtitles,
                talking_head=talking_head,
                motion_reel=motion_reel,
                report_artifact=report_bundle["artifact"],
                vault_note_path=report_bundle["vault_note_path"],
            )

        return {
            "status": "completed",
            "creator_project_id": project.id,
            "project_id": project.project_id,
            "project_name": project.name,
            "goal": effective_goal,
            "quality_profile": normalized_quality_profile,
            "style": {
                "key": style.key,
                "label": style.label,
                "voice_name": style.voice_name,
                "voice_speed": style.voice_speed,
            },
            "reel_type": {
                "key": reel.key,
                "label": reel.label,
                "timing_hint": reel.timing_hint,
            },
            "script_bundle": script_bundle.to_dict(),
            "artifacts": {
                "portrait": self._serialize_artifact(portrait),
                "cutaways": cutaways.to_dict(),
                "audio": self._serialize_artifact(audio),
                "subtitles": self._serialize_artifact(subtitles),
                "talking_head": talking_head.to_dict(),
                "motion_reel": motion_reel.to_dict(),
                "report": self._serialize_artifact(report_bundle["artifact"]),
            },
            "vault_note_path": report_bundle["vault_note_path"],
            "workflow_state": {
                "current_node": "graph.synthetic_creator_complete",
                "status": "done",
            },
            "runtime_stack": {
                "image": "comfyui",
                "broll": cutaways.engine,
                "voice": "kokoro-local",
                "lip_sync": "wav2lip",
                "motion_reel": "moviepy-synthetic-motion",
                "subtitles": "local-srt",
                "render_profile": self._render_profile_for_quality(normalized_quality_profile),
            },
            "hard_controls": effective_hard_controls.to_dict() if effective_hard_controls is not None else None,
        }

    def generate_preview_batch(
        self,
        *,
        creator_project_id: int | None = None,
        canonical_project_id: int | None = None,
        conversation_id: int | None = None,
        goal: str | None = None,
        style_keys: list[str] | None = None,
        reel_types: list[str] | None = None,
        quality_profile: str = "standard",
        max_items: int = 4,
        execution_run=None,
    ) -> dict[str, Any]:
        normalized_quality_profile = self._normalize_quality_profile(quality_profile)
        project = self._resolve_project(
            creator_project_id=creator_project_id,
            canonical_project_id=canonical_project_id,
        )
        selected_styles = self._expand_style_keys(style_keys)
        selected_reels = self._expand_reel_keys(reel_types)
        analytics_learning_profile = self._run_step(
            execution_run=execution_run,
            step_type="graph.analytics_learning",
            step_name="Historical analytics learning",
            input_json={"project_id": project.project_id},
            fn=lambda: self._serialize_analytics_learning_profile(
                self.analytics_learning.build_calibration(project_id=project.project_id)
            ),
        )
        variant_planning_profile = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_planning",
            step_name="Variant planning",
            input_json={
                "styles": [style.key for style in selected_styles],
                "reel_types": [reel.key for reel in selected_reels],
                "max_items": max_items,
                "analytics_samples": analytics_learning_profile.get("sample_count", 0),
            },
            fn=lambda: self.variant_planning.build_plan(
                project_name=project.name,
                goal=goal,
                project_strategy_profile=dict(project.strategy_profile or {}),
                styles=[{"key": style.key, "label": style.label} for style in selected_styles],
                reels=[{"key": reel.key, "label": reel.label} for reel in selected_reels],
                max_items=max_items,
                calibration=analytics_learning_profile,
            ).to_dict(),
        )
        trend_snapshot = self._run_step(
            execution_run=execution_run,
            step_type="graph.trend_watch",
            step_name="Trend watch evaluator",
            input_json={"project_id": project.project_id},
            fn=lambda: TrendWatchService(
                self.session,
                self.settings,
            ).latest_payload(project_id=project.project_id),
        )
        planned_variants = list(variant_planning_profile.get("recommendations") or [])
        variants: list[dict[str, Any]] = []
        for planned_variant in planned_variants:
            planned_hard_controls = self._coerce_hard_controls(
                dict(planned_variant.get("initial_hard_controls") or {})
            )
            preview = self.generate_preview(
                creator_project_id=project.id,
                conversation_id=conversation_id,
                goal=str(planned_variant.get("goal") or "").strip() or None,
                style_key=str(planned_variant.get("style_key") or "").strip() or None,
                reel_type=str(planned_variant.get("reel_key") or "").strip() or None,
                quality_profile=normalized_quality_profile,
                hard_controls=planned_hard_controls,
                attach_message=False,
                execution_run=execution_run,
            )
            variants.append(preview)
        selection = self._run_step(
            execution_run=execution_run,
            step_type="graph.synthetic_rank",
            step_name="Variant ranking",
            input_json={"variants": len(variants), "analytics_samples": analytics_learning_profile.get("sample_count", 0)},
            fn=lambda: self.variant_scoring.rank_variants(
                variants,
                calibration=analytics_learning_profile,
                trend_snapshot=trend_snapshot,
            ),
        )
        best_variant_index = selection.get("best_variant_index")
        if isinstance(best_variant_index, int) and 0 <= best_variant_index < len(variants):
            best_variant = variants[best_variant_index]
        else:
            best_variant = None
        refinement_plan = None
        refined_best_variant = None
        refined_score = None
        refinement_attempts: list[dict[str, object]] = []
        if best_variant is not None:
            refinement_plan = self._run_step(
                execution_run=execution_run,
                step_type="graph.synthetic_refinement_plan",
                step_name="Refinement plan",
                input_json={
                    "best_variant_index": best_variant_index,
                    "style": dict(best_variant.get("style") or {}).get("key"),
                    "reel_type": dict(best_variant.get("reel_type") or {}).get("key"),
                },
                fn=lambda: self.variant_refinement.build_plan(
                    project_name=project.name,
                    best_variant=best_variant,
                    selection=selection,
                    analytics_learning_profile=analytics_learning_profile,
                ),
            )
            refined_best_variant, refined_score, refinement_attempts = self._execute_refinement_loop(
                project=project,
                conversation_id=conversation_id,
                refinement_plan=refinement_plan,
                analytics_learning_profile=analytics_learning_profile,
                trend_snapshot=trend_snapshot,
                quality_profile=normalized_quality_profile,
                execution_run=execution_run,
            )
        release_pack_bundle = None
        winner_variant = refined_best_variant or best_variant
        winner_score = refined_score or self._score_payload_from_selection(selection=selection)
        if winner_variant is not None:
            release_pack_bundle = self._run_step(
                execution_run=execution_run,
                step_type="graph.synthetic_release_pack",
                step_name="Release pack",
                input_json={
                    "style": dict(winner_variant.get("style") or {}).get("key"),
                    "reel_type": dict(winner_variant.get("reel_type") or {}).get("key"),
                    "best_variant_index": selection.get("best_variant_index"),
                },
                fn=lambda: self._write_release_pack_bundle(
                    project=project,
                    conversation_id=conversation_id,
                    preview=winner_variant,
                    selection=selection,
                    score_payload=winner_score,
                    analytics_learning_profile=analytics_learning_profile,
                ),
            )
        summary_bundle = self._write_batch_report_bundle(
            project=project,
            conversation_id=conversation_id,
            variants=variants,
            selection=selection,
            refinement_plan=refinement_plan,
            refined_best_variant=refined_best_variant,
            refined_score=refined_score,
            refinement_attempts=refinement_attempts,
            release_pack=release_pack_bundle["pack"] if release_pack_bundle is not None else None,
            analytics_learning_profile=analytics_learning_profile,
            variant_planning_profile=variant_planning_profile,
        )
        if conversation_id is not None:
            self._attach_batch_summary_message(
                conversation_id=conversation_id,
                project=project,
                selection=selection,
                refined_best_variant=refined_best_variant,
                refined_score=refined_score,
                batch_report_artifact=summary_bundle["artifact"],
                batch_vault_note_path=summary_bundle["vault_note_path"],
                release_pack=release_pack_bundle["pack"] if release_pack_bundle is not None else None,
                release_pack_artifact=release_pack_bundle["artifact"] if release_pack_bundle is not None else None,
                release_pack_json_artifact=release_pack_bundle["json_artifact"] if release_pack_bundle is not None else None,
                release_pack_vault_note_path=release_pack_bundle["vault_note_path"] if release_pack_bundle is not None else None,
            )
        publish_preview = (
            dict(release_pack_bundle["pack"].publish_result_preview)
            if release_pack_bundle is not None
            else None
        )
        analytics_summary = (
            self._pending_analytics_summary(release_pack_bundle["pack"])
            if release_pack_bundle is not None
            else None
        )
        waiting_for_approval = (
            release_pack_bundle is not None
            and release_pack_bundle["pack"].publish_path == "official_youtube_ready"
        )
        return {
            "status": "awaiting_approval" if waiting_for_approval else "completed",
            "creator_project_id": project.id,
            "project_id": project.project_id,
            "project_name": project.name,
            "quality_profile": normalized_quality_profile,
            "variants": variants,
            "selection": selection,
            "analytics_learning_profile": analytics_learning_profile,
            "variant_planning_profile": variant_planning_profile,
            "best_variant": best_variant,
            "refinement_plan": refinement_plan.to_dict() if refinement_plan is not None else None,
            "refined_best_variant": refined_best_variant,
            "refined_score": refined_score,
            "refinement_attempts": refinement_attempts,
            "release_pack": release_pack_bundle["pack"].to_dict() if release_pack_bundle is not None else None,
            "release_pack_artifact": self._serialize_artifact(release_pack_bundle["artifact"]) if release_pack_bundle is not None else None,
            "release_pack_json_artifact": self._serialize_artifact(release_pack_bundle["json_artifact"]) if release_pack_bundle is not None else None,
            "release_pack_vault_note_path": release_pack_bundle["vault_note_path"] if release_pack_bundle is not None else None,
            "batch_report": self._serialize_artifact(summary_bundle["artifact"]),
            "vault_note_path": summary_bundle["vault_note_path"],
            "approval_required": waiting_for_approval,
            "approval_package": (
                dict(release_pack_bundle["pack"].approval_package)
                if waiting_for_approval and release_pack_bundle is not None
                else None
            ),
            "publish_result": publish_preview,
            "analytics_summary": analytics_summary,
            "workflow_state": {
                "current_node": (
                    "graph.synthetic_release_pack"
                    if waiting_for_approval
                    else "graph.synthetic_creator_batch_complete"
                ),
                "status": "awaiting_human" if waiting_for_approval else "done",
            },
        }

    def publish_approved_batch(
        self,
        *,
        source_run,
        publish_run,
        controls,
    ) -> dict[str, Any]:
        source_output = dict(source_run.output_json or {})
        release_pack_payload = dict(source_output.get("release_pack") or {})
        if not release_pack_payload:
            raise RuntimeError("Source synthetic batch run has no release pack to publish.")
        publish_path = self._clean_text(release_pack_payload.get("publish_path"))
        if publish_path != "official_youtube_ready":
            raise RuntimeError("Synthetic release pack is only manual-upload ready. Official YouTube publish is not available.")
        metadata_bundle = dict(release_pack_payload.get("metadata") or {})
        if not metadata_bundle:
            raise RuntimeError("Synthetic release pack is missing publish metadata.")
        winner_variant = self._resolve_winner_variant_from_batch(source_output)
        if winner_variant is None:
            raise RuntimeError("Synthetic batch run has no winning variant to publish.")
        winner_artifacts = dict(winner_variant.get("artifacts") or {})
        motion_reel_payload = dict(dict(winner_artifacts.get("motion_reel") or {}).get("descriptor") or {})
        portrait_payload = dict(winner_artifacts.get("portrait") or {})
        subtitles_payload = dict(winner_artifacts.get("subtitles") or {})
        video_descriptor = self._descriptor_from_payload(motion_reel_payload)
        thumbnail_descriptor = self._descriptor_from_payload(portrait_payload)
        subtitles_descriptor = self._descriptor_from_payload(subtitles_payload)
        if video_descriptor is None or thumbnail_descriptor is None or subtitles_descriptor is None:
            raise RuntimeError("Synthetic batch winner is missing publish artifacts.")

        self._run_step(
            execution_run=publish_run,
            step_type="graph.approval_gate",
            step_name="Approval gate",
            input_json={"source_run_id": source_run.id},
            fn=lambda: {
                "status": "approved",
                "source_run_id": source_run.id,
                "message": "Human approval confirmed for synthetic release pack publish.",
            },
        )
        publish_result = self._run_step(
            execution_run=publish_run,
            step_type="graph.upload",
            step_name="Upload",
            input_json={
                "connector_key": release_pack_payload.get("connector_key") or source_run.connector_key,
                "platform_key": release_pack_payload.get("platform_key") or source_run.platform_key,
                "source_run_id": source_run.id,
            },
            fn=lambda: self._publish_release_pack_if_possible(
                controls=controls,
                video_descriptor=video_descriptor,
                thumbnail_descriptor=thumbnail_descriptor,
                subtitles_descriptor=subtitles_descriptor,
                metadata_bundle=metadata_bundle,
            ),
        )
        analytics_summary = self._run_step(
            execution_run=publish_run,
            step_type="graph.analytics_sync",
            step_name="Analytics",
            input_json={"video_id": publish_result.get("video_id")},
            fn=lambda: self._analytics_if_possible(publish_result),
        )
        feedback_summary = self._run_step(
            execution_run=publish_run,
            step_type="graph.memory_update",
            step_name="Memory update",
            input_json={"project_name": source_output.get("project_name") or source_run.goal},
            fn=lambda: self._build_publish_feedback_summary(
                project_name=str(source_output.get("project_name") or source_run.goal),
                release_pack_payload=release_pack_payload,
                publish_result=publish_result,
                analytics_summary=analytics_summary,
            ),
        )
        conversation_id = (
            source_run.input_json.get("conversation_id")
            if isinstance(source_run.input_json, dict) and isinstance(source_run.input_json.get("conversation_id"), int)
            else None
        )
        feedback_artifact = self._write_markdown_artifact(
            conversation_id=conversation_id,
            filename="atnos-synthetic-feedback-loop.md",
            content=feedback_summary,
        )
        feedback_vault_note_path = self._write_publish_feedback_obsidian_note(
            project_name=str(source_output.get("project_name") or source_run.goal),
            content=feedback_summary,
        )
        artifact_payload = {
            **dict(source_run.artifacts_json or {}),
            "feedback_loop": self._serialize_artifact(feedback_artifact),
        }
        if feedback_vault_note_path:
            artifact_payload["feedback_vault_note_path"] = feedback_vault_note_path
        artifact_payload["youtube_connector"] = self.youtube.probe().to_dict() if publish_result.get("status") == "uploaded" else {}
        return {
            "status": "completed",
            "workflow": "synthetic_creator_publish",
            "source_run_id": source_run.id,
            "project_name": source_output.get("project_name") or source_run.goal,
            "publish_result": publish_result,
            "analytics_summary": analytics_summary,
            "approval_required": False,
            "approval_package": dict(release_pack_payload.get("approval_package") or {}),
            "release_pack": release_pack_payload,
            "workflow_state": {
                "current_node": "graph.memory_update",
                "status": "done",
            },
            "artifacts": artifact_payload,
            "vault_note_path": feedback_vault_note_path,
        }

    def _execute_refinement_loop(
        self,
        *,
        project: CreatorProjectView,
        conversation_id: int | None,
        refinement_plan: SyntheticVariantRefinementPlan,
        analytics_learning_profile: dict[str, Any] | SyntheticAnalyticsCalibration,
        trend_snapshot: dict[str, Any] | None,
        quality_profile: str,
        execution_run,
    ) -> tuple[dict[str, Any], dict[str, object], list[dict[str, object]]]:
        current_controls = refinement_plan.hard_controls
        attempts: list[dict[str, object]] = []
        current_goal = refinement_plan.goal
        refined_best_variant: dict[str, Any] | None = None
        refined_score: dict[str, object] | None = None

        for attempt_index in range(1, self.MAX_REFINEMENT_ATTEMPTS + 1):
            refined_best_variant = self.generate_preview(
                creator_project_id=project.id,
                conversation_id=conversation_id,
                goal=current_goal,
                style_key=refinement_plan.style_key,
                reel_type=refinement_plan.reel_key,
                quality_profile=quality_profile,
                hard_controls=current_controls,
                attach_message=False,
                execution_run=execution_run,
            )
            refined_score = self._run_step(
                execution_run=execution_run,
                step_type="graph.synthetic_refinement_score",
                step_name=f"Refined variant score #{attempt_index}",
                input_json={
                    "attempt": attempt_index,
                    "source_variant_index": refinement_plan.source_variant_index,
                    "style": refinement_plan.style_key,
                    "reel_type": refinement_plan.reel_key,
                    "hard_controls": current_controls.to_dict() if current_controls is not None else None,
                },
                fn=lambda preview=refined_best_variant: self.variant_scoring.score_variant(
                    preview,
                    index=refinement_plan.source_variant_index,
                    calibration=analytics_learning_profile,
                    trend_snapshot=trend_snapshot,
                ).to_dict(),
            )
            qa = dict(dict(refined_best_variant.get("artifacts") or {}).get("motion_reel") or {}).get("qa") or {}
            attempt_record = {
                "attempt": attempt_index,
                "duration_sec": dict(qa).get("duration_sec"),
                "score": dict(refined_score).get("score"),
                "confidence": dict(refined_score).get("confidence"),
                "hard_controls": current_controls.to_dict() if current_controls is not None else None,
                "target_met": not self._needs_refinement_retry(refined_best_variant, current_controls),
            }
            attempts.append(attempt_record)
            if not self._needs_refinement_retry(refined_best_variant, current_controls):
                break
            if attempt_index >= self.MAX_REFINEMENT_ATTEMPTS or current_controls is None:
                break
            current_controls = current_controls.escalate()
            current_goal = (
                f"{refinement_plan.goal} "
                "Wymus jeszcze ciasniejszy timing, krotszy hook i krotszy body bez utraty payoffu."
            )

        assert refined_best_variant is not None
        assert refined_score is not None
        return refined_best_variant, refined_score, attempts

    @staticmethod
    def _normalize_quality_profile(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"premium", "fast-turnaround", "standard"}:
            return normalized
        return "standard"

    @staticmethod
    def _render_profile_for_quality(quality_profile: str) -> str:
        normalized = SyntheticCreatorWorkflowService._normalize_quality_profile(quality_profile)
        if normalized == "fast-turnaround":
            return "balanced"
        return "quality"

    @staticmethod
    def _serialize_analytics_learning_profile(
        profile: SyntheticAnalyticsCalibration | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(profile, SyntheticAnalyticsCalibration):
            return profile.to_dict()
        return dict(profile or {})

    @staticmethod
    def _coerce_hard_controls(payload: dict[str, Any] | None) -> SyntheticPreviewHardControls | None:
        if not isinstance(payload, dict) or not payload:
            return None
        target_window_value = payload.get("target_duration_window_sec")
        target_window: tuple[float, float] | None = None
        if (
            isinstance(target_window_value, list)
            and len(target_window_value) == 2
            and all(isinstance(item, (int, float)) for item in target_window_value)
        ):
            target_window = (float(target_window_value[0]), float(target_window_value[1]))
        return SyntheticPreviewHardControls(
            target_duration_window_sec=target_window,
            target_duration_cap_sec=float(payload["target_duration_cap_sec"]) if isinstance(payload.get("target_duration_cap_sec"), (int, float)) else None,
            max_hook_words=int(payload["max_hook_words"]) if isinstance(payload.get("max_hook_words"), int) else None,
            max_body_lines=int(payload["max_body_lines"]) if isinstance(payload.get("max_body_lines"), int) else None,
            drop_outro=bool(payload.get("drop_outro")),
            voice_speed_multiplier=float(payload["voice_speed_multiplier"]) if isinstance(payload.get("voice_speed_multiplier"), (int, float)) else 1.0,
            hook_duration_cap_sec=float(payload["hook_duration_cap_sec"]) if isinstance(payload.get("hook_duration_cap_sec"), (int, float)) else None,
            max_cutaways=int(payload["max_cutaways"]) if isinstance(payload.get("max_cutaways"), int) else None,
            cutaway_display_duration_sec=float(payload["cutaway_display_duration_sec"]) if isinstance(payload.get("cutaway_display_duration_sec"), (int, float)) else None,
        )

    def _resolve_project_default_hard_controls(
        self,
        *,
        project: CreatorProjectView,
        reel_key: str,
    ) -> SyntheticPreviewHardControls | None:
        profile = dict(project.strategy_profile or {})
        controls_by_reel = dict(profile.get("initial_hard_controls_by_reel") or {})
        candidate = controls_by_reel.get(reel_key)
        return self._coerce_hard_controls(candidate if isinstance(candidate, dict) else None)

    def _resolve_project(
        self,
        *,
        creator_project_id: int | None,
        canonical_project_id: int | None,
    ) -> CreatorProjectView:
        project = (
            self.creator_ops.get_project(creator_project_id)
            if creator_project_id is not None
            else None
        )
        if project is None and canonical_project_id is not None:
            project = next(
                (
                    item
                    for item in self.creator_ops.list_projects()
                    if item.project_id == canonical_project_id
                ),
                None,
            )
        if project is None:
            raise ValueError("Synthetic creator project was not found.")
        if project.project_type not in {"synthetic_influencer", "synthetic_model"}:
            raise ValueError("Synthetic creator preview is only available for synthetic creator projects.")
        return project

    def _draft_script_bundle(
        self,
        *,
        project: CreatorProjectView,
        goal: str,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        hard_controls: SyntheticPreviewHardControls | None = None,
    ) -> SyntheticScriptBundle:
        prompt = self._build_script_prompt_v2(
            project=project,
            goal=goal,
            style=style,
            reel=reel,
            hard_controls=hard_controls,
        )
        try:
            result = self.workflow_llm.generate_text(
                prompt=prompt,
                task=ModelTask.SYNTHESIS,
                provider_posture=ProviderPosture.PREFER_LOCAL,
            )
            parsed = self._parse_script_bundle(result.content)
            if parsed is not None:
                return self._finalize_script_bundle(
                    project=project,
                    style=style,
                    reel=reel,
                    bundle=parsed,
                    hard_controls=hard_controls,
                )
        except Exception:
            pass
        return self._build_fallback_script_bundle_v2(
            project=project,
            style=style,
            reel=reel,
            hard_controls=hard_controls,
        )

    def _build_script_prompt(self, *, project: CreatorProjectView, goal: str) -> str:
        return (
            "Przygotuj bardzo krotki, polski teaser talking-head dla w pelni oryginalnej "
            "syntetycznej persony. Zwracaj tylko JSON.\n"
            "{\n"
            '  "title": "...",\n'
            '  "hook": "...",\n'
            '  "body": ["...", "..."],\n'
            '  "outro": "...",\n'
            '  "portrait_prompt": "...",\n'
            '  "visual_direction": "..."\n'
            "}\n"
            "Zasady:\n"
            "- jezyk polski\n"
            "- 2 krótkie zdania body max\n"
            "- ton premium, pewny, naturalny\n"
            "- bez erotyki explicite, bez minor-coded vibe, bez podszywania sie pod realna osobe\n"
            "- portret ma byc dorosly, w pelni ubrany, editorial, 9:16, clean background\n"
            f"Projekt: {project.name}\n"
            f"Nisza: {project.niche or 'synthetic creator'}\n"
            f"Audience: {project.target_audience or 'adult viewers'}\n"
            f"Granice: {project.content_boundaries or 'legal, consent-based, non-impersonating'}\n"
            f"Cel: {goal}\n"
        )

    def _build_script_prompt_v2(
        self,
        *,
        project: CreatorProjectView,
        goal: str,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        hard_controls: SyntheticPreviewHardControls | None = None,
    ) -> str:
        extra_rules: list[str] = []
        if hard_controls is not None:
            if hard_controls.max_hook_words is not None:
                extra_rules.append(f"- hook maksymalnie {hard_controls.max_hook_words} slow")
            if hard_controls.max_body_lines is not None:
                extra_rules.append(f"- body maksymalnie {hard_controls.max_body_lines} linie po hard trimie")
            if hard_controls.target_duration_cap_sec is not None:
                extra_rules.append(f"- narracja po twardym trimie ma zmiescic sie w okolo {hard_controls.target_duration_cap_sec:.1f} sekundy")
            if hard_controls.drop_outro:
                extra_rules.append("- jesli trzeba, wytnij outro zamiast rozwlekac pacing")
        extra_rules.extend(self._project_strategy_rules(project=project))
        return (
            "Przygotuj bardzo krotki, polski teaser talking-head dla w pelni oryginalnej "
            "syntetycznej persony. Zwracaj tylko JSON.\n"
            "{\n"
            '  "title": "...",\n'
            '  "hook": "...",\n'
            '  "body": ["...", "..."],\n'
            '  "outro": "...",\n'
            '  "portrait_prompt": "...",\n'
            '  "visual_direction": "..."\n'
            "}\n"
            "Zasady:\n"
            "- jezyk polski\n"
            f"- maksymalnie {reel.max_body_lines} krotkie linie body\n"
            f"- ton i delivery: {style.delivery_hint}\n"
            f"- struktura: {reel.structure_hint}\n"
            f"- timing: {reel.timing_hint}\n"
            f"{''.join(rule + '\n' for rule in extra_rules)}"
            "- bez erotyki explicite, bez minor-coded vibe, bez podszywania sie pod realna osobe\n"
            "- portret ma byc dorosly, w pelni ubrany, editorial, 9:16, clean background\n"
            f"Projekt: {project.name}\n"
            f"Nisza: {project.niche or 'synthetic creator'}\n"
            f"Audience: {project.target_audience or 'adult viewers'}\n"
            f"Granice: {project.content_boundaries or 'legal, consent-based, non-impersonating'}\n"
            f"Styl: {style.label}\n"
            f"Visual direction: {style.visual_direction}\n"
            f"Reel type: {reel.label}\n"
            f"Cel: {goal}\n"
        )

    def _parse_script_bundle(self, content: str) -> SyntheticScriptBundle | None:
        payload = self._extract_json_object(content)
        if payload is None:
            return None
        title = self._clean_text(payload.get("title"))
        hook = self._clean_text(payload.get("hook"))
        outro = self._clean_text(payload.get("outro"))
        portrait_prompt = self._clean_text(payload.get("portrait_prompt"))
        visual_direction = self._clean_text(payload.get("visual_direction"))
        raw_body = payload.get("body")
        if isinstance(raw_body, str):
            body = tuple(
                item.strip()
                for item in re.split(r"(?<=[.!?])\s+", raw_body)
                if item.strip()
            )
        elif isinstance(raw_body, list):
            body = tuple(self._clean_text(item) for item in raw_body if self._clean_text(item))
        else:
            body = ()
        if not title or not hook or not body or not portrait_prompt:
            return None
        return SyntheticScriptBundle(
            title=title,
            hook=hook,
            body=body[:3],
            outro=outro or "Jesli ten klimat siada, odpalam kolejny drop.",
            portrait_prompt=portrait_prompt,
            visual_direction=visual_direction or "Premium portrait, direct eye contact, clean editorial light.",
            engine="workflow-llm",
        )

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any] | None:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        candidate = stripped[start : end + 1]
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _project_strategy_rules(self, *, project: CreatorProjectView) -> list[str]:
        profile = dict(project.strategy_profile or {})
        lines: list[str] = []
        hook_template = dict(profile.get("hook_template") or {})
        pacing_policy = dict(profile.get("pacing_policy") or {})
        title_policy = dict(profile.get("title_policy") or {})
        for label, payload in (
            ("domyslny hook template projektu", hook_template),
            ("polityka pacingu projektu", pacing_policy),
            ("polityka tytulu projektu", title_policy),
        ):
            instruction = self._clean_text(payload.get("instruction"))
            if instruction:
                lines.append(f"- {label}: {instruction}")
        return lines

    def _resolve_project_default_goal(
        self,
        *,
        project: CreatorProjectView,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
    ) -> str:
        profile = dict(project.strategy_profile or {})
        next_generation_brief = self._clean_text(profile.get("next_generation_brief"))
        if next_generation_brief:
            return (
                f"{next_generation_brief} Prepare one automated synthetic creator "
                f"{reel.label.lower()} preview for {project.name} in style {style.label}."
            )
        return f"Prepare one automated synthetic creator {reel.label.lower()} preview for {project.name} in style {style.label}."

    def _build_fallback_script_bundle(self, *, project: CreatorProjectView) -> SyntheticScriptBundle:
        niche = self._clean_text(project.niche) or "premium synthetic creator"
        base_name = self._clean_text(project.name) or "ATNOS Creator"
        return SyntheticScriptBundle(
            title=f"{base_name} / talking-head preview",
            hook=f"Hej, tu {base_name}. Testuje nowy format: krotko, premium i bez plastikowego looku.",
            body=(
                f"Buduje oryginalna persone pod {niche}.",
                "Najpierw sprawdzam glos, twarz i rytm, dopiero potem ide w wiekszy batch.",
            ),
            outro="Jesli ten klimat trzyma poziom, rozwijam z tego cala serie.",
            portrait_prompt=(
                f"original adult synthetic creator portrait, fully clothed, premium editorial lighting, "
                f"direct eye contact, clean background, natural makeup, subtle confident expression, "
                f"waist-up framing, 9:16 composition, safe for work, non-explicit, inspired by {niche}"
            ),
            visual_direction="Premium portrait, stable identity sheet, clean soft contrast, camera-facing delivery.",
            engine="fallback-template",
        )

    def _build_fallback_script_bundle_v2(
        self,
        *,
        project: CreatorProjectView,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        hard_controls: SyntheticPreviewHardControls | None = None,
    ) -> SyntheticScriptBundle:
        niche = self._clean_text(project.niche) or "premium synthetic creator"
        base_name = self._clean_text(project.name) or "ATNOS Creator"
        hook_map = {
            "editorial_premium": {
                "talking_hook": "Zatrzymaj sie na chwile. Tu liczy sie twarz, glos i zero plastikowego vibe'u.",
                "micro_story": "W kilka sekund widac, czy ten format wyglada premium, czy tylko drogo udaje premium.",
                "cta_teaser": "Jesli pierwszy kadr nie trzyma wzroku, reszta tej rolki nie ma znaczenia.",
                "behind_the_scene": "Pokazuje kulisy: jak testuje twarz, glos i pacing zanim cos dostanie wiekszy budzet.",
            },
            "cozy_girlfriend": {
                "talking_hook": "Hej, zostan chwile. Ten format ma byc bliski i naturalny, a nie przeslodzony.",
                "micro_story": "Najpierw buduje spokojny klimat. Potem sprawdzam, czy kamera dalej czuje prawdziwa osobe.",
                "cta_teaser": "Jesli ten kadr daje cieplo od pierwszej sekundy, wtedy dopiero ma sens reszta serii.",
                "behind_the_scene": "To jest cichy backstage: mniej gadania, wiecej sprawdzania czy vibe naprawde siadl.",
            },
            "late_night_egirl": {
                "talking_hook": "Nocny vibe, szybki hook i twarz, ktora ma trzymac wzrok od pierwszej sekundy.",
                "micro_story": "Ten format ma wciagac jak nocny feed, ale bez plastikowej przesady i bez chaosu.",
                "cta_teaser": "Jesli klimat nie klei sie od razu, nie ma sensu dokladac neonu ani kolejnych efektow.",
                "behind_the_scene": "Sprawdzam, ile tu jest prawdziwego klimatu, a ile pustego glow od swiatelek.",
            },
            "energetic_expert": {
                "talking_hook": "W kilka sekund widac, czy ten format ma performance, czy tylko ladny thumbnail.",
                "micro_story": "Tu nie chodzi o vibe dla samego vibe'u. Chodzi o retention, klarowny przekaz i timing.",
                "cta_teaser": "Jesli hook nie wbija sie od razu, CTA nie uratuje tej rolki.",
                "behind_the_scene": "Pokazuje proces jak operator: brief, test, korekta i dopiero potem szerszy batch.",
            },
        }
        hook = hook_map.get(style.key, hook_map[self.DEFAULT_STYLE_KEY]).get(
            reel.key,
            hook_map[self.DEFAULT_STYLE_KEY]["talking_hook"],
        )
        body_map = {
            "talking_hook": {
                "editorial_premium": (
                    f"Buduje spokojna, wiarygodna persone pod {niche}.",
                    "Najpierw sprawdzam jak siedzi twarz, glos i kontakt z kamera. Dopiero potem ide w wieksza serie.",
                ),
                "cozy_girlfriend": (
                    f"Tu ma dzialac bliskosc i lekki spokoj zamiast sprzedazowego tonu pod {niche}.",
                    "Jesli usmiech, glos i tempo sa naturalne, ten format ma sens rozwijac dalej.",
                ),
                "late_night_egirl": (
                    f"To ma byc nocny internetowy klimat pod {niche}, ale dalej czytelny i kontrolowany.",
                    "Jesli oczy, swiatlo i pacing dzialaja razem, ten wariant zostaje w shortlistcie.",
                ),
                "energetic_expert": (
                    f"To jest szybki performance test pod {niche}.",
                    "Liczy sie jasny przekaz, kontakt z kamera i brak lania wody juz w pierwszych sekundach.",
                ),
            },
            "micro_story": {
                "editorial_premium": (
                    f"Biore elegancki, czysty styl i skladam go pod {niche}.",
                    "Jesli obraz trzyma klase, glos nie brzmi syntetycznie, a pacing nie siada, z tego robi sie seria.",
                    "To jest maly screening przed mocniejszym batchiem rolek.",
                ),
                "cozy_girlfriend": (
                    f"Najpierw ustawiam bliski, cieply klimat dla {niche}.",
                    "Potem sprawdzam, czy kamera nadal czuje osobe, a nie tylko wygenerowany ladny portret.",
                    "Jesli to trzyma naturalnosc, warto to rozwijac.",
                ),
                "late_night_egirl": (
                    "Najpierw wchodzi nocny klimat i mocniejszy kontakt z kamera.",
                    "Potem sprawdzam, czy neon i pacing buduja napiecie, a nie tylko powierzchowny efekt.",
                    "Jesli to oglada sie do konca, wariant przechodzi dalej.",
                ),
                "energetic_expert": (
                    "Ten wariant ma dzialac szybko, jasno i bez ozdobnikow.",
                    "Jesli historia trzyma rytm i nie rozmiekcza hooka, format nadaje sie do skalowania.",
                    "To jest test pod retention, nie pod sam look.",
                ),
            },
            "cta_teaser": {
                "editorial_premium": (
                    "Tutaj liczy sie mocny pierwszy kadr i glos, ktory nie brzmi jak demo.",
                    "Jesli teaser zatrzymuje wzrok i naturalnie domyka przejscie do kolejnego kroku, zostawiam go w pakiecie.",
                ),
                "cozy_girlfriend": (
                    "Ten teaser ma byc lekki, bliski i bez agresywnego domykania.",
                    "Jesli zatrzymuje uwage bez sztucznego ciśnienia, ten kierunek ma sens.",
                ),
                "late_night_egirl": (
                    "Tutaj najwazniejszy jest klimat, ktory lapie od razu i nie rozmywa sie po trzech sekundach.",
                    "Jesli teaser buduje ciekawosc bez chaosu, ten wariant zostaje.",
                ),
                "energetic_expert": (
                    "Najpierw liczy sie precyzyjny hook. Potem klarowny payoff i lekki ruch w strone kolejnego kroku.",
                    "Jesli to nie niesie konkretu, wycinam bez sentymentu.",
                ),
            },
            "behind_the_scene": {
                "editorial_premium": (
                    "To jest kulisowy test jak skladam twarz, glos i pacing do jednej premium calosci.",
                    "Jesli proces wyglada dojrzale, a nie jak prototyp, wariant zostaje.",
                    "Reszta odpada zanim zje wiecej czasu GPU.",
                ),
                "cozy_girlfriend": (
                    "Tutaj pokazuje proces spokojniej: mniej efektu, wiecej sprawdzania czy vibe jest naturalny.",
                    "Jesli kulisy nadal wygladaja jak gotowy format, to dobry znak.",
                    "Jesli nie, wycinam i ide dalej.",
                ),
                "late_night_egirl": (
                    "To jest test czy nocny styl ma substance, a nie tylko swiatelka.",
                    "Patrze czy twarz, glos i pacing niosa ten format bez podporek.",
                    "Jesli nie, wariant nie idzie dalej.",
                ),
                "energetic_expert": (
                    "Pokazuje proces jak operator: brief, generacja, walidacja, korekta.",
                    "Tu nie ma miejsca na efekt bez wyniku.",
                    "Przechodza tylko te warianty, ktore sa czytelne i wydajne.",
                ),
            },
        }
        body = body_map.get(reel.key, body_map["talking_hook"]).get(
            style.key,
            body_map[reel.key][self.DEFAULT_STYLE_KEY] if reel.key in body_map else body_map["talking_hook"][self.DEFAULT_STYLE_KEY],
        )
        bundle = SyntheticScriptBundle(
            title=f"{base_name} / {style.label} / {reel.label}",
            hook=hook,
            body=body[: reel.max_body_lines],
            outro="Jesli ten klimat trzyma poziom, rozwijam z tego cala serie.",
            portrait_prompt=self._build_style_locked_portrait_prompt(project=project, style=style),
            visual_direction=style.visual_direction,
            engine="fallback-template",
        )
        return self._apply_hard_controls_to_script_bundle(
            bundle=bundle,
            reel=reel,
            hard_controls=hard_controls,
            base_voice_speed=style.voice_speed,
        )

    def _finalize_script_bundle(
        self,
        *,
        project: CreatorProjectView,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        bundle: SyntheticScriptBundle,
        hard_controls: SyntheticPreviewHardControls | None = None,
    ) -> SyntheticScriptBundle:
        if self._is_low_quality_script_bundle(bundle):
            return self._build_fallback_script_bundle_v2(
                project=project,
                style=style,
                reel=reel,
                hard_controls=hard_controls,
            )
        body = tuple(self._clean_text(item) for item in bundle.body if self._clean_text(item))[: reel.max_body_lines]
        if not body:
            return self._build_fallback_script_bundle_v2(
                project=project,
                style=style,
                reel=reel,
                hard_controls=hard_controls,
            )
        title = self._clean_text(bundle.title) or f"{project.name} / {style.label} / {reel.label}"
        hook = self._clean_text(bundle.hook)
        outro = self._clean_text(bundle.outro) or "Jesli ten klimat trzyma poziom, rozwijam z tego cala serie."
        if not hook:
            return self._build_fallback_script_bundle_v2(
                project=project,
                style=style,
                reel=reel,
                hard_controls=hard_controls,
            )
        bundle = SyntheticScriptBundle(
            title=title,
            hook=hook,
            body=body,
            outro=outro,
            portrait_prompt=self._build_style_locked_portrait_prompt(project=project, style=style),
            visual_direction=style.visual_direction,
            engine=bundle.engine,
        )
        return self._apply_hard_controls_to_script_bundle(
            bundle=bundle,
            reel=reel,
            hard_controls=hard_controls,
            base_voice_speed=style.voice_speed,
        )

    def _apply_hard_controls_to_script_bundle(
        self,
        *,
        bundle: SyntheticScriptBundle,
        reel: SyntheticReelPreset,
        hard_controls: SyntheticPreviewHardControls | None,
        base_voice_speed: float,
    ) -> SyntheticScriptBundle:
        if hard_controls is None:
            return bundle
        body_limit = min(
            reel.max_body_lines,
            hard_controls.max_body_lines if hard_controls.max_body_lines is not None else reel.max_body_lines,
        )
        hook = self._trim_words(bundle.hook, hard_controls.max_hook_words)
        body = tuple(bundle.body[:body_limit])
        outro = "" if hard_controls.drop_outro else bundle.outro
        voice_speed = self._resolve_voice_speed(base_voice_speed, hard_controls)
        hook, body, outro = self._tighten_script_to_duration(
            hook=hook,
            body=body,
            outro=outro,
            target_duration_cap_sec=hard_controls.target_duration_cap_sec,
            voice_speed=voice_speed,
        )
        if not body:
            body = tuple(bundle.body[:1]) or ("To jest finalny test, czy ten format siada bez lania wody.",)
        return SyntheticScriptBundle(
            title=bundle.title,
            hook=hook,
            body=body,
            outro=outro,
            portrait_prompt=bundle.portrait_prompt,
            visual_direction=bundle.visual_direction,
            engine=bundle.engine,
        )

    def _tighten_script_to_duration(
        self,
        *,
        hook: str,
        body: tuple[str, ...],
        outro: str,
        target_duration_cap_sec: float | None,
        voice_speed: float,
    ) -> tuple[str, tuple[str, ...], str]:
        if target_duration_cap_sec is None or target_duration_cap_sec <= 0:
            return hook, body, outro
        current_hook = hook
        current_body = [item for item in body]
        current_outro = outro
        hook_limit = max(6, len(current_hook.split()))
        while self._estimate_narration_duration_sec(
            " ".join(part for part in (current_hook, *current_body, current_outro) if part),
            voice_speed=voice_speed,
        ) > target_duration_cap_sec:
            if current_outro:
                current_outro = ""
                continue
            if len(current_body) > 1:
                current_body.pop()
                continue
            if hook_limit > 6:
                hook_limit -= 1
                current_hook = self._trim_words(current_hook, hook_limit)
                continue
            if current_body:
                shortened = self._trim_words(current_body[-1], max(6, len(current_body[-1].split()) - 3))
                if shortened != current_body[-1]:
                    current_body[-1] = shortened
                    continue
            break
        return current_hook, tuple(item for item in current_body if item), current_outro

    @staticmethod
    def _estimate_narration_duration_sec(text: str, *, voice_speed: float) -> float:
        words = len([part for part in text.split() if part.strip()])
        if words <= 0:
            return 0.0
        effective_speed = max(0.7, voice_speed)
        return round((words / (2.45 * effective_speed)) + 0.45, 2)

    @staticmethod
    def _trim_words(text: str, max_words: int | None) -> str:
        normalized = " ".join(text.split()).strip()
        if not normalized or max_words is None or max_words <= 0:
            return normalized
        words = normalized.split()
        if len(words) <= max_words:
            return normalized
        trimmed = " ".join(words[:max_words]).rstrip(",;:-")
        if trimmed and trimmed[-1] not in ".!?":
            trimmed += "."
        return trimmed

    @staticmethod
    def _resolve_voice_speed(base_speed: float, hard_controls: SyntheticPreviewHardControls | None) -> float:
        multiplier = hard_controls.voice_speed_multiplier if hard_controls is not None else 1.0
        return round(min(1.18, max(0.82, base_speed * multiplier)), 2)

    @staticmethod
    def _apply_hard_controls_to_cutaways(
        *,
        cutaways: SyntheticBrollPlanResult,
        hard_controls: SyntheticPreviewHardControls | None,
    ) -> SyntheticBrollPlanResult:
        if hard_controls is None or hard_controls.max_cutaways is None or hard_controls.max_cutaways <= 0:
            return cutaways
        if len(cutaways.assets) <= hard_controls.max_cutaways:
            return cutaways
        return SyntheticBrollPlanResult(
            assets=cutaways.assets[: hard_controls.max_cutaways],
            engine=cutaways.engine,
            warnings=cutaways.warnings,
        )

    @staticmethod
    def _needs_refinement_retry(
        preview: dict[str, Any],
        hard_controls: SyntheticPreviewHardControls | None,
    ) -> bool:
        if hard_controls is None:
            return False
        script_bundle = dict(preview.get("script_bundle") or {})
        artifacts = dict(preview.get("artifacts") or {})
        motion_reel = dict(artifacts.get("motion_reel") or {})
        qa = dict(motion_reel.get("qa") or {})
        duration = float(qa.get("duration_sec") or 0.0)
        if hard_controls.target_duration_window_sec is not None:
            _, maximum = hard_controls.target_duration_window_sec
            if duration > maximum:
                return True
        if hard_controls.target_duration_cap_sec is not None and duration > hard_controls.target_duration_cap_sec + 0.15:
            return True
        if hard_controls.max_hook_words is not None:
            hook_words = len(str(script_bundle.get("hook") or "").split())
            if hook_words > hard_controls.max_hook_words:
                return True
        if hard_controls.max_body_lines is not None:
            body_count = len(list(script_bundle.get("body") or []))
            if body_count > hard_controls.max_body_lines:
                return True
        return False

    def _is_low_quality_script_bundle(self, bundle: SyntheticScriptBundle) -> bool:
        lowered = self._normalize_quality_text(bundle.narration_text)
        if not lowered:
            return True
        if any(marker in lowered for marker in self.LOW_QUALITY_SCRIPT_MARKERS):
            return True
        if len(bundle.hook.split()) > 20:
            return True
        if len(bundle.body) > 3:
            return True
        if "..." in bundle.hook:
            return True
        return False

    def _build_style_locked_portrait_prompt(
        self,
        *,
        project: CreatorProjectView,
        style: SyntheticStylePreset,
    ) -> str:
        niche = self._clean_text(project.niche) or "premium synthetic creator"
        return (
            f"original adult synthetic creator portrait, fully clothed, direct eye contact, clean background, "
            f"natural skin texture, waist-up framing, 9:16 composition, safe for work, non-explicit, inspired by {niche}, "
            f"{', '.join(style.portrait_keywords)}"
        )

    def _generate_validated_portrait_artifact(
        self,
        *,
        project: CreatorProjectView,
        bundle: SyntheticScriptBundle,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        conversation_id: int | None,
        comfyui_steps_override: int | None = None,
        comfyui_cfg_scale_override: float | None = None,
    ) -> ManagedArtifactDescriptor:
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_PORTRAIT_ATTEMPTS + 1):
            output_path = self.artifacts.create_output_path(
                conversation_id=conversation_id,
                extension=".png",
            )
            seed = self._portrait_seed(project, style_key=style.key, reel_type=reel.key) + (attempt - 1) * 9973
            prompt = self._build_portrait_generation_prompt(
                project=project,
                bundle=bundle,
                style=style,
                attempt=attempt,
            )
            self.comfyui.generate_background(
                prompt=prompt,
                negative_prompt=self.PORTRAIT_NEGATIVE_PROMPT,
                output_path=output_path,
                width=self.PORTRAIT_SIZE[0],
                height=self.PORTRAIT_SIZE[1],
                seed=seed,
                steps_override=comfyui_steps_override,
                cfg_scale_override=comfyui_cfg_scale_override,
            )
            descriptor = self.artifacts.finalize_output(
                absolute_path=output_path,
                filename="atnos-synthetic-portrait.png",
                media_type="image/png",
            )
            try:
                self.wav2lip.verify_face_detectable(portrait_path=descriptor.absolute_path)
                return descriptor
            except Exception as exc:
                last_error = exc
        raise RuntimeError(
            f"Synthetic creator portrait validation failed after {self.MAX_PORTRAIT_ATTEMPTS} attempts: {last_error}"
        )

    def _generate_validated_portrait_artifact_with_reflection(
        self,
        *,
        project: CreatorProjectView,
        bundle: SyntheticScriptBundle,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        conversation_id: int | None,
        execution_run,
    ) -> ManagedArtifactDescriptor:
        steps_override: int | None = None
        cfg_scale_override: float | None = None
        for attempt in range(1, 3):
            try:
                return self._generate_validated_portrait_artifact(
                    project=project,
                    bundle=bundle,
                    style=style,
                    reel=reel,
                    conversation_id=conversation_id,
                    comfyui_steps_override=steps_override,
                    comfyui_cfg_scale_override=cfg_scale_override,
                )
            except Exception as exc:
                plan = self.reflector.reflect_image_generation_failure(
                    stage_key="synthetic_identity",
                    error_message=str(exc),
                    attempt=attempt,
                )
                if plan is None or not plan.retry_allowed:
                    raise
                self._record_reflector_step(
                    execution_run=execution_run,
                    source_step="graph.synthetic_identity",
                    attempt=attempt,
                    plan=plan.to_dict(),
                )
                steps_override = int(plan.adjustments.get("comfyui_steps")) if plan.adjustments.get("comfyui_steps") is not None else steps_override
                cfg_scale_override = (
                    float(plan.adjustments.get("comfyui_cfg_scale"))
                    if plan.adjustments.get("comfyui_cfg_scale") is not None
                    else cfg_scale_override
                )
        raise RuntimeError("Synthetic identity reflector exhausted its retry budget.")

    def _build_portrait_generation_prompt(
        self,
        *,
        project: CreatorProjectView,
        bundle: SyntheticScriptBundle,
        style: SyntheticStylePreset,
        attempt: int,
    ) -> str:
        niche = self._clean_text(project.niche) or "premium synthetic creator"
        direction = self._clean_text(bundle.visual_direction) or "premium editorial portrait"
        prompt_parts = [
            "photorealistic studio portrait photo of one adult woman",
            "front-facing and looking directly into camera",
            "tight head-and-shoulders framing",
            "face centered and fully visible",
            "natural skin texture and realistic eyes",
            "fully clothed, tasteful styling, safe for work",
            "clean seamless background",
            "soft editorial lighting",
            f"premium creator branding for {niche}",
            *style.portrait_keywords,
            direction,
        ]
        if attempt >= 2:
            prompt_parts.extend(
                [
                    "passport-style centered composition",
                    "no profile angle",
                    "no props",
                    "no hands in frame",
                    "no hair covering the face",
                ]
            )
        if attempt >= 3:
            prompt_parts.extend(
                [
                    "upper face and mouth sharply visible",
                    "symmetrical face presentation",
                    "plain background only",
                ]
            )
        return ", ".join(prompt_parts)

    def _compose_motion_reel_with_reflection(
        self,
        *,
        portrait_path: Path,
        talking_head_path: Path,
        audio_path: Path,
        subtitles_path: Path | None,
        cutaway_paths: tuple[Path, ...],
        conversation_id: int | None,
        style_key: str,
        style_label: str,
        reel_label: str,
        hook_text: str,
        hard_controls: SyntheticPreviewHardControls | None,
        quality_profile: str,
        execution_run,
    ) -> RenderArtifactResult:
        render_profile = self._render_profile_for_quality(quality_profile)
        max_cutaways = hard_controls.max_cutaways if hard_controls is not None else None
        cutaway_display_duration_sec = (
            hard_controls.cutaway_display_duration_sec if hard_controls is not None else None
        )
        target_duration_cap_sec = (
            hard_controls.target_duration_cap_sec if hard_controls is not None else None
        )
        target_duration_window_sec = (
            hard_controls.target_duration_window_sec if hard_controls is not None else None
        )
        hook_duration_cap_sec = (
            hard_controls.hook_duration_cap_sec if hard_controls is not None else None
        )

        for attempt in range(1, 4):
            try:
                return self.motion_reel.compose(
                    portrait_path=portrait_path,
                    talking_head_path=talking_head_path,
                    audio_path=audio_path,
                    subtitles_path=subtitles_path,
                    cutaway_paths=cutaway_paths,
                    conversation_id=conversation_id,
                    style_key=style_key,
                    style_label=style_label,
                    reel_label=reel_label,
                    hook_text=hook_text,
                    target_duration_cap_sec=target_duration_cap_sec,
                    target_duration_window_sec=target_duration_window_sec,
                    hook_duration_cap_sec=hook_duration_cap_sec,
                    max_cutaways=max_cutaways,
                    cutaway_display_duration_sec=cutaway_display_duration_sec,
                    render_profile=render_profile,
                )
            except Exception as exc:
                plan = self.reflector.reflect_render_failure(
                    stage_key="synthetic_motion_reel",
                    error_message=str(exc),
                    attempt=attempt,
                    current_render_profile=render_profile,
                    cutaway_count=len(cutaway_paths),
                )
                if plan is None or not plan.retry_allowed:
                    raise
                self._record_reflector_step(
                    execution_run=execution_run,
                    source_step="graph.synthetic_motion_reel",
                    attempt=attempt,
                    plan=plan.to_dict(),
                )
                render_profile = str(plan.adjustments.get("render_profile") or render_profile)
                if plan.adjustments.get("max_cutaways") is not None:
                    max_cutaways = int(plan.adjustments["max_cutaways"])
                if plan.adjustments.get("cutaway_display_duration_sec") is not None:
                    cutaway_display_duration_sec = float(plan.adjustments["cutaway_display_duration_sec"])
                if plan.adjustments.get("target_duration_cap_sec") is not None:
                    target_duration_cap_sec = float(plan.adjustments["target_duration_cap_sec"])
        raise RuntimeError("Synthetic motion reflector exhausted its retry budget.")

    def _record_reflector_step(
        self,
        *,
        execution_run,
        source_step: str,
        attempt: int,
        plan: dict[str, object],
    ) -> None:
        if execution_run is None:
            return
        self._run_step(
            execution_run=execution_run,
            step_type="graph.reflector",
            step_name="Reflector",
            input_json={"source_step": source_step, "attempt": attempt},
            fn=lambda: plan,
        )

    def _portrait_seed(
        self,
        project: CreatorProjectView,
        *,
        style_key: str | None = None,
        reel_type: str | None = None,
    ) -> int:
        raw = (
            f"{project.id}:{project.project_id}:{project.name}:{project.niche or ''}:{style_key or ''}:{reel_type or ''}"
        ).encode("utf-8")
        return int(hashlib.sha256(raw).hexdigest()[:8], 16)

    def _write_report_bundle(
        self,
        *,
        project: CreatorProjectView,
        goal: str,
        conversation_id: int | None,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        script_bundle: SyntheticScriptBundle,
        portrait: ManagedArtifactDescriptor,
        cutaways: SyntheticBrollPlanResult,
        audio: ManagedArtifactDescriptor,
        subtitles: ManagedArtifactDescriptor,
        talking_head: Wav2LipArtifactResult,
        motion_reel: RenderArtifactResult,
    ) -> dict[str, object]:
        markdown = self._build_report_markdown(
            project=project,
            goal=goal,
            style=style,
            reel=reel,
            script_bundle=script_bundle,
            portrait=portrait,
            cutaways=cutaways,
            audio=audio,
            subtitles=subtitles,
            talking_head=talking_head,
            motion_reel=motion_reel,
        )
        artifact = self._write_markdown_artifact(
            conversation_id=conversation_id,
            filename="atnos-synthetic-creator-preview.md",
            content=markdown,
        )
        vault_note_path = self._write_obsidian_note(project_name=project.name, content=markdown)
        return {
            "artifact": artifact,
            "vault_note_path": vault_note_path,
        }

    def _write_batch_report_bundle(
        self,
        *,
        project: CreatorProjectView,
        conversation_id: int | None,
        variants: list[dict[str, Any]],
        selection: dict[str, object],
        refinement_plan: SyntheticVariantRefinementPlan | None,
        refined_best_variant: dict[str, Any] | None,
        refined_score: dict[str, object] | None,
        refinement_attempts: list[dict[str, object]],
        release_pack: SyntheticReleasePack | None,
        analytics_learning_profile: dict[str, Any] | None,
        variant_planning_profile: dict[str, Any] | None,
    ) -> dict[str, object]:
        markdown = self._build_batch_report_markdown(
            project=project,
            variants=variants,
            selection=selection,
            refinement_plan=refinement_plan,
            refined_best_variant=refined_best_variant,
            refined_score=refined_score,
            refinement_attempts=refinement_attempts,
            release_pack=release_pack,
            analytics_learning_profile=analytics_learning_profile,
            variant_planning_profile=variant_planning_profile,
        )
        artifact = self._write_markdown_artifact(
            conversation_id=conversation_id,
            filename="atnos-synthetic-creator-batch.md",
            content=markdown,
        )
        vault_note_path = self._write_obsidian_note(project_name=f"{project.name}-batch", content=markdown)
        return {
            "artifact": artifact,
            "vault_note_path": vault_note_path,
        }

    def _build_batch_report_markdown(
        self,
        *,
        project: CreatorProjectView,
        variants: list[dict[str, Any]],
        selection: dict[str, object],
        refinement_plan: SyntheticVariantRefinementPlan | None,
        refined_best_variant: dict[str, Any] | None,
        refined_score: dict[str, object] | None,
        refinement_attempts: list[dict[str, object]],
        release_pack: SyntheticReleasePack | None,
        analytics_learning_profile: dict[str, Any] | None,
        variant_planning_profile: dict[str, Any] | None,
    ) -> str:
        best_variant_index = selection.get("best_variant_index")
        ranked_variants = list(selection.get("ranked_variants") or [])
        lines = [
            f"# Synthetic Creator Batch - {project.name}",
            "",
            f"- variants: {len(variants)}",
            "",
        ]
        if ranked_variants:
            lines.extend(
                [
                    "## Selection",
                    "",
                    f"- best_variant_index: {best_variant_index if best_variant_index is not None else 'brak'}",
                    f"- best_score: {selection.get('best_score') or 'brak'}",
                    f"- best_confidence: {selection.get('best_confidence') or 'brak'}",
                    "",
                ]
            )
            for rank, item in enumerate(ranked_variants, start=1):
                row = dict(item)
                strengths = list(row.get("strengths") or [])
                risks = list(row.get("risks") or [])
                lines.extend(
                    [
                        f"- rank {rank}: variant {row.get('variant_index')} / score {row.get('score')}",
                        f"  - confidence: {row.get('confidence')}",
                        f"  - strengths: {', '.join(strengths) if strengths else 'brak'}",
                        f"  - risks: {', '.join(risks) if risks else 'brak'}",
                    ]
                )
            lines.append("")
        learning_profile = dict(analytics_learning_profile or {})
        if int(learning_profile.get("sample_count") or 0) > 0:
            lines.extend(
                [
                    "## Historical analytics learning",
                    "",
                    f"- sample_count: {learning_profile.get('sample_count')}",
                    f"- baseline_average_view_percentage: {learning_profile.get('baseline_average_view_percentage') or 'brak'}",
                    f"- baseline_views: {learning_profile.get('baseline_views') or 'brak'}",
                    f"- strongest_style_key: {learning_profile.get('strongest_style_key') or 'brak'}",
                    f"- strongest_reel_key: {learning_profile.get('strongest_reel_key') or 'brak'}",
                    f"- strongest_hook_strategy: {learning_profile.get('strongest_hook_strategy') or 'brak'}",
                    f"- strongest_pacing_strategy: {learning_profile.get('strongest_pacing_strategy') or 'brak'}",
                    f"- strongest_cutaway_strategy: {learning_profile.get('strongest_cutaway_strategy') or 'brak'}",
                    f"- strongest_title_strategy: {learning_profile.get('strongest_title_strategy') or 'brak'}",
                ]
            )
            insights = [str(item).strip() for item in list(learning_profile.get("insights") or []) if str(item).strip()]
            if insights:
                lines.extend(
                    [
                        "- insights:",
                        *[f"  - {item}" for item in insights[:4]],
                    ]
                )
            lines.append("")
        planning_profile = dict(variant_planning_profile or {})
        planned_recommendations = [dict(item) for item in list(planning_profile.get("recommendations") or [])]
        if planned_recommendations:
            lines.extend(
                [
                    "## Variant planning",
                    "",
                    f"- sample_count: {planning_profile.get('sample_count') or 0}",
                    f"- strongest_style_key: {planning_profile.get('strongest_style_key') or 'brak'}",
                    f"- strongest_reel_key: {planning_profile.get('strongest_reel_key') or 'brak'}",
                ]
            )
            strategy_summary = [
                str(item).strip()
                for item in list(planning_profile.get("strategy_summary") or [])
                if str(item).strip()
            ]
            if strategy_summary:
                lines.extend(
                    [
                        "- strategy_summary:",
                        *[f"  - {item}" for item in strategy_summary[:4]],
                    ]
                )
            lines.extend(["- recommendations:"])
            for item in planned_recommendations:
                lines.extend(
                    [
                        f"  - {item.get('style_key')} / {item.get('reel_key')} / priority {item.get('priority_score')}",
                        f"    - kind: {item.get('candidate_kind') or 'brak'}",
                        f"    - rationale: {', '.join(str(x) for x in list(item.get('rationale') or [])) or 'brak'}",
                        f"    - initial_hard_controls: {item.get('initial_hard_controls') or 'brak'}",
                    ]
                )
            lines.append("")
        if refinement_plan is not None:
            lines.extend(
                [
                    "## Refinement",
                    "",
                    f"- source_variant_index: {refinement_plan.source_variant_index}",
                    f"- style: {refinement_plan.style_key or 'brak'}",
                    f"- reel_type: {refinement_plan.reel_key or 'brak'}",
                    f"- score_before: {refinement_plan.score_before or 'brak'}",
                    f"- confidence_before: {refinement_plan.confidence_before or 'brak'}",
                ]
            )
            if refinement_plan.target_duration_window_sec is not None:
                lines.append(
                    f"- target_duration_window_sec: {list(refinement_plan.target_duration_window_sec)}"
                )
            if refinement_plan.hard_controls is not None:
                lines.append(f"- hard_controls: {refinement_plan.hard_controls.to_dict()}")
            lines.extend(
                [
                    "- directives:",
                    *[f"  - {item}" for item in refinement_plan.directives],
                    "",
                ]
            )
            if refinement_attempts:
                lines.extend(["### Refinement attempts", ""])
                for attempt in refinement_attempts:
                    row = dict(attempt)
                    lines.extend(
                        [
                            f"- attempt {row.get('attempt')}: duration {row.get('duration_sec') or 'brak'} / score {row.get('score') or 'brak'}",
                            f"  - confidence: {row.get('confidence') or 'brak'}",
                            f"  - target_met: {row.get('target_met')}",
                            f"  - hard_controls: {row.get('hard_controls') or 'brak'}",
                        ]
                    )
                lines.append("")
            if refined_best_variant is not None:
                refined_artifacts = dict(refined_best_variant.get("artifacts") or {})
                refined_motion = dict(refined_artifacts.get("motion_reel") or {})
                refined_motion_descriptor = dict(refined_motion.get("descriptor") or {})
                lines.extend(
                    [
                        "### Refined winner",
                        "",
                        f"- motion_reel: {refined_motion_descriptor.get('storage_path') or 'brak'}",
                        f"- cutaways: {len(list(dict(refined_artifacts.get('cutaways') or {}).get('assets') or []))}",
                    ]
                )
                if refined_score:
                    lines.extend(
                        [
                            f"- refined_score: {dict(refined_score).get('score') or 'brak'}",
                            f"- refined_confidence: {dict(refined_score).get('confidence') or 'brak'}",
                        ]
                    )
                lines.append("")
        if release_pack is not None:
            release_metadata = dict(release_pack.metadata)
            lines.extend(
                [
                    "## Release pack",
                    "",
                    f"- platform: {release_pack.platform_key}",
                    f"- publish_path: {release_pack.publish_path}",
                    f"- primary_title: {release_metadata.get('title') or 'brak'}",
                    f"- title_options: {', '.join(str(item) for item in list(release_metadata.get('title_options') or [])) or 'brak'}",
                    f"- hashtags: {' '.join(str(item) for item in list(release_metadata.get('hashtags') or [])) or 'brak'}",
                    f"- pinned_comment: {release_metadata.get('pinned_comment') or 'brak'}",
                    "",
                ]
            )
        lines.extend(
            [
            "## Variants",
            "",
            ]
        )
        for index, variant in enumerate(variants, start=1):
            style = dict(variant.get("style") or {})
            reel = dict(variant.get("reel_type") or {})
            artifacts = dict(variant.get("artifacts") or {})
            talking_head = dict(artifacts.get("talking_head") or {})
            talking_head_descriptor = dict(talking_head.get("descriptor") or {})
            motion_reel = dict(artifacts.get("motion_reel") or {})
            motion_reel_descriptor = dict(motion_reel.get("descriptor") or {})
            cutaways = dict(artifacts.get("cutaways") or {})
            cutaway_assets = list(cutaways.get("assets") or [])
            ranking_item = next(
                (dict(item) for item in ranked_variants if dict(item).get("variant_index") == index - 1),
                {},
            )
            lines.extend(
                [
                    f"### Variant {index}",
                    "",
                    f"- style: {style.get('label') or style.get('key') or 'brak'}",
                    f"- reel_type: {reel.get('label') or reel.get('key') or 'brak'}",
                    f"- ranked_score: {ranking_item.get('score') or 'brak'}",
                    f"- ranked_confidence: {ranking_item.get('confidence') or 'brak'}",
                    f"- voice: {style.get('voice_name') or 'brak'} @ {style.get('voice_speed') or 'brak'}",
                    f"- portrait: {dict(artifacts.get('portrait') or {}).get('storage_path') or 'brak'}",
                    f"- cutaways: {len(cutaway_assets)}",
                    f"- talking_head: {talking_head_descriptor.get('storage_path') or 'brak'}",
                    f"- motion_reel: {motion_reel_descriptor.get('storage_path') or 'brak'}",
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    def _build_report_markdown(
        self,
        *,
        project: CreatorProjectView,
        goal: str,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        script_bundle: SyntheticScriptBundle,
        portrait: ManagedArtifactDescriptor,
        cutaways: SyntheticBrollPlanResult,
        audio: ManagedArtifactDescriptor,
        subtitles: ManagedArtifactDescriptor,
        talking_head: Wav2LipArtifactResult,
        motion_reel: RenderArtifactResult,
    ) -> str:
        generated_at = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"# Synthetic Creator Preview - {project.name}",
            "",
            f"- generated_at: {generated_at}",
            f"- creator_project_id: {project.id}",
            f"- project_id: {project.project_id}",
            f"- goal: {goal}",
            f"- style: {style.label} ({style.key})",
            f"- reel_type: {reel.label} ({reel.key})",
            f"- engine_script: {script_bundle.engine}",
            f"- engine_image: comfyui",
            f"- engine_broll: {cutaways.engine}",
            f"- engine_voice: kokoro-local",
            f"- engine_lip_sync: {talking_head.engine}",
            f"- engine_motion_reel: {motion_reel.engine}",
            "",
            "## Script bundle",
            "",
            f"- title: {script_bundle.title}",
            f"- hook: {script_bundle.hook}",
        ]
        if script_bundle.body:
            lines.append("- body:")
            lines.extend(f"  - {item}" for item in script_bundle.body)
        lines.extend(
            [
                f"- outro: {script_bundle.outro}",
                f"- visual_direction: {script_bundle.visual_direction}",
                "",
                "## Runtime artifacts",
                "",
                f"- portrait: {portrait.storage_path}",
                f"- cutaways_count: {len(cutaways.assets)}",
                f"- audio: {audio.storage_path}",
                f"- subtitles: {subtitles.storage_path}",
                f"- talking_head: {talking_head.descriptor.storage_path}",
                f"- motion_reel: {motion_reel.descriptor.storage_path}",
                "",
            ]
        )
        if cutaways.assets:
            lines.extend(["## Cutaways", ""])
            for asset in cutaways.assets:
                lines.extend(
                    [
                        f"- {asset.label}: {asset.descriptor.storage_path}",
                        f"  - source_line: {asset.source_line}",
                    ]
                )
            lines.append("")
        if cutaways.warnings:
            lines.extend(["## B-roll warnings", ""])
            lines.extend(f"- {warning}" for warning in cutaways.warnings)
            lines.append("")
        lines.extend(
            [
                "## Guardrails",
                "",
                "- Oryginalna syntetyczna persona, bez podszywania sie pod realna osobe.",
                "- Dorosly, fully clothed, non-explicit visual direction.",
                "- Tylko lokalny preview. Bez publikacji i bez zewnetrznych akcji kontowych.",
            ]
        )
        return "\n".join(lines).strip() + "\n"

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

    def _write_json_artifact(
        self,
        *,
        conversation_id: int | None,
        filename: str,
        payload: dict[str, object],
    ) -> ManagedArtifactDescriptor:
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".json")
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=filename,
            media_type="application/json",
        )

    def _write_obsidian_note(self, *, project_name: str, content: str) -> str | None:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists or not config.export_enabled:
            return None
        safe_project = self._slugify(project_name)
        stamp = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        relative = Path("Atnos") / "Business" / "SyntheticCreator" / f"{stamp}-synthetic-preview-{safe_project}.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return relative.as_posix()

    def _write_release_pack_obsidian_note(self, *, project_name: str, content: str) -> str | None:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists or not config.export_enabled:
            return None
        safe_project = self._slugify(project_name)
        stamp = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        relative = Path("Atnos") / "Social" / "ReleasePacks" / f"{stamp}-synthetic-release-pack-{safe_project}.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return relative.as_posix()

    def _write_publish_feedback_obsidian_note(self, *, project_name: str, content: str) -> str | None:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists or not config.export_enabled:
            return None
        safe_project = self._slugify(project_name)
        stamp = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        relative = Path("Atnos") / "Social" / "Analytics" / f"{stamp}-synthetic-feedback-{safe_project}.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return relative.as_posix()

    def _attach_preview_message(
        self,
        *,
        conversation_id: int,
        project: CreatorProjectView,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        script_bundle: SyntheticScriptBundle,
        portrait: ManagedArtifactDescriptor,
        cutaways: SyntheticBrollPlanResult,
        audio: ManagedArtifactDescriptor,
        subtitles: ManagedArtifactDescriptor,
        talking_head: Wav2LipArtifactResult,
        motion_reel: RenderArtifactResult,
        report_artifact: ManagedArtifactDescriptor,
        vault_note_path: str | None,
    ) -> None:
        assistant_message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=(
                f"Przygotowalem automatyczny synthetic creator preview dla projektu `{project.name}`.\n\n"
                f"- style: {style.label}\n"
                f"- reel: {reel.label}\n"
                f"- hook: {script_bundle.hook}\n"
                f"- portrait: {portrait.filename}\n"
                f"- cutaways: {len(cutaways.assets)}\n"
                f"- final reel: {motion_reel.descriptor.filename}\n"
                f"- vault_note: {vault_note_path or 'brak'}"
            ),
            metadata_json={
                "synthetic_creator_preview": True,
                "creator_project_id": project.id,
                "project_id": project.project_id,
                "style": {"key": style.key, "label": style.label},
                "reel_type": {"key": reel.key, "label": reel.label},
                "script_bundle": script_bundle.to_dict(),
                "vault_note_path": vault_note_path,
            },
        )
        attachments = [
            ("Portrait", portrait),
            *((asset.label, asset.descriptor) for asset in cutaways.assets),
            ("Narration", audio),
            ("Subtitles", subtitles),
            ("Talking head", talking_head.descriptor),
            ("Motion reel", motion_reel.descriptor),
            ("Synthetic creator note", report_artifact),
        ]
        for label, artifact in attachments:
            self.attachments.create(
                filename=artifact.filename,
                storage_path=artifact.storage_path,
                kind=self._attachment_kind_for_extension(artifact.file_extension),
                conversation_id=conversation_id,
                message_id=assistant_message.id,
                source=AttachmentSource.GENERATED,
                status=AttachmentStatus.AVAILABLE,
                media_type=artifact.media_type,
                file_extension=artifact.file_extension,
                size_bytes=artifact.size_bytes,
                extracted_text=None,
                metadata_json={
                    "generated_by": "synthetic_creator_preview",
                    "label": label,
                    "creator_project_id": project.id,
                    "style_key": style.key,
                    "reel_type": reel.key,
                },
            )
        self.session.commit()

    def _attach_batch_summary_message(
        self,
        *,
        conversation_id: int,
        project: CreatorProjectView,
        selection: dict[str, object],
        refined_best_variant: dict[str, Any] | None,
        refined_score: dict[str, object] | None,
        batch_report_artifact: ManagedArtifactDescriptor,
        batch_vault_note_path: str | None,
        release_pack: SyntheticReleasePack | None,
        release_pack_artifact: ManagedArtifactDescriptor | None,
        release_pack_json_artifact: ManagedArtifactDescriptor | None,
        release_pack_vault_note_path: str | None,
    ) -> None:
        winner = refined_best_variant or {}
        winner_style = dict(dict(winner.get("style") or {})).get("label") or "brak"
        winner_reel = dict(dict(winner.get("reel_type") or {})).get("label") or "brak"
        metadata = dict(release_pack.metadata) if release_pack is not None else {}
        publish_preview = dict(release_pack.publish_result_preview) if release_pack is not None else {}
        assistant_message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=(
                f"Przygotowalem batch synthetic creator dla projektu `{project.name}`.\n\n"
                f"- winner: {winner_style} / {winner_reel}\n"
                f"- best_score: {selection.get('best_score') or 'brak'}\n"
                f"- refined_score: {dict(refined_score).get('score') if isinstance(refined_score, dict) else 'brak'}\n"
                f"- publish_status: {publish_preview.get('status') or 'brak'}\n"
                f"- release_title: {metadata.get('title') or 'brak'}\n"
                f"- batch_note: {batch_vault_note_path or 'brak'}\n"
                f"- release_note: {release_pack_vault_note_path or 'brak'}"
            ),
            metadata_json={
                "synthetic_creator_preview_batch": True,
                "creator_project_id": project.id,
                "project_id": project.project_id,
                "best_variant_index": selection.get("best_variant_index"),
                "best_score": selection.get("best_score"),
                "release_pack": release_pack.to_dict() if release_pack is not None else None,
                "batch_vault_note_path": batch_vault_note_path,
                "release_pack_vault_note_path": release_pack_vault_note_path,
            },
        )
        attachments: list[tuple[str, ManagedArtifactDescriptor]] = [("Batch report", batch_report_artifact)]
        motion_reel_payload = dict(dict(dict(winner.get("artifacts") or {}).get("motion_reel") or {}).get("descriptor") or {})
        motion_reel = self._descriptor_from_payload(motion_reel_payload)
        if motion_reel is not None:
            attachments.append(("Winning motion reel", motion_reel))
        report_payload = dict(dict(winner.get("artifacts") or {}).get("report") or {})
        report_artifact = self._descriptor_from_payload(report_payload)
        if report_artifact is not None:
            attachments.append(("Winner preview note", report_artifact))
        if release_pack_artifact is not None:
            attachments.append(("Release pack", release_pack_artifact))
        if release_pack_json_artifact is not None:
            attachments.append(("Release pack JSON", release_pack_json_artifact))
        for label, artifact in attachments:
            self.attachments.create(
                filename=artifact.filename,
                storage_path=artifact.storage_path,
                kind=self._attachment_kind_for_extension(artifact.file_extension),
                conversation_id=conversation_id,
                message_id=assistant_message.id,
                source=AttachmentSource.GENERATED,
                status=AttachmentStatus.AVAILABLE,
                media_type=artifact.media_type,
                file_extension=artifact.file_extension,
                size_bytes=artifact.size_bytes,
                extracted_text=None,
                metadata_json={
                    "generated_by": "synthetic_creator_preview_batch",
                    "label": label,
                    "creator_project_id": project.id,
                },
            )
        self.session.commit()

    def _resolve_style_preset(self, style_key: str | None) -> SyntheticStylePreset:
        normalized = (style_key or "").strip().lower()
        return self.STYLE_PRESETS.get(normalized, self.STYLE_PRESETS[self.DEFAULT_STYLE_KEY])

    def _resolve_reel_preset(self, reel_type: str | None) -> SyntheticReelPreset:
        normalized = (reel_type or "").strip().lower()
        return self.REEL_PRESETS.get(normalized, self.REEL_PRESETS[self.DEFAULT_REEL_KEY])

    def _expand_style_keys(self, style_keys: list[str] | None) -> list[SyntheticStylePreset]:
        if not style_keys:
            return list(self.STYLE_PRESETS.values())[:3]
        ordered: list[SyntheticStylePreset] = []
        seen: set[str] = set()
        for raw in style_keys:
            preset = self._resolve_style_preset(raw)
            if preset.key in seen:
                continue
            seen.add(preset.key)
            ordered.append(preset)
        return ordered or [self.STYLE_PRESETS[self.DEFAULT_STYLE_KEY]]

    def _expand_reel_keys(self, reel_types: list[str] | None) -> list[SyntheticReelPreset]:
        if not reel_types:
            return [
                self.REEL_PRESETS["talking_hook"],
                self.REEL_PRESETS["micro_story"],
                self.REEL_PRESETS["cta_teaser"],
            ]
        ordered: list[SyntheticReelPreset] = []
        seen: set[str] = set()
        for raw in reel_types:
            preset = self._resolve_reel_preset(raw)
            if preset.key in seen:
                continue
            seen.add(preset.key)
            ordered.append(preset)
        return ordered or [self.REEL_PRESETS[self.DEFAULT_REEL_KEY]]

    def _resolve_winner_variant_from_batch(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        refined = payload.get("refined_best_variant")
        if isinstance(refined, dict):
            return refined
        best_variant = payload.get("best_variant")
        if isinstance(best_variant, dict):
            return best_variant
        selection = dict(payload.get("selection") or {})
        best_variant_index = selection.get("best_variant_index")
        variants = list(payload.get("variants") or [])
        if isinstance(best_variant_index, int) and 0 <= best_variant_index < len(variants):
            candidate = variants[best_variant_index]
            return candidate if isinstance(candidate, dict) else None
        return None

    def _pending_analytics_summary(self, pack: SyntheticReleasePack) -> dict[str, Any]:
        followup = dict(pack.analytics_followup)
        if pack.publish_path == "official_youtube_ready":
            return {
                "status": "waiting_for_publish",
                "trigger": followup.get("trigger") or "post_publish",
                "message": "Analytics sync ruszy po zatwierdzeniu publikacji i uploadzie na YouTube.",
                "recommended_metrics": list(followup.get("recommended_metrics") or []),
                "recommended_review_window_hours": followup.get("recommended_review_window_hours"),
            }
        return followup

    def _publish_release_pack_if_possible(
        self,
        *,
        controls,
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

    def _build_publish_feedback_summary(
        self,
        *,
        project_name: str,
        release_pack_payload: dict[str, Any],
        publish_result: dict[str, Any],
        analytics_summary: dict[str, Any],
    ) -> str:
        metadata = dict(release_pack_payload.get("metadata") or {})
        connector = dict(release_pack_payload.get("connector_status") or {})
        return (
            f"# Synthetic publish feedback - {project_name}\n\n"
            f"- title: {metadata.get('title') or 'brak'}\n"
            f"- publish_status: {publish_result.get('status') or 'brak'}\n"
            f"- video_id: {publish_result.get('video_id') or 'brak'}\n"
            f"- analytics_status: {analytics_summary.get('status') or 'brak'}\n"
            f"- connector_ready: {connector.get('ready')}\n"
            f"- review_window_hours: {dict(release_pack_payload.get('analytics_followup') or {}).get('recommended_review_window_hours') or 'brak'}\n\n"
            "## Publish notes\n\n"
            f"{publish_result.get('message') or 'Upload zakonczony bez dodatkowej wiadomosci.'}\n\n"
            "## Analytics snapshot\n\n"
            f"{json.dumps(analytics_summary, ensure_ascii=False, indent=2)}\n"
        )

    def _run_step(
        self,
        *,
        execution_run,
        step_type: str,
        step_name: str,
        input_json: dict[str, object],
        fn,
    ):
        if execution_run is None:
            return fn()
        step_index = len(self.execution_runs.list_steps(execution_run.id))
        step = self.execution_runs.create_step(
            execution_run_id=execution_run.id,
            step_index=step_index,
            step_type=step_type,
            step_name=step_name,
            status="running",
            selector_hint=None,
            input_json=input_json,
            output_json={},
            error_message=None,
            screenshot_path=None,
            started_at=self._now(),
            finished_at=None,
        )
        self.session.commit()
        try:
            result = fn()
        except Exception as exc:
            step.status = "failed"
            step.error_message = str(exc)
            step.finished_at = self._now()
            self.execution_runs.save_step(step)
            self.session.commit()
            raise
        step.status = "done"
        step.output_json = self._serialize_step_output(result)
        step.finished_at = self._now()
        self.execution_runs.save_step(step)
        self.session.commit()
        return result

    def _serialize_step_output(self, value):
        return self._json_ready(value)

    def _json_ready(self, value):
        if isinstance(value, SyntheticScriptBundle):
            return value.to_dict()
        if isinstance(value, ManagedArtifactDescriptor):
            return self._serialize_artifact(value)
        if isinstance(value, Wav2LipArtifactResult):
            return value.to_dict()
        if isinstance(value, SyntheticBrollPlanResult):
            return value.to_dict()
        if isinstance(value, SyntheticVariantRefinementPlan):
            return value.to_dict()
        if isinstance(value, dict):
            return {str(key): self._json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._json_ready(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _serialize_artifact(artifact: ManagedArtifactDescriptor) -> dict[str, object]:
        return {
            "filename": artifact.filename,
            "storage_path": artifact.storage_path,
            "media_type": artifact.media_type,
            "file_extension": artifact.file_extension,
            "size_bytes": artifact.size_bytes,
        }

    def _write_release_pack_bundle(
        self,
        *,
        project: CreatorProjectView,
        conversation_id: int | None,
        preview: dict[str, Any],
        selection: dict[str, object],
        score_payload: dict[str, object] | None,
        analytics_learning_profile: dict[str, Any] | None,
    ) -> dict[str, object]:
        connector_status = self._resolve_youtube_connector_status()
        pack = self.release_pack.build_release_pack(
            project_name=project.name,
            preview=preview,
            selection=selection,
            score_payload=score_payload,
            connector_status=connector_status,
            analytics_learning_profile=analytics_learning_profile,
            project_strategy_profile=dict(project.strategy_profile or {}),
        )
        markdown = self.release_pack.build_markdown(pack)
        artifact = self._write_markdown_artifact(
            conversation_id=conversation_id,
            filename="atnos-synthetic-release-pack.md",
            content=markdown,
        )
        json_artifact = self._write_json_artifact(
            conversation_id=conversation_id,
            filename="atnos-synthetic-release-pack.json",
            payload=pack.to_dict(),
        )
        vault_note_path = self._write_release_pack_obsidian_note(project_name=project.name, content=markdown)
        return {
            "pack": pack,
            "artifact": artifact,
            "json_artifact": json_artifact,
            "vault_note_path": vault_note_path,
        }

    @staticmethod
    def _score_payload_from_selection(selection: dict[str, object]) -> dict[str, object] | None:
        best_variant_index = selection.get("best_variant_index")
        ranked_variants = list(selection.get("ranked_variants") or [])
        for item in ranked_variants:
            row = dict(item)
            if row.get("variant_index") == best_variant_index:
                return row
        if selection:
            return {
                "score": selection.get("best_score"),
                "confidence": selection.get("best_confidence"),
            }
        return None

    def _descriptor_from_payload(self, payload: dict[str, object]) -> ManagedArtifactDescriptor | None:
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
            absolute_path=self.artifacts.resolve_download_path(storage_path),
            media_type=media_type,
            file_extension=file_extension,
            size_bytes=size_bytes,
        )

    def _resolve_youtube_connector_status(self) -> dict[str, object]:
        fallback = {
            "connector_key": "youtube-studio",
            "display_name": "YouTube Studio",
            "configured": False,
            "connected": False,
            "ready": False,
            "warnings": ["YouTube connector status is unavailable in this runtime."],
            "account_id": None,
            "channel_title": None,
            "granted_scopes": [],
        }
        try:
            status = self.youtube.get_status()
        except Exception as exc:
            fallback["warnings"] = [f"Failed to read YouTube connector status: {exc}"]
            return fallback
        payload = status.to_dict()
        if bool(payload.get("ready")):
            try:
                return self.youtube.probe().to_dict()
            except Exception as exc:
                warnings = list(payload.get("warnings") or [])
                warnings.append(f"YouTube live probe failed: {exc}")
                payload["warnings"] = warnings
                payload["ready"] = False
                return payload
        return payload

    @staticmethod
    def _attachment_kind_for_extension(extension: str) -> AttachmentKind:
        normalized = extension.lower().strip()
        if normalized in {".png", ".jpg", ".jpeg"}:
            return AttachmentKind.IMAGE
        if normalized in {".wav"}:
            return AttachmentKind.AUDIO
        if normalized in {".mp4"}:
            return AttachmentKind.VIDEO
        if normalized in {".md", ".txt", ".srt", ".json"}:
            return AttachmentKind.TEXT
        return AttachmentKind.OTHER

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized or "synthetic-creator"

    @staticmethod
    def _normalize_quality_text(value: object) -> str:
        cleaned = SyntheticCreatorWorkflowService._clean_text(value).lower()
        normalized = unicodedata.normalize("NFKD", cleaned)
        return "".join(ch for ch in normalized if not unicodedata.combining(ch))

    @staticmethod
    def _clean_text(value: object) -> str:
        if not isinstance(value, str):
            return ""
        collapsed = re.sub(r"\s+", " ", value).strip()
        return collapsed

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC)
