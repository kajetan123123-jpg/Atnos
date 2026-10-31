from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from content.script_generator import ShortScript


@dataclass(frozen=True, slots=True)
class VisualStoryboard:
    frames: tuple[Path, ...]
    manifest_path: Path


class VisualStoryboardService:
    SIZE = (1080, 1920)

    def __init__(
        self,
        *,
        brand_color: str = "#C2E5FF",
        brand_color_dark: str = "#0A1628",
        show_watermark: bool = False,
        watermark_text: str = "",
    ) -> None:
        self.brand_color = _hex_to_rgb(brand_color, fallback=(194, 229, 255))
        self.brand_color_dark = _hex_to_rgb(brand_color_dark, fallback=(10, 22, 40))
        self.show_watermark = show_watermark
        self.watermark_text = " ".join(watermark_text.split()).strip()

    def generate(
        self,
        *,
        script: ShortScript,
        topic: str,
        output_dir: Path,
        asset_plan: dict[str, object] | None = None,
    ) -> VisualStoryboard:
        frames_dir = output_dir / "visual_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        scene_texts = self._scene_texts_from_asset_plan(asset_plan) or self._scene_texts(script)
        visual_mode = self._visual_mode(topic=topic, asset_plan=asset_plan, scene_texts=scene_texts)
        frames: list[Path] = []
        for index, text in enumerate(scene_texts, start=1):
            frame = frames_dir / f"scene_{index:02d}.png"
            self._draw_scene(
                frame=frame,
                index=index,
                total=len(scene_texts),
                topic=topic,
                text=text,
                visual_mode=visual_mode,
            )
            frames.append(frame)
        manifest_path = output_dir / "visual_storyboard.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "engine": "pillow-editorial-storyboard",
                    "frame_count": len(frames),
                    "asset_plan_used": asset_plan is not None,
                    "subtitle_safe_zone_reserved": True,
                    "subtitle_safe_zone_height_px": 460,
                    "max_subtitle_words_per_cue": 4,
                    "duplicated_scene_text_layers": False,
                    "full_scene_text_on_frame": False,
                    "visual_mode": visual_mode,
                    "scenes": list(scene_texts),
                    "frames": [str(frame) for frame in frames],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return VisualStoryboard(frames=tuple(frames), manifest_path=manifest_path)

    @staticmethod
    def _scene_texts(script: ShortScript) -> tuple[str, ...]:
        body = tuple(item for item in script.body if item.strip())
        scenes = [script.hook, *body[:4], script.cta]
        return tuple(item.strip() for item in scenes if item.strip())[:6] or (script.title,)

    @staticmethod
    def _scene_texts_from_asset_plan(asset_plan: dict[str, object] | None) -> tuple[str, ...]:
        if not isinstance(asset_plan, dict):
            return ()
        raw_scenes = asset_plan.get("scenes")
        if not isinstance(raw_scenes, list):
            return ()
        scenes: list[str] = []
        for raw_scene in raw_scenes:
            if not isinstance(raw_scene, dict):
                continue
            text = str(raw_scene.get("overlay_text") or raw_scene.get("caption") or "").strip()
            if text:
                scenes.append(text)
        return tuple(scenes[:6])

    @staticmethod
    def _visual_mode(
        *,
        topic: str,
        asset_plan: dict[str, object] | None,
        scene_texts: tuple[str, ...],
    ) -> str:
        combined = " ".join((topic, " ".join(scene_texts), json.dumps(asset_plan or {}, ensure_ascii=False))).casefold()
        if "kot" in combined and ("qa" in combined or "short" in combined or "kanal" in combined or "kana" in combined):
            return "ai_cat_microstory"
        return "editorial_badge_storyboard"

    def _draw_scene(
        self,
        *,
        frame: Path,
        index: int,
        total: int,
        topic: str,
        text: str,
        visual_mode: str,
    ) -> None:
        image = Image.new("RGB", self.SIZE, (7, 12, 20))
        draw = ImageDraw.Draw(image)
        self._draw_background(draw=draw, index=index)
        self._draw_glow(image=image, index=index)

        label_font = _font(34)
        title_font = _font(88)
        small_font = _font(30)

        accent = self.brand_color
        draw.rounded_rectangle((70, 72, 360, 136), radius=24, fill=self.brand_color_dark, outline=accent, width=3)
        draw.text((96, 88), f"SCENA {index}/{total}", font=label_font, fill=(235, 244, 255))

        if self.show_watermark and self.watermark_text:
            draw.text((76, 172), self.watermark_text, font=small_font, fill=(125, 160, 190))

        badge = self._scene_badge(text=text, index=index, total=total)
        y = 520
        for line in textwrap.wrap(badge.upper(), width=12)[:3]:
            draw.text((78, y), line, font=title_font, fill=(248, 250, 252), stroke_width=4, stroke_fill=(4, 8, 16))
            y += 104
        if visual_mode == "ai_cat_microstory":
            self._draw_ai_cat_microstory_scene(draw=draw, index=index, total=total, accent=accent)
        else:
            self._draw_visual_anchor(draw=draw, index=index, accent=accent)

        self._draw_subtitle_safe_zone(draw=draw)
        self._draw_progress(draw=draw, index=index, total=total, accent=accent)
        image.save(frame)

    @staticmethod
    def _draw_ai_cat_microstory_scene(
        *,
        draw: ImageDraw.ImageDraw,
        index: int,
        total: int,
        accent: tuple[int, int, int],
    ) -> None:
        if index == 1:
            VisualStoryboardService._draw_server_racks(draw=draw, x=610, y=280, accent=accent)
            VisualStoryboardService._draw_monitor(draw=draw, x=530, y=710, label="QA OFF", accent=accent)
            VisualStoryboardService._draw_cat(draw=draw, x=565, y=950, scale=1.05, accent=accent)
            return
        if index == 2:
            VisualStoryboardService._draw_cat(draw=draw, x=180, y=930, scale=0.9, accent=accent)
            VisualStoryboardService._draw_file_stack(draw=draw, x=520, y=650, labels=("HOOK?", "TXT TXT", "NO QA"), accent=accent)
            return
        if index == 3:
            VisualStoryboardService._draw_split_panel(draw=draw, accent=accent)
            return
        if index == 4:
            VisualStoryboardService._draw_qa_button(draw=draw, accent=accent)
            VisualStoryboardService._draw_cat(draw=draw, x=150, y=990, scale=0.72, accent=accent)
            return
        if index == total:
            VisualStoryboardService._draw_storyboard_cards(draw=draw, accent=accent, checked=True)
            VisualStoryboardService._draw_cat(draw=draw, x=720, y=1010, scale=0.7, accent=accent)
            return
        VisualStoryboardService._draw_storyboard_cards(draw=draw, accent=accent, checked=False)

    @staticmethod
    def _draw_cat(
        *,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        scale: float,
        accent: tuple[int, int, int],
    ) -> None:
        def s(value: int) -> int:
            return int(value * scale)

        fur = (32, 36, 48)
        fur_light = (54, 61, 78)
        eye = (166, 245, 216)
        outline = (5, 8, 14)
        draw.ellipse((x + s(80), y + s(150), x + s(390), y + s(520)), fill=fur, outline=outline, width=s(8))
        draw.ellipse((x + s(35), y + s(10), x + s(330), y + s(290)), fill=fur_light, outline=outline, width=s(8))
        draw.polygon(
            [(x + s(80), y + s(60)), (x + s(115), y - s(95)), (x + s(190), y + s(45))],
            fill=fur_light,
            outline=outline,
        )
        draw.polygon(
            [(x + s(235), y + s(45)), (x + s(310), y - s(92)), (x + s(320), y + s(82))],
            fill=fur_light,
            outline=outline,
        )
        draw.ellipse((x + s(100), y + s(112), x + s(150), y + s(170)), fill=eye)
        draw.ellipse((x + s(218), y + s(112), x + s(268), y + s(170)), fill=eye)
        draw.ellipse((x + s(120), y + s(128), x + s(132), y + s(160)), fill=outline)
        draw.ellipse((x + s(238), y + s(128), x + s(250), y + s(160)), fill=outline)
        draw.polygon(
            [(x + s(178), y + s(178)), (x + s(202), y + s(178)), (x + s(190), y + s(198))],
            fill=accent,
        )
        for dy in (180, 205):
            draw.line((x + s(38), y + s(dy), x + s(150), y + s(dy - 18)), fill=(220, 230, 235), width=s(4))
            draw.line((x + s(230), y + s(dy - 18), x + s(340), y + s(dy)), fill=(220, 230, 235), width=s(4))
        draw.arc((x + s(315), y + s(270), x + s(500), y + s(520)), 245, 85, fill=accent, width=s(14))

    @staticmethod
    def _draw_server_racks(*, draw: ImageDraw.ImageDraw, x: int, y: int, accent: tuple[int, int, int]) -> None:
        for column in range(3):
            left = x + column * 130
            draw.rounded_rectangle((left, y, left + 100, y + 620), radius=18, fill=(9, 16, 28), outline=(38, 56, 78), width=3)
            for row in range(8):
                top = y + 35 + row * 66
                color = accent if (row + column) % 3 == 0 else (60, 85, 110)
                draw.rounded_rectangle((left + 18, top, left + 82, top + 28), radius=8, fill=(17, 28, 44), outline=color, width=2)

    @staticmethod
    def _draw_monitor(*, draw: ImageDraw.ImageDraw, x: int, y: int, label: str, accent: tuple[int, int, int]) -> None:
        draw.rounded_rectangle((x, y, x + 430, y + 270), radius=28, fill=(5, 9, 16), outline=accent, width=6)
        draw.rectangle((x + 185, y + 270, x + 245, y + 350), fill=(40, 52, 68))
        draw.rounded_rectangle((x + 100, y + 342, x + 330, y + 372), radius=14, fill=(40, 52, 68))
        draw.text((x + 50, y + 45), label, font=_font(44), fill=(250, 250, 252))
        for line in range(3):
            draw.rounded_rectangle((x + 54, y + 120 + line * 42, x + 370, y + 145 + line * 42), radius=9, fill=(24, 40, 58))

    @staticmethod
    def _draw_file_stack(
        *,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        labels: tuple[str, ...],
        accent: tuple[int, int, int],
    ) -> None:
        for index, label in enumerate(labels):
            top = y + index * 145
            draw.rounded_rectangle((x + index * 28, top, x + 390 + index * 28, top + 105), radius=24, fill=(245, 237, 215), outline=accent, width=5)
            draw.text((x + 35 + index * 28, top + 32), label, font=_font(42), fill=(16, 20, 28))
        draw.text((x - 10, y + 500), "CHAOS", font=_font(58), fill=(255, 118, 96), stroke_width=3, stroke_fill=(0, 0, 0))

    @staticmethod
    def _draw_split_panel(*, draw: ImageDraw.ImageDraw, accent: tuple[int, int, int]) -> None:
        draw.rounded_rectangle((110, 780, 500, 1240), radius=34, fill=(45, 20, 25), outline=(255, 104, 104), width=5)
        draw.rounded_rectangle((580, 780, 970, 1240), radius=34, fill=(12, 46, 40), outline=accent, width=5)
        draw.text((158, 835), "BEZ", font=_font(58), fill=(255, 180, 180))
        draw.text((128, 910), "PLANU", font=_font(58), fill=(255, 180, 180))
        draw.text((642, 835), "PLAN", font=_font(58), fill=(198, 255, 232))
        draw.text((628, 910), "+ QA", font=_font(58), fill=(198, 255, 232))
        for i in range(4):
            draw.line((165, 1025 + i * 40, 430, 1060 + i * 38), fill=(255, 118, 96), width=6)
            draw.rounded_rectangle((640, 1025 + i * 48, 910, 1058 + i * 48), radius=12, fill=(23, 72, 64), outline=accent, width=3)

    @staticmethod
    def _draw_qa_button(*, draw: ImageDraw.ImageDraw, accent: tuple[int, int, int]) -> None:
        draw.rounded_rectangle((250, 820, 830, 1180), radius=90, fill=(120, 28, 28), outline=(255, 170, 120), width=10)
        draw.text((360, 930), "QA", font=_font(150), fill=(255, 255, 255), stroke_width=5, stroke_fill=(24, 0, 0))
        draw.line((220, 1280, 860, 700), fill=accent, width=8)
        draw.text((290, 1220), "ODRZUC", font=_font(58), fill=(255, 210, 190), stroke_width=3, stroke_fill=(0, 0, 0))

    @staticmethod
    def _draw_storyboard_cards(
        *,
        draw: ImageDraw.ImageDraw,
        accent: tuple[int, int, int],
        checked: bool,
    ) -> None:
        labels = ("HOOK", "BEAT", "PAYOFF", "CTA")
        for index, label in enumerate(labels):
            left = 140 + index * 220
            top = 820 + (index % 2) * 80
            draw.rounded_rectangle((left, top, left + 170, top + 235), radius=28, fill=(7, 12, 20), outline=accent, width=5)
            draw.text((left + 26, top + 42), label, font=_font(34), fill=(235, 244, 255))
            draw.rectangle((left + 35, top + 112, left + 135, top + 180), outline=(70, 95, 120), width=4)
            if checked:
                draw.line((left + 48, top + 148, left + 78, top + 178), fill=(120, 255, 180), width=7)
                draw.line((left + 78, top + 178, left + 132, top + 122), fill=(120, 255, 180), width=7)

    @staticmethod
    def _scene_badge(*, text: str, index: int, total: int) -> str:
        normalized = _clean_display(text).casefold()
        if index == 1:
            return "HOOK"
        if index == total:
            return "CTA"
        if "qa" in normalized:
            return "QA CHECK"
        if "plan" in normalized or "storyboard" in normalized:
            return "PLAN"
        if "blad" in normalized or "błąd" in normalized or "problem" in normalized:
            return "PROBLEM"
        if "wynik" in normalized or "jakosc" in normalized or "jakość" in normalized:
            return "WYNIK"
        return f"KADR {index}"

    @staticmethod
    def _draw_visual_anchor(*, draw: ImageDraw.ImageDraw, index: int, accent: tuple[int, int, int]) -> None:
        center_x, center_y = 540, 980
        draw.ellipse((center_x - 150, center_y - 150, center_x + 150, center_y + 150), outline=accent, width=8)
        draw.ellipse((center_x - 72, center_y - 72, center_x + 72, center_y + 72), outline=(235, 244, 255), width=5)
        if index % 3 == 1:
            draw.line((center_x - 210, center_y + 210, center_x + 210, center_y - 210), fill=(235, 244, 255), width=7)
        elif index % 3 == 2:
            draw.rounded_rectangle((center_x - 180, center_y - 95, center_x + 180, center_y + 95), radius=36, outline=accent, width=7)
        else:
            for offset in (-170, 0, 170):
                draw.line((center_x + offset, center_y - 170, center_x + offset, center_y + 170), fill=accent, width=6)

    @staticmethod
    def _draw_background(*, draw: ImageDraw.ImageDraw, index: int) -> None:
        width, height = VisualStoryboardService.SIZE
        palettes = (
            ((5, 15, 28), (24, 83, 96)),
            ((12, 11, 32), (96, 48, 122)),
            ((22, 13, 10), (138, 52, 28)),
            ((4, 22, 18), (32, 112, 72)),
        )
        top, bottom = palettes[(index - 1) % len(palettes)]
        for y in range(height):
            factor = y / height
            color = tuple(int(top[i] + (bottom[i] - top[i]) * factor) for i in range(3))
            draw.line((0, y, width, y), fill=color)

        accent = _accent(index)
        for offset in range(0, 720, 120):
            draw.arc((80 + offset, 220 + offset, 1000 - offset // 3, 1520 + offset), 205, 340, fill=accent, width=4)
        draw.rectangle((0, 0, width, height), outline=(255, 255, 255), width=0)

    @staticmethod
    def _draw_glow(*, image: Image.Image, index: int) -> None:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        accent = _accent(index)
        draw.ellipse((620, 260, 1120, 760), fill=(*accent, 48))
        draw.ellipse((-130, 980, 480, 1590), fill=(255, 210, 110, 38))
        overlay = overlay.filter(ImageFilter.GaussianBlur(22))
        image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))

    @staticmethod
    def _draw_subtitle_safe_zone(*, draw: ImageDraw.ImageDraw) -> None:
        # Final subtitles are burned in by FFmpeg. Keep this zone visually calm instead of
        # duplicating the same scene text three times on screen.
        draw.rounded_rectangle((62, 1450, 1018, 1768), radius=34, fill=(4, 8, 16), outline=(50, 70, 96), width=2)
        draw.text((100, 1498), "NAPISY / VOICEOVER", font=_font(30), fill=(96, 122, 150))

    @staticmethod
    def _draw_progress(*, draw: ImageDraw.ImageDraw, index: int, total: int, accent: tuple[int, int, int]) -> None:
        x1, y1, x2, y2 = 72, 1810, 1008, 1832
        draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill=(28, 36, 50))
        progress = max(0.0, min(1.0, index / max(total, 1)))
        draw.rounded_rectangle((x1, y1, int(x1 + (x2 - x1) * progress), y2), radius=10, fill=accent)


def _accent(index: int) -> tuple[int, int, int]:
    accents = (
        (255, 126, 68),
        (69, 196, 255),
        (255, 214, 92),
        (88, 220, 150),
        (235, 92, 255),
        (255, 84, 112),
    )
    return accents[(index - 1) % len(accents)]


def _hex_to_rgb(value: str, *, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    normalized = str(value or "").strip().lstrip("#")
    if len(normalized) != 6:
        return fallback
    try:
        return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _font(size: int):
    for candidate in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _clean_display(value: str) -> str:
    return " ".join(str(value or "").replace("`", "").split())
