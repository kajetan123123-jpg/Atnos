from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from PIL import Image

from atnos.core.config import Settings
import content.public_short_frame_observer as observer_module
from content.public_short_frame_observer import PublicShortFrameObservationRunner, ResolvedPublicVideo


def test_observe_url_deletes_raw_frames_by_default() -> None:
    with TemporaryDirectory(prefix="atnos-public-frame-") as temp:
        root = Path(temp)
        runner = PublicShortFrameObservationRunner(
            Settings(app_root=root, data_dir=root / "data"),
            stream_resolver=_fake_resolver,
            frame_extractor=_fake_extractor,
        )

        result = runner.observe_url(
            url="https://www.youtube.com/watch?v=abc123",
            output_dir=root / "observed",
            sample_times_sec=(0.5, 2.0),
            keep_frames=False,
        )

        assert result.status == "observed"
        assert result.extracted_frame_count == 2
        assert result.raw_frames_retained is False
        assert not (root / "observed" / "raw_frames").exists()
        assert (root / "observed" / "analysis" / "frame_observation.json").exists()
        assert (root / "observed" / "public_player_observation.json").exists()
        assert result.observation_package.source_kind == "public_player_sample_analysis_only"


def test_observe_url_can_keep_raw_frames_for_manual_qa() -> None:
    with TemporaryDirectory(prefix="atnos-public-frame-keep-") as temp:
        root = Path(temp)
        runner = PublicShortFrameObservationRunner(
            Settings(app_root=root, data_dir=root / "data"),
            stream_resolver=_fake_resolver,
            frame_extractor=_fake_extractor,
        )

        result = runner.observe_url(
            url="https://www.youtube.com/watch?v=abc123",
            output_dir=root / "observed",
            sample_times_sec=(0.5,),
            keep_frames=True,
        )

        assert result.status == "observed"
        assert result.raw_frames_retained is True
        assert (root / "observed" / "raw_frames").exists()
        assert list((root / "observed" / "raw_frames").glob("*.jpg"))


def test_observe_benchmark_reads_top_candidates() -> None:
    with TemporaryDirectory(prefix="atnos-public-frame-benchmark-") as temp:
        root = Path(temp)
        benchmark = root / "benchmark_run.json"
        benchmark.write_text(
            json.dumps(
                {
                    "top_candidates": [
                        {
                            "url": "https://www.youtube.com/watch?v=abc123",
                            "video_id": "abc123",
                            "title": "AI cat story",
                            "channel": "Cat Channel",
                        }
                    ],
                    "visual_observations": [
                        {
                            "video_id": "abc123",
                            "thumbnail_path": None,
                            "contrast_score": 0.5,
                            "saturation_score": 0.3,
                            "visual_notes": ["mock visual note"],
                        }
                    ],
                    "replication_briefs": [
                        {
                            "brief_id": "benchmark-brief-01-ai_animal_microstory",
                            "source_pattern": "ai_animal_microstory",
                            "source_title": "AI cat story",
                            "source_channel": "Cat Channel",
                            "source_url": "https://www.youtube.com/watch?v=abc123",
                            "source_video_id": "abc123",
                            "hook_template": "Ten kot zatrzymał scroll.",
                            "forbidden_copying": ["Do not copy source pixels."],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        runner = PublicShortFrameObservationRunner(
            Settings(app_root=root, data_dir=root / "data"),
            stream_resolver=_fake_resolver,
            frame_extractor=_fake_extractor,
        )

        results = runner.observe_benchmark(
            benchmark_json_path=benchmark,
            output_dir=root / "benchmark-observed",
            top_limit=1,
            sample_times_sec=(0.5,),
        )

        assert len(results) == 1
        assert results[0].video_id == "abc123"
        assert (root / "benchmark-observed" / "public_player_observation_summary.json").exists()
        assert (root / "benchmark-observed" / "visual_recreation_plans_player_observed.json").exists()


def test_observe_url_returns_failed_package_when_public_stream_and_thumbnail_fail() -> None:
    with TemporaryDirectory(prefix="atnos-public-frame-fail-") as temp:
        root = Path(temp)

        def failing_resolver(_url: str) -> ResolvedPublicVideo:
            raise RuntimeError("blocked")

        runner = PublicShortFrameObservationRunner(
            Settings(app_root=root, data_dir=root / "data"),
            stream_resolver=failing_resolver,
            frame_extractor=_fake_extractor,
        )
        runner._download_public_thumbnail = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("thumbnail blocked"))

        result = runner.observe_url(
            url="https://www.youtube.com/watch?v=abc123",
            output_dir=root / "observed",
            source_video_id="abc123",
        )

        assert result.status == "failed"
        assert result.error == "RuntimeError: blocked"
        assert result.observation_package.status == "missing_reference"
        assert (root / "observed" / "public_player_observation.json").exists()


def test_observe_url_uses_thumbnail_fallback_when_public_stream_fails() -> None:
    with TemporaryDirectory(prefix="atnos-public-frame-thumbnail-") as temp:
        root = Path(temp)

        def failing_resolver(_url: str) -> ResolvedPublicVideo:
            raise RuntimeError("blocked")

        runner = PublicShortFrameObservationRunner(
            Settings(app_root=root, data_dir=root / "data"),
            stream_resolver=failing_resolver,
            frame_extractor=_fake_extractor,
        )

        def fake_thumbnail(*, video_id: str, output_path: Path) -> None:
            Image.new("RGB", (1280, 720), (220, 80, 40)).save(output_path)

        runner._download_public_thumbnail = fake_thumbnail

        result = runner.observe_url(
            url="https://www.youtube.com/watch?v=abc123",
            output_dir=root / "observed",
            source_video_id="abc123",
            source_title="AI cat story",
            source_channel="Cat Channel",
        )

        assert result.status == "thumbnail_fallback"
        assert result.observation_package.status == "observed"
        assert result.observation_package.source_kind == "public_thumbnail_reference"
        assert result.observation_package.frame_count == 1
        assert (root / "observed" / "public_thumbnail_reference.jpg").exists()


def test_resolve_public_stream_runs_ytdlp_with_timeout(monkeypatch) -> None:
    with TemporaryDirectory(prefix="atnos-public-frame-resolve-") as temp:
        root = Path(temp)
        captured: dict[str, object] = {}

        def fake_run(command, *, capture_output, text, timeout, check):
            captured["command"] = command
            captured["timeout"] = timeout
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "id": "abc123",
                        "title": "AI cat story",
                        "channel": "Cat Channel",
                        "duration": 42,
                        "url": "https://example.test/stream.mp4",
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )

        monkeypatch.setattr(observer_module.subprocess, "run", fake_run)
        runner = PublicShortFrameObservationRunner(Settings(app_root=root, data_dir=root / "data"))

        resolved = runner._resolve_public_stream("https://www.youtube.com/watch?v=abc123")

        assert resolved.video_id == "abc123"
        assert resolved.stream_url == "https://example.test/stream.mp4"
        assert captured["timeout"] == 35
        assert "--socket-timeout" in captured["command"]


def _fake_resolver(url: str) -> ResolvedPublicVideo:
    return ResolvedPublicVideo(
        source_url=url,
        stream_url="https://example.test/stream.mp4",
        video_id="abc123",
        title="Fake AI cat story",
        channel="Fake Channel",
        duration_s=42,
    )


def _fake_extractor(_stream_url: str, frames_dir: Path, sample_times_sec) -> list[tuple[Path, float]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[tuple[Path, float]] = []
    colors = ((220, 80, 40), (20, 40, 80), (240, 220, 90))
    for index, timestamp in enumerate(sample_times_sec, start=1):
        frame = frames_dir / f"frame_{index}.jpg"
        Image.new("RGB", (1280, 720), colors[(index - 1) % len(colors)]).save(frame)
        extracted.append((frame, float(timestamp)))
    return extracted
