"""Mandatory renderer provenance for Wealth Blueprint Remotion orders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_KEYS = (
    "requested_renderer",
    "selected_renderer",
    "executed_renderer",
    "fallback_used",
    "project_manifest_path",
    "remotion_render_id",
    "remotion_output",
    "final_mp4_path",
)


def load_render_provenance(artifact_dir: Path) -> dict[str, Any]:
    path = artifact_dir / "render_provenance.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    edit_path = artifact_dir / "edit.json"
    if edit_path.is_file():
        edit = json.loads(edit_path.read_text(encoding="utf-8"))
        if isinstance(edit.get("render_provenance"), dict):
            return dict(edit["render_provenance"])
        # Backfill from pre-Phase-12 edit.json (E2E#1) when explicit provenance file is absent.
        manifest = Path(str(edit.get("manifest_path") or artifact_dir / "project_manifest.json"))
        final = Path(str(edit.get("output_file") or ""))
        remotion = edit.get("remotion") if isinstance(edit.get("remotion"), dict) else {}
        return {
            "requested_renderer": str(edit.get("requested_renderer") or edit.get("renderer") or "remotion"),
            "selected_renderer": str(edit.get("selected_renderer") or edit.get("renderer") or ""),
            "executed_renderer": str(edit.get("executed_renderer") or edit.get("renderer") or ""),
            "fallback_used": bool(edit.get("fallback_used") or edit.get("editorial_fallback")),
            "project_manifest_path": str(manifest),
            "remotion_render_id": str(
                remotion.get("render_id")
                or remotion.get("output_sha256")
                or edit.get("sha256")
                or ""
            ),
            "remotion_output": str(
                remotion.get("output_path") or remotion.get("output") or artifact_dir / "assets" / "renderer_silent.mp4"
            ),
            "final_mp4_path": str(final),
            "editorial_fallback": bool(edit.get("editorial_fallback")),
            "backfilled": True,
        }
    raise FileNotFoundError(f"render provenance missing under {artifact_dir}")


def assert_render_provenance(
    artifact_dir: Path,
    *,
    expected_requested: str,
    expected_selected: str = "remotion",
    expected_executed: str = "remotion",
    expected_fallback: bool = False,
) -> dict[str, Any]:
    """FAIL if provenance mismatches — even when an MP4 exists."""
    payload = load_render_provenance(artifact_dir)
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise AssertionError(f"provenance missing keys: {missing}")
    if str(payload["requested_renderer"]) != expected_requested:
        raise AssertionError(
            f"requested_renderer={payload['requested_renderer']!r} expected {expected_requested!r}"
        )
    if str(payload["selected_renderer"]) != expected_selected:
        raise AssertionError(
            f"selected_renderer={payload['selected_renderer']!r} expected {expected_selected!r}"
        )
    if str(payload["executed_renderer"]) != expected_executed:
        raise AssertionError(
            f"executed_renderer={payload['executed_renderer']!r} expected {expected_executed!r}"
        )
    if bool(payload["fallback_used"]) != expected_fallback:
        raise AssertionError(f"fallback_used={payload['fallback_used']!r} expected {expected_fallback!r}")
    if bool(payload.get("editorial_fallback")):
        raise AssertionError("editorial_fallback must be false")
    manifest = Path(str(payload["project_manifest_path"]))
    final = Path(str(payload["final_mp4_path"]))
    remotion_out = Path(str(payload["remotion_output"]))
    if not manifest.is_file():
        raise AssertionError(f"project_manifest_path missing: {manifest}")
    if not final.is_file() or final.stat().st_size < 4096:
        raise AssertionError(f"final_mp4_path missing/too small: {final}")
    if not remotion_out.is_file():
        raise AssertionError(f"remotion_output missing: {remotion_out}")
    if not str(payload.get("remotion_render_id") or "").strip():
        raise AssertionError("remotion_render_id empty")
    return payload
