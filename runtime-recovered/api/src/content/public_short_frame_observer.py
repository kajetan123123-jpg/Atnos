from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Sequence
from urllib.request import Request, urlopen

import imageio_ffmpeg

from atnos.core.config import Settings
from content.video_frame_observation import FrameObservationPackage, VideoFrameObservationService
from content.visual_recreation_planner import build_visual_recreation_plans, render_visual_recreation_plan_markdown


@dataclass(frozen=True, slots=True)
class ResolvedPublicVideo:
    source_url: str
    stream_url: str
    video_id: str
    title: str
    channel: str
    duration_s: int | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stream_url"] = "<redacted-public-stream-url>"
        return data


@dataclass(frozen=True, slots=True)
class PublicShortFrameObservationResult:
    source_url: str
    video_id: str
    title: str
    channel: str
    output_dir: str
    sample_times_sec: tuple[float, ...]
    raw_frames_retained: bool
    extracted_frame_count: int
    observation_package: FrameObservationPackage
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observation_package"] = self.observation_package.to_dict()
        return payload


StreamResolver = Callable[[str], ResolvedPublicVideo]
FrameExtractor = Callable[[str, Path, Sequence[float]], list[tuple[Path, float]]]


class PublicShortFrameObservationRunner:
    """Samples a few public-player frames for analysis-only visual learning.

    The runner is intentionally separate from the normal benchmark. It can touch
    public video streams, so it must be invoked explicitly and defaults to
    deleting raw frames after writing descriptive observations.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        stream_resolver: StreamResolver | None = None,
        frame_extractor: FrameExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.stream_resolver = stream_resolver or self._resolve_public_stream
        self.frame_extractor = frame_extractor or self._extract_frames_with_ffmpeg
        self.frame_observer = VideoFrameObservationService(settings)

    def observe_url(
        self,
        *,
        url: str,
        output_dir: str | Path,
        sample_times_sec: Sequence[float] = (0.5, 2.0, 5.0, 10.0, 20.0),
        keep_frames: bool = False,
        source_title: str | None = None,
        source_channel: str | None = None,
        source_video_id: str | None = None,
    ) -> PublicShortFrameObservationResult:
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        frames_dir = output_path / "raw_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        sample_times = tuple(float(item) for item in sample_times_sec)
        try:
            resolved = self.stream_resolver(url)
            title = source_title or resolved.title
            channel = source_channel or resolved.channel
            video_id = source_video_id or resolved.video_id
            extracted = self.frame_extractor(resolved.stream_url, frames_dir, sample_times)
            frame_paths = [path for path, _timestamp in extracted if path.exists()]
            if frame_paths:
                package = self.frame_observer.observe_image_sequence(
                    frame_paths=frame_paths,
                    output_dir=output_path / "analysis",
                    source_ref=video_id,
                    source_title=title,
                    source_channel=channel,
                    source_kind="public_player_sample_analysis_only",
                )
                status = "observed"
                error = None
            else:
                package, fallback_error = self._observe_thumbnail_fallback(
                    video_id=video_id,
                    title=title,
                    channel=channel,
                    output_path=output_path,
                )
                if package is not None:
                    status = "thumbnail_fallback"
                    error = "no frames extracted; used public thumbnail reference"
                else:
                    package = self.frame_observer.missing_reference(
                        output_dir=output_path / "analysis",
                        source_ref=video_id,
                        source_title=title,
                        source_channel=channel,
                        reason=f"Public player frame extraction produced no frames. Thumbnail fallback failed: {fallback_error}",
                    )
                    status = "no_frames_extracted"
                    error = "no frames extracted"
        except Exception as exc:
            video_id = source_video_id or _video_id_from_url(url)
            title = source_title or ""
            channel = source_channel or ""
            package, fallback_error = self._observe_thumbnail_fallback(
                video_id=video_id,
                title=title,
                channel=channel,
                output_path=output_path,
            )
            if package is not None:
                status = "thumbnail_fallback"
                error = f"{type(exc).__name__}: {exc}; used public thumbnail reference"
            else:
                package = self.frame_observer.missing_reference(
                    output_dir=output_path / "analysis",
                    source_ref=video_id,
                    source_title=title,
                    source_channel=channel,
                    reason=f"Public player observation failed: {type(exc).__name__}: {exc}. Thumbnail fallback failed: {fallback_error}",
                )
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
            extracted = []

        if not keep_frames and frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)

        result = PublicShortFrameObservationResult(
            source_url=url,
            video_id=video_id,
            title=title,
            channel=channel,
            output_dir=str(output_path),
            sample_times_sec=sample_times,
            raw_frames_retained=bool(keep_frames),
            extracted_frame_count=len(extracted),
            observation_package=package,
            status=status,
            error=error,
        )
        self._write_result(result, output_path)
        return result

    def _observe_thumbnail_fallback(
        self,
        *,
        video_id: str,
        title: str,
        channel: str,
        output_path: Path,
    ) -> tuple[FrameObservationPackage | None, str | None]:
        thumbnail_path = output_path / "public_thumbnail_reference.jpg"
        try:
            self._download_public_thumbnail(video_id=video_id, output_path=thumbnail_path)
            package = self.frame_observer.observe_image_reference(
                image_path=thumbnail_path,
                output_dir=output_path / "analysis",
                source_ref=video_id,
                source_title=title,
                source_channel=channel,
                source_kind="public_thumbnail_reference",
            )
            return package, None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def _download_public_thumbnail(self, *, video_id: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        last_error = ""
        for url in _thumbnail_urls(video_id):
            try:
                request = Request(url, headers={"User-Agent": "Atnos format-learning/1.0"})
                with urlopen(request, timeout=12) as response:
                    data = response.read()
                if len(data) <= 1024:
                    raise RuntimeError("thumbnail response is too small")
                output_path.write_bytes(data)
                return
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        raise RuntimeError(last_error or "no thumbnail URL succeeded")

    def observe_benchmark(
        self,
        *,
        benchmark_json_path: str | Path,
        output_dir: str | Path | None = None,
        top_limit: int = 1,
        sample_times_sec: Sequence[float] = (0.5, 2.0, 5.0, 10.0, 20.0),
        keep_frames: bool = False,
    ) -> list[PublicShortFrameObservationResult]:
        benchmark_path = Path(benchmark_json_path).resolve()
        payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
        candidates = payload.get("top_candidates")
        if not isinstance(candidates, list):
            raise ValueError(f"Benchmark payload has no top_candidates list: {benchmark_path}")

        base_dir = Path(output_dir).resolve() if output_dir else benchmark_path.parent / "public_player_observations"
        base_dir.mkdir(parents=True, exist_ok=True)
        results: list[PublicShortFrameObservationResult] = []
        for raw in candidates[: max(0, int(top_limit))]:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            video_id = str(raw.get("video_id") or _video_id_from_url(url)).strip()
            result = self.observe_url(
                url=url,
                output_dir=base_dir / _safe_slug(video_id),
                sample_times_sec=sample_times_sec,
                keep_frames=keep_frames,
                source_title=str(raw.get("title") or ""),
                source_channel=str(raw.get("channel") or ""),
                source_video_id=video_id,
            )
            results.append(result)

        summary_path = base_dir / "public_player_observation_summary.json"
        summary_path.write_text(
            json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_player_observed_visual_plans(payload=payload, results=results, output_dir=base_dir)
        return results

    def _resolve_public_stream(self, url: str) -> ResolvedPublicVideo:
        # Run yt-dlp out-of-process so an unavailable public Short cannot hang
        # the whole learning batch.
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            "--no-playlist",
            "--socket-timeout",
            "10",
            "--retries",
            "1",
            "--extractor-retries",
            "1",
            "--format",
            "best[height<=720][ext=mp4]/best[height<=720]/best",
            url,
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=35, check=False)
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "yt-dlp failed").strip()
            raise RuntimeError(reason)
        try:
            info = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("yt-dlp returned invalid JSON") from exc
        if not isinstance(info, dict):
            raise RuntimeError("yt-dlp returned an invalid info payload")

        stream_url = str(info.get("url") or "").strip()
        if not stream_url:
            stream_url = _select_stream_url_from_formats(info.get("formats"))
        if not stream_url:
            raise RuntimeError("yt-dlp did not return a playable stream URL")

        return ResolvedPublicVideo(
            source_url=url,
            stream_url=stream_url,
            video_id=str(info.get("id") or _video_id_from_url(url)),
            title=str(info.get("title") or ""),
            channel=str(info.get("channel") or info.get("uploader") or ""),
            duration_s=_safe_optional_int(info.get("duration")),
        )

    def _extract_frames_with_ffmpeg(
        self,
        stream_url: str,
        frames_dir: Path,
        sample_times_sec: Sequence[float],
    ) -> list[tuple[Path, float]]:
        ffmpeg = self._resolve_ffmpeg()
        extracted: list[tuple[Path, float]] = []
        for index, timestamp in enumerate(sample_times_sec, start=1):
            frame_path = frames_dir / f"public_frame_{index:02d}_{float(timestamp):g}s.jpg"
            command = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{float(timestamp):.3f}",
                "-i",
                stream_url,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(frame_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
            if result.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 0:
                extracted.append((frame_path, float(timestamp)))
        return extracted

    def _resolve_ffmpeg(self) -> str:
        configured = (self.settings.ffmpeg_exe or "").strip()
        if configured:
            return configured
        local = Path("E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe")
        if local.exists():
            return str(local)
        return imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def _write_result(result: PublicShortFrameObservationResult, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "public_player_observation.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_player_observed_visual_plans(
        *,
        payload: dict[str, Any],
        results: list[PublicShortFrameObservationResult],
        output_dir: Path,
    ) -> None:
        briefs = payload.get("replication_briefs")
        if not isinstance(briefs, list) or not briefs:
            return
        visual_observations = payload.get("visual_observations")
        plans = build_visual_recreation_plans(
            briefs=briefs,
            observations=visual_observations if isinstance(visual_observations, list) else [],
            frame_observations=[result.observation_package.to_dict() for result in results],
        )
        plans_path = output_dir / "visual_recreation_plans_player_observed.json"
        plans_path.write_text(
            json.dumps([plan.to_dict() for plan in plans], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        plan_dir = output_dir / "visual_recreation_plans_player_observed"
        plan_dir.mkdir(parents=True, exist_ok=True)
        for plan in plans:
            (plan_dir / f"{_safe_slug(plan.plan_id)}.md").write_text(
                render_visual_recreation_plan_markdown(plan),
                encoding="utf-8",
            )


def _select_stream_url_from_formats(formats: object) -> str:
    if not isinstance(formats, list):
        return ""
    candidates: list[tuple[int, int, str]] = []
    for item in formats:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        vcodec = str(item.get("vcodec") or "")
        if vcodec == "none":
            continue
        height = _safe_optional_int(item.get("height")) or 0
        ext_bonus = 1 if str(item.get("ext") or "") == "mp4" else 0
        if height <= 720:
            candidates.append((ext_bonus, height, url))
    if not candidates:
        return ""
    return sorted(candidates, reverse=True)[0][2]


def _video_id_from_url(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{6,})", url)
    return match.group(1) if match else _safe_slug(url)


def _thumbnail_urls(video_id: str) -> tuple[str, ...]:
    safe_id = str(video_id).strip()
    return (
        f"https://i.ytimg.com/vi/{safe_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{safe_id}/sddefault.jpg",
        f"https://i.ytimg.com/vi/{safe_id}/hqdefault.jpg",
    )


def _safe_slug(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value).strip()).strip("-")
    return safe[:80] or "unknown"


def _safe_optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return max(0, int(value))
    except (TypeError, ValueError):
        return None
