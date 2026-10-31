from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json
import math
import sys

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from PIL import Image, ImageDraw, ImageFont

from atnos.core.config import Settings
from channel_ops import ChannelOpsService, ContentStatus
from content.render_ffmpeg import ShortRenderService
from content.render_qa import RenderQaService
from content.subtitles import ShortSubtitleService
from content.tts_piper import PiperNarration
from content.visual_quality_review import VisualQualityReviewService


W, H = 1080, 1920
BG = (7, 13, 19)
INK = (235, 242, 246)
MUTED = (137, 155, 166)
GREEN = (53, 232, 139)
TEAL = (37, 178, 204)
AMBER = (255, 184, 77)
RED = (244, 82, 82)
CARD = (15, 28, 38)


def main() -> None:
    settings = Settings()
    settings.ensure_directories()
    channel_id = "faceless-ai-pl"
    ops = ChannelOpsService(settings)
    if channel_id not in {channel.channel_id for channel in ops.list_channels()}:
        ops.register_channel(
            channel_id=channel_id,
            display_name="Faceless AI PL",
            niche="AI narzedzia, lokalne AI i automatyzacja dla tworcow solo",
            language="pl",
            audience="tworcy solo",
            daily_target_count=2,
        )

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output_dir = settings.data_dir / "channel_ops" / "channels" / channel_id / "renders" / f"{run_id}-ideal-local-cloud-v3"
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    script = {
        "title": "Nie wysylaj tego do chmury",
        "hook": "Nie wysylaj tego do chmury, jesli laptop moze zrobic pierwszy szkic lokalnie.",
        "body": [
            "Brudne notatki, szkice promptow i pierwsze wersje scenariusza trzymaj lokalnie.",
            "Atnos uklada script.md, narration.wav, subtitles.srt i thumbnail.png bez dotykania publicznego konta.",
            "Dopiero po QA decydujesz, czy uzyc Claude, Gemini albo prywatnego uploadu YouTube.",
        ],
        "cta": "Nastepna rolka: co wysylac do chmury, a czego nie ruszac poza sandboxiem.",
    }
    voiceover = " ".join([script["hook"], *script["body"], script["cta"]])
    script["voiceover"] = voiceover
    write_script(output_dir, script)

    frames = create_frames(frames_dir)
    visual_manifest = write_visual_manifest(output_dir=output_dir, frames=frames)
    visual_report = VisualQualityReviewService().review(
        output_dir=output_dir,
        storyboard_manifest=visual_manifest,
        frames=frames,
    )
    narration = PiperNarration(settings).generate(text=voiceover, output_dir=output_dir)
    subtitles = ShortSubtitleService(settings).generate(audio_path=narration, output_dir=output_dir, fallback_text=voiceover)
    final_mp4, render_metadata = ShortRenderService(settings).render(
        frame_paths=frames,
        audio_path=narration,
        subtitles_path=subtitles,
        output_dir=output_dir,
    )
    qa = RenderQaService().build(
        final_mp4=final_mp4,
        render_metadata=render_metadata,
        subtitles_path=subtitles,
        audio_path=narration,
        expected_text=voiceover,
    )
    (output_dir / "render_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "visual_plan.json").write_text(
        json.dumps({"frames": [str(path) for path in frames]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(output_dir=output_dir, final_mp4=final_mp4, frame_count=len(frames), qa=qa, visual_report=visual_report.to_dict())

    item = ops.enqueue_topic(channel_id, "IDEAL SAMPLE V3: Nie wysylaj tego do chmury")
    ready = ops.mark_render_ready(item.item_id, output_dir)
    ops.calendar.update_item(ready.with_update(status=ContentStatus.MANUAL_UPLOAD_READY))

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "final_mp4": str(final_mp4),
                "script": str(output_dir / "script.md"),
                "subtitles": str(subtitles),
                "render_qa": str(output_dir / "render_qa.json"),
                "visual_quality_report": str(output_dir / "visual_quality_report.json"),
                "production_report": str(output_dir / "production_report.md"),
                "qa_ok": qa.get("ok"),
                "visual_quality_ok": visual_report.passed,
                "youtube_shorts_ready": qa.get("youtube_shorts_ready"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def write_script(output_dir: Path, script: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    body = [str(item) for item in script["body"]]
    (output_dir / "script.md").write_text(
        "\n".join(
            [
                f"# {script['title']}",
                "",
                "## Hook",
                str(script["hook"]),
                "",
                "## Body",
                *[f"- {line}" for line in body],
                "",
                "## CTA",
                str(script["cta"]),
                "",
                "## Voiceover",
                str(script["voiceover"]),
                "",
            ]
        ),
        encoding="utf-8",
    )


def create_frames(frames_dir: Path) -> list[Path]:
    font_path = next(
        (path for path in (Path(r"C:\Windows\Fonts\bahnschrift.ttf"), Path(r"C:\Windows\Fonts\segoeui.ttf")) if path.exists()),
        None,
    )

    def font(size: int):
        return ImageFont.truetype(str(font_path), size=size) if font_path else ImageFont.load_default()

    fx = font(82)
    fl = font(58)
    fm = font(42)
    fs = font(30)
    frames = []
    specs = [
        (
            "01-hook.png",
            "NIE WYSYLAJ TEGO DO CHMURY",
            "jesli laptop moze zrobic pierwszy szkic lokalnie",
            GREEN,
            [("LOCAL", "notatki / drafty / QA"), ("CLOUD", "research / model premium / review")],
        ),
        (
            "02-drafty.png",
            "1. BRUDNE SZKICE LOKALNIE",
            "pomysly i prompt drafty nie musza wychodzic poza komputer",
            AMBER,
            [("INPUT", "chaos notatek"), ("ATNOS", "uklada plan"), ("OUTPUT", "script.md")],
        ),
        (
            "03-artefakty.png",
            "2. ARTEFAKTY, NIE TEKST",
            "short ma konczyc sie paczka plikow do oceny",
            TEAL,
            [("SCRIPT", "script.md"), ("VOICE", "narration.wav"), ("CAPTIONS", "subtitles.srt"), ("VIDEO", "final.mp4")],
        ),
        (
            "04-qa.png",
            "3. QA PRZED UPLOADEM",
            "hook, napisy, audio i pionowy format musza przejsc test",
            GREEN,
            [("CHECK", "hook < 2s"), ("CHECK", "czytelne napisy"), ("CHECK", "1080x1920 + audio")],
        ),
        (
            "05-approval.png",
            "UPLOAD TO OSOBNA DECYZJA",
            "prywatnie dopiero po approval, publicznie nigdy automatycznie",
            RED,
            [("SAFE", "manual_upload_ready"), ("APPROVAL", "private upload"), ("BLOCK", "public auto upload")],
        ),
        (
            "06-loop.png",
            "LOKALNIE = START PRODUKCJI",
            "nastepna rolka: co wysylac do Claude, a czego nie ruszac poza sandboxiem",
            GREEN,
            [("NEXT", "Claude / Gemini / lokalne AI: kto robi co?")],
        ),
    ]
    for spec in specs:
        frames.append(draw_frame(frames_dir, spec, fx, fl, fm, fs))
    return frames


def write_visual_manifest(*, output_dir: Path, frames: list[Path]) -> Path:
    path = output_dir / "visual_storyboard.json"
    path.write_text(
        json.dumps(
            {
                "engine": "controlled-ideal-reel-sample",
                "frame_count": len(frames),
                "subtitle_safe_zone_reserved": True,
                "subtitle_safe_zone_height_px": 460,
                "max_subtitle_words_per_cue": 4,
                "duplicated_scene_text_layers": False,
                "full_scene_text_on_frame": False,
                "scenes": [
                    "Nie wysylaj tego do chmury",
                    "Brudne szkice lokalnie",
                    "Artefakty zamiast sciany tekstu",
                    "QA przed uploadem",
                    "Upload wymaga approval",
                    "Lokalnie start produkcji",
                ],
                "frames": [str(frame) for frame in frames],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def draw_frame(frames_dir: Path, spec, fx, fl, fm, fs) -> Path:
    filename, title, subtitle, accent, cards = spec
    img = gradient(accent)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((54, 56, 370, 116), radius=20, fill=(10, 22, 30), outline=(31, 76, 86), width=2)
    draw.text((82, 75), "ATNOS / SHORT", font=fs, fill=MUTED)
    y = 250
    y = draw_wrapped(draw, title, (70, y), fx, 940, INK, 10) + 26
    y = draw_wrapped(draw, subtitle, (74, y), fm, 900, MUTED, 8) + 70
    card_y = max(780, y)
    for index, (kicker, body) in enumerate(cards):
        row = index // 2
        col = index % 2
        if len(cards) <= 3:
            x1, x2 = 80, 1000
            y1 = card_y + index * 190
        else:
            x1 = 80 if col == 0 else 590
            x2 = 490 if col == 0 else 1000
            y1 = card_y + row * 220
        y2 = y1 + 160
        draw.rounded_rectangle((x1, y1, x2, y2), radius=28, fill=CARD, outline=accent, width=3)
        draw.text((x1 + 30, y1 + 24), kicker, font=fs, fill=accent)
        draw_wrapped(draw, body, (x1 + 30, y1 + 72), fm, x2 - x1 - 60, INK, 4)
    draw.rounded_rectangle((70, 1662, 1010, 1788), radius=26, fill=(5, 14, 20), outline=(42, 67, 76), width=2)
    draw_wrapped(draw, "Kazda ryzykowna akcja czeka na approval. Zero publicznego uploadu bez zgody.", (104, 1694), fs, 860, MUTED, 6)
    path = frames_dir / filename
    img.save(path)
    return path


def gradient(accent: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    pix = img.load()
    for y in range(H):
        for x in range(W):
            dx = (x - W * 0.70) / W
            dy = (y - H * 0.22) / H
            glow = max(0, 1 - math.sqrt(dx * dx + dy * dy) * 2.1)
            base = 8 + int(glow * 22)
            pix[x, y] = (base, base + int(glow * accent[1] * 0.13), base + int(glow * accent[2] * 0.17))
    return img


def draw_wrapped(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], font, max_width: int, fill, line_gap: int) -> int:
    x, y = xy
    line = ""
    lines = []
    for word in text.split():
        test = f"{line} {word}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    for item in lines:
        draw.text((x, y), item, font=font, fill=fill)
        bbox = draw.textbbox((x, y), item, font=font)
        y += bbox[3] - bbox[1] + line_gap
    return y


def write_report(
    *,
    output_dir: Path,
    final_mp4: Path,
    frame_count: int,
    qa: dict[str, object],
    visual_report: dict[str, object],
) -> None:
    (output_dir / "production_report.md").write_text(
        "\n".join(
            [
                "# Ideal Manual Reel V3",
                "",
                "- status: manual_upload_ready",
                "- channel_id: faceless-ai-pl",
                f"- output_dir: {output_dir}",
                f"- final_mp4: {final_mp4}",
                f"- frames: {frame_count}",
                f"- render_qa_ok: {qa.get('ok')}",
                f"- visual_quality_ok: {visual_report.get('passed')}",
                f"- visual_quality_score: {visual_report.get('overall_score')}",
                f"- youtube_shorts_ready: {qa.get('youtube_shorts_ready')}",
                "- note: ASCII-only controlled sample to avoid PowerShell encoding damage.",
                "",
                "## Script",
                (output_dir / "script.md").read_text(encoding="utf-8"),
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
