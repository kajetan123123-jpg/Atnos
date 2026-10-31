from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from atnos.core.config import Settings
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService
from atnos.services.voice_synthesis_service import VoiceSynthesisService


class PiperTtsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PiperRuntimeAvailability:
    available: bool
    executable_path: str | None
    model_path: str | None
    config_path: str | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "executable_path": self.executable_path,
            "model_path": self.model_path,
            "config_path": self.config_path,
            "reason": self.reason,
        }


class PiperTtsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)

    def runtime_availability(self) -> PiperRuntimeAvailability:
        executable = self._resolve_executable(self.settings.piper_exe)
        model_path = self.settings.piper_pl_model_path.resolve() if self.settings.piper_pl_model_path else None
        config_path = self.settings.piper_pl_config_path.resolve() if self.settings.piper_pl_config_path else None

        if executable is None:
            return PiperRuntimeAvailability(
                available=False,
                executable_path=None,
                model_path=model_path.as_posix() if model_path is not None else None,
                config_path=config_path.as_posix() if config_path is not None else None,
                reason="Piper executable is not configured or not found on PATH.",
            )
        if model_path is None or not model_path.exists():
            return PiperRuntimeAvailability(
                available=False,
                executable_path=executable.as_posix(),
                model_path=model_path.as_posix() if model_path is not None else None,
                config_path=config_path.as_posix() if config_path is not None else None,
                reason="Polish Piper model file is missing.",
            )
        if config_path is None or not config_path.exists():
            return PiperRuntimeAvailability(
                available=False,
                executable_path=executable.as_posix(),
                model_path=model_path.as_posix(),
                config_path=config_path.as_posix() if config_path is not None else None,
                reason="Polish Piper config file is missing.",
            )
        return PiperRuntimeAvailability(
            available=True,
            executable_path=executable.as_posix(),
            model_path=model_path.as_posix(),
            config_path=config_path.as_posix(),
            reason="Piper Polish runtime is ready.",
        )

    def build_command(
        self,
        *,
        input_path: Path,
        output_path: Path,
        speed: float = 1.0,
    ) -> list[str]:
        runtime = self.runtime_availability()
        if not runtime.available or runtime.executable_path is None or runtime.model_path is None:
            raise PiperTtsError(runtime.reason)

        command = [
            runtime.executable_path,
            "-m",
            runtime.model_path,
            "-f",
            str(output_path),
            "--input-file",
            str(input_path),
        ]
        if speed > 0 and abs(speed - 1.0) > 0.001:
            command.extend(["--length-scale", f"{1.0 / speed:.4f}"])
        model_dir = Path(runtime.model_path).parent
        command.extend(["--data-dir", str(model_dir)])
        return command

    def synthesize(
        self,
        *,
        text: str,
        conversation_id: int | None,
        speed: float = 1.0,
    ) -> ManagedArtifactDescriptor:
        normalized = VoiceSynthesisService._prepare_tts_text(text)
        if not normalized:
            raise PiperTtsError("Narration text is empty.")

        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".wav")
        temp_root = (self.settings.workspace_staging_dir / "tts" / "piper").resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        input_path = (temp_root / f"{uuid4().hex}.txt").resolve()
        input_path.write_text(normalized, encoding="utf-8")
        command = self.build_command(input_path=input_path, output_path=output_path, speed=speed)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if output_path.exists() and output_path.stat().st_size > 1024:
                return self.artifacts.finalize_output(
                    absolute_path=output_path,
                    filename="atnos-narration.wav",
                    media_type="audio/wav",
                )
            raise PiperTtsError("Piper synthesis timed out after 180 seconds.") from exc
        finally:
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass

        if result.returncode != 0:
            raise PiperTtsError(result.stderr.strip() or result.stdout.strip() or "Piper synthesis failed.")
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise PiperTtsError("Piper finished without producing a WAV file.")

        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-narration.wav",
            media_type="audio/wav",
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
