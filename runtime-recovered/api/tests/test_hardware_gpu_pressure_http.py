"""Configured residency observation must neither start a CLI nor control models."""
from unittest.mock import Mock

import httpx
import pytest

from test_support import build_test_settings
from atnos.services.hardware_guard_service import HardwareGuardService


def probe(tmp_path, monkeypatch, handler, *, base_url="http://127.0.0.1:11435"):
    settings = build_test_settings(tmp_path)
    settings.ollama_base_url = base_url
    requests, options = [], []
    original_client = httpx.Client
    def respond(request):
        requests.append(request)
        return handler(request)
    def make_client(**kwargs):
        options.append(kwargs)
        return original_client(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr("atnos.services.hardware_guard_service.httpx.Client", make_client)
    monkeypatch.setattr("atnos.services.hardware_guard_service.subprocess.run",
                        Mock(side_effect=AssertionError("No process may be launched by a model probe")))
    result = HardwareGuardService(None, settings).release_gpu_pressure()
    return result, requests, options


def test_only_configured_endpoint_is_observed_with_bounded_nonproxy_client(tmp_path, monkeypatch):
    result, requests, options = probe(tmp_path, monkeypatch, lambda _: httpx.Response(200, json={"models": []}),
                                      base_url="http://127.0.0.1:11435/")
    assert result["status"] == "noop" and result["verified_released"] is True
    assert [(req.method, str(req.url)) for req in requests] == [("GET", "http://127.0.0.1:11435/api/ps")]
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    timeout = options[0]["timeout"]
    assert 0 < timeout.connect <= 2 and 0 < timeout.read <= 2 and 0 < timeout.pool <= 2


def test_resident_models_are_not_unloaded_or_mistaken_for_idle(tmp_path, monkeypatch):
    result, requests, _ = probe(tmp_path, monkeypatch, lambda _: httpx.Response(200, json={"models": [
        {"name": "qwen3:14b"}, {"model": "gemma3:4b"}, {"name": "qwen3:14b"},
    ]}))
    assert result["status"] == "deferred" and result["verified_released"] is False
    assert result["active_models_before"] == ["qwen3:14b", "gemma3:4b"]
    assert result["active_models_after"] is None and result["stopped_models"] == []
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("payload", [None, {}, [], {"models": None}, {"models": {}},
                                     {"models": [None]}, {"models": [{}]}, {"models": [{"name": 2}]}])
def test_malformed_json_schema_cannot_verify_release(tmp_path, monkeypatch, payload):
    result, requests, _ = probe(tmp_path, monkeypatch, lambda _: httpx.Response(200, json=payload))
    assert result["status"] == "failed" and result["verified_released"] is False
    assert result["stopped_models"] == [] and len(requests) == 1


@pytest.mark.parametrize("response", [httpx.Response(200, content=b"not json"),
                                      httpx.Response(500, text="private diagnostic"),
                                      httpx.Response(302, headers={"Location": "http://127.0.0.1:11434/api/ps"})])
def test_bad_response_or_redirect_is_not_followed_or_reported_as_release(tmp_path, monkeypatch, response):
    result, requests, _ = probe(tmp_path, monkeypatch, lambda _: response)
    assert result["status"] == "failed" and result["verified_released"] is False
    assert len(requests) == 1 and requests[0].url.port == 11435
    assert "private diagnostic" not in result["message"]


@pytest.mark.parametrize("exception", [httpx.ReadTimeout("private token"), httpx.ConnectError("connection refused")])
def test_timeout_and_offline_do_not_claim_released(tmp_path, monkeypatch, exception):
    def fail(_):
        raise exception
    result, requests, _ = probe(tmp_path, monkeypatch, fail)
    assert result["status"] == "failed" and result["verified_released"] is False
    assert result["stopped_models"] == [] and len(requests) == 1
    assert "private token" not in result["message"]


@pytest.mark.parametrize("endpoint", ["", "not a url", "file:///E:/fake", "http://", "http://x/api?token=secret",
                                      "http://x/#secret", "http://name:secret@127.0.0.1:11435"])
def test_invalid_configuration_makes_no_request(tmp_path, monkeypatch, endpoint):
    result, requests, options = probe(tmp_path, monkeypatch, lambda _: pytest.fail("must not request"), base_url=endpoint)
    assert result["status"] == "failed" and result["verified_released"] is False
    assert not requests and not options and "secret" not in result["message"]


def test_response_size_is_bounded(tmp_path, monkeypatch):
    result, _, _ = probe(tmp_path, monkeypatch, lambda _: httpx.Response(200, content=b" " * 262145))
    assert result["status"] == "failed" and result["verified_released"] is False


def test_slow_stream_exceeding_total_budget_is_rejected(tmp_path, monkeypatch):
    clock = iter([0.0, 6.1])
    monkeypatch.setattr("atnos.services.hardware_guard_service.monotonic", lambda: next(clock))
    result, _, _ = probe(tmp_path, monkeypatch, lambda _: httpx.Response(200, json={"models": []}))
    assert result["status"] == "failed" and result["verified_released"] is False
