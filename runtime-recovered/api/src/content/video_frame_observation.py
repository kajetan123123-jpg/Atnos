from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import subprocess
from typing import Any, Iterable, Sequence

import imageio_ffmpeg

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class ObservedFrame:
    frame_id: str
    source_path: str
    timestamp_sec: float | None
    width: int | None
    height: int | None
    aspect_ratio: float | None
    brightness_score: float | None
    contrast_score: float | None
    saturation_score: float | None
    dominant_palette: tuple[str, ...]
    composition_notes: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FrameObservationPackage:
    observation_id: str
    source_ref: str
    source_title: str
    source_channel: str
    source_kind: str
    source_path: str | None
    output_dir: str
    observation_mode: str
    frame_count: int
    frames: tuple[ObservedFrame, ...]
    aggregate_notes: tuple[str, ...]
    motion_pacing_hypothesis: str
    safe_learning_summary: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["frames"] = [frame.to_dict() for frame in self.frames]
        return payload


class VideoFrameObservationService:
    """Extracts format-level visual signals without copying source media into production.

    Public YouTube runs should prefer thumbnail/reference observation. Full frame
    extraction is intended for local files the user owns or temporary browser QA
    captures, not for reusing source footage.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def observe_image_reference(
        self,
        *,
        image_path: str | Path,
        output_dir: str | Path,
        source_ref: str,
        source_title: str,
        source_channel: str,
        source_kind: str = "public_thumbnail_reference",
    ) -> FrameObservationPackage:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        image = Path(image_path).resolve()
        frame = self._analyze_frame(
            frame_id="thumbnail_reference",
            frame_path=image,
            timestamp_sec=None,
        )
        package = self._build_package(
            source_ref=source_ref,
            source_title=source_title,
            source_channel=source_channel,
            source_kind=source_kind,
            source_path=str(image),
            output_dir=output_path,
            observation_mode="single_reference_image",
            frames=(frame,),
            status="observed",
        )
        self._write_package(package, output_path)
        return package

    def observe_image_sequence(
        self,
        *,
        frame_paths: Iterable[str | Path],
        output_dir: str | Path,
        source_ref: str,
        source_title: str = "",
        source_channel: str = "",
        source_kind: str = "local_frame_sequence",
    ) -> FrameObservationPackage:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        frames = tuple(
            self._analyze_frame(
                frame_id=f"frame_{index:02d}",
                frame_path=Path(path).resolve(),
                timestamp_sec=None,
            )
            for index, path in enumerate(frame_paths, start=1)
        )
        package = self._build_package(
            source_ref=source_ref,
            source_title=source_title,
            source_channel=source_channel,
            source_kind=source_kind,
            source_path=None,
            output_dir=output_path,
            observation_mode="image_sequence",
            frames=frames,
            status="observed" if frames else "empty",
        )
        self._write_package(package, output_path)
        return package

    def observe_local_video(
        self,
        *,
        video_path: str | Path,
        output_dir: str | Path,
        source_ref: str,
        source_title: str = "",
        source_channel: str = "",
        sample_times_sec: Sequence[float] = (0.5, 2.0, 5.0, 10.0, 20.0, 35.0),
    ) -> FrameObservationPackage:
        output_path = Path(output_dir)
        frames_dir = output_path / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        video = Path(video_path).resolve()
        if not video.exists():
            return self.missing_reference(
                output_dir=output_path,
                source_ref=source_ref,
                source_title=source_title,
                source_channel=source_channel,
                reason=f"local video does not exist: {video}",
            )

        ffmpeg = self._resolve_ffmpeg()
        extracted: list[tuple[Path, float]] = []
        for index, timestamp in enumerate(sample_times_sec, start=1):
            frame_path = frames_dir / f"frame_{index:02d}_{timestamp:g}s.jpg"
            command = [
                ffmpeg,
                "-y",
                "-ss",
                f"{float(timestamp):.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(frame_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            if result.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 0:
                extracted.append((frame_path, float(timestamp)))

        frames = tuple(
            self._analyze_frame(frame_id=f"frame_{index:02d}", frame_path=path, timestamp_sec=timestamp)
            for index, (path, timestamp) in enumerate(extracted, start=1)
        )
        package = self._build_package(
            source_ref=source_ref,
            source_title=source_title,
            source_channel=source_channel,
            source_kind="local_video",
            source_path=str(video),
            output_dir=output_path,
            observation_mode="sampled_video_frames",
            frames=frames,
            status="observed" if frames else "no_frames_extracted",
        )
        self._write_package(package, output_path)
        return package

    def missing_reference(
        self,
        *,
        output_dir: str | Path,
        source_ref: str,
        source_title: str = "",
        source_channel: str = "",
        reason: str = "no frame or thumbnail reference available",
    ) -> FrameObservationPackage:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        package = FrameObservationPackage(
            observation_id=f"frame-observation-{_safe_slug(source_ref)}",
            source_ref=source_ref,
            source_title=source_title,
            source_channel=source_channel,
            source_kind="missing_reference",
            source_path=None,
            output_dir=str(output_path),
            observation_mode="none",
            frame_count=0,
            frames=(),
            aggregate_notes=(reason,),
            motion_pacing_hypothesis="No visual frame evidence available. Use metadata and pattern rules only.",
            safe_learning_summary="No frame-level learning was performed for this source.",
            status="missing_reference",
        )
        self._write_package(package, output_path)
        return package

    def _analyze_frame(self, *, frame_id: str, frame_path: Path, timestamp_sec: float | None) -> ObservedFrame:
        try:
            from PIL import Image, ImageStat
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Pillow is required for frame observation.") from exc

        with Image.open(frame_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            stat = ImageStat.Stat(rgb)
            brightness = round(min(sum(stat.mean) / 3.0 / 255.0, 1.0), 3)
            contrast = round(min(sum(stat.stddev) / 3.0 / 80.0, 1.0), 3)
            saturation = round(min(ImageStat.Stat(rgb.convert("HSV")).mean[1] / 255.0, 1.0), 3)
            palette = _dominant_palette(rgb)

        aspect_ratio = round(width / height, 3) if height else None
        notes = _composition_notes(
            width=width,
            height=height,
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
        )
        return ObservedFrame(
            frame_id=frame_id,
            source_path=str(frame_path),
            timestamp_sec=timestamp_sec,
            width=width,
            height=height,
            aspect_ratio=aspect_ratio,
            brightness_score=brightness,
            contrast_score=contrast,
            saturation_score=saturation,
            dominant_palette=palette,
            composition_notes=tuple(notes),
            status="observed",
        )

    def _build_package(
        self,
        *,
        source_ref: str,
        source_title: str,
        source_channel: str,
        source_kind: str,
        source_path: str | None,
        output_dir: Path,
        observation_mode: str,
        frames: tuple[ObservedFrame, ...],
        status: str,
    ) -> FrameObservationPackage:
        notes = _aggregate_notes(frames)
        return FrameObservationPackage(
            observation_id=f"frame-observation-{_safe_slug(source_ref)}",
            source_ref=source_ref,
            source_title=source_title,
            source_channel=source_channel,
            source_kind=source_kind,
            source_path=source_path,
            output_dir=str(output_dir),
            observation_mode=observation_mode,
            frame_count=len(frames),
            frames=frames,
            aggregate_notes=tuple(notes),
            motion_pacing_hypothesis=_motion_pacing_hypothesis(frames, observation_mode=observation_mode),
            safe_learning_summary=_safe_learning_summary(frames, notes=notes, observation_mode=observation_mode),
            status=status,
        )

    def _write_package(self, package: FrameObservationPackage, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "frame_observation.json").write_text(
            json.dumps(package.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _resolve_ffmpeg(self) -> str:
        configured = (self.settings.ffmpeg_exe or "").strip()
        if configured:
            return configured
        return imageio_ffmpeg.get_ffmpeg_exe()


def _dominant_palette(image) -> tuple[str, ...]:
    small = image.resize((64, 64))
    quantized = small.quantize(colors=5)
    palette = quantized.getpalette() or []
    colors = quantized.getcolors(maxcolors=4096) or []
    result: list[str] = []
    for _count, index in sorted(colors, reverse=True)[:5]:
        offset = int(index) * 3
        if offset + 2 >= len(palette):
            continue
        result.append(f"#{palette[offset]:02x}{palette[offset + 1]:02x}{palette[offset + 2]:02x}")
    return tuple(dict.fromkeys(result))


def _composition_notes(
    *,
    width: int,
    height: int,
    brightness: float,
    contrast: float,
    saturation: float,
) -> list[str]:
    notes: list[str] = []
    if height and width > height:
        notes.append("Source frame is landscape; recreate as vertical 9:16 instead of copying the crop.")
    elif width and height > width:
        notes.append("Source frame is already vertical; preserve vertical readability, not exact pixels.")
    if contrast >= 0.55:
        notes.append("High contrast: good feed readability.")
    elif contrast < 0.25:
        notes.append("Low contrast: strengthen foreground/background separation in recreation.")
    if saturation >= 0.35:
        notes.append("Saturated palette: useful for scroll-stopping emotional style.")
    elif saturation < 0.12:
        notes.append("Low saturation: use typography or shape contrast to carry attention.")
    if brightness < 0.24:
        notes.append("Dark frame: keep captions bright and large.")
    elif brightness > 0.78:
        notes.append("Bright frame: add outline/shadow for captions.")
    return notes or ["Frame has neutral visual signal; rely on story beat and captions."]


def _aggregate_notes(frames: tuple[ObservedFrame, ...]) -> list[str]:
    if not frames:
        return ["No frame-level visual evidence was available."]
    notes: list[str] = []
    avg_contrast = sum(frame.contrast_score or 0.0 for frame in frames) / len(frames)
    avg_saturation = sum(frame.saturation_score or 0.0 for frame in frames) / len(frames)
    avg_brightness = sum(frame.brightness_score or 0.0 for frame in frames) / len(frames)
    notes.append(f"Average contrast={avg_contrast:.3f}, saturation={avg_saturation:.3f}, brightness={avg_brightness:.3f}.")
    if any(frame.width and frame.height and frame.width > frame.height for frame in frames):
        notes.append("At least one reference is landscape; translate composition into vertical Shorts framing.")
    if avg_contrast >= 0.5:
        notes.append("Use high foreground/background separation.")
    if avg_saturation >= 0.35:
        notes.append("Keep warm/saturated color energy, but change exact palette and subject identity.")
    return notes


def _motion_pacing_hypothesis(frames: tuple[ObservedFrame, ...], *, observation_mode: str) -> str:
    if not frames:
        return "No pacing signal available."
    if observation_mode == "single_reference_image":
        return "Thumbnail-only observation: infer first-frame readability, not actual motion timing."
    if len(frames) <= 2:
        return "Limited frame sequence: use 2-4 second cuts and verify with final QA."
    return "Multiple frames observed: recreate the rhythm as distinct beat changes, not as reused footage."


def _safe_learning_summary(
    frames: tuple[ObservedFrame, ...],
    *,
    notes: Sequence[str],
    observation_mode: str,
) -> str:
    if not frames:
        return "No visual source evidence. Use pattern-level benchmark only."
    first = frames[0]
    palette = ", ".join(first.dominant_palette[:4]) or "unknown palette"
    return (
        f"Learn format-level visual cues only from {observation_mode}: aspect={first.aspect_ratio}, "
        f"contrast={first.contrast_score}, saturation={first.saturation_score}, palette={palette}. "
        f"{' '.join(notes[:2])} Do not copy source pixels, exact characters, outfits, composition, watermark, or branding."
    )


def _safe_slug(value: str) -> str:
    import re

    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value).strip()).strip("-")
    return safe[:80] or "unknown"
