from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.project_manifest import file_sha256
from atnos.production.stability_runner import (
    StabilityRunError,
    _probe_final_media,
    _resolve_artifact_path,
    _require_frozen_gates,
    _validate_approved_topics,
    _validate_topic_ids,
    _verify_manifest_assets,
    _require_unique_run_receipt,
    _verify_semantic_storyboard_evidence,
    _verify_required_scene_coverage,
    load_stability_report,
    run_stability_series,
)
from test_support import create_test_app


def test_stability_requires_exactly_ten_distinct_topics() -> None:
    with pytest.raises(StabilityRunError, match="exactly 10"):
        _validate_topic_ids([f"topic-{index}" for index in range(9)])
    with pytest.raises(StabilityRunError, match="distinct"):
        _validate_topic_ids(["duplicate"] * 10)


def test_stability_rejects_reused_job_and_final_video() -> None:
    prior = [{"passed": True, "job_id": "job-1", "final_video_sha256": "a" * 64}]
    with pytest.raises(StabilityRunError, match="reused a production job"):
        _require_unique_run_receipt(
            prior,
            {"passed": True, "job_id": "job-1", "final_video_sha256": "b" * 64},
        )
    with pytest.raises(StabilityRunError, match="reused a final video"):
        _require_unique_run_receipt(
            prior,
            {"passed": True, "job_id": "job-2", "final_video_sha256": "a" * 64},
        )


def test_stability_scene_contract_requires_every_declared_scene() -> None:
    _verify_required_scene_coverage(
        {"HookScene", "ImageScene", "ChartScene", "CTAScene"},
        ["HookScene", "ImageScene", "ChartScene", "CTAScene"],
    )
    with pytest.raises(StabilityRunError, match="missing required scenes"):
        _verify_required_scene_coverage(
            {"HookScene", "ImageScene", "CTAScene"},
            ["HookScene", "ImageScene", "ChartScene", "CTAScene"],
        )


def test_stability_binds_semantic_audit_to_storyboard_bytes(tmp_path: Path) -> None:
    storyboard = tmp_path / "qa_storyboard_contact_sheet.jpg"
    storyboard.write_bytes(b"authenticated-storyboard")
    qa = {
        "semantic_storyboard_audit": {
            "status": "pass",
            "passed": True,
            "storyboard_path": str(storyboard),
            "audit_path": str(tmp_path / "semantic_storyboard_audit.json"),
            "model": "qwen3-vl:8b",
            "audit_input": {
                "source_size_bytes": storyboard.stat().st_size,
                "source_sha256": hashlib.sha256(storyboard.read_bytes()).hexdigest(),
            },
        }
    }

    evidence = _verify_semantic_storyboard_evidence(qa, artifact_dir=tmp_path)
    assert evidence["status"] == "verified"
    assert evidence["storyboard_sha256"] == hashlib.sha256(storyboard.read_bytes()).hexdigest()

    storyboard.write_bytes(b"tampered-storyboard")
    with pytest.raises(StabilityRunError, match="hash does not match"):
        _verify_semantic_storyboard_evidence(qa, artifact_dir=tmp_path)


def test_stability_rejects_semantic_storyboard_outside_job(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-storyboard.jpg"
    outside.write_bytes(b"outside")
    qa = {
        "semantic_storyboard_audit": {
            "status": "pass",
            "passed": True,
            "storyboard_path": str(outside),
            "audit_input": {
                "source_size_bytes": outside.stat().st_size,
                "source_sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            },
        }
    }
    with pytest.raises(StabilityRunError, match="escapes the job directory"):
        _verify_semantic_storyboard_evidence(qa, artifact_dir=tmp_path)


def test_stability_preflight_blocks_without_signed_owner_gates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings

    with pytest.raises(StabilityRunError, match="Gate A production voice approval is missing") as exc_info:
        _require_frozen_gates(settings)
    assert "Gates B-C accepted visual reference baseline is missing" in str(exc_info.value)
    assert "Errno" not in str(exc_info.value)


def test_stability_rejects_ten_ids_that_repeat_the_same_topic_title(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, _params):
            return self

        def fetchall(self):
            return [
                {
                    "id": f"topic-{index}",
                    "title": "The Same Topic",
                    "inspired_by": json.dumps({"contract": "wealth_blueprint_v1_stability_source_v1"}),
                    "status": "approved",
                }
                for index in range(10)
            ]

    monkeypatch.setattr("atnos.production.stability_runner.connect", lambda _settings: FakeConnection())

    with pytest.raises(StabilityRunError, match="distinct non-empty titles"):
        _validate_approved_topics(
            settings,
            "youtube:wealth-blueprint",
            [f"topic-{index}" for index in range(10)],
        )


def test_stability_persists_ten_verified_receipts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings
    topics = [f"topic-{index:02d}" for index in range(10)]

    monkeypatch.setattr(
        "atnos.production.stability_runner._require_frozen_gates",
        lambda _settings: {"reference_id": "WEALTH_BLUEPRINT_REFERENCE_001"},
    )
    monkeypatch.setattr(
        "atnos.production.stability_runner._validate_approved_topics",
        lambda _settings, _account_id, topic_ids: {
            topic_id: {"visual_test_focus": [], "production_brief": {"expected_scene_evidence": []}}
            for topic_id in topic_ids
        },
    )
    monkeypatch.setattr(
        "atnos.production.stability_runner._verify_completed_run",
        lambda result, **_kwargs: {
            "status": "passed",
            "passed": True,
            "job_id": result["id"],
            "final_video_sha256": hashlib.sha256(result["topic_id"].encode()).hexdigest(),
        },
    )
    monkeypatch.setattr("atnos.production.stability_runner._revalidate_run_receipt", lambda *_args, **_kwargs: None)

    class FakeOrchestrator:
        async def start(self, *, account_id: str, topic_id: str) -> dict[str, str]:
            return {"id": f"job-{topic_id}", "account_id": account_id, "topic_id": topic_id}

    output_dir = tmp_path / "stability-pass"
    report = asyncio.run(
        run_stability_series(
            settings,
            account_id="youtube:wealth-blueprint",
            topic_ids=topics,
            output_dir=output_dir,
            orchestrator_factory=lambda _settings: FakeOrchestrator(),
        )
    )

    persisted = load_stability_report(output_dir / "stability_report.json")
    assert report["status"] == "verified"
    assert report["passed"] is True
    assert report["consecutive_passes"] == 10
    assert len(report["runs"]) == 10
    assert persisted["runs"] == report["runs"]


def test_stability_stops_on_first_failed_verification(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings
    topics = [f"topic-{index:02d}" for index in range(10)]
    calls: list[str] = []

    monkeypatch.setattr("atnos.production.stability_runner._require_frozen_gates", lambda _settings: {})
    monkeypatch.setattr(
        "atnos.production.stability_runner._validate_approved_topics",
        lambda _settings, _account_id, topic_ids: {
            topic_id: {"visual_test_focus": [], "production_brief": {"expected_scene_evidence": []}}
            for topic_id in topic_ids
        },
    )

    def verify(result, **_kwargs):
        passed = result["topic_id"] != "topic-02"
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "job_id": result["id"],
            "final_video_sha256": hashlib.sha256(result["topic_id"].encode()).hexdigest(),
        }

    monkeypatch.setattr("atnos.production.stability_runner._verify_completed_run", verify)

    class FakeOrchestrator:
        async def start(self, *, account_id: str, topic_id: str) -> dict[str, str]:
            calls.append(topic_id)
            return {"id": f"job-{topic_id}", "account_id": account_id, "topic_id": topic_id}

    output_dir = tmp_path / "stability-fail"
    report = asyncio.run(
        run_stability_series(
            settings,
            account_id="youtube:wealth-blueprint",
            topic_ids=topics,
            output_dir=output_dir,
            orchestrator_factory=lambda _settings: FakeOrchestrator(),
        )
    )

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["consecutive_passes"] == 2
    assert calls == topics[:3]
    assert len(report["runs"]) == 3


def test_stability_resumes_signed_interrupted_series_without_repeating_passes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings
    topics = [f"topic-{index:02d}" for index in range(10)]
    contracts = {
        topic_id: {"visual_test_focus": [], "production_brief": {"expected_scene_evidence": []}}
        for topic_id in topics
    }
    monkeypatch.setattr(
        "atnos.production.stability_runner._require_frozen_gates",
        lambda _settings: {
            "reference_id": "WEALTH_BLUEPRINT_REFERENCE_001",
            "acceptance_receipt_hash": "a" * 64,
            "canonical_manifest_hash": "b" * 64,
        },
    )
    monkeypatch.setattr(
        "atnos.production.stability_runner._validate_approved_topics",
        lambda _settings, _account_id, _topic_ids: contracts,
    )
    monkeypatch.setattr(
        "atnos.production.stability_runner._verify_completed_run",
        lambda result, **_kwargs: {
            "status": "passed",
            "passed": True,
            "job_id": result["id"],
            "final_video_sha256": hashlib.sha256(result["topic_id"].encode()).hexdigest(),
        },
    )
    monkeypatch.setattr("atnos.production.stability_runner._revalidate_run_receipt", lambda *_args, **_kwargs: None)

    first_calls: list[str] = []

    class InterruptingOrchestrator:
        async def start(self, *, account_id: str, topic_id: str) -> dict[str, str]:
            first_calls.append(topic_id)
            if topic_id == topics[1]:
                raise asyncio.CancelledError("simulated shutdown")
            return {"id": f"job-{topic_id}", "account_id": account_id, "topic_id": topic_id}

    output_dir = tmp_path / "stability-resume"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_stability_series(
                settings,
                account_id="youtube:wealth-blueprint",
                topic_ids=topics,
                output_dir=output_dir,
                orchestrator_factory=lambda _settings: InterruptingOrchestrator(),
            )
        )
    interrupted = load_stability_report(output_dir / "stability_report.json")
    assert interrupted["status"] == "interrupted"
    assert interrupted["consecutive_passes"] == 1

    resumed_calls: list[str] = []

    class ResumedOrchestrator:
        async def start(self, *, account_id: str, topic_id: str) -> dict[str, str]:
            resumed_calls.append(topic_id)
            return {"id": f"job-{topic_id}", "account_id": account_id, "topic_id": topic_id}

    result = asyncio.run(
        run_stability_series(
            settings,
            account_id="youtube:wealth-blueprint",
            topic_ids=topics,
            resume_report_path=output_dir / "stability_report.json",
            orchestrator_factory=lambda _settings: ResumedOrchestrator(),
        )
    )
    assert result["status"] == "verified"
    assert result["consecutive_passes"] == 10
    assert first_calls == topics[:2]
    assert resumed_calls == topics[1:]
    assert len(result["runs"]) == 10
    assert result["resumed_at"]


def test_stability_resume_rejects_changed_topic_suite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings
    from atnos.production.stability_runner import _persist_report

    original = [f"topic-{index:02d}" for index in range(10)]
    changed = [*original[:-1], "topic-other"]
    report_path = tmp_path / "resume-mismatch.json"
    _persist_report(
        report_path,
        {
            "schema_version": "1.1",
            "run_id": "run-1",
            "style_id": "WEALTH_BLUEPRINT_STYLE_V1",
            "account_id": "youtube:wealth-blueprint",
            "status": "interrupted",
            "passed": False,
            "required_consecutive_passes": 10,
            "consecutive_passes": 0,
            "topic_ids": original,
            "topic_contract_hashes": {},
            "gate_evidence": {
                "acceptance_receipt_hash": "a" * 64,
                "canonical_manifest_hash": "b" * 64,
            },
            "runs": [],
        },
    )
    monkeypatch.setattr(
        "atnos.production.stability_runner._require_frozen_gates",
        lambda _settings: {
            "acceptance_receipt_hash": "a" * 64,
            "canonical_manifest_hash": "b" * 64,
        },
    )
    monkeypatch.setattr(
        "atnos.production.stability_runner._validate_approved_topics",
        lambda *_args: {},
    )
    with pytest.raises(StabilityRunError, match="resume evidence mismatch"):
        asyncio.run(
            run_stability_series(
                settings,
                account_id="youtube:wealth-blueprint",
                topic_ids=changed,
                resume_report_path=report_path,
            )
        )


def test_signed_stability_report_rejects_manual_result_change(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    path = tmp_path / "stability.json"
    from atnos.production.stability_runner import _persist_report

    payload = {
        "run_id": "run-1",
        "status": "failed",
        "passed": False,
        "consecutive_passes": 2,
        "runs": [],
    }
    _persist_report(path, payload)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["status"] = "verified"
    stored["passed"] = True
    path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(StabilityRunError, match="hash mismatch"):
        load_stability_report(path)


def test_stability_refuses_to_sign_verified_report_without_real_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    from atnos.production.stability_runner import _persist_report

    topics = [f"topic-{index:02d}" for index in range(10)]
    payload = {
        "run_id": "forged-run",
        "status": "verified",
        "passed": True,
        "account_id": "youtube:wealth-blueprint",
        "required_consecutive_passes": 10,
        "consecutive_passes": 10,
        "topic_ids": topics,
        "runs": [
            {
                "ordinal": index,
                "topic_id": topic,
                "status": "passed",
                "passed": True,
                "job_id": f"job-{index}",
                "artifact_dir": str(tmp_path / f"missing-{index}"),
                "final_video_sha256": hashlib.sha256(topic.encode()).hexdigest(),
            }
            for index, topic in enumerate(topics, start=1)
        ],
    }

    with pytest.raises(StabilityRunError, match="artifact directory is missing"):
        _persist_report(tmp_path / "forged.json", payload)
    assert not (tmp_path / "forged.json").exists()


def test_stability_records_verifier_exception_as_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings
    topics = [f"topic-{index:02d}" for index in range(10)]
    monkeypatch.setattr("atnos.production.stability_runner._require_frozen_gates", lambda _settings: {})
    monkeypatch.setattr(
        "atnos.production.stability_runner._validate_approved_topics",
        lambda _settings, _account_id, topic_ids: {
            topic_id: {"visual_test_focus": [], "production_brief": {"expected_scene_evidence": []}}
            for topic_id in topic_ids
        },
    )
    monkeypatch.setattr(
        "atnos.production.stability_runner._verify_completed_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(StabilityRunError("tampered manifest")),
    )

    class FakeOrchestrator:
        async def start(self, *, account_id: str, topic_id: str) -> dict[str, str]:
            return {"id": "job-1", "account_id": account_id, "topic_id": topic_id}

    output_dir = tmp_path / "verifier-exception"
    report = asyncio.run(
        run_stability_series(
            settings,
            account_id="youtube:wealth-blueprint",
            topic_ids=topics,
            output_dir=output_dir,
            orchestrator_factory=lambda _settings: FakeOrchestrator(),
        )
    )

    persisted = load_stability_report(output_dir / "stability_report.json")
    assert report["status"] == "failed"
    assert report["consecutive_passes"] == 0
    assert len(report["runs"]) == 1
    assert "tampered manifest" in persisted["runs"][0]["error"]


def test_manifest_integrity_rejects_tampered_global_artifact(tmp_path: Path) -> None:
    scene_file = tmp_path / "scene.png"
    final_file = tmp_path / "final.mp4"
    scene_file.write_bytes(b"scene-original")
    final_file.write_bytes(b"final-original")
    manifest = SimpleNamespace(
        scenes=[
            SimpleNamespace(
                assets=[
                    SimpleNamespace(
                        asset_id="scene-01",
                        path="scene.png",
                        exists=True,
                        provenance=SimpleNamespace(artifact_hash=file_sha256(scene_file)),
                    )
                ]
            )
        ],
        artifacts=[
            SimpleNamespace(
                artifact_id="final-video",
                path="final.mp4",
                exists=True,
                artifact_hash=file_sha256(final_file),
                provenance=SimpleNamespace(artifact_hash=file_sha256(final_file)),
            )
        ],
    )
    assert _verify_manifest_assets(manifest, artifact_dir=tmp_path)["status"] == "verified"

    final_file.write_bytes(b"tampered")
    with pytest.raises(StabilityRunError, match="Global artifact integrity mismatch"):
        _verify_manifest_assets(manifest, artifact_dir=tmp_path)


def test_manifest_integrity_rejects_missing_declared_asset(tmp_path: Path) -> None:
    manifest = SimpleNamespace(
        scenes=[
            SimpleNamespace(
                scene_id="scene-01",
                assets=[
                    SimpleNamespace(
                        asset_id="missing-scene",
                        path="missing.png",
                        exists=False,
                        provenance=SimpleNamespace(artifact_hash=None),
                    )
                ],
            )
        ],
        artifacts=[],
    )
    with pytest.raises(StabilityRunError, match="missing scene asset"):
        _verify_manifest_assets(manifest, artifact_dir=tmp_path)


def test_artifact_path_must_remain_inside_job_directory(tmp_path: Path) -> None:
    assert _resolve_artifact_path(tmp_path, "video/final.mp4") == (tmp_path / "video" / "final.mp4").resolve()
    with pytest.raises(StabilityRunError, match="escapes the job directory"):
        _resolve_artifact_path(tmp_path, "../outside.mp4")
    with pytest.raises(StabilityRunError, match="escapes the job directory"):
        _resolve_artifact_path(tmp_path, tmp_path.parent / "outside.mp4")


def test_ffprobe_contract_accepts_only_production_media(tmp_path: Path) -> None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        pytest.skip("FFmpeg is unavailable")
    output = tmp_path / "contract.mp4"
    process = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:r=30:d=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-shortest",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if process.returncode != 0:
        pytest.skip(f"Test FFmpeg cannot encode H264/AAC: {process.stderr[-300:]}")

    evidence = _probe_final_media(output)
    assert evidence["status"] == "verified"
    assert all(evidence["checks"].values())

    short = tmp_path / "too-short.mp4"
    short_process = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:r=30:d=0.25",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "0.25",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-shortest",
            str(short),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if short_process.returncode == 0:
        with pytest.raises(StabilityRunError, match="independent media checks"):
            _probe_final_media(short)

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not-an-mp4" * 200)
    with pytest.raises(StabilityRunError, match="ffprobe rejected"):
        _probe_final_media(broken)
