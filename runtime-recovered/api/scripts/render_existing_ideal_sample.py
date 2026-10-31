from __future__ import annotations

from pathlib import Path
import json
import sys

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings
from channel_ops import ChannelOpsService, ContentStatus
from content.render_ffmpeg import ShortRenderService
from content.render_qa import RenderQaService


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/render_existing_ideal_sample.py <output_dir>")

    output_dir = Path(sys.argv[1]).resolve()
    frames = sorted((output_dir / "frames").glob("*.png"))
    if not frames:
        raise SystemExit(f"No frames found in {output_dir / 'frames'}")
    audio_path = output_dir / "narration.wav"
    subtitles_path = output_dir / "subtitles.srt"
    script_path = output_dir / "script.json"
    if not audio_path.exists():
        raise SystemExit(f"Missing narration: {audio_path}")
    if not subtitles_path.exists():
        raise SystemExit(f"Missing subtitles: {subtitles_path}")

    settings = Settings()
    final_mp4, render_metadata = ShortRenderService(settings).render(
        frame_paths=frames,
        audio_path=audio_path,
        subtitles_path=subtitles_path,
        output_dir=output_dir,
    )
    voiceover = ""
    if script_path.exists():
        script = json.loads(script_path.read_text(encoding="utf-8"))
        voiceover = str(script.get("voiceover") or "")
    qa = RenderQaService().build(
        final_mp4=final_mp4,
        render_metadata=render_metadata,
        subtitles_path=subtitles_path,
        audio_path=audio_path,
        expected_text=voiceover,
    )
    (output_dir / "render_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "visual_plan.json").write_text(
        json.dumps({"frames": [str(path) for path in frames]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path = output_dir / "production_report.md"
    report_path.write_text(
        "\n".join(
            [
                "# Ideal Manual Reel V2",
                "",
                "- status: manual_upload_ready",
                "- channel_id: faceless-ai-pl",
                f"- output_dir: {output_dir}",
                f"- final_mp4: {final_mp4}",
                f"- frames: {len(frames)}",
                f"- render_qa_ok: {qa.get('ok')}",
                f"- youtube_shorts_ready: {qa.get('youtube_shorts_ready')}",
                "- note: recznie kontrolowany skrypt i assety po research session; public upload zablokowany.",
                "",
                "## Script",
                (output_dir / "script.md").read_text(encoding="utf-8") if (output_dir / "script.md").exists() else "",
            ]
        ),
        encoding="utf-8",
    )

    ops = ChannelOpsService(settings)
    item = ops.enqueue_topic("faceless-ai-pl", "IDEAL SAMPLE V2: Nie wysylaj tego do chmury")
    ready = ops.mark_render_ready(item.item_id, output_dir)
    ops.calendar.update_item(ready.with_update(status=ContentStatus.MANUAL_UPLOAD_READY))

    print(json.dumps({"final_mp4": str(final_mp4), "render_qa": qa, "production_report": str(report_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
