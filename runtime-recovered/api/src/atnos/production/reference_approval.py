from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.production.project_manifest import canonical_json, file_sha256, load_project_manifest
from atnos.production.qa_gate import (
    ADVISORY_QA_CHECK_NAMES,
    REQUIRED_QA_CHECK_NAMES,
    actual_visual_variety_evidence_passed,
    remeasure_actual_visual_variety_evidence,
)
from atnos.production.visual_semantic_audit import SEMANTIC_SCORE_FIELDS, WEALTH_SEMANTIC_MIN_SCORE
from atnos.production.visual_regression import (
    APPROVAL_FILENAME,
    REFERENCE_ID,
    STYLE_ID,
    create_visual_regression_baseline,
    load_visual_regression_approval,
    visual_regression_baseline_path,
)
from atnos.production.voice_approval import (
    SIGNING_KEY_ENV,
    approve_production_voice,
    load_production_voice_approval,
    production_voice_approval_path,
)


REVIEW_MANIFEST_FILENAME = "gate_review_manifest.json"
ACCEPTANCE_RECEIPT_FILENAME = "reference_acceptance_receipt.json"
OWNER_ACCEPTANCE_TEXT = (
    "Akceptuj\u0119 g\u0142os F5 i WEALTH_BLUEPRINT_REFERENCE_001 "
    "jako WEALTH_BLUEPRINT_STYLE_V1."
)
APPROVAL_LOCK_FILENAME = ".reference_approval.lock"
APPROVAL_TRANSACTION_FILENAME = ".reference_approval.transaction.json"
class ReferenceApprovalError(RuntimeError):
    pass


def _validate_reference_candidate_qa(payload: dict[str, Any], *, storyboard_path: Path) -> None:
    """Require every non-owner QA gate to pass before human reference review."""
    expected_owner_gates = {"Production voice approval", "Visual regression baseline"}
    blocking = {str(item) for item in payload.get("blocking_failures") or [] if str(item).strip()}
    advisory = [str(item) for item in payload.get("advisory_failures") or [] if str(item).strip()]
    warnings = [str(item) for item in payload.get("warnings") or [] if str(item).strip()]
    decision = payload.get("upload_decision") if isinstance(payload.get("upload_decision"), dict) else {}
    decision_failures = {
        str(item) for item in decision.get("required_failures") or [] if str(item).strip()
    }
    required_checks = payload.get("required_checks") if isinstance(payload.get("required_checks"), list) else []
    advisory_checks = payload.get("advisory_checks") if isinstance(payload.get("advisory_checks"), list) else []
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    required_names = tuple(
        str(check.get("name") or "") for check in required_checks if isinstance(check, dict)
    )
    advisory_names = tuple(
        str(check.get("name") or "") for check in advisory_checks if isinstance(check, dict)
    )
    all_names = tuple(str(check.get("name") or "") for check in checks if isinstance(check, dict))
    complete_contract = bool(
        _safe_int(payload.get("required_check_count")) == len(REQUIRED_QA_CHECK_NAMES)
        and required_names == REQUIRED_QA_CHECK_NAMES
        and advisory_names == ADVISORY_QA_CHECK_NAMES
        and all_names == (*REQUIRED_QA_CHECK_NAMES, *ADVISORY_QA_CHECK_NAMES)
        and len(set(all_names)) == len(all_names)
    )
    statuses = {
        str(check.get("name") or ""): str(check.get("status") or "")
        for check in checks
        if isinstance(check, dict) and str(check.get("name") or "").strip()
    }
    failed_checks = {name for name, status in statuses.items() if status == "fail"}
    non_terminal_checks = {name for name, status in statuses.items() if status not in {"pass", "fail"}}
    if (
        payload.get("overall") != "failed"
        or payload.get("auto_uploadable") is not False
        or blocking != expected_owner_gates
        or advisory
        or warnings
        or decision.get("status") != "blocked"
        or decision.get("auto_uploadable") is not False
        or decision_failures != expected_owner_gates
        or not complete_contract
        or failed_checks != expected_owner_gates
        or non_terminal_checks
        or not expected_owner_gates.issubset(statuses)
        or statuses.get("Semantic storyboard quality") != "pass"
    ):
        raise ReferenceApprovalError(
            "Reference candidate QA must contain only the two unresolved owner gates; "
            "all measured technical, content, and semantic checks must pass"
        )
    _validate_semantic_storyboard_evidence(payload, storyboard_path=storyboard_path)
    _validate_visual_variety_evidence(checks)


def _validate_semantic_storyboard_evidence(payload: dict[str, Any], *, storyboard_path: Path) -> None:
    audit = payload.get("semantic_storyboard_audit")
    audit_input = audit.get("audit_input") if isinstance(audit, dict) else None
    expected_hash = str(audit_input.get("source_sha256") or "").strip().lower() if isinstance(audit_input, dict) else ""
    expected_size = int(audit_input.get("source_size_bytes") or 0) if isinstance(audit_input, dict) else 0
    actual_hash = file_sha256(storyboard_path)
    actual_size = storyboard_path.stat().st_size if storyboard_path.is_file() else 0
    scores = audit.get("scores") if isinstance(audit, dict) and isinstance(audit.get("scores"), dict) else {}
    recorded_min_score = _finite_float(audit.get("min_score")) if isinstance(audit, dict) else None
    scores_valid = bool(
        recorded_min_score is not None
        and WEALTH_SEMANTIC_MIN_SCORE <= recorded_min_score <= 10.0
        and all(
            (score := _finite_float(scores.get(field))) is not None
            and recorded_min_score <= score <= 10.0
            for field in SEMANTIC_SCORE_FIELDS
        )
    )
    if (
        not isinstance(audit, dict)
        or audit.get("passed") is not True
        or audit.get("status") != "pass"
        or not expected_hash
        or expected_hash != actual_hash
        or expected_size <= 0
        or expected_size != actual_size
        or not scores_valid
    ):
        raise ReferenceApprovalError(
            "Reference candidate semantic storyboard evidence is missing or does not match the audited image"
        )


def _validate_visual_variety_evidence(checks: list[Any]) -> None:
    visual_check = next(
        (
            check
            for check in checks
            if isinstance(check, dict) and check.get("name") == "Visual variety"
        ),
        None,
    )
    value = visual_check.get("value") if isinstance(visual_check, dict) else None
    actual = value.get("actual_visual_variety") if isinstance(value, dict) else None
    if not actual_visual_variety_evidence_passed(actual) or not remeasure_actual_visual_variety_evidence(actual):
        raise ReferenceApprovalError(
            "Reference candidate visual-variety evidence is incomplete or below the measured floor"
        )


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return -1


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def prepare_wealth_reference_review_package(
    *,
    artifact_dir: Path,
    review_dir: Path,
) -> dict[str, Any]:
    """Create an immutable, hash-bound owner review package without accepting it."""
    artifact_dir = artifact_dir.expanduser().resolve()
    review_dir = review_dir.expanduser().resolve()
    if review_dir.exists():
        raise ReferenceApprovalError(f"Refusing to overwrite an existing review package: {review_dir}")

    manifest_path = artifact_dir / "project_manifest.json"
    qa_path = artifact_dir / "qa_report.json"
    storyboard_path = artifact_dir / "qa_storyboard_contact_sheet.jpg"
    manifest = load_project_manifest(manifest_path)
    if manifest.channel != "wealth_blueprint":
        raise ReferenceApprovalError("Canonical manifest is not a Wealth Blueprint production")
    if manifest.qa.action.value != "BLOCK_FINAL":
        raise ReferenceApprovalError("Reference candidate must remain BLOCK_FINAL before owner approval")
    allowed_owner_gates = {"Production voice approval", "Visual regression baseline"}
    blocking_reasons = {str(item) for item in manifest.qa.reasons if str(item).strip()}
    if not blocking_reasons or not blocking_reasons.issubset(allowed_owner_gates):
        raise ReferenceApprovalError(
            "Reference candidate has non-owner QA failures: "
            f"{sorted(blocking_reasons - allowed_owner_gates)}"
        )
    _validate_reference_candidate_qa(_read_json(qa_path), storyboard_path=storyboard_path)

    artifacts_by_role = {artifact.role: artifact for artifact in manifest.artifacts}
    try:
        final_video = artifacts_by_role["final_video"]
        narration = artifacts_by_role["narration"]
    except KeyError as exc:
        raise ReferenceApprovalError(f"Canonical manifest is missing required artifact: {exc.args[0]}") from exc
    sources = {
        "video": (Path(final_video.path), "WEALTH_BLUEPRINT_REFERENCE_001.mp4", final_video.artifact_hash),
        "voice": (Path(narration.path), "WEALTH_BLUEPRINT_REFERENCE_001_voice.wav", narration.artifact_hash),
        "storyboard": (storyboard_path, "WEALTH_BLUEPRINT_REFERENCE_001_storyboard.jpg", file_sha256(storyboard_path)),
        "qa_report": (qa_path, "qa_report.json", file_sha256(qa_path)),
    }
    for key, (source, _filename, expected_hash) in sources.items():
        actual_hash = file_sha256(source)
        if not actual_hash or actual_hash != expected_hash:
            raise ReferenceApprovalError(f"Canonical source integrity mismatch: {key}")

    review_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{review_dir.name}.", dir=review_dir.parent))
    try:
        records: dict[str, dict[str, Any]] = {}
        for key, (source, filename, _expected_hash) in sources.items():
            target = staging / filename
            shutil.copyfile(source, target)
            records[key] = {
                "filename": filename,
                "bytes": target.stat().st_size,
                "sha256": file_sha256(target),
            }
        review_manifest = {
            "schema_version": "1.0",
            "reference_id": REFERENCE_ID,
            "style_id": STYLE_ID,
            "status": "awaiting_owner_approval",
            "upload_allowed": False,
            "created_at": datetime.now(UTC).isoformat(),
            "job_id": manifest.job_id,
            "canonical_manifest_hash": manifest.content_hash,
            "source_revision": _repository_source_revision(),
            "blocking_owner_gates": sorted(blocking_reasons),
            "artifacts": records,
        }
        review_manifest["record_hash"] = _review_manifest_hash(review_manifest)
        review_manifest["manifest_signature"] = _review_manifest_signature(review_manifest)
        _atomic_json_write(staging / REVIEW_MANIFEST_FILENAME, review_manifest)
        os.replace(staging, review_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        evidence = _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)
    except Exception:
        # The package is useful only when every copied artifact still matches
        # its canonical source. Do not leave an apparently reviewable folder
        # behind when the final integrity pass fails.
        shutil.rmtree(review_dir, ignore_errors=True)
        raise
    return {
        "status": "awaiting_owner_approval",
        "reference_id": REFERENCE_ID,
        "review_dir": str(review_dir),
        "review_manifest_path": str(review_dir / REVIEW_MANIFEST_FILENAME),
        "verified_artifacts": evidence["verified_artifacts"],
    }


def approve_wealth_reference(
    settings: Settings,
    *,
    artifact_dir: Path,
    review_dir: Path,
    reviewer: str,
    approval_note: str = "",
    confirm_reference_id: str,
    owner_confirmation: str,
) -> dict[str, Any]:
    """Atomically freeze the owner-approved voice and visual reference."""
    if confirm_reference_id != REFERENCE_ID:
        raise ReferenceApprovalError(f"Owner approval must name {REFERENCE_ID}")
    if owner_confirmation != OWNER_ACCEPTANCE_TEXT:
        raise ReferenceApprovalError("Owner approval must use the exact Wealth Blueprint acceptance text")
    if not reviewer.strip():
        raise ReferenceApprovalError("Owner approval requires a named reviewer")

    artifact_dir = artifact_dir.expanduser().resolve()
    review_dir = review_dir.expanduser().resolve()
    voice_path = production_voice_approval_path(settings)
    baseline_path = visual_regression_baseline_path(settings)
    visual_sidecar_path = baseline_path.with_name(APPROVAL_FILENAME)
    receipt_path = baseline_path.with_name(ACCEPTANCE_RECEIPT_FILENAME)
    protected_paths = (voice_path, baseline_path, visual_sidecar_path, receipt_path)
    lock = _acquire_approval_lock(receipt_path.parent)
    transaction_path = receipt_path.with_name(APPROVAL_TRANSACTION_FILENAME)
    transaction_started = False
    try:
        _recover_interrupted_approval(transaction_path, protected_paths)
        existing = [str(path) for path in protected_paths if path.exists()]
        if existing:
            raise ReferenceApprovalError(f"Refusing to overwrite an existing signed approval: {existing}")
        evidence = _validate_review_package(artifact_dir=artifact_dir, review_dir=review_dir)
        current_revision = _repository_source_revision()
        if current_revision != evidence["source_revision"]:
            raise ReferenceApprovalError(
                "Production sources changed after the owner review package was prepared"
            )
        if evidence["source_revision"].get("production_tree_clean") is not True:
            raise ReferenceApprovalError("Owner approval requires a clean committed production source tree")
        review_manifest_path = review_dir / REVIEW_MANIFEST_FILENAME
        _atomic_json_write(
            transaction_path,
            {
                "schema_version": "1.0",
                "token": lock["token"],
                "pid": os.getpid(),
                "reference_id": REFERENCE_ID,
                "review_manifest_sha256": file_sha256(review_manifest_path),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        transaction_started = True
        voice_manifest = json.loads((artifact_dir / "audio_timestamps.json").read_text(encoding="utf-8"))
        voice = approve_production_voice(
            settings,
            voice_manifest,
            reviewer=reviewer,
            approval_note=approval_note,
        )
        assembly = json.loads((artifact_dir / "assembly_manifest.json").read_text(encoding="utf-8"))
        baseline = create_visual_regression_baseline(
            artifact_dir / "project_manifest.json",
            Path(evidence["source_video_path"]),
            baseline_path,
            reference_id=confirm_reference_id,
            approval_status="accepted",
            reviewer=reviewer,
            approval_note=approval_note,
            subtitle_style=assembly.get("subtitle_style"),
            renderer_evidence=assembly,
        )
        authenticated_voice = load_production_voice_approval(voice_path)
        authenticated_baseline = load_visual_regression_approval(baseline_path)
        receipt = {
            "schema_version": "1.0",
            "reference_id": REFERENCE_ID,
            "style_id": STYLE_ID,
            "status": "accepted",
            "reviewer": reviewer.strip(),
            "approval_note": approval_note.strip(),
            "owner_confirmation": owner_confirmation,
            "review_manifest_path": str(review_dir / REVIEW_MANIFEST_FILENAME),
            "review_manifest_sha256": file_sha256(review_manifest_path),
            "artifact_dir": str(artifact_dir),
            "canonical_manifest_hash": evidence["canonical_manifest_hash"],
            "source_revision": evidence["source_revision"],
            "voice_approval_path": str(voice_path),
            "voice_identity_hash": authenticated_voice.voice_identity_hash,
            "voice_approval_signature": authenticated_voice.approval_signature,
            "visual_baseline_path": str(baseline_path),
            "visual_baseline_hash": authenticated_baseline.baseline_hash,
            "visual_approval_signature": authenticated_baseline.approval_signature,
            "verified_artifacts": evidence["verified_artifacts"],
        }
        receipt["record_hash"] = _receipt_hash(receipt)
        receipt["receipt_signature"] = _receipt_signature(receipt)
        _atomic_json_write(receipt_path, receipt)
        authenticated_bundle = load_wealth_reference_acceptance(settings)
        transaction_path.unlink(missing_ok=True)
        return {
            "status": "accepted",
            "reference_id": REFERENCE_ID,
            "voice_approval": voice.model_dump(mode="json"),
            "visual_baseline": baseline.model_dump(mode="json"),
            "receipt_path": str(receipt_path),
            "receipt_hash": authenticated_bundle["record_hash"],
        }
    except Exception:
        # Preflight guarantees these paths did not exist, so removing every
        # protected output also covers a callee that wrote a partial file and
        # raised before returning control to us.
        if transaction_started:
            for path in reversed(protected_paths):
                path.unlink(missing_ok=True)
            transaction_path.unlink(missing_ok=True)
        raise
    finally:
        _release_approval_lock(lock)


def load_wealth_reference_acceptance(settings: Settings) -> dict[str, Any]:
    """Authenticate the complete owner-approval bundle; partial files never count."""
    voice_path = production_voice_approval_path(settings)
    baseline_path = visual_regression_baseline_path(settings)
    sidecar_path = baseline_path.with_name(APPROVAL_FILENAME)
    receipt_path = baseline_path.with_name(ACCEPTANCE_RECEIPT_FILENAME)
    required = (voice_path, baseline_path, sidecar_path, receipt_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ReferenceApprovalError(f"Owner approval bundle is incomplete: {missing}")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ReferenceApprovalError(f"Owner approval receipt is invalid: {exc}") from exc
    if receipt.get("record_hash") != _receipt_hash(receipt):
        raise ReferenceApprovalError("Owner approval receipt hash mismatch")
    if not hmac.compare_digest(str(receipt.get("receipt_signature") or ""), _receipt_signature(receipt)):
        raise ReferenceApprovalError("Owner approval receipt signature mismatch")
    if (
        receipt.get("status") != "accepted"
        or receipt.get("reference_id") != REFERENCE_ID
        or receipt.get("style_id") != STYLE_ID
        or receipt.get("owner_confirmation") != OWNER_ACCEPTANCE_TEXT
    ):
        raise ReferenceApprovalError("Owner approval receipt does not contain the required acceptance")
    source_revision = receipt.get("source_revision")
    source_commit = str(source_revision.get("commit_sha") or "") if isinstance(source_revision, dict) else ""
    if len(source_commit) != 40 or any(ch not in "0123456789abcdef" for ch in source_commit.lower()):
        raise ReferenceApprovalError("Owner approval receipt has no reproducible source commit")
    current_revision = _repository_source_revision()
    if current_revision != source_revision or current_revision.get("production_tree_clean") is not True:
        raise ReferenceApprovalError(
            "Owner approval is stale because the production source revision changed after acceptance"
        )
    voice = load_production_voice_approval(voice_path)
    baseline = load_visual_regression_approval(baseline_path)
    expected = {
        "voice_approval_path": str(voice_path),
        "voice_identity_hash": voice.voice_identity_hash,
        "voice_approval_signature": voice.approval_signature,
        "visual_baseline_path": str(baseline_path),
        "visual_baseline_hash": baseline.baseline_hash,
        "visual_approval_signature": baseline.approval_signature,
        "canonical_manifest_hash": baseline.reference_manifest_hash,
    }
    mismatched = [key for key, value in expected.items() if receipt.get(key) != value]
    if mismatched:
        raise ReferenceApprovalError(f"Owner approval bundle evidence mismatch: {mismatched}")
    review_manifest_path = Path(str(receipt.get("review_manifest_path") or ""))
    if file_sha256(review_manifest_path) != receipt.get("review_manifest_sha256"):
        raise ReferenceApprovalError("Owner approval review manifest integrity mismatch")
    review_manifest = _read_json(review_manifest_path)
    if receipt.get("source_revision") != review_manifest.get("source_revision"):
        raise ReferenceApprovalError("Owner approval source revision does not match the review package")
    artifacts = receipt.get("verified_artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ReferenceApprovalError("Owner approval receipt has no verified artifacts")
    for role, record in artifacts.items():
        if not isinstance(record, dict):
            raise ReferenceApprovalError(f"Owner approval artifact record is invalid: {role}")
        path = Path(str(record.get("path") or ""))
        if (
            not path.is_file()
            or file_sha256(path) != record.get("sha256")
            or path.stat().st_size != int(record.get("bytes") or 0)
        ):
            raise ReferenceApprovalError(f"Owner approval artifact integrity mismatch: {role}")
    return receipt


def _validate_review_package(*, artifact_dir: Path, review_dir: Path) -> dict[str, Any]:
    review_manifest_path = review_dir / REVIEW_MANIFEST_FILENAME
    try:
        review = json.loads(review_manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ReferenceApprovalError(f"Invalid review manifest: {review_manifest_path}: {exc}") from exc
    stored_hash = str(review.get("record_hash") or "")
    if not hmac.compare_digest(stored_hash, _review_manifest_hash(review)):
        raise ReferenceApprovalError("Review manifest hash mismatch")
    stored_signature = str(review.get("manifest_signature") or "")
    if not hmac.compare_digest(stored_signature, _review_manifest_signature(review)):
        raise ReferenceApprovalError("Review manifest signature mismatch")
    if review.get("reference_id") != REFERENCE_ID or review.get("style_id") != STYLE_ID:
        raise ReferenceApprovalError("Review package does not identify the canonical Wealth Blueprint reference")
    if review.get("status") != "awaiting_owner_approval":
        raise ReferenceApprovalError("Review package is not awaiting owner approval")
    if review.get("upload_allowed") is not False:
        raise ReferenceApprovalError("Review candidate must remain upload-blocked during owner approval")
    expected_owner_gates = {"Production voice approval", "Visual regression baseline"}
    recorded_owner_gates = {
        str(item)
        for item in (review.get("blocking_owner_gates") or [])
        if str(item).strip()
    }
    if recorded_owner_gates != expected_owner_gates:
        raise ReferenceApprovalError("Review package does not preserve the two required owner gates")
    source_revision = review.get("source_revision")
    commit_sha = str(source_revision.get("commit_sha") or "") if isinstance(source_revision, dict) else ""
    if len(commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in commit_sha.lower()):
        raise ReferenceApprovalError("Review package has no reproducible source commit")

    artifacts = review.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ReferenceApprovalError("Review package is missing artifact evidence")
    verified: dict[str, dict[str, Any]] = {}
    for key in ("video", "voice", "storyboard", "qa_report"):
        record = artifacts.get(key)
        if not isinstance(record, dict):
            raise ReferenceApprovalError(f"Review package is missing {key}")
        path = review_dir / str(record.get("filename") or "")
        actual_hash = file_sha256(path)
        actual_bytes = path.stat().st_size if path.is_file() else 0
        if actual_hash != record.get("sha256") or actual_bytes != int(record.get("bytes") or 0):
            raise ReferenceApprovalError(f"Review artifact integrity mismatch: {key}")
        verified[key] = {"path": str(path), "bytes": actual_bytes, "sha256": actual_hash}

    manifest_path = artifact_dir / "project_manifest.json"
    assembly_path = artifact_dir / "assembly_manifest.json"
    voice_manifest_path = artifact_dir / "audio_timestamps.json"
    qa_path = artifact_dir / "qa_report.json"
    storyboard_path = artifact_dir / "qa_storyboard_contact_sheet.jpg"
    for path in (manifest_path, assembly_path, voice_manifest_path, qa_path, storyboard_path):
        if not path.is_file():
            raise ReferenceApprovalError(f"Canonical source evidence is missing: {path}")
    manifest = load_project_manifest(manifest_path)
    if manifest.channel != "wealth_blueprint":
        raise ReferenceApprovalError("Canonical manifest is not a Wealth Blueprint production")
    if str(review.get("canonical_manifest_hash") or "") != manifest.content_hash:
        raise ReferenceApprovalError("Review package no longer matches the canonical project manifest")
    assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    voice_manifest = json.loads(voice_manifest_path.read_text(encoding="utf-8"))
    source_video = Path(str(assembly.get("output_file") or "")).expanduser().resolve()
    source_voice = Path(str(voice_manifest.get("output_file") or voice_manifest.get("audio_path") or "")).expanduser().resolve()
    source_pairs = {
        "video": source_video,
        "voice": source_voice,
        "storyboard": storyboard_path,
        "qa_report": qa_path,
    }
    for key, source in source_pairs.items():
        if file_sha256(source) != verified[key]["sha256"]:
            raise ReferenceApprovalError(f"Review artifact no longer matches canonical source: {key}")
    _validate_reference_candidate_qa(
        _read_json(Path(verified["qa_report"]["path"])),
        storyboard_path=Path(verified["storyboard"]["path"]),
    )
    _validate_reference_candidate_qa(_read_json(qa_path), storyboard_path=storyboard_path)
    return {
        "source_video_path": str(source_video),
        "source_voice_path": str(source_voice),
        "canonical_manifest_hash": manifest.content_hash,
        "source_revision": source_revision,
        "verified_artifacts": verified,
    }


def _repository_source_revision() -> dict[str, Any]:
    """Capture the Git revision and production-tree state used to build a candidate."""
    probe = subprocess.run(
        ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        raise ReferenceApprovalError("A reproducible review package requires a Git repository")
    root = Path(probe.stdout.strip()).resolve()
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    commit_sha = commit.stdout.strip().lower()
    if commit.returncode != 0 or len(commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in commit_sha):
        raise ReferenceApprovalError("A reproducible review package requires a valid Git commit")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if status.returncode != 0:
        raise ReferenceApprovalError("Could not verify the production source worktree")
    changes = [line for line in status.stdout.splitlines() if line.strip()]
    tree = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if tree.returncode != 0 or not tree.stdout.strip():
        raise ReferenceApprovalError("Could not fingerprint the production source tree")
    return {
        "commit_sha": commit_sha,
        "revision_scope": "entire_repository",
        "production_paths": ["."],
        "production_tree_hash": hashlib.sha256(tree.stdout.encode("utf-8")).hexdigest(),
        "production_tree_clean": not changes,
        "production_tree_changes": changes,
    }


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _receipt_hash(receipt: dict[str, Any]) -> str:
    payload = dict(receipt)
    payload.pop("record_hash", None)
    payload.pop("receipt_signature", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _review_manifest_hash(manifest: dict[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("record_hash", None)
    payload.pop("manifest_signature", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _review_manifest_signature(manifest: dict[str, Any]) -> str:
    key = os.getenv(SIGNING_KEY_ENV, "").encode("utf-8")
    if len(key) < 32:
        raise ReferenceApprovalError(
            f"Owner review manifest requires {SIGNING_KEY_ENV} with at least 32 bytes"
        )
    record_hash = str(manifest.get("record_hash") or _review_manifest_hash(manifest))
    return hmac.new(key, record_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _receipt_signature(receipt: dict[str, Any]) -> str:
    key = os.getenv(SIGNING_KEY_ENV, "").encode("utf-8")
    if len(key) < 32:
        raise ReferenceApprovalError(
            f"Owner approval receipt requires {SIGNING_KEY_ENV} with at least 32 bytes"
        )
    record_hash = str(receipt.get("record_hash") or _receipt_hash(receipt))
    return hmac.new(key, record_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _acquire_approval_lock(directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / APPROVAL_LOCK_FILENAME
    token = uuid4().hex
    payload = {"pid": os.getpid(), "token": token, "created_at": datetime.now(UTC).isoformat()}
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_json(path)
            if _pid_is_alive(int(existing.get("pid") or 0)):
                raise ReferenceApprovalError(
                    f"Another owner-approval process is active in PID {existing.get('pid')}"
                )
            path.unlink(missing_ok=True)
            continue
        try:
            os.write(descriptor, json.dumps(payload, sort_keys=True).encode("utf-8"))
        finally:
            os.close(descriptor)
        return {**payload, "path": str(path)}
    raise ReferenceApprovalError("Could not acquire the owner-approval lock")


def _release_approval_lock(lock: dict[str, Any]) -> None:
    path = Path(str(lock.get("path") or ""))
    if path.is_file() and _read_json(path).get("token") == lock.get("token"):
        path.unlink(missing_ok=True)


def _recover_interrupted_approval(transaction_path: Path, protected_paths: tuple[Path, ...]) -> None:
    if not transaction_path.is_file():
        return
    receipt_path = next((path for path in protected_paths if path.name == ACCEPTANCE_RECEIPT_FILENAME), None)
    if receipt_path is not None and receipt_path.is_file():
        return
    for path in protected_paths:
        path.unlink(missing_ok=True)
    transaction_path.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
