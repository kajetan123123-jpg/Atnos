from __future__ import annotations

from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont


class ThumbnailService:
    def __init__(self, *, brand_label: str = "") -> None:
        self.brand_label = " ".join(brand_label.split()).strip()

    def generate(self, *, title: str, topic: str, output_dir: Path) -> Path:
        path = output_dir / "thumbnail.png"
        image = Image.new("RGB", (1080, 1920), (16, 24, 32))
        draw = ImageDraw.Draw(image)
        for y in range(1920):
            shade = int(32 + (y / 1920) * 80)
            draw.line((0, y, 1080, y), fill=(shade, 42, 28))
        font_title = _font(86)
        font_topic = _font(46)
        draw.rectangle((70, 130, 1010, 1790), outline=(255, 110, 68), width=8)
        y = 280
        for line in textwrap.wrap(title.upper(), width=14)[:5]:
            draw.text((110, y), line, font=font_title, fill=(255, 245, 226))
            y += 105
        if self.brand_label:
            draw.text((110, 1530), self.brand_label.upper(), font=font_topic, fill=(255, 168, 96))
        for line in textwrap.wrap(topic, width=28)[:3]:
            draw.text((110, y + 40), line, font=font_topic, fill=(235, 235, 220))
            y += 58
        image.save(path)
        return path


def _font(size: int):
    for candidate in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()
