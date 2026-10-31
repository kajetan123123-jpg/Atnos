"""A scoped local voice replaces billing dependency, never the audio validators."""
from types import SimpleNamespace

import pytest

from atnos.production.order_execution import _voice_preflight_settings
from atnos.production.order_store import ProductionBlocked
from atnos.production import local_voice_lane
from atnos.production.steps import order_voice
from atnos.production import media_tools
from atnos.services import whisper_cpp_subtitle_service


def setup_runtime(monkeypatch, *, local, local_available=True, whisper=True, ffmpeg=True, api_key=""):
    settings = SimpleNamespace(elevenlabs_api_key=api_key, shorts_allow_cloud_tts=True)
    executor = SimpleNamespace(store=SimpleNamespace(settings=settings), context=SimpleNamespace(job_id="prod-pilot"))
    monkeypatch.setattr(order_voice, "voice_settings", lambda *_: settings)
    monkeypatch.setattr(media_tools, "resolve_ffmpeg", lambda: "ffmpeg.exe" if ffmpeg else None)
    monkeypatch.setattr(whisper_cpp_subtitle_service, "WhisperCppSubtitleService",
                        lambda *_: SimpleNamespace(runtime_availability=lambda: SimpleNamespace(available=whisper)))
    monkeypatch.setattr(local_voice_lane, "load_local_voice_lane", lambda *_: object() if local else None)
    monkeypatch.setattr(local_voice_lane, "verify_runtime_availability", lambda: {"available": local_available})
    return executor, settings


def test_registered_verified_local_voice_does_not_require_cloud_key(monkeypatch):
    executor, settings = setup_runtime(monkeypatch, local=True)
    assert _voice_preflight_settings(executor, {"voice": {}}) is settings


def test_no_opt_in_still_requires_existing_cloud_key(monkeypatch):
    executor, _ = setup_runtime(monkeypatch, local=False)
    with pytest.raises(ProductionBlocked, match="ElevenLabs"):
        _voice_preflight_settings(executor, {"voice": {}})


def test_disabled_cloud_blocks_even_with_key_before_provider(monkeypatch):
    executor, settings = setup_runtime(monkeypatch, local=False, api_key="configured")
    settings.shorts_allow_cloud_tts = False
    with pytest.raises(ProductionBlocked, match="cloud TTS is disabled"):
        _voice_preflight_settings(executor, {"voice": {}})


def test_missing_local_alignment_or_runtime_blocks_before_synthesis(monkeypatch):
    executor, _ = setup_runtime(monkeypatch, local=True, local_available=False, api_key="configured")
    with pytest.raises(ProductionBlocked, match="local voice runtime or alignment"):
        _voice_preflight_settings(executor, {"voice": {}})


@pytest.mark.parametrize("missing", ["whisper", "ffmpeg"])
def test_local_candidate_cannot_bypass_independent_audio_tools(monkeypatch, missing):
    executor, _ = setup_runtime(monkeypatch, local=True, **{missing: False})
    with pytest.raises(ProductionBlocked, match="independent Whisper"):
        _voice_preflight_settings(executor, {"voice": {}})
