from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any

from atnos.core.config import Settings
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe


MIN_STRICT_UPLOAD_BYTES = 64 * 1024
TEMP_FILE_MARKERS = ("TEMP_MPY", "_wvf_snd", ".tmp", "placeholder", "draft", "partial")


@dataclass(frozen=True, slots=True)
class UploadVideoValidation:
    path: Path
    valid: bool
    issues: tuple[str, ...]
    strict_probe: bool
    probe: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "valid": self.valid,
            "issues": list(self.issues),
            "strict_probe": self.strict_probe,
            "probe": self.probe,
        }


def validate_upload_video_file(
    path: str | Path,
    *,
    settings: Settings | None = None,
    require_run_artifacts: bool = False,
    require_audio: bool = True,
    strict_probe: bool | None = None,
) -> UploadVideoValidation:
    """Sprawdź, czy MP4 jest bezpiecznym kandydatem do prywatnego uploadu YouTube."""
    video_path = Path(path).resolve()
    strict = _strict_probe(settings=settings) if strict_probe is None else strict_probe
    issues: list[str] = []
    probe: dict[str, Any] = {}

    if not video_path.exists() or not video_path.is_file():
        issues.append("video_missing")
        return _result(video_path, issues, strict, probe)
    if video_path.suffix.lower() != ".mp4":
        issues.append("not_mp4")
    if _looks_temporary(video_path):
        issues.append("temporary_or_intermediate_video_name")

    size = video_path.stat().st_size
    min_size = MIN_STRICT_UPLOAD_BYTES if strict else 1
    if size < min_size:
        issues.append(f"video_too_small:{size}_bytes")

    if require_run_artifacts and not _has_upload_gate_artifact(video_path.parent):
        issues.append("missing_upload_gate_artifact")

    if strict:
        probe = _probe_upload_media(video_path)
        if not probe.get("available"):
            issues.append(f"media_probe_failed:{probe.get('reason') or 'unknown'}")
        if not probe.get("video_codec"):
            issues.append("no_video_stream")
        if require_audio and not probe.get("audio_codec"):
            issues.append("no_audio_stream")
        width = int(_float(probe.get("width")))
        height = int(_float(probe.get("height")))
        duration = _float(probe.get("duration_s"))
        if width <= 0 or height <= 0:
            issues.append("missing_video_dimensions")
        elif height <= width:
            issues.append(f"not_vertical:{width}x{height}")
        elif abs((width / height) - (9 / 16)) > 0.08:
            issues.append(f"not_9_16:{width}x{height}")
        if duration <= 0:
            issues.append("duration_missing_or_zero")
        elif duration > 60:
            issues.append(f"duration_over_60s:{duration:.2f}")

    return _result(video_path, issues, strict, probe)


def assert_upload_video_file(
    path: str | Path,
    *,
    settings: Settings | None = None,
    require_run_artifacts: bool = False,
    require_audio: bool = True,
    strict_probe: bool | None = None,
) -> Path:
    validation = validate_upload_video_file(
        path,
        settings=settings,
        require_run_artifacts=require_run_artifacts,
        require_audio=require_audio,
        strict_probe=strict_probe,
    )
    if not validation.valid:
        raise ValueError("Video is not upload-ready: " + "; ".join(validation.issues))
    return validation.path


def _result(path: Path, issues: list[str], strict: bool, probe: dict[str, Any]) -> UploadVideoValidation:
    return UploadVideoValidation(path=path, valid=not issues, issues=tuple(dict.fromkeys(issues)), strict_probe=strict, probe=probe)


def _strict_probe(*, settings: Settings | None) -> bool:
    return settings is None or settings.app_env != "test"


def _looks_temporary(path: Path) -> bool:
    normalized = f"{path.name} {path.stem}".casefold()
    return any(marker.casefold() in normalized for marker in TEMP_FILE_MARKERS)


def _has_upload_gate_artifact(run_dir: Path) -> bool:
    for name in ("render_qa.json", "qa_report.json", "youtube_upload_package.json", "youtube_private_upload_package.json"):
        path = run_dir / name
        if path.exists() and path.stat().st_size > 0:
            return True
    return False


def _probe_upload_media(path: Path) -> dict[str, Any]:
    ffprobe = resolve_ffprobe(resolve_ffmpeg())
    if not ffprobe:
        return {"available": False, "reason": "ffprobe_unavailable"}
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": str(exc)}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"available": False, "reason": proc.stderr.strip()[-500:] or f"ffprobe_exit_{proc.returncode}"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"ffprobe_json_error:{exc}"}
    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    return {
        "available": True,
        "duration_s": _float(fmt.get("duration") or video.get("duration") or audio.get("duration")),
        "size_bytes": int(_float(fmt.get("size"))),
        "width": int(_float(video.get("width"))),
        "height": int(_float(video.get("height"))),
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audio.get("codec_name") or ""),
    }


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
