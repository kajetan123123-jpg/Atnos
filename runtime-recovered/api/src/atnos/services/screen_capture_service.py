from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from PIL import ImageGrab

from atnos.core.config import Settings


class ScreenCaptureError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScreenArtifact:
    kind: str
    path: Path
    metadata_path: Path
    label: str
    created_at: str
    duration_seconds: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        payload["metadata_path"] = str(self.metadata_path)
        return payload


class ScreenCaptureService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_dir = settings.visual_qa_dir
        self.captures_dir = self.base_dir / "captures"
        self.recordings_dir = self.base_dir / "recordings"

    def capture_screenshot(self, *, label: str = "screen") -> ScreenArtifact:
        self._ensure_dirs()
        created_at = datetime.now().isoformat()
        safe_label = self._safe_label(label)
        path = (self.captures_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_label}_{uuid4().hex[:8]}.png").resolve()
        try:
            image = ImageGrab.grab(all_screens=True)
            image.save(path)
        except Exception as exc:  # pragma: no cover - depends on live desktop session
            raise ScreenCaptureError(f"Screen capture failed: {exc}") from exc
        return self._write_metadata(
            kind="screenshot",
            path=path,
            label=label,
            created_at=created_at,
            duration_seconds=None,
        )

    def record_screen(self, *, duration_seconds: int = 10, label: str = "screen-recording") -> ScreenArtifact:
        if duration_seconds < 1 or duration_seconds > 120:
            raise ScreenCaptureError("duration_seconds must be between 1 and 120.")
        self._ensure_dirs()
        ffmpeg = self._resolve_ffmpeg()
        created_at = datetime.now().isoformat()
        safe_label = self._safe_label(label)
        path = (self.recordings_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_label}_{uuid4().hex[:8]}.mp4").resolve()
        command = self.build_ffmpeg_record_command(
            ffmpeg=ffmpeg,
            output_path=path,
            duration_seconds=duration_seconds,
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=duration_seconds + 30,
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()[-2000:]
            raise ScreenCaptureError(f"FFmpeg screen recording failed with exit code {completed.returncode}: {stderr}")
        return self._write_metadata(
            kind="screen_recording",
            path=path,
            label=label,
            created_at=created_at,
            duration_seconds=duration_seconds,
        )

    @staticmethod
    def build_ffmpeg_record_command(*, ffmpeg: str, output_path: Path, duration_seconds: int) -> list[str]:
        return [
            ffmpeg,
            "-y",
            "-f",
            "gdigrab",
            "-framerate",
            "15",
            "-i",
            "desktop",
            "-t",
            str(duration_seconds),
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]

    def _write_metadata(
        self,
        *,
        kind: str,
        path: Path,
        label: str,
        created_at: str,
        duration_seconds: int | None,
    ) -> ScreenArtifact:
        metadata_path = path.with_suffix(path.suffix + ".json")
        artifact = ScreenArtifact(
            kind=kind,
            path=path,
            metadata_path=metadata_path,
            label=label,
            created_at=created_at,
            duration_seconds=duration_seconds,
        )
        metadata_path.write_text(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return artifact

    def _resolve_ffmpeg(self) -> str:
        configured = (self.settings.ffmpeg_exe or "").strip()
        if configured:
            configured_path = Path(configured)
            if configured_path.is_absolute():
                if configured_path.exists():
                    return str(configured_path)
                raise ScreenCaptureError(f"Configured FFMPEG_EXE does not exist: {configured}")
            discovered_configured = shutil.which(configured)
            if discovered_configured:
                return discovered_configured
            raise ScreenCaptureError(f"Configured FFMPEG_EXE is not available on PATH: {configured}")
        discovered = shutil.which("ffmpeg")
        if discovered:
            return discovered
        raise ScreenCaptureError("FFmpeg is not configured. Set FFMPEG_EXE or add ffmpeg to PATH.")

    def _ensure_dirs(self) -> None:
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_label(label: str) -> str:
        normalized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in label.strip().lower())
        normalized = "-".join(part for part in normalized.split("-") if part)
        return normalized[:48] or "screen"
