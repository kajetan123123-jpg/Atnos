from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from atnos.core.config import Settings
from atnos.services.output_artifact_service import ManagedArtifactDescriptor
from atnos.services.piper_tts_service import PiperTtsError
from content.tts_piper import PiperNarration


def test_piper_narration_falls_back_to_voice_synthesis_service() -> None:
    with TemporaryDirectory(prefix="atnos-tts-fallback-") as tmpdir:
        root = Path(tmpdir)
        settings = Settings(app_env="test", app_root=root, data_dir=root / "data")
        output_dir = root / "short"
        output_dir.mkdir(parents=True)
        fallback_wav = settings.data_dir / "atnos-outputs" / "global" / "fallback.wav"
        fallback_wav.parent.mkdir(parents=True, exist_ok=True)
        fallback_wav.write_bytes(b"RIFFfallback-wave")
        descriptor = ManagedArtifactDescriptor(
            filename="atnos-narration.wav",
            storage_path=fallback_wav.as_posix(),
            absolute_path=fallback_wav,
            media_type="audio/wav",
            file_extension=".wav",
            size_bytes=fallback_wav.stat().st_size,
        )
        narration = PiperNarration(settings)

        with patch.object(narration.service, "synthesize", side_effect=PiperTtsError("timeout")), patch.object(
            narration.fallback_service,
            "synthesize",
            return_value=descriptor,
        ):
            target = narration.generate(text="Testowa narracja.", output_dir=output_dir)

        assert target == output_dir / "narration.wav"
        assert target.read_bytes() == b"RIFFfallback-wave"
