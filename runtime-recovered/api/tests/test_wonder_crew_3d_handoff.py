"""Wonder Crew → Blender 3D handoff contract tests.

Structure validity is separate from production approval / READY_FOR_3D.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from atnos.production.wonder_crew_3d_handoff import (
    REQUIRED_REFERENCE_TYPES,
    asset_id,
    build_niko_test_package,
    empty_approval_audit,
    package_id,
    parse_asset_id,
    validate_package,
    validate_package_structure,
    write_package_files,
)
from atnos.production.wonder_crew_3d_handoff.content_control import (
    CONTENT_STATUS_FAIL,
    CONTENT_STATUS_PASS,
    build_content_control,
)


def test_naming_patterns():
    assert asset_id("niko", "FRONT", 1) == "WC_NIKO_FRONT_V001"
    assert asset_id("niko", "THREE_QUARTER", 2) == "WC_NIKO_THREE_QUARTER_V002"
    assert asset_id("niko", "3/4", 1) == "WC_NIKO_THREE_QUARTER_V001"
    assert package_id("niko", 1) == "WC_NIKO_V001"
    parsed = parse_asset_id("WC_NIKO_EXPRESSION_V002")
    assert parsed["character"] == "niko"
    assert parsed["reference_type"] == "EXPRESSION"
    assert parsed["version"] == 2


def test_builder_does_not_auto_grant_approval_or_canonical(tmp_path):
    with pytest.raises(ValueError, match="must not auto-grant production approval"):
        build_niko_test_package(
            version=9,
            packages_root_override=tmp_path,
            overwrite=True,
            grant_production_approval=True,
        )
    with pytest.raises(ValueError, match="must not auto-set canonical"):
        build_niko_test_package(
            version=9,
            packages_root_override=tmp_path,
            overwrite=True,
            set_as_canonical=True,
        )


def test_structure_can_pass_without_production_status(tmp_path):
    result = build_niko_test_package(version=9, packages_root_override=tmp_path, overwrite=True)
    assert result["approved"] is False
    assert result["canonical"] is False
    assert result["ready_for_3d"] is False
    assert result["status"] == "COMPLETE"
    assert result["validation"]["structure_ok"] is True
    assert result["validation"]["ok"] is True
    assert result["validation"]["content_ok"] is False
    assert result["validation"]["approval_ok"] is False
    assert result["validation"]["ready_for_3d"] is False

    pkg = Path(result["package_path"])
    structure = validate_package_structure(pkg)
    assert structure["structure_ok"] is True
    assert structure["ready_for_3d"] is False

    package = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    types = {a["reference_type"] for a in package["assets"]}
    for req in REQUIRED_REFERENCE_TYPES:
        assert req in types
    for asset in package["assets"]:
        assert (pkg / asset["relative_path"]).is_file()
        assert asset["approved"] is False


def test_face_labeled_as_back_not_ready(tmp_path):
    result = build_niko_test_package(version=9, packages_root_override=tmp_path, overwrite=True)
    pkg = Path(result["package_path"])
    package = json.loads((pkg / "package.json").read_text(encoding="utf-8"))

    front_src = Path(r"E:\Atnos\App\config\channel_references\20260916\crops\niko_front_v1.png")
    back_asset = next(a for a in package["assets"] if a["reference_type"] == "BACK")
    dest = pkg / back_asset["relative_path"]
    shutil.copy2(front_src, dest)

    # Explicit content control: face/front content labeled BACK
    back_asset["content_control"] = build_content_control(
        declared_view="BACK",
        observed_view="FACE_SHEET_FRAGMENT",
        view_matches_label=False,
        content_status=CONTENT_STATUS_FAIL,
        limitations=["face_sheet_fragment_not_back_view"],
        provenance="mislabeled_face_as_back_test",
        character_id="niko",
        costume_version=package["costume_version"],
        notes="Face/front material labeled as BACK must not unlock READY_FOR_3D.",
    )
    back_asset["notes"] = "TEST: face labeled as BACK"
    write_package_files(package, pkg)

    report = validate_package(pkg)
    assert report["structure_ok"] is True
    assert report["content_ok"] is False
    assert report["ready_for_3d"] is False
    assert any("face_sheet_fragment_not_back_view" in e for e in report["content_errors"])


def test_missing_approval_not_ready(tmp_path):
    result = build_niko_test_package(version=9, packages_root_override=tmp_path, overwrite=True)
    pkg = Path(result["package_path"])
    package = json.loads((pkg / "package.json").read_text(encoding="utf-8"))

    # Even if content were forced PASS, missing production audit blocks ready.
    for asset in package["assets"]:
        ref = asset["reference_type"]
        if ref in {"FRONT", "SIDE", "BACK", "THREE_QUARTER"}:
            asset["content_control"] = build_content_control(
                declared_view=ref,
                observed_view=ref,
                view_matches_label=True,
                content_status=CONTENT_STATUS_PASS,
                limitations=[],
                provenance="forced_pass_for_approval_gate_test",
                character_id="niko",
                costume_version=package["costume_version"],
                evidence_basis="test_override",
                verified_by="unit_test",
                verified_at="2026-09-19T00:00:00+00:00",
            )
    package["approval_audit"] = empty_approval_audit(actor="unit_test")
    package["approved"] = False
    write_package_files(package, pkg)

    report = validate_package(pkg)
    assert report["structure_ok"] is True
    assert report["content_ok"] is True
    assert report["approval_ok"] is False
    assert report["ready_for_3d"] is False
    assert any("missing_production_approval" in e or "production_approved is false" in e for e in report["approval_errors"])


def test_mixed_outfit_versions_not_ready(tmp_path):
    result = build_niko_test_package(version=9, packages_root_override=tmp_path, overwrite=True)
    pkg = Path(result["package_path"])
    package = json.loads((pkg / "package.json").read_text(encoding="utf-8"))

    for asset in package["assets"]:
        if asset["reference_type"] == "BACK":
            asset["costume_version"] = "COSTUME_V999"
            cc = dict(asset.get("content_control") or {})
            cc["same_outfit_version"] = "COSTUME_V999"
            asset["content_control"] = cc
    write_package_files(package, pkg)

    report = validate_package(pkg)
    assert report["ready_for_3d"] is False
    # Structure catches costume_version field mismatch; content also flags mixed outfits.
    assert report["structure_ok"] is False or report["content_ok"] is False
    blob = " ".join(report["errors"])
    assert "costume_version mismatch" in blob or "mixed_outfit_versions" in blob


def test_validation_fails_when_file_missing(tmp_path):
    result = build_niko_test_package(version=9, packages_root_override=tmp_path, overwrite=True)
    pkg = Path(result["package_path"])
    victim = next((pkg / "assets").glob("WC_NIKO_SIDE_*.png"))
    victim.unlink()
    report = validate_package(pkg)
    assert report["structure_ok"] is False
    assert report["ok"] is False
    assert report["ready_for_3d"] is False
    assert any("missing file" in e for e in report["structure_errors"])


def test_independent_consumer_script_fail_closed_without_canonical():
    script = Path(r"E:\Atnos\App\scripts\consume_wonder_crew_3d_manifest.py")
    proc = subprocess.run(
        [sys.executable, str(script), "niko"],
        capture_output=True,
        text=True,
        check=False,
    )
    # After false-readiness correction, Niko must not resolve as ready canonical.
    assert proc.returncode != 0
    assert "No canonical package" in (proc.stderr or proc.stdout) or "canonical" in (
        proc.stderr or proc.stdout
    ).lower()


def test_live_niko_v001_not_falsely_ready():
    pkg = Path(r"E:\Atnos\App\data\wonder_crew_3d_handoff\packages\niko\WC_NIKO_V001")
    if not (pkg / "package.json").is_file():
        pytest.skip("WC_NIKO_V001 not present")
    report = validate_package(pkg)
    assert report["ready_for_3d"] is False
    package = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    assert package.get("canonical") is False
    assert package.get("status") != "READY_FOR_3D"
    assert package.get("approved") is False
    back = next(a for a in package["assets"] if a["reference_type"] == "BACK")
    cc = back.get("content_control") or {}
    assert cc.get("view_matches_label") is False
    assert "face_sheet_fragment_not_back_view" in (cc.get("limitations") or [])
