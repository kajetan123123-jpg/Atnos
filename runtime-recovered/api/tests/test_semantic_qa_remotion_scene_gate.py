"""Semantic QA must audit Remotion film scenes — covers alone cannot PASS."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
import pytest

from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_manifest import (
    BrandSpec,
    ProjectManifest,
    QAAction,
    QAResult,
    SceneManifest,
    SceneType,
    TimeRange,
    deterministic_hash,
    save_project_manifest,
)
from atnos.production.steps import order_delivery, order_repair
from atnos.production.steps.common import write_json
from test_production_order_store import store, create
from test_production_order_script import script


def _minimal_manifest(job_id: str, scenes: list[tuple[str, float, float]]) -> ProjectManifest:
    now = datetime.now(UTC).isoformat()
    built = []
    for scene_id, start, end in scenes:
        material = {"scene_id": scene_id, "timing": [start, end]}
        built.append(
            SceneManifest(
                scene_id=scene_id,
                scene_type=SceneType.HOOK if scene_id.endswith("01") else SceneType.NUMBER,
                timing=TimeRange(start_sec=start, end_sec=end),
                narration=f"Narration for {scene_id}",
                caption=f"Caption {scene_id}",
                headline=f"Headline {scene_id}",
                visual_prompt=f"Show the intended visual for {scene_id}",
                input_hash=deterministic_hash(material),
            )
        )
    return ProjectManifest(
        manifest_id=f"manifest-{job_id}",
        job_id=job_id,
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="wb-order",
        topic_title="Give your next payday a job",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="wealth_blueprint_style_v1",
        renderer_version="test",
        workflow_version="test",
        voice_version="test",
        brand=BrandSpec(),
        status="order_remotion",
        current_step="semantic_qa",
        scenes=built,
        qa=QAResult(status="pending", action=QAAction.WARN),
        created_at=now,
        updated_at=now,
    )


def _pass_verdict():
    return {"status": "pass", "contract_satisfied": True, "confidence": 9, "violations": []}


def _fail_verdict():
    return {"status": "fail", "contract_satisfied": False, "confidence": 9,
            "violations": ["Scene imagery does not match narration"]}


def _install_vision(monkeypatch, *, scene_verdict_by_id=None, default_scene=None, cover_pass=True):
    scene_verdict_by_id = scene_verdict_by_id or {}
    default_scene = default_scene or _pass_verdict()
    extract_calls = []

    def extract(video, output, **kwargs):
        extract_calls.append({"video": str(video), "output": str(output), **kwargs})
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (270, 480), "green").save(output)
        return output

    def inspect_contract(settings, path, **kwargs):
        name = Path(path).stem.replace("_contact", "")
        return scene_verdict_by_id.get(name, default_scene)

    def inspect_cover(*_a, **_k):
        if cover_pass:
            return {"status": "pass", "passed": True}
        return {"status": "fail", "passed": False}

    monkeypatch.setattr(order_delivery.visual_semantic_audit, "extract_storyboard_contact_sheet", extract)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_contract_compliance", inspect_contract)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_semantics", inspect_cover)
    return extract_calls


def _prepare_remotion_artifacts(ex, script, *, scene_specs, video_bytes=b"remotion-final-mp4-v1"):
    artifact_dir = ex.context.artifact_dir
    video = artifact_dir / "assets" / "final_short.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(video_bytes)
    job_id = artifact_dir.name
    manifest = _minimal_manifest(job_id, scene_specs)
    manifest_path = artifact_dir / "project_manifest.json"
    save_project_manifest(manifest_path, manifest)
    ex.context.artifacts["script"] = script.model_dump()
    ex.context.artifacts["visuals"] = {
        "scenes": [
            {
                "segment_id": scene_id,
                "narration": f"Narration for {scene_id}",
                "kind": "footage",
                "semantic_goal": f"Show the intended visual for {scene_id}",
                "headline": f"Headline {scene_id}",
            }
            for scene_id, _, _ in scene_specs
        ]
    }
    ex.context.artifacts["edit"] = {
        "output_file": str(video),
        "sha256": file_hash(video),
        "clips": [],
        "renderer": "remotion",
        "manifest_path": str(manifest_path),
        "manifest_sha256": file_hash(manifest_path),
        "semantic_scene_source": "project_manifest_mp4",
        "semantic_scene_windows": [
            {"scene_id": scene_id, "start_sec": start, "end_sec": end}
            for scene_id, start, end in scene_specs
        ],
    }
    return video, manifest_path


def _seed_checked_script(store, job, token, script):
    # These fixtures contain no numeric claims. Persist their script so the
    # real financial media admission guard can inspect it instead of mocking
    # that guard or exercising an unrelated missing-ledger failure.
    assert store.begin(job, token, "script", {}) is None
    result = write_json(store.context(job).artifact_dir / "script.json", script.model_dump(mode="json"))
    store.complete(job, token, "script", result.payload, [result.artifact_path])


def test_covers_alone_without_scene_reports_are_not_ready():
    reports = {
        "cover_portrait": {"status": "passed", "verdict": {"status": "pass", "passed": True}},
        "cover_landscape": {"status": "passed", "verdict": {"status": "pass", "passed": True}},
    }
    assert order_delivery.scene_reports_complete(
        reports,
        expected_scene_ids=["s01", "s02"],
        expected_bindings={
            "s01": {"video_sha256": "abc", "manifest_sha256": "m1", "clip_sha256": "abc"},
            "s02": {"video_sha256": "abc", "manifest_sha256": "m1", "clip_sha256": "abc"},
        },
    ) is False


def test_good_covers_but_missing_scene_reports_fail_remotion_stage(store, script, monkeypatch):
    """Regression: empty edit.clips must not let covers alone return PASS."""
    job = create(store)["id"]
    extract_calls = _install_vision(monkeypatch)
    with store.lease(job) as token:
        _seed_checked_script(store, job, token, script)
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        asyncio.run(order_delivery.produce_covers(ex))
        _prepare_remotion_artifacts(ex, script, scene_specs=[("s01", 0.0, 2.0), ("s02", 2.0, 4.0)])
        # Simulate the old bug path: only cover reports present in the completeness gate.
        only_covers = {
            "cover_portrait": {"status": "passed", "verdict": {"status": "pass", "passed": True}},
            "cover_landscape": {"status": "passed", "verdict": {"status": "pass", "passed": True}},
        }
        assert order_delivery.scene_reports_complete(
            only_covers,
            expected_scene_ids=["s01", "s02"],
            expected_bindings={
                "s01": {"video_sha256": "x", "manifest_sha256": "y", "clip_sha256": "x"},
                "s02": {"video_sha256": "x", "manifest_sha256": "y", "clip_sha256": "x"},
            },
        ) is False
        result = asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert result["status"] == "passed"
        assert set(result["expected_scene_ids"]) == {"s01", "s02"}
        assert "s01" in result["reports"] and "s02" in result["reports"]
        assert len(extract_calls) == 2
        assert result["scene_source"] == "remotion_manifest_mp4"


def test_one_bad_scene_blocks_remotion_semantic_ready(store, script, monkeypatch):
    job = create(store)["id"]
    _install_vision(monkeypatch, scene_verdict_by_id={"s02": _fail_verdict()})
    with store.lease(job) as token:
        _seed_checked_script(store, job, token, script)
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        asyncio.run(order_delivery.produce_covers(ex))
        _prepare_remotion_artifacts(ex, script, scene_specs=[("s01", 0.0, 2.0), ("s02", 2.0, 4.0)])
        result = asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert result["status"] == "failed"
        assert result["reports"]["s01"]["status"] == "passed"
        assert result["reports"]["s02"]["status"] == "failed"
        assert result["reports"]["cover_portrait"]["status"] == "passed"


def test_mp4_swapped_after_audit_rejects_stale_scene_reports(store, script, monkeypatch):
    job = create(store)["id"]
    _install_vision(monkeypatch)
    with store.lease(job) as token:
        _seed_checked_script(store, job, token, script)
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        asyncio.run(order_delivery.produce_covers(ex))
        video, manifest_path = _prepare_remotion_artifacts(
            ex, script, scene_specs=[("s01", 0.0, 2.5)], video_bytes=b"original-remotion-bytes"
        )
        first = asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert first["status"] == "passed"
        stale = first["reports"]["s01"]
        assert stale["video_sha256"] == file_hash(video)
        video.write_bytes(b"swapped-remotion-bytes-after-audit")
        live = {
            "s01": {
                "video_sha256": file_hash(video),
                "clip_sha256": file_hash(video),
                "manifest_sha256": file_hash(manifest_path),
            }
        }
        assert order_delivery.scene_reports_complete(
            first["reports"],
            expected_scene_ids=["s01"],
            expected_bindings=live,
        ) is False
        second = asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert second["reports"]["s01"]["video_sha256"] == file_hash(video)
        assert second["reports"]["s01"]["video_sha256"] != stale["video_sha256"]
        assert second["status"] == "passed"


def test_full_positive_remotion_scene_set_passes(store, script, monkeypatch):
    job = create(store)["id"]
    extract_calls = _install_vision(monkeypatch)
    with store.lease(job) as token:
        _seed_checked_script(store, job, token, script)
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        asyncio.run(order_delivery.produce_covers(ex))
        video, manifest_path = _prepare_remotion_artifacts(
            ex, script, scene_specs=[("s01", 0.0, 2.0), ("s02", 2.0, 4.0), ("s03", 4.0, 6.0)]
        )
        result = asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert result["status"] == "passed"
        assert len(result["expected_scene_ids"]) == 3
        assert len(extract_calls) == 3
        for scene_id in ("s01", "s02", "s03"):
            report = result["reports"][scene_id]
            assert report["status"] == "passed"
            assert report["video_sha256"] == file_hash(video)
            assert report["manifest_sha256"] == file_hash(manifest_path)
            assert report["scene_id"] == scene_id
            assert "timing" in report
        for index, call in enumerate(extract_calls):
            assert call['start_frame'] == index * 60
            assert call['end_frame_exclusive'] == (index + 1) * 60
            assert call['frame_rate'] == 30
            report = result['reports'][f's{index + 1:02}']
            assert report['frame_window'] == {'start_frame': index * 60,
                'end_frame_exclusive': (index + 1) * 60, 'frame_rate': 30}
            assert report['sample_frame_indices'][0] == index * 60
            assert report['sample_frame_indices'][-1] == (index + 1) * 60 - 1


def test_editorial_clip_path_still_works(store, script, monkeypatch):
    job = create(store)["id"]
    extract_calls = _install_vision(monkeypatch)
    with store.lease(job) as token:
        _seed_checked_script(store, job, token, script)
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        asyncio.run(order_delivery.produce_covers(ex))
        clip = ex.context.artifact_dir / "test_fixture.mp4"
        clip.write_bytes(b"editorial clip bytes")
        ex.context.artifacts["edit"] = {"clips": [{"segment_id": "s01", "path": str(clip)}]}
        ex.context.artifacts["visuals"] = {
            "scenes": [{
                "segment_id": "s01",
                "narration": script.segments[0].text,
                "kind": "footage",
                "semantic_goal": "Show banking",
                "headline": "Your next step",
            }]
        }
        result = asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert result["status"] == "passed"
        assert result["scene_source"] == "editorial_clips"
        assert result["reports"]["s01"]["clip_sha256"] == file_hash(clip)
        assert len(extract_calls) == 1
        assert extract_calls[0].get("start_sec") is None
        assert 'start_frame' not in extract_calls[0]


def test_remotion_negative_scene_requires_bound_manifest_report_without_editorial_clip_lookup(store, script, monkeypatch):
    job = create(store)["id"]
    with store.lease(job) as token:
        _seed_checked_script(store, job, token, script)
        ex = StageExecutor(store, job, token)
        _prepare_remotion_artifacts(ex, script, scene_specs=[("s01", 0.0, 2.0)])
        report = {
            "status": "failed",
            "clip_sha256": "unused",
            "verdict": {"status": "fail", "violations": ["unrelated imagery"]},
        }
        with pytest.raises(ProductionBlocked, match="current manifest and actual MP4"):
            asyncio.run(order_repair.repair_scene(ex, "s01", report))


@pytest.mark.parametrize('actual_stage', ['access', 'unsupported'])
def test_author_handoff_rejects_wrong_stage_before_any_model_or_cached_pass(store, script, actual_stage):
    job = create(store)['id']
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        _, manifest_path = _prepare_remotion_artifacts(ex, script, scene_specs=[('s01',0.,3.)])
        ex.context.artifacts['script']={'segments':[{'id':'s01-practical','text':'Name this specific repair cost now.',
            'visual_intent':'identify CAR REPAIR object and show READY RESERVE nearby.'}]}
        from atnos.production.project_manifest import load_project_manifest
        manifest=load_project_manifest(manifest_path,verify_hash=True)
        manifest.scenes[0].narration='Name this specific repair cost now.'
        manifest.scenes[0].semantic_overlay_kind='reserve_readiness'
        manifest.scenes[0].visual_relation={'kind':'reserve_readiness','stage':actual_stage}
        save_project_manifest(manifest_path,manifest)
        before=store.get(job)
        with pytest.raises(ValueError,match='Author visual handoff mismatch'):
            asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert store.get(job)['provider_calls']==before['provider_calls']
        assert not any(row['stage'].startswith('semantic/') for row in store.get(job)['checkpoints'])


def test_matching_handoff_enters_real_frame_audit_contract(store, script):
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token)
        _, manifest_path=_prepare_remotion_artifacts(ex,script,scene_specs=[('s01',0.,3.)])
        directed='show READY RESERVE with questions about access conditions.'
        ex.context.artifacts['script']={'segments':[{'id':'s01','text':'Check how you access these funds today.',
                                                   'visual_intent':directed}]}
        from atnos.production.project_manifest import load_project_manifest
        manifest=load_project_manifest(manifest_path,verify_hash=True)
        manifest.scenes[0].semantic_overlay_kind='reserve_readiness'
        manifest.scenes[0].visual_relation={'kind':'reserve_readiness','stage':'access'}
        save_project_manifest(manifest_path,manifest)
        targets=order_delivery.resolve_semantic_scene_targets(ex.context)
        assert directed in targets[0]['contract']
        assert targets[0]['source']=='remotion_manifest_mp4'


@pytest.mark.parametrize('version',['wealth-blueprint-reference-v0.39','wealth-blueprint-reference-v0.40'])
def test_known_review_reset_versions_require_a_real_new_render_before_cached_pass(store,script,version):
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token)
        _,path=_prepare_remotion_artifacts(ex,script,scene_specs=[('s01',0.,3.)])
        ex.context.artifacts['production_order']['topic']='Emergency fund'
        ex.context.artifacts['script']={'segments':[{'id':'s01','text':'Review how these roles separate.',
            'visual_intent':'review separation of the two financial objects in a wide shot.'}]}
        from atnos.production.project_manifest import load_project_manifest
        manifest=load_project_manifest(path,verify_hash=True)
        manifest.renderer_version=version
        manifest.scenes[0].semantic_overlay_kind='reserve_readiness'
        manifest.scenes[0].visual_relation={'kind':'reserve_readiness','stage':'review'}
        save_project_manifest(path,manifest)
        before=store.get(job)['provider_calls']
        with pytest.raises(ValueError,match='continuity mismatch'):
            asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert store.get(job)['provider_calls']==before
        manifest.renderer_version='wealth-blueprint-reference-v0.41'
        save_project_manifest(path,manifest)
        assert order_delivery.resolve_semantic_scene_targets(ex.context)[0]['source']=='remotion_manifest_mp4'
