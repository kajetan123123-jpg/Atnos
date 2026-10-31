"""Wonder Crew → future Blender 3D handoff contract.

Comfy-approved character reference packages with unambiguous naming,
canonical versioning, and a Blender-readable manifest.
Does not implement Blender modeling/rigging.

Structure validity ≠ production approval ≠ READY_FOR_3D.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from atnos.production.wonder_crew_3d_handoff.content_control import (
    MANDATORY_TURNAROUND_VIEWS,
    NON_PRODUCTION_APPROVERS,
    assess_asset_content_control,
    empty_approval_audit,
    is_production_approval_valid,
    niko_v001_honest_content_controls,
    validate_content_control,
)

CHANNEL = "wonder_crew"
CHARACTERS = ("niko", "pif", "tika", "mira")

ReferenceType = Literal[
    "FRONT",
    "THREE_QUARTER",
    "SIDE",
    "BACK",
    "EXPRESSION",
    "COLOR",
    "OUTFIT",
    "PROP_DETAIL",
]

REQUIRED_REFERENCE_TYPES: tuple[ReferenceType, ...] = (
    "FRONT",
    "THREE_QUARTER",
    "SIDE",
    "BACK",
    "EXPRESSION",
    "COLOR",
    "OUTFIT",
)
OPTIONAL_REFERENCE_TYPES: tuple[ReferenceType, ...] = ("PROP_DETAIL",)
ALL_REFERENCE_TYPES: tuple[ReferenceType, ...] = REQUIRED_REFERENCE_TYPES + OPTIONAL_REFERENCE_TYPES

PackageStatus = Literal[
    "DRAFT",
    "COMPLETE",
    "AWAITING_REVIEW",
    "APPROVED",
    "READY_FOR_3D",
    "SUPERSEDED",
    "NOT_READY",
]

ASSET_NAME_RE = re.compile(
    r"^WC_(?P<character>[A-Z]+)_(?P<ref_type>[A-Z0-9_]+)_V(?P<version>\d{3})$"
)
PACKAGE_ID_RE = re.compile(r"^WC_(?P<character>[A-Z]+)_V(?P<version>\d{3})$")


def contract_path() -> Path:
    return Path(r"E:\Atnos\App\config\wonder_crew_3d_handoff\contract.json")


def canonical_registry_path() -> Path:
    return Path(r"E:\Atnos\App\config\wonder_crew_3d_handoff\canonical_characters.json")


def packages_root() -> Path:
    return Path(r"E:\Atnos\App\data\wonder_crew_3d_handoff\packages")


def evidence_root() -> Path:
    return Path(r"E:\Atnos\App\data\wonder_crew_3d_handoff\evidence")


def load_contract() -> dict[str, Any]:
    return json.loads(contract_path().read_text(encoding="utf-8"))


def load_canonical_registry() -> dict[str, Any]:
    path = canonical_registry_path()
    if not path.is_file():
        return {"version": 1, "channel": CHANNEL, "characters": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_canonical_registry(payload: dict[str, Any]) -> None:
    path = canonical_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def format_version(version: int) -> str:
    if version < 1 or version > 999:
        raise ValueError(f"version out of range 1..999: {version}")
    return f"{version:03d}"


def asset_id(character_id: str, reference_type: str, version: int) -> str:
    ch = character_id.strip().upper()
    rt = reference_type.strip().upper().replace("3/4", "THREE_QUARTER").replace("-", "_")
    if rt == "3_4":
        rt = "THREE_QUARTER"
    return f"WC_{ch}_{rt}_V{format_version(version)}"


def package_id(character_id: str, version: int) -> str:
    return f"WC_{character_id.strip().upper()}_V{format_version(version)}"


def parse_asset_id(value: str) -> dict[str, Any]:
    m = ASSET_NAME_RE.match(value.strip())
    if not m:
        raise ValueError(f"Invalid asset id: {value}")
    return {
        "character": m.group("character").lower(),
        "reference_type": m.group("ref_type"),
        "version": int(m.group("version")),
        "asset_id": value.strip(),
    }


def parse_package_id(value: str) -> dict[str, Any]:
    m = PACKAGE_ID_RE.match(value.strip())
    if not m:
        raise ValueError(f"Invalid package id: {value}")
    return {
        "character": m.group("character").lower(),
        "version": int(m.group("version")),
        "package_id": value.strip(),
    }


def package_dir(character_id: str, version: int, *, root: Path | None = None) -> Path:
    base = root if root is not None else packages_root()
    return base / character_id.lower() / package_id(character_id, version)


def extract_palette(image_path: Path, max_colors: int = 8) -> list[str]:
    with Image.open(image_path) as img:
        small = img.convert("RGB").resize((64, 64))
        quantized = small.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        colors: list[str] = []
        for i in range(max_colors):
            r, g, b = palette[i * 3 : i * 3 + 3]
            colors.append(f"#{r:02X}{g:02X}{b:02X}")
        return colors


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def _ensure_min_size(img: Image.Image, min_w: int = 256, min_h: int = 256) -> Image.Image:
    w, h = img.size
    if w >= min_w and h >= min_h:
        return img
    scale = max(min_w / max(w, 1), min_h / max(h, 1))
    nw, nh = max(min_w, int(round(w * scale))), max(min_h, int(round(h * scale)))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def _crop_frac(src: Path, box: tuple[float, float, float, float], dest: Path) -> Path:
    with Image.open(src) as img:
        w, h = img.size
        x0, y0, x1, y1 = box
        crop = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))).convert("RGB")
        crop = _ensure_min_size(crop)
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, format="PNG")
    return dest


def _copy_ensure_size(src: Path, dest: Path) -> Path:
    with Image.open(src) as img:
        out = _ensure_min_size(img.convert("RGB"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(dest, format="PNG")
    return dest


def build_reference_asset(
    *,
    character_id: str,
    reference_type: ReferenceType,
    version: int,
    source_path: Path,
    dest_dir: Path,
    workflow: str,
    model: str,
    anchor: str,
    approved: bool,
    approval_metadata: dict[str, Any] | None,
    notes: str = "",
    costume_version: str = "COSTUME_V001",
    copy_mode: Literal["copy", "crop"] = "copy",
    crop_box: tuple[float, float, float, float] | None = None,
    content_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    aid = asset_id(character_id, reference_type, version)
    dest = dest_dir / f"{aid}.png"
    if copy_mode == "crop":
        if crop_box is None:
            raise ValueError("crop_box required for crop mode")
        _crop_frac(source_path, crop_box, dest)
    else:
        _copy_ensure_size(source_path, dest)
    width, height = _image_size(dest)
    palette = extract_palette(dest) if reference_type in {"COLOR", "OUTFIT", "FRONT"} else []
    return {
        "asset_id": aid,
        "character_id": character_id.lower(),
        "reference_type": reference_type,
        "version": format_version(version),
        "version_int": version,
        "filename": dest.name,
        "relative_path": f"assets/{dest.name}",
        "absolute_path": str(dest.resolve()),
        "workflow": workflow,
        "model": model,
        "anchor": anchor,
        "dimensions": {"width": width, "height": height},
        "color_palette": palette,
        "costume_version": costume_version,
        "approved": bool(approved),
        "approval_metadata": approval_metadata or {},
        "content_control": content_control,
        "created_at": datetime.now(UTC).isoformat(),
        "notes": notes,
        "source_path": str(source_path.resolve()),
    }


def build_manifest(package: dict[str, Any]) -> dict[str, Any]:
    """Blender-facing entry document — no folder scanning required."""
    refs = {a["reference_type"]: a for a in package["assets"]}
    return {
        "schema": "wonder_crew_character_reference_manifest_v1",
        "channel": CHANNEL,
        "package_id": package["package_id"],
        "character_id": package["character_id"],
        "package_version": package["version"],
        "canonical": package.get("canonical", False),
        "status": package["status"],
        "costume_version": package["costume_version"],
        "ready_for_3d": package["status"] == "READY_FOR_3D",
        "structure_valid": package.get("structure_valid"),
        "content_control_ok": package.get("content_control_ok"),
        "production_approved": bool((package.get("approval_audit") or {}).get("production_approved")),
        "limitations": package.get("limitations") or [],
        "canonical_views": {
            "front": refs.get("FRONT"),
            "three_quarter": refs.get("THREE_QUARTER"),
            "side": refs.get("SIDE"),
            "back": refs.get("BACK"),
        },
        "expressions": refs.get("EXPRESSION"),
        "color_reference": refs.get("COLOR"),
        "costume": refs.get("OUTFIT"),
        "associated_props": [refs["PROP_DETAIL"]] if "PROP_DETAIL" in refs else [],
        "assets_index": [
            {
                "asset_id": a["asset_id"],
                "reference_type": a["reference_type"],
                "relative_path": a["relative_path"],
                "approved": a.get("approved", False),
                "content_status": (a.get("content_control") or {}).get("content_status"),
            }
            for a in package["assets"]
        ],
        "package_metadata": {
            "workflow": package.get("workflow"),
            "model": package.get("model"),
            "anchor": package.get("anchor"),
            "approval_metadata": package.get("approval_metadata"),
            "approval_audit": package.get("approval_audit"),
            "created_at": package.get("created_at"),
            "supersedes": package.get("supersedes"),
            "notes": package.get("notes"),
        },
        "consumer_instructions": {
            "resolve_paths_relative_to": "this_manifest_directory",
            "prefer_canonical_registry": str(canonical_registry_path()),
            "do_not_guess_older_versions": True,
            "ready_for_3d_requires_content_and_production_approval": True,
        },
    }


def validate_package_structure(package_path: Path) -> dict[str, Any]:
    """Structure / contract validity only — does NOT grant production status."""
    pkg_file = package_path / "package.json"
    manifest_file = package_path / "character_reference_manifest.json"
    errors: list[str] = []
    warnings: list[str] = []

    if not pkg_file.is_file():
        return {
            "ok": False,
            "structure_ok": False,
            "ready_for_3d": False,
            "errors": ["missing package.json"],
            "warnings": [],
        }
    package = json.loads(pkg_file.read_text(encoding="utf-8"))

    character_id = str(package.get("character_id") or "")
    version = int(package.get("version_int") or 0)
    costume_version = str(package.get("costume_version") or "")
    contract = load_contract()
    dim_policy = contract.get("dimensions_policy") or {}
    min_w = int(dim_policy.get("min_width") or 256)
    min_h = int(dim_policy.get("min_height") or 256)

    assets = package.get("assets") or []
    by_type = {a.get("reference_type"): a for a in assets if isinstance(a, dict)}

    for req in REQUIRED_REFERENCE_TYPES:
        if req not in by_type:
            errors.append(f"missing required reference_type: {req}")

    for asset in assets:
        rel = asset.get("relative_path") or ""
        path = package_path / rel
        if not path.is_file():
            errors.append(f"missing file: {rel}")
            continue
        try:
            w, h = _image_size(path)
        except OSError as exc:
            errors.append(f"unreadable image {rel}: {exc}")
            continue
        if w < min_w or h < min_h:
            errors.append(f"dimensions too small for {rel}: {w}x{h} (min {min_w}x{min_h})")
        if asset.get("character_id") != character_id:
            errors.append(f"character mismatch on {asset.get('asset_id')}")
        if int(asset.get("version_int") or -1) != version:
            errors.append(f"asset version mismatch on {asset.get('asset_id')} (package V{version:03d})")
        if str(asset.get("costume_version") or "") != costume_version:
            errors.append(f"costume_version mismatch on {asset.get('asset_id')}")

    if not manifest_file.is_file():
        errors.append("missing character_reference_manifest.json")
    else:
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            for key in ("canonical_views", "expressions", "color_reference", "costume", "assets_index"):
                if key not in manifest:
                    errors.append(f"manifest missing key: {key}")
        except json.JSONDecodeError as exc:
            errors.append(f"manifest JSON invalid: {exc}")

    structure_ok = len(errors) == 0
    return {
        "ok": structure_ok,
        "structure_ok": structure_ok,
        "ready_for_3d": False,
        "errors": errors,
        "warnings": warnings,
        "package_id": package.get("package_id"),
        "character_id": character_id,
        "status": package.get("status"),
        "gate": "structure",
    }


def validate_package(package_path: Path) -> dict[str, Any]:
    """Full validation report.

    - structure_ok: files/naming/dimensions/schema (can pass without approval)
    - content_ok: mandatory views identity + same character/outfit/version
    - approval_ok: real production approval audit (not bootstrap)
    - ready_for_3d: all three gates pass (+ canonical registry consistency if flagged)
    """
    structure = validate_package_structure(package_path)
    pkg_file = package_path / "package.json"
    if not pkg_file.is_file():
        return {
            **structure,
            "content_ok": False,
            "approval_ok": False,
            "ready_for_3d": False,
        }

    package = json.loads(pkg_file.read_text(encoding="utf-8"))
    content = validate_content_control(package)
    approval_ok, approval_errors = is_production_approval_valid(package)

    content_errors = list(content["errors"])
    ready_errors: list[str] = list(structure["errors"]) + content_errors + list(approval_errors)

    if package.get("canonical"):
        reg = load_canonical_registry()
        entry = (reg.get("characters") or {}).get(str(package.get("character_id") or "")) or {}
        if entry.get("canonical_package_id") != package.get("package_id"):
            ready_errors.append(
                f"canonical=true but registry points to {entry.get('canonical_package_id')!r} "
                f"not {package.get('package_id')!r}"
            )

    if package.get("approved") and not approval_ok:
        if not any("bootstrap_approval" in e or "missing_production_approval" in e for e in ready_errors):
            ready_errors.append(
                "package.approved without valid production approval_audit — not READY_FOR_3D"
            )

    structure_ok = bool(structure["structure_ok"])
    content_ok = bool(content["ok"])
    registry_ok = True
    if package.get("canonical"):
        reg = load_canonical_registry()
        entry = (reg.get("characters") or {}).get(str(package.get("character_id") or "")) or {}
        registry_ok = entry.get("canonical_package_id") == package.get("package_id")

    ready = structure_ok and content_ok and approval_ok and registry_ok

    return {
        "ok": structure_ok,  # structure validity only — does not imply READY_FOR_3D
        "structure_ok": structure_ok,
        "content_ok": content_ok,
        "approval_ok": approval_ok,
        "ready_for_3d": ready,
        "errors": ready_errors,
        "structure_errors": list(structure["errors"]),
        "content_errors": content_errors,
        "approval_errors": list(approval_errors),
        "warnings": list(structure.get("warnings") or []) + list(content.get("warnings") or []),
        "package_id": package.get("package_id"),
        "character_id": package.get("character_id"),
        "status": package.get("status"),
    }


def write_package_files(package: dict[str, Any], root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    (root / "character_reference_manifest.json").write_text(
        json.dumps(build_manifest(package), indent=2) + "\n", encoding="utf-8"
    )


def grant_production_approval(
    package_path: Path,
    *,
    approved_by: str,
    note: str = "",
    require_content_pass: bool = True,
) -> dict[str, Any]:
    """Explicit production approval with audit trail. Builder must not call this implicitly."""
    approved_by = (approved_by or "").strip()
    if not approved_by:
        raise ValueError("approved_by required")
    if approved_by.lower() in NON_PRODUCTION_APPROVERS:
        raise ValueError(f"Non-production approver cannot grant approval: {approved_by}")

    report = validate_package(package_path)
    if not report["structure_ok"]:
        raise ValueError(f"Cannot approve — structure invalid: {report['structure_errors']}")
    if require_content_pass and not report["content_ok"]:
        raise ValueError(f"Cannot approve — content control failed: {report['content_errors']}")

    package = json.loads((package_path / "package.json").read_text(encoding="utf-8"))
    now = datetime.now(UTC).isoformat()
    audit = dict(package.get("approval_audit") or empty_approval_audit(actor=approved_by))
    audit["production_approved"] = True
    audit["production_approved_by"] = approved_by
    audit["production_approved_at"] = now
    events = list(audit.get("events") or [])
    events.append(
        {
            "action": "production_approval_granted",
            "at": now,
            "by": approved_by,
            "grants_production_approval": True,
            "grants_canonical": False,
            "note": note or "Manual production approval after content review.",
        }
    )
    audit["events"] = events
    package["approval_audit"] = audit
    package["approved"] = True
    package["approval_metadata"] = {
        "quality_status": "APPROVED",
        "approved_by": approved_by,
        "approved_at": now,
        "source": "production_approval_audit",
        "note": note,
    }
    package["status"] = "APPROVED"
    write_package_files(package, package_path)
    return validate_package(package_path)


def set_canonical(character_id: str, package_version: int, *, notes: str = "") -> dict[str, Any]:
    """Point Blender consumers at exactly one package version for a character.

    Requires READY_FOR_3D gates (structure + content + production approval).
    """
    character_id = character_id.lower()
    if character_id not in CHARACTERS:
        raise ValueError(f"Unknown Wonder Crew character: {character_id}")
    pid = package_id(character_id, package_version)
    pkg_path = package_dir(character_id, package_version)
    result = validate_package(pkg_path)
    if not result["ready_for_3d"]:
        raise ValueError(
            "Cannot set canonical — not READY_FOR_3D: "
            f"structure_ok={result['structure_ok']} content_ok={result['content_ok']} "
            f"approval_ok={result['approval_ok']} errors={result['errors']}"
        )

    package = json.loads((pkg_path / "package.json").read_text(encoding="utf-8"))
    char_root = packages_root() / character_id
    if char_root.is_dir():
        for other in char_root.iterdir():
            other_pkg = other / "package.json"
            if not other_pkg.is_file():
                continue
            data = json.loads(other_pkg.read_text(encoding="utf-8"))
            if data.get("package_id") == pid:
                continue
            if data.get("canonical"):
                data["canonical"] = False
                if data.get("status") == "READY_FOR_3D":
                    data["status"] = "SUPERSEDED"
                data["notes"] = (data.get("notes") or "") + f" | superseded by {pid}"
                write_package_files(data, other)

    now = datetime.now(UTC).isoformat()
    audit = dict(package.get("approval_audit") or {})
    audit["canonical_granted"] = True
    events = list(audit.get("events") or [])
    events.append(
        {
            "action": "canonical_granted",
            "at": now,
            "by": "set_canonical",
            "grants_production_approval": False,
            "grants_canonical": True,
            "note": notes or "Canonical pointer set after READY_FOR_3D validation.",
        }
    )
    audit["events"] = events
    package["approval_audit"] = audit
    package["canonical"] = True
    package["status"] = "READY_FOR_3D"
    package["notes"] = notes or package.get("notes") or ""
    write_package_files(package, pkg_path)

    reg = load_canonical_registry()
    reg.setdefault("characters", {})[character_id] = {
        "character_id": character_id,
        "display_name": character_id.capitalize(),
        "canonical_package_id": pid,
        "canonical_package_version": format_version(package_version),
        "canonical": True,
        "package_path": str(pkg_path.resolve()),
        "manifest_path": str((pkg_path / "character_reference_manifest.json").resolve()),
        "notes": notes,
    }
    save_canonical_registry(reg)
    return reg["characters"][character_id]


def clear_canonical(character_id: str, *, reason: str) -> dict[str, Any]:
    character_id = character_id.lower()
    reg = load_canonical_registry()
    entry = (reg.get("characters") or {}).get(character_id) or {
        "character_id": character_id,
        "display_name": character_id.capitalize(),
    }
    entry["canonical_package_id"] = None
    entry["canonical_package_version"] = None
    entry["canonical"] = False
    entry["package_path"] = None
    entry["manifest_path"] = None
    entry["notes"] = reason
    reg.setdefault("characters", {})[character_id] = entry
    save_canonical_registry(reg)
    return entry


def resolve_canonical_package(character_id: str) -> dict[str, Any]:
    """Independent consumer helper: resolve current canonical package + manifest paths."""
    character_id = character_id.lower()
    reg = load_canonical_registry()
    entry = (reg.get("characters") or {}).get(character_id)
    if not entry or not entry.get("canonical") or not entry.get("canonical_package_id"):
        raise FileNotFoundError(f"No canonical package for {character_id}")
    manifest_path = Path(str(entry["manifest_path"]))
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Canonical manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("ready_for_3d"):
        raise FileNotFoundError(
            f"Canonical package {entry.get('canonical_package_id')} is not ready_for_3d"
        )
    return {"registry_entry": entry, "manifest": manifest, "manifest_path": str(manifest_path)}


def list_packages() -> list[dict[str, Any]]:
    """List package.json summaries under the handoff packages root."""
    root = packages_root()
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("package.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        report = validate_package(path.parent)
        out.append(
            {
                "package_id": data.get("package_id"),
                "character_id": data.get("character_id"),
                "status": data.get("status"),
                "canonical": data.get("canonical", False),
                "approved": data.get("approved", False),
                "structure_ok": report.get("structure_ok"),
                "content_ok": report.get("content_ok"),
                "approval_ok": report.get("approval_ok"),
                "ready_for_3d": report.get("ready_for_3d"),
                "package_path": str(path.parent.resolve()),
            }
        )
    return out


def mark_existing_niko_v001_limitations(*, evidence_label: str = "20260919_false_readiness_correction") -> dict[str, Any]:
    """Keep WC_NIKO_V001 history; strip false READY/canonical; record limitations + evidence copy."""
    root = package_dir("niko", 1)
    pkg_file = root / "package.json"
    if not pkg_file.is_file():
        raise FileNotFoundError(root)

    evidence_dir = evidence_root() / evidence_label
    evidence_dir.mkdir(parents=True, exist_ok=True)
    snapshot = evidence_dir / "WC_NIKO_V001_pre_correction_snapshot"
    if not snapshot.exists():
        shutil.copytree(root, snapshot, dirs_exist_ok=False)

    package = json.loads(pkg_file.read_text(encoding="utf-8"))
    honest = niko_v001_honest_content_controls(str(package.get("costume_version") or "COSTUME_V001"))
    now = datetime.now(UTC).isoformat()

    limitations = [
        {
            "code": "false_ready_for_3d_revoked",
            "at": now,
            "detail": (
                "Package previously marked READY_FOR_3D/canonical via bootstrap auto-approval. "
                "Filename/resolution/presence were treated as sufficient; content was not."
            ),
        },
        {
            "code": "face_sheet_fragment_not_back_view",
            "asset_id": "WC_NIKO_BACK_V001",
            "at": now,
            "detail": "WC_NIKO_BACK_V001.png is a face-sheet fragment, not a back view.",
        },
        {
            "code": "bio_sheet_fragment_not_side_view",
            "asset_id": "WC_NIKO_SIDE_V001",
            "at": now,
            "detail": "SIDE asset is a bio/face sheet fragment, not a dedicated profile.",
        },
        {
            "code": "sheet_panel_crop_not_dedicated_view",
            "asset_ids": ["WC_NIKO_THREE_QUARTER_V001", "WC_NIKO_COLOR_V001"],
            "at": now,
            "detail": "THREE_QUARTER/COLOR derived from sheet panels — not dedicated Comfy turnaround renders.",
        },
        {
            "code": "dual_panel_crop_not_clean_single_front",
            "asset_id": "WC_NIKO_FRONT_V001",
            "at": now,
            "detail": "FRONT is a dual-panel crop, not a clean single orthographic front.",
        },
        {
            "code": "missing_materials_require_atnos_generation",
            "at": now,
            "detail": (
                "Missing correct FRONT/SIDE/BACK/THREE_QUARTER must be generated via Atnos "
                "Comfy/generation paths — do not invent by renaming crops."
            ),
        },
    ]

    for asset in package.get("assets") or []:
        ref = str(asset.get("reference_type") or "")
        if ref in honest:
            asset["content_control"] = honest[ref]
        asset["approved"] = False
        # Preserve historical bootstrap metadata but mark it non-production.
        meta = dict(asset.get("approval_metadata") or {})
        meta["quality_status"] = "NOT_PRODUCTION_APPROVED"
        meta["superseded_note"] = (
            "Historical bootstrap approval retained for audit; does not grant READY_FOR_3D."
        )
        asset["approval_metadata"] = meta

    audit = empty_approval_audit(
        actor="false_readiness_correction",
        note="Revoked false READY_FOR_3D / canonical; content limitations recorded.",
    )
    audit["events"].append(
        {
            "action": "false_ready_for_3d_revoked",
            "at": now,
            "by": "false_readiness_correction",
            "grants_production_approval": False,
            "grants_canonical": False,
            "note": "Bootstrap auto-approval and canonical pointer removed.",
            "evidence_dir": str(evidence_dir.resolve()),
        }
    )

    package["approved"] = False
    package["canonical"] = False
    package["status"] = "NOT_READY"
    package["structure_valid"] = True
    package["content_control_ok"] = False
    package["limitations"] = limitations
    package["approval_audit"] = audit
    package["approval_metadata"] = {
        "quality_status": "NOT_PRODUCTION_APPROVED",
        "approved_by": "handoff_contract_bootstrap",
        "approved_at": (package.get("approval_metadata") or {}).get("approved_at"),
        "source": "historical_bootstrap_retained",
        "note": "Historical bootstrap metadata retained; production_approved=false.",
    }
    package["notes"] = (
        "WC_NIKO_V001 retained as historical structure fixture. "
        "NOT READY_FOR_3D: BACK is face-sheet fragment; SIDE/THREE_QUARTER provisional sheet crops; "
        "bootstrap auto-approval revoked. Evidence: "
        f"{evidence_dir.resolve()}"
    )

    write_package_files(package, root)
    (root / "LIMITATIONS.md").write_text(
        "\n".join(
            [
                "# WC_NIKO_V001 — limitations (do not treat as READY_FOR_3D)",
                "",
                f"Recorded: {now}",
                "",
                "## Root cause",
                "- Test builder auto-set `approved=true` and `set_canonical` → false `READY_FOR_3D`.",
                "- Validator treated file presence + flags as sufficient; no content-view control.",
                "",
                "## Confirmed content failures",
                "- `WC_NIKO_BACK_V001.png` — face-sheet fragment, not back view.",
                "- `WC_NIKO_SIDE_V001.png` — bio/face sheet fragment, not dedicated side.",
                "- `WC_NIKO_THREE_QUARTER_V001.png` — sheet panel crop, not verified 3/4 lock.",
                "- `WC_NIKO_FRONT_V001.png` — dual-panel crop (LIMITED), not clean single front.",
                "",
                "## Policy",
                "- Structure validity may still pass.",
                "- Production approval / canonical / READY_FOR_3D require content PASS + real audit.",
                "- Missing views must be generated via Atnos Comfy paths — no rename hacks.",
                "",
                f"Evidence snapshot: `{snapshot.resolve()}`",
                "",
            ]
        ),
        encoding="utf-8",
    )

    clear_canonical(
        "niko",
        reason=(
            "Cleared false canonical WC_NIKO_V001 — content control failed "
            "(BACK face-sheet fragment; bootstrap approval revoked)."
        ),
    )

    report = validate_package(root)
    summary = {
        "package_path": str(root.resolve()),
        "evidence_dir": str(evidence_dir.resolve()),
        "snapshot_dir": str(snapshot.resolve()),
        "status": package["status"],
        "canonical": False,
        "validation": report,
        "limitations_count": len(limitations),
    }
    (evidence_dir / "correction_report.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def build_niko_test_package(
    *,
    version: int = 1,
    set_as_canonical: bool = False,
    packages_root_override: Path | None = None,
    overwrite: bool = False,
    grant_production_approval: bool = False,
) -> dict[str, Any]:
    """Assemble WC_NIKO structure fixture from existing Comfy sheets/crops.

    Does NOT grant production approval or canonical by default.
    Does NOT model in Blender. Honest content_control marks provisional crops as FAIL/LIMITED.
    """
    if grant_production_approval:
        raise ValueError(
            "Test builder must not auto-grant production approval. "
            "Use grant_production_approval() after real content review."
        )
    if set_as_canonical:
        raise ValueError(
            "Test builder must not auto-set canonical. "
            "Canonical requires READY_FOR_3D (content PASS + production approval)."
        )

    refs = Path(r"E:\Atnos\App\config\channel_references\20260916")
    sheet = refs / "image-3.png"
    portrait = refs / "crops" / "niko_portrait_v1.png"
    front_crop = refs / "crops" / "niko_front_v1.png"
    if not sheet.is_file():
        raise FileNotFoundError(sheet)
    if not portrait.is_file():
        raise FileNotFoundError(portrait)

    character_id = "niko"
    pid = package_id(character_id, version)
    root = package_dir(character_id, version, root=packages_root_override)
    assets_dir = root / "assets"

    # Never silently destroy historical production packages on the real root.
    if root.exists():
        if not overwrite:
            raise FileExistsError(
                f"Package already exists: {root}. Pass overwrite=True for disposable fixtures only."
            )
        if packages_root_override is None and (root / "LIMITATIONS.md").is_file():
            raise RuntimeError(
                f"Refusing to overwrite limited historical package {root}. "
                "Use packages_root_override for tests."
            )
        shutil.rmtree(root)
    assets_dir.mkdir(parents=True, exist_ok=True)

    costume_version = "COSTUME_V001"
    workflow = "wonder_character_reference_v1"
    model = "checkpoints/sd_xl_turbo_1.0_fp16.safetensors"
    anchor = "image-3.png / niko_portrait_v1.png"
    honest = niko_v001_honest_content_controls(costume_version)

    # Non-production metadata only — never unlocks READY_FOR_3D.
    bootstrap_meta = {
        "quality_status": "STRUCTURE_FIXTURE_ONLY",
        "approved_by": "handoff_contract_bootstrap",
        "approved_at": datetime.now(UTC).isoformat(),
        "source": "wonder_crew_comfy_bible_sheet_image-3",
        "note": "Structure fixture only — bootstrap must not grant production approval.",
    }

    specs: list[dict[str, Any]] = [
        {
            "reference_type": "FRONT",
            "source": front_crop if front_crop.is_file() else portrait,
            "mode": "copy",
            "notes": "Dedicated front crop from approved sheet (LIMITED — dual panel).",
        },
        {
            "reference_type": "THREE_QUARTER",
            "source": sheet,
            "mode": "crop",
            "box": (0.40, 0.06, 0.56, 0.40),
            "notes": "3/4 panel crop from expression sheet (FAIL — not dedicated view).",
        },
        {
            "reference_type": "SIDE",
            "source": sheet,
            "mode": "crop",
            "box": (0.08, 0.06, 0.22, 0.40),
            "notes": "Side/profile panel crop from expression sheet (FAIL — bio fragment).",
        },
        {
            "reference_type": "BACK",
            "source": sheet,
            "mode": "crop",
            "box": (0.08, 0.42, 0.24, 0.78),
            "notes": "FAIL: face-sheet fragment labeled BACK — not a back view.",
        },
        {
            "reference_type": "EXPRESSION",
            "source": sheet,
            "mode": "copy",
            "notes": "Full expression sheet (image-3).",
        },
        {
            "reference_type": "COLOR",
            "source": sheet,
            "mode": "crop",
            "box": (0.72, 0.72, 0.98, 0.98),
            "notes": "Palette / color swatch region from sheet (LIMITED).",
        },
        {
            "reference_type": "OUTFIT",
            "source": front_crop if front_crop.is_file() else portrait,
            "mode": "copy",
            "notes": "Outfit lock from front reference (LIMITED).",
        },
        {
            "reference_type": "PROP_DETAIL",
            "source": portrait,
            "mode": "crop",
            "box": (0.15, 0.55, 0.85, 0.95),
            "notes": "Signature detail / lower costume prop zone from portrait.",
        },
    ]

    assets: list[dict[str, Any]] = []
    for spec in specs:
        ref = spec["reference_type"]
        assets.append(
            build_reference_asset(
                character_id=character_id,
                reference_type=ref,
                version=version,
                source_path=Path(spec["source"]),
                dest_dir=assets_dir,
                workflow=workflow,
                model=model,
                anchor=anchor,
                approved=False,
                approval_metadata=bootstrap_meta,
                notes=spec.get("notes") or "",
                costume_version=costume_version,
                copy_mode=spec["mode"],
                crop_box=spec.get("box"),
                content_control=honest.get(ref),
            )
        )

    package = {
        "schema": "wonder_crew_reference_package_v1",
        "package_id": pid,
        "character_id": character_id,
        "version": format_version(version),
        "version_int": version,
        "canonical": False,
        "status": "COMPLETE",
        "approved": False,
        "workflow": workflow,
        "model": model,
        "anchor": anchor,
        "costume_version": costume_version,
        "dimensions_policy": load_contract().get("dimensions_policy"),
        "color_palette": extract_palette(portrait),
        "approval_metadata": bootstrap_meta,
        "approval_audit": empty_approval_audit(
            actor="build_niko_test_package",
            note="Structure fixture built; production_approved=false; canonical=false.",
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "supersedes": None,
        "notes": (
            "Structure/handoff contract fixture. "
            "SIDE/BACK/THREE_QUARTER are sheet crops with FAIL/LIMITED content_control — "
            "not READY_FOR_3D. Replace via Atnos Comfy generation."
        ),
        "limitations": [
            {"code": "face_sheet_fragment_not_back_view", "asset_id": f"WC_NIKO_BACK_V{format_version(version)}"},
            {"code": "bio_sheet_fragment_not_side_view", "asset_id": f"WC_NIKO_SIDE_V{format_version(version)}"},
            {"code": "sheet_panel_crop_not_dedicated_view"},
        ],
        "assets": assets,
    }

    write_package_files(package, root)
    validation = validate_package(root)
    if not validation["structure_ok"]:
        raise RuntimeError(f"Niko package failed structure validation: {validation['structure_errors']}")

    return {
        "package_path": str(root.resolve()),
        "package_id": pid,
        "validation": validation,
        "manifest_path": str((root / "character_reference_manifest.json").resolve()),
        "status": package.get("status"),
        "canonical": False,
        "approved": False,
        "ready_for_3d": False,
    }


__all__ = [
    "ALL_REFERENCE_TYPES",
    "CHANNEL",
    "CHARACTERS",
    "MANDATORY_TURNAROUND_VIEWS",
    "OPTIONAL_REFERENCE_TYPES",
    "REQUIRED_REFERENCE_TYPES",
    "assess_asset_content_control",
    "asset_id",
    "build_manifest",
    "build_niko_test_package",
    "build_reference_asset",
    "canonical_registry_path",
    "clear_canonical",
    "contract_path",
    "empty_approval_audit",
    "evidence_root",
    "format_version",
    "grant_production_approval",
    "list_packages",
    "load_canonical_registry",
    "load_contract",
    "mark_existing_niko_v001_limitations",
    "package_dir",
    "package_id",
    "packages_root",
    "parse_asset_id",
    "parse_package_id",
    "resolve_canonical_package",
    "set_canonical",
    "validate_content_control",
    "validate_package",
    "validate_package_structure",
    "write_package_files",
]
