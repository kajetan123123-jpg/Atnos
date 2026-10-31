from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from atnos.core.config import Settings
from atnos.intelligence.storage import connect
from atnos.production.media_tools import resolve_ffprobe
from atnos.production.orchestrator import ProductionOrchestrator
from atnos.production.project_manifest import file_sha256, load_project_manifest
from atnos.production.project_manifest import canonical_json
from atnos.production.reference_approval import (
    ReferenceApprovalError,
    load_wealth_reference_acceptance,
)
from atnos.production.stability_topics import (
    DEFAULT_STABILITY_SUITE_PATH,
    canonical_stability_contracts,
    is_stability_source_record,
    load_stability_topic_suite,
    parse_stability_source_record,
)
from atnos.production.visual_regression import (
    STYLE_ID,
    load_visual_regression_baseline,
    visual_regression_baseline_path,
)
from atnos.production.voice_approval import (
    SIGNING_KEY_ENV,
    load_production_voice_approval,
    production_voice_approval_path,
)


STABILITY_RENDER_COUNT = 10
ALLOWED_SCENE_TYPES = {"HookScene", "ImageScene", "NumberScene", "ChartScene", "CTAScene"}


class StabilityRunError(RuntimeError):
    pass


async def run_stability_series(
    settings: Settings,
    *,
    account_id: str,
    topic_ids: list[str],
    output_dir: Path | None = None,
    resume_report_path: Path | None = None,
    orchestrator_factory: Callable[..., ProductionOrchestrator] = ProductionOrchestrator,
) -> dict[str, Any]:
    """Run ten sequential real production jobs and persist evidence after every job."""
    normalized_topics = _validate_topic_ids(topic_ids)
    gate_evidence = _require_frozen_gates(settings)
    topic_contracts = _validate_approved_topics(settings, account_id, normalized_topics) or {}
    topic_contract_hashes = {
        topic_id: hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()
        for topic_id, contract in topic_contracts.items()
    }
    if resume_report_path is not None:
        report_path = resume_report_path.expanduser().resolve()
        if output_dir is not None and output_dir.expanduser().resolve() != report_path.parent:
            raise StabilityRunError("output_dir must match the resumed stability report directory")
        report = load_stability_report(report_path)
        _prepare_resumed_report(
            report,
            account_id=account_id,
            topic_ids=normalized_topics,
            topic_contract_hashes=topic_contract_hashes,
            gate_evidence=gate_evidence,
        )
        if report.get("status") == "verified":
            return {**report, "report_path": str(report_path)}
        report["status"] = "running"
        report["passed"] = False
        report["completed_at"] = None
        report.setdefault("resumed_at", []).append(datetime.now(UTC).isoformat())
    else:
        run_id = f"wealth-v1-stability-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        root = output_dir or Path(settings.data_dir) / "production" / "stability" / STYLE_ID / run_id
        root.mkdir(parents=True, exist_ok=False)
        report_path = root / "stability_report.json"
        report = {
            "schema_version": "1.1",
            "run_id": run_id,
            "style_id": STYLE_ID,
            "account_id": account_id,
            "status": "running",
            "passed": False,
            "required_consecutive_passes": STABILITY_RENDER_COUNT,
            "consecutive_passes": 0,
            "topic_ids": normalized_topics,
            "topic_contract_hashes": topic_contract_hashes,
            "gate_evidence": gate_evidence,
            "runs": [],
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
            "resumed_at": [],
        }
    _persist_report(report_path, report)

    orchestrator = orchestrator_factory(settings)
    completed_count = len(report["runs"])
    for ordinal, topic_id in enumerate(normalized_topics[completed_count:], start=completed_count + 1):
        started_at = datetime.now(UTC).isoformat()
        try:
            result = await orchestrator.start(account_id=account_id, topic_id=topic_id)
            receipt = await asyncio.to_thread(
                _verify_completed_run,
                result,
                expected_account_id=account_id,
                expected_topic_id=topic_id,
                expected_scene_types=list(topic_contracts.get(topic_id, {}).get("visual_test_focus") or []),
                expected_scene_evidence=list(
                    (topic_contracts.get(topic_id, {}).get("production_brief") or {}).get(
                        "expected_scene_evidence"
                    )
                    or []
                ),
            )
            _require_unique_run_receipt(report["runs"], receipt)
        except asyncio.CancelledError as exc:
            receipt = {
                "ordinal": ordinal,
                "topic_id": topic_id,
                "status": "failed",
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
            }
            report["runs"].append(receipt)
            report["status"] = "interrupted"
            report["completed_at"] = datetime.now(UTC).isoformat()
            _persist_report(report_path, report)
            raise
        except Exception as exc:
            receipt = {
                "ordinal": ordinal,
                "topic_id": topic_id,
                "status": "failed",
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
            }
            report["runs"].append(receipt)
            report["status"] = "failed"
            report["completed_at"] = datetime.now(UTC).isoformat()
            _persist_report(report_path, report)
            return {**report, "report_path": str(report_path)}

        receipt.update(
            {
                "ordinal": ordinal,
                "topic_id": topic_id,
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
            }
        )
        report["runs"].append(receipt)
        if receipt.get("passed") is not True:
            report["status"] = "failed"
            report["completed_at"] = datetime.now(UTC).isoformat()
            _persist_report(report_path, report)
            return {**report, "report_path": str(report_path)}
        report["consecutive_passes"] = ordinal
        _persist_report(report_path, report)

    report["status"] = "verified"
    report["passed"] = True
    report["completed_at"] = datetime.now(UTC).isoformat()
    _persist_report(report_path, report)
    return {**report, "report_path": str(report_path)}


def _prepare_resumed_report(
    report: dict[str, Any],
    *,
    account_id: str,
    topic_ids: list[str],
    topic_contract_hashes: dict[str, str],
    gate_evidence: dict[str, Any],
) -> None:
    """Validate a signed checkpoint and remove only its interrupted tail receipt."""
    status = str(report.get("status") or "")
    if status == "verified":
        if report.get("passed") is not True or int(report.get("consecutive_passes") or 0) != STABILITY_RENDER_COUNT:
            raise StabilityRunError("Verified stability report has an inconsistent terminal state")
    elif status not in {"running", "interrupted"}:
        raise StabilityRunError(f"Stability report cannot be resumed from status: {status}")
    checks = {
        "style_id": report.get("style_id") == STYLE_ID,
        "account_id": report.get("account_id") == account_id,
        "topic_ids": report.get("topic_ids") == topic_ids,
        "topic_contract_hashes": report.get("topic_contract_hashes") == topic_contract_hashes,
        "required_consecutive_passes": int(report.get("required_consecutive_passes") or 0)
        == STABILITY_RENDER_COUNT,
        "acceptance_receipt_hash": (report.get("gate_evidence") or {}).get("acceptance_receipt_hash")
        == gate_evidence.get("acceptance_receipt_hash"),
        "canonical_manifest_hash": (report.get("gate_evidence") or {}).get("canonical_manifest_hash")
        == gate_evidence.get("canonical_manifest_hash"),
        "source_revision": (report.get("gate_evidence") or {}).get("source_revision")
        == gate_evidence.get("source_revision"),
    }
    failed = [key for key, passed in checks.items() if not passed]
    if failed:
        raise StabilityRunError(f"Stability resume evidence mismatch: {failed}")
    runs = report.get("runs")
    if not isinstance(runs, list):
        raise StabilityRunError("Stability report runs are invalid")
    if status == "interrupted" and runs and runs[-1].get("passed") is not True:
        runs.pop()
    if any(run.get("passed") is not True for run in runs):
        raise StabilityRunError("Stability report contains a non-passing completed receipt")
    if len(runs) > STABILITY_RENDER_COUNT:
        raise StabilityRunError("Stability report contains too many completed receipts")
    for index, receipt in enumerate(runs, start=1):
        if int(receipt.get("ordinal") or 0) != index or receipt.get("topic_id") != topic_ids[index - 1]:
            raise StabilityRunError("Stability report receipt order does not match the topic suite")
        _require_unique_run_receipt(runs[: index - 1], receipt)
        _revalidate_run_receipt(receipt, expected_account_id=account_id)
    report["runs"] = runs
    report["consecutive_passes"] = len(runs)


def _validate_topic_ids(topic_ids: list[str]) -> list[str]:
    normalized = [str(item).strip() for item in topic_ids if str(item).strip()]
    if len(normalized) != STABILITY_RENDER_COUNT:
        raise StabilityRunError(f"Stability run requires exactly {STABILITY_RENDER_COUNT} topic IDs")
    if len(set(normalized)) != len(normalized):
        raise StabilityRunError("Stability run requires ten distinct topic IDs")
    return normalized


def _require_frozen_gates(settings: Settings) -> dict[str, Any]:
    voice_path = production_voice_approval_path(settings)
    visual_path = visual_regression_baseline_path(settings)
    missing: list[str] = []
    if not voice_path.is_file():
        missing.append(f"Gate A production voice approval is missing: {voice_path}")
    if not visual_path.is_file():
        missing.append(f"Gates B-C accepted visual reference baseline is missing: {visual_path}")
    if missing:
        raise StabilityRunError(f"Gate A-C preflight failed: {'; '.join(missing)}")
    try:
        bundle = load_wealth_reference_acceptance(settings)
        voice = load_production_voice_approval(voice_path)
        visual = load_visual_regression_baseline(visual_path)
    except (OSError, ValueError, RuntimeError, ReferenceApprovalError) as exc:
        raise StabilityRunError(f"Gate A-C preflight failed: {exc}") from exc
    return {
        "voice_approval_path": str(voice_path),
        "voice_identity_hash": voice.voice_identity_hash,
        "voice_approval_signature": voice.approval_signature,
        "visual_baseline_path": str(visual_path),
        "visual_baseline_hash": visual.baseline_hash,
        "visual_approval_signature": visual.approval_signature,
        "reference_id": visual.reference_id,
        "acceptance_receipt_path": str(visual_path.with_name("reference_acceptance_receipt.json")),
        "acceptance_receipt_hash": bundle["record_hash"],
        "canonical_manifest_hash": bundle["canonical_manifest_hash"],
        "source_revision": bundle["source_revision"],
        "source_availability": _require_source_verification(settings),
    }


def _require_source_verification(settings: Settings) -> dict[str, Any]:
    path = (
        Path(settings.data_dir)
        / "production"
        / "evidence"
        / "wealth_v1_stability_sources_latest.json"
    )
    if not path.is_file():
        raise StabilityRunError(f"Source availability evidence is missing: {path}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(str(report.get("checked_at") or ""))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise StabilityRunError(f"Source availability evidence is invalid: {exc}") from exc
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    current_suite_hash = hashlib.sha256(DEFAULT_STABILITY_SUITE_PATH.read_bytes()).hexdigest()
    suite = load_stability_topic_suite(DEFAULT_STABILITY_SUITE_PATH)
    expected_urls = {topic.source_url for topic in suite.topics}
    sources = report.get("sources") if isinstance(report.get("sources"), list) else []
    observed_urls = {str(row.get("source_url") or "") for row in sources if isinstance(row, dict)}
    valid_records = all(
        isinstance(row, dict)
        and row.get("status") == "verified"
        and bool(row.get("content_sha256"))
        for row in sources
    )
    checks = {
        "passed": report.get("passed") is True,
        "suite_hash": str(report.get("suite_sha256") or "") == current_suite_hash,
        "source_set": observed_urls == expected_urls,
        "records": bool(sources) and valid_records,
        "fresh": datetime.now(UTC) - checked_at.astimezone(UTC) <= timedelta(days=7),
    }
    if not all(checks.values()):
        raise StabilityRunError(f"Source availability evidence failed preflight: {checks}")
    return {
        "status": "availability_verified",
        "report_path": str(path),
        "report_sha256": file_sha256(path),
        "checked_at": checked_at.isoformat(),
        "checks": checks,
        "limitation": "This proves primary-source availability and integrity hashes, not independent semantic truth.",
    }


def _validate_approved_topics(
    settings: Settings,
    account_id: str,
    topic_ids: list[str],
) -> dict[str, dict[str, Any]]:
    placeholders = ",".join("?" for _ in topic_ids)
    with connect(settings) as db:
        rows = db.execute(
            f"SELECT id, title, description, inspired_by, status FROM topic_queue WHERE account_id = ? AND id IN ({placeholders})",
            [account_id, *topic_ids],
        ).fetchall()
    statuses = {str(row["id"]): str(row["status"]) for row in rows}
    missing = [topic_id for topic_id in topic_ids if topic_id not in statuses]
    not_approved = [topic_id for topic_id in topic_ids if statuses.get(topic_id) != "approved"]
    if missing or not_approved:
        raise StabilityRunError(
            f"All stability topics must exist and be approved; missing={missing}, not_approved={not_approved}"
        )
    normalized_titles = [" ".join(str(row["title"] or "").casefold().split()) for row in rows]
    if any(not title for title in normalized_titles) or len(set(normalized_titles)) != STABILITY_RENDER_COUNT:
        raise StabilityRunError("Stability run requires ten approved topics with distinct non-empty titles")
    ungrounded = [str(row["id"]) for row in rows if not is_stability_source_record(row["inspired_by"])]
    if ungrounded:
        raise StabilityRunError(f"Stability topics are missing the source contract: {ungrounded}")
    canonical_suite = load_stability_topic_suite(DEFAULT_STABILITY_SUITE_PATH)
    canonical_topics = {topic.id: topic for topic in canonical_suite.topics}
    canonical_contracts = canonical_stability_contracts(DEFAULT_STABILITY_SUITE_PATH)
    contracts: dict[str, dict[str, Any]] = {}
    for row in rows:
        topic_id = str(row["id"])
        contract = parse_stability_source_record(row["inspired_by"]) or {}
        canonical_topic = canonical_topics.get(topic_id)
        if canonical_topic is None:
            raise StabilityRunError(f"Stability topic is outside the canonical suite: {topic_id}")
        if str(row["title"] or "") != canonical_topic.title or str(row["description"] or "") != canonical_topic.description:
            raise StabilityRunError(f"Stability topic text differs from the canonical suite: {topic_id}")
        if contract != canonical_contracts[topic_id]:
            raise StabilityRunError(f"Stability source contract differs from the canonical suite: {topic_id}")
        expected_scenes = contract.get("visual_test_focus")
        production_brief = contract.get("production_brief")
        if not isinstance(expected_scenes, list) or not expected_scenes:
            raise StabilityRunError(f"Stability topic has no visual_test_focus contract: {topic_id}")
        normalized_scenes = {str(item) for item in expected_scenes}
        if not normalized_scenes.issubset(ALLOWED_SCENE_TYPES):
            raise StabilityRunError(f"Stability topic requests unsupported scenes: {topic_id}")
        if not {"HookScene", "CTAScene"}.issubset(normalized_scenes):
            raise StabilityRunError(f"Stability topic must require HookScene and CTAScene: {topic_id}")
        if not isinstance(production_brief, dict) or not production_brief.get("expected_scene_evidence"):
            raise StabilityRunError(f"Stability topic has no production brief evidence: {topic_id}")
        contracts[topic_id] = contract
    return contracts


def _verify_completed_run(
    result: dict[str, Any],
    *,
    expected_account_id: str,
    expected_topic_id: str,
    expected_scene_types: list[str] | None = None,
    expected_scene_evidence: list[str] | None = None,
) -> dict[str, Any]:
    if str(result.get("status") or "") != "qa_passed":
        return {
            "status": "failed",
            "passed": False,
            "job_id": result.get("id"),
            "reason": f"job_status={result.get('status')}",
        }
    if str(result.get("account_id") or "") != expected_account_id:
        raise StabilityRunError("Completed job account does not match the requested account")
    if str(result.get("topic_id") or "") != expected_topic_id:
        raise StabilityRunError("Completed job topic does not match the requested topic")
    artifact_dir = Path(str(result.get("artifact_dir") or "")).expanduser().resolve()
    qa_path = artifact_dir / "qa_report.json"
    assembly_path = artifact_dir / "assembly_manifest.json"
    manifest_path = artifact_dir / "project_manifest.json"
    for required_path in (qa_path, assembly_path, manifest_path):
        if not required_path.is_file():
            raise StabilityRunError(f"Required production evidence is missing: {required_path}")
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    manifest = load_project_manifest(manifest_path)
    result_job_id = str(result.get("id") or "")
    if not result_job_id or manifest.job_id != result_job_id:
        raise StabilityRunError("Completed manifest job does not match the orchestrator result")
    if manifest.account_id != expected_account_id:
        raise StabilityRunError("Completed manifest account does not match the requested account")
    if manifest.topic_id != expected_topic_id:
        raise StabilityRunError("Completed manifest topic does not match the requested topic")
    if manifest.status != "qa_passed":
        raise StabilityRunError(f"Completed manifest status is not qa_passed: {manifest.status}")
    if qa.get("overall") != "passed" or qa.get("auto_uploadable") is not True:
        raise StabilityRunError("QA report does not prove a production PASS")
    if qa.get("blocking_failures"):
        raise StabilityRunError(f"QA report still contains blockers: {qa.get('blocking_failures')}")
    semantic_storyboard = _verify_semantic_storyboard_evidence(qa, artifact_dir=artifact_dir)
    final_video = _resolve_artifact_path(artifact_dir, assembly.get("output_file"))
    final_artifacts = [artifact for artifact in manifest.artifacts if artifact.role == "final_video"]
    if len(final_artifacts) != 1:
        raise StabilityRunError("Manifest must declare exactly one final_video artifact")
    manifest_final_video = _resolve_artifact_path(artifact_dir, final_artifacts[0].path)
    if manifest_final_video != final_video:
        raise StabilityRunError("Assembly output does not match the manifest final_video artifact")
    expected_final_hash = final_artifacts[0].artifact_hash or final_artifacts[0].provenance.artifact_hash
    actual_final_hash = file_sha256(final_video)
    if not expected_final_hash or actual_final_hash != expected_final_hash:
        raise StabilityRunError("Final video hash does not match the canonical manifest")
    media = _probe_final_media(final_video)
    scene_types = {scene.scene_type.value for scene in manifest.scenes}
    if not scene_types.issubset(ALLOWED_SCENE_TYPES):
        raise StabilityRunError(f"Manifest contains scene types outside V1: {sorted(scene_types - ALLOWED_SCENE_TYPES)}")
    if not {"HookScene", "CTAScene"}.issubset(scene_types):
        raise StabilityRunError("A stability Short must contain HookScene and CTAScene")
    _verify_required_scene_coverage(scene_types, expected_scene_types or [])
    scene_contract = _verify_scene_contract_details(
        manifest,
        expected_scene_evidence=expected_scene_evidence or [],
    )
    asset_evidence = _verify_manifest_assets(manifest, artifact_dir=artifact_dir)
    return {
        "status": "passed",
        "passed": True,
        "job_id": result_job_id,
        "artifact_dir": str(artifact_dir),
        "manifest_path": str(manifest_path),
        "manifest_content_hash": manifest.content_hash,
        "qa_report_path": str(qa_path),
        "qa_report_sha256": file_sha256(qa_path),
        "semantic_storyboard": semantic_storyboard,
        "final_video_path": str(final_video),
        "final_video_sha256": actual_final_hash,
        "media_probe": media,
        "scene_types": sorted(scene_types),
        "expected_scene_types": sorted({str(item) for item in expected_scene_types or []}),
        "scene_contract": scene_contract,
        "asset_integrity": asset_evidence,
    }


def _verify_semantic_storyboard_evidence(
    qa: dict[str, Any],
    *,
    artifact_dir: Path,
) -> dict[str, Any]:
    audit = qa.get("semantic_storyboard_audit")
    if not isinstance(audit, dict) or audit.get("passed") is not True or audit.get("status") != "pass":
        raise StabilityRunError("QA report does not contain a passing semantic storyboard audit")
    storyboard = _resolve_artifact_path(artifact_dir, audit.get("storyboard_path"))
    if not storyboard.is_file() or storyboard.stat().st_size <= 0:
        raise StabilityRunError(f"Semantic storyboard evidence is missing: {storyboard}")
    audit_input = audit.get("audit_input")
    if not isinstance(audit_input, dict):
        raise StabilityRunError("Semantic storyboard audit has no authenticated input metadata")
    expected_hash = str(audit_input.get("source_sha256") or "").strip().lower()
    actual_hash = str(file_sha256(storyboard) or "").lower()
    if not expected_hash or expected_hash != actual_hash:
        raise StabilityRunError("Semantic storyboard hash does not match the audited image")
    expected_size = int(audit_input.get("source_size_bytes") or 0)
    if expected_size <= 0 or expected_size != storyboard.stat().st_size:
        raise StabilityRunError("Semantic storyboard size does not match the audited image")
    return {
        "status": "verified",
        "storyboard_path": str(storyboard),
        "storyboard_sha256": actual_hash,
        "qa_audit_path": str(audit.get("audit_path") or ""),
        "model": str(audit.get("model") or ""),
    }


def _require_unique_run_receipt(existing_runs: list[dict[str, Any]], receipt: dict[str, Any]) -> None:
    if receipt.get("passed") is not True:
        return
    job_id = str(receipt.get("job_id") or "")
    video_hash = str(receipt.get("final_video_sha256") or "")
    if not job_id or not video_hash:
        raise StabilityRunError("A passing stability receipt requires job_id and final_video_sha256")
    if any(str(run.get("job_id") or "") == job_id for run in existing_runs):
        raise StabilityRunError(f"Stability series reused a production job: {job_id}")
    if any(str(run.get("final_video_sha256") or "") == video_hash for run in existing_runs):
        raise StabilityRunError(f"Stability series reused a final video artifact: {video_hash}")


def _verify_required_scene_coverage(actual: set[str], expected: list[str]) -> None:
    required = {str(item) for item in expected}
    if not required:
        raise StabilityRunError("Stability topic did not provide an expected scene contract")
    invalid = required - ALLOWED_SCENE_TYPES
    if invalid:
        raise StabilityRunError(f"Expected scene contract contains unsupported scenes: {sorted(invalid)}")
    missing = required - actual
    if missing:
        raise StabilityRunError(f"Rendered manifest is missing required scenes: {sorted(missing)}")


def _verify_scene_contract_details(
    manifest: Any,
    *,
    expected_scene_evidence: list[str],
) -> dict[str, Any]:
    if not expected_scene_evidence:
        raise StabilityRunError("Production brief contains no expected scene evidence")
    scene_types = {scene.scene_type.value for scene in manifest.scenes}
    evidence_checks: list[dict[str, Any]] = []
    evidence_scene_names = {
        "hook": "HookScene",
        "image": "ImageScene",
        "number": "NumberScene",
        "chart": "ChartScene",
        "cta": "CTAScene",
    }
    for evidence in expected_scene_evidence:
        normalized = str(evidence or "").strip()
        if not normalized:
            raise StabilityRunError("Production brief contains empty scene evidence")
        named_types = {
            scene_type
            for marker, scene_type in evidence_scene_names.items()
            if marker in normalized.casefold()
        }
        if named_types and not named_types.issubset(scene_types):
            raise StabilityRunError(
                f"Expected scene evidence is not represented in the manifest: {normalized}"
            )
        evidence_checks.append(
            {
                "evidence": normalized,
                "required_scene_types": sorted(named_types),
                "status": "structurally_verified",
            }
        )

    prior_image_scene_count = 0
    for scene in manifest.scenes:
        if scene.qa.status != "passed":
            raise StabilityRunError(
                f"Scene QA is not passed: {scene.scene_id}={scene.qa.status}"
            )
        if scene.scene_type.value == "NumberScene" and not scene.number_value:
            raise StabilityRunError(f"NumberScene has no source-backed value: {scene.scene_id}")
        if scene.scene_type.value == "ChartScene" and len(scene.chart_values) < 2:
            raise StabilityRunError(f"ChartScene has fewer than two values: {scene.scene_id}")
        if scene.scene_type.value == "ImageScene":
            selected = [
                asset
                for asset in scene.assets
                if asset.role == "selected_image" and asset.exists and asset.qa.status == "passed"
            ]
            if len(selected) != 1:
                raise StabilityRunError(
                    f"ImageScene needs exactly one QA-passed selected image: {scene.scene_id}"
                )
            selection = selected[0].provenance.generation_parameters.get("selection_evidence")
            selection = selection if isinstance(selection, dict) else {}
            comparison = selection.get("comparison") if isinstance(selection.get("comparison"), dict) else {}
            diversity = (
                selection.get("cross_scene_diversity")
                if isinstance(selection.get("cross_scene_diversity"), dict)
                else {}
            )
            if comparison.get("status") != "pass" or selection.get("decision") != "selected_by_comparative_vision":
                raise StabilityRunError(
                    f"ImageScene has no verified comparative-vision selection: {scene.scene_id}"
                )
            if (
                diversity.get("passed") is not True
                or int(diversity.get("expected_scene_count") or 0) != prior_image_scene_count
                or int(diversity.get("compared_scene_count") or 0) != prior_image_scene_count
            ):
                raise StabilityRunError(
                    f"ImageScene has incomplete cross-scene diversity evidence: {scene.scene_id}"
                )
            prior_image_scene_count += 1
    return {
        "status": "structurally_verified",
        "expected_evidence": evidence_checks,
        "limitation": (
            "Narrative semantics are recorded in the QA report whose SHA-256 is sealed by the signed "
            "stability report; this check proves manifest structure and selected-image evidence."
        ),
    }


def _verify_manifest_assets(manifest: Any, *, artifact_dir: Path) -> dict[str, Any]:
    scene_assets_checked = 0
    global_artifacts_checked = 0
    for scene in manifest.scenes:
        scene_checked = 0
        for asset in scene.assets:
            if not asset.exists:
                raise StabilityRunError(f"Manifest declares a missing scene asset: {asset.asset_id}")
            scene_assets_checked += 1
            scene_checked += 1
            path = _resolve_artifact_path(artifact_dir, asset.path)
            actual = file_sha256(path)
            expected = asset.provenance.artifact_hash
            if actual is None or not expected or actual != expected:
                raise StabilityRunError(f"Asset integrity mismatch: {asset.asset_id}")
        if scene_checked == 0:
            raise StabilityRunError(f"Scene has no verifiable assets: {scene.scene_id}")
    for artifact in manifest.artifacts:
        if not artifact.exists:
            raise StabilityRunError(f"Manifest declares a missing global artifact: {artifact.artifact_id}")
        global_artifacts_checked += 1
        path = _resolve_artifact_path(artifact_dir, artifact.path)
        actual = file_sha256(path)
        expected = artifact.artifact_hash or artifact.provenance.artifact_hash
        if actual is None or not expected or actual != expected:
            raise StabilityRunError(f"Global artifact integrity mismatch: {artifact.artifact_id}")
    if scene_assets_checked == 0:
        raise StabilityRunError("Manifest does not contain any verifiable final assets")
    if global_artifacts_checked == 0:
        raise StabilityRunError("Manifest does not contain any verifiable global artifacts")
    return {
        "status": "verified",
        "scene_assets_checked": scene_assets_checked,
        "global_artifacts_checked": global_artifacts_checked,
    }


def _resolve_artifact_path(artifact_dir: Path, value: Any) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise StabilityRunError("Production evidence contains an empty artifact path")
    root = artifact_dir.expanduser().resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StabilityRunError(f"Production artifact escapes the job directory: {resolved}") from exc
    return resolved


def _probe_final_media(path: Path) -> dict[str, Any]:
    ffprobe = resolve_ffprobe()
    if not ffprobe or not path.is_file() or path.stat().st_size <= 1024:
        raise StabilityRunError(f"Final MP4 is missing or ffprobe is unavailable: {path}")
    process = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.returncode != 0:
        raise StabilityRunError(f"ffprobe rejected final MP4: {(process.stderr or '')[-600:]}")
    payload = json.loads(process.stdout)
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    duration_sec = float(payload.get("format", {}).get("duration") or 0.0)
    checks = {
        "width": int(video.get("width") or 0) == 1080,
        "height": int(video.get("height") or 0) == 1920,
        "fps": abs(fps - 30.0) <= 0.01,
        "video_codec": str(video.get("codec_name") or "").casefold() == "h264",
        "pixel_format": str(video.get("pix_fmt") or "").casefold() == "yuv420p",
        "audio_codec": str(audio.get("codec_name") or "").casefold() == "aac",
        "sample_rate": int(audio.get("sample_rate") or 0) == 48000,
        "duration": 30.0 <= duration_sec < 60.0,
    }
    if not all(checks.values()):
        raise StabilityRunError(f"Final MP4 failed independent media checks: {checks}")
    return {
        "status": "verified",
        "checks": checks,
        "duration_sec": duration_sec,
        "bytes": path.stat().st_size,
    }


def _parse_rate(value: Any) -> float:
    text = str(value or "0")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        return float(numerator) / float(denominator) if float(denominator) else 0.0
    return float(text or 0.0)


def load_stability_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StabilityRunError(f"Stability report must contain a JSON object: {path}")
    stored_hash = str(payload.get("record_hash") or "")
    if not hmac.compare_digest(stored_hash, _report_hash(payload)):
        raise StabilityRunError(f"Stability report hash mismatch: {path}")
    stored_signature = str(payload.get("report_signature") or "")
    if not hmac.compare_digest(stored_signature, _report_signature(payload)):
        raise StabilityRunError(f"Stability report signature mismatch: {path}")
    return payload


def _persist_report(path: Path, report: dict[str, Any]) -> None:
    _validate_report_before_signing(report)
    report.pop("record_hash", None)
    report.pop("report_signature", None)
    report["record_hash"] = _report_hash(report)
    report["report_signature"] = _report_signature(report)
    _atomic_json_write(path, report)


def _validate_report_before_signing(report: dict[str, Any]) -> None:
    """Refuse to sign a terminal success that is not backed by live artifacts."""
    runs = report.get("runs")
    if not isinstance(runs, list):
        raise StabilityRunError("Stability report runs are invalid")
    status = str(report.get("status") or "")
    if status != "verified":
        return
    topic_ids = report.get("topic_ids")
    if not isinstance(topic_ids, list) or len(topic_ids) != STABILITY_RENDER_COUNT:
        raise StabilityRunError("Verified stability report requires the canonical ten-topic suite")
    if (
        report.get("passed") is not True
        or int(report.get("consecutive_passes") or 0) != STABILITY_RENDER_COUNT
        or len(runs) != STABILITY_RENDER_COUNT
    ):
        raise StabilityRunError("Verified stability report has an inconsistent terminal state")
    account_id = str(report.get("account_id") or "")
    if not account_id:
        raise StabilityRunError("Verified stability report has no account_id")
    for index, receipt in enumerate(runs, start=1):
        if (
            receipt.get("passed") is not True
            or int(receipt.get("ordinal") or 0) != index
            or str(receipt.get("topic_id") or "") != str(topic_ids[index - 1])
        ):
            raise StabilityRunError("Verified stability report contains an invalid run receipt")
        _require_unique_run_receipt(runs[: index - 1], receipt)
        _revalidate_run_receipt(receipt, expected_account_id=account_id)


def _revalidate_run_receipt(receipt: dict[str, Any], *, expected_account_id: str) -> None:
    """Re-hash every persisted proof instead of trusting a prior PASS label."""
    artifact_dir = Path(str(receipt.get("artifact_dir") or "")).expanduser().resolve()
    if not artifact_dir.is_dir():
        raise StabilityRunError(f"Stability artifact directory is missing: {artifact_dir}")
    manifest_path = _resolve_artifact_path(artifact_dir, receipt.get("manifest_path"))
    qa_path = _resolve_artifact_path(artifact_dir, receipt.get("qa_report_path"))
    final_video = _resolve_artifact_path(artifact_dir, receipt.get("final_video_path"))
    manifest = load_project_manifest(manifest_path)
    if manifest.job_id != str(receipt.get("job_id") or ""):
        raise StabilityRunError("Stability receipt job no longer matches its manifest")
    if manifest.account_id != expected_account_id:
        raise StabilityRunError("Stability receipt account no longer matches its manifest")
    if manifest.topic_id != str(receipt.get("topic_id") or ""):
        raise StabilityRunError("Stability receipt topic no longer matches its manifest")
    if manifest.content_hash != str(receipt.get("manifest_content_hash") or ""):
        raise StabilityRunError("Stability receipt manifest content hash mismatch")
    if file_sha256(qa_path) != str(receipt.get("qa_report_sha256") or ""):
        raise StabilityRunError("Stability receipt QA report hash mismatch")
    if file_sha256(final_video) != str(receipt.get("final_video_sha256") or ""):
        raise StabilityRunError("Stability receipt final video hash mismatch")
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    if qa.get("overall") != "passed" or qa.get("auto_uploadable") is not True or qa.get("blocking_failures"):
        raise StabilityRunError("Stability receipt QA evidence no longer proves a PASS")
    semantic = _verify_semantic_storyboard_evidence(qa, artifact_dir=artifact_dir)
    recorded_semantic = receipt.get("semantic_storyboard")
    if not isinstance(recorded_semantic, dict) or semantic.get("storyboard_sha256") != recorded_semantic.get(
        "storyboard_sha256"
    ):
        raise StabilityRunError("Stability receipt semantic storyboard evidence mismatch")
    _verify_manifest_assets(manifest, artifact_dir=artifact_dir)
    _probe_final_media(final_video)


def _report_hash(report: dict[str, Any]) -> str:
    payload = dict(report)
    payload.pop("record_hash", None)
    payload.pop("report_signature", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _report_signature(report: dict[str, Any]) -> str:
    key = os.getenv(SIGNING_KEY_ENV, "").encode("utf-8")
    if len(key) < 32:
        raise StabilityRunError(f"Stability evidence requires {SIGNING_KEY_ENV} with at least 32 bytes")
    message = ":".join(
        (
            str(report.get("record_hash") or _report_hash(report)),
            str(report.get("run_id") or ""),
            str(report.get("status") or ""),
            str(report.get("consecutive_passes") or 0),
        )
    )
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


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
