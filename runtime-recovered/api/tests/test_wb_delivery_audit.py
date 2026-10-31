from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atnos.production import wb_delivery_audit as audit
from atnos.api.routes import wb_delivery_audit as routes


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def sign_manifest(order):
    data = audit.read_json(order / "project_manifest.json")
    data.pop("content_hash", None)
    data["content_hash"] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                                    separators=(",", ":")).encode()).hexdigest()
    put(order / "project_manifest.json", data)
    semantic = audit.read_json(order / "semantic_qa.json")
    semantic["reports"]["s01"]["manifest_sha256"] = audit.digest(order / "project_manifest.json")
    put(order / "semantic_qa.json", semantic)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app = tmp_path / "app"
    order = tmp_path / "order"
    order.mkdir()
    video = order / "final.mp4"
    video.write_bytes(b"fake video; probe stubbed")
    vh = audit.digest(video)
    words = [{"text": "Save", "start_sec": 0, "end_sec": .5, "segment_id": "s01"},
             {"text": "first.", "start_sec": .5, "end_sec": 1.5, "segment_id": "s01"}]
    put(order / "project_manifest.json", {"channel": "wealth_blueprint", "language_code": "en",
        "brand": {"background": "#142633"}, "scenes": [{"scene_id": "s01", "narration": "Save first.",
        "caption_words": words, "timing": {"start_sec": 0, "end_sec": 2}, "layout_id": "fullscreen_statement",
        "animation_presets": ["TEXT_FADE"], "assets": []}]})
    put(order / "script.json", {"segments": [{"id": "s01", "text": "Save first.", "claim_ids": []}], "claims": []})
    put(order / "captions.json", {"groups": [words]})
    put(order / "audio_timestamps.json", {"word_timestamps": words})
    put(order / "edit.json", {"renderer": "remotion", "fallback_used": False, "sha256": vh, "output_file": str(video)})
    put(order / "media_qa.json", {"status": "passed", "video_sha256": vh})
    cover = order / "cover.png"
    cover.write_bytes(b"fake cover")
    put(order / "covers.json", {"portrait": str(cover), "landscape": str(cover)})
    put(order / "semantic_qa.json", {"status": "passed", "reports": {
        "s01": {"status": "passed", "video_sha256": vh},
        "cover_portrait": {"status": "passed", "sha256": audit.digest(cover)},
        "cover_landscape": {"status": "passed", "sha256": audit.digest(cover)}}})
    put(order / "sound_mix_notes.json", {"executed": True})
    put(order / "sound_mix_plan.json", {"clips": []})
    library = app / "runtime-recovered/api/config/asset_libraries/wealth_blueprint"
    put(library / "catalog.json", {"assets": []})
    put(library / "sounds/catalog.json", {"assets": []})
    put(app / "runtime-recovered/api/config/visual_profiles/wealth_blueprint_visual_v1.json",
        {"palette": {"roles": {"background": "#142633"}}})
    sign_manifest(order)
    probe = {"streams": [{"codec_type": "audio"}, {"codec_type": "video", "codec_name": "h264",
        "width": 1080, "height": 1920, "avg_frame_rate": "30/1"}], "format": {"duration": "2.0"},
        "loudness": {"input_i": "-14", "input_tp": "-1.8"}}
    monkeypatch.setattr(audit, "probe_final", lambda *args: copy.deepcopy(probe))
    return SimpleNamespace(order=order, app=app, library=library, video=video, probe=probe)


def run(f, **kwargs):
    return audit.audit_order(f.order, app_root=f.app, catalog_path=f.library / "catalog.json",
        sound_catalog_path=f.library / "sounds/catalog.json", ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"), **kwargs)


def codes(report):
    return {r["code"] for r in report["blockers"]}


def test_automatic_pass_never_invents_listening(fixture):
    report = run(fixture)
    assert report["blockers"] == []
    assert report["status"] == "awaiting_editorial_review"
    assert report["production_accepted"] is False


def test_covers_only_cannot_accept_video(fixture):
    semantic = audit.read_json(fixture.order / "semantic_qa.json")
    semantic["reports"].pop("s01")
    put(fixture.order / "semantic_qa.json", semantic)
    assert "scene_qa_binding" in codes(run(fixture))


def test_changed_final_invalidates_qa(fixture):
    fixture.video.write_bytes(b"changed render")
    assert {"video_hash", "scene_qa_binding", "media_qa_binding"} <= codes(run(fixture))


def test_caption_omission_blocked_even_if_legacy_validator_allows_substring(fixture):
    path = fixture.order / "project_manifest.json"
    manifest = audit.read_json(path)
    manifest["scenes"][0]["caption_words"] = manifest["scenes"][0]["caption_words"][:1]
    put(path, manifest)
    sign_manifest(fixture.order)
    assert {"caption_content", "caption_stage_binding"} <= codes(run(fixture))


def test_correct_arithmetic_without_source_classification_is_blocked(fixture):
    path = fixture.order / "script.json"
    script = audit.read_json(path)
    script["claims"] = [{"id": "c1", "operation": "sum", "operands": [100, 200], "result": 300,
                         "assumptions": "Monthly example"}]
    put(path, script)
    result = run(fixture)
    assert "claim_evidence" in codes(result)
    assert "calculation" not in codes(result)


def test_bad_arithmetic_blocked(fixture):
    path = fixture.order / "script.json"
    script = audit.read_json(path)
    script["claims"] = [{"id": "c1", "operation": "sum", "operands": [100, 200], "result": 400}]
    put(path, script)
    assert "calculation" in codes(run(fixture))


def test_external_fact_without_snapshot_or_validity_blocked(fixture):
    path = fixture.order / "script.json"
    script = audit.read_json(path)
    script["segments"][0]["fact_ids"] = ["f1"]
    put(path, script)
    put(fixture.order / "finance_evidence.json", {"script_sha256": audit.digest(path), "facts": {
        "f1": {"source_url": "https://example.com/facts", "statement": "A URL is not verification."}}})
    assert {"source_snapshot", "source_metadata", "source_freshness"} <= codes(run(fixture))


def test_approved_placeholder_still_rejected(fixture):
    path = fixture.library / "placeholders/alex.png"
    path.parent.mkdir()
    path.write_bytes(b"not a production character")
    put(fixture.library / "catalog.json", {"assets": [{"id": "alex", "file_path": "placeholders/alex.png",
        "channel": "wealth_blueprint", "approved": True, "quality_status": "APPROVED", "type": "character",
        "production_role": "character_cutout", "license_status": "owned", "reviewed_by": "fixture", "reviewed_at": "now"}]})
    inventory = audit.library_inventory(fixture.library / "catalog.json", fixture.app)
    assert inventory["eligible_by_metadata"] == 0
    assert inventory["assets"][0]["reasons"] == ["placeholder_not_production"]


def test_final_mix_peak_is_measured_not_inferred_from_voice(fixture):
    fixture.probe["loudness"]["input_tp"] = "0.4"
    assert "final_mix_peak" in codes(run(fixture))


def test_render_missing_layout_and_animation_detected(fixture):
    path = fixture.order / "project_manifest.json"
    manifest = audit.read_json(path)
    manifest["scenes"][0].pop("layout_id")
    manifest["scenes"][0].pop("animation_presets")
    put(path, manifest)
    sign_manifest(fixture.order)
    assert {"layout_transferred", "motion_transferred"} <= codes(run(fixture))


def test_review_binds_exact_version_and_requires_all_controls(fixture):
    report = run(fixture)
    review = {"reviewed_by": "editor", "reviewed_at": "2026-09-19T18:00:00Z", "verdict": "approved",
              "full_video_watched": True, "full_audio_listened": True, "financial_content_reviewed": True,
              "video_sha256": report["evidence"]["video_sha256"], "evidence_sha256": report["evidence_sha256"],
              "scenes": {"s01": {k: "passed" for k in ("visual", "content", "captions", "audio")}}}
    assert run(fixture, human_review=review)["status"] == "accepted"
    review["full_audio_listened"] = False
    assert run(fixture, human_review=review)["status"] == "awaiting_editorial_review"
    review["full_audio_listened"] = True
    put(fixture.library / "sounds/catalog.json", {"assets": [], "revision": 2})
    assert run(fixture, human_review=review)["status"] == "awaiting_editorial_review"


def test_report_output_escapes_text_and_never_overwrites(fixture, tmp_path):
    report = run(fixture)
    report["scenes"][0]["narration"] = '<script>alert("bad")</script>'
    output = tmp_path / "new-report"
    audit.write_report(report, output)
    assert "<script>" not in (output / "review.html").read_text(encoding="utf-8")
    assert audit.read_json(output / "editorial-review.template.json")["full_audio_listened"] is False
    with pytest.raises(ValueError, match="immutable"):
        audit.write_report(report, output)
    with pytest.raises(ValueError, match="outside"):
        audit.write_report(report, fixture.order / "new-report")


def client_for(fixture, monkeypatch, *, live=False):
    class Store:
        def __init__(self, settings): pass
        def get(self, job_id):
            if job_id != "known": raise KeyError(job_id)
            return {"request": {"project": "wealth_blueprint"}, "worker_live": live}
        def context(self, job_id): return SimpleNamespace(artifact_dir=fixture.order)
    monkeypatch.setattr(routes, "ProductionOrderStore", Store)
    monkeypatch.setattr(routes, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(routes, "resolve_ffprobe", lambda _: "ffprobe")
    app = FastAPI()
    app.state.settings = SimpleNamespace(app_root=fixture.app)
    app.include_router(routes.router)
    return TestClient(app)


def test_api_exposes_real_audit_without_marking_order_ready(fixture, monkeypatch):
    client = client_for(fixture, monkeypatch)
    assert client.get("/api/production/wealth-blueprint/library-audit").status_code == 200
    reply = client.post("/api/production/orders/known/delivery-audit", json={})
    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "awaiting_editorial_review"
    assert reply.json()["production_order_modified"] is False
    assert Path(reply.json()["report_path"]).is_file()
    assert client.post("/api/production/orders/unknown/delivery-audit", json={}).status_code == 404


def test_api_does_not_audit_running_worker(fixture, monkeypatch):
    client = client_for(fixture, monkeypatch, live=True)
    assert client.post("/api/production/orders/known/delivery-audit", json={}).status_code == 409
