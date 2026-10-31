import json
from types import SimpleNamespace

import httpx
import pytest

from atnos.production import asset_observation
from atnos.production.project_profile import VisualQAProfile


@pytest.mark.parametrize("count,truncated", [(8, False), (10, False), (8, True)])
def test_observation_bounds_feedback_and_original_response_are_retained(tmp_path, monkeypatch, count, truncated):
    sheet = tmp_path / "frames.png"
    sheet.write_bytes(b"fixture")
    monkeypatch.setattr(asset_observation.visual_semantic_audit, "_encode_audit_image",
                        lambda _: ("AA==", {"source_sha256": "fixture-hash"}))
    observation = {"summary": "A person uses a calculator beside household bills.",
                   "visible_objects": ["calculator"], "visible_actions": ["pressing buttons"],
                   "readable_text": [str(i) for i in range(count)],
                   "limitations": "Only sampled frames are visible; text is uncertain."}
    def response(request):
        body = json.loads(request.content)
        fields = body["format"]["properties"]
        assert fields["visible_objects"]["maxItems"] == 12
        assert fields["visible_actions"]["maxItems"] == fields["readable_text"]["maxItems"] == 8
        assert "readable_text had ten items" in body["messages"][0]["content"]
        return httpx.Response(200, json={"done_reason": "length" if truncated else "stop",
                                        "message": {"content": json.dumps(observation)}})
    client = httpx.Client
    monkeypatch.setattr(asset_observation.httpx, "Client",
                        lambda **kwargs: client(transport=httpx.MockTransport(response), **kwargs))
    def run():
        return asset_observation.describe_frames(SimpleNamespace(ollama_base_url="http://local.invalid"),
            sheet, VisualQAProfile(), retry_feedback="readable_text had ten items")
    if count > 8 or truncated:
        with pytest.raises(ValueError):
            run()
    else:
        result = run()
        assert result["observation"] == observation
        assert result["model_call_path"]
    traces = list(tmp_path.glob("frames.model-*.json"))
    assert len(traces) == 1
    trace = json.loads(traces[0].read_text())
    assert trace["status"] == "response_received"
    assert json.loads(trace["response"]["message"]["content"]) == observation
    assert trace["audit_input"]["source_sha256"] == "fixture-hash"
