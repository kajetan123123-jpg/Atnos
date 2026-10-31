from pathlib import Path

import cv2
import numpy as np

from atnos.production.shot_boundary_audit import analyze_shot_boundaries


def test_detects_real_cut_at_video_time_not_frame_number(tmp_path: Path) -> None:
    video_path = tmp_path / "two_shots.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 24, (96, 96))
    assert writer.isOpened()
    for color in ((10, 10, 10), (240, 240, 240)):
        frame = np.full((96, 96, 3), color, dtype=np.uint8)
        for _ in range(24):
            writer.write(frame)
    writer.release()

    result = analyze_shot_boundaries(video_path, max_seconds=3.0)
    assert result["status"] == "measured"
    assert result["cut_count"] == 1
    assert abs(result["cut_times_seconds"][0] - 1.0) < 0.1
    assert sum(result["shot_lengths_seconds"]) >= 1.9


def test_missing_video_reports_unavailable(tmp_path: Path) -> None:
    result = analyze_shot_boundaries(tmp_path / "missing.mp4")
    assert result == {"status": "unavailable", "reason": "final_video_missing"}
