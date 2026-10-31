"""Formal character asset quality approval workflow.

generated != approved. Production selectors use APPROVED only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from atnos.production.asset_brain.catalog import save_catalog
from atnos.production.asset_brain.models import (
    CHARACTER_IDS_REQUIRING_APPROVAL,
    REJECT_REASONS,
    AssetCatalog,
    AssetRecord,
    QualityStatus,
    RejectReason,
)
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain


class AssetApprovalError(ValueError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_pre_gate(
    asset: AssetRecord,
    *,
    resolve_file,
    catalog: AssetCatalog,
    require_alpha: bool | None = None,
    expected_channel: str | None = None,
    expected_character: str | None = None,
) -> dict[str, Any]:
    """Automatic checks before an asset may enter AWAITING_REVIEW."""
    checks: list[dict[str, Any]] = []
    path = resolve_file(asset)

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("file_integrity", path.is_file() and path.stat().st_size >= 2048, str(path))
    blank = False
    clipping = False
    corrupt = False
    dims_ok = False
    alpha_ok = True
    try:
        with Image.open(path) as img:
            img.load()
            w, h = img.size
            dims_ok = w >= 256 and h >= 256
            if asset.width and asset.height:
                dims_ok = dims_ok and w == asset.width and h == asset.height
            add("dimensions", dims_ok, f"{w}x{h}")
            need_alpha = require_alpha if require_alpha is not None else bool(asset.transparent_background)
            if need_alpha:
                alpha_ok = img.mode in {"RGBA", "LA"} or ("transparency" in img.info)
                add("alpha", alpha_ok, f"mode={img.mode}")
            else:
                add("alpha", True, "not required")
            # blank / clipping heuristics
            sample = img.convert("RGB").resize((64, 64))
            pixels = list(sample.getdata())
            uniq = len(set(pixels))
            arr = np.asarray(sample, dtype=np.float32)
            variance = float(arr.var())
            blank = variance < 2.0 and uniq <= 4
            extremes = sum(1 for r, g, b in pixels if (r + g + b) <= 6 or (r + g + b) >= 765)
            clipping = extremes / max(len(pixels), 1) > 0.92
            add("blank_image", not blank, f"unique_colors={uniq} variance={variance:.2f}")
            add("extreme_clipping", not clipping, f"extreme_ratio={extremes/max(len(pixels),1):.3f}")
    except OSError as exc:
        corrupt = True
        add("dimensions", False, str(exc))
        add("alpha", False, str(exc))
        add("blank_image", False, str(exc))
        add("extreme_clipping", False, str(exc))
    add("obvious_corruption", not corrupt, "ok" if not corrupt else "cannot decode")

    alpha_qc_payload: dict[str, Any] | None = None
    need_alpha_final = require_alpha if require_alpha is not None else bool(asset.transparent_background)
    if need_alpha_final and path.is_file() and not corrupt:
        try:
            from atnos.production.cutout_quality import run_alpha_qc

            qc = run_alpha_qc(path, require_alpha=True)
            alpha_qc_payload = qc.as_dict()
            add("alpha_qc", qc.passed, f"verdict={qc.verdict}")
        except Exception as exc:  # noqa: BLE001
            add("alpha_qc", False, str(exc)[:200])
    else:
        add("alpha_qc", True, "not required")

    # duplicates by fingerprint or file hash
    dup = False
    if path.is_file():
        digest = _sha256(path)
        for other in catalog.assets:
            if other.id == asset.id:
                continue
            if other.request_fingerprint and asset.request_fingerprint and other.request_fingerprint == asset.request_fingerprint:
                dup = True
                break
            try:
                other_path = resolve_file(other)
                if other_path.is_file() and _sha256(other_path) == digest:
                    dup = True
                    break
            except Exception:
                continue
    add("duplicate_image", not dup, "unique" if not dup else "duplicate of another catalog asset")

    provenance_ok = bool(asset.generation and asset.generation.prompt and asset.generation.workflow_id and asset.generation.model)
    add("provenance", provenance_ok, "generation block present" if provenance_ok else "missing provenance")

    channel = expected_channel or asset.channel
    add("channel", bool(asset.channel) and asset.channel == channel, f"{asset.channel}")

    char_ok = True
    if asset.type == "character":
        expected = (expected_character or asset.character or "").strip().casefold()
        char_ok = bool(asset.character) and asset.character.strip().casefold() == expected
        if expected in CHARACTER_IDS_REQUIRING_APPROVAL or (asset.character or "").casefold() in CHARACTER_IDS_REQUIRING_APPROVAL:
            char_ok = char_ok and bool(asset.character)
    add("character_id", char_ok, asset.character or "missing")

    anchor_ok = True
    if asset.type == "character" and (asset.character or "").casefold() in CHARACTER_IDS_REQUIRING_APPROVAL:
        refs = list((asset.generation.reference_files if asset.generation else []) or [])
        anchor_ok = bool(refs)
        add("anchor_metadata", anchor_ok, f"{len(refs)} refs" if refs else "missing reference_files")
    else:
        add("anchor_metadata", True, "not required")

    failed = [c for c in checks if not c["ok"]]
    passed = not failed
    alpha_qc_failed = any(c["name"] == "alpha_qc" and not c["ok"] for c in checks)
    alpha_verdict = (alpha_qc_payload or {}).get("verdict") if alpha_qc_failed else None
    if not passed and alpha_qc_failed and alpha_verdict == "REJECTED":
        gate_verdict = "REJECTED"
    elif not passed and alpha_qc_failed:
        gate_verdict = "NEEDS_REPROCESS"
    elif passed:
        gate_verdict = "TECHNICALLY_VALID"
    else:
        gate_verdict = "PRE_GATE_FAIL"
    return {
        "passed": passed,
        "verdict": gate_verdict,
        "checks": checks,
        "failed": [c["name"] for c in failed],
        "alpha_qc": alpha_qc_payload,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def build_review_contact_sheet(
    assets: list[AssetRecord],
    *,
    resolve_file,
    dest: Path,
    cols: int = 4,
    cell: int = 280,
) -> Path:
    """Separate review artifact — does not modify production PNGs."""
    tiles: list[Image.Image] = []
    labels: list[str] = []
    for asset in assets:
        path = resolve_file(asset)
        with Image.open(path) as img:
            foreground = img.convert("RGBA").resize((cell, cell), Image.Resampling.LANCZOS)
        tile = Image.new('RGB', (cell, cell), (20, 38, 51))
        checker = ImageDraw.Draw(tile)
        for y in range(0, cell, 20):
            for x in range(0, cell, 20):
                if (x // 20 + y // 20) % 2:
                    checker.rectangle((x, y, x + 19, y + 19), fill=(58, 78, 89))
        tile.paste(foreground, (0, 0), foreground.getchannel('A'))
        # Label strip under tile (not burned into source PNG)
        canvas = Image.new("RGB", (cell, cell + 56), (18, 18, 18))
        canvas.paste(tile, (0, 0))
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.load_default()
        except OSError:
            font = None
        text = (
            f"{asset.id}\n"
            f"e={asset.emotion or '-'} p={asset.pose or '-'} d={asset.direction or '-'}\n"
            f"seed={(asset.generation.seed if asset.generation else None)}"
        )
        draw.multiline_text((6, cell + 4), text, fill=(230, 230, 230), font=font, spacing=2)
        tiles.append(canvas)
        labels.append(asset.id)

    if not tiles:
        raise AssetApprovalError("No assets for contact sheet")
    rows = (len(tiles) + cols - 1) // cols
    tw, th = tiles[0].size
    sheet = Image.new("RGB", (cols * tw, rows * th), (12, 12, 12))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % cols) * tw, (i // cols) * th))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, quality=92)
    meta = {
        "asset_ids": [a.id for a in assets],
        "labels": labels,
        "created_at": datetime.now(UTC).isoformat(),
        "note": "Review artifact only — production PNGs unlabeled",
        "alpha_preview": "composited over navy checkerboard; source PNG not modified",
    }
    dest.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return dest


def character_requires_contact_sheet_review(asset: AssetRecord) -> bool:
    """Named lead characters need manual contact-sheet QC before APPROVED (img2img ≠ IPAdapter)."""
    if asset.type != "character":
        return False
    name = (asset.character or "").strip().casefold()
    return name in CHARACTER_IDS_REQUIRING_APPROVAL


class AssetApprovalService:
    def __init__(self, brain: WealthBlueprintAssetBrain) -> None:
        self.brain = brain

    def _replace(self, record: AssetRecord) -> AssetRecord:
        assets = [record if a.id == record.id else a for a in self.brain.catalog.assets]
        if not any(a.id == record.id for a in self.brain.catalog.assets):
            assets = [*self.brain.catalog.assets, record]
        catalog = AssetCatalog(
            schema_version=self.brain.catalog.schema_version,
            channel=self.brain.catalog.channel,
            library_id=self.brain.catalog.library_id,
            assets=assets,
        )
        self.brain.catalog = catalog
        save_catalog(catalog, self.brain.catalog_file)
        return record

    def ingest_generated(self, asset: AssetRecord, *, run_pregate: bool = True) -> AssetRecord:
        """Register/update as GENERATED then optionally promote to AWAITING_REVIEW."""
        record = asset.model_copy(
            update={
                "quality_status": "GENERATED",
                "approved": False,
                "reject_reason": None,
            }
        )
        if not run_pregate:
            return self._replace(record)
        gate = run_pre_gate(record, resolve_file=self.brain.resolve_file, catalog=self.brain.catalog)
        if gate["passed"]:
            record = record.model_copy(
                update={
                    "quality_status": "AWAITING_REVIEW",
                    "pre_gate": gate,
                    "approved": False,
                }
            )
        else:
            status = "GENERATED"
            if gate.get("verdict") == "NEEDS_REPROCESS":
                status = "NEEDS_REPROCESS"
            elif gate.get("verdict") == "REJECTED":
                status = "REJECTED"
            record = record.model_copy(
                update={
                    "quality_status": status,  # type: ignore[arg-type]
                    "pre_gate": gate,
                    "approved": False,
                    "reject_reason": "ALPHA_QC_FAIL" if status == "REJECTED" else None,
                }
            )
        return self._replace(record)

    def approve(self, asset_id: str, *, reviewed_by: str = "operator") -> AssetRecord:
        asset = self.brain.get(asset_id)
        if asset is None:
            raise AssetApprovalError(f"Unknown asset: {asset_id}")
        if asset.quality_status not in {"TECHNICALLY_VALID", "AWAITING_REVIEW", "GENERATED"}:
            # Allow re-approve only from review queue; GENERATED only if pre-gate already passed
            if asset.quality_status == "APPROVED":
                return asset
            raise AssetApprovalError(f"Cannot approve asset in status {asset.quality_status}")
        gate = asset.pre_gate if asset.pre_gate.get("passed") else run_pre_gate(
            asset, resolve_file=self.brain.resolve_file, catalog=self.brain.catalog
        )
        if not gate.get("passed"):
            # Transparent assets: alpha QC failure blocks APPROVED permanently until reprocess
            if "alpha_qc" in (gate.get("failed") or []):
                raise AssetApprovalError(
                    f"Alpha QC failed ({gate.get('verdict')}) — asset cannot be APPROVED; "
                    f"use NEEDS_REPROCESS or REJECTED. failed={gate.get('failed')}"
                )
            raise AssetApprovalError(f"Pre-gate failed: {gate.get('failed')}")
        if asset.quality_status == "NEEDS_REPROCESS":
            raise AssetApprovalError("Cannot approve NEEDS_REPROCESS — re-run cutout first")
        if character_requires_contact_sheet_review(asset):
            sheet = (asset.contact_sheet_path or "").strip()
            if not sheet or not Path(sheet).is_file():
                raise AssetApprovalError(
                    "Named character assets require manual contact-sheet QC before APPROVED "
                    "(img2img + anchors — not IPAdapter). Build a review sheet via "
                    "POST /api/production/asset-brain/contact-sheet, review face/outfit, then approve."
                )
        # Only replace an older version of the same named-character pose.
        # Unnamed scene objects coexist; approving a wallet must not retire
        # the already approved envelope required by another scene.
        superseded: list[AssetRecord] = []
        for other in self.brain.catalog.assets:
            if asset.type != "character" or not asset.character:
                break
            if other.id == asset.id:
                continue
            if other.quality_status != "APPROVED":
                continue
            if other.type != asset.type:
                continue
            if (other.character or "").casefold() != (asset.character or "").casefold():
                continue
            if other.channel != asset.channel or other.pose != asset.pose:
                continue
            scope_prefixes = ("wb-style-", "wb-order-", "wb-scene-")
            if {t for t in other.tags if t.startswith(scope_prefixes)} != {
                t for t in asset.tags if t.startswith(scope_prefixes)
            }:
                continue
            if (other.emotion or "").casefold() == (asset.emotion or "").casefold() and (
                other.direction or ""
            ).casefold() == (asset.direction or "").casefold():
                superseded.append(
                    other.model_copy(update={"quality_status": "SUPERSEDED", "approved": False})
                )
        updated = asset.model_copy(
            update={
                "quality_status": "APPROVED",
                "approved": True,
                "pre_gate": gate,
                "reject_reason": None,
                "reject_notes": "",
                "reviewed_at": datetime.now(UTC).isoformat(),
                "reviewed_by": reviewed_by,
            }
        )
        by_id = {a.id: a for a in self.brain.catalog.assets}
        by_id[updated.id] = updated
        for row in superseded:
            by_id[row.id] = row
        catalog = AssetCatalog(
            schema_version=self.brain.catalog.schema_version,
            channel=self.brain.catalog.channel,
            library_id=self.brain.catalog.library_id,
            assets=list(by_id.values()),
        )
        self.brain.catalog = catalog
        save_catalog(catalog, self.brain.catalog_file)
        return updated

    def reject(
        self,
        asset_id: str,
        reason: RejectReason,
        *,
        notes: str = "",
        reviewed_by: str = "operator",
    ) -> AssetRecord:
        if reason not in REJECT_REASONS:
            raise AssetApprovalError(f"Invalid reject reason: {reason}")
        asset = self.brain.get(asset_id)
        if asset is None:
            raise AssetApprovalError(f"Unknown asset: {asset_id}")
        if asset.quality_status == "APPROVED":
            raise AssetApprovalError("Refuse to reject an APPROVED asset without supersede flow")
        updated = asset.model_copy(
            update={
                "quality_status": "REJECTED",
                "approved": False,
                "reject_reason": reason,
                "reject_notes": (notes or "")[:500],
                "reviewed_at": datetime.now(UTC).isoformat(),
                "reviewed_by": reviewed_by,
            }
        )
        return self._replace(updated)

    def list_awaiting_review(self) -> list[AssetRecord]:
        return [
            a
            for a in self.brain.catalog.assets
            if a.quality_status in {"AWAITING_REVIEW", "TECHNICALLY_VALID"}
        ]

    def metrics(self) -> dict[str, Any]:
        assets = self.brain.catalog.assets
        statuses = Counter(a.quality_status for a in assets)
        generated = sum(1 for a in assets if a.generation is not None or a.source.startswith("comfy"))
        approved = statuses.get("APPROVED", 0)
        rejected = statuses.get("REJECTED", 0)
        decided = approved + rejected
        reasons = Counter(a.reject_reason for a in assets if a.reject_reason)
        workflows = Counter(
            (a.generation.workflow_id if a.generation else a.source) for a in assets if a.generation or a.source
        )
        models = Counter((a.generation.model if a.generation else "") for a in assets if a.generation)
        times: list[float] = []
        # generation time not always stored — skip if absent
        return {
            "generated_count": generated,
            "approved_count": approved,
            "rejected_count": rejected,
            "awaiting_review_count": statuses.get("AWAITING_REVIEW", 0) + statuses.get("TECHNICALLY_VALID", 0),
            "approval_rate": round(approved / decided, 4) if decided else None,
            "reject_reasons": dict(reasons),
            "by_status": dict(statuses),
            "workflows": dict(workflows),
            "models": {k: v for k, v in models.items() if k},
            "average_generation_time_s": round(sum(times) / len(times), 3) if times else None,
        }
