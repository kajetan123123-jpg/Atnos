from __future__ import annotations

import json
import hashlib
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

from atnos.production.project_manifest import file_sha256
from atnos.production.qa_gate import ADVISORY_QA_CHECK_NAMES, REQUIRED_QA_CHECK_NAMES
from atnos.production.reference_approval import (
    ACCEPTANCE_RECEIPT_FILENAME,
    APPROVAL_TRANSACTION_FILENAME,
    OWNER_ACCEPTANCE_TEXT,
    ReferenceApprovalError,
    _acquire_approval_lock,
    _receipt_hash,
    _receipt_signature,
    _recover_interrupted_approval,
    _release_approval_lock,
    _repository_source_revision,
    _review_manifest_hash,
    _review_manifest_signature,
    _validate_reference_candidate_qa,
    _validate_review_package,
    approve_wealth_reference,
    load_wealth_reference_acceptance,
    prepare_wealth_reference_review_package,
)
from atnos.production.visual_regression import APPROVAL_FILENAME, visual_regression_baseline_path
from atnos.production.voice_approval import production_voice_approval_path
from test_support import create_test_app


class _Dumpable(SimpleNamespace):
    def model_dump(self, **_kwargs):
        return dict(self.__dict__)


def _actual_visual_variety_evidence(tmp_path: Path) -> dict:
    artifacts = []
    for index in range(3):
        clip = tmp_path / f"measured-{index}.mp4"
        clip.write_bytes((f"clip-{index}" * 256).encode("utf-8"))
        artifacts.append(
            {
                "path": str(clip.resolve()),
                "size_bytes": clip.stat().st_size,
                "sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
            }
        )
    evidence_hash = hashlib.sha256(
        json.dumps(artifacts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "pass",
        "generated_clip_file_count": 3,
        "sampled_frame_count": 3,
        "mean_hash_distance": 0.31,
        "min_hash_distance": 0.18,
        "mean_top_crop_pixel_diff": 0.22,
        "min_top_crop_pixel_diff": 0.11,
        "thresholds": {
            "min_mean_hash_distance": 0.16,
            "min_mean_top_crop_pixel_diff": 0.10,
        },
        "sampled_clip_artifacts": artifacts,
        "evidence_sha256": evidence_hash,
        "reason": "clip_frames_are_visually_distinct",
    }


def _valid_reference_qa_payload(tmp_path: Path) -> dict:
    owner_gates = ["Production voice approval", "Visual regression baseline"]
    required_checks = [
        {
            "name": name,
            "status": "fail" if name in owner_gates else "pass",
            **(
                {
                    "value": {
                        "actual_visual_variety": _actual_visual_variety_evidence(tmp_path)
                    }
                }
                if name == "Visual variety"
                else {}
            ),
        }
        for name in REQUIRED_QA_CHECK_NAMES
    ]
    advisory_checks = [{"name": name, "status": "pass"} for name in ADVISORY_QA_CHECK_NAMES]
    return {
        "overall": "failed",
        "auto_uploadable": False,
        "blocking_failures": owner_gates,
        "advisory_failures": [],
        "warnings": [],
        "required_check_count": len(required_checks),
        "required_checks": required_checks,
        "advisory_checks": advisory_checks,
        "checks": [*required_checks, *advisory_checks],
        "upload_decision": {
            "status": "blocked",
            "auto_uploadable": False,
            "required_failures": owner_gates,
        },
    }


def _attach_valid_semantic_audit(qa: dict, storyboard: Path) -> None:
    qa["semantic_storyboard_audit"] = {
        "status": "pass",
        "passed": True,
        "blockers": [],
        "scores": {
            "topic_relevance_score": 8.0,
            "maturity_score": 8.0,
            "premium_style_score": 8.0,
            "clarity_score": 8.0,
        },
        "min_score": 7.0,
        "storyboard_path": str(storyboard),
        "audit_input": {
            "source_sha256": hashlib.sha256(storyboard.read_bytes()).hexdigest(),
            "source_size_bytes": storyboard.stat().st_size,
        },
    }


def _review_fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    monkeypatch.setattr(
        "atnos.production.reference_approval.remeasure_actual_visual_variety_evidence",
        lambda _payload: True,
    )
    artifact_dir = tmp_path / "job"
    review_dir = artifact_dir / "review" / "WEALTH_BLUEPRINT_REFERENCE_001"
    audio_dir = artifact_dir / "audio"
    artifact_dir.mkdir()
    review_dir.mkdir(parents=True)
    audio_dir.mkdir()
    source_video = artifact_dir / "final.mp4"
    source_voice = audio_dir / "narration.wav"
    source_storyboard = artifact_dir / "qa_storyboard_contact_sheet.jpg"
    source_qa = artifact_dir / "qa_report.json"
    source_video.write_bytes(b"video" * 512)
    source_voice.write_bytes(b"voice" * 512)
    source_storyboard.write_bytes(b"storyboard" * 128)
    qa_payload = _valid_reference_qa_payload(tmp_path)
    _attach_valid_semantic_audit(qa_payload, source_storyboard)
    source_qa.write_text(json.dumps(qa_payload), encoding="utf-8")
    (artifact_dir / "project_manifest.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "assembly_manifest.json").write_text(
        json.dumps({"output_file": str(source_video)}), encoding="utf-8"
    )
    (artifact_dir / "audio_timestamps.json").write_text(
        json.dumps({"output_file": str(source_voice)}), encoding="utf-8"
    )
    monkeypatch.setattr(
        "atnos.production.reference_approval.load_project_manifest",
        lambda _path: SimpleNamespace(channel="wealth_blueprint", content_hash="a" * 64),
    )
    source_map = {
        "video": (source_video, "WEALTH_BLUEPRINT_REFERENCE_001.mp4"),
        "voice": (source_voice, "WEALTH_BLUEPRINT_REFERENCE_001_voice.wav"),
        "storyboard": (source_storyboard, "WEALTH_BLUEPRINT_REFERENCE_001_storyboard.jpg"),
        "qa_report": (source_qa, "qa_report.json"),
    }
    records = {}
    for key, (source, filename) in source_map.items():
        target = review_dir / filename
        target.write_bytes(source.read_bytes())
        records[key] = {"filename": filename, "bytes": target.stat().st_size, "sha256": file_sha256(target)}
    review_manifest = {
                "reference_id": "WEALTH_BLUEPRINT_REFERENCE_001",
                "style_id": "WEALTH_BLUEPRINT_STYLE_V1",
                "status": "awaiting_owner_approval",
                "upload_allowed": False,
                "canonical_manifest_hash": "a" * 64,
                "source_revision": {
                    "commit_sha": "1" * 40,
                    "production_path": "runtime-recovered/api/src/atnos/production",
                    "production_tree_clean": True,
                    "production_tree_changes": [],
                },
                "blocking_owner_gates": [
                    "Production voice approval",
                    "Visual regression baseline",
                ],
                "artifacts": records,
    }
    review_manifest["record_hash"] = _review_manifest_hash(review_manifest)
    review_manifest["manifest_signature"] = _review_manifest_signature(review_manifest)
    (review_dir / "gate_review_manifest.json").write_text(json.dumps(review_manifest), encoding="utf-8")
    return artifact_dir, review_dir


def test_review_package_hashes_must_match_canonical_sources(tmp_path: Path, monkeypatch) -> None:
    artifact_dir, review_dir = _review_fixture(tmp_path, monkeypatch)
    evidence = _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)
    assert len(evidence["verified_artifacts"]) == 4

    (review_dir / "WEALTH_BLUEPRINT_REFERENCE_001.mp4").write_bytes(b"tampered")
    with pytest.raises(ReferenceApprovalError, match="integrity mismatch: video"):
        _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)


def test_review_package_rejects_hash_valid_qa_with_extra_failure(tmp_path: Path, monkeypatch) -> None:
    artifact_dir, review_dir = _review_fixture(tmp_path, monkeypatch)
    qa = json.loads((artifact_dir / "qa_report.json").read_text(encoding="utf-8"))
    qa["blocking_failures"].append("Visual variety")
    qa["required_checks"] = [
        {**check, "status": "fail" if check["name"] == "Visual variety" else check["status"]}
        for check in qa["required_checks"]
    ]
    qa["checks"] = [*qa["required_checks"], *qa["advisory_checks"]]
    payload = json.dumps(qa).encode("utf-8")
    canonical = artifact_dir / "qa_report.json"
    copied = review_dir / "qa_report.json"
    canonical.write_bytes(payload)
    copied.write_bytes(payload)
    review_path = review_dir / "gate_review_manifest.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["artifacts"]["qa_report"].update(
        {"bytes": copied.stat().st_size, "sha256": file_sha256(copied)}
    )
    review["record_hash"] = _review_manifest_hash(review)
    review["manifest_signature"] = _review_manifest_signature(review)
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ReferenceApprovalError, match="only the two unresolved owner gates"):
        _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)


def test_reference_candidate_rejects_incomplete_qa_contract(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    qa["required_checks"] = qa["required_checks"][:3]
    qa["required_check_count"] = 3
    qa["checks"] = [*qa["required_checks"], *qa["advisory_checks"]]

    with pytest.raises(ReferenceApprovalError, match="only the two unresolved owner gates"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


def test_reference_candidate_rejects_non_numeric_required_check_count(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    qa["required_check_count"] = "not-a-number"

    with pytest.raises(ReferenceApprovalError, match="only the two unresolved owner gates"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


def test_reference_candidate_rejects_semantic_pass_without_scores(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    qa["semantic_storyboard_audit"] = {
        "status": "pass",
        "passed": True,
        "blockers": [],
        "storyboard_path": str(storyboard),
        "audit_input": {
            "source_sha256": hashlib.sha256(storyboard.read_bytes()).hexdigest(),
            "source_size_bytes": storyboard.stat().st_size,
        },
    }

    with pytest.raises(ReferenceApprovalError, match="semantic storyboard evidence"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


def test_reference_candidate_rejects_semantic_scores_above_scale(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    qa["semantic_storyboard_audit"] = {
        "status": "pass",
        "passed": True,
        "blockers": [],
        "scores": {field: 100.0 for field in (
            "topic_relevance_score",
            "maturity_score",
            "premium_style_score",
            "clarity_score",
        )},
        "min_score": 100.0,
        "storyboard_path": str(storyboard),
        "audit_input": {
            "source_sha256": hashlib.sha256(storyboard.read_bytes()).hexdigest(),
            "source_size_bytes": storyboard.stat().st_size,
        },
    }

    with pytest.raises(ReferenceApprovalError, match="semantic storyboard evidence"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


def test_reference_candidate_rejects_visual_variety_pass_without_measurements(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    _attach_valid_semantic_audit(qa, storyboard)
    visual = next(check for check in qa["checks"] if check["name"] == "Visual variety")
    visual["value"] = {"actual_visual_variety": {"status": "pass"}}

    with pytest.raises(ReferenceApprovalError, match="visual-variety evidence"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


def test_reference_candidate_rejects_visual_evidence_after_clip_tamper(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    _attach_valid_semantic_audit(qa, storyboard)
    visual = next(check for check in qa["checks"] if check["name"] == "Visual variety")
    evidence = visual["value"]["actual_visual_variety"]
    Path(evidence["sampled_clip_artifacts"][0]["path"]).write_bytes(b"tampered")

    with pytest.raises(ReferenceApprovalError, match="visual-variety evidence"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


@pytest.mark.parametrize("invalid_size", [True, 1.5])
def test_reference_candidate_rejects_non_integer_visual_artifact_size(
    tmp_path: Path,
    invalid_size: object,
) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    _attach_valid_semantic_audit(qa, storyboard)
    visual = next(check for check in qa["checks"] if check["name"] == "Visual variety")
    visual["value"]["actual_visual_variety"]["sampled_clip_artifacts"][0]["size_bytes"] = invalid_size

    with pytest.raises(ReferenceApprovalError, match="visual-variety evidence"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


def test_reference_candidate_rejects_visual_metrics_that_fail_remeasurement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    storyboard = tmp_path / "storyboard.jpg"
    storyboard.write_bytes(b"storyboard")
    qa = _valid_reference_qa_payload(tmp_path)
    _attach_valid_semantic_audit(qa, storyboard)
    monkeypatch.setattr(
        "atnos.production.reference_approval.remeasure_actual_visual_variety_evidence",
        lambda _payload: False,
    )

    with pytest.raises(ReferenceApprovalError, match="visual-variety evidence"):
        _validate_reference_candidate_qa(qa, storyboard_path=storyboard)


def test_repository_revision_fingerprints_entire_worktree(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n"),
            SimpleNamespace(returncode=0, stdout=f"{'1' * 40}\n"),
            SimpleNamespace(returncode=0, stdout="?? untracked-production-input.py\n"),
            SimpleNamespace(returncode=0, stdout="100644 2f00 path/to/tracked.py\n"),
        ]
    )

    def fake_run(command, **_kwargs):
        calls.append(command)
        return next(responses)

    monkeypatch.setattr("atnos.production.reference_approval.subprocess.run", fake_run)

    revision = _repository_source_revision()

    assert revision["revision_scope"] == "entire_repository"
    assert revision["production_paths"] == ["."]
    assert revision["production_tree_clean"] is False
    assert calls[2][-2:] == ["--porcelain", "--untracked-files=normal"]
    assert calls[3][-2:] == ["ls-files", "-s"]


def test_review_package_rejects_coordinated_artifact_and_manifest_rewrite(tmp_path: Path, monkeypatch) -> None:
    artifact_dir, review_dir = _review_fixture(tmp_path, monkeypatch)
    review_path = review_dir / "gate_review_manifest.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    replacement = b"different-video" * 512
    (artifact_dir / "final.mp4").write_bytes(replacement)
    copied = review_dir / "WEALTH_BLUEPRINT_REFERENCE_001.mp4"
    copied.write_bytes(replacement)
    review["artifacts"]["video"].update(
        {"bytes": copied.stat().st_size, "sha256": file_sha256(copied)}
    )
    review["record_hash"] = _review_manifest_hash(review)
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ReferenceApprovalError, match="signature mismatch"):
        _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)


def test_review_package_rejects_source_drift_after_review_copy(tmp_path: Path, monkeypatch) -> None:
    artifact_dir, review_dir = _review_fixture(tmp_path, monkeypatch)
    (artifact_dir / "final.mp4").write_bytes(b"source-drift" * 256)

    with pytest.raises(ReferenceApprovalError, match="no longer matches canonical source: video"):
        _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)


def test_review_package_rejects_canonical_manifest_drift(tmp_path: Path, monkeypatch) -> None:
    artifact_dir, review_dir = _review_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "atnos.production.reference_approval.load_project_manifest",
        lambda _path: SimpleNamespace(channel="wealth_blueprint", content_hash="b" * 64),
    )

    with pytest.raises(ReferenceApprovalError, match="no longer matches the canonical project manifest"):
        _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)


def test_prepare_review_package_copies_and_verifies_canonical_artifacts(tmp_path: Path, monkeypatch) -> None:
    artifact_dir, fixture_review_dir = _review_fixture(tmp_path, monkeypatch)
    shutil_target = tmp_path / "prepared" / "WEALTH_BLUEPRINT_REFERENCE_001"
    fixture_review_dir.rename(tmp_path / "fixture-review-unused")
    final_video = artifact_dir / "final.mp4"
    narration = artifact_dir / "audio" / "narration.wav"
    monkeypatch.setattr(
        "atnos.production.reference_approval.load_project_manifest",
        lambda _path: SimpleNamespace(
            channel="wealth_blueprint",
            job_id="job-1",
            content_hash="a" * 64,
            qa=SimpleNamespace(
                action=SimpleNamespace(value="BLOCK_FINAL"),
                reasons=["Production voice approval", "Visual regression baseline"],
            ),
            artifacts=[
                SimpleNamespace(role="final_video", path=str(final_video), artifact_hash=file_sha256(final_video)),
                SimpleNamespace(role="narration", path=str(narration), artifact_hash=file_sha256(narration)),
            ],
        ),
    )

    result = prepare_wealth_reference_review_package(
        artifact_dir=artifact_dir,
        review_dir=shutil_target,
    )

    assert result["status"] == "awaiting_owner_approval"
    payload = json.loads((shutil_target / "gate_review_manifest.json").read_text(encoding="utf-8"))
    assert payload["upload_allowed"] is False
    assert payload["canonical_manifest_hash"] == "a" * 64
    assert set(result["verified_artifacts"]) == {"video", "voice", "storyboard", "qa_report"}


def test_prepare_review_package_rejects_non_owner_qa_failure(tmp_path: Path, monkeypatch) -> None:
    artifact_dir = tmp_path / "job"
    artifact_dir.mkdir()
    (artifact_dir / "project_manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "atnos.production.reference_approval.load_project_manifest",
        lambda _path: SimpleNamespace(
            channel="wealth_blueprint",
            qa=SimpleNamespace(
                action=SimpleNamespace(value="BLOCK_FINAL"),
                reasons=["Production voice approval", "Audio format"],
            ),
        ),
    )

    with pytest.raises(ReferenceApprovalError, match="non-owner QA failures:.*Audio format"):
        prepare_wealth_reference_review_package(
            artifact_dir=artifact_dir,
            review_dir=tmp_path / "review",
        )


def test_prepare_review_package_removes_output_when_final_validation_fails(tmp_path: Path, monkeypatch) -> None:
    artifact_dir, fixture_review_dir = _review_fixture(tmp_path, monkeypatch)
    review_dir = tmp_path / "prepared" / "WEALTH_BLUEPRINT_REFERENCE_001"
    fixture_review_dir.rename(tmp_path / "fixture-review-unused")
    final_video = artifact_dir / "final.mp4"
    narration = artifact_dir / "audio" / "narration.wav"
    monkeypatch.setattr(
        "atnos.production.reference_approval.load_project_manifest",
        lambda _path: SimpleNamespace(
            channel="wealth_blueprint",
            job_id="job-1",
            content_hash="a" * 64,
            qa=SimpleNamespace(
                action=SimpleNamespace(value="BLOCK_FINAL"),
                reasons=["Production voice approval", "Visual regression baseline"],
            ),
            artifacts=[
                SimpleNamespace(role="final_video", path=str(final_video), artifact_hash=file_sha256(final_video)),
                SimpleNamespace(role="narration", path=str(narration), artifact_hash=file_sha256(narration)),
            ],
        ),
    )
    monkeypatch.setattr(
        "atnos.production.reference_approval._validate_review_package",
        lambda **_kwargs: (_ for _ in ()).throw(ReferenceApprovalError("final validation failed")),
    )

    with pytest.raises(ReferenceApprovalError, match="final validation failed"):
        prepare_wealth_reference_review_package(
            artifact_dir=artifact_dir,
            review_dir=review_dir,
        )

    assert not review_dir.exists()


def test_atomic_reference_approval_rolls_back_voice_when_visual_freeze_fails(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings
    artifact_dir = tmp_path / "job"
    review_dir = tmp_path / "review"
    artifact_dir.mkdir()
    review_dir.mkdir()
    (artifact_dir / "audio_timestamps.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "assembly_manifest.json").write_text("{}", encoding="utf-8")
    voice_path = tmp_path / "signed" / "voice.json"
    baseline_path = tmp_path / "signed" / "baseline.json"
    source_revision = {
        "commit_sha": "1" * 40,
        "production_paths": ["runtime-recovered/api/src/atnos/production"],
        "production_tree_hash": "2" * 64,
        "production_tree_clean": True,
        "production_tree_changes": [],
    }
    monkeypatch.setattr(
        "atnos.production.reference_approval._validate_review_package",
        lambda **_kwargs: {
            "source_video_path": str(tmp_path / "video.mp4"),
            "canonical_manifest_hash": "e" * 64,
            "source_revision": source_revision,
            "verified_artifacts": {},
        },
    )
    monkeypatch.setattr("atnos.production.reference_approval._repository_source_revision", lambda: source_revision)
    monkeypatch.setattr("atnos.production.reference_approval.production_voice_approval_path", lambda _s: voice_path)
    monkeypatch.setattr("atnos.production.reference_approval.visual_regression_baseline_path", lambda _s: baseline_path)

    def approve_voice(*_args, **_kwargs):
        voice_path.parent.mkdir(parents=True, exist_ok=True)
        voice_path.write_text("signed", encoding="utf-8")
        return _Dumpable(voice_identity_hash="a" * 64, approval_signature="b" * 64)

    monkeypatch.setattr("atnos.production.reference_approval.approve_production_voice", approve_voice)
    def fail_after_partial_visual_write(*_args, **_kwargs):
        baseline_path.write_text("partial", encoding="utf-8")
        baseline_path.with_name("reference_approval.json").write_text("partial", encoding="utf-8")
        raise RuntimeError("visual freeze failed")

    monkeypatch.setattr(
        "atnos.production.reference_approval.create_visual_regression_baseline",
        fail_after_partial_visual_write,
    )

    with pytest.raises(RuntimeError, match="visual freeze failed"):
        approve_wealth_reference(
            settings,
            artifact_dir=artifact_dir,
            review_dir=review_dir,
            reviewer="operator",
            confirm_reference_id="WEALTH_BLUEPRINT_REFERENCE_001",
            owner_confirmation=OWNER_ACCEPTANCE_TEXT,
        )
    assert not voice_path.exists()
    assert not baseline_path.exists()
    assert not baseline_path.with_name("reference_approval.json").exists()


def test_reference_approval_refuses_existing_signed_outputs(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings
    artifact_dir = tmp_path / "job"
    review_dir = tmp_path / "review"
    artifact_dir.mkdir()
    review_dir.mkdir()
    voice_path = tmp_path / "signed" / "voice.json"
    baseline_path = tmp_path / "signed" / "baseline.json"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_text("existing-signed-record", encoding="utf-8")
    monkeypatch.setattr(
        "atnos.production.reference_approval._validate_review_package",
        lambda **_kwargs: {
            "source_video_path": str(tmp_path / "video.mp4"),
            "canonical_manifest_hash": "e" * 64,
            "verified_artifacts": {},
        },
    )
    monkeypatch.setattr("atnos.production.reference_approval.production_voice_approval_path", lambda _s: voice_path)
    monkeypatch.setattr("atnos.production.reference_approval.visual_regression_baseline_path", lambda _s: baseline_path)

    with pytest.raises(ReferenceApprovalError, match="Refusing to overwrite"):
        approve_wealth_reference(
            settings,
            artifact_dir=artifact_dir,
            review_dir=review_dir,
            reviewer="operator",
            confirm_reference_id="WEALTH_BLUEPRINT_REFERENCE_001",
            owner_confirmation=OWNER_ACCEPTANCE_TEXT,
        )
    assert voice_path.read_text(encoding="utf-8") == "existing-signed-record"


def test_atomic_reference_approval_persists_authenticated_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings
    artifact_dir = tmp_path / "job"
    review_dir = tmp_path / "review"
    artifact_dir.mkdir()
    review_dir.mkdir()
    (artifact_dir / "audio_timestamps.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "assembly_manifest.json").write_text("{}", encoding="utf-8")
    voice_path = tmp_path / "signed" / "voice.json"
    baseline_path = tmp_path / "signed" / "baseline.json"
    sidecar_path = baseline_path.with_name("reference_approval.json")
    source_revision = {
        "commit_sha": "1" * 40,
        "production_paths": ["runtime-recovered/api/src/atnos/production"],
        "production_tree_hash": "2" * 64,
        "production_tree_clean": True,
        "production_tree_changes": [],
    }
    monkeypatch.setattr(
        "atnos.production.reference_approval._validate_review_package",
        lambda **_kwargs: {
            "source_video_path": str(tmp_path / "video.mp4"),
            "canonical_manifest_hash": "e" * 64,
            "source_revision": source_revision,
            "verified_artifacts": {"video": {"sha256": "c" * 64}},
        },
    )
    monkeypatch.setattr("atnos.production.reference_approval._repository_source_revision", lambda: source_revision)
    monkeypatch.setattr("atnos.production.reference_approval.production_voice_approval_path", lambda _s: voice_path)
    monkeypatch.setattr("atnos.production.reference_approval.visual_regression_baseline_path", lambda _s: baseline_path)

    voice = _Dumpable(voice_identity_hash="a" * 64, approval_signature="b" * 64)
    baseline = _Dumpable(baseline_hash="c" * 64, approval_signature="d" * 64)

    def approve_voice(*_args, **_kwargs):
        voice_path.parent.mkdir(parents=True, exist_ok=True)
        voice_path.write_text("signed", encoding="utf-8")
        return voice

    def approve_visual(*_args, **_kwargs):
        baseline_path.write_text("signed", encoding="utf-8")
        sidecar_path.write_text("signed", encoding="utf-8")
        return baseline

    monkeypatch.setattr("atnos.production.reference_approval.approve_production_voice", approve_voice)
    monkeypatch.setattr("atnos.production.reference_approval.create_visual_regression_baseline", approve_visual)
    monkeypatch.setattr("atnos.production.reference_approval.load_production_voice_approval", lambda _p: voice)
    monkeypatch.setattr("atnos.production.reference_approval.load_visual_regression_approval", lambda _p: baseline)
    monkeypatch.setattr(
        "atnos.production.reference_approval.load_wealth_reference_acceptance",
        lambda _settings: {"record_hash": "f" * 64},
    )

    result = approve_wealth_reference(
        settings,
        artifact_dir=artifact_dir,
        review_dir=review_dir,
        reviewer="operator",
        approval_note="Owner accepted reference",
        confirm_reference_id="WEALTH_BLUEPRINT_REFERENCE_001",
        owner_confirmation=OWNER_ACCEPTANCE_TEXT,
    )

    receipt = baseline_path.with_name("reference_acceptance_receipt.json")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert result["status"] == "accepted"
    assert payload["reference_id"] == "WEALTH_BLUEPRINT_REFERENCE_001"
    assert payload["voice_identity_hash"] == "a" * 64
    assert payload["visual_baseline_hash"] == "c" * 64
    assert payload["owner_confirmation"] == OWNER_ACCEPTANCE_TEXT


def test_reference_approval_rejects_inexact_owner_confirmation(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings

    with pytest.raises(ReferenceApprovalError, match="exact Wealth Blueprint acceptance text"):
        approve_wealth_reference(
            settings,
            artifact_dir=tmp_path / "job",
            review_dir=tmp_path / "review",
            reviewer="operator",
            confirm_reference_id="WEALTH_BLUEPRINT_REFERENCE_001",
            owner_confirmation="Akceptuje reference.",
        )


def test_reference_approval_lock_rejects_parallel_process(tmp_path: Path) -> None:
    first = _acquire_approval_lock(tmp_path)
    try:
        with pytest.raises(ReferenceApprovalError, match="Another owner-approval process is active"):
            _acquire_approval_lock(tmp_path)
    finally:
        _release_approval_lock(first)
    second = _acquire_approval_lock(tmp_path)
    _release_approval_lock(second)


def test_reference_approval_recovers_incomplete_transaction(tmp_path: Path) -> None:
    receipt = tmp_path / ACCEPTANCE_RECEIPT_FILENAME
    protected = (
        tmp_path / "voice.json",
        tmp_path / "baseline.json",
        tmp_path / APPROVAL_FILENAME,
        receipt,
    )
    transaction = tmp_path / APPROVAL_TRANSACTION_FILENAME
    for path in protected[:-1]:
        path.write_text("partial", encoding="utf-8")
    transaction.write_text('{"pid": 999999}', encoding="utf-8")

    _recover_interrupted_approval(transaction, protected)

    assert not transaction.exists()
    assert not any(path.exists() for path in protected)


def test_complete_reference_bundle_detects_artifact_tampering(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "s" * 64)
    settings = create_test_app(tmp_path).state.settings
    voice_path = production_voice_approval_path(settings)
    baseline_path = visual_regression_baseline_path(settings)
    sidecar_path = baseline_path.with_name(APPROVAL_FILENAME)
    receipt_path = baseline_path.with_name(ACCEPTANCE_RECEIPT_FILENAME)
    for path in (voice_path, baseline_path, sidecar_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("signed", encoding="utf-8")
    review_manifest = tmp_path / "gate_review_manifest.json"
    source_revision = {
        "commit_sha": "1" * 40,
        "production_path": "runtime-recovered/api/src/atnos/production",
        "production_tree_clean": True,
        "production_tree_changes": [],
    }
    review_manifest.write_text(
        json.dumps({"status": "awaiting_owner_approval", "source_revision": source_revision}),
        encoding="utf-8",
    )
    artifact = tmp_path / "reference.mp4"
    artifact.write_bytes(b"reference-video")
    voice = _Dumpable(voice_identity_hash="a" * 64, approval_signature="b" * 64)
    baseline = _Dumpable(
        baseline_hash="c" * 64,
        approval_signature="d" * 64,
        reference_manifest_hash="e" * 64,
    )
    monkeypatch.setattr("atnos.production.reference_approval.load_production_voice_approval", lambda _p: voice)
    monkeypatch.setattr("atnos.production.reference_approval.load_visual_regression_approval", lambda _p: baseline)
    receipt = {
        "schema_version": "1.0",
        "reference_id": "WEALTH_BLUEPRINT_REFERENCE_001",
        "style_id": "WEALTH_BLUEPRINT_STYLE_V1",
        "status": "accepted",
        "owner_confirmation": OWNER_ACCEPTANCE_TEXT,
        "review_manifest_path": str(review_manifest),
        "review_manifest_sha256": file_sha256(review_manifest),
        "canonical_manifest_hash": "e" * 64,
        "source_revision": source_revision,
        "voice_approval_path": str(voice_path),
        "voice_identity_hash": "a" * 64,
        "voice_approval_signature": "b" * 64,
        "visual_baseline_path": str(baseline_path),
        "visual_baseline_hash": "c" * 64,
        "visual_approval_signature": "d" * 64,
        "verified_artifacts": {
            "video": {
                "path": str(artifact),
                "bytes": artifact.stat().st_size,
                "sha256": file_sha256(artifact),
            }
        },
    }
    receipt["record_hash"] = _receipt_hash(receipt)
    receipt["receipt_signature"] = _receipt_signature(receipt)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(
        "atnos.production.reference_approval._repository_source_revision",
        lambda: source_revision,
    )

    assert load_wealth_reference_acceptance(settings)["status"] == "accepted"
    stale_revision = dict(source_revision)
    stale_revision["commit_sha"] = "9" * 40
    monkeypatch.setattr(
        "atnos.production.reference_approval._repository_source_revision",
        lambda: stale_revision,
    )
    with pytest.raises(ReferenceApprovalError, match="source revision changed"):
        load_wealth_reference_acceptance(settings)
    monkeypatch.setattr(
        "atnos.production.reference_approval._repository_source_revision",
        lambda: source_revision,
    )
    artifact.write_bytes(b"tampered")
    with pytest.raises(ReferenceApprovalError, match="artifact integrity mismatch"):
        load_wealth_reference_acceptance(settings)
