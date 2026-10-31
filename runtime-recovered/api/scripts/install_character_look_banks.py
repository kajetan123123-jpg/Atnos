#!/usr/bin/env python3
"""Install Wealth + Archipelag look banks and render orientation training reels."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from atnos.core.config import get_settings  # noqa: E402
from atnos.production.character_look_bank import KIT_KEYS, all_status, install_all  # noqa: E402


def _font(size: int):
    for candidate in (
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _ffmpeg_executable() -> str:
    candidates = (
        Path(r"E:\Atnos\Tools\FFmpeg\bin\ffmpeg.exe"),
        Path(r"E:\Atnos\Tools\FFmpegShared\bin\ffmpeg.exe"),
        Path("ffmpeg"),
    )
    for candidate in candidates:
        if candidate.name == "ffmpeg" or candidate.is_file():
            return str(candidate)
    return "ffmpeg"


def render_orientation_reel(kit_dir: Path, out_dir: Path) -> Path | None:
    board = kit_dir / "visual-bible-board.jpg"
    if not board.is_file():
        board = kit_dir / "visual-reference.png"
    if not board.is_file():
        return None
    manifest = json.loads((kit_dir / "training" / "look-bank-manifest.json").read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    base = Image.open(board).convert("RGB")
    # Normalize to vertical short frame with board letterboxed.
    canvas_w, canvas_h = 1080, 1920
    title_font = _font(42)
    body_font = _font(28)
    frame_paths: list[Path] = []
    slides = [
        {
            "title": manifest.get("universe") or manifest.get("channel") or kit_dir.name,
            "body": "Visual bible lock\nSame hand. Same line. Same cast.",
            "seconds": 3,
        }
    ]
    for film in manifest.get("training_films") or []:
        slides.append(
            {
                "title": film.get("title") or film.get("id"),
                "body": film.get("goal") or "",
                "seconds": 4,
            }
        )
    slides.append(
        {
            "title": "Look bank",
            "body": "Approved assets only.\nNo episode until continuity slots are filled.",
            "seconds": 3,
        }
    )
    index = 0
    for slide in slides:
        for _ in range(int(slide["seconds"]) * 30):
            frame = Image.new("RGB", (canvas_w, canvas_h), (20, 24, 32))
            # Fit board into upper 60% of frame.
            max_w, max_h = canvas_w - 80, int(canvas_h * 0.58)
            ratio = min(max_w / base.width, max_h / base.height)
            sized = base.resize((max(1, int(base.width * ratio)), max(1, int(base.height * ratio))))
            x = (canvas_w - sized.width) // 2
            y = 80
            frame.paste(sized, (x, y))
            draw = ImageDraw.Draw(frame)
            draw.rectangle((40, int(canvas_h * 0.68), canvas_w - 40, canvas_h - 80), fill=(14, 20, 28))
            draw.text((70, int(canvas_h * 0.70)), str(slide["title"])[:80], fill=(240, 229, 210), font=title_font)
            draw.multiline_text(
                (70, int(canvas_h * 0.78)),
                str(slide["body"])[:220],
                fill=(180, 200, 210),
                font=body_font,
                spacing=8,
            )
            path = frames_dir / f"frame-{index:05d}.jpg"
            frame.save(path, quality=90)
            frame_paths.append(path)
            index += 1
    mp4 = out_dir / f"{kit_dir.name}-look-bank-orientation.mp4"
    ffmpeg = _ffmpeg_executable()
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        "30",
        "-i",
        str(frames_dir / "frame-%05d.jpg"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(mp4),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        (out_dir / "ffmpeg-error.txt").write_text(str(exc), encoding="utf-8")
        return None
    return mp4


def main() -> int:
    parser = argparse.ArgumentParser(description="Install character look banks for Atnos channels")
    parser.add_argument("--render-orientation", action="store_true", help="Render bible orientation reels")
    args = parser.parse_args()
    settings = get_settings()
    installed = install_all(settings)
    print(json.dumps({"installed": installed}, ensure_ascii=False, indent=2))
    if args.render_orientation:
        outputs = []
        for key in KIT_KEYS:
            kit_dir = settings.data_dir / "brand-kits" / key
            out_dir = settings.data_dir / "look-bank-training" / key
            mp4 = render_orientation_reel(kit_dir, out_dir)
            outputs.append({"kit": key, "mp4": str(mp4) if mp4 else None, "out_dir": str(out_dir)})
        print(json.dumps({"orientation_reels": outputs, "status": all_status(settings)}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(all_status(settings), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
