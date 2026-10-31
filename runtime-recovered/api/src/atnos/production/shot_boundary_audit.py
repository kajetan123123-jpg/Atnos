"""Read-only cut evidence from the final video, using PySceneDetect.

Content changes within a shot can also trigger this detector.  The result is
diagnostic evidence for a reviewer, not an automatic editing decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def analyze_shot_boundaries(
    video_path: str | Path,
    *,
    max_seconds: float = 65.0,
    threshold: float = 27.0,
) -> dict[str, Any]:
    path = Path(video_path)
    if not path.is_file() or path.stat().st_size <= 0:
        return {"status": "unavailable", "reason": "final_video_missing"}
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        return {"status": "unavailable", "reason": "scenedetect_headless_not_installed"}

    try:
        video = open_video(str(path))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold))
        # PySceneDetect interprets a numeric end_time as frames on some backends.
        manager.detect_scenes(video, end_time=f"{max_seconds}s", show_progress=False)
        scenes = manager.get_scene_list(start_in_scene=True)
        cut_times = [round(float(end.seconds), 3) for _, end in scenes[:-1]]
        shot_lengths = [round(float(end.seconds - start.seconds), 3) for start, end in scenes]
        return {
            "status": "measured",
            "source": "PySceneDetect ContentDetector 0.7.1",
            "sample_limit_seconds": max_seconds,
            "threshold": threshold,
            "scene_count": len(scenes),
            "cut_count": len(cut_times),
            "cut_times_seconds": cut_times,
            "shot_lengths_seconds": shot_lengths,
            "longest_shot_seconds": max(shot_lengths, default=None),
        }
    except (OSError, ValueError, RuntimeError) as exc:
        return {"status": "unavailable", "reason": type(exc).__name__, "detail": str(exc)[:300]}
