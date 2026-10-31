"""Reject the semantically failed WB coin in Atnos Asset Brain."""

from __future__ import annotations

import json
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.asset_brain.approval import AssetApprovalService
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain


REPORT = Path("E:/Atnos/App/reports/llm-system-20260927/wb-coin-layer-20260929.json")


def main() -> int:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    if payload.get("literal_contract_audit", {}).get("status") != "fail":
        raise RuntimeError("Refusing to reject without a failed literal image audit")
    asset_id = str(payload["metadata"]["generation_settings"]["asset_brain_id"])
    brain = WealthBlueprintAssetBrain(get_settings())
    asset = brain.get(asset_id)
    if asset is None:
        raise RuntimeError(f"Atnos asset is missing: {asset_id}")
    if asset.quality_status != "REJECTED":
        asset = AssetApprovalService(brain).reject(
            asset_id,
            "OTHER",
            notes="WB pilot A: generated coin carries a large bird and decorative rings; literal visual contract failed.",
            reviewed_by="Codex visual review",
        )
    payload["asset_brain_review"] = {
        "asset_id": asset_id,
        "quality_status": asset.quality_status,
        "reject_reason": asset.reject_reason,
        "reject_notes": asset.reject_notes,
        "reviewed_by": asset.reviewed_by,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["asset_brain_review"], ensure_ascii=False))
    return 0 if asset.quality_status == "REJECTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
