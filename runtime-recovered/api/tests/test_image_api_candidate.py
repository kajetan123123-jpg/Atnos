"""Single-call production image API contract, with no network or real vault."""

from __future__ import annotations

import base64
from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from atnos.core.config import Settings
from atnos.services.creative_asset_operator_service import (
    CreativeAssetOperatorService,
    CreativeAssetRequest,
)


FAKE_SECRET = "sk-proj-candidate-unit-test-not-a-real-key-123456"


def _request() -> CreativeAssetRequest:
    return CreativeAssetRequest(
        raw_content="Create a WB scene",
        brief="Hand-drawn navy contours, cream paper, one open pay envelope. No lettering.",
        graphic_type="generic_image",
        needs_text_overlay=False,
        prefers_generative_art=True,
        prefers_photorealism=False,
        prefers_external_image_api=True,
        prefers_browser_image_tool=False,
    )


def _image_payload(*, mode="RGB", color=(20, 38, 51)):
    buffer = BytesIO()
    Image.new(mode, (1024, 1536), color).save(buffer, format="PNG")
    return {"data": [{"b64_json": base64.b64encode(buffer.getvalue()).decode("ascii")}],
            "size": "1024x1536", "quality": "high", "background": "opaque", "output_format": "png"}


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    """A real artifact service under pytest's E: root, a fake vault and HTTP."""
    settings = Settings(_env_file=None, app_root=tmp_path, data_dir=tmp_path / "data").resolve_paths()
    resolved_accounts = []
    accounts = SimpleNamespace(
        list_accounts=lambda: [SimpleNamespace(id=2, provider_key="openai-cloud", status="configured", has_secret=True)],
        resolve_secret_for_adapter=lambda account_id: resolved_accounts.append(account_id) or FAKE_SECRET,
    )
    service = CreativeAssetOperatorService(settings, account_credentials=accounts)
    calls = []
    clients = []
    handles = []
    scenario = {"status": 200, "payload": _image_payload(), "request_id": "req_candidate_test", "transport_error": False}

    class FakeClient:
        def __init__(self, **kwargs):
            clients.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            for field, (filename, handle, media_type) in kwargs.get("files", []):
                assert field == "image[]"
                assert not handle.closed
                assert handle.read(8)
                handle.seek(0)
                handles.append(handle)
            wire_request = httpx.Request("POST", url, headers=kwargs.get("headers"),
                                         json=kwargs.get("json") or kwargs.get("data"))
            if scenario["transport_error"]:
                raise httpx.ReadTimeout(f"transport echoes {FAKE_SECRET}", request=wire_request)
            return httpx.Response(scenario["status"], json=scenario["payload"],
                                  headers={"x-request-id": scenario["request_id"]}, request=wire_request)

    monkeypatch.setattr("atnos.services.creative_asset_operator_service.httpx.Client", FakeClient)
    return SimpleNamespace(service=service, calls=calls, clients=clients, handles=handles,
                           scenario=scenario, root=tmp_path, resolved_accounts=resolved_accounts)


def _generate(adapter, **kwargs):
    return adapter.service.generate_image_api_candidate(
        request=_request(), allow_external_fallback=True, **kwargs,
    )


def test_one_generation_preserves_the_production_prompt_and_provenance(adapter):
    payload = adapter.scenario["payload"]
    payload["model"] = "gpt-image-1.5"
    payload["usage"] = {"input_tokens": 35, "output_tokens": 900, "total_tokens": 935,
                        "input_tokens_details": {"text_tokens": 35, "image_tokens": 0, "credential": FAKE_SECRET},
                        "credential": FAKE_SECRET}
    payload["data"][0]["revised_prompt"] = f"A scene. {FAKE_SECRET} Bearer confidential-token"
    result = _generate(adapter)

    assert len(adapter.calls) == 1
    url, arguments = adapter.calls[0]
    assert url.endswith("/images/generations")
    assert arguments["json"] == {
        "model": "gpt-image-1.5", "prompt": _request().brief, "size": "1024x1536",
        "quality": "high", "output_format": "png", "background": "opaque", "n": 1,
    }
    assert "files" not in arguments
    assert result.model_name == "gpt-image-1.5"
    assert result.settings["model"] == "gpt-image-1.5"
    assert result.request_id == "req_candidate_test"
    assert result.prompt == _request().brief
    assert result.reference_paths == ()
    assert result.usage == {"input_tokens": 35, "output_tokens": 900, "total_tokens": 935,
                            "input_tokens_details": {"text_tokens": 35, "image_tokens": 0}}
    assert adapter.resolved_accounts == [2]
    assert adapter.clients[0]["timeout"].read == 300
    assert adapter.clients[0]["timeout"].connect == 120
    assert adapter.clients[0]["follow_redirects"] is False
    assert result.descriptor.absolute_path.is_relative_to(adapter.root)
    with Image.open(result.descriptor.absolute_path) as image:
        assert image.size == (1024, 1536)
        assert image.getpixel((0, 0)) == (20, 38, 51)
    assert FAKE_SECRET not in json.dumps(asdict(result), default=str)
    assert "confidential-token" not in result.revised_prompt
    for evidence in adapter.root.rglob("*.json"):
        assert FAKE_SECRET not in evidence.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", [401, 404, 429])
def test_http_failure_is_one_call_with_no_model_fallback_or_secret_in_error(adapter, status):
    adapter.scenario.update(status=status, payload={"error": {"code": "invalid_api_key",
                              "message": f"Incorrect API key provided: {FAKE_SECRET}"}})
    with pytest.raises(httpx.HTTPStatusError) as raised:
        _generate(adapter)
    error = raised.value
    assert len(adapter.calls) == 1
    assert error.response.status_code == status
    assert error.request.url.path == "/v1/images/generations"
    assert "authorization" not in error.request.headers
    assert FAKE_SECRET not in str(error)
    assert FAKE_SECRET not in repr(error)
    assert FAKE_SECRET.encode() not in error.response.content
    assert FAKE_SECRET.encode() not in error.request.content
    assert error.__context__ is None
    assert not list(adapter.root.rglob("*.png"))
    for evidence in adapter.root.rglob("*.json"):
        assert FAKE_SECRET not in evidence.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", [200, 401])
def test_reference_edits_use_verified_files_and_close_handles_on_success_and_failure(adapter, status):
    paths = [adapter.root / "reference.png", adapter.root / "reference.jpg"]
    Image.new("RGB", (128, 128), (30, 50, 70)).save(paths[0])
    Image.new("RGB", (128, 128), (230, 215, 195)).save(paths[1])
    originals = [path.read_bytes() for path in paths]
    adapter.scenario["status"] = status
    if status == 200:
        result = _generate(adapter, reference_paths=paths)
        assert result.reference_paths == tuple(str(path.resolve()) for path in paths)
        assert result.settings["input_fidelity"] == "high"
        assert result.model_name == "gpt-image-1.5"
    else:
        with pytest.raises(httpx.HTTPStatusError):
            _generate(adapter, reference_paths=paths)
    assert len(adapter.calls) == 1
    url, arguments = adapter.calls[0]
    assert url.endswith("/images/edits")
    assert "json" not in arguments
    assert arguments["data"]["model"] == "gpt-image-1.5"
    assert arguments["data"]["input_fidelity"] == "high"
    assert arguments["data"]["n"] == "1"
    assert arguments["data"]["prompt"] == _request().brief
    assert [file[1][2] for file in arguments["files"]] == ["image/png", "image/jpeg"]
    assert len(adapter.handles) == 2 and all(handle.closed for handle in adapter.handles)
    assert [path.read_bytes() for path in paths] == originals


def test_transport_timeout_is_not_retried_and_retains_no_authenticated_request(adapter):
    adapter.scenario["transport_error"] = True
    with pytest.raises(httpx.ReadTimeout) as raised:
        _generate(adapter)
    assert len(adapter.calls) == 1
    assert FAKE_SECRET not in str(raised.value)
    assert "authorization" not in raised.value.request.headers
    assert raised.value.__context__ is None


@pytest.mark.parametrize("problem", ["not_image", "wrong_format", "too_many"])
def test_invalid_references_fail_before_any_provider_request(adapter, problem):
    source = adapter.root / "reference.png"
    if problem == "not_image":
        source.write_bytes(b"not a PNG")
    elif problem == "wrong_format":
        Image.new("RGB", (128, 128)).save(source, format="BMP")
    else:
        Image.new("RGB", (128, 128)).save(source)
    paths = [source] * (3 if problem == "too_many" else 1)
    with pytest.raises(ValueError):
        _generate(adapter, reference_paths=paths)
    assert adapter.calls == []
    assert adapter.resolved_accounts == []


def test_external_policy_must_be_explicitly_authorized(adapter):
    with pytest.raises(RuntimeError, match="policy"):
        adapter.service.generate_image_api_candidate(request=_request())
    with pytest.raises(RuntimeError, match="Runtime"):
        _generate(adapter, runtime_controls=SimpleNamespace(external_actions_enabled=False))
    assert adapter.calls == []
    assert adapter.resolved_accounts == []


@pytest.mark.parametrize("payload", [
    {"data": [{"b64_json": "not an image"}]},
    {"data": []},
    _image_payload(mode="RGBA", color=(230, 215, 195, 0)),
])
def test_invalid_or_transparent_output_does_not_become_an_artifact(adapter, payload):
    adapter.scenario["payload"] = payload
    with pytest.raises(ValueError, match="valid opaque PNG"):
        _generate(adapter)
    assert len(adapter.calls) == 1
    assert not list(adapter.root.rglob("*.png"))
