from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from atnos.core.config import Settings
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService


class WhisperCppSubtitleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WhisperCppRuntimeAvailability:
    available: bool
    executable_path: str | None
    model_path: str | None
    language: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "executable_path": self.executable_path,
            "model_path": self.model_path,
            "language": self.language,
            "reason": self.reason,
        }


class WhisperCppSubtitleService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)

    def runtime_availability(self) -> WhisperCppRuntimeAvailability:
        executable = self._resolve_executable(self.settings.whisper_cpp_exe)
        model_path = self.settings.whisper_model_path.resolve() if self.settings.whisper_model_path else None
        if executable is None:
            return WhisperCppRuntimeAvailability(
                available=False,
                executable_path=None,
                model_path=model_path.as_posix() if model_path is not None else None,
                language=self.settings.whisper_language,
                reason="whisper.cpp executable is not configured or not found on PATH.",
            )
        if model_path is None or not model_path.exists():
            return WhisperCppRuntimeAvailability(
                available=False,
                executable_path=executable.as_posix(),
                model_path=model_path.as_posix() if model_path is not None else None,
                language=self.settings.whisper_language,
                reason="whisper.cpp model file is missing.",
            )
        return WhisperCppRuntimeAvailability(
            available=True,
            executable_path=executable.as_posix(),
            model_path=model_path.as_posix(),
            language=self.settings.whisper_language,
            reason="whisper.cpp subtitle runtime is ready.",
        )

    def build_command(
        self,
        *,
        audio_path: Path,
        output_path: Path,
    ) -> list[str]:
        runtime = self.runtime_availability()
        if not runtime.available or runtime.executable_path is None or runtime.model_path is None:
            raise WhisperCppSubtitleError(runtime.reason)
        output_base = output_path.with_suffix("")
        return [
            runtime.executable_path,
            "-m",
            runtime.model_path,
            "-f",
            str(audio_path),
            "-l",
            runtime.language,
            "-osrt",
            "-of",
            str(output_base),
        ]

    def generate(
        self,
        *,
        audio_path: Path,
        conversation_id: int | None,
    ) -> ManagedArtifactDescriptor:
        if not audio_path.exists():
            raise WhisperCppSubtitleError("Narration audio file is missing.")
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".srt")
        command = self.build_command(audio_path=audio_path, output_path=output_path)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise WhisperCppSubtitleError(
                result.stderr.strip() or result.stdout.strip() or "whisper.cpp failed to generate subtitles."
            )
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise WhisperCppSubtitleError("whisper.cpp finished without producing an SRT file.")
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-subtitles.srt",
            media_type="application/x-subrip",
        )

    @staticmethod
    def _resolve_executable(command: str | None) -> Path | None:
        normalized = str(command or "").strip()
        if not normalized:
            return None
        direct = Path(normalized)
        if direct.exists():
            return direct.resolve()
        discovered = shutil.which(normalized)
        if discovered:
            return Path(discovered).resolve()
        return None
