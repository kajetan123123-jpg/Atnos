from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageStat
from pydantic import BaseModel, ConfigDict, Field

from atnos.core.config import Settings
from atnos.core.paths import atnos_state_dir
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.project_manifest import (
    ProjectManifest,
    canonical_json,
    deterministic_hash,
    file_sha256,
    load_project_manifest,
)
from atnos.production.remotion_renderer import probe_silent_render


STYLE_ID = "WEALTH_BLUEPRINT_STYLE_V1"
REFERENCE_ID = "WEALTH_BLUEPRINT_REFERENCE_001"
BASELINE_FILENAME = "visual_regression_baseline.json"
APPROVAL_FILENAME = "reference_approval.json"
FIXED_REGION_HASH_DISTANCE_MAX = 0.18
FIXED_REGION_RGB_DELTA_MAX = 18.0
LAYOUT_REGION_HASH_DISTANCE_MAX = 0.35
LAYOUT_REGION_RGB_DELTA_MAX = 45.0
IMAGE_LAYOUT_REGION_HASH_DISTANCE_MAX = 0.70
IMAGE_LAYOUT_REGION_RGB_DELTA_MAX = 110.0
IMAGE_CONTENT_LUMA_STDDEV_MIN = 6.0
IMAGE_CONTENT_SAMPLE_FRACTIONS = (0.2, 0.5, 0.8)
BASELINE_SIGNING_KEY_ENV = "ATNOS_VISUAL_BASELINE_SIGNING_KEY"
_RENDERER_STYLE_FILES = (
    "package.json",
    "pnpm-lock.yaml",
    "scripts/render.ts",
    "src/index.ts",
    "src/Root.tsx",
    "src/contracts.ts",
    "src/design.tsx",
    "src/scenes.tsx",
    "src/WealthBlueprintShort.tsx",
)


class VisualRegressionError(RuntimeError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualStyleBaseline(_StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    style_id: Literal["WEALTH_BLUEPRINT_STYLE_V1"] = STYLE_ID
    reference_id: Literal["WEALTH_BLUEPRINT_REFERENCE_001"] = REFERENCE_ID
    approval_status: Literal["accepted"] = "accepted"
    reference_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_render_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1)
    approval_note: str = ""
    signature: dict[str, Any]
    created_at: str
    baseline_hash: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    approval_signature: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")


def visual_regression_baseline_path(settings: Settings) -> Path:
    return (
        Path(settings.data_dir)
        / "production"
        / "styles"
        / STYLE_ID
        / BASELINE_FILENAME
    )


def create_visual_regression_baseline(
    manifest_path: Path,
    render_path: Path,
    output_path: Path,
    *,
    reference_id: str,
    approval_status: str,
    reviewer: str,
    approval_note: str = "",
    subtitle_style: dict[str, Any] | None = None,
    renderer_root: Path | None = None,
    fonts_dir: Path | None = None,
    media_probe: dict[str, Any] | None = None,
    renderer_evidence: dict[str, Any] | None = None,
    fixed_region_fingerprints: list[dict[str, Any]] | None = None,
) -> VisualStyleBaseline:
    """Zapisz wzorzec dopiero po jawnej akceptacji konkretnego renderu referencyjnego."""
    if reference_id != REFERENCE_ID or approval_status != "accepted":
        raise VisualRegressionError(
            f"Baseline requires reference_id={REFERENCE_ID} and approval_status=accepted"
        )
    if not reviewer.strip():
        raise VisualRegressionError("Accepted visual baseline requires a named reviewer")
    manifest = load_project_manifest(manifest_path)
    if manifest.channel != "wealth_blueprint":
        raise VisualRegressionError("Visual baseline supports only the wealth_blueprint channel")
    render_hash = file_sha256(render_path)
    if render_hash is None:
        raise VisualRegressionError(f"Accepted reference render does not exist: {render_path}")
    signature = collect_visual_style_signature(
        manifest_path,
        render_path,
        subtitle_style=subtitle_style,
        renderer_root=renderer_root,
        fonts_dir=fonts_dir,
        media_probe=media_probe,
        renderer_evidence=renderer_evidence,
        fixed_region_fingerprints=fixed_region_fingerprints,
    )
    if signature["render_time_evidence"].get("verified") is not True:
        raise VisualRegressionError(
            "Accepted reference is missing verified render-time style evidence"
        )
    baseline = VisualStyleBaseline(
        reference_manifest_hash=manifest.content_hash,
        reference_render_hash=render_hash,
        reviewer=reviewer.strip(),
        approval_note=approval_note.strip(),
        signature=signature,
        created_at=datetime.now(UTC).isoformat(),
    )
    hashed = baseline.model_copy(update={"baseline_hash": _baseline_hash(baseline)})
    stamped = hashed.model_copy(update={"approval_signature": _approval_signature(hashed)})
    _atomic_json_write(output_path, stamped.model_dump(mode="json"))
    _write_approval_sidecar(output_path, stamped)
    return stamped


def load_visual_regression_baseline(path: Path) -> VisualStyleBaseline:
    baseline = VisualStyleBaseline.model_validate_json(path.read_text(encoding="utf-8"))
    if baseline.baseline_hash != _baseline_hash(baseline):
        raise VisualRegressionError(f"Visual regression baseline hash mismatch: {path}")
    if not hmac.compare_digest(baseline.approval_signature, _approval_signature(baseline)):
        raise VisualRegressionError(f"Visual regression baseline approval signature mismatch: {path}")
    return baseline


def load_visual_regression_approval(path: Path) -> VisualStyleBaseline:
    """Load a signed baseline and require its separate owner-approval evidence."""
    baseline = load_visual_regression_baseline(path)
    _verify_approval_sidecar(path, baseline)
    return baseline


def compare_visual_regression(
    manifest_path: Path,
    render_path: Path,
    baseline_path: Path,
    *,
    subtitle_style: dict[str, Any] | None = None,
    renderer_root: Path | None = None,
    fonts_dir: Path | None = None,
    media_probe: dict[str, Any] | None = None,
    renderer_evidence: dict[str, Any] | None = None,
    fixed_region_fingerprints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Porownaj stale cechy stylu i rzeczywiste piksele stalego obszaru kadru."""
    try:
        baseline = load_visual_regression_baseline(baseline_path)
        _verify_approval_sidecar(baseline_path, baseline)
        actual = collect_visual_style_signature(
            manifest_path,
            render_path,
            subtitle_style=subtitle_style,
            renderer_root=renderer_root,
            fonts_dir=fonts_dir,
            media_probe=media_probe,
            renderer_evidence=renderer_evidence,
            fixed_region_fingerprints=fixed_region_fingerprints,
        )
    except (OSError, ValueError, VisualRegressionError) as exc:
        return {
            "status": "fail",
            "action": "BLOCK_FINAL",
            "passed": False,
            "reasons": [str(exc)],
            "baseline_path": str(baseline_path),
        }

    reasons: list[str] = []
    expected = baseline.signature
    for key in (
        "renderer_version",
        "renderer_source_fingerprint",
        "renderer_source_hashes",
        "font_assets",
        "brand",
        "scene_layout_order",
        "media_contract",
        "subtitle_style",
        "image_content_sample_fractions",
        "render_time_evidence",
    ):
        if expected.get(key) != actual.get(key):
            reasons.append(f"visual style drift: {key}")
    fixed_region = _compare_fixed_region_fingerprints(
        expected.get("fixed_region_fingerprints"),
        actual.get("fixed_region_fingerprints"),
        hash_distance_max=float(
            expected.get("fixed_region_hash_distance_max")
            or FIXED_REGION_HASH_DISTANCE_MAX
        ),
        rgb_delta_max=float(
            expected.get("fixed_region_rgb_delta_max") or FIXED_REGION_RGB_DELTA_MAX
        ),
        layout_hash_distance_max=float(
            expected.get("layout_region_hash_distance_max")
            or LAYOUT_REGION_HASH_DISTANCE_MAX
        ),
        layout_rgb_delta_max=float(
            expected.get("layout_region_rgb_delta_max") or LAYOUT_REGION_RGB_DELTA_MAX
        ),
        image_layout_hash_distance_max=float(
            expected.get("image_layout_region_hash_distance_max")
            or IMAGE_LAYOUT_REGION_HASH_DISTANCE_MAX
        ),
        image_layout_rgb_delta_max=float(
            expected.get("image_layout_region_rgb_delta_max")
            or IMAGE_LAYOUT_REGION_RGB_DELTA_MAX
        ),
        image_content_luma_stddev_min=float(
            expected.get("image_content_luma_stddev_min")
            or IMAGE_CONTENT_LUMA_STDDEV_MIN
        ),
    )
    if not fixed_region["passed"]:
        reasons.extend(fixed_region["reasons"])
    return {
        "status": "pass" if not reasons else "fail",
        "action": "PASS" if not reasons else "BLOCK_FINAL",
        "passed": not reasons,
        "reasons": reasons,
        "style_id": baseline.style_id,
        "reference_id": baseline.reference_id,
        "baseline_path": str(baseline_path),
        "baseline_hash": baseline.baseline_hash,
        "actual_signature_hash": deterministic_hash(actual),
        "fixed_region_comparison": fixed_region,
    }


def collect_visual_style_signature(
    manifest_path: Path,
    render_path: Path,
    *,
    subtitle_style: dict[str, Any] | None = None,
    renderer_root: Path | None = None,
    fonts_dir: Path | None = None,
    media_probe: dict[str, Any] | None = None,
    renderer_evidence: dict[str, Any] | None = None,
    fixed_region_fingerprints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = load_project_manifest(manifest_path)
    if manifest.channel != "wealth_blueprint":
        raise VisualRegressionError("Visual signature supports only wealth_blueprint")
    source_root = (renderer_root or _renderer_package_root()).resolve()
    source_hashes = _renderer_source_hashes(source_root)
    probe = (
        media_probe
        if media_probe is not None
        else probe_silent_render(render_path, require_silent=False)
    )
    if probe.get("ok") is not True:
        raise VisualRegressionError(
            f"Reference render failed media verification: {probe.get('reason') or 'unknown reason'}"
        )
    font_assets = _font_assets(fonts_dir)
    source_fingerprint = deterministic_hash(source_hashes)
    frame_fingerprints = (
        fixed_region_fingerprints
        if fixed_region_fingerprints is not None
        else _extract_fixed_region_fingerprints(manifest, render_path)
    )
    return {
        "renderer_version": manifest.renderer_version,
        "renderer_source_hashes": source_hashes,
        "renderer_source_fingerprint": source_fingerprint,
        "font_assets": font_assets,
        "brand": manifest.brand.model_dump(mode="json"),
        "scene_layout_order": [scene.scene_type.value for scene in manifest.scenes],
        "media_contract": {
            "width": int(probe.get("width") or 0),
            "height": int(probe.get("height") or 0),
            "fps": round(float(probe.get("fps") or 0.0), 3),
            "codec": str(probe.get("codec") or ""),
            "pixel_format": str(probe.get("pixel_format") or ""),
        },
        "subtitle_style": _normalized_subtitle_style(subtitle_style),
        "fixed_region_fingerprints": frame_fingerprints,
        "fixed_region_hash_distance_max": FIXED_REGION_HASH_DISTANCE_MAX,
        "fixed_region_rgb_delta_max": FIXED_REGION_RGB_DELTA_MAX,
        "layout_region_hash_distance_max": LAYOUT_REGION_HASH_DISTANCE_MAX,
        "layout_region_rgb_delta_max": LAYOUT_REGION_RGB_DELTA_MAX,
        "image_layout_region_hash_distance_max": IMAGE_LAYOUT_REGION_HASH_DISTANCE_MAX,
        "image_layout_region_rgb_delta_max": IMAGE_LAYOUT_REGION_RGB_DELTA_MAX,
        "image_content_luma_stddev_min": IMAGE_CONTENT_LUMA_STDDEV_MIN,
        "image_content_sample_fractions": list(IMAGE_CONTENT_SAMPLE_FRACTIONS),
        "render_time_evidence": _render_time_evidence(
            renderer_evidence,
            renderer_version=manifest.renderer_version,
            manifest_content_hash=manifest.content_hash,
            render_path=render_path,
            source_fingerprint=source_fingerprint,
            source_hashes=source_hashes,
            font_assets=font_assets,
        ),
    }


def _renderer_source_hashes(renderer_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in _RENDERER_STYLE_FILES:
        source = renderer_root / relative
        digest = file_sha256(source)
        if digest is None:
            raise VisualRegressionError(f"Renderer style source is missing: {source}")
        result[relative] = digest
    return result


def _font_assets(fonts_dir: Path | None) -> dict[str, dict[str, str]]:
    roots = []
    if fonts_dir is not None:
        roots.append(fonts_dir)
    elif os.getenv("ATNOS_FONTS_DIR"):
        roots.append(Path(str(os.environ["ATNOS_FONTS_DIR"])))
    else:
        roots.append(atnos_state_dir() / "fonts")
    roots.append(Path("C:/Windows/Fonts"))
    choices = {
        "regular": ("Montserrat-Bold.ttf", "montserrat-bold.ttf", "arialbd.ttf"),
        "extra_bold": ("Montserrat-ExtraBold.ttf", "montserrat-extrabold.ttf", "arialbd.ttf"),
    }
    result: dict[str, dict[str, str]] = {}
    for role, names in choices.items():
        selected = next(
            (root / name for root in roots for name in names if (root / name).is_file()),
            None,
        )
        if selected is None:
            raise VisualRegressionError(f"Renderer font is missing for role: {role}")
        digest = file_sha256(selected)
        if digest is None:
            raise VisualRegressionError(f"Unable to hash renderer font: {selected}")
        result[role] = {"filename": selected.name, "sha256": digest}
    return result


def _normalized_subtitle_style(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "font": str(source.get("font") or "Montserrat ExtraBold"),
        "font_size": int(source.get("font_size") or 132),
        "max_words_per_cue": int(source.get("max_words_per_cue") or 4),
        "max_duration_s": float(source.get("max_duration_s") or 1.5),
        "position": str(source.get("position") or "lower_third_center"),
        "fade_ms": source.get("fade_ms") or {"in": 80, "out": 60},
        "outline_px": int(source.get("outline_px") or 6),
        "frame_px": source.get("frame_px") or {"width": 1080, "height": 1920},
        "safe_area_px": source.get("safe_area_px")
        or {"left": 72, "right": 72, "top": 96, "bottom": 220},
    }


def _render_time_evidence(
    value: dict[str, Any] | None,
    *,
    renderer_version: str,
    manifest_content_hash: str,
    render_path: Path,
    source_fingerprint: str,
    source_hashes: dict[str, str],
    font_assets: dict[str, dict[str, str]],
) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    renderer_payload = source.get("renderer") if isinstance(source.get("renderer"), dict) else source
    marker = (
        renderer_payload.get("renderer")
        if isinstance(renderer_payload.get("renderer"), dict)
        else renderer_payload
    )
    marker_fonts = marker.get("font_assets") if isinstance(marker.get("font_assets"), dict) else {}
    marker_sources = (
        marker.get("renderer_source_hashes")
        if isinstance(marker.get("renderer_source_hashes"), dict)
        else {}
    )
    raw_output = Path(str(marker.get("output") or "")).expanduser()
    expected_output_hash = str(marker.get("output_sha256") or "")
    actual_output_hash = file_sha256(raw_output) if raw_output.is_file() else None
    direct_output_binding = (
        raw_output.is_file()
        and raw_output.resolve() == render_path.expanduser().resolve()
        and actual_output_hash == file_sha256(render_path)
    )
    assembly_binding = {"verified": False, "reason": "not supplied"}
    assembly_marker_binding = False
    if not direct_output_binding and isinstance(source.get("assembly_provenance"), dict):
        from atnos.production.steps.assembler import verify_remotion_assembly_provenance

        assembly_binding = verify_remotion_assembly_provenance(source, render_path)
        provenance = source["assembly_provenance"]
        provenance_raw_path = Path(str(provenance.get("raw_video_path") or "")).expanduser()
        assembly_marker_binding = (
            raw_output.is_file()
            and provenance_raw_path.is_file()
            and provenance_raw_path.resolve() == raw_output.resolve()
            and provenance.get("raw_renderer_sha256") == expected_output_hash
            and provenance.get("renderer_manifest_content_hash")
            == marker.get("manifest_content_hash")
            == manifest_content_hash
            and provenance.get("renderer_version")
            == marker.get("renderer_version")
            == renderer_version
        )
    evidence = {
        "renderer_version": str(marker.get("renderer_version") or ""),
        "renderer_source_fingerprint": str(marker.get("renderer_source_fingerprint") or ""),
        "font_assets": marker_fonts,
        "manifest_binding_verified": str(marker.get("manifest_content_hash") or "")
        == manifest_content_hash,
        "source_hashes_verified": marker_sources == source_hashes,
        "output_binding_verified": bool(expected_output_hash)
        and actual_output_hash == expected_output_hash,
        "assembly_marker_binding_verified": direct_output_binding or assembly_marker_binding,
        "final_output_binding_verified": direct_output_binding
        or (assembly_binding.get("verified") is True and assembly_marker_binding),
    }
    evidence["verified"] = (
        evidence["renderer_version"] == renderer_version
        and evidence["renderer_source_fingerprint"] == source_fingerprint
        and evidence["font_assets"] == font_assets
        and evidence["manifest_binding_verified"]
        and evidence["source_hashes_verified"]
        and evidence["output_binding_verified"]
        and evidence["assembly_marker_binding_verified"]
        and evidence["final_output_binding_verified"]
    )
    return evidence


def _extract_fixed_region_fingerprints(
    manifest: ProjectManifest,
    render_path: Path,
) -> list[dict[str, Any]]:
    """Zmierz faktyczne piksele stalego naglowka w srodku kazdej sceny."""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise VisualRegressionError("FFmpeg is unavailable for fixed-region visual comparison")
    fingerprints: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="atnos-visual-regression-") as temporary:
        temporary_root = Path(temporary)
        for index, scene in enumerate(manifest.scenes):
            sample_sec = (scene.timing.start_sec + scene.timing.end_sec) / 2
            frame_path = temporary_root / f"frame-{index:02d}.png"
            _extract_frame_at(
                ffmpeg,
                render_path,
                frame_path,
                sample_sec=sample_sec,
                scene_id=scene.scene_id,
            )
            with Image.open(frame_path) as frame:
                rgb = frame.convert("RGB")
                # Header/wordmark is fixed; editorial image and captions remain outside this crop.
                region = rgb.crop((60, 72, 1020, 184))
                fingerprint = {
                    "scene_type": scene.scene_type.value,
                    **_fingerprint_region(region, size=(16, 16)),
                }
                layout_region = rgb.crop((40, 220, 1040, 1520))
                layout = _fingerprint_region(layout_region, size=(16, 24))
                fingerprint.update(
                    {
                        "layout_average_hash": layout["average_hash"],
                        "layout_rgb_mean": layout["rgb_mean"],
                    }
                )
                if scene.scene_type.value == "ImageScene":
                    image_content = rgb.crop((100, 260, 980, 1000)).convert("L")
                    midpoint_luma = float(ImageStat.Stat(image_content).stddev[0])
            if scene.scene_type.value == "ImageScene":
                duration = scene.timing.end_sec - scene.timing.start_sec
                luma_samples: list[float] = []
                for sample_index, fraction in enumerate(IMAGE_CONTENT_SAMPLE_FRACTIONS):
                    if fraction == 0.5:
                        luma_samples.append(midpoint_luma)
                        continue
                    temporal_frame = temporary_root / f"frame-{index:02d}-image-{sample_index}.png"
                    _extract_frame_at(
                        ffmpeg,
                        render_path,
                        temporal_frame,
                        sample_sec=scene.timing.start_sec + duration * fraction,
                        scene_id=scene.scene_id,
                    )
                    with Image.open(temporal_frame) as sampled_frame:
                        content = (
                            sampled_frame.convert("RGB")
                            .crop((100, 260, 980, 1000))
                            .convert("L")
                        )
                        luma_samples.append(float(ImageStat.Stat(content).stddev[0]))
                fingerprint["image_content_luma_stddev_samples"] = [
                    round(value, 3) for value in luma_samples
                ]
                fingerprint["image_content_luma_stddev"] = round(min(luma_samples), 3)
            fingerprints.append(fingerprint)
    return fingerprints


def _extract_frame_at(
    ffmpeg: str,
    render_path: Path,
    frame_path: Path,
    *,
    sample_sec: float,
    scene_id: str,
) -> None:
    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{sample_sec:.3f}",
                "-i",
                str(render_path),
                "-frames:v",
                "1",
                "-y",
                str(frame_path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VisualRegressionError(
            f"Unable to sample visual regression frame for {scene_id}: {exc}"
        ) from exc
    if process.returncode != 0 or not frame_path.is_file():
        diagnostic = (process.stderr or process.stdout or "frame extraction failed")[-1200:]
        raise VisualRegressionError(
            f"Unable to sample visual regression frame for {scene_id}: {diagnostic}"
        )


def _compare_fixed_region_fingerprints(
    expected: Any,
    actual: Any,
    *,
    hash_distance_max: float,
    rgb_delta_max: float,
    layout_hash_distance_max: float,
    layout_rgb_delta_max: float,
    image_layout_hash_distance_max: float,
    image_layout_rgb_delta_max: float,
    image_content_luma_stddev_min: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    measurements: list[dict[str, Any]] = []
    if not isinstance(expected, list) or not isinstance(actual, list) or not expected or not actual:
        return {
            "passed": False,
            "reasons": ["visual style drift: fixed_region_fingerprints missing"],
            "measurements": measurements,
        }
    if len(expected) != len(actual):
        return {
            "passed": False,
            "reasons": ["visual style drift: fixed_region_fingerprints scene count"],
            "measurements": measurements,
        }
    for index, (reference, candidate) in enumerate(zip(expected, actual, strict=True)):
        scene_type = str(reference.get("scene_type") or "")
        candidate_type = str(candidate.get("scene_type") or "")
        if scene_type != candidate_type:
            reasons.append(f"visual style drift: fixed region scene {index + 1} type")
            continue
        try:
            expected_hash = int(str(reference["average_hash"]), 16)
            actual_hash = int(str(candidate["average_hash"]), 16)
            hash_distance = (expected_hash ^ actual_hash).bit_count() / 256
            expected_rgb = [float(value) for value in reference["rgb_mean"]]
            actual_rgb = [float(value) for value in candidate["rgb_mean"]]
            rgb_delta = max(abs(left - right) for left, right in zip(expected_rgb, actual_rgb, strict=True))
        except (KeyError, TypeError, ValueError) as exc:
            reasons.append(f"visual style drift: invalid fixed region evidence for {scene_type}: {exc}")
            continue
        measurements.append(
            {
                "scene_type": scene_type,
                "hash_distance": round(hash_distance, 4),
                "rgb_delta_max": round(rgb_delta, 3),
            }
        )
        if hash_distance > hash_distance_max or rgb_delta > rgb_delta_max:
            reasons.append(f"visual style drift: fixed region pixels for {scene_type}")
        if scene_type:
            try:
                expected_layout_hash = int(str(reference["layout_average_hash"]), 16)
                actual_layout_hash = int(str(candidate["layout_average_hash"]), 16)
                layout_hash_distance = (
                    (expected_layout_hash ^ actual_layout_hash).bit_count() / 384
                )
                expected_layout_rgb = [float(value) for value in reference["layout_rgb_mean"]]
                actual_layout_rgb = [float(value) for value in candidate["layout_rgb_mean"]]
                layout_rgb_delta = max(
                    abs(left - right)
                    for left, right in zip(
                        expected_layout_rgb,
                        actual_layout_rgb,
                        strict=True,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                reasons.append(
                    f"visual style drift: invalid layout region evidence for {scene_type}: {exc}"
                )
            else:
                measurements[-1].update(
                    {
                        "layout_hash_distance": round(layout_hash_distance, 4),
                        "layout_rgb_delta_max": round(layout_rgb_delta, 3),
                    }
                )
                effective_hash_max = (
                    image_layout_hash_distance_max
                    if scene_type == "ImageScene"
                    else layout_hash_distance_max
                )
                effective_rgb_max = (
                    image_layout_rgb_delta_max
                    if scene_type == "ImageScene"
                    else layout_rgb_delta_max
                )
                if layout_hash_distance > effective_hash_max or layout_rgb_delta > effective_rgb_max:
                    reasons.append(f"visual style drift: layout region pixels for {scene_type}")
                if scene_type == "ImageScene":
                    try:
                        image_samples = [
                            float(value)
                            for value in candidate["image_content_luma_stddev_samples"]
                        ]
                        image_luma_stddev = float(candidate["image_content_luma_stddev"])
                    except (KeyError, TypeError, ValueError) as exc:
                        reasons.append(
                            f"visual style drift: invalid image content evidence for {scene_type}: {exc}"
                        )
                    else:
                        if len(image_samples) != len(IMAGE_CONTENT_SAMPLE_FRACTIONS):
                            reasons.append(
                                f"visual style drift: invalid image temporal coverage for {scene_type}"
                            )
                        measurements[-1]["image_content_luma_stddev"] = round(
                            image_luma_stddev,
                            3,
                        )
                        measurements[-1]["image_content_luma_stddev_samples"] = [
                            round(value, 3) for value in image_samples
                        ]
                        if image_luma_stddev < image_content_luma_stddev_min:
                            reasons.append(f"visual style drift: blank image content for {scene_type}")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "measurements": measurements,
        "thresholds": {
            "hash_distance_max": hash_distance_max,
            "rgb_delta_max": rgb_delta_max,
            "layout_hash_distance_max": layout_hash_distance_max,
            "layout_rgb_delta_max": layout_rgb_delta_max,
            "image_layout_hash_distance_max": image_layout_hash_distance_max,
            "image_layout_rgb_delta_max": image_layout_rgb_delta_max,
            "image_content_luma_stddev_min": image_content_luma_stddev_min,
        },
    }


def _fingerprint_region(region: Image.Image, *, size: tuple[int, int]) -> dict[str, Any]:
    reduced = region.convert("L").resize(size, Image.Resampling.LANCZOS)
    pixels = list(reduced.getdata())
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if value >= mean else "0" for value in pixels)
    width = size[0] * size[1] // 4
    return {
        "average_hash": f"{int(bits, 2):0{width}x}",
        "rgb_mean": [round(float(value), 3) for value in ImageStat.Stat(region).mean[:3]],
    }


def _write_approval_sidecar(path: Path, baseline: VisualStyleBaseline) -> None:
    payload = {
        "reference_id": baseline.reference_id,
        "decision": baseline.approval_status,
        "reviewer": baseline.reviewer,
        "note": baseline.approval_note,
        "approved_at": baseline.created_at,
        "reference_manifest_hash": baseline.reference_manifest_hash,
        "reference_render_hash": baseline.reference_render_hash,
        "baseline_hash": baseline.baseline_hash,
        "approval_signature": baseline.approval_signature,
    }
    _atomic_json_write(path.with_name(APPROVAL_FILENAME), payload)


def _verify_approval_sidecar(path: Path, baseline: VisualStyleBaseline) -> None:
    approval_path = path.with_name(APPROVAL_FILENAME)
    try:
        payload = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualRegressionError(
            f"Accepted visual baseline approval evidence is unavailable: {approval_path}: {exc}"
        ) from exc
    required = {
        "reference_id": baseline.reference_id,
        "decision": baseline.approval_status,
        "reviewer": baseline.reviewer,
        "reference_manifest_hash": baseline.reference_manifest_hash,
        "reference_render_hash": baseline.reference_render_hash,
        "baseline_hash": baseline.baseline_hash,
        "approval_signature": baseline.approval_signature,
    }
    mismatched = [key for key, value in required.items() if payload.get(key) != value]
    if mismatched:
        raise VisualRegressionError(
            f"Accepted visual baseline approval evidence mismatch: {', '.join(mismatched)}"
        )


def _renderer_package_root() -> Path:
    return Path(__file__).resolve().parents[4] / "remotion-renderer"


def _baseline_hash(baseline: VisualStyleBaseline) -> str:
    payload = baseline.model_dump(mode="json")
    payload.pop("baseline_hash", None)
    payload.pop("approval_signature", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _approval_signature(baseline: VisualStyleBaseline) -> str:
    key = os.getenv(BASELINE_SIGNING_KEY_ENV, "").encode("utf-8")
    if len(key) < 32:
        raise VisualRegressionError(
            f"Accepted visual baseline requires {BASELINE_SIGNING_KEY_ENV} with at least 32 bytes"
        )
    payload = baseline.model_dump(mode="json")
    payload.pop("approval_signature", None)
    return hmac.new(key, canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
