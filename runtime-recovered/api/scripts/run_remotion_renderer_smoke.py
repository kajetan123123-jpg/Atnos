from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from atnos.core.paths import atnos_state_dir
from atnos.production.project_manifest import (
    AssetProvenance,
    ProjectManifest,
    QAAction,
    QAResult,
    SceneAsset,
    SceneManifest,
    SceneType,
    TimeRange,
    deterministic_hash,
    file_sha256,
    save_project_manifest,
)
from atnos.production.remotion_renderer import (
    REMOTION_RENDERER_VERSION,
    render_project_manifest,
)


def main() -> int:
    output_dir = atnos_state_dir() / "reports" / "remotion_renderer_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "source_image.png"
    _write_source_image(image_path)
    manifest_path = output_dir / "project_manifest.json"
    output_path = output_dir / "renderer_silent.mp4"
    now = datetime.now(UTC).isoformat()
    image_input_hash = deterministic_hash({"kind": "technical_smoke_image", "version": 1})
    image_asset = SceneAsset(
        asset_id="s02-selected-image",
        role="selected_image",
        path=str(image_path),
        exists=True,
        provenance=AssetProvenance(
            provider="deterministic_smoke_fixture",
            model="none",
            workflow="pillow_technical_fixture_v1",
            prompt="Technical renderer fixture; not a production content asset.",
            seed=0,
            generation_parameters={"width": 1080, "height": 1920},
            input_hash=image_input_hash,
            artifact_hash=file_sha256(image_path),
        ),
        qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
    )
    scenes = [
        _scene("s01", SceneType.HOOK, 0, 2, "Your money needs a system.", "YOUR MONEY NEEDS A SYSTEM"),
        _scene(
            "s02",
            SceneType.IMAGE,
            2,
            4,
            "Small choices compound over time.",
            "SMALL CHOICES COMPOUND",
            assets=[image_asset],
        ),
        _scene(
            "s03",
            SceneType.NUMBER,
            4,
            6,
            "Save 20% before spending.",
            "SAVE 20% FIRST",
            number_value="20%",
        ),
        _scene(
            "s04",
            SceneType.CHART,
            6,
            8,
            "A balance can grow from 10 to 18 to 27.",
            "CONSISTENCY COMPOUNDS",
            chart_values=[10, 18, 27],
        ),
        _scene("s05", SceneType.CTA, 8, 10, "Automate one transfer today.", "AUTOMATE ONE TRANSFER"),
    ]
    manifest = ProjectManifest(
        manifest_id="manifest-remotion-technical-smoke",
        job_id="remotion-technical-smoke",
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="technical-smoke",
        topic_title="Technical renderer smoke",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="brand-reference-unapproved-v0.1",
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="technical-smoke-v1",
        voice_version="none-silent-render",
        status="technical_smoke",
        current_step="remotion_renderer",
        scenes=scenes,
        created_at=now,
        updated_at=now,
    )
    saved = save_project_manifest(manifest_path, manifest)
    result = render_project_manifest(manifest_path, output_path)
    report = {
        "status": "verified" if result.get("ok") else "failed",
        "scope": "technical_renderer_only",
        "not_production_content_evidence": True,
        "manifest_content_hash": saved.content_hash,
        "result": result,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "report_path": str(report_path)}, ensure_ascii=False, indent=2))
    return 0


def _scene(
    scene_id: str,
    scene_type: SceneType,
    start: float,
    end: float,
    caption: str,
    headline: str,
    *,
    number_value: str | None = None,
    chart_values: list[float] | None = None,
    assets: list[SceneAsset] | None = None,
) -> SceneManifest:
    material = {
        "scene_id": scene_id,
        "scene_type": scene_type,
        "timing": [start, end],
        "caption": caption,
        "headline": headline,
        "number_value": number_value,
        "chart_values": chart_values or [],
    }
    return SceneManifest(
        scene_id=scene_id,
        scene_type=scene_type,
        timing=TimeRange(start_sec=start, end_sec=end),
        narration=caption,
        caption=caption,
        headline=headline,
        overlay_text=headline,
        number_value=number_value,
        chart_values=chart_values or [],
        input_hash=deterministic_hash(material),
        assets=assets or [],
    )


def _write_source_image(path: Path) -> None:
    image = Image.new("RGB", (1080, 1920), "#111111")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((110, 300, 970, 1450), radius=72, fill="#1C1C1E", outline="#0F5257", width=10)
    for index, height in enumerate((180, 300, 450, 620)):
        left = 190 + index * 190
        draw.rounded_rectangle((left, 1250 - height, left + 110, 1250), radius=24, fill="#39FF9F")
    draw.line((190, 1250, 870, 520), fill="#F5F5F5", width=24)
    draw.ellipse((820, 470, 920, 570), fill="#39FF9F")
    image.save(path, format="PNG", optimize=True)


if __name__ == "__main__":
    raise SystemExit(main())
