from __future__ import annotations

from pathlib import Path
import re
from urllib.request import urlretrieve

from atnos.core.paths import atnos_state_dir


FONTS_DIR = atnos_state_dir() / "fonts"

REQUIRED_FONTS = {
    "Montserrat-Bold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf",
    "Montserrat-ExtraBold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf",
}


def ensure_caption_fonts(*, fonts_dir: Path | None = None, download_missing: bool = True) -> Path:
    target_dir = (fonts_dir or FONTS_DIR).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    if not download_missing:
        return target_dir
    for filename, url in REQUIRED_FONTS.items():
        target = target_dir / filename
        if target.exists() and target.stat().st_size > 0:
            continue
        urlretrieve(url, target)
    return target_dir


def generate_ass_from_srt(*, srt_path: Path, output_path: Path, fonts_dir: Path) -> Path:
    return generate_ass_subtitles(
        segments=parse_srt_segments(srt_path),
        output_path=output_path,
        fonts_dir=fonts_dir,
    )


def parse_srt_segments(srt_path: Path) -> list[dict[str, object]]:
    raw = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip())
    segments: list[dict[str, object]] = []
    for block in blocks:
        lines = [line.strip("\ufeff ") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_line = next((line for line in lines if "-->" in line), "")
        if not time_line:
            continue
        start_raw, end_raw = [part.strip() for part in time_line.split("-->", maxsplit=1)]
        text_lines = [line for line in lines if line != time_line and not line.isdigit()]
        text = " ".join(text_lines).strip()
        if not text:
            continue
        segments.append(
            {
                "start": _parse_srt_time(start_raw),
                "end": _parse_srt_time(end_raw),
                "text": text,
            }
        )
    return segments


def generate_ass_subtitles(
    *,
    segments: list[dict[str, object]],
    output_path: Path,
    fonts_dir: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not segments:
        segments = [{"start": 0.0, "end": 3.0, "text": "Atnos short"}]

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat ExtraBold,22,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for segment in segments:
        start_seconds = float(segment.get("start") or 0.0)
        end_seconds = float(segment.get("end") or 0.0)
        if end_seconds <= start_seconds:
            end_seconds = start_seconds + 1.5
        start = _ass_timestamp(start_seconds)
        end = _ass_timestamp(end_seconds)
        text = _wrap_ass_text(str(segment.get("text") or ""))
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output_path


def subtitle_filter_for_path(subtitles_path: Path, *, fonts_dir: Path | None = None) -> str:
    if subtitles_path.suffix.casefold() == ".ass":
        filename = _escape_filter_path(subtitles_path)
        font_dir = _escape_filter_path(fonts_dir or FONTS_DIR)
        return f"ass=filename='{filename}':fontsdir='{font_dir}'"
    filename = _escape_filter_path(subtitles_path)
    style = (
        "FontName=Montserrat ExtraBold,"
        "FontSize=22,"
        "Bold=1,"
        "Outline=4,"
        "Shadow=2,"
        "Alignment=2,"
        "MarginV=120,"
        "PrimaryColour=&H00FFFFFF&,"
        "OutlineColour=&H00000000&,"
        "BackColour=&H80000000&"
    )
    return f"subtitles=filename='{filename}':force_style='{style}'"


def _wrap_ass_text(text: str, max_chars: int = 35) -> str:
    words = " ".join(text.split()).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return r"\N".join(_escape_ass_text(line) for line in lines[:2])


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _parse_srt_time(value: str) -> float:
    match = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)", value)
    if not match:
        return 0.0
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _escape_filter_path(path: Path) -> str:
    value = path.expanduser().resolve().as_posix()
    value = value.replace("\\", "/")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    return value
