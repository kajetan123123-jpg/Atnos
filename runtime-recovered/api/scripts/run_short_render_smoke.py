from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

from PIL import Image, ImageDraw


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.render_ffmpeg import ShortRenderService  # noqa: E402
from content.render_qa import RenderQaService  # noqa: E402


def _write_frame(path: Path) -> None:
    image = Image.new("RGB", (1080, 1920), "#07121c")
    draw = ImageDraw.Draw(image)
    draw.rectangle((72, 120, 1008, 1800), outline="#c2e5ff", width=8)
    draw.text((110, 810), "ATNOS QUALITY COUNCIL", fill="#ffffff")
    draw.text((110, 870), "VALID RENDER SMOKE TEST", fill="#c2e5ff")
    image.save(path)


def _write_audio(path: Path, duration_s: float = 9.0) -> None:
    rate = 44100
    samples = int(rate * duration_s)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        for index in range(samples):
            value = int(2500 * math.sin(2 * math.pi * 220 * index / rate))
            wav.writeframesraw(struct.pack("<h", value))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render and ffprobe a real vertical MP4 smoke test.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ffmpeg", default="E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = output_dir / "frame.png"
    audio = output_dir / "narration.wav"
    subtitles = output_dir / "subtitles.srt"
    _write_frame(frame)
    _write_audio(audio)
    expected_text = "Quality Council verified this render."
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:09,000\nQuality Council verified this render.\n",
        encoding="utf-8",
    )

    settings = Settings(
        app_env="test",
        app_root=API_ROOT,
        data_dir=output_dir / "runtime-data",
        shorts_output_dir=output_dir,
        ffmpeg_exe=str(Path(args.ffmpeg).resolve()),
        render_watchdog_timeout_s=120,
    )
    settings.ensure_directories()
    renderer = ShortRenderService(settings)
    final_mp4, metadata = renderer._render_fast_ffmpeg(
        frame_paths=[frame],
        audio_path=audio,
        subtitles_path=subtitles,
        output_dir=output_dir,
    )
    qa = RenderQaService().build(
        final_mp4=final_mp4,
        render_metadata=metadata,
        subtitles_path=subtitles,
        audio_path=audio,
        expected_text=expected_text,
    )

    ffprobe = Path(args.ffmpeg).resolve().with_name("ffprobe.exe")
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(final_mp4),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    probe = json.loads(completed.stdout) if completed.returncode == 0 else {"error": completed.stderr[-1000:]}
    evidence = {
        "output_path": str(final_mp4),
        "size_bytes": final_mp4.stat().st_size,
        "render_metadata": metadata,
        "qa": qa,
        "ffprobe_exit_code": completed.returncode,
        "ffprobe": probe,
    }
    evidence_path = output_dir / "render-smoke-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"video": str(final_mp4), "evidence": str(evidence_path), "qa_ok": qa["ok"], "ffprobe_exit_code": completed.returncode, "size_bytes": final_mp4.stat().st_size}, ensure_ascii=False))
    return 0 if qa["ok"] and completed.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
