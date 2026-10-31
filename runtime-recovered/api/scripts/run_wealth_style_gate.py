from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from atnos.core.paths import atnos_state_dir
from atnos.production.project_manifest import (
    AssetProvenance,
    BrandSpec,
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
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION, render_project_manifest


STYLE_OPTIONS = {
    "a_institutional_emerald": BrandSpec(
        background="#0B0F0E",
        panel="#151B19",
        accent="#2FE6A6",
        accentSoft="#224C42",
        text="#F4F7F5",
        muted="#93A69F",
        wordmark="WEALTH BLUEPRINT",
    ),
    "b_midnight_cobalt": BrandSpec(
        background="#080D18",
        panel="#121B2B",
        accent="#67B7FF",
        accentSoft="#243C5A",
        text="#F7FAFF",
        muted="#98A9C3",
        wordmark="WEALTH BLUEPRINT",
    ),
    "c_private_banking_bronze": BrandSpec(
        background="#0E0D0B",
        panel="#1C1914",
        accent="#D8B36A",
        accentSoft="#58462C",
        text="#F8F1E4",
        muted="#B3A58D",
        wordmark="WEALTH BLUEPRINT",
    ),
}


def main() -> int:
    output_root = atnos_state_dir() / "reports" / "wealth_v1_style_gate"
    output_root.mkdir(parents=True, exist_ok=True)
    source_image = output_root / "neutral_finance_fixture.png"
    _write_source_image(source_image)
    runs: list[dict[str, object]] = []

    for style_key, brand in STYLE_OPTIONS.items():
        style_dir = output_root / style_key
        style_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = style_dir / "project_manifest.json"
        video_path = style_dir / "style_preview_silent.mp4"
        manifest = _build_manifest(style_key, brand, source_image)
        saved = save_project_manifest(manifest_path, manifest)
        render = render_project_manifest(manifest_path, video_path)
        frames = _extract_frames(video_path, style_dir)
        contact_sheet = style_dir / "contact_sheet.png"
        _write_contact_sheet(frames, contact_sheet, style_key)
        runs.append(
            {
                "style_key": style_key,
                "brand": brand.model_dump(mode="json"),
                "manifest_path": str(manifest_path),
                "manifest_content_hash": saved.content_hash,
                "video_path": str(video_path),
                "video_sha256": file_sha256(video_path),
                "contact_sheet_path": str(contact_sheet),
                "contact_sheet_sha256": file_sha256(contact_sheet),
                "render": render,
            }
        )

    report = {
        "status": "verified",
        "scope": "wealth_blueprint_visual_style_gate",
        "product_status": "pending_user_approval",
        "not_reference_short": True,
        "renderer_version": REMOTION_RENDERER_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "shared_constraints": {
            "resolution": "1080x1920",
            "fps": 30,
            "typography": "Montserrat Bold / ExtraBold",
            "scene_types": [item.value for item in SceneType],
            "layout": "identical across variants",
        },
        "runs": runs,
    }
    report_path = output_root / "style_gate_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), **report}, ensure_ascii=False, indent=2))
    return 0


def _build_manifest(style_key: str, brand: BrandSpec, image_path: Path) -> ProjectManifest:
    now = datetime.now(UTC).isoformat()
    image_asset = SceneAsset(
        asset_id="s02-selected-image",
        role="selected_image",
        path=str(image_path),
        exists=True,
        provenance=AssetProvenance(
            provider="deterministic_style_fixture",
            model="none",
            workflow="pillow_finance_fixture_v1",
            prompt="Neutral finance fixture for visual-style comparison only.",
            seed=0,
            generation_parameters={"width": 1080, "height": 1920},
            input_hash=deterministic_hash({"fixture": "neutral_finance", "version": 1}),
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
            "Save 20 percent before spending.",
            "SAVE 20% FIRST",
            number_value="20%",
        ),
        _scene(
            "s04",
            SceneType.CHART,
            6,
            8,
            "A balance can grow from ten to eighteen to twenty seven.",
            "CONSISTENCY COMPOUNDS",
            chart_values=[10, 18, 27],
        ),
        _scene("s05", SceneType.CTA, 8, 10, "Automate one transfer today.", "AUTOMATE ONE TRANSFER"),
    ]
    return ProjectManifest(
        manifest_id=f"manifest-style-gate-{style_key}",
        job_id=f"style-gate-{style_key}",
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="style-gate",
        topic_title="Wealth Blueprint visual style gate",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version=f"unapproved-{style_key}-v1",
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="style-gate-v1",
        voice_version="none-silent-preview",
        brand=brand,
        status="style_gate_preview",
        current_step="visual_style_gate",
        scenes=scenes,
        created_at=now,
        updated_at=now,
    )


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
    image = Image.new("RGB", (1080, 1920), "#151719")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((96, 250, 984, 1430), radius=72, fill="#202428", outline="#6D777C", width=8)
    draw.rounded_rectangle((210, 460, 870, 1240), radius=64, fill="#101315", outline="#AAB3B6", width=10)
    for index, height in enumerate((170, 280, 430, 610)):
        left = 270 + index * 145
        draw.rounded_rectangle((left, 1120 - height, left + 82, 1120), radius=20, fill="#D8E0E2")
    draw.line((270, 1120, 789, 540), fill="#FFFFFF", width=24)
    draw.ellipse((738, 489, 840, 591), fill="#FFFFFF")
    image.save(path, format="PNG", optimize=True)


def _extract_frames(video_path: Path, output_dir: Path) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:  # pragma: no cover - environment guard
            raise RuntimeError("ffmpeg is required for style-gate frame extraction") from exc
    frames: list[Path] = []
    for index, second in enumerate((1, 3, 5, 7, 9), start=1):
        target = output_dir / f"frame_{index:02d}_{second:02d}s.png"
        process = subprocess.run(
            [ffmpeg, "-y", "-ss", str(second), "-i", str(video_path), "-frames:v", "1", str(target)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if process.returncode != 0 or not target.is_file():
            raise RuntimeError(f"Could not extract frame at {second}s: {(process.stderr or '')[-1000:]}")
        frames.append(target)
    return frames


def _write_contact_sheet(frame_paths: list[Path], output_path: Path, style_key: str) -> None:
    thumb_width, thumb_height = 216, 384
    header_height = 72
    sheet = Image.new("RGB", (thumb_width * len(frame_paths), thumb_height + header_height), "#07090A")
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 20), style_key.replace("_", " ").upper(), fill="#FFFFFF")
    for index, frame_path in enumerate(frame_paths):
        with Image.open(frame_path) as source:
            frame = source.convert("RGB").resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)
        sheet.paste(frame, (index * thumb_width, header_height))
    sheet.save(output_path, format="PNG", optimize=True)


if __name__ == "__main__":
    raise SystemExit(main())
