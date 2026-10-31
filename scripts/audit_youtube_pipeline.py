from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "runtime-recovered" / "api"
API_SRC = API_ROOT / "src"
REPORT_DATE = datetime.now(UTC).strftime("%Y%m%d")
REPORT_PATH = PROJECT_ROOT / f"youtube_pipeline_audit_{REPORT_DATE}.md"
ARTIFACT_DIR = PROJECT_ROOT / f"youtube_pipeline_audit_{REPORT_DATE}_artifacts"

MEDIA_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi"}
THUMB_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "tool-caches",
    "audit_garbage_20260605_artifacts",
    "obsidian_memory_audit_20260605_artifacts",
    "desktop_browser_audit_20260605_artifacts",
}


@dataclass
class MediaCandidate:
    path: str
    size_bytes: int
    modified_at: str
    valid_size: bool


@dataclass
class VideoAnalysis:
    path: str
    status: str
    score: int = 0
    duration_sec: float | None = None
    resolution: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate_kbps: int | None = None
    score_breakdown: dict[str, int] | None = None
    thumbnail: dict[str, Any] | None = None
    audio_quality: dict[str, Any] | None = None
    error: str | None = None


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    if str(API_SRC) not in sys.path:
        sys.path.insert(0, str(API_SRC))

    ffmpeg = resolve_ffmpeg()
    ffprobe = resolve_ffprobe(ffmpeg)
    candidates = find_media_candidates(PROJECT_ROOT)
    valid_candidates = [item for item in candidates if item.valid_size]
    analyzed = analyze_recent_videos(valid_candidates[:10], ffprobe=ffprobe, ffmpeg=ffmpeg)
    thumb_results = audit_all_thumbnails(PROJECT_ROOT)
    youtube_scan = scan_youtube_integration(PROJECT_ROOT)
    credential_status = scan_credentials(PROJECT_ROOT)
    google_status = check_google_libraries()
    dry_run = run_upload_dry_run(valid_candidates[:1], thumb_results)
    connectivity = build_connectivity_matrix()

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "media": {
            "count": len(candidates),
            "valid_sized_count": len(valid_candidates),
            "tiny_or_invalid_count": len(candidates) - len(valid_candidates),
            "total_size_bytes": sum(item.size_bytes for item in candidates),
            "candidates": [asdict(item) for item in candidates[:250]],
        },
        "analyzed_videos": [asdict(item) for item in analyzed],
        "thumbnails": thumb_results,
        "youtube_scan": youtube_scan,
        "credential_status": credential_status,
        "google_libraries": google_status,
        "upload_dry_run": dry_run,
        "connectivity": connectivity,
    }
    write_json(ARTIFACT_DIR / "youtube_pipeline_audit.json", payload)
    write_report(payload)
    print(str(REPORT_PATH))
    return 0


def resolve_ffmpeg() -> str | None:
    try:
        from atnos.production.media_tools import resolve_ffmpeg as project_resolve

        resolved = project_resolve()
        if resolved:
            return resolved
    except Exception:
        pass
    return shutil.which("ffmpeg")


def resolve_ffprobe(ffmpeg_path: str | None) -> str | None:
    try:
        from atnos.production.media_tools import resolve_ffprobe as project_resolve

        resolved = project_resolve(ffmpeg_path)
        if resolved:
            return resolved
    except Exception:
        pass
    path = shutil.which("ffprobe")
    if path:
        return path
    if ffmpeg_path:
        sibling = Path(ffmpeg_path).with_name("ffprobe.exe")
        if sibling.exists():
            return str(sibling)
    return None


def find_media_candidates(root: Path) -> list[MediaCandidate]:
    result: list[MediaCandidate] = []
    for path in safe_rglob(root, MEDIA_EXTENSIONS):
        try:
            stat = path.stat()
        except OSError:
            continue
        result.append(
            MediaCandidate(
                path=str(path),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                valid_size=stat.st_size > 1024,
            )
        )
    return sorted(result, key=lambda item: item.modified_at, reverse=True)


def safe_rglob(root: Path, extensions: set[str]) -> list[Path]:
    found: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for child in children:
            if child.is_dir():
                if child.name in EXCLUDED_DIR_NAMES:
                    continue
                stack.append(child)
            elif child.suffix.lower() in extensions:
                found.append(child)
    return found


def analyze_recent_videos(candidates: list[MediaCandidate], *, ffprobe: str | None, ffmpeg: str | None) -> list[VideoAnalysis]:
    analyses: list[VideoAnalysis] = []
    for candidate in candidates:
        path = Path(candidate.path)
        analysis = analyze_video(path, ffprobe=ffprobe)
        analysis.thumbnail = audit_thumbnail_for_video(path)
        if analysis.audio_codec and analysis.audio_codec != "none":
            analysis.audio_quality = audit_audio_quality(path, ffmpeg=ffmpeg)
        else:
            analysis.audio_quality = {"status": "no_audio"}
        analyses.append(analysis)
    return analyses


def analyze_video(path: Path, *, ffprobe: str | None) -> VideoAnalysis:
    if not ffprobe:
        return VideoAnalysis(path=str(path), status="ffprobe_missing", error="ffprobe_not_available")
    completed = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return VideoAnalysis(path=str(path), status="ffprobe_failed", error=completed.stderr.strip()[:500])
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return VideoAnalysis(path=str(path), status="ffprobe_json_failed", error=str(exc))
    streams = data.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video_stream:
        return VideoAnalysis(path=str(path), status="no_video_stream", error="No video stream found")

    duration = _safe_float((data.get("format") or {}).get("duration"))
    width = _safe_int(video_stream.get("width"))
    height = _safe_int(video_stream.get("height"))
    fps = _parse_fps(str(video_stream.get("r_frame_rate") or "0/1"))
    video_codec = str(video_stream.get("codec_name") or "unknown")
    audio_codec = str(audio_stream.get("codec_name") or "none") if audio_stream else "none"
    bitrate_kbps = int(_safe_int((data.get("format") or {}).get("bit_rate")) / 1000)
    breakdown = score_video(
        duration_sec=duration,
        width=width,
        height=height,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )
    return VideoAnalysis(
        path=str(path),
        status="analyzed",
        score=sum(breakdown.values()),
        duration_sec=round(duration, 2),
        resolution=f"{width}x{height}",
        width=width,
        height=height,
        fps=round(fps, 2),
        video_codec=video_codec,
        audio_codec=audio_codec,
        bitrate_kbps=bitrate_kbps,
        score_breakdown=breakdown,
    )


def score_video(
    *,
    duration_sec: float,
    width: int,
    height: int,
    fps: float,
    video_codec: str,
    audio_codec: str,
) -> dict[str, int]:
    duration_points = 20 if duration_sec <= 60 else 10 if duration_sec <= 90 else 0
    if width == 1080 and height == 1920:
        resolution_points = 20
    elif width == 720 and height == 1280:
        resolution_points = 15
    elif height > width:
        resolution_points = 10
    else:
        resolution_points = 0
    ratio = width / height if height else 0
    target = 9 / 16
    aspect_points = 15 if abs(ratio - target) <= 0.02 else 8 if abs(ratio - target) <= 0.08 else 0
    fps_points = 15 if fps >= 59 else 12 if fps >= 29 else 8 if fps >= 23.5 else 0
    codec_points = 10 if video_codec.lower() in {"h264", "hevc", "h265"} else 5 if video_codec else 0
    audio_present_points = 10 if audio_codec and audio_codec != "none" else 0
    if audio_codec == "aac":
        audio_codec_points = 10
    elif audio_codec in {"mp3", "mp3float"}:
        audio_codec_points = 8
    elif audio_codec and audio_codec != "none":
        audio_codec_points = 5
    else:
        audio_codec_points = 0
    return {
        "duration": duration_points,
        "resolution": resolution_points,
        "aspect_ratio": aspect_points,
        "fps": fps_points,
        "video_codec": codec_points,
        "audio_present": audio_present_points,
        "audio_codec": audio_codec_points,
    }


def audit_thumbnail_for_video(video_path: Path) -> dict[str, Any]:
    candidates = [video_path.with_suffix(ext) for ext in THUMB_EXTENSIONS]
    candidates.extend(video_path.parent / f"thumbnail{ext}" for ext in THUMB_EXTENSIONS)
    for candidate in candidates:
        if candidate.exists():
            return validate_thumbnail(candidate)
    return {"status": "missing", "path": None, "valid": False, "issues": ["thumbnail_missing"]}


def audit_all_thumbnails(root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in safe_rglob(root, THUMB_EXTENSIONS):
        same_stem_video = any(path.with_suffix(ext).exists() for ext in MEDIA_EXTENSIONS)
        if path.name.lower().startswith("thumbnail") or same_stem_video:
            results.append(validate_thumbnail(path))
    return sorted(results, key=lambda item: str(item.get("path") or ""))[:250]


def validate_thumbnail(path: Path) -> dict[str, Any]:
    try:
        from youtube.youtube_metadata import validate_thumbnail_for_youtube

        result = validate_thumbnail_for_youtube(path)
        result["status"] = "checked"
        return result
    except Exception as exc:
        return {"path": str(path), "status": "failed", "valid": False, "issues": [str(exc)]}


def audit_audio_quality(path: Path, *, ffmpeg: str | None) -> dict[str, Any]:
    if not ffmpeg:
        return {"status": "ffmpeg_missing"}
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "NUL"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    mean = _extract_volume(output, "mean_volume")
    max_volume = _extract_volume(output, "max_volume")
    issues: list[str] = []
    if mean is not None and mean < -23:
        issues.append("too_quiet_mean_volume_below_minus_23_dbfs")
    if max_volume is not None and max_volume > -1:
        issues.append("clipping_risk_max_volume_above_minus_1_dbfs")
    return {
        "status": "checked" if completed.returncode == 0 else "ffmpeg_failed",
        "mean_volume_dbfs": mean,
        "max_volume_dbfs": max_volume,
        "issues": issues,
    }


def scan_youtube_integration(root: Path) -> dict[str, Any]:
    patterns = ("youtube", "yt_dlp", "googleapiclient", "youtube_upload", "pytube", "upload", "video_id", "playlist")
    files: dict[str, int] = {}
    samples: list[str] = []
    for path in safe_python_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        count = sum(text.lower().count(pattern) for pattern in patterns)
        if count:
            rel = str(path.relative_to(root))
            files[rel] = count
            if len(samples) < 40:
                for idx, line in enumerate(text.splitlines(), start=1):
                    if any(pattern in line.lower() for pattern in patterns):
                        samples.append(f"{rel}:{idx}: {line.strip()[:160]}")
                        break
    return {
        "matching_file_count": len(files),
        "top_files": sorted(files.items(), key=lambda item: item[1], reverse=True)[:40],
        "samples": samples,
    }


def safe_python_files(root: Path) -> list[Path]:
    return [path for path in safe_rglob(root, {".py"}) if "site-packages" not in str(path)]


def scan_credentials(root: Path) -> dict[str, Any]:
    interesting_files: list[dict[str, Any]] = []
    env_keys: dict[str, list[str]] = {}
    for path in safe_rglob(root, {".json", ".pickle", ".env"}):
        lower = path.name.lower()
        if any(token in lower for token in ("client_secret", "credential", "token", ".env")):
            try:
                stat = path.stat()
            except OSError:
                continue
            interesting_files.append(
                {
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    "content_redacted": True,
                }
            )
            if path.name.startswith(".env") or path.suffix == ".env":
                env_keys[str(path)] = scan_env_key_names(path)
    return {
        "files": interesting_files,
        "env_keys_detected": env_keys,
        "youtube_client_secret_present": any("client_secret" in Path(item["path"]).name.lower() for item in interesting_files),
        "oauth_token_like_file_present": any("token" in Path(item["path"]).name.lower() for item in interesting_files),
    }


def scan_env_key_names(path: Path) -> list[str]:
    names: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return names
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            continue
        name = line.split("=", maxsplit=1)[0].strip()
        if any(token in name.upper() for token in ("YOUTUBE", "GOOGLE", "GMAIL")):
            names.append(name)
    return names


def check_google_libraries() -> dict[str, Any]:
    checks = {}
    for module in ("googleapiclient.discovery", "googleapiclient.http", "google.oauth2.credentials"):
        try:
            __import__(module)
            checks[module] = "ok"
        except Exception as exc:
            checks[module] = f"missing:{type(exc).__name__}"
    return checks


def run_upload_dry_run(candidates: list[MediaCandidate], thumbnails: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from googleapiclient.http import MediaFileUpload
        from youtube.youtube_metadata import build_thumbnail_upload_descriptor, build_upload_body, generate_yt_description
    except Exception as exc:
        return {"status": "failed", "error": f"import_failed:{type(exc).__name__}:{exc}"}
    if not candidates:
        return {"status": "failed", "error": "no_valid_video_candidate"}
    video_path = Path(candidates[0].path)
    description = generate_yt_description(
        topic="Atnos YouTube dry run",
        hook="Atnos testuje prywatny upload bez publikacji.",
        tags=["Atnos", "Shorts", "Test"],
        language="pl",
    )
    body = build_upload_body(
        title="Atnos Dry Run Short",
        description=description,
        tags=["Atnos", "Shorts", "Test"],
        privacy="private",
    )
    try:
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        fake = FakeYouTubeService()
        fake.videos().insert(part="snippet,status", body=body, media_body=media)
    except Exception as exc:
        return {"status": "failed", "error": f"media_or_insert_failed:{type(exc).__name__}:{exc}"}
    valid_thumb = next((item for item in thumbnails if item.get("valid")), None)
    thumb_descriptor = None
    if valid_thumb and valid_thumb.get("path"):
        thumb_descriptor = build_thumbnail_upload_descriptor("DRY_RUN_VIDEO_ID", valid_thumb["path"])
    return {
        "status": "ok",
        "video_path": str(video_path),
        "body_valid": bool(body["snippet"]["title"] and body["snippet"]["description"]),
        "privacy": body["status"]["privacyStatus"],
        "mediafile_upload_built": True,
        "mock_insert_reached": bool(fake.insert_calls),
        "thumbnail_upload_descriptor": thumb_descriptor,
        "description_preview": description[:125],
    }


class FakeYouTubeService:
    def __init__(self) -> None:
        self.insert_calls: list[dict[str, Any]] = []

    def videos(self) -> "FakeYouTubeService":
        return self

    def insert(self, **kwargs: Any) -> "FakeYouTubeService":
        self.insert_calls.append(kwargs)
        return self

    def execute(self) -> dict[str, str]:
        return {"id": "DRY_RUN_VIDEO_ID"}


def build_connectivity_matrix() -> list[dict[str, str]]:
    checks = [
        ("Topic research", ["src/atnos/agents/topic_hook/agent.py", "src/atnos/intelligence/youtube_trends.py", "src/atnos/scoring/topic_scorer.py"]),
        ("Script generation", ["src/atnos/production/steps/script_generator.py", "src/content/script_generator.py"]),
        ("Narration / TTS", ["src/atnos/production/steps/voice_synthesis.py", "src/content/elevenlabs_tts.py", "src/atnos/services/piper_tts_service.py"]),
        ("Video generation", ["src/atnos/production/steps/video_generator.py", "src/atnos/services/local_video_generation_service.py"]),
        ("Thumbnail creation", ["src/atnos/production/steps/thumbnail_generator.py", "src/content/thumbnail.py"]),
        ("Caption/subtitle generation", ["src/atnos/services/subtitle_service.py", "src/content/subtitles.py", "src/atnos/services/whisper_cpp_subtitle_service.py"]),
        ("Upload to YouTube", ["src/youtube/youtube_upload.py", "src/atnos/services/youtube_connector_service.py"]),
        ("Metadata write", ["src/youtube/youtube_metadata.py", "src/content/youtube_package.py"]),
        ("Thumbnail upload to YouTube", ["src/atnos/services/youtube_connector_service.py", "src/youtube/youtube_upload.py"]),
        ("Analytics read-back", ["src/youtube/youtube_analytics.py", "src/atnos/services/publish_analytics_followup_service.py"]),
    ]
    matrix: list[dict[str, str]] = []
    for step, rel_paths in checks:
        existing = [rel for rel in rel_paths if (API_ROOT / rel).exists()]
        status = "CONNECTED" if existing else "MISSING"
        matrix.append({"step": step, "module": ", ".join(existing) if existing else "-", "status": status})
    return matrix


def write_report(payload: dict[str, Any]) -> None:
    analyzed = payload["analyzed_videos"]
    media = payload["media"]
    score_lines = []
    for item in analyzed:
        score_lines.append(
            "| {score} | {status} | {duration} | {resolution} | {fps} | {vcodec}/{acodec} | `{path}` |".format(
                score=item.get("score", 0),
                status=item.get("status"),
                duration=item.get("duration_sec"),
                resolution=item.get("resolution"),
                fps=item.get("fps"),
                vcodec=item.get("video_codec"),
                acodec=item.get("audio_codec"),
                path=_short_path(item.get("path")),
            )
        )
    low_scores = [item for item in analyzed if int(item.get("score") or 0) < 80]
    thumb_invalid = [item for item in payload["thumbnails"] if not item.get("valid")]
    audio_issues = [
        item
        for item in analyzed
        if isinstance(item.get("audio_quality"), dict) and item["audio_quality"].get("issues")
    ]
    lines = [
        "# YouTube Pipeline & Shorts Quality Audit - Atnos",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Executive Summary",
        "",
        f"- Media files found: {media['count']} total, {media['valid_sized_count']} larger than 1 KB, {media['tiny_or_invalid_count']} tiny/placeholder.",
        f"- FFmpeg: `{payload.get('ffmpeg') or 'missing'}`",
        f"- FFprobe: `{payload.get('ffprobe') or 'missing'}`",
        f"- Videos analyzed: {len(analyzed)} most recent valid-size candidates.",
        f"- Low technical scores (<80): {len(low_scores)}.",
        f"- Invalid/missing thumbnail records: {len(thumb_invalid)}.",
        f"- Audio volume warnings: {len(audio_issues)}.",
        f"- YouTube dry-run status: {payload['upload_dry_run'].get('status')}.",
        "",
        "## Shorts Quality Score Table",
        "",
        "| Score | Status | Duration | Resolution | FPS | Codecs | Path |",
        "|---:|---|---:|---|---:|---|---|",
        *(score_lines or ["| - | no videos analyzed | - | - | - | - | - |"]),
        "",
        "## Main Quality Findings",
        "",
        *render_quality_findings(payload),
        "",
        "## Thumbnail Audit",
        "",
        *render_thumbnail_summary(payload["thumbnails"]),
        "",
        "## YouTube Autonomy Audit",
        "",
        *render_youtube_summary(payload),
        "",
        "## Pipeline Connectivity Matrix",
        "",
        "| Step | Module | Status |",
        "|---|---|---|",
        *[f"| {row['step']} | `{row['module']}` | {row['status']} |" for row in payload["connectivity"]],
        "",
        "## Safe Actions Executed",
        "",
        "- Added `src/youtube/youtube_metadata.py` for deterministic description/body/thumbnail validation.",
        "- Wired `youtube_upload.py` to reuse `build_upload_body()` and metadata fallback.",
        "- Added `tests/test_youtube_metadata.py`.",
        "- Performed dry-run upload validation with mocked insert call only. No real upload was executed.",
        "",
        "## Artifacts",
        "",
        f"- JSON: `{_short_path(str(ARTIFACT_DIR / 'youtube_pipeline_audit.json'))}`",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_quality_findings(payload: dict[str, Any]) -> list[str]:
    analyzed = payload["analyzed_videos"]
    if not analyzed:
        return ["- No valid-size video files were available for technical scoring."]
    findings: list[str] = []
    for item in analyzed[:10]:
        score = int(item.get("score") or 0)
        if score >= 90:
            continue
        problems = []
        breakdown = item.get("score_breakdown") or {}
        if breakdown.get("resolution", 0) < 20:
            problems.append("resolution not exact 1080x1920")
        if breakdown.get("aspect_ratio", 0) < 15:
            problems.append("aspect ratio not exact 9:16")
        if breakdown.get("fps", 0) < 12:
            problems.append("fps below preferred 30/60")
        if breakdown.get("audio_codec", 0) < 10:
            problems.append("audio codec not AAC")
        if item.get("thumbnail", {}).get("valid") is not True:
            problems.append("thumbnail missing or invalid")
        if item.get("audio_quality", {}).get("issues"):
            problems.extend(item["audio_quality"]["issues"])
        findings.append(f"- `{_short_path(item['path'])}` scored {score}/100: {', '.join(problems) or 'minor technical gaps'}.")
    return findings or ["- All analyzed files scored high on technical Shorts criteria."]


def render_thumbnail_summary(thumbnails: list[dict[str, Any]]) -> list[str]:
    if not thumbnails:
        return ["- No thumbnail files were found next to produced videos."]
    valid = [item for item in thumbnails if item.get("valid")]
    lines = [f"- Thumbnails checked: {len(thumbnails)}; valid for YouTube constraints: {len(valid)}."]
    for item in thumbnails[:10]:
        lines.append(
            f"- `{_short_path(str(item.get('path')))}`: valid={item.get('valid')} size={item.get('width')}x{item.get('height')} issues={item.get('issues')}"
        )
    return lines


def render_youtube_summary(payload: dict[str, Any]) -> list[str]:
    creds = payload["credential_status"]
    dry = payload["upload_dry_run"]
    google = payload["google_libraries"]
    scan = payload["youtube_scan"]
    lines = [
        f"- YouTube-related Python files detected: {scan['matching_file_count']}.",
        f"- Client secret present: {creds['youtube_client_secret_present']}.",
        f"- OAuth token-like file present: {creds['oauth_token_like_file_present']}.",
        f"- Google library checks: {json.dumps(google, ensure_ascii=False)}.",
        f"- Dry-run body valid: {dry.get('body_valid')} privacy={dry.get('privacy')} mock_insert_reached={dry.get('mock_insert_reached')}.",
        f"- Description preview: `{dry.get('description_preview', '')}`.",
    ]
    if dry.get("thumbnail_upload_descriptor"):
        lines.append(f"- Thumbnail upload descriptor valid: {dry['thumbnail_upload_descriptor'].get('valid')}.")
    lines.append("- Secret values were not printed or copied into this report.")
    return lines


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _short_path(path: str | None) -> str:
    if not path:
        return "-"
    value = str(path).replace("\\", "/")
    root = str(PROJECT_ROOT).replace("\\", "/")
    return value.replace(root + "/", "")


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_fps(value: str) -> float:
    if "/" not in value:
        return _safe_float(value)
    numerator, denominator = value.split("/", maxsplit=1)
    den = _safe_float(denominator)
    return _safe_float(numerator) / den if den else 0.0


def _extract_volume(output: str, key: str) -> float | None:
    match = re.search(rf"{re.escape(key)}:\s*(-?\d+(?:\.\d+)?)\s*dB", output)
    return float(match.group(1)) if match else None


if __name__ == "__main__":
    raise SystemExit(main())
