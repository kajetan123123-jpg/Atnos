"""Generate one unapproved WB coin layer through Atnos' normal channel workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.comfy_channels import ChannelComfyGenerationService, GenerationRequest


REPORT = Path("E:/Atnos/App/reports/llm-system-20260927/wb-coin-layer-20260929.json")


def main() -> int:
    if REPORT.drive.upper() != "E:":
        raise RuntimeError("WB evidence must remain on E:")
    request = GenerationRequest(
        channel="wealth_blueprint",
        workflow="WB_OBJECT",
        subject="(ink and gouache drawing:1.5) of a single unmarked brass coin",
        asset_type="object",
        composition="One complete round coin tilted slightly, centered on plain cream paper, generous clear margins, no other objects",
        lighting="soft warm lamplight, matte painted shading, fine navy ink outline",
        width=768,
        height=768,
        seed=20260929,
        force_regenerate=True,
    )
    meta = ChannelComfyGenerationService(get_settings()).generate(request)
    output = Path(meta.output_path)
    if output.drive.upper() != "E:":
        raise RuntimeError(f"Atnos generated outside E: {output}")
    evidence = {
        "date": datetime.now().astimezone().isoformat(),
        "purpose": "WB pilot A coin layer candidate, not approved for production",
        "request": request.model_dump(),
        "metadata": meta.model_dump(),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "appearance_status": "pending_visual_review",
        "user_approved": False,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "report": str(REPORT), "model": meta.model, "backend": meta.backend, "cache_hit": meta.cache_hit}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
