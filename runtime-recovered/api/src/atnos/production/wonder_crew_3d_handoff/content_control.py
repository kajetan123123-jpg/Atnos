"""Content control for Wonder Crew 3D handoff packages.

Filename / resolution / file presence are insufficient for READY_FOR_3D.
Mandatory turnaround views must be verified as the correct view of the same
character, outfit, and version — with an explicit audit trail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Turnaround views that must match label and be dedicated single-character shots.
MANDATORY_TURNAROUND_VIEWS: tuple[str, ...] = (
    "FRONT",
    "SIDE",
    "BACK",
    "THREE_QUARTER",
)

CONTENT_STATUS_PASS = "PASS"
CONTENT_STATUS_FAIL = "FAIL"
CONTENT_STATUS_LIMITED = "LIMITED"
CONTENT_STATUS_UNVERIFIED = "UNVERIFIED"

# Approvers that never count as production approval (bootstrap / test builders).
NON_PRODUCTION_APPROVERS: frozenset[str] = frozenset(
    {
        "handoff_contract_bootstrap",
        "build_niko_test_package",
        "test_builder",
        "auto",
        "bootstrap",
    }
)

KNOWN_LIMITATION_CODES: frozenset[str] = frozenset(
    {
        "face_sheet_fragment_not_back_view",
        "sheet_panel_crop_not_dedicated_view",
        "bio_sheet_fragment_not_side_view",
        "dual_panel_crop_not_clean_single_front",
        "view_identity_unverified_or_mismatched",
        "missing_content_control",
        "view_label_mismatch",
        "unverified_view_identity",
        "mixed_outfit_versions",
        "missing_production_approval_audit",
        "bootstrap_approval_not_production",
    }
)


def build_content_control(
    *,
    declared_view: str,
    observed_view: str,
    view_matches_label: bool,
    content_status: str,
    limitations: list[str] | None = None,
    provenance: str,
    character_id: str,
    costume_version: str,
    evidence_basis: str = "builder_provenance",
    verified_by: str | None = None,
    verified_at: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Build an explicit content-control record (missing record ≠ pass)."""
    return {
        "declared_view": declared_view.upper(),
        "observed_view": observed_view.upper(),
        "view_matches_label": bool(view_matches_label),
        "content_status": content_status.upper(),
        "limitations": list(limitations or []),
        "provenance": provenance,
        "same_character_id": character_id.lower(),
        "same_outfit_version": costume_version,
        "evidence_basis": evidence_basis,
        "verified_by": verified_by,
        "verified_at": verified_at,
        "notes": notes,
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def empty_approval_audit(*, actor: str, note: str = "") -> dict[str, Any]:
    return {
        "production_approved": False,
        "production_approved_by": None,
        "production_approved_at": None,
        "canonical_granted": False,
        "events": [
            {
                "action": "package_structure_built",
                "at": datetime.now(UTC).isoformat(),
                "by": actor,
                "grants_production_approval": False,
                "grants_canonical": False,
                "note": note
                or "Structure/fixture build only — does not grant production approval or canonical.",
            }
        ],
    }


def append_audit_event(audit: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    events = list(audit.get("events") or [])
    events.append(event)
    audit = dict(audit)
    audit["events"] = events
    return audit


def is_production_approval_valid(package: dict[str, Any]) -> tuple[bool, list[str]]:
    """Real production approval requires explicit audit — bootstrap auto-approve fails."""
    errors: list[str] = []
    audit = package.get("approval_audit")
    if not isinstance(audit, dict):
        errors.append("missing_production_approval_audit: package.approval_audit required")
        return False, errors

    if not audit.get("production_approved"):
        errors.append("missing_production_approval_audit: production_approved is false")

    approved_by = str(audit.get("production_approved_by") or "").strip()
    if not approved_by:
        errors.append("missing_production_approval_audit: production_approved_by empty")
    elif approved_by.lower() in NON_PRODUCTION_APPROVERS:
        errors.append(
            f"bootstrap_approval_not_production: approved_by={approved_by!r} cannot grant READY_FOR_3D"
        )

    if not audit.get("production_approved_at"):
        errors.append("missing_production_approval_audit: production_approved_at empty")

    # Legacy bootstrap metadata alone is never enough.
    legacy = package.get("approval_metadata") or {}
    legacy_by = str(legacy.get("approved_by") or "").strip().lower()
    if package.get("approved") and legacy_by in NON_PRODUCTION_APPROVERS and not audit.get("production_approved"):
        errors.append(
            "bootstrap_approval_not_production: package.approved set by non-production approver"
        )

    return len(errors) == 0, errors


def assess_asset_content_control(
    asset: dict[str, Any],
    *,
    package_character_id: str,
    package_costume_version: str,
) -> tuple[str, list[str]]:
    """Return content_status + error codes for one asset (missing evidence ≠ pass)."""
    errors: list[str] = []
    ref = str(asset.get("reference_type") or "").upper()
    cc = asset.get("content_control")

    if ref not in MANDATORY_TURNAROUND_VIEWS:
        # Non-turnaround required types still need presence; content identity softer.
        if not isinstance(cc, dict):
            return CONTENT_STATUS_UNVERIFIED, []
        return str(cc.get("content_status") or CONTENT_STATUS_UNVERIFIED).upper(), []

    if not isinstance(cc, dict):
        errors.append(f"missing_content_control on {asset.get('asset_id')} ({ref})")
        return CONTENT_STATUS_UNVERIFIED, errors

    declared = str(cc.get("declared_view") or ref).upper()
    observed = str(cc.get("observed_view") or "").upper()
    status = str(cc.get("content_status") or CONTENT_STATUS_UNVERIFIED).upper()
    matches = bool(cc.get("view_matches_label"))

    if declared != ref:
        errors.append(
            f"view_label_mismatch on {asset.get('asset_id')}: "
            f"declared_view={declared} vs reference_type={ref}"
        )
    if not matches or declared != observed:
        # observed may be more specific (e.g. FRONT_DUAL_PANEL) — allow if matches + PASS
        if not matches:
            errors.append(
                f"view_label_mismatch on {asset.get('asset_id')}: "
                f"declared={declared} observed={observed or 'UNKNOWN'} view_matches_label=false"
            )
    if status == CONTENT_STATUS_UNVERIFIED:
        errors.append(f"unverified_view_identity on {asset.get('asset_id')}")
    if status in {CONTENT_STATUS_FAIL, CONTENT_STATUS_LIMITED}:
        lims = cc.get("limitations") or []
        detail = ",".join(str(x) for x in lims) if lims else status
        errors.append(
            f"content_control_{status.lower()} on {asset.get('asset_id')} ({ref}): {detail}"
        )
    if status != CONTENT_STATUS_PASS:
        if f"content_control_{status.lower()}" not in " ".join(errors):
            errors.append(
                f"content_control not PASS on {asset.get('asset_id')}: status={status}"
            )

    same_char = str(cc.get("same_character_id") or asset.get("character_id") or "").lower()
    if same_char != package_character_id.lower():
        errors.append(
            f"character mismatch in content_control on {asset.get('asset_id')}: "
            f"{same_char} != {package_character_id}"
        )

    same_outfit = str(cc.get("same_outfit_version") or asset.get("costume_version") or "")
    if same_outfit != package_costume_version:
        errors.append(
            f"mixed_outfit_versions on {asset.get('asset_id')}: "
            f"{same_outfit} != {package_costume_version}"
        )

    # Explicit known failure: face sheet labeled as BACK
    lim_set = {str(x) for x in (cc.get("limitations") or [])}
    if ref == "BACK" and (
        "face_sheet_fragment_not_back_view" in lim_set
        or observed in {"FACE_SHEET_FRAGMENT", "FACE_SHEET", "EXPRESSION_SHEET_FRAGMENT"}
    ):
        if not any("face_sheet_fragment_not_back_view" in e for e in errors):
            errors.append(
                f"face_sheet_fragment_not_back_view on {asset.get('asset_id')}: "
                "BACK label but content is a face-sheet fragment"
            )

    return status, errors


def validate_content_control(package: dict[str, Any]) -> dict[str, Any]:
    """Content gate for READY_FOR_3D — independent from structure validity."""
    errors: list[str] = []
    warnings: list[str] = []
    character_id = str(package.get("character_id") or "")
    costume_version = str(package.get("costume_version") or "")
    assets = package.get("assets") or []
    by_type = {str(a.get("reference_type") or "").upper(): a for a in assets if isinstance(a, dict)}

    for view in MANDATORY_TURNAROUND_VIEWS:
        asset = by_type.get(view)
        if not asset:
            errors.append(f"missing mandatory turnaround view for content control: {view}")
            continue
        status, asset_errors = assess_asset_content_control(
            asset,
            package_character_id=character_id,
            package_costume_version=costume_version,
        )
        errors.extend(asset_errors)
        if status != CONTENT_STATUS_PASS:
            warnings.append(f"{view} content_status={status}")

    # Same outfit version across all assets (structure also checks; content re-asserts).
    outfits = {str(a.get("costume_version") or "") for a in assets if isinstance(a, dict)}
    if len(outfits) > 1:
        errors.append(f"mixed_outfit_versions across assets: {sorted(outfits)}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def niko_v001_honest_content_controls(costume_version: str = "COSTUME_V001") -> dict[str, dict[str, Any]]:
    """Honest assessments for the known WC_NIKO_V001 sheet-crop package."""
    return {
        "FRONT": build_content_control(
            declared_view="FRONT",
            observed_view="FRONT_DUAL_PANEL",
            view_matches_label=True,
            content_status=CONTENT_STATUS_LIMITED,
            limitations=["dual_panel_crop_not_clean_single_front", "sheet_panel_crop_not_dedicated_view"],
            provenance="dedicated_front_crop_from_channel_references",
            character_id="niko",
            costume_version=costume_version,
            notes="Front-ish dual panel; not a clean dedicated orthographic FRONT for 3D.",
        ),
        "THREE_QUARTER": build_content_control(
            declared_view="THREE_QUARTER",
            observed_view="SHEET_PANEL_MIXED",
            view_matches_label=False,
            content_status=CONTENT_STATUS_FAIL,
            limitations=["sheet_panel_crop_not_dedicated_view", "view_identity_unverified_or_mismatched"],
            provenance="expression_sheet_panel_crop",
            character_id="niko",
            costume_version=costume_version,
            notes="Sheet panel crop; not a verified dedicated THREE_QUARTER of the same lock.",
        ),
        "SIDE": build_content_control(
            declared_view="SIDE",
            observed_view="BIO_SHEET_FRAGMENT",
            view_matches_label=False,
            content_status=CONTENT_STATUS_FAIL,
            limitations=["bio_sheet_fragment_not_side_view", "sheet_panel_crop_not_dedicated_view"],
            provenance="expression_sheet_panel_crop",
            character_id="niko",
            costume_version=costume_version,
            notes="Bio/face sheet fragment with text — not a clean SIDE/profile turnaround.",
        ),
        "BACK": build_content_control(
            declared_view="BACK",
            observed_view="FACE_SHEET_FRAGMENT",
            view_matches_label=False,
            content_status=CONTENT_STATUS_FAIL,
            limitations=["face_sheet_fragment_not_back_view", "sheet_panel_crop_not_dedicated_view"],
            provenance="expression_sheet_panel_crop",
            character_id="niko",
            costume_version=costume_version,
            notes="Confirmed: face-sheet fragment (expressions/variants), not a back view.",
        ),
        "EXPRESSION": build_content_control(
            declared_view="EXPRESSION",
            observed_view="EXPRESSION",
            view_matches_label=True,
            content_status=CONTENT_STATUS_LIMITED,
            limitations=["sheet_panel_crop_not_dedicated_view"],
            provenance="full_expression_sheet_copy",
            character_id="niko",
            costume_version=costume_version,
            notes="Full sheet present; usable as expression reference but package turnaround incomplete.",
        ),
        "COLOR": build_content_control(
            declared_view="COLOR",
            observed_view="COLOR_SWATCH_CROP",
            view_matches_label=True,
            content_status=CONTENT_STATUS_LIMITED,
            limitations=["sheet_panel_crop_not_dedicated_view"],
            provenance="expression_sheet_panel_crop",
            character_id="niko",
            costume_version=costume_version,
        ),
        "OUTFIT": build_content_control(
            declared_view="OUTFIT",
            observed_view="OUTFIT_FROM_FRONT_CROP",
            view_matches_label=True,
            content_status=CONTENT_STATUS_LIMITED,
            limitations=["dual_panel_crop_not_clean_single_front"],
            provenance="copy_of_front_crop",
            character_id="niko",
            costume_version=costume_version,
        ),
        "PROP_DETAIL": build_content_control(
            declared_view="PROP_DETAIL",
            observed_view="PROP_DETAIL",
            view_matches_label=True,
            content_status=CONTENT_STATUS_LIMITED,
            limitations=["sheet_panel_crop_not_dedicated_view"],
            provenance="portrait_crop",
            character_id="niko",
            costume_version=costume_version,
        ),
    }
