from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import textwrap
from typing import Any
import unicodedata
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from atnos.core.config import Settings
from atnos.services.asset_dedupe_service import AssetDedupeService
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationError, ComfyUiImageGenerationService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService


@dataclass(frozen=True, slots=True)
class GeneratedStoryAssets:
    frames: tuple[ManagedArtifactDescriptor, ...]
    thumbnail: ManagedArtifactDescriptor


@dataclass(frozen=True, slots=True)
class SceneCard:
    headline: str
    caption: str
    fruit: str
    action: str
    palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]


class AssetGenerationService:
    FRAME_SIZE = (1080, 1920)
    THUMBNAIL_SIZE = (1280, 720)
    PROMPT_VERSION = "cinematic-fruit-v3"
    FRUIT_KEYWORDS = {
        "jablko": "apple",
        "apple": "apple",
        "arbuz": "watermelon",
        "watermelon": "watermelon",
        "pomarancza": "orange",
        "orange": "orange",
        "truskawka": "strawberry",
        "strawberry": "strawberry",
        "banan": "banana",
        "banana": "banana",
        "mango": "mango",
        "kiwi": "kiwi",
        "cytryna": "lemon",
        "lemon": "lemon",
    }
    PALETTES = {
        "apple": ((20, 8, 10), (217, 42, 52), (255, 210, 116)),
        "watermelon": ((10, 18, 12), (239, 84, 116), (91, 203, 108)),
        "orange": ((28, 14, 6), (255, 130, 38), (255, 220, 108)),
        "strawberry": ((24, 8, 12), (229, 54, 95), (255, 232, 192)),
        "banana": ((22, 16, 7), (250, 221, 88), (255, 171, 52)),
        "mango": ((26, 14, 8), (255, 176, 68), (245, 85, 58)),
        "kiwi": ((10, 22, 14), (126, 198, 78), (252, 241, 188)),
        "lemon": ((20, 20, 6), (255, 230, 102), (255, 250, 215)),
        "generic": ((8, 12, 20), (88, 160, 255), (255, 122, 45)),
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)
        self.dedupe = AssetDedupeService(settings)
        self.comfyui = ComfyUiImageGenerationService(settings)
        self.font_regular_path = self._find_font("Vera.ttf")
        self.font_bold_path = self._find_font("VeraBd.ttf")

    def generate_story_assets(
        self,
        *,
        title: str,
        topic: str,
        scenes: list[Any],
        conversation_id: int | None,
        quality_profile: str = "standard",
    ) -> GeneratedStoryAssets:
        frame_quality_profile = self._frame_quality_profile_for_execution(quality_profile)
        thumbnail_quality_profile = self._thumbnail_quality_profile_for_execution(quality_profile)
        normalized_scenes = [self._normalize_scene(scene, index) for index, scene in enumerate(scenes or [], start=1)]
        if not normalized_scenes:
            normalized_scenes = [
                self._normalize_scene(
                    {
                        "headline": title,
                        "caption": topic,
                        "fruit": "apple",
                        "action": "impact",
                    },
                    1,
                )
            ]

        frames: list[ManagedArtifactDescriptor] = []
        for index, scene in enumerate(normalized_scenes, start=1):
            prompt = f"frame:{self.PROMPT_VERSION}:{title}:{topic}:{index}:{scene.headline}:{scene.caption}:{scene.fruit}:{scene.action}"
            cached = self.dedupe.resolve(prompt=prompt, seed=index)
            if cached is not None:
                frames.append(cached)
                continue
            output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
            self._render_frame(
                output_path=output_path,
                scene=scene,
                title=title,
                topic=topic,
                scene_index=index,
                quality_profile=frame_quality_profile,
            )
            descriptor = self.artifacts.finalize_output(
                absolute_path=output_path,
                filename=f"atnos-frame-{index}.png",
                media_type="image/png",
            )
            self.dedupe.remember(prompt=prompt, descriptor=descriptor, seed=index)
            frames.append(descriptor)

        thumbnail_prompt = f"thumbnail:{self.PROMPT_VERSION}:{title}:{topic}:{normalized_scenes[0].fruit}:{normalized_scenes[0].headline}"
        cached_thumbnail = self.dedupe.resolve(prompt=thumbnail_prompt, seed=0)
        if cached_thumbnail is None:
            thumbnail_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
            self._render_thumbnail(
                output_path=thumbnail_path,
                title=title,
                topic=topic,
                scene=normalized_scenes[0],
                quality_profile=thumbnail_quality_profile,
            )
            thumbnail = self.artifacts.finalize_output(
                absolute_path=thumbnail_path,
                filename="atnos-thumbnail.png",
                media_type="image/png",
            )
            self.dedupe.remember(prompt=thumbnail_prompt, descriptor=thumbnail, seed=0)
        else:
            thumbnail = cached_thumbnail

        return GeneratedStoryAssets(frames=tuple(frames), thumbnail=thumbnail)

    def _render_frame(
        self,
        *,
        output_path: Path,
        scene: SceneCard,
        title: str,
        topic: str,
        scene_index: int,
        quality_profile: str,
    ) -> None:
        image = self._create_frame_background(
            scene=scene,
            title=title,
            topic=topic,
            scene_index=scene_index,
            quality_profile=quality_profile,
        )
        draw = ImageDraw.Draw(image, "RGBA")

        self._draw_energy_rings(draw=draw, size=self.FRAME_SIZE, palette=scene.palette, scene_index=scene_index)
        self._draw_glow_blobs(draw=draw, size=self.FRAME_SIZE, palette=scene.palette)
        self._draw_fruit_scene(draw=draw, size=self.FRAME_SIZE, scene=scene, compact=False)

        topic_font = self._load_font(size=38, bold=False)
        headline_font = self._load_font(size=108, bold=True)
        caption_font = self._load_font(size=32, bold=False)
        chip_font = self._load_font(size=32, bold=True)
        footer_font = self._load_font(size=28, bold=False)

        topic_display = self._display_text(topic or title).upper()
        headline_display = self._display_text(scene.headline).upper()
        caption_display = self._display_text(scene.caption)

        self._draw_text_chip(
            draw=draw,
            xy=(72, 72),
            text=f"ATNOS / SCENA {scene_index}",
            font=chip_font,
            fill=(255, 255, 255, 220),
            background=(10, 14, 24, 196),
        )
        self._draw_wrapped_text(
            draw=draw,
            xy=(72, 164),
            text=topic_display[:34],
            font=topic_font,
            fill=(214, 232, 255, 215),
            width_chars=20,
            line_height=46,
            max_lines=2,
        )
        self._draw_wrapped_text(
            draw=draw,
            xy=(72, 282),
            text=headline_display[:28],
            font=headline_font,
            fill=(255, 255, 255, 248),
            width_chars=11,
            line_height=108,
            max_lines=3,
        )
        self._draw_wrapped_text(
            draw=draw,
            xy=(72, 1505),
            text=caption_display[:78],
            font=caption_font,
            fill=(244, 246, 250, 230),
            width_chars=29,
            line_height=40,
            max_lines=2,
        )

        self._draw_text_chip(
            draw=draw,
            xy=(72, 1804),
            text=f"{self._fruit_label(scene.fruit)} / {self._action_label(scene.action)}",
            font=footer_font,
            fill=(255, 242, 226, 220),
            background=(20, 16, 18, 170),
        )
        image = image.filter(ImageFilter.GaussianBlur(radius=0.35))
        image.save(output_path)

    def _render_thumbnail(
        self,
        *,
        output_path: Path,
        title: str,
        topic: str,
        scene: SceneCard,
        quality_profile: str,
    ) -> None:
        image = self._create_thumbnail_background(
            scene=scene,
            title=title,
            topic=topic,
            quality_profile=quality_profile,
        )
        draw = ImageDraw.Draw(image, "RGBA")
        self._draw_energy_rings(draw=draw, size=self.THUMBNAIL_SIZE, palette=scene.palette, scene_index=1)
        self._draw_glow_blobs(draw=draw, size=self.THUMBNAIL_SIZE, palette=scene.palette)
        self._draw_fruit_scene(draw=draw, size=self.THUMBNAIL_SIZE, scene=scene, compact=True)

        chip_font = self._load_font(size=28, bold=True)
        title_font = self._load_font(size=86, bold=True)
        body_font = self._load_font(size=28, bold=False)
        headline_display = self._display_text(scene.headline or title).upper()
        topic_display = self._display_text(topic)
        caption_display = self._display_text(scene.caption)

        self._draw_text_chip(
            draw=draw,
            xy=(52, 44),
            text="ATNOS / SENSORY SHORT",
            font=chip_font,
            fill=(255, 255, 255, 220),
            background=(12, 18, 28, 190),
        )
        self._draw_wrapped_text(
            draw=draw,
            xy=(52, 126),
            text=headline_display[:28],
            font=title_font,
            fill=(255, 255, 255, 245),
            width_chars=15,
            line_height=86,
            max_lines=3,
        )
        self._draw_wrapped_text(
            draw=draw,
            xy=(52, 420),
            text=topic_display[:64],
            font=body_font,
            fill=(240, 243, 248, 225),
            width_chars=29,
            line_height=38,
            max_lines=2,
        )
        self._draw_wrapped_text(
            draw=draw,
            xy=(52, 522),
            text=caption_display[:64],
            font=body_font,
            fill=(255, 228, 194, 205),
            width_chars=27,
            line_height=36,
            max_lines=2,
        )
        image.save(output_path)

    def _create_frame_background(
        self,
        *,
        scene: SceneCard,
        title: str,
        topic: str,
        scene_index: int,
        quality_profile: str,
    ) -> Image.Image:
        prompt = self._build_comfyui_prompt(
            title=title,
            topic=topic,
            scene=scene,
            mode="vertical short frame",
            extra=(
                "macro food cinematography, one dominant object, premium texture detail, "
                "dramatic splash, clean dark background, high contrast, no typography"
            ),
        )
        return self._create_base_canvas(
            size=self.FRAME_SIZE,
            palette=scene.palette,
            prompt=prompt,
            seed=scene_index,
            quality_profile=quality_profile,
        )

    def _create_thumbnail_background(
        self,
        *,
        scene: SceneCard,
        title: str,
        topic: str,
        quality_profile: str,
    ) -> Image.Image:
        prompt = self._build_comfyui_prompt(
            title=title,
            topic=topic,
            scene=scene,
            mode="youtube thumbnail background",
            extra=(
                "clean composition, center-weighted subject, premium short-form social cover art, "
                "high color separation, no text, no watermark"
            ),
        )
        return self._create_base_canvas(
            size=self.THUMBNAIL_SIZE,
            palette=scene.palette,
            prompt=prompt,
            seed=0,
            quality_profile=quality_profile,
        )

    def _create_base_canvas(
        self,
        *,
        size: tuple[int, int],
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
        prompt: str,
        seed: int,
        quality_profile: str = "balanced",
    ) -> Image.Image:
        comfy = self._try_comfyui_background(
            size=size,
            prompt=prompt,
            seed=seed,
            palette=palette,
            quality_profile=quality_profile,
        )
        if comfy is not None:
            return comfy
        return self._create_gradient_canvas(size=size, palette=palette)

    def _try_comfyui_background(
        self,
        *,
        size: tuple[int, int],
        prompt: str,
        seed: int,
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
        quality_profile: str,
    ) -> Image.Image | None:
        if not self.comfyui.can_generate():
            return None
        temp_path = (self.settings.workspace_staging_dir / "comfyui-renders" / f"{uuid4().hex}.png").resolve()
        steps_override, cfg_scale_override = self._resolve_background_quality_overrides(quality_profile)
        try:
            self.comfyui.generate_background(
                prompt=prompt,
                output_path=temp_path,
                width=size[0],
                height=size[1],
                seed=seed,
                steps_override=steps_override,
                cfg_scale_override=cfg_scale_override,
            )
        except ComfyUiImageGenerationError:
            return None
        except Exception:
            return None

        try:
            image = Image.open(temp_path).convert("RGB")
        except Exception:
            return None
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

        fitted = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", size, color=(*palette[0], 108))
        fitted_rgba = fitted.convert("RGBA")
        composed = Image.alpha_composite(fitted_rgba, overlay)
        return composed.convert("RGB")

    @staticmethod
    def _build_comfyui_prompt(
        *,
        title: str,
        topic: str,
        scene: SceneCard,
        mode: str,
        extra: str,
    ) -> str:
        headline = AssetGenerationService._display_text(scene.headline)
        caption = AssetGenerationService._display_text(scene.caption)
        fruit = AssetGenerationService._fruit_label(scene.fruit)
        action = AssetGenerationService._action_label(scene.action)
        topic_display = AssetGenerationService._display_text(topic or title)
        return (
            f"{mode}, {fruit}, {action}, {topic_display}, {headline}, {caption}, "
            f"{extra}, single hero subject, premium commercial lighting, cinematic depth, polished composition, "
            "clean background separation, realistic materials, no text, no watermark, no logo"
        )

    @staticmethod
    def _resolve_background_quality_overrides(quality_profile: str) -> tuple[int, float]:
        profile = str(quality_profile or "balanced").strip().lower()
        if profile == "thumbnail_premium":
            return (30, 7.2)
        if profile == "thumbnail":
            return (24, 6.8)
        if profile == "thumbnail_fast":
            return (20, 6.0)
        if profile == "story_frame_premium":
            return (28, 6.9)
        if profile == "story_frame":
            return (22, 6.4)
        if profile == "story_frame_fast":
            return (18, 5.8)
        return (20, 6.0)

    @staticmethod
    def _frame_quality_profile_for_execution(quality_profile: str) -> str:
        normalized = str(quality_profile or "standard").strip().lower()
        if normalized == "premium":
            return "story_frame_premium"
        if normalized == "fast-turnaround":
            return "story_frame_fast"
        return "story_frame"

    @staticmethod
    def _thumbnail_quality_profile_for_execution(quality_profile: str) -> str:
        normalized = str(quality_profile or "standard").strip().lower()
        if normalized == "premium":
            return "thumbnail_premium"
        if normalized == "fast-turnaround":
            return "thumbnail_fast"
        return "thumbnail"

    def _normalize_scene(self, scene: Any, index: int) -> SceneCard:
        if isinstance(scene, dict):
            headline = self._clean_text(str(scene.get("headline") or scene.get("caption") or f"Ujecie {index}"))
            caption = self._clean_text(str(scene.get("caption") or scene.get("visual") or scene.get("action") or headline))
            fruit = self._detect_fruit(
                " ".join(str(scene.get(key) or "") for key in ("fruit", "caption", "visual", "action", "headline"))
            )
            action = self._detect_action(
                " ".join(str(scene.get(key) or "") for key in ("action", "caption", "visual", "headline"))
            )
        else:
            raw = self._clean_text(str(scene))
            headline = self._make_headline(raw, index=index)
            caption = raw
            fruit = self._detect_fruit(raw)
            action = self._detect_action(raw)
        return SceneCard(
            headline=headline,
            caption=caption,
            fruit=fruit,
            action=action,
            palette=self.PALETTES.get(fruit, self.PALETTES["generic"]),
        )

    def _create_gradient_canvas(
        self,
        *,
        size: tuple[int, int],
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
    ) -> Image.Image:
        width, height = size
        image = Image.new("RGB", size, color=palette[0])
        px = image.load()
        top, mid, accent = palette
        for y in range(height):
            t = y / max(1, height - 1)
            blend = self._mix(top, mid, min(1.0, t * 1.4))
            glow = self._mix(blend, accent, max(0.0, 1.0 - abs(t - 0.62) * 1.8) * 0.26)
            for x in range(width):
                vignette = 1.0 - (((x - width / 2) / width) ** 2 + ((y - height / 2) / height) ** 2) * 1.28
                vignette = max(0.22, min(1.0, vignette))
                px[x, y] = tuple(int(channel * vignette) for channel in glow)
        return image

    def _draw_energy_rings(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        size: tuple[int, int],
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
        scene_index: int,
    ) -> None:
        width, height = size
        accent = palette[2]
        primary = palette[1]
        for index in range(7):
            inset_x = 70 + index * 36
            inset_y = 118 + index * 44
            if width - inset_x <= inset_x or height - inset_y <= inset_y:
                break
            draw.ellipse(
                (inset_x, inset_y, width - inset_x, height - inset_y),
                outline=(*accent, max(18, 126 - index * 14)),
                width=max(2, 8 - index),
            )
        for index in range(18):
            offset = (scene_index * 37 + index * 57) % width
            draw.line(
                ((offset, 0), (max(0, offset - 300), height)),
                fill=(*primary, 18),
                width=3,
            )

    def _draw_glow_blobs(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        size: tuple[int, int],
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
    ) -> None:
        width, height = size
        primary, accent = palette[1], palette[2]
        positions = (
            (int(width * 0.22), int(height * 0.29), 168, (*accent, 76)),
            (int(width * 0.78), int(height * 0.26), 132, (*primary, 78)),
            (int(width * 0.76), int(height * 0.88), 226, (*accent, 64)),
        )
        for cx, cy, radius, fill in positions:
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)

    def _draw_fruit_scene(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        size: tuple[int, int],
        scene: SceneCard,
        compact: bool,
    ) -> None:
        width, height = size
        cx = int(width * (0.64 if compact else 0.63))
        cy = int(height * (0.56 if compact else 0.60))
        radius = int(min(width, height) * (0.13 if compact else 0.20))
        self._draw_smash_burst(draw=draw, center=(cx, cy), radius=radius, palette=scene.palette)
        self._draw_splashes(draw=draw, center=(cx, cy), radius=radius, palette=scene.palette, action=scene.action)
        self._draw_fruit(draw=draw, center=(cx, cy), radius=radius, fruit=scene.fruit, palette=scene.palette)
        if not compact:
            self._draw_cut_line(draw=draw, center=(cx, cy), radius=radius, action=scene.action)

    @staticmethod
    def _draw_cut_line(
        *,
        draw: ImageDraw.ImageDraw,
        center: tuple[int, int],
        radius: int,
        action: str,
    ) -> None:
        if action not in {"slice", "impact"}:
            return
        cx, cy = center
        offset = int(radius * 0.82)
        draw.line(
            ((cx - offset, cy + offset), (cx + offset, cy - offset)),
            fill=(255, 255, 255, 138),
            width=10,
        )

    def _draw_fruit(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        center: tuple[int, int],
        radius: int,
        fruit: str,
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
    ) -> None:
        cx, cy = center
        primary = palette[1]
        accent = palette[2]
        outline = (*accent, 255)
        if fruit == "watermelon":
            draw.pieslice((cx - radius, cy - radius, cx + radius, cy + radius), start=185, end=355, fill=(242, 84, 107, 255), outline=outline, width=8)
            draw.arc((cx - radius, cy - radius, cx + radius, cy + radius), start=185, end=355, fill=(88, 203, 104, 255), width=20)
            for seed_index in range(7):
                seed_x = cx - radius // 2 + seed_index * radius // 6
                seed_y = cy - radius // 6 + (seed_index % 2) * 34
                draw.ellipse((seed_x - 8, seed_y - 14, seed_x + 8, seed_y + 14), fill=(34, 18, 14, 240))
            return
        if fruit in {"orange", "lemon"}:
            fill = (255, 187, 68, 255) if fruit == "orange" else (255, 231, 108, 255)
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill, outline=outline, width=8)
            for seg_index in range(6):
                angle = seg_index / 6 * math.tau
                ex = cx + int(math.cos(angle) * radius * 0.72)
                ey = cy + int(math.sin(angle) * radius * 0.72)
                draw.line((cx, cy, ex, ey), fill=(255, 248, 220, 180), width=6)
            draw.ellipse((cx - radius * 0.2, cy - radius * 0.2, cx + radius * 0.2, cy + radius * 0.2), fill=(255, 244, 220, 215))
            return
        if fruit == "strawberry":
            points = [
                (cx, cy - radius),
                (cx + int(radius * 0.82), cy - int(radius * 0.18)),
                (cx + int(radius * 0.68), cy + int(radius * 0.72)),
                (cx, cy + radius),
                (cx - int(radius * 0.68), cy + int(radius * 0.72)),
                (cx - int(radius * 0.82), cy - int(radius * 0.18)),
            ]
            draw.polygon(points, fill=(227, 44, 72, 255), outline=outline)
            for row in range(-2, 3):
                for col in range(-2, 3):
                    sx = cx + col * 42 + (row % 2) * 18
                    sy = cy + row * 52
                    draw.ellipse((sx - 5, sy - 3, sx + 5, sy + 3), fill=(255, 232, 160, 230))
            draw.polygon(
                [
                    (cx, cy - radius - 28),
                    (cx + 40, cy - radius + 12),
                    (cx + 6, cy - radius + 26),
                    (cx - 32, cy - radius + 10),
                ],
                fill=(100, 188, 70, 255),
            )
            return
        if fruit == "banana":
            draw.arc((cx - radius, cy - radius * 0.6, cx + radius, cy + radius), start=218, end=344, fill=(244, 214, 76, 255), width=38)
            draw.arc((cx - radius * 0.76, cy - radius * 0.44, cx + radius * 0.76, cy + radius * 0.76), start=220, end=342, fill=(255, 244, 198, 255), width=18)
            return
        if fruit == "mango":
            draw.ellipse((cx - int(radius * 0.82), cy - radius, cx + int(radius * 0.74), cy + radius), fill=(255, 150, 64, 255), outline=outline, width=8)
            draw.ellipse((cx - int(radius * 0.2), cy - int(radius * 0.82), cx + radius, cy + int(radius * 0.32)), fill=(244, 86, 66, 165))
            return
        if fruit == "kiwi":
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(116, 182, 68, 255), outline=outline, width=8)
            draw.ellipse((cx - radius * 0.72, cy - radius * 0.72, cx + radius * 0.72, cy + radius * 0.72), fill=(232, 250, 202, 255))
            draw.ellipse((cx - radius * 0.18, cy - radius * 0.18, cx + radius * 0.18, cy + radius * 0.18), fill=(255, 248, 224, 255))
            for seed_index in range(12):
                angle = seed_index / 12 * math.tau
                sx = cx + math.cos(angle) * radius * 0.42
                sy = cy + math.sin(angle) * radius * 0.42
                draw.ellipse((sx - 6, sy - 3, sx + 6, sy + 3), fill=(52, 44, 28, 240))
            return

        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(*primary, 255), outline=outline, width=8)
        highlight = radius // 2
        draw.ellipse((cx - highlight, cy - highlight, cx + highlight, cy + highlight), outline=(255, 255, 255, 120), width=8)
        if fruit == "apple":
            draw.polygon(
                [
                    (cx + 18, cy - radius - 8),
                    (cx + 88, cy - radius - 54),
                    (cx + 112, cy - radius - 12),
                    (cx + 44, cy - radius + 18),
                ],
                fill=(94, 190, 76, 255),
            )
            draw.line((cx, cy - radius + 8, cx + 24, cy - radius - 28), fill=(106, 60, 28, 255), width=10)

    def _draw_smash_burst(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        center: tuple[int, int],
        radius: int,
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
    ) -> None:
        cx, cy = center
        accent = palette[2]
        for index in range(16):
            angle = index / 16 * math.tau
            inner = radius * 0.84
            outer = radius * 1.58
            x1 = cx + math.cos(angle) * inner
            y1 = cy + math.sin(angle) * inner
            x2 = cx + math.cos(angle) * outer
            y2 = cy + math.sin(angle) * outer
            draw.line((x1, y1, x2, y2), fill=(*accent, 168), width=10 if index % 2 == 0 else 6)

    def _draw_splashes(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        center: tuple[int, int],
        radius: int,
        palette: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
        action: str,
    ) -> None:
        cx, cy = center
        primary = palette[1]
        accent = palette[2]
        multiplier = 1.45 if action in {"crush", "burst"} else 1.15
        for index in range(24):
            angle = index / 24 * math.tau
            distance = radius * (0.92 + (index % 3) * 0.24) * multiplier
            sx = cx + int(math.cos(angle) * distance)
            sy = cy + int(math.sin(angle) * distance * 0.72)
            blob_w = 18 + (index % 4) * 10
            blob_h = 10 + (index % 3) * 8
            fill = (*accent, 182) if index % 2 == 0 else (*primary, 165)
            draw.ellipse((sx - blob_w, sy - blob_h, sx + blob_w, sy + blob_h), fill=fill)

    def _draw_text_chip(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int, int],
        background: tuple[int, int, int, int],
    ) -> None:
        left, top, right, bottom = draw.textbbox(xy, text, font=font)
        box = (left - 18, top - 10, right + 18, bottom + 10)
        draw.rounded_rectangle(box, radius=22, fill=background)
        draw.text(xy, text, font=font, fill=fill)

    def _draw_wrapped_text(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int, int],
        width_chars: int,
        line_height: int,
        max_lines: int,
    ) -> None:
        x, y = xy
        for line in textwrap.wrap(text, width=width_chars)[:max_lines]:
            draw.text((x, y), line, font=font, fill=(10, 12, 18, 168), stroke_width=8, stroke_fill=(10, 12, 18, 168))
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height

    @staticmethod
    def _mix(source: tuple[int, int, int], target: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
        factor = max(0.0, min(1.0, factor))
        return tuple(int(s + (t - s) * factor) for s, t in zip(source, target, strict=True))

    def _load_font(self, *, size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        path = self.font_bold_path if bold else self.font_regular_path
        if path is None:
            return ImageFont.load_default()
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            return ImageFont.load_default()

    @staticmethod
    def _find_font(name: str) -> Path | None:
        reportlab_font = Path(__import__("reportlab").__file__).resolve().parent / "fonts" / name
        if reportlab_font.exists():
            return reportlab_font
        return None

    def _detect_fruit(self, value: str) -> str:
        normalized = self._normalize_for_match(value)
        for keyword, fruit in self.FRUIT_KEYWORDS.items():
            if keyword in normalized:
                return fruit
        return "apple"

    @staticmethod
    def _detect_action(value: str) -> str:
        normalized = AssetGenerationService._normalize_for_match(value)
        if any(keyword in normalized for keyword in ("roztrzask", "smash", "burst", "wybuch")):
            return "burst"
        if any(keyword in normalized for keyword in ("zgniat", "crush", "miazdz", "gniecen")):
            return "crush"
        if any(keyword in normalized for keyword in ("slice", "ciecie", "przecina", "nacin")):
            return "slice"
        return "impact"

    @staticmethod
    def _normalize_for_match(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.lower())
        return normalized.encode("ascii", "ignore").decode("ascii")

    @staticmethod
    def _clean_text(value: str) -> str:
        text = re.sub(r"[#*`_]+", " ", value)
        text = text.replace("â€“", "-").replace("â€”", "-").replace("–", "-").replace("—", "-")
        text = re.sub(r"\s+", " ", text).strip(" -")
        return text

    def _make_headline(self, raw: str, *, index: int) -> str:
        clauses = re.split(r"[.!?–—:-]\s*", raw)
        for clause in clauses:
            cleaned = self._clean_text(clause)
            if len(cleaned) >= 8:
                return cleaned[:44]
        return f"Ujecie {index}"

    @staticmethod
    def _display_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"\s+", " ", ascii_only).strip()
        return cleaned or "ATNOS"

    @staticmethod
    def _fruit_label(fruit: str) -> str:
        labels = {
            "apple": "Jablko",
            "watermelon": "Arbuz",
            "orange": "Pomarancza",
            "strawberry": "Truskawka",
            "banana": "Banan",
            "mango": "Mango",
            "kiwi": "Kiwi",
            "lemon": "Cytryna",
        }
        return labels.get(fruit, "Owoc")

    @staticmethod
    def _action_label(action: str) -> str:
        return {
            "burst": "rozbryzg",
            "crush": "zgniecenie",
            "slice": "ciecie",
            "impact": "uderzenie",
        }.get(action, "akcja")
