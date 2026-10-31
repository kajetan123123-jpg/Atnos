"""Install one already-downloaded official full SDXL candidate on E:.

No image generation, model-service restart, source-profile rewriting or
approval is performed.  Run downloads with the pinned HF CLI command in
ILLUSTRATION-ENGINE.md before this installer.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from atnos.core.config import get_settings
from atnos.production.comfy_channels.illustration_checkpoint import (
    CANDIDATE_ID, CHECKPOINT_FILE, MODEL_BYTES, MODEL_SHA256, REPOSITORY,
    REVISION, SOURCE_FILE, IllustrationCheckpointError, receipt_path,
    sha256_file, verify_full_sdxl_checkpoint,
)
from atnos.production.comfy_channels.profiles import shared_models_root


def install(source_dir: Path) -> dict:
    settings = get_settings()
    source_dir = source_dir.resolve()
    if source_dir.drive.casefold() != "e:":
        raise IllustrationCheckpointError("Downloaded model and evidence must stay on E:")
    source = source_dir / SOURCE_FILE
    hub_path = source_dir / "hub-source.json"
    license_path = source_dir / "author-model-license.json"
    hub = json.loads(hub_path.read_text("utf-8-sig"))
    license_data = json.loads(license_path.read_text("utf-8-sig"))
    row = next((row for row in hub.get("siblings", []) if row.get("rfilename") == SOURCE_FILE), {})
    author_version = next((row for row in license_data.get("modelVersions", [])
                           if row.get("id") == 126688), {})
    author_files = author_version.get("files") or []
    if (hub.get("id") != REPOSITORY or hub.get("sha") != REVISION
            or row.get("lfs", {}).get("sha256") != MODEL_SHA256
            or row.get("size") != MODEL_BYTES
            or license_data.get("id") != 112902
            or "Image" not in (license_data.get("allowCommercialUse") or [])
            or not any(str(row.get("hashes", {}).get("SHA256", "")).casefold() == MODEL_SHA256
                       for row in author_files)):
        raise IllustrationCheckpointError("Author metadata, license, or model hash does not match")
    verification = verify_full_sdxl_checkpoint(source)
    target = shared_models_root(settings) / "checkpoints" / CHECKPOINT_FILE
    if target.resolve().drive.casefold() != "e:":
        raise IllustrationCheckpointError("Comfy checkpoint destination must stay on E:")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verify_full_sdxl_checkpoint(target)
    else:
        # The same-volume hardlink avoids duplicating the 6.94 GB source.
        os.link(source, target)
    payload = {
        "schema_version": "wb-illustration-checkpoint-1", "candidate_id": CANDIDATE_ID,
        "checkpoint_path": str(target.resolve()), "verification": verification,
        "source": {"repository": REPOSITORY, "revision": REVISION, "filename": SOURCE_FILE,
                   "url": f"https://huggingface.co/{REPOSITORY}/blob/{REVISION}/{SOURCE_FILE}",
                   "metadata_path": str(hub_path), "metadata_sha256": sha256_file(hub_path)},
        "license": {"commercial_images": True, "author_model_url": "https://civitai.com/models/112902",
                    "author_version_id": 126688, "metadata_path": str(license_path),
                    "metadata_sha256": sha256_file(license_path),
                    "base_license": "SDXL Open RAIL++; author permits commercial generated images"},
        "installed_at": datetime.now(UTC).isoformat(),
        "appearance_approval": "pending", "user_approved": False,
        "limitations": ["Alpha2 candidate; not a verified Wealth Blueprint visual standard",
                        "Checkpoint selection alone does not lock Alex identity or create animation",
                        "Generated assets still require actual image and scene review"],
    }
    receipt = receipt_path(settings)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    if receipt.exists():
        old = json.loads(receipt.read_text("utf-8"))
        for key in ("candidate_id", "checkpoint_path", "verification", "source", "license",
                    "appearance_approval", "user_approved"):
            if old.get(key) != payload[key]:
                raise IllustrationCheckpointError("Existing candidate installation receipt differs")
        return old
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.source_dir), ensure_ascii=False, indent=2))
