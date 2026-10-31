from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from atnos.production import editorial_film
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.order_execution import StageExecutor
from atnos.production.steps.order_media import delivery_format_matches, full_frame_scan, produce_captions
from test_production_order_store import store, create
from test_production_order_script import script


@pytest.mark.parametrize("fps,expected", [(30, True), (30.00022398, True), (29.97, False),
    (25, False), (60, False), (None, False), (float("nan"), False)])
def test_format_tolerates_only_container_rounding(fps, expected):
    probe = {"width": 1080, "height": 1920, "fps": fps, "video_codec": "h264", "pixel_format": "yuv420p"}
    assert delivery_format_matches(probe) is expected
    assert not delivery_format_matches({**probe, "width": 1920})
    assert not delivery_format_matches({**probe, "video_codec": "hevc"})
    assert not delivery_format_matches({**probe, "pixel_format": "yuv444p"})


def test_caption_limits_and_actual_layout_follow_profile():
    words = [{"text": word, "segment_id": "s01", "start_sec": i*.3, "end_sec": (i+1)*.3}
             for i, word in enumerate("Choose a manageable amount for your savings today".split())]
    groups = editorial_film.caption_groups(words, max_words=3, max_characters=25)
    assert all(len(g) <= 3 and len(" ".join(w["text"] for w in g)) <= 25 for g in groups)
    assert [w for g in groups for w in g] == words
    for group in groups:
        box = editorial_film.caption_layout(group, safe_area=(100, 300, 900, 1500))[-1]
        assert 100 <= box[0] < box[2] <= 900 and 300 <= box[1] < box[3] <= 1500


def test_oversized_caption_cannot_be_reported_safe():
    with pytest.raises(ValueError, match="safe area"):
        editorial_film.caption_layout([{"text": "ThisCaptionCannotPossiblyFitHere"}], safe_area=(0, 0, 100, 200))
    with pytest.raises(ValueError, match="length"):
        editorial_film.caption_groups([{"text": "UnreasonablyLongWordBeyondTheLimit", "segment_id": "a"}], max_characters=20)


def test_one_group_does_not_cover_the_next_at_boundary():
    groups = [[{"text": "First", "start_sec": 0, "end_sec": 1}], [{"text": "Next", "start_sec": 1, "end_sec": 2}]]
    one, two = Image.new("RGB", (1080, 1920)), Image.new("RGB", (1080, 1920))
    editorial_film.draw_captions(one, groups, 1)
    editorial_film.draw_captions(two, [groups[1]], 1)
    assert one.tobytes() == two.tobytes()


def test_captions_persist_and_mismatch_is_rejected(store, script):
    job = create(store)["id"]
    words = []
    for segment in script.segments:
        for text in segment.text.split():
            start = len(words)*.3
            words.append({"text": text, "start_sec": start, "end_sec": start+.3, "segment_id": segment.id})
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump(mode="json")
        ex.context.artifacts["voice"] = {"word_timestamps": words, "sha256": "test-audio", "duration_seconds": len(words)*.3,
                                         "timing_source": "provider_character_alignment"}
        result = asyncio.run(produce_captions(ex))
    assert result["qa"]["status"] == "passed"
    assert Path(result["output_file"]).read_text(encoding="utf-8").count("-->") == len(result["groups"])


@pytest.mark.parametrize("defect", [None, "black", "flash", "missing_frame"])
def test_real_encoded_video_checks_every_frame(tmp_path, defect):
    ffmpeg = resolve_ffmpeg()
    assert ffmpeg, "This integration test requires the installed FFmpeg runtime"
    frames = [bytes([95]) * 64 * 114 for _ in range(15)]
    if defect in {"black", "flash"}:
        frames[7] = bytes([0 if defect == "black" else 255]) * 64 * 114
    if defect == "missing_frame":
        frames.pop()
    output = tmp_path / "probe.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "64x114", "-r", "30", "-i", "pipe:0",
                    "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", str(output)], input=b"".join(frames), check=True, capture_output=True, timeout=30)
    qa = full_frame_scan(output, expected_frames=15)
    assert qa["status"] == ("passed" if defect is None else "failed")
    if defect == "black":
        assert qa["black_frames"] == [7]
    if defect == "flash":
        assert qa["isolated_flash_frames"] == [7]
    if defect == "missing_frame":
        assert qa["frames_decoded"] == 14
