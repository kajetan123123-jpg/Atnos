from __future__ import annotations

import asyncio
import base64
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.core.logging import redact_secrets
from atnos.production.channel_profile import ProductionChannelProfile, resolve_channel_profile
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.models import ProductionContext, StepResult
from atnos.production.narration_alignment import align_narration
from atnos.production.steps.common import write_json
from atnos.services.f5_tts_runtime_service import F5TTSRuntimeService
from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeError, KokoroLocalRuntimeService
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleError, WhisperCppSubtitleService


F5_TTS_URL = "http://localhost:7860/api/predict"
F5_TTS_GRADIO_API_PREFIX = "/gradio_api"
F5_TTS_DEFAULT_API_NAME = "basic_tts"
KOKORO_ONNX_PACKAGE = "kokoro-onnx>=0.4.7,<0.5.0"
ESPEAKNG_LOADER_PACKAGE = "espeakng-loader>=0.2.4,<0.3.0"
COQUI_TTS_PACKAGE = "TTS>=0.22,<1.0"
PYTTSX3_PACKAGE = "pyttsx3>=2.99,<3.0"
TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 2
TARGET_BITRATE = "192k"
OPENAI_TTS_MAX_ATTEMPTS = 3
OPENAI_TTS_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
OPENAI_TTS_RETRY_BASE_DELAY_SEC = 4.0
OPENAI_TTS_RETRY_MAX_DELAY_SEC = 30.0
F5_TTS_GRADIO_CLIENT_TIMEOUT_S = 300.0


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    """Generate narration via premium/local TTS fallbacks, then deterministic silence."""
    script = context.artifacts["script"]
    audio_dir = context.artifact_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    timestamps: list[dict[str, Any]] = []
    segment_files: list[Path] = []
    engine_attempts: list[dict[str, Any]] = []
    normalization_results: list[dict[str, Any]] = []
    cursor = 0.0
    channel_profile = resolve_channel_profile(context)
    language_hint = _language_hint_for_context(context, channel_profile=channel_profile)
    voice_profile = _voice_profile_for_channel(settings, channel_profile)
    single_pass_payload = await _try_single_pass_elevenlabs_narration(
        settings,
        script=script,
        audio_dir=audio_dir,
        channel_profile=channel_profile,
        voice_profile=voice_profile,
    )
    if single_pass_payload is not None:
        single_pass_payload["voice_identity"] = build_voice_identity(settings, single_pass_payload)
        return write_json(context.artifact_dir / "audio_timestamps.json", single_pass_payload)
    single_pass_payload = await _try_single_pass_f5_narration(
        settings,
        script=script,
        audio_dir=audio_dir,
        channel_profile=channel_profile,
        voice_profile=voice_profile,
    )
    if single_pass_payload is not None:
        single_pass_payload["voice_identity"] = build_voice_identity(settings, single_pass_payload)
        return write_json(context.artifact_dir / "audio_timestamps.json", single_pass_payload)

    disabled_engines: set[str] = set()
    for segment in script.get("segments", []):
        segment_id = str(segment.get("id") or f"s{len(segment_files) + 1:02d}")
        text = str(segment.get("text") or "").strip()
        target_duration = float(segment.get("duration_target_sec") or 4)
        raw_path = audio_dir / f"{segment_id}.raw.wav"
        final_path = audio_dir / f"{segment_id}.wav"
        result = await _synthesize_segment(
            settings,
            text=text,
            output_path=raw_path,
            language_hint=language_hint,
            voice_profile=voice_profile,
            disabled_engines=disabled_engines,
        )
        if not raw_path.exists() or raw_path.stat().st_size <= 44:
            fallback_duration = min(target_duration, 0.35) if os.getenv("PYTEST_CURRENT_TEST") else target_duration
            _write_silent_wav(raw_path, fallback_duration)
            result = {**result, "engine": "silent_wav", "status": "fallback_silence"}
        if os.getenv("PYTEST_CURRENT_TEST") and result.get("engine") == "silent_wav":
            _write_silent_wav(final_path, _wav_duration(raw_path) or 0.35)
            normalization = {"status": "normalized", "engine": "test_silent_wav", **_validate_wav_format(final_path)}
        else:
            normalization = await _normalize_audio(raw_path, final_path)
        normalization_results.append({"segment_id": segment_id, **normalization})
        if normalization.get("status") != "normalized":
            result = {
                **result,
                "engine": "silent_wav",
                "status": "fallback_silence",
                "normalization_error": normalization.get("error") or normalization.get("status"),
            }
        engine_attempts.append({"segment_id": segment_id, **result})
        actual_duration = _wav_duration(final_path) or target_duration
        segment_files.append(final_path)
        timestamps.append(
            {
                "segment_id": segment_id,
                "start_sec": round(cursor, 2),
                "end_sec": round(cursor + actual_duration, 2),
                "duration_sec": round(actual_duration, 2),
                "target_duration_sec": round(target_duration, 2),
                "file": str(final_path),
                "text": text,
                "engine": result.get("engine") or "unknown",
            }
        )
        cursor += actual_duration

    raw_path = audio_dir / "narration_raw.wav"
    final_path = audio_dir / "narration.wav"
    _concat_wav(segment_files, raw_path, fallback_duration=max(cursor, 1.0))
    final_normalization = await _normalize_audio(raw_path, final_path)
    subtitle_path, subtitle_status = _generate_subtitles(settings, final_path, timestamps, audio_dir)
    overall_status = _overall_status(engine_attempts)
    quality_tier = _quality_tier(engine_attempts)
    silent_segments = [
        item.get("segment_id")
        for item in engine_attempts
        if str(item.get("engine") or "") == "silent_wav"
        or str(item.get("status") or "") == "fallback_silence"
    ]
    production_ready = (
        overall_status == "generated"
        and not silent_segments
        and quality_tier in {"premium_cloud", "primary", "local_neural_fallback", "cloud_neural_fallback"}
        and final_normalization.get("status") == "normalized"
    )
    payload = {
        "status": overall_status,
        "primary": "elevenlabs_openai_or_f5_tts",
        "fallbacks": ["elevenlabs", "openai_tts", "f5_tts_http", "coqui_tts", "edge_tts", "pyttsx3", "silent_wav"],
        "quality_tier": quality_tier,
        "voice_backend_grade": _voice_backend_grade(quality_tier),
        "channel_profile": {
            "key": channel_profile.key,
            "content_label": channel_profile.content_label,
            "language_code": channel_profile.language_code,
        },
        "voice_profile": voice_profile,
        "production_ready": production_ready,
        "silent_segment_ids": [str(item) for item in silent_segments if item],
        "audio_path": str(final_path),
        "output_file": str(final_path),
        "raw_audio_path": str(raw_path),
        "duration_sec": round(_wav_duration(final_path) or cursor, 2),
        "timestamps": timestamps,
        "subtitle_path": str(subtitle_path),
        "subtitle_status": subtitle_status,
        "engine_attempts": engine_attempts,
        "normalization_results": normalization_results,
        "final_normalization": final_normalization,
        "normalization": "ffmpeg loudnorm I=-14 TP=-1.5 LRA=11, 44100 Hz stereo AAC-ready WAV",
    }
    payload["voice_identity"] = build_voice_identity(settings, payload)
    return write_json(context.artifact_dir / "audio_timestamps.json", payload)


async def _try_single_pass_elevenlabs_narration(
    settings: Settings,
    *,
    script: dict[str, Any],
    audio_dir: Path,
    channel_profile: ProductionChannelProfile,
    voice_profile: dict[str, Any],
) -> dict[str, Any] | None:
    """Keep one performance and one mastering pass across the complete Short."""
    if settings.shorts_tts_backend != "elevenlabs":
        return None
    segments = [segment for segment in script.get("segments", []) if isinstance(segment, dict)]
    # Newlines make ElevenLabs pause as if every visual beat were a paragraph.
    # Sentences already carry punctuation, so spaces preserve a single natural performance.
    full_text = " ".join(
        str(segment.get("text") or "").strip()
        for segment in segments
        if str(segment.get("text") or "").strip()
    )
    if not full_text:
        return None

    raw_path = audio_dir / "narration_raw.mp3"
    final_path = audio_dir / "narration.wav"
    result = await _try_elevenlabs_tts(
        settings,
        text=full_text,
        output_path=raw_path,
        voice_profile=voice_profile,
        with_timestamps=True,
    )
    if not result.get("ok") or not raw_path.is_file() or raw_path.stat().st_size <= 1024:
        return None
    final_normalization = await _normalize_audio(raw_path, final_path)
    if final_normalization.get("status") != "normalized":
        return None

    final_duration = float(_wav_duration(final_path) or script.get("total_duration_target_sec") or 1.0)
    alignment = result.pop("alignment", None)
    word_timestamps: list[dict[str, Any]] = []
    timing_error = "Provider did not return character alignment"
    try:
        timestamps, word_timestamps = align_narration(segments, alignment or {}, duration=final_duration)
        timing_source = "provider_character_alignment"
        subtitle_path = audio_dir / "subtitles.provider.srt"
        _write_srt_from_timestamps(timestamps, subtitle_path)
        subtitle_status = {"engine": "elevenlabs_alignment", "source": timing_source, "status": "generated"}
    except ValueError as exc:
        timing_error = str(exc)
        timing_source = "estimated_word_weights"
        timestamps = _scaled_script_timestamps(segments, final_duration=final_duration, audio_file=final_path, engine="elevenlabs")
        subtitle_path, subtitle_status = _generate_subtitles(settings, final_path, timestamps, audio_dir)
    engine_attempts = [
        {
            "segment_id": str(item.get("segment_id") or ""),
            "engine": "elevenlabs",
            "status": "generated",
            "single_pass": True,
            "attempts": [result],
        }
        for item in timestamps
    ]
    return {
        "status": "generated",
        "primary": "elevenlabs_single_pass",
        "timing_source": timing_source,
        "timing_warning": timing_error if not word_timestamps else None,
        "word_timestamps": word_timestamps,
        "fallbacks": ["openai_tts", "f5_tts_http", "kokoro_local", "edge_tts"],
        "quality_tier": "premium_cloud",
        "voice_backend_grade": "premium",
        "channel_profile": {
            "key": channel_profile.key,
            "content_label": channel_profile.content_label,
            "language_code": channel_profile.language_code,
        },
        "voice_profile": voice_profile,
        "production_ready": True,
        "single_pass_narration": True,
        "cache_reused": False,
        "silent_segment_ids": [],
        "audio_path": str(final_path),
        "output_file": str(final_path),
        "raw_audio_path": str(raw_path),
        "duration_sec": round(final_duration, 2),
        "timestamps": timestamps,
        "subtitle_path": str(subtitle_path),
        "subtitle_status": subtitle_status,
        "engine_attempts": engine_attempts,
        "normalization_results": [{"segment_id": "narration", **final_normalization}],
        "final_normalization": final_normalization,
        "normalization": "single ffmpeg loudness pass after one ElevenLabs performance",
    }


async def _try_single_pass_f5_narration(
    settings: Settings,
    *,
    script: dict[str, Any],
    audio_dir: Path,
    channel_profile: ProductionChannelProfile,
    voice_profile: dict[str, Any],
) -> dict[str, Any] | None:
    """Generate one F5 narration file for the whole Short to avoid per-segment model overhead."""
    segments = [segment for segment in script.get("segments", []) if isinstance(segment, dict)]
    if not _should_use_single_pass_f5(settings, segments):
        return None
    full_text = "\n".join(str(segment.get("text") or "").strip() for segment in segments if str(segment.get("text") or "").strip())
    if not full_text:
        return None
    cached_payload = _restore_cached_single_pass_f5_narration(
        settings,
        script=script,
        audio_dir=audio_dir,
        channel_profile=channel_profile,
        voice_profile=voice_profile,
        full_text=full_text,
    )
    if cached_payload is not None:
        return cached_payload
    raw_path = audio_dir / "narration_raw.wav"
    final_path = audio_dir / "narration.wav"
    result = await _try_f5_tts(settings, text=full_text, output_path=raw_path)
    if not result.get("ok") or not raw_path.exists() or raw_path.stat().st_size <= 44:
        if _strict_f5_required(settings):
            raise RuntimeError(f"F5-TTS is required for public-quality voice but failed: {result.get('error') or 'unknown error'}")
        return None
    final_normalization = await _normalize_audio(raw_path, final_path)
    if final_normalization.get("status") != "normalized":
        return None
    final_duration = float(_wav_duration(final_path) or script.get("total_duration_target_sec") or 1.0)
    timestamps = _scaled_script_timestamps(segments, final_duration=final_duration, audio_file=final_path)
    subtitle_path, subtitle_status = _generate_subtitles(settings, final_path, timestamps, audio_dir)
    engine_attempts = [
        {
            "segment_id": str(item.get("segment_id") or ""),
            "engine": "f5_tts_http",
            "status": "generated",
            "single_pass": True,
            "attempts": [result],
        }
        for item in timestamps
    ]
    quality_tier = _quality_tier(engine_attempts)
    return {
        "status": "generated",
        "primary": "f5_tts_single_pass",
        "fallbacks": ["elevenlabs", "openai_tts", "f5_tts_http", "kokoro_local", "coqui_tts", "edge_tts", "pyttsx3", "silent_wav"],
        "quality_tier": quality_tier,
        "voice_backend_grade": _voice_backend_grade(quality_tier),
        "channel_profile": {
            "key": channel_profile.key,
            "content_label": channel_profile.content_label,
            "language_code": channel_profile.language_code,
        },
        "voice_profile": voice_profile,
        "production_ready": True,
        "single_pass_narration": True,
        "cache_reused": False,
        "narration_cache_fingerprint": _narration_cache_fingerprint(
            full_text=full_text,
            channel_profile=channel_profile,
            voice_profile=voice_profile,
            settings=settings,
        ),
        "silent_segment_ids": [],
        "audio_path": str(final_path),
        "output_file": str(final_path),
        "raw_audio_path": str(raw_path),
        "duration_sec": round(final_duration, 2),
        "timestamps": timestamps,
        "subtitle_path": str(subtitle_path),
        "subtitle_status": subtitle_status,
        "engine_attempts": engine_attempts,
        "normalization_results": [{"segment_id": "narration", **final_normalization}],
        "final_normalization": final_normalization,
        "normalization": "ffmpeg loudnorm I=-14 TP=-1.5 LRA=11, 44100 Hz stereo AAC-ready WAV",
    }


def _restore_cached_single_pass_f5_narration(
    settings: Settings,
    *,
    script: dict[str, Any],
    audio_dir: Path,
    channel_profile: ProductionChannelProfile,
    voice_profile: dict[str, Any],
    full_text: str,
) -> dict[str, Any] | None:
    """Ponownie wykorzystaj zweryfikowany głos F5 tylko dla identycznego tekstu i profilu."""
    if not bool(getattr(settings, "f5_tts_reuse_identical_narration", True)):
        return None
    account_root = audio_dir.parent.parent
    if not account_root.is_dir():
        return None
    expected_fingerprint = _narration_cache_fingerprint(
        full_text=full_text,
        channel_profile=channel_profile,
        voice_profile=voice_profile,
        settings=settings,
    )
    candidates = sorted(
        (path for path in account_root.iterdir() if path.is_dir() and path != audio_dir.parent),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate_dir in candidates:
        metadata_path = candidate_dir / "audio_timestamps.json"
        script_path = candidate_dir / "script.json"
        if not metadata_path.is_file() or not script_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            candidate_script = json.loads(script_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not _cached_f5_payload_is_eligible(metadata):
            continue
        candidate_segments = [item for item in candidate_script.get("segments", []) if isinstance(item, dict)]
        candidate_text = "\n".join(
            str(item.get("text") or "").strip()
            for item in candidate_segments
            if str(item.get("text") or "").strip()
        )
        candidate_fingerprint = str(metadata.get("narration_cache_fingerprint") or "").strip()
        if not candidate_fingerprint:
            candidate_fingerprint = _narration_cache_fingerprint(
                full_text=candidate_text,
                channel_profile=channel_profile,
                voice_profile=metadata.get("voice_profile") if isinstance(metadata.get("voice_profile"), dict) else {},
                settings=settings,
            )
        if candidate_fingerprint != expected_fingerprint:
            continue
        source_audio = Path(str(metadata.get("audio_path") or candidate_dir / "audio" / "narration.wav"))
        source_raw = Path(str(metadata.get("raw_audio_path") or candidate_dir / "audio" / "narration_raw.wav"))
        if not _valid_cached_wav(source_audio):
            continue
        final_path = audio_dir / "narration.wav"
        raw_path = audio_dir / "narration_raw.wav"
        shutil.copy2(source_audio, final_path)
        if _valid_cached_wav(source_raw):
            shutil.copy2(source_raw, raw_path)
        else:
            shutil.copy2(source_audio, raw_path)
        final_duration = float(_wav_duration(final_path) or script.get("total_duration_target_sec") or 1.0)
        timestamps = _scaled_script_timestamps(
            [item for item in script.get("segments", []) if isinstance(item, dict)],
            final_duration=final_duration,
            audio_file=final_path,
        )
        subtitle_source = Path(str(metadata.get("subtitle_path") or ""))
        subtitle_path = audio_dir / "subtitles.srt"
        if subtitle_source.is_file() and subtitle_source.stat().st_size > 0:
            cached_text = subtitle_source.read_text(encoding="utf-8", errors="ignore")
            subtitle_match = _srt_script_match_score(cached_text, timestamps)
            if subtitle_match["accepted"]:
                subtitle_path = audio_dir / subtitle_source.name
                shutil.copy2(subtitle_source, subtitle_path)
                subtitle_status = {
                    **(metadata.get("subtitle_status") if isinstance(metadata.get("subtitle_status"), dict) else {}),
                    "cache_reused": True,
                    "quality_checked": True,
                    "script_word_overlap": subtitle_match["overlap"],
                    "expected_script_words": subtitle_match["expected_words"],
                    "sequence_ratio": subtitle_match["sequence_ratio"],
                    "numeric_claims_match": subtitle_match["numeric_claims_match"],
                    "path": str(subtitle_path),
                }
            else:
                _write_srt_from_timestamps(timestamps, subtitle_path)
                subtitle_status = {
                    "engine": "script_aligned_fallback",
                    "source": "script_timestamps",
                    "status": "generated",
                    "quality_checked": True,
                    "cache_reused": False,
                    "fallback_reason": "cached_subtitles_failed_script_match",
                    "sequence_ratio": subtitle_match["sequence_ratio"],
                    "numeric_claims_match": subtitle_match["numeric_claims_match"],
                    "path": str(subtitle_path),
                }
        else:
            subtitle_path, subtitle_status = _generate_subtitles(settings, final_path, timestamps, audio_dir)
        source_job_id = candidate_dir.name
        normalization = {
            **_validate_wav_format(final_path),
            "status": "normalized",
            "engine": "verified_f5_cache",
            "cache_reused": True,
        }
        return {
            "status": "generated",
            "primary": "f5_tts_single_pass_cache",
            "fallbacks": ["f5_tts_http"],
            "quality_tier": "primary",
            "voice_backend_grade": "premium",
            "channel_profile": {
                "key": channel_profile.key,
                "content_label": channel_profile.content_label,
                "language_code": channel_profile.language_code,
            },
            "voice_profile": voice_profile,
            "production_ready": True,
            "single_pass_narration": True,
            "cache_reused": True,
            "cache_source_job_id": source_job_id,
            "narration_cache_fingerprint": expected_fingerprint,
            "silent_segment_ids": [],
            "audio_path": str(final_path),
            "output_file": str(final_path),
            "raw_audio_path": str(raw_path),
            "duration_sec": round(final_duration, 2),
            "timestamps": timestamps,
            "subtitle_path": str(subtitle_path),
            "subtitle_status": subtitle_status,
            "engine_attempts": [
                {
                    "segment_id": str(item.get("segment_id") or ""),
                    "engine": "f5_tts_http",
                    "status": "generated",
                    "single_pass": True,
                    "cache_reused": True,
                    "cache_source_job_id": source_job_id,
                }
                for item in timestamps
            ],
            "normalization_results": [{"segment_id": "narration", **normalization}],
            "final_normalization": normalization,
            "normalization": "verified normalized F5 WAV cache, 44100 Hz stereo",
        }
    return None


def _cached_f5_payload_is_eligible(payload: dict[str, Any]) -> bool:
    if payload.get("production_ready") is not True or payload.get("single_pass_narration") is not True:
        return False
    engines = {
        str(item.get("engine") or "").strip().casefold()
        for item in payload.get("engine_attempts", [])
        if isinstance(item, dict)
    }
    primary = str(payload.get("primary") or "").casefold()
    return "f5_tts_http" in engines or "f5_tts" in primary


def _valid_cached_wav(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 44:
        return False
    validation = _validate_wav_format(path)
    return bool(validation.get("ok"))


def _narration_cache_fingerprint(
    *,
    full_text: str,
    channel_profile: ProductionChannelProfile,
    voice_profile: dict[str, Any],
    settings: Settings,
) -> str:
    stable_voice = {
        key: voice_profile.get(key)
        for key in (
            "channel_profile_key",
            "language_code",
            "narration_style",
            "gender",
            "pace",
        )
    }
    identity = {
        "text": " ".join(full_text.split()),
        "channel": channel_profile.key,
        "voice": stable_voice,
        "backend": "f5_tts_http",
        "ref_text": str(getattr(settings, "f5_tts_ref_text", "") or "").strip(),
        "speed": float(getattr(settings, "f5_tts_speed", 1.0) or 1.0),
        "nfe_steps": int(getattr(settings, "f5_tts_nfe_steps", 32) or 32),
        "reference_audio_sha256": _file_sha256(_resolve_f5_ref_audio_path(settings)),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_voice_identity(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """Zbuduj stabilną, pozbawioną sekretów tożsamość użytego głosu produkcyjnego."""
    attempts = [item for item in payload.get("engine_attempts", []) if isinstance(item, dict)]
    primary = str(payload.get("primary") or "").strip().casefold()
    provider_family = _voice_provider_family(primary, attempts)
    profile = payload.get("voice_profile") if isinstance(payload.get("voice_profile"), dict) else {}
    channel = payload.get("channel_profile") if isinstance(payload.get("channel_profile"), dict) else {}
    identity: dict[str, Any] = {
        "schema_version": "1.0",
        "provider_family": provider_family,
        "channel_profile_key": str(channel.get("key") or profile.get("channel_profile_key") or ""),
        "language_code": str(channel.get("language_code") or profile.get("language_code") or ""),
        "narration_style": str(profile.get("narration_style") or ""),
        "performance_direction": str(profile.get("performance_direction") or ""),
    }
    if provider_family == "f5_tts":
        identity.update(
            {
                "reference_audio_sha256": _file_sha256(_resolve_f5_ref_audio_path(settings)),
                "reference_text_sha256": hashlib.sha256(
                    str(getattr(settings, "f5_tts_ref_text", "") or "").strip().encode("utf-8")
                ).hexdigest(),
                "speed": float(getattr(settings, "f5_tts_speed", 1.0) or 1.0),
                "nfe_steps": int(getattr(settings, "f5_tts_nfe_steps", 32) or 32),
            }
        )
    elif provider_family == "elevenlabs":
        identity["voice_id"] = str(profile.get("elevenlabs_voice_id") or "")
    elif provider_family == "openai_tts":
        identity["voice_id"] = str(profile.get("openai_tts_voice") or "")
    elif provider_family == "kokoro":
        identity["voice_id"] = str(profile.get("kokoro_voice") or "")
        identity["speed"] = float(profile.get("kokoro_speed") or 1.0)
    elif provider_family == "edge_tts":
        identity["voice_id"] = str(profile.get("edge_tts_voice") or "")
        identity["rate"] = str(profile.get("edge_tts_rate") or "")
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**identity, "identity_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def _voice_provider_family(primary: str, attempts: list[dict[str, Any]]) -> str:
    # A failed premium attempt is not evidence that it produced the narration.
    # Segment mode may also mix engines; a mixed voice must never inherit the
    # production approval of any single provider.
    generated_families = {
        family
        for item in attempts
        if str(item.get("status") or "").strip().casefold() == "generated"
        for family in [_provider_family_from_engine(str(item.get("engine") or ""))]
        if family != "unknown"
    }
    if len(generated_families) == 1:
        return next(iter(generated_families))
    if len(generated_families) > 1:
        return "mixed"

    # Single-pass manifests have a precise primary marker. The generic
    # multi-provider marker is intentionally ignored when no generated attempt
    # identifies the actual engine.
    if primary in {"f5_tts_single_pass", "f5_tts_single_pass_cache"}:
        return "f5_tts"
    return _provider_family_from_engine(primary)


def _provider_family_from_engine(engine: str) -> str:
    normalized = engine.strip().casefold()
    if normalized in {"f5_tts_http", "f5_tts_single_pass", "f5_tts_single_pass_cache"}:
        return "f5_tts"
    if normalized == "elevenlabs":
        return "elevenlabs"
    if normalized == "openai_tts":
        return "openai_tts"
    if normalized == "kokoro_local":
        return "kokoro"
    if normalized == "coqui_tts":
        return "coqui"
    if normalized == "edge_tts":
        return "edge_tts"
    if normalized == "pyttsx3":
        return "pyttsx3"
    if normalized == "silent_wav":
        return "silent"
    return "unknown"


def _file_sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _should_use_single_pass_f5(settings: Settings, segments: list[dict[str, Any]]) -> bool:
    if os.getenv("PYTEST_CURRENT_TEST") and not bool(getattr(settings, "f5_tts_single_pass_narration", False)):
        return False
    return bool(
        getattr(settings, "f5_tts_single_pass_narration", False)
        and str(settings.shorts_tts_backend or "").strip().lower() == "f5_tts_http"
        and len(segments) >= 2
    )


def _strict_f5_required(settings: Settings) -> bool:
    return bool(
        str(settings.shorts_tts_backend or "").strip().lower() == "f5_tts_http"
        and bool(getattr(settings, "shorts_require_premium_voice_before_render", False))
    )


def _scaled_script_timestamps(
    segments: list[dict[str, Any]],
    *,
    final_duration: float,
    audio_file: Path,
    engine: str = "f5_tts_http",
) -> list[dict[str, Any]]:
    targets = [max(0.1, float(segment.get("duration_target_sec") or 1.0)) for segment in segments]
    target_total = sum(targets) or float(len(segments) or 1)
    scale = max(0.01, final_duration / target_total)
    cursor = 0.0
    timestamps: list[dict[str, Any]] = []
    for index, (segment, target) in enumerate(zip(segments, targets), start=1):
        duration = max(0.1, target * scale)
        end = final_duration if index == len(segments) else min(final_duration, cursor + duration)
        timestamps.append(
            {
                "segment_id": str(segment.get("id") or f"s{index:02d}"),
                "start_sec": round(cursor, 2),
                "end_sec": round(end, 2),
                "duration_sec": round(max(0.0, end - cursor), 2),
                "target_duration_sec": round(target, 2),
                "file": str(audio_file),
                "text": str(segment.get("text") or "").strip(),
                "engine": engine,
                "single_pass": True,
            }
        )
        cursor = end
    return timestamps


async def _synthesize_segment(
    settings: Settings,
    *,
    text: str,
    output_path: Path,
    language_hint: str = "",
    voice_profile: dict[str, Any] | None = None,
    disabled_engines: set[str] | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    engines = _engine_sequence(settings)
    for engine, fn in engines:
        if disabled_engines is not None and engine in disabled_engines:
            attempts.append({"engine": engine, "ok": False, "error": "Skipped after earlier failure in this job."})
            continue
        if engine in {"elevenlabs", "openai_tts"}:
            result = await fn(settings, text=text, output_path=output_path, voice_profile=voice_profile)
        elif engine == "kokoro_local":
            result = await fn(
                settings,
                text=text,
                output_path=output_path,
                language_hint=language_hint,
                voice_profile=voice_profile,
            )
        elif engine == "edge_tts":
            result = await fn(
                settings,
                text=text,
                output_path=output_path,
                language_hint=language_hint,
                voice_profile=voice_profile,
            )
        else:
            result = await fn(settings, text=text, output_path=output_path)
        attempts.append(result)
        if result.get("ok"):
            return {"engine": engine, "status": "generated", "attempts": attempts}
        if engine == "f5_tts_http" and _strict_f5_required(settings):
            raise RuntimeError(f"F5-TTS is required for public-quality voice but failed: {result.get('error') or 'unknown error'}")
        if engine == "f5_tts_http" and disabled_engines is not None and _should_disable_f5_for_remaining_segments(result):
            disabled_engines.add("f5_tts_http")
    return {"engine": "silent_wav", "status": "fallback_silence", "attempts": attempts}


def _should_disable_f5_for_remaining_segments(result: dict[str, Any]) -> bool:
    """Nie ponawiaj F5 na każdym segmencie, jeśli runtime jest wolny albo niedostępny."""
    error = str(result.get("error") or "").casefold()
    if not error:
        return False
    markers = (
        "timed out",
        "timeout",
        "not ready",
        "connection refused",
        "connecterror",
        "readtimeout",
        "404 not found",
    )
    return any(marker in error for marker in markers)


def _engine_sequence(settings: Settings):
    """Ustal kolejność TTS tak, żeby nie blokować renderu ciężką instalacją Coqui."""
    coqui_entry = ("coqui_tts", _try_coqui_tts)
    base = [
        ("elevenlabs", _try_elevenlabs_tts),
        ("openai_tts", _try_openai_tts),
        ("f5_tts_http", _try_f5_tts),
        ("kokoro_local", _try_kokoro_local_tts),
        ("edge_tts", _try_edge_tts),
        ("pyttsx3", _try_pyttsx3),
    ]
    if settings.shorts_tts_backend == "coqui_tts":
        return [base[0], base[1], coqui_entry, base[2], base[3], base[4], base[5]]
    if settings.shorts_tts_backend == "kokoro_local":
        return [base[3], base[0], base[1], base[2], base[4], coqui_entry, base[5]]
    if settings.shorts_tts_backend == "openai_tts":
        return [base[1], base[0], base[2], base[3], base[4], coqui_entry, base[5]]
    if settings.shorts_tts_backend == "f5_tts_http":
        return [base[2], base[3], base[4], base[0], base[1], coqui_entry, base[5]]
    return [*base[:5], coqui_entry, base[5]]


async def _try_elevenlabs_tts(
    settings: Settings,
    *,
    text: str,
    output_path: Path,
    voice_profile: dict[str, Any] | None = None,
    with_timestamps: bool = False,
    previous_text: str | None = None,
    next_text: str | None = None,
    pronunciation_dictionary_locators: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    enabled = settings.shorts_tts_backend == "elevenlabs" or bool(settings.shorts_allow_cloud_tts)
    if not enabled:
        return {"engine": "elevenlabs", "ok": False, "error": "ElevenLabs disabled by settings."}
    voice_id = _preferred_elevenlabs_voice_id(settings, voice_profile=voice_profile)
    if not settings.elevenlabs_api_key or not voice_id:
        return {
            "engine": "elevenlabs",
            "ok": False,
            "error": "ElevenLabs API key or voice_id missing.",
            "channel_profile_key": str((voice_profile or {}).get("channel_profile_key") or ""),
        }
    endpoint = (
        f"{settings.elevenlabs_base_url.rstrip('/')}/v1/text-to-speech/"
        f"{voice_id}{'/with-timestamps' if with_timestamps else ''}?output_format={settings.elevenlabs_output_format}"
    )
    payload = {
        "text": text,
        "model_id": settings.elevenlabs_model_id,
        "voice_settings": {
            "stability": settings.elevenlabs_stability,
            "similarity_boost": settings.elevenlabs_similarity_boost,
            "style": settings.elevenlabs_style,
            "speed": settings.elevenlabs_speed,
            "use_speaker_boost": settings.elevenlabs_use_speaker_boost,
        },
    }
    language_code = _preferred_elevenlabs_language_code(settings, voice_profile=voice_profile)
    if language_code:
        payload["language_code"] = language_code
    # Eleven v3 explicitly does not support request stitching. Sending even
    # text-only neighbouring context is rejected rather than ignored by the
    # provider. Preserve the chosen voice/model and the complete spoken text;
    # do not hide this capability gap by switching to an older model.
    stitching_supported = str(settings.elevenlabs_model_id).strip().casefold() != "eleven_v3"
    omitted_context = []
    if previous_text and stitching_supported:
        payload["previous_text"] = previous_text
    elif previous_text:
        omitted_context.append("previous_text")
    if next_text and stitching_supported:
        payload["next_text"] = next_text
    elif next_text:
        omitted_context.append("next_text")
    if pronunciation_dictionary_locators:
        payload["pronunciation_dictionary_locators"] = pronunciation_dictionary_locators
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                endpoint,
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "Accept": "application/json" if with_timestamps else "audio/mpeg",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            if with_timestamps:
                data = response.json()
                audio_bytes = base64.b64decode(data["audio_base64"], validate=True)
                alignment = data.get("alignment") or data.get("normalized_alignment")
            else:
                audio_bytes, alignment = response.content, None
    except Exception as exc:
        # HTTPStatusError.__str__ omits the useful provider response and may
        # include a URL. Keep the provider's bounded diagnostic while stripping
        # the actual credential even if it appears as an unlabelled value.
        error = _safe_http_error(exc)
        if settings.elevenlabs_api_key:
            error = error.replace(settings.elevenlabs_api_key, "[REDACTED]")
        return {"engine": "elevenlabs", "ok": False,
                "error": str(redact_secrets(error))[:300],
                "status_code": _http_error_status_code(exc),
                "model_id": settings.elevenlabs_model_id,
                "request_stitching": {"supported": stitching_supported,
                                      "context_applied": False,
                                      "omitted_parameters": omitted_context}}
    output_path.write_bytes(audio_bytes)
    return {
        "engine": "elevenlabs",
        "alignment": alignment,
        "ok": output_path.exists() and output_path.stat().st_size > 1024,
        "model_id": settings.elevenlabs_model_id,
        "voice_id_source": _elevenlabs_voice_id_source(settings, voice_profile=voice_profile),
        "voice_id_configured": True,
        "voice_id": voice_id,
        "voice_settings": payload["voice_settings"],
        "channel_profile_key": str((voice_profile or {}).get("channel_profile_key") or ""),
        "language_code": language_code,
        "output_format": settings.elevenlabs_output_format,
        "request_stitching": {"supported": stitching_supported,
                              "context_applied": bool(payload.get("previous_text") or payload.get("next_text")),
                              "omitted_parameters": omitted_context},
    }


async def _try_openai_tts(
    settings: Settings,
    *,
    text: str,
    output_path: Path,
    voice_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enabled = settings.shorts_tts_backend == "openai_tts" or bool(settings.shorts_allow_cloud_tts)
    if not enabled:
        return {"engine": "openai_tts", "ok": False, "error": "OpenAI TTS disabled by settings."}
    if not settings.openai_api_key:
        return {"engine": "openai_tts", "ok": False, "error": "OPENAI_API_KEY missing."}
    voice = _preferred_openai_tts_voice(settings, voice_profile=voice_profile)
    instructions = _openai_tts_instructions(voice_profile=voice_profile)
    payload = {
        "model": settings.openai_tts_model,
        "voice": voice,
        "input": text,
        "response_format": settings.openai_tts_response_format,
    }
    if instructions:
        payload["instructions"] = instructions
    attempts: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            for attempt in range(1, OPENAI_TTS_MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        "https://api.openai.com/v1/audio/speech",
                        headers={
                            "Authorization": f"Bearer {settings.openai_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    break
                except Exception as exc:
                    status_code = _http_error_status_code(exc)
                    retryable = _is_retryable_openai_tts_error(exc)
                    attempts.append(
                        {
                            "attempt": attempt,
                            "status_code": status_code,
                            "retryable": retryable,
                            "error": _safe_http_error(exc),
                        }
                    )
                    if not retryable or attempt >= OPENAI_TTS_MAX_ATTEMPTS:
                        return {
                            "engine": "openai_tts",
                            "ok": False,
                            "error": _safe_http_error(exc),
                            "voice": voice,
                            "model_id": settings.openai_tts_model,
                            "channel_profile_key": str((voice_profile or {}).get("channel_profile_key") or ""),
                            "attempts": attempts,
                        }
                    await asyncio.sleep(_openai_tts_retry_delay_seconds(exc, attempt=attempt))
    except Exception as exc:
        return {
            "engine": "openai_tts",
            "ok": False,
            "error": _safe_http_error(exc),
            "voice": voice,
            "model_id": settings.openai_tts_model,
            "channel_profile_key": str((voice_profile or {}).get("channel_profile_key") or ""),
            "attempts": attempts,
        }
    output_path.write_bytes(response.content)
    return {
        "engine": "openai_tts",
        "ok": output_path.exists() and output_path.stat().st_size > 1024,
        "model_id": settings.openai_tts_model,
        "voice": voice,
        "voice_source": _openai_tts_voice_source(settings, voice_profile=voice_profile),
        "response_format": settings.openai_tts_response_format,
        "channel_profile_key": str((voice_profile or {}).get("channel_profile_key") or ""),
        "retry_attempts": attempts,
    }


async def _try_f5_tts(settings: Settings, *, text: str, output_path: Path) -> dict[str, Any]:
    if str(getattr(settings, "f5_tts_transport", "gradio")).strip().lower() == "cli":
        return await _try_f5_tts_cli(settings, text=text, output_path=output_path)

    endpoint = settings.f5_tts_base_url or F5_TTS_URL
    timeout_s = 0.25 if os.getenv("PYTEST_CURRENT_TEST") else 8.0
    if not os.getenv("PYTEST_CURRENT_TEST"):
        runtime = await _ensure_f5_runtime_ready(settings)
        if not runtime.get("ready"):
            return {
                "engine": "f5_tts_http",
                "ok": False,
                "error": str(runtime.get("error") or "F5-TTS runtime is not ready.")[:300],
                "runtime": runtime,
            }
        gradio_result = await _try_f5_tts_gradio(settings, endpoint=endpoint, text=text, output_path=output_path)
        if gradio_result.get("ok"):
            return gradio_result
        if not gradio_result.get("legacy_fallback_allowed"):
            return gradio_result
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(
                endpoint,
                json={"data": [text]},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
    except Exception as exc:
        return {"engine": "f5_tts_http", "ok": False, "error": str(exc)[:300]}

    try:
        payload = response.json()
    except ValueError:
        payload = None
    audio_bytes = _extract_audio_bytes(payload) if isinstance(payload, dict) else response.content
    if not audio_bytes:
        return {"engine": "f5_tts_http", "ok": False, "error": "F5-TTS returned no audio payload."}
    output_path.write_bytes(audio_bytes)
    return {"engine": "f5_tts_http", "ok": output_path.stat().st_size > 44}


async def _try_f5_tts_cli(settings: Settings, *, text: str, output_path: Path) -> dict[str, Any]:
    """Generate locally without the unstable Gradio SSE transport on Windows."""

    runtime = F5TTSRuntimeService(settings)
    install_dir = runtime.resolve_install_dir()
    runtime_python = runtime.resolve_runtime_python(install_dir)
    reference_audio = runtime.resolve_reference_audio(install_dir)
    if runtime_python is None or not runtime_python.exists():
        return {"engine": "f5_tts_http", "ok": False, "mode": "cli", "error": "F5-TTS runtime Python is missing."}
    if reference_audio is None or not reference_audio.exists():
        return {"engine": "f5_tts_http", "ok": False, "mode": "cli", "error": "F5-TTS reference audio is missing."}

    try:
        await asyncio.to_thread(runtime.stop, wait_timeout_s=20)
    except Exception as exc:
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "cli",
            "error": f"Could not stop the F5-TTS server before CLI generation: {exc}"[:300],
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    command = [
        str(runtime_python),
        "-m",
        "f5_tts.infer.infer_cli",
        "-r",
        str(reference_audio),
        "-s",
        settings.f5_tts_ref_text,
        "-t",
        text,
        "-o",
        str(output_path.parent),
        "-w",
        output_path.name,
        "--nfe_step",
        str(settings.f5_tts_nfe_steps),
        "--speed",
        str(settings.f5_tts_speed),
        "--device",
        "cpu",
    ]
    env = runtime.subprocess_environment()
    ffmpeg_dir = getattr(settings, "f5_tts_ffmpeg_dir", None)
    if ffmpeg_dir is None:
        try:
            ffmpeg_exe = resolve_ffmpeg()
            ffmpeg_dir = Path(ffmpeg_exe).parent
        except Exception:
            ffmpeg_dir = None
    if ffmpeg_dir:
        env["PATH"] = f"{Path(ffmpeg_dir)}{os.pathsep}{env.get('PATH', '')}"

    def run_cli() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=str(install_dir or output_path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_f5_tts_generation_timeout_s(settings),
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )

    try:
        completed = await asyncio.to_thread(run_cli)
    except subprocess.TimeoutExpired:
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "cli",
            "error": f"F5-TTS CLI timed out after {_f5_tts_generation_timeout_s(settings):.0f}s.",
        }
    except Exception as exc:
        return {"engine": "f5_tts_http", "ok": False, "mode": "cli", "error": str(exc)[:300]}

    ok = completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024
    return {
        "engine": "f5_tts_http",
        "ok": ok,
        "mode": "cli",
        "returncode": completed.returncode,
        "reference_audio": str(reference_audio),
        "error": "" if ok else (completed.stderr or completed.stdout or "F5-TTS CLI produced no audio.")[-300:],
    }


async def _ensure_f5_runtime_ready(settings: Settings) -> dict[str, Any]:
    """Start configured local F5-TTS runtime when the endpoint is offline."""
    service = F5TTSRuntimeService(settings)
    try:
        status = await asyncio.to_thread(service.status)
    except Exception as exc:
        return {"ready": False, "error": str(exc)[:240], "started": False}
    if status.endpoint_ready:
        return {"ready": True, "started": False, "endpoint": status.endpoint}
    if not status.launch_supported:
        return {
            "ready": False,
            "started": False,
            "endpoint": status.endpoint,
            "error": status.endpoint_error or "F5-TTS launch command is not configured.",
        }
    try:
        started_status = await asyncio.to_thread(service.start, wait_timeout_s=90)
    except Exception as exc:
        return {"ready": False, "started": False, "endpoint": status.endpoint, "error": str(exc)[:240]}
    return {
        "ready": bool(started_status.endpoint_ready),
        "started": bool(started_status.started),
        "endpoint": started_status.endpoint,
        "error": started_status.endpoint_error or "",
    }


async def _try_f5_tts_gradio(
    settings: Settings,
    *,
    endpoint: str,
    text: str,
    output_path: Path,
) -> dict[str, Any]:
    """Generate F5-TTS through the queued Gradio 6 SSE contract."""
    base_url = _f5_gradio_base_url(endpoint)
    api_name = _f5_gradio_api_name(settings)
    ref_audio = _resolve_f5_ref_audio_path(settings)
    if ref_audio is None:
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "gradio",
            "base_url": base_url,
            "error": "F5-TTS reference audio is missing.",
            "legacy_fallback_allowed": True,
        }
    request_timeout = 30.0
    generation_timeout_s = _f5_tts_generation_timeout_s(settings)
    config_seen = False
    event_id = ""
    request_started_ns = __import__("time").time_ns()
    gradio_audio_before = _snapshot_f5_gradio_audio(settings)
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            config_response = await client.get(f"{base_url}/config")
            if config_response.status_code != 200:
                return {
                    "engine": "f5_tts_http",
                    "ok": False,
                    "mode": "gradio",
                    "base_url": base_url,
                    "status_code": config_response.status_code,
                    "error": f"F5-TTS Gradio config unavailable: HTTP {config_response.status_code}",
                    "legacy_fallback_allowed": True,
                }
            config_seen = True
            config_payload = config_response.json()
            api_prefix = str(config_payload.get("api_prefix") or F5_TTS_GRADIO_API_PREFIX).rstrip("/")
            dependency_index = _f5_gradio_dependency_index(config_payload, api_name=api_name)
            if dependency_index is None:
                return {
                    "engine": "f5_tts_http",
                    "ok": False,
                    "mode": "gradio_queue",
                    "base_url": base_url,
                    "error": f"F5-TTS Gradio API '{api_name}' is not registered.",
                    "legacy_fallback_allowed": False,
                }
            with ref_audio.open("rb") as audio_handle:
                upload_response = await client.post(
                    f"{base_url}{api_prefix}/upload",
                    files={"files": (ref_audio.name, audio_handle, "audio/wav")},
                )
            upload_response.raise_for_status()
            uploaded_path = _extract_gradio_upload_path(upload_response.json())
            if not uploaded_path:
                return {
                    "engine": "f5_tts_http",
                    "ok": False,
                    "mode": "gradio",
                    "base_url": base_url,
                    "error": "F5-TTS Gradio upload returned no file path.",
                    "legacy_fallback_allowed": False,
                }
            payload = {
                "data": [
                    {
                        "path": uploaded_path,
                        "orig_name": ref_audio.name,
                        "meta": {"_type": "gradio.FileData"},
                    },
                    settings.f5_tts_ref_text,
                    text,
                    False,
                    True,
                    0,
                    settings.f5_tts_cross_fade_duration,
                    settings.f5_tts_nfe_steps,
                    settings.f5_tts_speed,
                ]
            }
            session_hash = uuid.uuid4().hex
            call_response = await client.post(
                f"{base_url}{api_prefix}/queue/join",
                json={
                    **payload,
                    "fn_index": dependency_index,
                    "session_hash": session_hash,
                },
            )
            call_response.raise_for_status()
            event_id = str((call_response.json() or {}).get("event_id") or "").strip()
            if not event_id:
                return {
                    "engine": "f5_tts_http",
                    "ok": False,
                    "mode": "gradio",
                    "base_url": base_url,
                    "error": "F5-TTS Gradio returned no event_id.",
                    "legacy_fallback_allowed": False,
                }
            last_status = "queued"
            stream_attempt = 0
            deadline = asyncio.get_running_loop().time() + generation_timeout_s
            while stream_attempt < 3 and asyncio.get_running_loop().time() < deadline:
                stream_attempt += 1
                remaining_s = max(0.1, deadline - asyncio.get_running_loop().time())
                try:
                    async with asyncio.timeout(remaining_s):
                        async with client.stream(
                            "GET",
                            f"{base_url}{api_prefix}/queue/data",
                            params={"session_hash": session_hash},
                            timeout=httpx.Timeout(connect=request_timeout, read=min(60.0, remaining_s), write=request_timeout, pool=request_timeout),
                        ) as event_response:
                            event_response.raise_for_status()
                            async for raw_line in event_response.aiter_lines():
                                line = str(raw_line or "").strip()
                                if not line.startswith("data:"):
                                    continue
                                try:
                                    message = json.loads(line.split(":", maxsplit=1)[1].strip())
                                except json.JSONDecodeError:
                                    continue
                                if not isinstance(message, dict) or message.get("event_id") != event_id:
                                    continue
                                last_status = str(message.get("msg") or "unknown")
                                if last_status in {"process_errored", "process_failed"}:
                                    return {
                                        "engine": "f5_tts_http",
                                        "ok": False,
                                        "mode": "gradio_queue",
                                        "base_url": base_url,
                                        "event_id": event_id,
                                        "error": str(message.get("error") or "F5-TTS Gradio generation failed.")[:300],
                                        "legacy_fallback_allowed": False,
                                    }
                                if last_status != "process_completed":
                                    continue
                                output_payload = message.get("output") if isinstance(message.get("output"), dict) else {}
                                if output_payload.get("success") is False:
                                    return {
                                        "engine": "f5_tts_http",
                                        "ok": False,
                                        "mode": "gradio_queue",
                                        "base_url": base_url,
                                        "event_id": event_id,
                                        "error": str(output_payload.get("error") or "F5-TTS Gradio generation failed.")[:300],
                                        "legacy_fallback_allowed": False,
                                    }
                                audio_file = _extract_gradio_audio_file(output_payload.get("data"))
                                if not audio_file:
                                    return {
                                        "engine": "f5_tts_http",
                                        "ok": False,
                                        "mode": "gradio_queue",
                                        "base_url": base_url,
                                        "event_id": event_id,
                                        "error": "F5-TTS Gradio returned no audio file.",
                                        "legacy_fallback_allowed": False,
                                    }
                                await _write_gradio_audio_output(client, audio_file, output_path)
                                return {
                                    "engine": "f5_tts_http",
                                    "ok": output_path.exists() and output_path.stat().st_size > 1024,
                                    "mode": "gradio_queue",
                                    "base_url": base_url,
                                    "api_name": api_name,
                                    "event_id": event_id,
                                    "reference_audio": str(ref_audio),
                                    "stream_attempts": stream_attempt,
                                }
                except (TimeoutError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                    if stream_attempt >= 3 or asyncio.get_running_loop().time() >= deadline:
                        raise
                    await asyncio.sleep(0.5)
            return {
                "engine": "f5_tts_http",
                "ok": False,
                "mode": "gradio_queue",
                "base_url": base_url,
                "event_id": event_id,
                "error": f"F5-TTS Gradio stream closed before completion. Last event: {last_status}",
                "stream_attempts": stream_attempt,
                "legacy_fallback_allowed": False,
            }
    except TimeoutError:
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "gradio_queue",
            "base_url": base_url,
            "event_id": event_id,
            "error": f"F5-TTS Gradio timed out after {generation_timeout_s:.0f}s.",
            "legacy_fallback_allowed": False,
        }
    except Exception as exc:
        if event_id:
            recovered = await _recover_f5_gradio_audio(
                settings,
                before=gradio_audio_before,
                request_started_ns=request_started_ns,
                output_path=output_path,
            )
            if recovered:
                return {
                    "engine": "f5_tts_http",
                    "ok": True,
                    "mode": "gradio_temp_recovery",
                    "base_url": base_url,
                    "api_name": api_name,
                    "event_id": event_id,
                    "reference_audio": str(ref_audio),
                    "recovered_after_error": type(exc).__name__,
                }
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "gradio",
            "base_url": base_url,
            "error": str(exc)[:300],
            "legacy_fallback_allowed": not config_seen,
        }


def _f5_gradio_temp_dirs(settings: Settings) -> tuple[Path, ...]:
    """Return deterministic Gradio temp candidates without scanning unrelated disks."""

    configured = getattr(settings, "f5_tts_gradio_temp_dir", None)
    candidates = [
        Path(configured) if configured else None,
        settings.app_root / "data" / "temp" / "gradio",
        settings.data_dir / "temp" / "gradio",
        Path(tempfile.gettempdir()) / "gradio",
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        key = str(resolved).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return tuple(unique)


def _snapshot_f5_gradio_audio(settings: Settings) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for temp_dir in _f5_gradio_temp_dirs(settings):
        if not temp_dir.exists():
            continue
        for path in temp_dir.rglob("audio.wav"):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[str(path.resolve()).casefold()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


async def _recover_f5_gradio_audio(
    settings: Settings,
    *,
    before: dict[str, tuple[int, int]],
    request_started_ns: int,
    output_path: Path,
    wait_timeout_s: float = 12.0,
) -> bool:
    """Recover the one fresh WAV when Gradio closes SSE after successful generation."""

    deadline = asyncio.get_running_loop().time() + wait_timeout_s
    while asyncio.get_running_loop().time() < deadline:
        candidates: list[Path] = []
        for temp_dir in _f5_gradio_temp_dirs(settings):
            if not temp_dir.exists():
                continue
            for path in temp_dir.rglob("audio.wav"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = str(path.resolve()).casefold()
                state = (stat.st_mtime_ns, stat.st_size)
                # NTFS timestamp precision can trail time_ns() slightly on fast local writes.
                if (
                    stat.st_size > 1024
                    and stat.st_mtime_ns >= request_started_ns - 2_000_000_000
                    and before.get(key) != state
                ):
                    candidates.append(path)
        if len(candidates) == 1:
            source = candidates[0]
            size_before = source.stat().st_size
            await asyncio.sleep(0.25)
            if source.exists() and source.stat().st_size == size_before:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, output_path)
                return output_path.exists() and output_path.stat().st_size > 1024
        if len(candidates) > 1:
            return False
        await asyncio.sleep(0.5)
    return False


def _f5_gradio_dependency_index(config_payload: dict[str, Any], *, api_name: str) -> int | None:
    dependencies = config_payload.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, dict):
            continue
        if str(dependency.get("api_name") or "").strip().strip("/") != api_name:
            continue
        raw_id = dependency.get("id", index)
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            return index
    return None


async def _try_f5_tts_gradio_client(
    settings: Settings,
    *,
    endpoint: str,
    text: str,
    output_path: Path,
) -> dict[str, Any]:
    """Generuj F5-TTS przez oficjalny klient Gradio zamiast ręcznie parsować SSE."""
    base_url = _f5_gradio_base_url(endpoint)
    api_name = f"/{_f5_gradio_api_name(settings)}"
    ref_audio = _resolve_f5_ref_audio_path(settings)
    if ref_audio is None:
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "gradio_client",
            "base_url": base_url,
            "error": "F5-TTS reference audio is missing.",
            "legacy_fallback_allowed": True,
        }
    payload = {
        "base_url": base_url,
        "api_name": api_name,
        "ref_audio": str(ref_audio),
        "ref_text": settings.f5_tts_ref_text,
        "text": text,
        "output_path": str(output_path),
        "cross_fade_duration": settings.f5_tts_cross_fade_duration,
        "nfe_steps": settings.f5_tts_nfe_steps,
        "speed": settings.f5_tts_speed,
    }
    script = r'''
import json
import shutil
import sys
from pathlib import Path

MARKER = "ATNOS_JSON_RESULT="

def emit(payload):
    print(MARKER + json.dumps(payload, ensure_ascii=False), flush=True)

def extract_audio(payload):
    if isinstance(payload, str):
        path = Path(payload)
        return {"path": str(path)} if path.exists() else None
    path_value = getattr(payload, "path", None)
    url_value = getattr(payload, "url", None)
    if isinstance(path_value, str) or isinstance(url_value, str):
        return {"path": path_value, "url": url_value}
    if isinstance(payload, dict):
        path_item = payload.get("path") or payload.get("name")
        url_item = payload.get("url")
        if isinstance(path_item, str) or isinstance(url_item, str):
            return {"path": path_item, "url": url_item}
        for value in payload.values():
            found = extract_audio(value)
            if found:
                return found
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = extract_audio(item)
            if found:
                return found
    return None

try:
    from gradio_client import Client, handle_file
except Exception as exc:
    emit({"ok": False, "error": f"gradio_client unavailable: {exc}"})
    raise SystemExit(3)

request = json.loads(sys.stdin.read())
try:
    client = Client(request["base_url"])
    result = client.predict(
        ref_audio_input=handle_file(request["ref_audio"]),
        ref_text_input=request["ref_text"],
        gen_text_input=request["text"],
        remove_silence=False,
        randomize_seed=True,
        seed_input=0,
        cross_fade_duration_slider=request["cross_fade_duration"],
        nfe_slider=request["nfe_steps"],
        speed_slider=request["speed"],
        api_name=request["api_name"],
    )
    audio = extract_audio(result)
    if not audio:
        emit({"ok": False, "error": "gradio_client returned no audio file", "result_repr": repr(result)[:800]})
        raise SystemExit(4)
    output_path = Path(request["output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = audio.get("path")
    if isinstance(raw_path, str) and Path(raw_path).exists():
        shutil.copyfile(raw_path, output_path)
    elif isinstance(audio.get("url"), str) and audio["url"]:
        import httpx
        response = httpx.get(audio["url"], timeout=30.0)
        response.raise_for_status()
        output_path.write_bytes(response.content)
    else:
        emit({"ok": False, "error": "audio file has neither local path nor URL", "audio": audio})
        raise SystemExit(5)
    emit({"ok": output_path.exists() and output_path.stat().st_size > 1024, "audio": audio, "size": output_path.stat().st_size})
except Exception as exc:
    emit({"ok": False, "error": str(exc)[:800]})
    raise SystemExit(6)
'''
    generation_timeout_s = _f5_tts_generation_timeout_s(settings)
    try:
        completed = await asyncio.to_thread(
            _run_subprocess_with_timeout,
            [sys.executable, "-c", script],
            json.dumps(payload, ensure_ascii=False),
            generation_timeout_s,
        )
        child_payload = _parse_marked_json_line(completed.stdout, marker="ATNOS_JSON_RESULT=")
        if completed.returncode != 0 or not bool(child_payload.get("ok")):
            return {
                "engine": "f5_tts_http",
                "ok": False,
                "mode": "gradio_client",
                "base_url": base_url,
                "api_name": api_name,
                "error": str(
                    child_payload.get("error")
                    or completed.stderr
                    or completed.stdout
                    or f"gradio_client subprocess exited {completed.returncode}"
                )[:300],
                "returncode": completed.returncode,
                "legacy_fallback_allowed": False,
            }
        return {
            "engine": "f5_tts_http",
            "ok": output_path.exists() and output_path.stat().st_size > 1024,
            "mode": "gradio_client",
            "base_url": base_url,
            "api_name": api_name,
            "reference_audio": str(ref_audio),
            "subprocess": True,
        }
    except subprocess.TimeoutExpired:
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "gradio_client",
            "base_url": base_url,
            "api_name": api_name,
            "error": f"F5-TTS Gradio client timed out after {generation_timeout_s:.0f}s.",
            "legacy_fallback_allowed": False,
        }
    except Exception as exc:
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "gradio_client",
            "base_url": base_url,
            "api_name": api_name,
            "error": str(exc)[:300],
            "legacy_fallback_allowed": False,
        }


def _f5_tts_generation_timeout_s(settings: Settings) -> float:
    """Return the configured wall-clock budget for one F5-TTS generation."""
    raw_value = getattr(settings, "f5_tts_generation_timeout_sec", F5_TTS_GRADIO_CLIENT_TIMEOUT_S)
    try:
        return min(1800.0, max(30.0, float(raw_value)))
    except (TypeError, ValueError):
        return F5_TTS_GRADIO_CLIENT_TIMEOUT_S


def _run_subprocess_with_timeout(
    command: list[str],
    input_text: str,
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    """Uruchom proces potomny i zabij całe drzewo, jeśli Gradio zawiesi request."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    expected_created = None
    try:
        import psutil

        expected_created = psutil.Process(process.pid).create_time()
    except Exception:
        # The Popen handle remains a safe fallback even when psutil is absent.
        pass
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process.pid, expected_created=expected_created)
        # Popen.kill is bound to the handle created above; it is safe against
        # PID reuse and is the only fallback when the identity check cannot run.
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        except Exception:
            stdout, stderr = exc.output or "", exc.stderr or ""
        raise subprocess.TimeoutExpired(command, timeout_s, output=stdout, stderr=stderr) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _terminate_process_tree(pid: int, *, expected_created: float | None = None) -> bool:
    """Stop only the timed-out F5 client family after an identity check.

    A PID-only taskkill fallback is intentionally forbidden: a failed process
    inspection must leave the process for the owning Popen handle instead of
    risking a signal to an unrelated process that reused the PID.
    """
    if expected_created is None:
        return False
    try:
        import psutil

        parent = psutil.Process(pid)
        if abs(parent.create_time() - expected_created) > 0.001:
            return False
        children = parent.children(recursive=True)
        for process in children:
            process.terminate()
        parent.terminate()
        gone, alive = psutil.wait_procs([parent, *children], timeout=3.0)
        for process in alive:
            process.kill()
        psutil.wait_procs(alive, timeout=2.0)
        return not alive
    except Exception:
        return False


async def _try_kokoro_local_tts(
    settings: Settings,
    *,
    text: str,
    output_path: Path,
    language_hint: str = "",
    voice_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if settings.app_env == "test":
        return {"engine": "kokoro_local", "ok": False, "error": "Kokoro local disabled in test env."}
    installed = await _ensure_kokoro_runtime_dependencies(settings)
    if not installed:
        return {
            "engine": "kokoro_local",
            "ok": False,
            "error": "Kokoro local runtime packages are missing.",
        }
    try:
        service = KokoroLocalRuntimeService(settings)
        result = await asyncio.to_thread(
            service.synthesize_to_wav,
            text=text,
            output_path=output_path,
            voice_name=_preferred_kokoro_voice(settings, voice_profile=voice_profile),
            lang=_kokoro_language(language_hint, voice_profile=voice_profile),
            speed=_preferred_kokoro_speed(voice_profile=voice_profile),
        )
        return {
            "engine": "kokoro_local",
            "ok": output_path.exists() and output_path.stat().st_size > 1024,
            **result,
        }
    except (KokoroLocalRuntimeError, Exception) as exc:
        return {"engine": "kokoro_local", "ok": False, "error": str(exc)[:300]}


async def _ensure_kokoro_runtime_dependencies(settings: Settings) -> bool:
    if KokoroLocalRuntimeService(settings).available_python_module() is not None:
        return True
    if settings.app_env == "test":
        return False
    packages = [KOKORO_ONNX_PACKAGE]
    if importlib.util.find_spec("espeakng_loader") is None:
        packages.append(ESPEAKNG_LOADER_PACKAGE)
    for package in packages:
        if not await _auto_install(package):
            return False
    return KokoroLocalRuntimeService(settings).available_python_module() is not None


async def _try_coqui_tts(settings: Settings, *, text: str, output_path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("TTS") is None:
        if settings.app_env == "test":
            return {"engine": "coqui_tts", "ok": False, "error": "Coqui TTS disabled in test env."}
        if settings.shorts_tts_backend != "coqui_tts":
            return {
                "engine": "coqui_tts",
                "ok": False,
                "error": "Coqui TTS package missing; auto-install skipped unless SHORTS_TTS_BACKEND=coqui_tts.",
            }
        installed = await _auto_install(COQUI_TTS_PACKAGE)
        if not installed:
            return {"engine": "coqui_tts", "ok": False, "error": "Coqui TTS package missing."}

    def _run() -> dict[str, Any]:
        try:
            from TTS.api import TTS  # type: ignore

            model_name = settings.coqui_tts_model
            tts = TTS(model_name=model_name, progress_bar=False)
            tts.tts_to_file(text=text, file_path=str(output_path))
            return {"engine": "coqui_tts", "ok": output_path.exists() and output_path.stat().st_size > 44}
        except Exception as exc:
            return {"engine": "coqui_tts", "ok": False, "error": str(exc)[:300]}

    return await asyncio.to_thread(_run)


async def _try_edge_tts(
    settings: Settings,
    *,
    text: str,
    output_path: Path,
    language_hint: str = "",
    voice_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if settings.app_env == "test":
        return {"engine": "edge_tts", "ok": False, "error": "Edge TTS disabled in test env."}
    if importlib.util.find_spec("edge_tts") is None:
        installed = await _auto_install("edge-tts>=7.2.3,<8.0.0")
        if not installed:
            return {"engine": "edge_tts", "ok": False, "error": "edge-tts package missing."}

    try:
        import edge_tts  # type: ignore

        voice = _preferred_edge_voice(settings, text, language_hint=language_hint, voice_profile=voice_profile)
        rate = _preferred_edge_rate(settings, voice_profile=voice_profile)
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
            await communicate.save(str(output_path))
            profile_voice_error = ""
        except Exception as exc:
            fallback_voice = _preferred_edge_voice(settings, text, language_hint=language_hint)
            if fallback_voice == voice:
                raise
            voice = fallback_voice
            rate = settings.shorts_edge_tts_rate
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
            await communicate.save(str(output_path))
            profile_voice_error = str(exc)[:180]
        return {
            "engine": "edge_tts",
            "ok": output_path.exists() and output_path.stat().st_size > 1024,
            "voice": voice,
            "rate": rate,
            "narration_style": str((voice_profile or {}).get("narration_style") or ""),
            "profile_voice_fallback": bool(profile_voice_error),
            "profile_voice_error": profile_voice_error,
        }
    except Exception as exc:
        return {"engine": "edge_tts", "ok": False, "error": str(exc)[:300]}


async def _try_pyttsx3(settings: Settings, *, text: str, output_path: Path) -> dict[str, Any]:
    if settings.app_env == "test":
        return {"engine": "pyttsx3", "ok": False, "error": "pyttsx3 disabled in test env."}
    if importlib.util.find_spec("pyttsx3") is None:
        installed = await _auto_install(PYTTSX3_PACKAGE)
        if not installed:
            return {"engine": "pyttsx3", "ok": False, "error": "pyttsx3 package missing."}

    def _run() -> dict[str, Any]:
        try:
            import pyttsx3  # type: ignore

            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.save_to_file(text, str(output_path))
            engine.runAndWait()
            return {"engine": "pyttsx3", "ok": output_path.exists() and output_path.stat().st_size > 44}
        except Exception as exc:
            return {"engine": "pyttsx3", "ok": False, "error": str(exc)[:300]}

    return await asyncio.to_thread(_run)


async def _auto_install(package: str) -> bool:
    timeout_s = 900 if package.casefold().startswith("tts") else 180
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            package,
            "--break-system-packages",
            "-q",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        return proc.returncode == 0
    except Exception:
        return False


async def _normalize_audio(input_path: Path, output_path: Path) -> dict[str, Any]:
    ffmpeg = _ffmpeg_exe()
    if ffmpeg:
        normalized, primary_error = await _run_ffmpeg_audio_filter(
            ffmpeg,
            input_path=input_path,
            output_path=output_path,
            audio_filter="aresample=44100,pan=stereo|c0=c0|c1=c0,loudnorm=I=-14:TP=-1.5:LRA=11",
        )
        if normalized is not None:
            return {
                **normalized,
                "status": "normalized",
                "engine": "ffmpeg",
                "loudness_target_applied": True,
            }

        # A valid neural voice must never be destroyed because the loudness filter had a
        # transient failure. Preserve it with a deterministic format-only conversion;
        # downstream QA can still reject the missing LUFS target for public publishing.
        normalized, fallback_error = await _run_ffmpeg_audio_filter(
            ffmpeg,
            input_path=input_path,
            output_path=output_path,
            audio_filter="aresample=44100,pan=stereo|c0=c0|c1=c0",
        )
        if normalized is not None:
            return {
                **normalized,
                "status": "normalized",
                "engine": "ffmpeg_format_fallback",
                "loudness_target_applied": False,
                "warning": primary_error or "loudnorm failed; format-only normalization used",
            }
        error = "; ".join(item for item in (primary_error, fallback_error) if item)[:600]
    else:
        error = "ffmpeg executable not available."
    duration = _wav_duration(input_path) or 1.0
    _write_silent_wav(output_path, duration)
    validation = _validate_wav_format(output_path)
    return {
        **validation,
        "status": "fallback_silence",
        "engine": "silent_wav",
        "error": error,
    }


async def _run_ffmpeg_audio_filter(
    ffmpeg: str,
    *,
    input_path: Path,
    output_path: Path,
    audio_filter: str,
) -> tuple[dict[str, Any] | None, str]:
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-af",
        audio_filter,
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-ac",
        str(TARGET_CHANNELS),
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    def _execute() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        proc = await asyncio.to_thread(_execute)
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return None, detail[:300]
    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 44:
        detail = (proc.stderr or b"").decode(errors="ignore").strip()[-600:]
        return None, detail or f"ffmpeg exited with code {proc.returncode}"
    validation = _validate_wav_format(output_path)
    if not validation["ok"]:
        return None, str(validation["error"])
    return validation, ""


def _generate_subtitles(
    settings: Settings,
    audio_path: Path,
    timestamps: list[dict[str, Any]],
    audio_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    whisper_path = audio_dir / "subtitles.whisper.srt"
    whisper_error = ""
    try:
        service = WhisperCppSubtitleService(settings)
        descriptor = service.generate(audio_path=audio_path, conversation_id=None)
        source = Path(descriptor.absolute_path)
        if source.exists():
            shutil.copyfile(source, whisper_path)
            whisper_text = whisper_path.read_text(encoding="utf-8", errors="ignore")
            match = _srt_script_match_score(whisper_text, timestamps)
            if match["accepted"]:
                return whisper_path, {
                    "engine": "whisper_cpp",
                    "source": "whisper_cpp",
                    "status": "generated",
                    "quality_checked": True,
                    "script_word_overlap": match["overlap"],
                    "expected_script_words": match["expected_words"],
                    "sequence_ratio": match["sequence_ratio"],
                    "numeric_claims_match": match["numeric_claims_match"],
                    "path": str(whisper_path),
                }
            whisper_error = "whisper.cpp SRT did not match script text."
        whisper_error = "whisper.cpp did not produce a readable SRT."
    except (WhisperCppSubtitleError, Exception) as exc:
        whisper_error = str(exc)[:300]

    fallback_path = audio_dir / "subtitles.srt"
    _write_srt_from_timestamps(timestamps, fallback_path)
    return fallback_path, {
        "engine": "script_aligned_fallback",
        "source": "script_timestamps",
        "status": "generated",
        "quality_checked": True,
        "fallback_reason": "whisper_unavailable_or_rejected",
        "whisper_error": whisper_error,
        "path": str(fallback_path),
    }


def _extract_audio_bytes(payload: dict[str, Any]) -> bytes | None:
    data = payload.get("data")
    candidates = data if isinstance(data, list) else [payload]
    for item in candidates:
        if isinstance(item, str):
            if item.startswith("data:"):
                item = item.split(",", maxsplit=1)[-1]
            try:
                return base64.b64decode(item)
            except Exception:
                path = Path(item)
                if path.exists():
                    return path.read_bytes()
        if isinstance(item, dict):
            for key in ("path", "name", "file"):
                raw_path = item.get(key)
                if isinstance(raw_path, str) and Path(raw_path).exists():
                    return Path(raw_path).read_bytes()
            raw_data = item.get("data")
            if isinstance(raw_data, str):
                try:
                    return base64.b64decode(raw_data.split(",", maxsplit=1)[-1])
                except Exception:
                    continue
    return None


def _f5_gradio_base_url(endpoint: str) -> str:
    """Convert legacy predict/call endpoints to the Gradio base URL."""
    base = str(endpoint or F5_TTS_URL).strip().rstrip("/")
    suffixes = (
        f"{F5_TTS_GRADIO_API_PREFIX}/call/{F5_TTS_DEFAULT_API_NAME}",
        f"{F5_TTS_GRADIO_API_PREFIX}/call",
        F5_TTS_GRADIO_API_PREFIX,
        "/api/predict",
    )
    for suffix in suffixes:
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    if f"{F5_TTS_GRADIO_API_PREFIX}/" in base:
        base = base.split(f"{F5_TTS_GRADIO_API_PREFIX}/", maxsplit=1)[0].rstrip("/")
    return base or "http://localhost:7860"


def _f5_gradio_api_name(settings: Settings) -> str:
    return str(getattr(settings, "f5_tts_api_name", "") or F5_TTS_DEFAULT_API_NAME).strip().strip("/") or F5_TTS_DEFAULT_API_NAME


def _resolve_f5_ref_audio_path(settings: Settings) -> Path | None:
    configured = getattr(settings, "f5_tts_ref_audio_path", None)
    if configured is not None and Path(configured).exists():
        return Path(configured)
    install_dir = getattr(settings, "f5_tts_install_dir", None)
    if install_dir is not None:
        sample = (
            Path(install_dir)
            / ".venv"
            / "Lib"
            / "site-packages"
            / "f5_tts"
            / "infer"
            / "examples"
            / "basic"
            / "basic_ref_en.wav"
        )
        if sample.exists():
            return sample
    # Keep generation aligned with the runtime doctor, which can discover a
    # valid F5-TTS installation even when F5_TTS_INSTALL_DIR is not explicit.
    runtime = F5TTSRuntimeService(settings)
    discovered = runtime.resolve_reference_audio(runtime.resolve_install_dir())
    if discovered is not None and discovered.exists():
        return discovered
    return None


def _extract_gradio_upload_path(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        for item in payload:
            found = _extract_gradio_upload_path(item)
            if found:
                return found
    if isinstance(payload, dict):
        for key in ("path", "name", "file"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for key in ("data", "files"):
            found = _extract_gradio_upload_path(payload.get(key))
            if found:
                return found
    return ""


def _parse_gradio_sse_complete(sse_text: str) -> Any | None:
    event_name = ""
    data_lines: list[str] = []
    for raw_line in str(sse_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("event:"):
            if event_name == "complete" and data_lines:
                return json.loads("\n".join(data_lines))
            event_name = line.split(":", maxsplit=1)[1].strip()
            data_lines = []
            continue
        if line.startswith("data:") and event_name == "complete":
            data_lines.append(line.split(":", maxsplit=1)[1].strip())
    if event_name == "complete" and data_lines:
        return json.loads("\n".join(data_lines))
    return None


def _gradio_sse_error(sse_text: str) -> str:
    for raw_line in str(sse_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("data:"):
            return line.split(":", maxsplit=1)[1].strip()[:240]
    return ""


def _extract_gradio_audio_file(complete_payload: Any) -> dict[str, Any] | None:
    candidates = complete_payload if isinstance(complete_payload, list) else [complete_payload]
    for item in candidates:
        if isinstance(item, dict) and (item.get("path") or item.get("url")):
            return item
        if isinstance(item, list):
            found = _extract_gradio_audio_file(item)
            if found:
                return found
    return None


async def _write_gradio_audio_output(
    client: httpx.AsyncClient,
    audio_file: dict[str, Any],
    output_path: Path,
) -> None:
    raw_path = audio_file.get("path")
    if isinstance(raw_path, str) and Path(raw_path).exists():
        output_path.write_bytes(Path(raw_path).read_bytes())
        return
    url = audio_file.get("url")
    if isinstance(url, str) and url:
        response = await client.get(url)
        response.raise_for_status()
        output_path.write_bytes(response.content)
        return
    raise ValueError("F5-TTS Gradio audio file has neither readable local path nor download URL.")


def _parse_marked_json_line(value: str, *, marker: str) -> dict[str, Any]:
    for raw_line in reversed(str(value or "").splitlines()):
        line = raw_line.strip()
        if not line.startswith(marker):
            continue
        try:
            payload = json.loads(line[len(marker) :])
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _extract_gradio_client_audio_file(payload: Any) -> dict[str, Any] | None:
    """Wyciągnij plik audio z wyniku gradio_client niezależnie od wersji obiektu."""
    if isinstance(payload, Path):
        return {"path": str(payload)} if payload.exists() else None
    if isinstance(payload, str):
        path = Path(payload)
        return {"path": str(path)} if path.exists() else None
    path_value = getattr(payload, "path", None)
    url_value = getattr(payload, "url", None)
    if isinstance(path_value, str) or isinstance(url_value, str):
        return {"path": path_value, "url": url_value}
    if isinstance(payload, dict):
        path_item = payload.get("path") or payload.get("name")
        url_item = payload.get("url")
        if isinstance(path_item, str) or isinstance(url_item, str):
            return {"path": path_item, "url": url_item}
        for value in payload.values():
            found = _extract_gradio_client_audio_file(value)
            if found:
                return found
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = _extract_gradio_client_audio_file(item)
            if found:
                return found
    return None


async def _write_gradio_client_audio_output(audio_file: dict[str, Any], output_path: Path) -> None:
    raw_path = audio_file.get("path")
    if isinstance(raw_path, str) and raw_path:
        path = Path(raw_path)
        if path.exists():
            output_path.write_bytes(path.read_bytes())
            return
    url = audio_file.get("url")
    if isinstance(url, str) and url:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            output_path.write_bytes(response.content)
            return
    raise ValueError("F5-TTS Gradio client audio file has neither readable local path nor download URL.")


def _concat_wav(paths: list[Path], output_path: Path, *, fallback_duration: float) -> None:
    if not paths:
        _write_silent_wav(output_path, fallback_duration)
        return
    try:
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(TARGET_CHANNELS)
            output.setsampwidth(2)
            output.setframerate(TARGET_SAMPLE_RATE)
            for path in paths:
                with wave.open(str(path), "rb") as source:
                    output.writeframes(source.readframes(source.getnframes()))
        if output_path.stat().st_size > 44:
            return
    except Exception:
        pass
    _write_silent_wav(output_path, fallback_duration)


def _write_srt_from_timestamps(timestamps: list[dict[str, Any]], path: Path) -> None:
    blocks: list[str] = []
    for index, item in enumerate(timestamps, start=1):
        blocks.append(
            f"{index}\n{_srt_ts(float(item['start_sec']))} --> {_srt_ts(float(item['end_sec']))}\n{item.get('text', '')}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def _srt_matches_script(srt_text: str, timestamps: list[dict[str, Any]]) -> bool:
    return bool(_srt_script_match_score(srt_text, timestamps)["accepted"])


def _srt_script_match_score(srt_text: str, timestamps: list[dict[str, Any]]) -> dict[str, Any]:
    caption_text = _srt_caption_text(srt_text)
    expected_text = " ".join(str(item.get("text") or "").strip() for item in timestamps)
    recognized_tokens = _ordered_words(caption_text)
    expected_tokens = _ordered_words(expected_text)
    recognized_words = {word for word in recognized_tokens if len(word) >= 4 and not word.isdigit()}
    expected_words = {word for word in expected_tokens if len(word) >= 4 and not word.isdigit()}
    if not expected_tokens or not expected_words:
        return {
            "accepted": False,
            "overlap": 0,
            "expected_words": 0,
            "overlap_ratio": 0.0,
            "sequence_ratio": 0.0,
            "numeric_claims_match": False,
            "expected_numeric_claims": [],
            "recognized_numeric_claims": [],
        }
    overlap = expected_words.intersection(recognized_words)
    overlap_ratio = len(overlap) / len(expected_words)
    sequence_ratio = SequenceMatcher(a=expected_tokens, b=recognized_tokens, autojunk=False).ratio()
    expected_numeric_claims = _numeric_claims(expected_text)
    recognized_numeric_claims = _numeric_claims(caption_text)
    numeric_claims_match = Counter(expected_numeric_claims) == Counter(recognized_numeric_claims)
    # Word-set overlap alone accepted plausible hallucinations and corrupted financial values.
    # Preserve script order and require every numeric claim to match before using Whisper output.
    accepted = (
        overlap_ratio >= 0.80
        and sequence_ratio >= 0.90
        and numeric_claims_match
        and len(overlap) >= min(8, len(expected_words))
    )
    return {
        "accepted": accepted,
        "overlap": len(overlap),
        "expected_words": len(expected_words),
        "overlap_ratio": round(overlap_ratio, 4),
        "sequence_ratio": round(sequence_ratio, 4),
        "numeric_claims_match": numeric_claims_match,
        "expected_numeric_claims": expected_numeric_claims,
        "recognized_numeric_claims": recognized_numeric_claims,
    }


def _word_set(value: str) -> set[str]:
    return {word for word in _ordered_words(value) if not word.isdigit()}


def _srt_caption_text(value: str) -> str:
    caption_lines: list[str] = []
    for block in re.split(r"\r?\n\s*\r?\n", str(value or "").strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].isdigit() and len(lines) > 1 and "-->" in lines[1]:
            lines = lines[2:]
        else:
            lines = [line for line in lines if "-->" not in line]
        caption_lines.extend(lines)
    return " ".join(caption_lines)


def _ordered_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[.,][0-9]+)?", str(value or "").casefold())


def _numeric_claims(value: str) -> list[str]:
    claims: list[str] = []
    pattern = re.compile(r"(?<![a-z0-9])(?P<currency>\$)?(?P<number>\d+(?:[.,]\d+)?)(?P<percent>%?)", re.IGNORECASE)
    for match in pattern.finditer(str(value or "")):
        number = match.group("number").replace(",", ".")
        claims.append(f"{'$' if match.group('currency') else ''}{number}{match.group('percent')}")
    return claims


def _write_silent_wav(path: Path, duration_s: float) -> None:
    frames = int(TARGET_SAMPLE_RATE * max(duration_s, 0.1))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(TARGET_CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(TARGET_SAMPLE_RATE)
        wav.writeframes(b"\x00\x00\x00\x00" * frames)


def _wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except Exception:
        return None


def _validate_wav_format(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            frames = wav.getnframes()
    except Exception as exc:
        return {
            "ok": False,
            "sample_rate_hz": None,
            "channels": None,
            "duration_sec": None,
            "error": f"invalid WAV output: {str(exc)[:200]}",
        }
    ok = sample_rate == TARGET_SAMPLE_RATE and channels == TARGET_CHANNELS and frames > 0
    return {
        "ok": ok,
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "duration_sec": round(frames / float(sample_rate), 2) if sample_rate else None,
        "error": "" if ok else f"expected {TARGET_SAMPLE_RATE}Hz/{TARGET_CHANNELS}ch WAV, got {sample_rate}Hz/{channels}ch",
    }


def _srt_ts(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def _ffmpeg_exe() -> str | None:
    resolved = resolve_ffmpeg()
    if resolved:
        return resolved
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _overall_status(attempts: list[dict[str, Any]]) -> str:
    engines = {str(item.get("engine")) for item in attempts}
    if engines == {"silent_wav"}:
        return "generated_fallback_silence"
    if "silent_wav" in engines:
        return "generated_partial_fallback_silence"
    if engines:
        return "generated"
    return "generated_fallback_silence"


def _quality_tier(attempts: list[dict[str, Any]]) -> str:
    engines = {str(item.get("engine")) for item in attempts}
    if "f5_tts_http" in engines:
        return "primary"
    if "elevenlabs" in engines:
        return "premium_cloud"
    if "openai_tts" in engines:
        return "premium_cloud"
    if "kokoro_local" in engines:
        return "local_neural_fallback"
    if "coqui_tts" in engines:
        return "local_neural_fallback"
    if "edge_tts" in engines:
        return "cloud_neural_fallback"
    if "pyttsx3" in engines:
        return "offline_system_fallback"
    return "placeholder_silence"


def _voice_backend_grade(quality_tier: str) -> str:
    if quality_tier in {"premium_cloud", "primary"}:
        return "premium"
    if quality_tier in {"local_neural_fallback", "cloud_neural_fallback"}:
        return "acceptable_neural_fallback"
    if quality_tier == "offline_system_fallback":
        return "draft_only"
    return "not_usable"


def _language_hint_for_context(
    context: ProductionContext,
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> str:
    profile = channel_profile or resolve_channel_profile(context)
    if profile.language_code:
        return profile.language_code
    instruction = profile.language_instruction.casefold()
    if "english" in instruction:
        return "en"
    if "polish" in instruction:
        return "pl"
    return ""


def _voice_profile_for_context(settings: Settings, context: ProductionContext) -> dict[str, Any]:
    return _voice_profile_for_channel(settings, resolve_channel_profile(context))


def _voice_profile_for_channel(settings: Settings, profile: ProductionChannelProfile) -> dict[str, Any]:
    language_code = profile.language_code or settings.youtube_default_audio_language or settings.youtube_default_language
    fallback_voice = settings.shorts_edge_tts_voice_en if language_code.casefold().startswith("en") else settings.shorts_edge_tts_voice_pl
    voice_profile = {
        "channel_profile_key": profile.key,
        "language_code": language_code,
        "edge_tts_voice": profile.edge_tts_voice or fallback_voice,
        "edge_tts_rate": profile.edge_tts_rate or settings.shorts_edge_tts_rate,
        "narration_style": profile.narration_style,
        "performance_direction": _performance_direction(profile.key),
        "target_loudness_lufs": profile.target_loudness_lufs,
        "kokoro_voice": profile.kokoro_voice or settings.kokoro_voice_name,
        "kokoro_speed": profile.kokoro_speed,
    }
    elevenlabs_voice_id = _preferred_elevenlabs_voice_id(settings, voice_profile=voice_profile)
    voice_profile["elevenlabs_voice_id_configured"] = bool(elevenlabs_voice_id)
    voice_profile["elevenlabs_voice_id_source"] = _elevenlabs_voice_id_source(settings, voice_profile=voice_profile)
    voice_profile["openai_tts_voice"] = _preferred_openai_tts_voice(settings, voice_profile=voice_profile)
    voice_profile["openai_tts_voice_source"] = _openai_tts_voice_source(settings, voice_profile=voice_profile)
    return voice_profile


def _preferred_kokoro_voice(settings: Settings, *, voice_profile: dict[str, Any] | None = None) -> str:
    return str((voice_profile or {}).get("kokoro_voice") or settings.kokoro_voice_name or "af_heart").strip()


def _preferred_kokoro_speed(*, voice_profile: dict[str, Any] | None = None) -> float:
    try:
        return max(0.7, min(1.25, float((voice_profile or {}).get("kokoro_speed") or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _kokoro_language(language_hint: str = "", *, voice_profile: dict[str, Any] | None = None) -> str:
    language = str((voice_profile or {}).get("language_code") or language_hint or "en").strip().casefold()
    if language.startswith("en"):
        return "en-us"
    if language.startswith("pl"):
        return "pl"
    return language or "en-us"


def _elevenlabs_voice_ids_by_profile(settings: Settings) -> dict[str, str | None]:
    return {
        "wealth_blueprint": settings.elevenlabs_voice_id_wealth_blueprint,
        "dark_chronicles": settings.elevenlabs_voice_id_dark_chronicles,
        "sigma_squad": settings.elevenlabs_voice_id_sigma_squad,
    }


def _preferred_elevenlabs_voice_id(
    settings: Settings,
    *,
    voice_profile: dict[str, Any] | None = None,
) -> str:
    if voice_profile is not None and "elevenlabs_voice_id" in voice_profile:
        explicit = str(voice_profile["elevenlabs_voice_id"] or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", explicit):
            raise ValueError("The production voice ID is missing or invalid")
        return explicit
    profile_key = str((voice_profile or {}).get("channel_profile_key") or "").strip()
    profile_voice_id = _elevenlabs_voice_ids_by_profile(settings).get(profile_key)
    return str(profile_voice_id or settings.elevenlabs_voice_id or "").strip()


def _elevenlabs_voice_id_source(
    settings: Settings,
    *,
    voice_profile: dict[str, Any] | None = None,
) -> str:
    if voice_profile is not None and "elevenlabs_voice_id" in voice_profile:
        return "production_profile"
    profile_key = str((voice_profile or {}).get("channel_profile_key") or "").strip()
    profile_voice_id = _elevenlabs_voice_ids_by_profile(settings).get(profile_key)
    if profile_key and profile_voice_id:
        return f"profile:{profile_key}"
    if settings.elevenlabs_voice_id:
        return "global"
    return "missing"


def _preferred_elevenlabs_language_code(
    settings: Settings,
    *,
    voice_profile: dict[str, Any] | None = None,
) -> str:
    profile_language = str((voice_profile or {}).get("language_code") or "").strip()
    if profile_language:
        return profile_language
    return str(settings.elevenlabs_language_code or "").strip()


def _openai_tts_voices_by_profile(settings: Settings) -> dict[str, str]:
    return {
        "wealth_blueprint": settings.openai_tts_voice_wealth_blueprint,
        "dark_chronicles": settings.openai_tts_voice_dark_chronicles,
        "sigma_squad": settings.openai_tts_voice_sigma_squad,
    }


def _preferred_openai_tts_voice(
    settings: Settings,
    *,
    voice_profile: dict[str, Any] | None = None,
) -> str:
    profile_key = str((voice_profile or {}).get("channel_profile_key") or "").strip()
    profile_voice = _openai_tts_voices_by_profile(settings).get(profile_key)
    return str(profile_voice or settings.openai_tts_voice or "marin").strip()


def _openai_tts_voice_source(
    settings: Settings,
    *,
    voice_profile: dict[str, Any] | None = None,
) -> str:
    profile_key = str((voice_profile or {}).get("channel_profile_key") or "").strip()
    profile_voice = _openai_tts_voices_by_profile(settings).get(profile_key)
    if profile_key and profile_voice:
        return f"profile:{profile_key}"
    if settings.openai_tts_voice:
        return "global"
    return "default"


def _openai_tts_instructions(*, voice_profile: dict[str, Any] | None = None) -> str:
    profile_key = str((voice_profile or {}).get("channel_profile_key") or "").strip()
    style = str((voice_profile or {}).get("narration_style") or "").strip()
    direction = str((voice_profile or {}).get("performance_direction") or _performance_direction(profile_key)).strip()
    if style:
        return (
            f"Speak like a {style}. {direction} "
            "Prioritize retention: the first sentence must sound like a hook, not a neutral read. "
            "Use crisp diction, varied emphasis, controlled pacing, and natural micro-pauses. "
            "Avoid robotic cadence, flat intonation, audiobook monotony, and overacting."
        )
    return (
        f"{direction} Speak with clear, natural narration suitable for a high-retention YouTube Short. "
        "Make the first sentence more energetic than the middle, with crisp diction and no robotic cadence."
    )


def _performance_direction(profile_key: str) -> str:
    if profile_key == "wealth_blueprint":
        return (
            "Use a confident modern finance-creator delivery: urgent but trustworthy, conversational, "
            "with subtle emphasis on contrast words like 'most people', 'but', and 'this is why'."
        )
    if profile_key == "dark_chronicles":
        return (
            "Use cinematic suspense pacing: low controlled tension, short pauses before reveals, "
            "serious documentary energy, never cartoonish or melodramatic."
        )
    if profile_key == "sigma_squad":
        return (
            "Use fast punchy meme timing: expressive, sharp, and rhythmic, with quick emphasis changes, "
            "but keep every word understandable and safe for YouTube."
        )
    if profile_key == "history":
        return "Use documentary curiosity pacing with mild suspense and clear emphasis on surprising facts."
    if profile_key == "brainrot":
        return "Use playful energetic meme timing with fast rhythm, but keep it understandable and not chaotic."
    if profile_key == "finance":
        return "Use confident educational delivery with practical urgency and clear contrast between problem and solution."
    return "Use a direct, high-retention Shorts delivery with energy in the first three seconds."


def _preferred_edge_voice(
    settings: Settings,
    text: str,
    *,
    language_hint: str = "",
    voice_profile: dict[str, Any] | None = None,
) -> str:
    profile_voice = str((voice_profile or {}).get("edge_tts_voice") or "").strip()
    if profile_voice:
        return profile_voice
    normalized_hint = language_hint.strip().casefold()
    if normalized_hint.startswith("en"):
        return settings.shorts_edge_tts_voice_en
    if normalized_hint.startswith("pl"):
        return settings.shorts_edge_tts_voice_pl
    if _looks_english(text) or settings.youtube_default_language.casefold().startswith("en"):
        return settings.shorts_edge_tts_voice_en
    return settings.shorts_edge_tts_voice_pl


def _preferred_edge_rate(settings: Settings, *, voice_profile: dict[str, Any] | None = None) -> str:
    profile_rate = str((voice_profile or {}).get("edge_tts_rate") or "").strip()
    if profile_rate:
        return profile_rate
    return settings.shorts_edge_tts_rate


def _looks_english(text: str) -> bool:
    lowered = f" {text.casefold()} "
    english_markers = (
        " the ",
        " and ",
        " you ",
        " your ",
        " this ",
        " that ",
        " money ",
        " history ",
        " follow ",
        " save ",
        " why ",
        " what ",
    )
    polish_markers = (
        " że ",
        " nie ",
        " jest ",
        " jak ",
        " dlaczego ",
        " pieniadze ",
        " pieniądze ",
        " historia ",
        " obserwuj ",
        " jeśli ",
        " jezeli ",
    )
    english_score = sum(1 for marker in english_markers if marker in lowered)
    polish_score = sum(1 for marker in polish_markers if marker in lowered)
    return english_score > polish_score


def _safe_http_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}: {response.text[:220]}"
        message = ""
        if isinstance(payload, dict):
            raw_error = payload.get("error") or payload.get("detail")
            if isinstance(raw_error, dict):
                code = str(raw_error.get("code") or raw_error.get("status") or "").strip()
                text = str(raw_error.get("message") or "").strip()
                message = f"{code}: {text}" if code and text else text or code
            elif isinstance(raw_error, str):
                message = raw_error
        return f"HTTP {response.status_code}: {message[:260] or 'request failed'}"
    return str(exc)[:300]


def _http_error_status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return int(exc.response.status_code)
    return None


def _is_retryable_openai_tts_error(exc: Exception) -> bool:
    status_code = _http_error_status_code(exc)
    if status_code not in OPENAI_TTS_RETRYABLE_STATUS_CODES:
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        code, message = _openai_error_code_and_message(exc.response)
        normalized = f"{code} {message}".casefold()
        hard_failures = (
            "insufficient_quota",
            "billing",
            "hard limit",
            "hard_limit",
            "invalid_api_key",
            "unauthorized",
            "forbidden",
            "permission",
        )
        if any(term in normalized for term in hard_failures):
            return False
    return True


def _openai_error_code_and_message(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "", str(response.text or "")
    if not isinstance(payload, dict):
        return "", str(response.text or "")
    raw_error = payload.get("error") or payload.get("detail")
    if isinstance(raw_error, dict):
        return (
            str(raw_error.get("code") or raw_error.get("status") or "").strip(),
            str(raw_error.get("message") or "").strip(),
        )
    if isinstance(raw_error, str):
        return "", raw_error
    return "", str(response.text or "")


def _openai_tts_retry_delay_seconds(exc: Exception, *, attempt: int) -> float:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return 0.01
    retry_after = None
    if isinstance(exc, httpx.HTTPStatusError):
        raw = exc.response.headers.get("retry-after")
        if raw:
            try:
                retry_after = float(raw)
            except ValueError:
                retry_after = None
    fallback = OPENAI_TTS_RETRY_BASE_DELAY_SEC * (2 ** max(0, attempt - 1))
    delay = retry_after if retry_after is not None else fallback
    return min(max(delay, 0.25), OPENAI_TTS_RETRY_MAX_DELAY_SEC)
