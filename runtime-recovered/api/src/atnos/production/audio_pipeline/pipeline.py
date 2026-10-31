from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Protocol

from atnos.core.config import Settings
from atnos.production.audio_pipeline.alignment import (
    align_from_whisper_srt,
    cache_dir,
    copy_cached_artifacts,
    force_align_words,
    pipeline_fingerprint,
    split_sentences,
    store_cache,
    tokenize_words,
    validate_audio_alignment,
    wav_duration_seconds,
    write_silence_wav,
    write_word_timestamps_json,
)
from atnos.production.audio_pipeline.models import (
    CHANNEL,
    SCHEMA_VERSION,
    AudioPipelineRequest,
    AudioPipelineResult,
)
from atnos.production.media_tools import resolve_ffmpeg
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleError, WhisperCppSubtitleService


class NarrationSynthesizer(Protocol):
    async def synthesize(self, *, text: str, output_path: Path) -> dict[str, Any]:
        """Write narration audio and return {ok, engine, ...}."""


class F5NarrationSynthesizer:
    """Thin adapter over existing voice_synthesis F5 integration."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(self, *, text: str, output_path: Path) -> dict[str, Any]:
        from atnos.production.steps.voice_synthesis import _try_f5_tts

        result = await _try_f5_tts(self.settings, text=text, output_path=output_path)
        return result


class OfflineToneSynthesizer:
    """Deterministic offline synthesizer for tests (not a production voice)."""

    def __init__(self, *, seconds_per_word: float = 0.35) -> None:
        self.seconds_per_word = seconds_per_word

    async def synthesize(self, *, text: str, output_path: Path) -> dict[str, Any]:
        words = max(1, len(tokenize_words(text)))
        duration = max(0.6, words * self.seconds_per_word)
        write_silence_wav(output_path, duration_seconds=duration)
        return {"ok": True, "engine": "offline_tone", "duration_seconds": duration}


class WealthBlueprintAudioPipeline:
    """script → TTS (F5 preferred) → narration.wav → alignment → word_timestamps.json."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        synthesizer: NarrationSynthesizer | None = None,
        cache_root: Path | None = None,
        prefer_whisper: bool = True,
        require_whisper: bool = False,
    ) -> None:
        self.settings = settings
        if synthesizer is not None:
            self.synthesizer = synthesizer
        elif settings is not None:
            self.synthesizer = F5NarrationSynthesizer(settings)
        else:
            self.synthesizer = OfflineToneSynthesizer()
        self.prefer_whisper = prefer_whisper
        self.require_whisper = require_whisper
        if cache_root is not None:
            self.cache_root = cache_root
        elif settings is not None:
            self.cache_root = Path(settings.data_dir) / "audio-pipeline" / CHANNEL
        else:
            self.cache_root = Path("audio-pipeline-cache") / CHANNEL

    async def run(
        self,
        script: str | AudioPipelineRequest,
        *,
        work_dir: Path,
    ) -> AudioPipelineResult:
        request = script if isinstance(script, AudioPipelineRequest) else AudioPipelineRequest(script=script)
        work_dir.mkdir(parents=True, exist_ok=True)
        voice_identity = self._voice_identity()
        engine_hint = self._engine_hint()
        fingerprint = pipeline_fingerprint(request, engine=engine_hint, voice_identity=voice_identity)
        cached = cache_dir(self.cache_root, fingerprint)

        if not request.force_regenerate and (cached / "narration.wav").is_file() and (cached / "word_timestamps.json").is_file():
            audio_path, timestamps_path = copy_cached_artifacts(cached, work_dir)
            payload = json.loads(timestamps_path.read_text(encoding="utf-8"))
            result = AudioPipelineResult.model_validate({**payload, "cache_hit": True, "word_timestamps_path": str(timestamps_path)})
            # Re-validate after restore
            validation = validate_audio_alignment(
                audio_path=audio_path,
                words=result.words,
                segments=result.segments,
                large_gap_seconds=request.large_gap_seconds,
            )
            if validation.ok:
                result.validation = validation
                result.audio_file = str(audio_path)
                write_word_timestamps_json(timestamps_path, result.model_dump(mode="json"))
                return result

        audio_path = work_dir / "narration.wav"
        synth = await self.synthesizer.synthesize(text=request.script, output_path=audio_path)
        if not synth.get("ok") or not audio_path.is_file():
            raise RuntimeError(f"TTS failed for Wealth Blueprint audio pipeline: {synth.get('error') or 'no audio'}")

        # Normalize container to WAV if provider wrote another format
        if audio_path.suffix.casefold() != ".wav" or not _is_wav(audio_path):
            raise RuntimeError("Audio pipeline expects narration.wav (WAV PCM)")

        duration = wav_duration_seconds(audio_path)
        sentences = request.sentences or split_sentences(request.script)
        spans = [(f"s{index:02d}", tokenize_words(sentence)) for index, sentence in enumerate(sentences, start=1)]
        script_words = [word for _, words in spans for word in words]
        if not script_words:
            raise RuntimeError("Script produced no words")

        timing_source = "proportional_script"
        alignment_path: Path | None = None
        words = []
        segments = []

        if self.prefer_whisper and self.settings is not None:
            try:
                srt_path = work_dir / "alignment.whisper.srt"
                srt_text = self._run_whisper(audio_path, srt_path)
                alignment_path = srt_path
                words, segments, timing_source = align_from_whisper_srt(
                    script=request.script,
                    srt_text=srt_text,
                    duration_seconds=duration,
                )
            except (WhisperCppSubtitleError, RuntimeError, ValueError, OSError) as exc:
                if self.require_whisper:
                    raise RuntimeError(f"Whisper alignment required but failed: {exc}") from exc
                words, segments = force_align_words(
                    words=script_words,
                    duration_seconds=duration,
                    segment_spans=spans,
                )
                timing_source = "proportional_script"
        else:
            words, segments = force_align_words(
                words=script_words,
                duration_seconds=duration,
                segment_spans=spans,
            )

        validation = validate_audio_alignment(
            audio_path=audio_path,
            words=words,
            segments=segments,
            large_gap_seconds=request.large_gap_seconds,
        )
        if not validation.ok:
            details = "; ".join(issue.message for issue in validation.issues if issue.severity == "error")
            raise RuntimeError(f"Audio pipeline validation failed: {details}")

        timestamps_path = work_dir / "word_timestamps.json"
        result = AudioPipelineResult(
            schema_version=SCHEMA_VERSION,
            channel=request.channel,
            script=request.script,
            audio_file=str(audio_path.resolve()),
            duration_seconds=round(duration, 3),
            words=words,
            segments=segments,
            timing_source=timing_source,
            engine=str(synth.get("engine") or engine_hint),
            fingerprint=fingerprint,
            cache_hit=False,
            validation=validation,
            word_timestamps_path=str(timestamps_path.resolve()),
            alignment_path=str(alignment_path.resolve()) if alignment_path else None,
        )
        write_word_timestamps_json(timestamps_path, result.model_dump(mode="json"))
        store_cache(cached, audio=audio_path, timestamps_json=timestamps_path)
        return result

    def _voice_identity(self) -> dict[str, Any]:
        if self.settings is None:
            return {"engine": "offline_tone"}
        ref = getattr(self.settings, "f5_tts_ref_audio_path", None)
        ref_hash = ""
        if ref:
            path = Path(str(ref))
            if path.is_file():
                ref_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "engine": "f5_tts_http",
            "ref_text": str(getattr(self.settings, "f5_tts_ref_text", "") or "").strip(),
            "speed": float(getattr(self.settings, "f5_tts_speed", 1.0) or 1.0),
            "nfe_steps": int(getattr(self.settings, "f5_tts_nfe_steps", 32) or 32),
            "reference_audio_sha256": ref_hash,
        }

    def _engine_hint(self) -> str:
        if isinstance(self.synthesizer, OfflineToneSynthesizer):
            return "offline_tone"
        return "f5_tts_http"

    def _run_whisper(self, audio_path: Path, output_srt: Path) -> str:
        assert self.settings is not None
        service = WhisperCppSubtitleService(self.settings)
        runtime = service.runtime_availability()
        if not runtime.available:
            raise RuntimeError(runtime.reason)

        asr_input = audio_path
        ffmpeg = resolve_ffmpeg()
        mono16 = output_srt.with_suffix(".16k.wav")
        if ffmpeg:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(audio_path),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(mono16),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            asr_input = mono16

        command = service.build_command(audio_path=asr_input, output_path=output_srt)
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        if result.returncode or not output_srt.is_file():
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "whisper.cpp failed")
        return output_srt.read_text(encoding="utf-8")


def _is_wav(path: Path) -> bool:
    try:
        import wave

        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() > 0
    except Exception:
        return False
