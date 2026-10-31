from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import textwrap
from typing import Any, Protocol

from atnos.core.config import Settings
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
)
from content.render_ffmpeg import ShortRenderService
from content.render_qa import RenderQaService
from content.motion_render_ffmpeg import MotionShortRenderService


class ImageGenerator(Protocol):
    def generate_image(
        self,
        *,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
        steps: int,
        seed: int | None = None,
        negative_prompt: str | None = None,
    ) -> object:
        ...


class MotionRenderer(Protocol):
    def render_motion(
        self,
        *,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
        output_dir: Path,
    ) -> tuple[Path, dict[str, object]]:
        ...


@dataclass(frozen=True, slots=True)
class VisualPackSceneAsset:
    scene_id: str
    timing: str
    overlay_text: str
    prompt_path: str
    image_path: str | None
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VisualRecreationAssetPackResult:
    status: str
    output_dir: str
    source_plan_path: str
    source_short_dir: str | None
    tool_sequence_path: str
    comfyui_prompts_path: str
    runway_prompts_path: str
    capcut_edit_plan_path: str
    scene_assets: tuple[VisualPackSceneAsset, ...]
    final_mp4: str | None
    render_qa_path: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scene_assets"] = [item.to_dict() for item in self.scene_assets]
        return payload


class VisualRecreationAssetPackService:
    """Turns benchmark visual plans into concrete production assets.

    The pack keeps source material as analysis-only. Generated assets come from
    original prompts derived from format mechanics, not copied frames or audio.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        image_generator: ImageGenerator | None = None,
        renderer: ShortRenderService | None = None,
        motion_renderer: MotionRenderer | None = None,
    ) -> None:
        self.settings = settings
        self.image_generator = image_generator or ComfyUiImageGenerationService(settings)
        self.renderer = renderer or ShortRenderService(settings)
        self.motion_renderer = motion_renderer or MotionShortRenderService(settings)
        self.render_qa = RenderQaService()

    def build(
        self,
        *,
        visual_plan_json: Path,
        output_dir: Path,
        plan_index: int = 0,
        source_short_dir: Path | None = None,
        max_scenes: int = 5,
        generate_images: bool = True,
        render_video: bool = True,
        width: int = 768,
        height: int = 1344,
        steps: int | None = None,
    ) -> VisualRecreationAssetPackResult:
        created_at = datetime.now(UTC).isoformat()
        output_dir.mkdir(parents=True, exist_ok=True)
        plan = self._load_plan(visual_plan_json, plan_index=plan_index)
        selected_scenes = self._selected_scenes(plan, max_scenes=max_scenes)

        source_plan_path = output_dir / "source_visual_recreation_plan.json"
        source_plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        tool_sequence_path = output_dir / "tool_sequence.json"
        comfyui_prompts_path = output_dir / "comfyui_prompts.json"
        runway_prompts_path = output_dir / "runway_prompts.md"
        capcut_edit_plan_path = output_dir / "capcut_edit_plan.md"

        tool_sequence_path.write_text(
            json.dumps(_build_tool_sequence(plan), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        comfyui_prompts_path.write_text(
            json.dumps(_build_comfyui_prompts(plan, selected_scenes), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        runway_prompts_path.write_text(_render_runway_prompts(plan, selected_scenes), encoding="utf-8")
        capcut_edit_plan_path.write_text(_render_capcut_edit_plan(plan, selected_scenes), encoding="utf-8")

        scene_assets = self._generate_scene_assets(
            plan=plan,
            scenes=selected_scenes,
            output_dir=output_dir,
            generate_images=generate_images,
            width=width,
            height=height,
            steps=steps if steps is not None else int(self.settings.comfyui_steps),
        )
        final_mp4: Path | None = None
        render_qa_path: Path | None = None
        if render_video and source_short_dir is not None and any(item.image_path for item in scene_assets):
            final_mp4, render_qa_path = self._render_video(
                output_dir=output_dir,
                source_short_dir=source_short_dir,
                scene_assets=scene_assets,
            )

        status = self._resolve_status(scene_assets=scene_assets, final_mp4=final_mp4, render_video=render_video)
        result = VisualRecreationAssetPackResult(
            status=status,
            output_dir=str(output_dir),
            source_plan_path=str(source_plan_path),
            source_short_dir=str(source_short_dir) if source_short_dir else None,
            tool_sequence_path=str(tool_sequence_path),
            comfyui_prompts_path=str(comfyui_prompts_path),
            runway_prompts_path=str(runway_prompts_path),
            capcut_edit_plan_path=str(capcut_edit_plan_path),
            scene_assets=tuple(scene_assets),
            final_mp4=str(final_mp4) if final_mp4 is not None else None,
            render_qa_path=str(render_qa_path) if render_qa_path is not None else None,
            created_at=created_at,
        )
        (output_dir / "visual_recreation_asset_pack_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "visual_recreation_asset_pack_report.md").write_text(
            _render_result_markdown(result),
            encoding="utf-8",
        )
        return result

    @staticmethod
    def _load_plan(path: Path, *, plan_index: int) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            if not payload:
                raise ValueError("visual_recreation_plans.json contains no plans.")
            index = max(0, min(int(plan_index), len(payload) - 1))
            plan = payload[index]
        elif isinstance(payload, dict):
            plan = payload
        else:
            raise ValueError("Unsupported visual recreation plan JSON shape.")
        if not isinstance(plan, dict):
            raise ValueError("Selected visual recreation plan is not an object.")
        return plan

    @staticmethod
    def _selected_scenes(plan: dict[str, Any], *, max_scenes: int) -> list[dict[str, Any]]:
        raw_scenes = plan.get("scene_prompts")
        if not isinstance(raw_scenes, list):
            return []
        scenes = [item for item in raw_scenes if isinstance(item, dict)]
        return scenes[: max(1, int(max_scenes))]

    def _generate_scene_assets(
        self,
        *,
        plan: dict[str, Any],
        scenes: list[dict[str, Any]],
        output_dir: Path,
        generate_images: bool,
        width: int,
        height: int,
        steps: int,
    ) -> list[VisualPackSceneAsset]:
        scene_dir = output_dir / "scenes"
        scene_dir.mkdir(parents=True, exist_ok=True)
        assets: list[VisualPackSceneAsset] = []
        for index, scene in enumerate(scenes, start=1):
            scene_id = str(scene.get("scene_id") or f"scene_{index:02d}")
            prompt = _compose_scene_prompt(plan, scene)
            negative = _compose_negative_prompt(plan, scene)
            prompt_path = scene_dir / f"{scene_id}_prompt.txt"
            prompt_path.write_text(
                "\n".join(["PROMPT:", prompt, "", "NEGATIVE:", negative]),
                encoding="utf-8",
            )
            image_path = scene_dir / f"{scene_id}.png"
            if not generate_images:
                assets.append(
                    VisualPackSceneAsset(
                        scene_id=scene_id,
                        timing=str(scene.get("timing") or ""),
                        overlay_text=str(scene.get("overlay_text") or ""),
                        prompt_path=str(prompt_path),
                        image_path=None,
                        status="prompt_only",
                    )
                )
                continue
            try:
                self.image_generator.generate_image(
                    prompt=prompt,
                    output_path=image_path,
                    width=width,
                    height=height,
                    steps=steps,
                    seed=10_000 + index,
                    negative_prompt=negative,
                )
                _apply_overlay_text(
                    image_path=image_path,
                    text=str(scene.get("overlay_text") or ""),
                    scene_index=index,
                )
                assets.append(
                    VisualPackSceneAsset(
                        scene_id=scene_id,
                        timing=str(scene.get("timing") or ""),
                        overlay_text=str(scene.get("overlay_text") or ""),
                        prompt_path=str(prompt_path),
                        image_path=str(image_path),
                        status="generated",
                    )
                )
            except ComfyUiImageGenerationError as exc:
                assets.append(
                    VisualPackSceneAsset(
                        scene_id=scene_id,
                        timing=str(scene.get("timing") or ""),
                        overlay_text=str(scene.get("overlay_text") or ""),
                        prompt_path=str(prompt_path),
                        image_path=None,
                        status="generation_failed",
                        error=str(exc),
                    )
                )
                break
        return assets

    def _render_video(
        self,
        *,
        output_dir: Path,
        source_short_dir: Path,
        scene_assets: list[VisualPackSceneAsset],
    ) -> tuple[Path, Path]:
        audio_path = source_short_dir / "narration.wav"
        subtitles_path = source_short_dir / "subtitles.srt"
        frame_paths = [Path(item.image_path) for item in scene_assets if item.image_path]
        try:
            final_mp4, render_metadata = self.motion_renderer.render_motion(
                frame_paths=frame_paths,
                audio_path=audio_path,
                subtitles_path=subtitles_path,
                output_dir=output_dir,
            )
        except Exception:
            final_mp4, render_metadata = self.renderer.render(
                frame_paths=frame_paths,
                audio_path=audio_path,
                subtitles_path=subtitles_path,
                output_dir=output_dir,
            )
        qa = self.render_qa.build(
            final_mp4=final_mp4,
            render_metadata=render_metadata,
            subtitles_path=subtitles_path,
            audio_path=audio_path,
        )
        render_qa_path = output_dir / "render_qa.json"
        render_qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        return final_mp4, render_qa_path

    @staticmethod
    def _resolve_status(
        *,
        scene_assets: list[VisualPackSceneAsset],
        final_mp4: Path | None,
        render_video: bool,
    ) -> str:
        if not scene_assets:
            return "no_scenes"
        generated = [item for item in scene_assets if item.status == "generated"]
        failed = [item for item in scene_assets if item.status == "generation_failed"]
        if render_video and final_mp4 is not None:
            return "video_rendered"
        if generated and failed:
            return "partial_images_generated"
        if generated:
            return "images_generated"
        if all(item.status == "prompt_only" for item in scene_assets):
            return "prompt_pack_ready"
        return "failed"


def _build_tool_sequence(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_video_id": plan.get("source_video_id"),
        "source_pattern": plan.get("source_pattern"),
        "safety": {
            "copy_source_assets": False,
            "copy_source_audio": False,
            "copy_source_script": False,
            "use_source_as_format_reference_only": True,
        },
        "steps": [
            "Generate original scene stills with ComfyUI from scene prompts.",
            "Optionally animate the same prompts in Runway using motion prompts.",
            "Assemble scenes, narration, subtitles and cuts in CapCut.",
            "Run format review against the public-player observation.",
            "Keep upload private/manual until owner approval.",
        ],
    }


def _build_comfyui_prompts(plan: dict[str, Any], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "scene_id": str(scene.get("scene_id") or f"scene_{index:02d}"),
            "prompt": _compose_scene_prompt(plan, scene),
            "negative_prompt": _compose_negative_prompt(plan, scene),
            "width": 768,
            "height": 1344,
            "recommended_steps": 12,
        }
        for index, scene in enumerate(scenes, start=1)
    ]


def _render_runway_prompts(plan: dict[str, Any], scenes: list[dict[str, Any]]) -> str:
    lines = [
        "# Runway Prompt Pack",
        "",
        "Use these prompts as original scene prompts. Do not upload or reuse source frames.",
        "",
    ]
    for scene in scenes:
        scene_id = str(scene.get("scene_id") or "scene")
        lines.extend(
            [
                f"## {scene_id} / {scene.get('timing', '')}",
                "",
                f"Image prompt: {_compose_scene_prompt(plan, scene)}",
                "",
                f"Motion prompt: {scene.get('motion_prompt', '')}",
                "",
                f"Camera: {scene.get('camera', '')}",
                "",
                f"Negative: {_compose_negative_prompt(plan, scene)}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_capcut_edit_plan(plan: dict[str, Any], scenes: list[dict[str, Any]]) -> str:
    lines = [
        "# CapCut Edit Plan",
        "",
        f"- source_pattern: `{plan.get('source_pattern', 'unknown')}`",
        "- format target: fast faceless Shorts microstory",
        "- captions: max 4 words per cue, large white text, dark outline, bottom safe zone",
        "- publish: private/manual only",
        "",
        "| scene | timing | overlay | edit note |",
        "|---|---|---|---|",
    ]
    for scene in scenes:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(scene.get("scene_id") or ""),
                    str(scene.get("timing") or ""),
                    str(scene.get("overlay_text") or "").replace("|", " "),
                    str(scene.get("motion_prompt") or "").replace("|", " "),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _compose_scene_prompt(plan: dict[str, Any], scene: dict[str, Any]) -> str:
    parts = [
        str(plan.get("character_sheet_prompt") or ""),
        str(scene.get("image_prompt") or ""),
        str(scene.get("camera") or ""),
        "vertical 9:16, cinematic, high readability, strong focal subject, no copied source frame",
    ]
    return ", ".join(part.strip(" ,") for part in parts if part and part.strip())


def _compose_negative_prompt(plan: dict[str, Any], scene: dict[str, Any]) -> str:
    forbidden = ", ".join(str(item) for item in plan.get("forbidden_copying") or [] if str(item).strip())
    parts = [
        str(scene.get("negative_prompt") or ""),
        str(forbidden),
        "watermark, logo, copied character, copied composition, unreadable text, distorted face, deformed limbs",
    ]
    return ", ".join(part.strip(" ,") for part in parts if part and part.strip())


def _render_result_markdown(result: VisualRecreationAssetPackResult) -> str:
    lines = [
        "# Visual Recreation Asset Pack",
        "",
        f"- status: `{result.status}`",
        f"- output_dir: `{result.output_dir}`",
        f"- final_mp4: `{result.final_mp4 or 'not_rendered'}`",
        f"- render_qa: `{result.render_qa_path or 'not_run'}`",
        "",
        "## Scene Assets",
        "",
        "| scene | status | image | prompt |",
        "|---|---|---|---|",
    ]
    for item in result.scene_assets:
        lines.append(
            f"| {item.scene_id} | {item.status} | {item.image_path or ''} | {item.prompt_path} |"
        )
    lines.extend(
        [
            "",
            "## Tool Plans",
            "",
            f"- ComfyUI prompts: `{result.comfyui_prompts_path}`",
            f"- Runway prompts: `{result.runway_prompts_path}`",
            f"- CapCut edit plan: `{result.capcut_edit_plan_path}`",
            "",
            "## Safety",
            "",
            "- Source Shorts are used as format references only.",
            "- Do not copy source footage, characters, voices, scripts, watermarks or channel branding.",
            "- Any upload remains private/manual and approval-gated.",
            "",
        ]
    )
    return "\n".join(lines)


def _apply_overlay_text(*, image_path: Path, text: str, scene_index: int) -> None:
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return

    with Image.open(image_path) as source:
        image = source.convert("RGBA")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _overlay_font(max(42, int(width * 0.075)))
    lines = textwrap.wrap(text.upper(), width=14)[:3]
    if not lines:
        return
    line_height = int(font.size * 1.12) if hasattr(font, "size") else 72
    box_height = 82 + line_height * len(lines)
    x1, y1, x2, y2 = int(width * 0.055), int(height * 0.055), int(width * 0.945), int(height * 0.055) + box_height
    accent = _overlay_accent(scene_index)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=28, fill=(2, 5, 10, 178), outline=accent, width=4)
    y = y1 + 22
    for line in lines:
        draw.text(
            (x1 + 28, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=4,
            stroke_fill=(0, 0, 0, 230),
        )
        y += line_height
    image = Image.alpha_composite(image, overlay).convert("RGB")
    image.save(image_path)


def _overlay_font(size: int):
    try:
        from PIL import ImageFont
    except Exception:
        return None
    for candidate in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _overlay_accent(index: int) -> tuple[int, int, int, int]:
    return (194, 229, 255, 255)
