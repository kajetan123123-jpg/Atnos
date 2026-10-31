from __future__ import annotations

import json
from pathlib import Path

import pytest

from atnos.production.existing_short_remaster import (
    RemasterScene,
    build_clean_final_command,
    build_stabilized_visual_command,
    build_visual_command,
    load_remaster_scenes,
)


def test_load_remaster_scenes_aligns_each_clip_to_voice(tmp_path: Path) -> None:
    clips = []
    timestamps = []
    for index, duration in enumerate((2.0, 3.5), start=1):
        clip = tmp_path / f"s{index:02d}.mp4"
        clip.write_bytes(b"video")
        clips.append({"scene_id": index - 1, "file": str(clip), "duration_s": 1.5})
        start = sum((2.0, 3.5)[: index - 1])
        timestamps.append({"segment_id": f"s{index:02d}", "start_sec": start, "end_sec": start + duration})
    video_manifest = tmp_path / "video_manifest.json"
    voice_manifest = tmp_path / "audio_timestamps.json"
    video_manifest.write_text(json.dumps({"clips": clips}), encoding="utf-8")
    voice_manifest.write_text(json.dumps({"timestamps": timestamps}), encoding="utf-8")

    scenes = load_remaster_scenes(video_manifest, voice_manifest)

    assert [scene.target_duration_s for scene in scenes] == [2.0, 3.5]
    assert [scene.scene_id for scene in scenes] == ["s01", "s02"]


def test_load_remaster_scenes_rejects_scene_count_mismatch(tmp_path: Path) -> None:
    clip = tmp_path / "s01.mp4"
    clip.write_bytes(b"video")
    video_manifest = tmp_path / "video_manifest.json"
    voice_manifest = tmp_path / "audio_timestamps.json"
    video_manifest.write_text(json.dumps({"clips": [{"file": str(clip), "duration_s": 2.0}]}), encoding="utf-8")
    voice_manifest.write_text(json.dumps({"timestamps": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="Scene count mismatch"):
        load_remaster_scenes(video_manifest, voice_manifest)


def test_visual_command_uses_hard_cuts_without_flash_transitions(tmp_path: Path) -> None:
    scenes = [
        RemasterScene("s01", tmp_path / "s01.mp4", 2.0, 2.5),
        RemasterScene("s02", tmp_path / "s02.mp4", 3.0, 4.0),
    ]

    command = build_visual_command("ffmpeg", scenes, tmp_path / "silent.mp4")
    rendered = " ".join(command)

    assert "concat=n=2:v=1:a=0" in rendered
    assert "tpad=stop_mode=clone:stop_duration=1" in rendered
    assert "xfade" not in rendered
    assert "fade=" not in rendered
    assert "fps=30" in rendered


def test_clean_final_command_does_not_master_normalized_voice_twice(tmp_path: Path) -> None:
    command = build_clean_final_command(
        "ffmpeg",
        silent_video=tmp_path / "silent.mp4",
        narration=tmp_path / "narration.wav",
        subtitles=tmp_path / "subtitles.ass",
        fonts_dir=tmp_path / "fonts",
        output_path=tmp_path / "final.mp4",
    )
    rendered = " ".join(command)

    assert "aresample=48000" in rendered
    assert "equalizer=" not in rendered
    assert "acompressor=" not in rendered
    assert "loudnorm=" not in rendered


def test_stabilized_visual_command_uses_one_safe_frame_and_continuous_motion(tmp_path: Path) -> None:
    scenes = [RemasterScene("s01", tmp_path / "s01.mp4", 2.0, 2.5)]

    command = build_stabilized_visual_command("ffmpeg", scenes, tmp_path / "silent.mp4")
    rendered = " ".join(command)

    assert "trim=start=0.350000:end=0.383333" in rendered
    assert "zoompan=" in rendered
    assert "scale=4320:7680" in rendered
    assert "z='1+0.035*on/74'" in rendered
    assert ":d=75:s=1080x1920:fps=30" in rendered
    assert "setpts=1.250000000*PTS" not in rendered
    assert "xfade" not in rendered
