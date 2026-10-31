"""Asset quality gate — technical validation before Asset Brain approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from atnos.production.comfy_channels.isolation import ChannelIsolationError, assert_output_path_isolated
from atnos.production.comfy_channels.models import ChannelComfyProfile, GenerationMetadata, GenerationRequest


class AssetQualityGateResult:
    def __init__(self, *, passed: bool, verdict: str, checks: list[dict[str, Any]]) -> None:
        self.passed = passed
        self.verdict = verdict  # VALIDATED | FAIL_QUALITY
        self.checks = checks

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "verdict": self.verdict, "checks": self.checks}


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def run_asset_quality_gate(
    *,
    profile: ChannelComfyProfile,
    request: GenerationRequest,
    meta: GenerationMetadata,
    generated_root: Path,
    require_alpha: bool = False,
    character_consistency_required: bool = False,
) -> AssetQualityGateResult:
    checks: list[dict[str, Any]] = []
    path = Path(meta.output_path)

    checks.append(_check("file_exists", path.is_file(), str(path)))
    size_ok = path.is_file() and path.stat().st_size >= 2048
    checks.append(_check("file_integrity", size_ok, f"{path.stat().st_size if path.is_file() else 0} bytes"))

    try:
        assert_output_path_isolated(profile, path, generated_root)
        checks.append(_check("channel_isolation", True, profile.output_subdir))
    except ChannelIsolationError as exc:
        checks.append(_check("channel_isolation", False, str(exc)))

    dims_ok = False
    alpha_ok = not require_alpha
    no_tiny = False
    try:
        with Image.open(path) as img:
            w, h = img.size
            dims_ok = w >= 256 and h >= 256 and w == meta.width and h == meta.height
            checks.append(_check("dimensions", dims_ok, f"{w}x{h} expected {meta.width}x{meta.height}"))
            no_tiny = w * h >= 256 * 256
            if require_alpha:
                alpha_ok = img.mode in {"RGBA", "LA"} or ("transparency" in img.info)
                checks.append(_check("alpha_channel", alpha_ok, f"mode={img.mode}"))
            else:
                checks.append(_check("alpha_channel", True, "not required"))
    except OSError as exc:
        checks.append(_check("dimensions", False, str(exc)))
        checks.append(_check("alpha_channel", False, str(exc)))

    if require_alpha and path.is_file():
        try:
            from atnos.production.cutout_quality import run_alpha_qc

            qc = run_alpha_qc(path, require_alpha=True)
            checks.append(_check("alpha_qc", qc.passed, f"verdict={qc.verdict}"))
            meta.generation_settings = dict(meta.generation_settings or {})
            meta.generation_settings["alpha_qc"] = qc.as_dict()
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("alpha_qc", False, str(exc)[:200]))
    else:
        checks.append(_check("alpha_qc", True, "not required"))

    provenance_ok = bool(meta.workflow_id and meta.workflow_version and meta.model and meta.seed is not None)
    checks.append(
        _check(
            "provenance",
            provenance_ok,
            f"{meta.workflow_id}@{meta.workflow_version} model={meta.model} seed={meta.seed}",
        )
    )

    ref_ok = True
    if character_consistency_required or (request.references or meta.reference_paths):
        ref_ok = bool(meta.reference_paths) and all(Path(p).is_file() for p in meta.reference_paths)
        checks.append(
            _check(
                "reference_metadata",
                ref_ok,
                f"{len(meta.reference_paths)} refs; mode={meta.generation_settings.get('reference_conditioning')}",
            )
        )
        cond = str(meta.generation_settings.get("reference_conditioning") or "")
        cond_ok = cond in {"core_img2img", "prompt_anchor_names"} and bool(meta.reference_paths)
        # Prefer core_img2img for character assets
        if character_consistency_required:
            cond_ok = cond == "core_img2img" and bool(meta.reference_paths)
        checks.append(_check("reference_conditioning", cond_ok, cond or "missing"))
    else:
        checks.append(_check("reference_metadata", True, "not required"))
        checks.append(_check("reference_conditioning", True, "not required"))

    meta_path = path.with_name("metadata.json")
    meta_ok = meta_path.is_file()
    if meta_ok:
        try:
            json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta_ok = False
    checks.append(_check("metadata_json", meta_ok, str(meta_path)))

    failed = [c for c in checks if not c["ok"]]
    # Never auto-approve; VALIDATED means technical gate passed, approval stays pending.
    if failed or not no_tiny:
        return AssetQualityGateResult(passed=False, verdict="FAIL_QUALITY", checks=checks)
    return AssetQualityGateResult(passed=True, verdict="VALIDATED", checks=checks)
