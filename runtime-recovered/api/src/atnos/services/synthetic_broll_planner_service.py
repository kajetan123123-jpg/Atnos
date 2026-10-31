from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING

from atnos.core.config import Settings
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
from atnos.services.creator_ops_service import CreatorProjectView
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService

if TYPE_CHECKING:
    from atnos.services.synthetic_creator_workflow_service import (
        SyntheticReelPreset,
        SyntheticScriptBundle,
        SyntheticStylePreset,
    )


@dataclass(frozen=True, slots=True)
class SyntheticCutawayAsset:
    descriptor: ManagedArtifactDescriptor
    label: str
    source_line: str
    prompt: str

    def to_dict(self) -> dict[str, object]:
        return {
            "descriptor": {
                "filename": self.descriptor.filename,
                "storage_path": self.descriptor.storage_path,
                "media_type": self.descriptor.media_type,
                "file_extension": self.descriptor.file_extension,
                "size_bytes": self.descriptor.size_bytes,
            },
            "label": self.label,
            "source_line": self.source_line,
            "prompt": self.prompt,
        }


@dataclass(frozen=True, slots=True)
class SyntheticBrollPlanResult:
    assets: tuple[SyntheticCutawayAsset, ...]
    engine: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "assets": [asset.to_dict() for asset in self.assets],
            "engine": self.engine,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _CutawayPromptSpec:
    label: str
    source_line: str
    prompt: str
    negative_prompt: str


class SyntheticBrollPlannerService:
    CUTAWAY_SIZE = (720, 1280)
    MAX_CUTAWAYS = 3
    STYLE_SCENE_HINTS: dict[str, tuple[str, ...]] = {
        "editorial_premium": (
            "luxury beauty desk with reflective chrome details",
            "silk fabric texture with clean editorial styling",
            "high-end perfume or skincare still life with tactile highlights",
        ),
        "cozy_girlfriend": (
            "warm coffee mug, knit texture and soft window light",
            "cozy couch corner with blanket folds and intimate daylight",
            "bedside table detail with candle glow and gentle shadows",
        ),
        "late_night_egirl": (
            "keyboard glow, headphones and subtle neon reflections",
            "late-night desk texture with monitor spill and glossy props",
            "phone, mirror and ambient LED mood with internet-native styling",
        ),
        "energetic_expert": (
            "editing desk with notebook, phone and sharp commercial light",
            "dashboard-style setup with pen, notes and performance energy",
            "high-clarity workspace props with clean motion-friendly composition",
        ),
    }
    DEFAULT_NEGATIVE_PROMPT = (
        "close-up portrait face, person staring at camera, text, logo, watermark, "
        "explicit content, nudity, minor, lowres, blurry, duplicate objects, distorted hands, deformed anatomy"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.comfyui = ComfyUiImageGenerationService(settings)
        self.artifacts = OutputArtifactService(settings)

    def generate_cutaways(
        self,
        *,
        project: CreatorProjectView,
        script_bundle: SyntheticScriptBundle,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
        conversation_id: int | None,
        quality_profile: str = "standard",
    ) -> SyntheticBrollPlanResult:
        prompt_specs = self._build_prompt_specs(
            project=project,
            script_bundle=script_bundle,
            style=style,
            reel=reel,
        )
        if not prompt_specs:
            return SyntheticBrollPlanResult(
                assets=(),
                engine="comfyui-cutaway-skip",
                warnings=("No cutaway prompts were generated for this preview.",),
            )
        if not self.comfyui.can_generate():
            return SyntheticBrollPlanResult(
                assets=(),
                engine="comfyui-cutaway-unavailable",
                warnings=("ComfyUI cutaway generation is not available on this runtime.",),
            )

        assets: list[SyntheticCutawayAsset] = []
        warnings: list[str] = []
        steps_override, cfg_scale_override = self._resolve_quality_overrides(quality_profile)
        for index, spec in enumerate(prompt_specs, start=1):
            output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
            try:
                self.comfyui.generate_background(
                    prompt=spec.prompt,
                    output_path=output_path,
                    width=self.CUTAWAY_SIZE[0],
                    height=self.CUTAWAY_SIZE[1],
                    seed=self._seed_for(spec.prompt, salt=f"{project.id}:{style.key}:{reel.key}:{index}"),
                    negative_prompt=spec.negative_prompt,
                    steps_override=steps_override,
                    cfg_scale_override=cfg_scale_override,
                )
                descriptor = self.artifacts.finalize_output(
                    absolute_path=output_path,
                    filename=f"cutaway-{index:02d}.png",
                    media_type="image/png",
                )
                assets.append(
                    SyntheticCutawayAsset(
                        descriptor=descriptor,
                        label=spec.label,
                        source_line=spec.source_line,
                        prompt=spec.prompt,
                    )
                )
            except Exception as exc:
                warnings.append(f"{spec.label}: {exc}")
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
        engine = "comfyui-cutaway" if assets else "comfyui-cutaway-failed"
        return SyntheticBrollPlanResult(
            assets=tuple(assets),
            engine=engine,
            warnings=tuple(warnings),
        )

    def _build_prompt_specs(
        self,
        *,
        project: CreatorProjectView,
        script_bundle: SyntheticScriptBundle,
        style: SyntheticStylePreset,
        reel: SyntheticReelPreset,
    ) -> list[_CutawayPromptSpec]:
        story_lines = [script_bundle.hook, *script_bundle.body, script_bundle.outro]
        scene_hints = self.STYLE_SCENE_HINTS.get(style.key, self.STYLE_SCENE_HINTS["editorial_premium"])
        labels = ("Hook cutaway", "Body detail", "Payoff texture")
        niche = (project.niche or "synthetic creator").strip()
        prompt_specs: list[_CutawayPromptSpec] = []

        for index, source_line in enumerate(story_lines[: self.MAX_CUTAWAYS]):
            label = labels[min(index, len(labels) - 1)]
            scene_hint = scene_hints[min(index, len(scene_hints) - 1)]
            prompt_specs.append(
                _CutawayPromptSpec(
                    label=label,
                    source_line=source_line,
                    prompt=(
                        "cinematic vertical 9:16 B-roll still, premium safe-for-work creator marketing visual, "
                        f"{style.visual_direction}, {scene_hint}, reel pacing inspired by {reel.label}, "
                        f"supporting this spoken beat: {source_line}. "
                        f"Audience niche: {niche}. "
                        "No close-up face, no centered portrait, focus on hands, props, environment, textures, reflective details, "
                        "clean composition, tactile lighting, motion-friendly framing, premium color separation."
                    ),
                    negative_prompt=self.DEFAULT_NEGATIVE_PROMPT,
                )
            )
        return prompt_specs

    @staticmethod
    def _seed_for(prompt: str, *, salt: str) -> int:
        digest = hashlib.sha256(f"{salt}:{prompt}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    @staticmethod
    def _resolve_quality_overrides(quality_profile: str) -> tuple[int, float]:
        normalized = str(quality_profile or "standard").strip().lower()
        if normalized == "premium":
            return (28, 6.8)
        if normalized == "fast-turnaround":
            return (18, 5.6)
        return (22, 6.2)
