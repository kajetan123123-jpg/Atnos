from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from atnos.production.steps import voice_synthesis
from test_support import build_test_settings


@pytest.mark.parametrize("timestamps", [False, True])
@pytest.mark.parametrize("model", ["eleven_v3", "eleven_multilingual_v2", "eleven_flash_v2_5"])
def test_model_capability_controls_stitching_without_changing_voice_text_or_timing(monkeypatch, tmp_path, model, timestamps):
    settings = build_test_settings(tmp_path)
    settings.shorts_allow_cloud_tts = True
    settings.elevenlabs_api_key = "test-secret-key"
    settings.elevenlabs_model_id = model
    settings.elevenlabs_stability = .5
    settings.elevenlabs_speed = 1.0
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        if timestamps:
            return httpx.Response(200, json={"audio_base64": base64.b64encode(b"ID3" + b"x" * 2048).decode(),
                                             "alignment": {"characters": ["A"]}})
        return httpx.Response(200, content=b"ID3" + b"x" * 2048)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient",
                        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    text = "A pay raise can disappear into another recurring expense."
    result = asyncio.run(voice_synthesis._try_elevenlabs_tts(
        settings, text=text, output_path=tmp_path / "provider.mp3", with_timestamps=timestamps,
        voice_profile={"channel_profile_key": "wealth_blueprint", "elevenlabs_voice_id": "frozen-roger",
                       "language_code": "en"},
        previous_text="The previous complete sentence.", next_text="Keep part available for a goal.",
        pronunciation_dictionary_locators=[{"pronunciation_dictionary_id": "dictionary-one", "version_id": "version-one"}],
    ))
    payload = captured[0]
    assert payload["text"] == text
    assert payload["model_id"] == model
    assert payload["voice_settings"]["stability"] == .5
    assert payload["voice_settings"]["speed"] == 1.0
    assert payload["pronunciation_dictionary_locators"][0]["version_id"] == "version-one"
    assert result["ok"] is True
    assert result["voice_id"] == "frozen-roger"
    assert bool(result["alignment"]) == timestamps
    if model == "eleven_v3":
        assert "previous_text" not in payload and "next_text" not in payload
        assert result["request_stitching"] == {"supported": False, "context_applied": False,
                                               "omitted_parameters": ["previous_text", "next_text"]}
    else:
        assert payload["previous_text"] == "The previous complete sentence."
        assert payload["next_text"] == "Keep part available for a goal."
        assert result["request_stitching"]["context_applied"] is True


@pytest.mark.parametrize("json_response", [True, False])
def test_provider_error_retains_reason_but_never_credentials_or_failed_media(monkeypatch, tmp_path, json_response):
    settings = build_test_settings(tmp_path)
    settings.shorts_allow_cloud_tts = True
    settings.elevenlabs_api_key = "exact-test-secret-123"
    settings.elevenlabs_voice_id = "frozen-roger"
    settings.elevenlabs_model_id = "eleven_v3"
    calls = []

    def respond(request):
        calls.append(request)
        message = "Stitching is unavailable. exact-test-secret-123 api_key=other-secret"
        if json_response:
            return httpx.Response(400, json={"detail": {"status": "invalid_request", "message": message}})
        return httpx.Response(400, text=message)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(voice_synthesis.httpx, "AsyncClient",
                        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    output = tmp_path / "provider.mp3"
    result = asyncio.run(voice_synthesis._try_elevenlabs_tts(
        settings, text="The complete spoken sentence.", output_path=output, next_text="Following text."))
    assert result["ok"] is False
    assert result["status_code"] == 400
    assert "Stitching is unavailable" in result["error"]
    assert "exact-test-secret-123" not in json.dumps(result)
    assert "other-secret" not in json.dumps(result)
    assert result["model_id"] == "eleven_v3"
    assert len(calls) == 1  # Unsupported payload/auth errors are not retried silently.
    assert not output.exists()
