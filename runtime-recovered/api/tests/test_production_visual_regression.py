from __future__ import annotations

import json
from pathlib import Path

import pytest

from atnos.production.project_manifest import (
    BrandSpec,
    ProjectManifest,
    SceneManifest,
    SceneType,
    TimeRange,
    deterministic_hash,
    file_sha256,
    load_project_manifest,
    save_project_manifest,
)
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION
from atnos.production.visual_regression import (
    REFERENCE_ID,
    VisualStyleBaseline,
    VisualRegressionError,
    _baseline_hash,
    collect_visual_style_signature,
    compare_visual_regression,
    create_visual_regression_baseline,
    load_visual_regression_baseline,
    _compare_fixed_region_fingerprints,
)


PROBE = {
    "ok": True,
    "reason": "",
    "width": 1080,
    "height": 1920,
    "fps": 30.0,
    "codec": "h264",
    "pixel_format": "yuv420p",
}
FIXED_REGION = [
    {
        "scene_type": "HookScene",
        "average_hash": "0" * 64,
        "rgb_mean": [18.0, 19.0, 20.0],
        "layout_average_hash": "0" * 96,
        "layout_rgb_mean": [22.0, 23.0, 24.0],
    }
]


@pytest.fixture(autouse=True)
def _baseline_signing_key(monkeypatch) -> None:
    monkeypatch.setenv(
        "ATNOS_VISUAL_BASELINE_SIGNING_KEY",
        "test-only-visual-baseline-signing-key-64-bytes-long",
    )


def test_baseline_requires_explicit_accepted_reference(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    with pytest.raises(VisualRegressionError, match="requires reference_id"):
        create_visual_regression_baseline(
            paths["manifest"],
            paths["render"],
            paths["baseline"],
            reference_id=REFERENCE_ID,
            approval_status="pending",
            reviewer="test-reviewer",
            renderer_root=paths["renderer"],
            fonts_dir=paths["fonts"],
            media_probe=PROBE,
            fixed_region_fingerprints=FIXED_REGION,
        )


def test_same_style_passes_when_editorial_content_changes(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    evidence = _renderer_evidence(paths)
    baseline = create_visual_regression_baseline(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        reference_id=REFERENCE_ID,
        approval_status="accepted",
        reviewer="test-reviewer",
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )
    changed_manifest = tmp_path / "changed_manifest.json"
    save_project_manifest(changed_manifest, _manifest(headline="A different source-backed headline"))

    result = compare_visual_regression(
        changed_manifest,
        paths["render"],
        paths["baseline"],
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=_renderer_evidence(paths, manifest_path=changed_manifest),
        fixed_region_fingerprints=FIXED_REGION,
    )

    assert baseline.baseline_hash
    assert result["passed"] is True
    assert result["action"] == "PASS"
    assert result["reasons"] == []


def test_renderer_source_drift_blocks_final(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    evidence = _renderer_evidence(paths)
    create_visual_regression_baseline(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        reference_id=REFERENCE_ID,
        approval_status="accepted",
        reviewer="test-reviewer",
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )
    (paths["renderer"] / "src" / "design.tsx").write_text("changed spacing", encoding="utf-8")

    result = compare_visual_regression(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )

    assert result["passed"] is False
    assert result["action"] == "BLOCK_FINAL"
    assert "visual style drift: renderer_source_fingerprint" in result["reasons"]
    assert "visual style drift: render_time_evidence" in result["reasons"]


def test_brand_drift_blocks_final(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    evidence = _renderer_evidence(paths)
    create_visual_regression_baseline(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        reference_id=REFERENCE_ID,
        approval_status="accepted",
        reviewer="test-reviewer",
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )
    changed_manifest = tmp_path / "brand_drift.json"
    save_project_manifest(
        changed_manifest,
        _manifest(brand=BrandSpec(accent="#FF0000")),
    )

    result = compare_visual_regression(
        changed_manifest,
        paths["render"],
        paths["baseline"],
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=_renderer_evidence(paths, manifest_path=changed_manifest),
        fixed_region_fingerprints=FIXED_REGION,
    )

    assert result["passed"] is False
    assert result["reasons"] == ["visual style drift: brand"]


def test_tampered_baseline_is_rejected(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    evidence = _renderer_evidence(paths)
    create_visual_regression_baseline(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        reference_id=REFERENCE_ID,
        approval_status="accepted",
        reviewer="test-reviewer",
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )
    payload = json.loads(paths["baseline"].read_text(encoding="utf-8"))
    payload["signature"]["brand"]["accent"] = "#FF0000"
    paths["baseline"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(VisualRegressionError, match="hash mismatch"):
        load_visual_regression_baseline(paths["baseline"])


def test_rehashed_baseline_without_signing_key_is_rejected(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    create_visual_regression_baseline(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        reference_id=REFERENCE_ID,
        approval_status="accepted",
        reviewer="test-reviewer",
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=_renderer_evidence(paths),
        fixed_region_fingerprints=FIXED_REGION,
    )
    payload = json.loads(paths["baseline"].read_text(encoding="utf-8"))
    payload["signature"]["brand"]["accent"] = "#FF0000"
    forged = VisualStyleBaseline.model_validate(payload)
    payload["baseline_hash"] = _baseline_hash(forged)
    paths["baseline"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(VisualRegressionError, match="approval signature mismatch"):
        load_visual_regression_baseline(paths["baseline"])


def test_final_assembly_from_different_render_cannot_reuse_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _fixture_paths(tmp_path)
    marker_render = paths["render"]
    different_raw = tmp_path / "different-raw.mp4"
    different_raw.write_bytes(b"different-render-output")
    final_render = tmp_path / "final.mp4"
    final_render.write_bytes(b"muxed-final-output")
    manifest = load_project_manifest(paths["manifest"])
    source_signature = collect_visual_style_signature(
        paths["manifest"],
        marker_render,
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        fixed_region_fingerprints=FIXED_REGION,
    )
    marker = {
        "renderer_version": REMOTION_RENDERER_VERSION,
        "renderer_source_fingerprint": source_signature["renderer_source_fingerprint"],
        "renderer_source_hashes": source_signature["renderer_source_hashes"],
        "font_assets": source_signature["font_assets"],
        "manifest_content_hash": manifest.content_hash,
        "output": str(marker_render),
        "output_sha256": file_sha256(marker_render),
    }
    assembly = {
        "renderer": {"renderer": marker},
        "assembly_provenance": {
            "raw_video_path": str(different_raw),
            "raw_renderer_sha256": file_sha256(different_raw),
            "renderer_manifest_content_hash": manifest.content_hash,
            "renderer_version": REMOTION_RENDERER_VERSION,
        },
    }
    monkeypatch.setattr(
        "atnos.production.steps.assembler.verify_remotion_assembly_provenance",
        lambda *_args, **_kwargs: {"verified": True, "checks": {}},
    )

    signature = collect_visual_style_signature(
        paths["manifest"],
        final_render,
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=assembly,
        fixed_region_fingerprints=FIXED_REGION,
    )

    evidence = signature["render_time_evidence"]
    assert evidence["assembly_marker_binding_verified"] is False
    assert evidence["final_output_binding_verified"] is False
    assert evidence["verified"] is False


def test_tampered_approval_evidence_blocks_comparison(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    evidence = _renderer_evidence(paths)
    create_visual_regression_baseline(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        reference_id=REFERENCE_ID,
        approval_status="accepted",
        reviewer="test-reviewer",
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )
    approval = paths["baseline"].with_name("reference_approval.json")
    payload = json.loads(approval.read_text(encoding="utf-8"))
    payload["reviewer"] = "different-reviewer"
    approval.write_text(json.dumps(payload), encoding="utf-8")

    result = compare_visual_regression(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )

    assert result["passed"] is False
    assert "approval evidence mismatch" in result["reasons"][0]


def test_fixed_region_pixel_drift_blocks_final(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    evidence = _renderer_evidence(paths)
    create_visual_regression_baseline(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        reference_id=REFERENCE_ID,
        approval_status="accepted",
        reviewer="test-reviewer",
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=FIXED_REGION,
    )
    changed = [
        {
            "scene_type": "HookScene",
            "average_hash": "f" * 64,
            "rgb_mean": [90.0, 90.0, 90.0],
            "layout_average_hash": "0" * 96,
            "layout_rgb_mean": [22.0, 23.0, 24.0],
        }
    ]

    result = compare_visual_regression(
        paths["manifest"],
        paths["render"],
        paths["baseline"],
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        renderer_evidence=evidence,
        fixed_region_fingerprints=changed,
    )

    assert result["passed"] is False
    assert "visual style drift: fixed region pixels for HookScene" in result["reasons"]


def test_image_scene_blank_main_region_is_blocked() -> None:
    expected = [
        {
            "scene_type": "ImageScene",
            "average_hash": "0" * 64,
            "rgb_mean": [18.0, 19.0, 20.0],
            "layout_average_hash": "0" * 96,
            "layout_rgb_mean": [30.0, 31.0, 32.0],
            "image_content_luma_stddev": 24.0,
            "image_content_luma_stddev_samples": [24.0, 24.0, 24.0],
        }
    ]
    candidate = [
        {
            **expected[0],
            "image_content_luma_stddev": 0.0,
            "image_content_luma_stddev_samples": [24.0, 0.0, 24.0],
        }
    ]

    result = _compare_fixed_region_fingerprints(
        expected,
        candidate,
        hash_distance_max=0.18,
        rgb_delta_max=18.0,
        layout_hash_distance_max=0.35,
        layout_rgb_delta_max=45.0,
        image_layout_hash_distance_max=0.70,
        image_layout_rgb_delta_max=110.0,
        image_content_luma_stddev_min=6.0,
    )

    assert result["passed"] is False
    assert "visual style drift: blank image content for ImageScene" in result["reasons"]


def _fixture_paths(tmp_path: Path) -> dict[str, Path]:
    renderer = tmp_path / "remotion-renderer"
    for relative in (
        "package.json",
        "pnpm-lock.yaml",
        "scripts/render.ts",
        "src/index.ts",
        "src/Root.tsx",
        "src/contracts.ts",
        "src/design.tsx",
        "src/scenes.tsx",
        "src/WealthBlueprintShort.tsx",
    ):
        source = renderer / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"stable {relative}", encoding="utf-8")
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "Montserrat-Bold.ttf").write_bytes(b"regular-font")
    (fonts / "Montserrat-ExtraBold.ttf").write_bytes(b"extra-bold-font")
    manifest = tmp_path / "project_manifest.json"
    save_project_manifest(manifest, _manifest())
    render = tmp_path / "reference.mp4"
    render.write_bytes(b"verified-reference-render")
    return {
        "renderer": renderer,
        "fonts": fonts,
        "manifest": manifest,
        "render": render,
        "baseline": tmp_path / "baseline.json",
    }


def _renderer_evidence(
    paths: dict[str, Path],
    *,
    manifest_path: Path | None = None,
) -> dict:
    active_manifest_path = manifest_path or paths["manifest"]
    signature = collect_visual_style_signature(
        active_manifest_path,
        paths["render"],
        renderer_root=paths["renderer"],
        fonts_dir=paths["fonts"],
        media_probe=PROBE,
        fixed_region_fingerprints=FIXED_REGION,
    )
    manifest = load_project_manifest(active_manifest_path)
    return {
        "renderer_version": REMOTION_RENDERER_VERSION,
        "renderer_source_fingerprint": signature["renderer_source_fingerprint"],
        "renderer_source_hashes": signature["renderer_source_hashes"],
        "font_assets": signature["font_assets"],
        "manifest_content_hash": manifest.content_hash,
        "output": str(paths["render"]),
        "output_sha256": file_sha256(paths["render"]),
    }


def _manifest(
    *,
    headline: str = "Build the first thousand",
    brand: BrandSpec | None = None,
) -> ProjectManifest:
    return ProjectManifest(
        manifest_id="manifest-job-1",
        job_id="job-1",
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="topic-1",
        topic_title=headline,
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="brand-v1-test",
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="workflow-v1",
        voice_version="preview",
        status="rendered",
        current_step="qa",
        brand=brand or BrandSpec(),
        scenes=[
            SceneManifest(
                scene_id="s01",
                scene_type=SceneType.HOOK,
                timing=TimeRange(start_sec=0, end_sec=3),
                narration=headline,
                caption=headline,
                headline=headline,
                input_hash=deterministic_hash({"headline": headline}),
            )
        ],
        created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:00:00+00:00",
    )
