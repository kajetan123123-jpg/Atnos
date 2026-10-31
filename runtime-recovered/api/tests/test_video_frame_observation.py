from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from atnos.core.config import Settings
from content.video_frame_observation import VideoFrameObservationService


def test_observe_image_reference_writes_frame_observation_json() -> None:
    with TemporaryDirectory(prefix="atnos-frame-observation-") as temp:
        root = Path(temp)
        image_path = root / "thumbnail.jpg"
        Image.new("RGB", (1280, 720), (220, 80, 40)).save(image_path)

        package = VideoFrameObservationService(Settings(app_root=root, data_dir=root / "data")).observe_image_reference(
            image_path=image_path,
            output_dir=root / "observed",
            source_ref="video-1",
            source_title="AI cat story",
            source_channel="Cat Channel",
        )

        assert package.status == "observed"
        assert package.source_kind == "public_thumbnail_reference"
        assert package.frame_count == 1
        assert package.frames[0].width == 1280
        assert package.frames[0].height == 720
        assert package.frames[0].dominant_palette
        assert "Do not copy source pixels" in package.safe_learning_summary
        assert (root / "observed" / "frame_observation.json").exists()


def test_observe_image_sequence_aggregates_multiple_frames() -> None:
    with TemporaryDirectory(prefix="atnos-frame-sequence-") as temp:
        root = Path(temp)
        first = root / "first.jpg"
        second = root / "second.jpg"
        Image.new("RGB", (1080, 1920), (10, 10, 10)).save(first)
        Image.new("RGB", (1080, 1920), (250, 220, 40)).save(second)

        package = VideoFrameObservationService(Settings(app_root=root, data_dir=root / "data")).observe_image_sequence(
            frame_paths=(first, second),
            output_dir=root / "observed",
            source_ref="local-sequence",
            source_title="Generated QA sequence",
            source_channel="Atnos",
        )

        assert package.status == "observed"
        assert package.frame_count == 2
        assert package.observation_mode == "image_sequence"
        assert "Limited frame sequence" in package.motion_pacing_hypothesis
        assert any("Average contrast" in note for note in package.aggregate_notes)


def test_missing_reference_is_explicit_and_non_crashing() -> None:
    with TemporaryDirectory(prefix="atnos-frame-missing-") as temp:
        root = Path(temp)

        package = VideoFrameObservationService(Settings(app_root=root, data_dir=root / "data")).missing_reference(
            output_dir=root / "observed",
            source_ref="missing-video",
            reason="test missing thumbnail",
        )

        assert package.status == "missing_reference"
        assert package.frame_count == 0
        assert "test missing thumbnail" in package.aggregate_notes
        assert (root / "observed" / "frame_observation.json").exists()
