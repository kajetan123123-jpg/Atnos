from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from atnos.core.config import Settings
from content.visual_recreation_asset_pack import VisualRecreationAssetPackService


class FakeImageGenerator:
    def generate_image(
        self,
        *,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
        steps: int,
        seed: int | None = None,
        negative_prompt: str | None = None,
    ) -> object:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, height), (20, 80, 120)).save(output_path)
        return object()


def test_visual_recreation_asset_pack_writes_tool_prompts_without_generation() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan_path = _write_plan(root)
        output_dir = root / "pack"
        service = VisualRecreationAssetPackService(_settings(root), image_generator=FakeImageGenerator())

        result = service.build(
            visual_plan_json=plan_path,
            output_dir=output_dir,
            generate_images=False,
            render_video=False,
            max_scenes=2,
        )

        assert result.status == "prompt_pack_ready"
        assert Path(result.comfyui_prompts_path).exists()
        assert Path(result.runway_prompts_path).exists()
        assert Path(result.capcut_edit_plan_path).exists()
        assert len(result.scene_assets) == 2
        assert result.scene_assets[0].status == "prompt_only"
        assert "Source Shorts are used as format references only" in Path(
            output_dir / "visual_recreation_asset_pack_report.md"
        ).read_text(encoding="utf-8")


def test_visual_recreation_asset_pack_generates_scene_images_with_injected_generator() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan_path = _write_plan(root)
        output_dir = root / "pack"
        service = VisualRecreationAssetPackService(_settings(root), image_generator=FakeImageGenerator())

        result = service.build(
            visual_plan_json=plan_path,
            output_dir=output_dir,
            generate_images=True,
            render_video=False,
            max_scenes=1,
            width=320,
            height=568,
            steps=2,
        )

        assert result.status == "images_generated"
        assert result.scene_assets[0].status == "generated"
        image_path = Path(str(result.scene_assets[0].image_path))
        assert image_path.exists()
        with Image.open(image_path) as image:
            assert image.size == (320, 568)


def _settings(root: Path) -> Settings:
    return Settings(app_root=root, data_dir=root / "data")


def _write_plan(root: Path) -> Path:
    plan = [
        {
            "plan_id": "visual-plan-01-ai-animal-microstory",
            "brief_id": "brief-1",
            "source_pattern": "ai_animal_microstory",
            "source_title": "Daddy Fights Shark to Save a Kitten",
            "source_channel": "source",
            "source_url": "https://www.youtube.com/watch?v=ref",
            "source_video_id": "ref",
            "source_thumbnail_path": None,
            "reference_visual_summary": "format mechanics only",
            "safe_similarity_target": "match pacing, not source assets",
            "character_sheet_prompt": "original cinematic cat protagonist",
            "thumbnail_prompt": "original thumbnail",
            "caption_style": "large readable captions",
            "color_palette": ["teal", "orange"],
            "camera_style": "fast push-ins",
            "repair_checklist": ["first frame readable"],
            "forbidden_copying": ["do not copy source footage"],
            "scene_prompts": [
                {
                    "scene_id": "scene_01_hook",
                    "timing": "0-2s",
                    "goal": "stop scroll",
                    "image_prompt": "vertical cat rescue scene",
                    "motion_prompt": "fast push-in",
                    "overlay_text": "HOOK",
                    "camera": "close-up",
                    "negative_prompt": "watermark",
                },
                {
                    "scene_id": "scene_02_problem",
                    "timing": "2-8s",
                    "goal": "show problem",
                    "image_prompt": "vertical danger scene",
                    "motion_prompt": "quick cut",
                    "overlay_text": "PROBLEM",
                    "camera": "medium shot",
                    "negative_prompt": "logo",
                },
            ],
        }
    ]
    path = root / "visual_recreation_plans.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
