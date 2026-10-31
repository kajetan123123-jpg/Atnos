from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
import sys
import wave

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import voice_synthesis
from test_support import build_test_settings


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="job-voice-1",
        account_id="finance-pl",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="finance-pl",
            title="Inflacja zjada oszczednosci",
            description="Testowy temat z finansow.",
            total_score=80,
        ),
        artifact_dir=tmp_path / "job-voice-1",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "Inflacja zjada oszczednosci szybciej niz myslisz.",
                        "duration_target_sec": 2,
                    },
                    {
                        "id": "s02",
                        "type": "body",
                        "text": "Sprawdz realna wartosc pieniedzy zanim wybierzesz lokate.",
                        "duration_target_sec": 2,
                    },
                ]
            }
        },
    )


def test_f5_timeout_cleanup_never_falls_back_to_pid_only_taskkill(monkeypatch) -> None:
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("PID-only taskkill must not be used")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert voice_synthesis._terminate_process_tree(123456) is False
    assert calls == []


def test_voice_synthesis_success_outputs_youtube_ready_audio_and_script_fallback_srt(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_single_pass_narration = True
    context = _context(tmp_path)

    async def fake_f5(_settings, *, text: str, output_path: Path) -> dict:
        _write_wav(output_path, duration_s=0.2, sample_rate=22050, channels=1)
        return {"engine": "f5_tts_http", "ok": True}

    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", fake_f5)

    result = asyncio.run(voice_synthesis.run(settings, context))
    payload = result.payload

    assert payload["status"] == "generated"
    assert payload["production_ready"] is True
    assert payload["single_pass_narration"] is True
    assert payload["quality_tier"] == "primary"
    assert payload["voice_backend_grade"] == "premium"
    assert payload["channel_profile"]["key"] == "finance"
    assert payload["voice_profile"]["language_code"] == "en"
    assert payload["voice_profile"]["edge_tts_voice"] == settings.shorts_edge_tts_voice_en
    assert payload["silent_segment_ids"] == []
    assert payload["subtitle_status"]["engine"] == "script_aligned_fallback"
    assert payload["subtitle_status"]["fallback_reason"] == "whisper_unavailable_or_rejected"
    assert "Inflacja zjada oszczednosci" in Path(payload["subtitle_path"]).read_text(encoding="utf-8")
    assert payload["final_normalization"]["status"] == "normalized"
    assert payload["final_normalization"]["sample_rate_hz"] == 44100
    assert payload["final_normalization"]["channels"] == 2
    _assert_wav_format(Path(payload["audio_path"]), sample_rate=44100, channels=2)


def test_elevenlabs_voice_is_generated_once_for_the_complete_short(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_tts_backend = "elevenlabs"
    context = _context(tmp_path)
    calls: list[str] = []

    async def fake_elevenlabs(_settings, *, text: str, output_path: Path, voice_profile=None, with_timestamps=False) -> dict:
        calls.append(text)
        output_path.write_bytes(b"ID3" + b"audio" * 400)
        return {"engine": "elevenlabs", "ok": True}

    async def fake_normalize(_input_path: Path, output_path: Path) -> dict:
        _write_wav(output_path, duration_s=4.0, sample_rate=44100, channels=2)
        return {"status": "normalized", **voice_synthesis._validate_wav_format(output_path)}

    monkeypatch.setattr(voice_synthesis, "_try_elevenlabs_tts", fake_elevenlabs)
    monkeypatch.setattr(voice_synthesis, "_normalize_audio", fake_normalize)

    payload = asyncio.run(voice_synthesis.run(settings, context)).payload

    assert len(calls) == 1
    assert "Inflacja zjada" in calls[0]
    assert "Sprawdz realna" in calls[0]
    assert "\n" not in calls[0]
    assert payload["primary"] == "elevenlabs_single_pass"
    assert payload["single_pass_narration"] is True
    assert {item["engine"] for item in payload["timestamps"]} == {"elevenlabs"}


def test_voice_synthesis_reuses_verified_f5_audio_for_identical_script(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_tts_backend = "f5_tts_http"
    settings.f5_tts_single_pass_narration = True
    context = _context(tmp_path)
    source_dir = tmp_path / "source-job"
    source_audio_dir = source_dir / "audio"
    source_audio = source_audio_dir / "narration.wav"
    source_raw = source_audio_dir / "narration_raw.wav"
    source_subtitles = source_audio_dir / "subtitles.whisper.srt"
    _write_wav(source_audio, duration_s=0.3, sample_rate=44100, channels=2)
    _write_wav(source_raw, duration_s=0.3, sample_rate=44100, channels=2)
    source_subtitles.write_text("1\n00:00:00,000 --> 00:00:00,300\nCached narration.\n", encoding="utf-8")
    script = context.artifacts["script"]
    (source_dir / "script.json").write_text(json.dumps(script), encoding="utf-8")
    profile = voice_synthesis.resolve_channel_profile(context)
    voice_profile = voice_synthesis._voice_profile_for_channel(settings, profile)
    (source_dir / "audio_timestamps.json").write_text(
        json.dumps(
            {
                "production_ready": True,
                "single_pass_narration": True,
                "primary": "f5_tts_single_pass",
                "voice_profile": voice_profile,
                "audio_path": str(source_audio),
                "raw_audio_path": str(source_raw),
                "subtitle_path": str(source_subtitles),
                "subtitle_status": {"engine": "whisper_cpp"},
                "engine_attempts": [{"engine": "f5_tts_http", "status": "generated"}],
            }
        ),
        encoding="utf-8",
    )

    async def f5_must_not_run(*_args, **_kwargs):
        raise AssertionError("identical verified narration should come from cache")

    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", f5_must_not_run)

    payload = asyncio.run(voice_synthesis.run(settings, context)).payload

    assert payload["cache_reused"] is True
    assert payload["cache_source_job_id"] == "source-job"
    assert payload["production_ready"] is True
    assert payload["primary"] == "f5_tts_single_pass_cache"
    assert payload["subtitle_status"]["engine"] == "script_aligned_fallback"
    assert payload["subtitle_status"]["fallback_reason"] == "cached_subtitles_failed_script_match"
    assert Path(payload["subtitle_path"]).parent == context.artifact_dir / "audio"
    assert "Inflacja zjada oszczednosci" in Path(payload["subtitle_path"]).read_text(encoding="utf-8")
    _assert_wav_format(Path(payload["audio_path"]), sample_rate=44100, channels=2)


def test_voice_synthesis_does_not_reuse_f5_audio_when_script_differs(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_tts_backend = "f5_tts_http"
    settings.f5_tts_single_pass_narration = True
    context = _context(tmp_path)
    source_dir = tmp_path / "source-job"
    source_audio_dir = source_dir / "audio"
    source_audio = source_audio_dir / "narration.wav"
    _write_wav(source_audio, duration_s=0.3, sample_rate=44100, channels=2)
    different_script = {"segments": [{"id": "s01", "text": "Different words.", "duration_target_sec": 2}]}
    (source_dir / "script.json").write_text(json.dumps(different_script), encoding="utf-8")
    profile = voice_synthesis.resolve_channel_profile(context)
    voice_profile = voice_synthesis._voice_profile_for_channel(settings, profile)
    (source_dir / "audio_timestamps.json").write_text(
        json.dumps(
            {
                "production_ready": True,
                "single_pass_narration": True,
                "primary": "f5_tts_single_pass",
                "voice_profile": voice_profile,
                "audio_path": str(source_audio),
                "raw_audio_path": str(source_audio),
                "engine_attempts": [{"engine": "f5_tts_http", "status": "generated"}],
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    async def fake_f5(_settings, *, text: str, output_path: Path) -> dict:
        nonlocal calls
        calls += 1
        _write_wav(output_path, duration_s=0.2, sample_rate=22050, channels=1)
        return {"engine": "f5_tts_http", "ok": True}

    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", fake_f5)

    payload = asyncio.run(voice_synthesis.run(settings, context)).payload

    assert calls == 1
    assert payload["cache_reused"] is False


def test_audio_normalization_preserves_voice_when_loudnorm_pass_fails(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.wav"
    output_path = tmp_path / "normalized.wav"
    _write_wav(raw_path, duration_s=0.2, sample_rate=24000, channels=1)
    calls = 0

    async def fake_filter(_ffmpeg, *, input_path: Path, output_path: Path, audio_filter: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None, "loudnorm transient failure"
        _write_wav(output_path, duration_s=0.2, sample_rate=44100, channels=2)
        return voice_synthesis._validate_wav_format(output_path), ""

    monkeypatch.setattr(voice_synthesis, "_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(voice_synthesis, "_run_ffmpeg_audio_filter", fake_filter)

    result = asyncio.run(voice_synthesis._normalize_audio(raw_path, output_path))

    assert result["status"] == "normalized"
    assert result["engine"] == "ffmpeg_format_fallback"
    assert result["loudness_target_applied"] is False
    assert result["warning"] == "loudnorm transient failure"
    _assert_wav_format(output_path, sample_rate=44100, channels=2)


def test_ffmpeg_exe_uses_project_media_tool_resolver(monkeypatch) -> None:
    expected = r"E:\Atnos\Tools\FFmpeg\bin\ffmpeg.exe"
    monkeypatch.setattr(voice_synthesis, "resolve_ffmpeg", lambda: expected)

    assert voice_synthesis._ffmpeg_exe() == expected


def test_ffmpeg_filter_uses_threaded_subprocess_for_server_event_loops(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.wav"
    output_path = tmp_path / "normalized.wav"
    _write_wav(raw_path, duration_s=0.2, sample_rate=24000, channels=1)

    def fake_run(cmd, **kwargs):
        _write_wav(output_path, duration_s=0.2, sample_rate=44100, channels=2)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(voice_synthesis.subprocess, "run", fake_run)

    result, error = asyncio.run(
        voice_synthesis._run_ffmpeg_audio_filter(
            "ffmpeg",
            input_path=raw_path,
            output_path=output_path,
            audio_filter="aresample=44100",
        )
    )

    assert error == ""
    assert result is not None
    assert result["ok"] is True


def test_voice_synthesis_marks_silent_fallback_as_not_production_ready(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_require_premium_voice_before_publication = False
    context = _context(tmp_path)

    async def failed_engine(_settings, *, text: str, output_path: Path) -> dict:
        return {"engine": "f5_tts_http", "ok": False, "error": "offline"}

    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", failed_engine)
    monkeypatch.setattr(voice_synthesis, "_try_coqui_tts", failed_engine)
    monkeypatch.setattr(voice_synthesis, "_try_pyttsx3", failed_engine)

    result = asyncio.run(voice_synthesis.run(settings, context))
    payload = result.payload

    assert payload["production_ready"] is False
    assert payload["quality_tier"] == "placeholder_silence"
    assert payload["silent_segment_ids"] == ["s01", "s02"]
    assert payload["status"] == "generated_fallback_silence"
    assert payload["final_normalization"]["sample_rate_hz"] == 44100
    assert payload["final_normalization"]["channels"] == 2


def test_voice_synthesis_marks_pyttsx3_as_draft_not_production_ready(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_require_premium_voice_before_publication = False
    context = _context(tmp_path)

    async def failed_engine(_settings, *, text: str, output_path: Path) -> dict:
        return {"engine": "f5_tts_http", "ok": False, "error": "offline"}

    async def fake_pyttsx3(_settings, *, text: str, output_path: Path) -> dict:
        _write_wav(output_path, duration_s=0.2, sample_rate=22050, channels=1)
        return {"engine": "pyttsx3", "ok": True}

    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", failed_engine)
    monkeypatch.setattr(voice_synthesis, "_try_coqui_tts", failed_engine)
    monkeypatch.setattr(voice_synthesis, "_try_pyttsx3", fake_pyttsx3)

    result = asyncio.run(voice_synthesis.run(settings, context))
    payload = result.payload

    assert payload["status"] == "generated"
    assert payload["quality_tier"] == "offline_system_fallback"
    assert payload["production_ready"] is False
    assert payload["silent_segment_ids"] == []
    assert payload["final_normalization"]["sample_rate_hz"] == 44100
    assert payload["final_normalization"]["channels"] == 2


def test_voice_synthesis_rejects_partial_whisper_hallucination() -> None:
    timestamps = [
        {
            "text": "They danced until they died in Strasbourg while the city watched in horror.",
        },
        {
            "text": "Credibility comes first because one false detail breaks the entire atmosphere.",
        },
    ]
    hallucinated_srt = (
        "1\n00:00:00,000 --> 00:00:04,000\n"
        "W 1518 wsrod ludziom ktorzy wciaz stadzaja sie.\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\n"
        "Credibility comes first because one false detail breaks.\n"
    )

    score = voice_synthesis._srt_script_match_score(hallucinated_srt, timestamps)

    assert score["accepted"] is False
    assert score["overlap_ratio"] < 0.72


def test_voice_synthesis_accepts_script_faithful_whisper_output() -> None:
    timestamps = [
        {"text": "Start with $100 and a hypothetical 5% annual rate."},
        {"text": "After year one, the balance is $105."},
    ]
    faithful_srt = (
        "1\n00:00:00,000 --> 00:00:04,000\n"
        "Start with $100 and a hypothetical 5% annual rate.\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\n"
        "After year one, the balance is $105.\n"
    )

    score = voice_synthesis._srt_script_match_score(faithful_srt, timestamps)

    assert score["accepted"] is True
    assert score["sequence_ratio"] == 1.0
    assert score["numeric_claims_match"] is True


def test_voice_synthesis_rejects_numeric_claim_corruption_even_with_high_word_overlap() -> None:
    timestamps = [
        {"text": "Watch the balance move from $100 to $105 to $110.25 as each period compounds the larger base."}
    ]
    corrupted_srt = (
        "1\n00:00:00,000 --> 00:00:08,000\n"
        "Watch the balance move from $100 to $105 to $10.25 as each period compounds the larger base.\n"
    )

    score = voice_synthesis._srt_script_match_score(corrupted_srt, timestamps)

    assert score["overlap_ratio"] >= 0.90
    assert score["sequence_ratio"] >= 0.90
    assert score["numeric_claims_match"] is False
    assert score["accepted"] is False


def test_coqui_auto_install_uses_versioned_tts_package(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.app_env = "development"
    settings.shorts_tts_backend = "coqui_tts"
    installed: list[str] = []

    def fake_find_spec(name: str):
        assert name == "TTS"
        return None

    async def fake_install(package: str) -> bool:
        installed.append(package)
        return False

    monkeypatch.setattr(voice_synthesis.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(voice_synthesis, "_auto_install", fake_install)

    result = asyncio.run(
        voice_synthesis._try_coqui_tts(
            settings,
            text="Test",
            output_path=tmp_path / "coqui.wav",
        )
    )

    assert result["ok"] is False
    assert installed == [voice_synthesis.COQUI_TTS_PACKAGE]


def test_coqui_auto_install_is_skipped_unless_backend_selected(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.app_env = "development"
    settings.shorts_tts_backend = "f5_tts_http"
    installed: list[str] = []

    def fake_find_spec(name: str):
        assert name == "TTS"
        return None

    async def fake_install(package: str) -> bool:
        installed.append(package)
        return False

    monkeypatch.setattr(voice_synthesis.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(voice_synthesis, "_auto_install", fake_install)

    result = asyncio.run(
        voice_synthesis._try_coqui_tts(
            settings,
            text="Test",
            output_path=tmp_path / "coqui.wav",
        )
    )

    assert result["ok"] is False
    assert installed == []
    assert "auto-install skipped" in result["error"]


def test_f5_backend_tries_local_f5_before_cloud_and_heavy_coqui(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)

    engines = [engine for engine, _ in voice_synthesis._engine_sequence(settings)]

    assert engines[:3] == ["f5_tts_http", "kokoro_local", "edge_tts"]
    assert engines.index("f5_tts_http") < engines.index("elevenlabs")
    assert engines.index("kokoro_local") < engines.index("edge_tts")
    assert engines.index("edge_tts") < engines.index("coqui_tts")


def test_kokoro_local_backend_is_private_quality_neural_fallback(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_require_premium_voice_before_publication = False
    context = _context(tmp_path)

    async def failed_engine(_settings, *, text: str, output_path: Path, **kwargs) -> dict:
        return {"engine": "f5_tts_http", "ok": False, "error": "offline"}

    async def fake_kokoro(_settings, *, text: str, output_path: Path, **kwargs) -> dict:
        _write_wav(output_path, duration_s=0.2, sample_rate=24000, channels=1)
        return {"engine": "kokoro_local", "ok": True, "voice_name": "af_heart"}

    monkeypatch.setattr(voice_synthesis, "_try_elevenlabs_tts", failed_engine)
    monkeypatch.setattr(voice_synthesis, "_try_openai_tts", failed_engine)
    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", failed_engine)
    monkeypatch.setattr(voice_synthesis, "_try_kokoro_local_tts", fake_kokoro)

    result = asyncio.run(voice_synthesis.run(settings, context))
    payload = result.payload

    assert payload["status"] == "generated"
    assert payload["quality_tier"] == "local_neural_fallback"
    assert payload["voice_backend_grade"] == "acceptable_neural_fallback"
    assert payload["production_ready"] is True
    assert payload["engine_attempts"][0]["engine"] == "kokoro_local"


def test_f5_timeout_is_not_retried_for_every_segment(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_require_premium_voice_before_render = False
    context = _context(tmp_path)
    f5_calls = 0

    async def slow_f5(_settings, *, text: str, output_path: Path) -> dict:
        nonlocal f5_calls
        f5_calls += 1
        return {"engine": "f5_tts_http", "ok": False, "error": "F5-TTS Gradio client timed out after 45s."}

    async def good_kokoro(_settings, *, text: str, output_path: Path, **kwargs) -> dict:
        _write_wav(output_path, duration_s=0.2, sample_rate=22050, channels=1)
        return {"engine": "kokoro_local", "ok": True}

    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", slow_f5)
    monkeypatch.setattr(voice_synthesis, "_try_kokoro_local_tts", good_kokoro)

    result = asyncio.run(voice_synthesis.run(settings, context))
    payload = result.payload

    assert f5_calls == 1
    assert payload["status"] == "generated"
    assert payload["quality_tier"] == "local_neural_fallback"
    assert payload["engine_attempts"][1]["attempts"][0]["error"] == "Skipped after earlier failure in this job."


def test_pyttsx3_auto_install_uses_versioned_package(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.app_env = "development"
    installed: list[str] = []

    def fake_find_spec(name: str):
        assert name == "pyttsx3"
        return None

    async def fake_install(package: str) -> bool:
        installed.append(package)
        return False

    monkeypatch.setattr(voice_synthesis.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(voice_synthesis, "_auto_install", fake_install)

    result = asyncio.run(
        voice_synthesis._try_pyttsx3(
            settings,
            text="Test",
            output_path=tmp_path / "pyttsx3.wav",
        )
    )

    assert result["ok"] is False
    assert installed == [voice_synthesis.PYTTSX3_PACKAGE]


def test_english_channel_profile_forces_english_edge_voice(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    context = ProductionContext(
        job_id="job-voice-en",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-en",
            account_id="yt-wealth-blueprint",
            title="Why Your Savings Account Is Quietly Losing Money",
            description="English personal finance short.",
            total_score=82,
        ),
        artifact_dir=tmp_path / "job-voice-en",
        artifacts={"script": {"segments": []}},
    )

    language_hint = voice_synthesis._language_hint_for_context(context)
    voice_profile = voice_synthesis._voice_profile_for_context(settings, context)
    voice = voice_synthesis._preferred_edge_voice(
        settings,
        "Most people wait for a perfect market, but consistency usually matters more.",
        language_hint=language_hint,
        voice_profile=voice_profile,
    )

    assert language_hint == "en"
    assert voice_profile["channel_profile_key"] == "wealth_blueprint"
    assert voice_profile["edge_tts_voice"] == "en-US-ChristopherNeural"
    assert voice_profile["edge_tts_rate"] == "+2%"
    assert voice_profile["narration_style"] == "calm confident finance educator, precise and not salesy"
    assert voice == "en-US-ChristopherNeural"


def test_channel_voice_profiles_are_distinct_for_english_accounts(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    cases = [
        ("yt-wealth-blueprint", "personal finance", "en-US-ChristopherNeural", "+2%"),
        ("yt-dark-chronicles", "dark history", "en-US-GuyNeural", "-4%"),
        ("yt-sigma-squad", "Gen Alpha brainrot", "en-US-AndrewNeural", "+10%"),
    ]

    for account_id, title, expected_voice, expected_rate in cases:
        context = ProductionContext(
            job_id=f"job-{account_id}",
            account_id=account_id,
            topic=ApprovedTopic(
                id=f"topic-{account_id}",
                account_id=account_id,
                title=title,
                description="English channel profile test.",
                total_score=80,
            ),
            artifact_dir=tmp_path / f"job-{account_id}",
            artifacts={"script": {"segments": []}},
        )

        voice_profile = voice_synthesis._voice_profile_for_context(settings, context)

        assert voice_profile["language_code"] == "en"
        assert voice_profile["edge_tts_voice"] == expected_voice
        assert voice_profile["edge_tts_rate"] == expected_rate


def test_explicit_content_profiles_map_to_english_channel_profiles(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    cases = [
        ("personal_finance", "wealth_blueprint"),
        ("historical_curiosities", "dark_chronicles"),
        ("gen_alpha_brainrot", "sigma_squad"),
    ]

    for content_profile, expected_profile in cases:
        account_id = f"youtube:new-{expected_profile}@example.test"
        context = ProductionContext(
            job_id=f"job-{expected_profile}",
            account_id=account_id,
            topic=ApprovedTopic(
                id=f"topic-{expected_profile}",
                account_id=account_id,
                title="Generic smoke topic",
                description="No profile hints in topic text.",
                total_score=80,
                content_profile=content_profile,
            ),
            artifact_dir=tmp_path / f"job-{expected_profile}",
            artifacts={"script": {"segments": []}},
        )

        voice_profile = voice_synthesis._voice_profile_for_context(settings, context)

        assert voice_profile["channel_profile_key"] == expected_profile
        assert voice_profile["language_code"] == "en"


@pytest.mark.parametrize("explicit_voice", [None, "frozen-order-voice"])
def test_elevenlabs_uses_channel_profile_voice_id_and_language(monkeypatch, tmp_path: Path, explicit_voice) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_allow_cloud_tts = True
    settings.elevenlabs_api_key = "test-api-key"
    settings.elevenlabs_voice_id = "global-voice"
    settings.elevenlabs_voice_id_wealth_blueprint = "wealth-voice"
    settings.elevenlabs_language_code = "pl"
    context = ProductionContext(
        job_id="job-elevenlabs-profile",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-elevenlabs",
            account_id="yt-wealth-blueprint",
            title="Why Your Savings Account Is Quietly Losing Money",
            description="English personal finance short.",
            total_score=82,
        ),
        artifact_dir=tmp_path / "job-elevenlabs-profile",
        artifacts={"script": {"segments": []}},
    )
    calls: list[dict] = []

    class FakeResponse:
        content = b"ID3" + (b"x" * 2048)

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, endpoint: str, *, headers: dict, json: dict):
            calls.append({"endpoint": endpoint, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient", FakeClient)
    voice_profile = voice_synthesis._voice_profile_for_context(settings, context)
    if explicit_voice:
        voice_profile["elevenlabs_voice_id"] = explicit_voice

    result = asyncio.run(
        voice_synthesis._try_elevenlabs_tts(
            settings,
            text="Most people think saving money is enough. It is not.",
            output_path=tmp_path / "segment.raw.wav",
            voice_profile=voice_profile,
        )
    )

    assert result["ok"] is True
    assert result["engine"] == "elevenlabs"
    assert result["voice_id_source"] == ("production_profile" if explicit_voice else "profile:wealth_blueprint")
    assert result["language_code"] == "en"
    assert calls[0]["endpoint"].endswith(f"/v1/text-to-speech/{explicit_voice or 'wealth-voice'}?output_format=mp3_44100_128")
    assert calls[0]["headers"]["xi-api-key"] == "test-api-key"
    assert calls[0]["json"]["language_code"] == "en"
    assert calls[0]["json"]["voice_settings"]["speed"] == settings.elevenlabs_speed
    assert voice_profile["elevenlabs_voice_id_configured"] is True
    assert voice_profile["elevenlabs_voice_id_source"] == "profile:wealth_blueprint"


def test_openai_tts_uses_channel_profile_voice_and_instructions(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_allow_cloud_tts = True
    settings.openai_api_key = "test-openai-key"
    settings.openai_tts_voice = "global"
    settings.openai_tts_voice_wealth_blueprint = "marin"
    context = ProductionContext(
        job_id="job-openai-tts-profile",
        account_id="youtube:account-c5034bf4@example.invalid",
        topic=ApprovedTopic(
            id="topic-openai-tts",
            account_id="youtube:account-c5034bf4@example.invalid",
            title="Why Your Savings Account Is Quietly Losing Money",
            description="English personal finance short.",
            total_score=82,
        ),
        artifact_dir=tmp_path / "job-openai-tts-profile",
        artifacts={"script": {"segments": []}},
    )
    calls: list[dict] = []

    class FakeResponse:
        content = b"ID3" + (b"x" * 2048)

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, endpoint: str, *, headers: dict, json: dict):
            calls.append({"endpoint": endpoint, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient", FakeClient)
    voice_profile = voice_synthesis._voice_profile_for_context(settings, context)

    result = asyncio.run(
        voice_synthesis._try_openai_tts(
            settings,
            text="Most people think saving money is enough. It is not.",
            output_path=tmp_path / "segment.raw.wav",
            voice_profile=voice_profile,
        )
    )

    assert result["ok"] is True
    assert result["engine"] == "openai_tts"
    assert result["voice"] == "marin"
    assert result["voice_source"] == "profile:wealth_blueprint"
    assert result["model_id"] == settings.openai_tts_model
    assert calls[0]["endpoint"] == "https://api.openai.com/v1/audio/speech"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-openai-key"
    assert calls[0]["json"]["voice"] == "marin"
    assert calls[0]["json"]["model"] == settings.openai_tts_model
    assert "finance educator" in calls[0]["json"]["instructions"]
    assert "first sentence must sound like a hook" in calls[0]["json"]["instructions"]
    assert "Avoid robotic cadence" in calls[0]["json"]["instructions"]
    assert voice_profile["openai_tts_voice"] == "marin"
    assert voice_profile["openai_tts_voice_source"] == "profile:wealth_blueprint"


def test_openai_tts_retries_rate_limit_before_fallback(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_allow_cloud_tts = True
    settings.openai_api_key = "test-openai-key"
    calls: list[int] = []

    class FakeResponse:
        def __init__(self, status_code: int, content: bytes = b"") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"retry-after": "1"} if status_code == 429 else {}
            self.text = "rate limited" if status_code == 429 else ""
            self.request = voice_synthesis.httpx.Request("POST", "https://api.openai.com/v1/audio/speech")

        def json(self) -> dict:
            return {"error": {"code": "rate_limit_exceeded", "message": "slow down"}}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise voice_synthesis.httpx.HTTPStatusError(
                    "rate limited",
                    request=self.request,
                    response=self,
                )

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, endpoint: str, *, headers: dict, json: dict):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return FakeResponse(429)
            return FakeResponse(200, b"ID3" + (b"x" * 2048))

    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(
        voice_synthesis._try_openai_tts(
            settings,
            text="Most people think saving money is enough. It is not.",
            output_path=tmp_path / "segment.raw.wav",
            voice_profile={},
        )
    )

    assert result["ok"] is True
    assert result["engine"] == "openai_tts"
    assert len(calls) == 2
    assert result["retry_attempts"][0]["status_code"] == 429
    assert result["retry_attempts"][0]["retryable"] is True


def test_openai_tts_does_not_retry_insufficient_quota(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_allow_cloud_tts = True
    settings.openai_api_key = "test-openai-key"
    calls: list[int] = []

    class FakeResponse:
        status_code = 429
        content = b""
        headers: dict[str, str] = {}
        text = "insufficient quota"
        request = voice_synthesis.httpx.Request("POST", "https://api.openai.com/v1/audio/speech")

        def json(self) -> dict:
            return {
                "error": {
                    "code": "insufficient_quota",
                    "message": "You exceeded your current quota, please check your plan and billing details.",
                }
            }

        def raise_for_status(self) -> None:
            raise voice_synthesis.httpx.HTTPStatusError(
                "quota",
                request=self.request,
                response=self,
            )

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, endpoint: str, *, headers: dict, json: dict):
            calls.append(len(calls) + 1)
            return FakeResponse()

    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(
        voice_synthesis._try_openai_tts(
            settings,
            text="Most people think saving money is enough. It is not.",
            output_path=tmp_path / "segment.raw.wav",
            voice_profile={},
        )
    )

    assert result["ok"] is False
    assert result["engine"] == "openai_tts"
    assert len(calls) == 1
    assert result["attempts"][0]["status_code"] == 429
    assert result["attempts"][0]["retryable"] is False
    assert "insufficient_quota" in result["error"]


def test_openai_tts_instructions_are_directed_per_channel(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    cases = [
        ("personal_finance", "confident modern finance-creator delivery"),
        ("historical_curiosities", "cinematic suspense pacing"),
        ("gen_alpha_brainrot", "fast punchy meme timing"),
    ]

    for content_profile, expected in cases:
        account_id = f"youtube:new-{content_profile}@example.test"
        context = ProductionContext(
            job_id=f"job-{account_id}",
            account_id=account_id,
            topic=ApprovedTopic(
                id=f"topic-{account_id}",
                account_id=account_id,
                title="Generic topic",
                description="Profile routing test.",
                total_score=80,
                content_profile=content_profile,
            ),
            artifact_dir=tmp_path / account_id.replace(":", "-").replace("@", "-"),
            artifacts={"script": {"segments": []}},
        )
        voice_profile = voice_synthesis._voice_profile_for_context(settings, context)
        instructions = voice_synthesis._openai_tts_instructions(voice_profile=voice_profile)

        assert expected in instructions
        assert "first sentence must sound like a hook" in instructions
        assert "robotic cadence" in instructions


def test_f5_gradio_helpers_parse_current_gradio_contract(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_base_url = "http://localhost:7860/api/predict"
    upload_payload = ["C:\\Users\\localuser\\AppData\\Local\\Temp\\gradio\\sample\\basic_ref_en.wav"]
    complete_sse = (
        "event: complete\n"
        "data: [{\"path\":\"C:\\\\Users\\\\localuser\\\\AppData\\\\Local\\\\Temp\\\\gradio\\\\out\\\\audio.wav\","
        "\"url\":\"http://127.0.0.1:7860/gradio_api/file=C:\\\\Users\\\\localuser\\\\AppData\\\\Local\\\\Temp\\\\gradio\\\\out\\\\audio.wav\"}, 123]\n"
    )

    assert voice_synthesis._f5_gradio_base_url(settings.f5_tts_base_url) == "http://localhost:7860"
    assert voice_synthesis._f5_gradio_api_name(settings) == "basic_tts"
    assert voice_synthesis._extract_gradio_upload_path(upload_payload).endswith("basic_ref_en.wav")

    complete_payload = voice_synthesis._parse_gradio_sse_complete(complete_sse)
    audio_file = voice_synthesis._extract_gradio_audio_file(complete_payload)

    assert audio_file is not None
    assert audio_file["path"].endswith("audio.wav")
    assert "/gradio_api/file=" in audio_file["url"]


def test_f5_gradio_client_helpers_extract_audio_file(tmp_path: Path) -> None:
    generated_audio = tmp_path / "generated.wav"
    _write_wav(generated_audio, duration_s=0.1, sample_rate=16000, channels=1)

    from_string = voice_synthesis._extract_gradio_client_audio_file(str(generated_audio))
    from_tuple = voice_synthesis._extract_gradio_client_audio_file(({"path": str(generated_audio)}, 123))

    assert from_string == {"path": str(generated_audio)}
    assert from_tuple == {"path": str(generated_audio), "url": None}


def test_f5_tts_prefers_direct_gradio_contract_before_legacy_predict(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    settings = build_test_settings(tmp_path)
    settings.f5_tts_base_url = "http://localhost:7860/api/predict"
    output_path = tmp_path / "out.wav"

    async def ready(_settings):
        return {"ready": True, "started": False}

    async def gradio_ok(_settings, *, endpoint: str, text: str, output_path: Path):
        _write_wav(output_path, duration_s=0.1, sample_rate=16000, channels=1)
        return {"engine": "f5_tts_http", "ok": True, "mode": "gradio"}

    monkeypatch.setattr(voice_synthesis, "_ensure_f5_runtime_ready", ready)
    monkeypatch.setattr(voice_synthesis, "_try_f5_tts_gradio", gradio_ok)

    result = asyncio.run(voice_synthesis._try_f5_tts(settings, text="hello", output_path=output_path))

    assert result["ok"] is True
    assert result["mode"] == "gradio"
    assert output_path.exists()


def test_f5_reference_audio_uses_runtime_discovery(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_install_dir = None
    settings.f5_tts_ref_audio_path = None
    reference_audio = tmp_path / "discovered-reference.wav"
    reference_audio.write_bytes(b"RIFF" + b"\x00" * 128)

    monkeypatch.setattr(
        voice_synthesis.F5TTSRuntimeService,
        "resolve_install_dir",
        lambda self: tmp_path,
    )
    monkeypatch.setattr(
        voice_synthesis.F5TTSRuntimeService,
        "resolve_reference_audio",
        lambda self, install_dir=None: reference_audio,
    )

    assert voice_synthesis._resolve_f5_ref_audio_path(settings) == reference_audio


def test_f5_cli_transport_generates_audio_without_gradio(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_transport = "cli"
    settings.f5_tts_gradio_temp_dir = None
    # Inherited global caches must not redirect Atnos's child process outside its data.
    inherited_paths = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_DATASETS_CACHE",
                       "TRANSFORMERS_CACHE", "TORCH_HOME", "XDG_CACHE_HOME", "TEMP", "TMP", "TMPDIR",
                       "GRADIO_TEMP_DIR")
    for variable in inherited_paths:
        monkeypatch.setenv(variable, str(tmp_path / "unrelated-user-cache"))
    settings.f5_tts_runtime_python = tmp_path / "python.exe"
    settings.f5_tts_runtime_python.write_bytes(b"python")
    settings.f5_tts_ref_audio_path = tmp_path / "reference.wav"
    settings.f5_tts_ref_audio_path.write_bytes(b"RIFF" + b"\x00" * 128)
    output_path = tmp_path / "generated.wav"

    monkeypatch.setattr(voice_synthesis.F5TTSRuntimeService, "resolve_install_dir", lambda self: tmp_path)
    monkeypatch.setattr(
        voice_synthesis.F5TTSRuntimeService,
        "resolve_runtime_python",
        lambda self, install_dir=None: settings.f5_tts_runtime_python,
    )
    monkeypatch.setattr(
        voice_synthesis.F5TTSRuntimeService,
        "resolve_reference_audio",
        lambda self, install_dir=None: settings.f5_tts_ref_audio_path,
    )
    monkeypatch.setattr(
        voice_synthesis.F5TTSRuntimeService,
        "stop",
        lambda self, wait_timeout_s=20: None,
    )

    def fake_run(*args, **kwargs):
        for variable in inherited_paths:
            assert Path(kwargs["env"][variable]).is_relative_to(settings.data_dir.resolve())
            assert Path(kwargs["env"][variable]).is_dir()
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        _write_wav(output_path, duration_s=0.2, sample_rate=24000, channels=1)
        return subprocess.CompletedProcess(args[0], 0, "generated", "")

    monkeypatch.setattr(voice_synthesis.subprocess, "run", fake_run)

    result = asyncio.run(voice_synthesis._try_f5_tts(settings, text="Test narration.", output_path=output_path))

    assert result["ok"] is True
    assert result["mode"] == "cli"
    assert output_path.stat().st_size > 1024


def test_f5_tts_does_not_mask_gradio_generation_failure_with_legacy_predict(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    settings = build_test_settings(tmp_path)
    settings.f5_tts_base_url = "http://localhost:7860/api/predict"

    async def ready(_settings):
        return {"ready": True, "started": False}

    async def manual_failed(_settings, *, endpoint: str, text: str, output_path: Path):
        return {
            "engine": "f5_tts_http",
            "ok": False,
            "mode": "gradio",
            "error": "peer closed connection",
            "legacy_fallback_allowed": False,
        }

    monkeypatch.setattr(voice_synthesis, "_ensure_f5_runtime_ready", ready)
    monkeypatch.setattr(voice_synthesis, "_try_f5_tts_gradio", manual_failed)

    result = asyncio.run(voice_synthesis._try_f5_tts(settings, text="hello", output_path=tmp_path / "out.wav"))

    assert result["ok"] is False
    assert result["mode"] == "gradio"
    assert result["error"] == "peer closed connection"


def test_f5_gradio_temp_recovery_accepts_one_fresh_audio(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_gradio_temp_dir = tmp_path / "gradio"
    output_path = tmp_path / "recovered.wav"
    before = voice_synthesis._snapshot_f5_gradio_audio(settings)
    generated = settings.f5_tts_gradio_temp_dir / "unique-job" / "audio.wav"
    generated.parent.mkdir(parents=True)
    request_started_ns = __import__("time").time_ns()
    _write_wav(generated, duration_s=0.2, sample_rate=24000, channels=1)

    recovered = asyncio.run(
        voice_synthesis._recover_f5_gradio_audio(
            settings,
            before=before,
            request_started_ns=request_started_ns,
            output_path=output_path,
            wait_timeout_s=1.0,
        )
    )

    assert recovered is True
    assert output_path.read_bytes() == generated.read_bytes()


def test_f5_gradio_temp_recovery_rejects_ambiguous_audio(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_gradio_temp_dir = tmp_path / "gradio"
    before = voice_synthesis._snapshot_f5_gradio_audio(settings)
    request_started_ns = __import__("time").time_ns()
    for name in ("job-a", "job-b"):
        generated = settings.f5_tts_gradio_temp_dir / name / "audio.wav"
        generated.parent.mkdir(parents=True)
        _write_wav(generated, duration_s=0.2, sample_rate=24000, channels=1)

    recovered = asyncio.run(
        voice_synthesis._recover_f5_gradio_audio(
            settings,
            before=before,
            request_started_ns=request_started_ns,
            output_path=tmp_path / "must-not-exist.wav",
            wait_timeout_s=0.1,
        )
    )

    assert recovered is False


def test_f5_gradio_event_poll_retries_after_stream_timeout(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_base_url = "http://localhost:7860/api/predict"
    ref_audio = tmp_path / "ref.wav"
    generated_audio = tmp_path / "generated.wav"
    output_path = tmp_path / "out.wav"
    _write_wav(ref_audio, duration_s=0.1, sample_rate=16000, channels=1)
    _write_wav(generated_audio, duration_s=0.1, sample_rate=16000, channels=1)
    settings.f5_tts_ref_audio_path = ref_audio

    class FakeResponse:
        def __init__(self, *, status_code: int = 200, payload=None, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text
            self.content = b""

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    complete_sse = "data: " + json.dumps(
        {
            "msg": "process_completed",
            "event_id": "evt-1",
            "output": {"success": True, "data": [{"path": str(generated_audio)}]},
        }
    )

    class FakeStreamResponse(FakeResponse):
        def __init__(self, *, fail_on_enter: bool = False) -> None:
            super().__init__()
            self.fail_on_enter = fail_on_enter

        async def __aenter__(self):
            if self.fail_on_enter:
                raise TimeoutError()
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def aiter_lines(self):
            yield complete_sse

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout
            self.stream_attempts = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str):
            return FakeResponse(
                payload={
                    "api_prefix": "/gradio_api",
                    "dependencies": [{"id": 0, "api_name": "basic_tts"}],
                }
            )

        async def post(self, url: str, **kwargs):
            if url.endswith("/upload"):
                return FakeResponse(payload=[str(ref_audio)])
            return FakeResponse(payload={"event_id": "evt-1"})

        def stream(self, *_args, **_kwargs):
            self.stream_attempts += 1
            return FakeStreamResponse(fail_on_enter=self.stream_attempts == 1)

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(voice_synthesis.asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        voice_synthesis._try_f5_tts_gradio(
            settings,
            endpoint=settings.f5_tts_base_url,
            text="This is a short test.",
            output_path=output_path,
        )
    )

    assert result["ok"] is True
    assert result["event_id"] == "evt-1"
    assert result["stream_attempts"] == 2
    assert output_path.read_bytes() == generated_audio.read_bytes()


def test_f5_gradio_event_poll_fails_after_stream_retry_budget(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_base_url = "http://localhost:7860/api/predict"
    ref_audio = tmp_path / "ref.wav"
    _write_wav(ref_audio, duration_s=0.1, sample_rate=16000, channels=1)
    settings.f5_tts_ref_audio_path = ref_audio

    class FakeResponse:
        status_code = 200

        def __init__(self, payload=None) -> None:
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class FailingStream:
        async def __aenter__(self):
            raise TimeoutError()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, _url: str):
            return FakeResponse(
                {
                    "api_prefix": "/gradio_api",
                    "dependencies": [{"id": 0, "api_name": "basic_tts"}],
                }
            )

        async def post(self, url: str, **_kwargs):
            if url.endswith("/upload"):
                return FakeResponse([str(ref_audio)])
            return FakeResponse({"event_id": "evt-1"})

        def stream(self, *_args, **_kwargs):
            return FailingStream()

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(voice_synthesis.asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        voice_synthesis._try_f5_tts_gradio(
            settings,
            endpoint=settings.f5_tts_base_url,
            text="This request never completes.",
            output_path=tmp_path / "out.wav",
        )
    )

    assert result["ok"] is False
    assert result["mode"] == "gradio_queue"
    assert result["event_id"] == "evt-1"
    assert "timed out" in result["error"].lower()


def test_f5_reference_audio_prefers_configured_path(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    ref_audio = tmp_path / "voice.wav"
    _write_wav(ref_audio, duration_s=0.1, sample_rate=16000, channels=1)
    settings.f5_tts_ref_audio_path = ref_audio

    assert voice_synthesis._resolve_f5_ref_audio_path(settings) == ref_audio


def test_f5_required_blocks_silent_or_kokoro_fallback(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_require_premium_voice_before_publication = True
    settings.shorts_require_premium_voice_before_render = True
    settings.shorts_tts_backend = "f5_tts_http"
    settings.f5_tts_single_pass_narration = True
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()

    async def fake_f5(_settings, *, text: str, output_path: Path) -> dict:
        return {"engine": "f5_tts_http", "ok": False, "error": "offline"}

    monkeypatch.setattr(voice_synthesis, "_try_f5_tts", fake_f5)

    with pytest.raises(RuntimeError, match="F5-TTS is required"):
        asyncio.run(
            voice_synthesis._try_single_pass_f5_narration(
                settings,
                script={
                    "segments": [
                        {"id": "s01", "text": "First line.", "duration_target_sec": 3},
                        {"id": "s02", "text": "Second line.", "duration_target_sec": 3},
                    ]
                },
                audio_dir=audio_dir,
                channel_profile=voice_synthesis.resolve_channel_profile(_context(tmp_path)),
                voice_profile={},
            )
        )


def test_f5_generation_timeout_uses_configured_budget(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.f5_tts_generation_timeout_sec = 600

    assert voice_synthesis._f5_tts_generation_timeout_s(settings) == 600.0


def _write_wav(path: Path, *, duration_s: float, sample_rate: int, channels: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(duration_s * sample_rate)
    silence = b"\x00\x00" * channels * frames
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(silence)


def _assert_wav_format(path: Path, *, sample_rate: int, channels: int) -> None:
    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == sample_rate
        assert wav.getnchannels() == channels
        assert wav.getnframes() > 0
