from __future__ import annotations

from pathlib import Path
import shutil

from atnos.core.config import Settings
from atnos.services.piper_tts_service import PiperTtsError, PiperTtsService
from atnos.services.voice_synthesis_service import VoiceSynthesisError, VoiceSynthesisService
from content.audio_postprocess import postprocess_audio_for_youtube

import imageio_ffmpeg


class PiperNarration:
    supports_expressive_tags = False

    def __init__(self, settings: Settings) -> None:
        self.service = PiperTtsService(settings)
        self.fallback_service = VoiceSynthesisService(settings)

    def generate(self, *, text: str, output_dir: Path) -> Path:
        try:
            descriptor = self.service.synthesize(text=text, conversation_id=None)
        except PiperTtsError:
            try:
                descriptor = self.fallback_service.synthesize(text=text, conversation_id=None)
            except VoiceSynthesisError:
                raise
        target = output_dir / "narration.wav"
        shutil.copyfile(descriptor.absolute_path, target)
        postprocess_audio_for_youtube(
            input_path=target,
            output_path=target,
            ffmpeg_exe=self._resolve_ffmpeg(),
            strict=False,
        )
        return target

    def _resolve_ffmpeg(self) -> str:
        configured = (self.service.settings.ffmpeg_exe or "").strip()
        if configured:
            return configured
        return imageio_ffmpeg.get_ffmpeg_exe()
