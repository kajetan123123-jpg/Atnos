from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path

import numpy as np
import soundfile as sf

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class KokoroLocalRuntimeAssets:
    python_module: str
    model_path: Path
    voices_path: Path
    preferred_voice: str
    espeak_data_path: str | None
    espeak_library_path: str | None


class KokoroLocalRuntimeError(RuntimeError):
    pass


class KokoroLocalRuntimeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def available_python_module(self) -> str | None:
        for name in ("kokoro_onnx", "kokoro"):
            if importlib.util.find_spec(name) is not None:
                return name
        return None

    def resolve_assets(self) -> KokoroLocalRuntimeAssets | None:
        python_module = self.available_python_module()
        if not python_module:
            return None

        model_candidates = [self.settings.kokoro_model_path]
        if self.settings.kokoro_model_path.name.lower() != "model_q8f16.onnx":
            model_candidates.append(self.settings.kokoro_model_path.with_name("model_q8f16.onnx"))
        voices_candidates = [self.settings.kokoro_voices_path]

        model_path = next((path for path in model_candidates if path.exists()), None)
        voices_path = next((path for path in voices_candidates if path.exists()), None)
        if model_path is None or voices_path is None:
            return None

        espeak_data_path = None
        espeak_library_path = None
        try:
            import espeakng_loader  # type: ignore

            espeak_data_path = espeakng_loader.get_data_path()
            espeak_library_path = espeakng_loader.get_library_path()
            # Windows eSpeak expects the directory containing espeak-ng-data.
            if os.name == "nt" and Path(espeak_data_path).name == "espeak-ng-data":
                espeak_data_path = str(Path(espeak_data_path).parent)
        except Exception:
            espeak_data_path = None
            espeak_library_path = None

        return KokoroLocalRuntimeAssets(
            python_module=python_module,
            model_path=model_path.resolve(),
            voices_path=voices_path.resolve(),
            preferred_voice=self.settings.kokoro_voice_name.strip() or "af_heart",
            espeak_data_path=espeak_data_path,
            espeak_library_path=espeak_library_path,
        )

    def synthesize_to_wav(
        self,
        *,
        text: str,
        output_path: Path,
        voice_name: str | None = None,
        lang: str = "pl",
        speed: float = 1.0,
    ) -> dict[str, object]:
        assets = self.resolve_assets()
        if assets is None:
            raise KokoroLocalRuntimeError("Kokoro local runtime is not available.")

        from kokoro_onnx import Kokoro  # type: ignore
        from kokoro_onnx.config import EspeakConfig  # type: ignore
        from kokoro_onnx.config import SAMPLE_RATE  # type: ignore

        class _PatchedKokoro(Kokoro):
            # Support both older and newer ONNX exports. Some releases keep `input_ids`
            # but still expect a float `speed`, which upstream helper does not handle.
            def _create_audio(self, phonemes, voice, speed):  # type: ignore[override]
                tokens = np.array(self.tokenizer.tokenize(phonemes), dtype=np.int64)
                voice = voice[len(tokens)]
                tokens = [[0, *tokens, 0]]
                input_names = [item.name for item in self.sess.get_inputs()]
                speed_input = next((item for item in self.sess.get_inputs() if item.name == "speed"), None)
                speed_dtype = np.float32
                if speed_input is not None and "int32" in speed_input.type:
                    speed_dtype = np.int32
                if "input_ids" in input_names:
                    inputs = {
                        "input_ids": tokens,
                        "style": np.array(voice, dtype=np.float32),
                        "speed": np.array([speed], dtype=speed_dtype),
                    }
                else:
                    inputs = {
                        "tokens": tokens,
                        "style": voice,
                        "speed": np.array([speed], dtype=np.float32),
                    }
                audio = self.sess.run(None, inputs)[0]
                return audio, SAMPLE_RATE

        espeak_config = EspeakConfig(
            lib_path=assets.espeak_library_path,
            data_path=assets.espeak_data_path,
        )
        runtime = _PatchedKokoro(
            str(assets.model_path),
            str(assets.voices_path),
            espeak_config=espeak_config,
        )
        selected_voice = voice_name or assets.preferred_voice
        if selected_voice not in runtime.get_voices():
            selected_voice = assets.preferred_voice if assets.preferred_voice in runtime.get_voices() else runtime.get_voices()[0]

        audio, sample_rate = runtime.create(
            text,
            voice=selected_voice,
            speed=speed,
            lang=lang,
        )
        wav = np.asarray(audio, dtype=np.float32).squeeze()
        if wav.ndim != 1:
            wav = wav.reshape(-1)
        if wav.size == 0:
            raise KokoroLocalRuntimeError("Kokoro local runtime produced empty audio.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), wav, sample_rate, format="WAV")

        return {
            "voice_name": selected_voice,
            "sample_rate": sample_rate,
            "model_path": str(assets.model_path),
            "voices_path": str(assets.voices_path),
        }
