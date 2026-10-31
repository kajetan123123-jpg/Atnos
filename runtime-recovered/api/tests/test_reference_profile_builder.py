from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.reference_profile import load_reference_shorts_profile
from atnos.production.reference_profile_builder import build_reference_shorts_profile
from test_support import build_test_settings


def test_build_reference_profile_from_local_mp4_and_loads_latest(tmp_path: Path) -> None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    settings = build_test_settings(tmp_path)
    source = tmp_path / "reference.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=360x640:d=2:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )

    profile = build_reference_shorts_profile(settings, [source], max_video_seconds=2)

    assert profile["sample_count"] == 1
    assert Path(profile["path"]).exists()
    assert profile["median_duration_sec"] >= 1.8
    assert profile["median_brightness"] is not None
    assert profile["median_saturation"] is not None
    assert profile["median_frame_delta"] is not None
    assert profile["median_p95_brightness_delta"] is not None
    assert profile["median_flash_events_per_10s"] is not None
    assert profile["samples"][0]["file_name"] == "reference.mp4"
    assert profile["production_rules_for_atnos"]

    loaded = load_reference_shorts_profile(settings)

    assert loaded.available is True
    assert loaded.sample_count == 1
    assert loaded.median_brightness == profile["median_brightness"]
    assert loaded.median_frame_delta == profile["median_frame_delta"]
    assert any("white flashes" in rule for rule in loaded.rules)
    assert loaded.to_payload()["target_lufs"] <= -12.0


def test_active_app_reference_profile_takes_priority_over_user_profile(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    user_path = settings.data_dir / "learning" / "user-reference-shorts" / "latest_reference_profile.json"
    active_path = settings.data_dir / "learning" / "active-reference-shorts" / "latest_reference_profile.json"
    user_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(json.dumps({"source": "user", "sample_count": 7, "median_cuts_per_10s": 1.0}), encoding="utf-8")
    active_path.write_text(
        json.dumps({"source": "active_app_reference_channels", "sample_count": 4, "median_cuts_per_10s": 3.0}),
        encoding="utf-8",
    )

    loaded = load_reference_shorts_profile(settings)

    assert loaded.available is True
    assert loaded.source == "active_app_reference_channels"
    assert loaded.sample_count == 4
    assert loaded.median_cuts_per_10s == 3.0
    assert loaded.path == str(active_path)
