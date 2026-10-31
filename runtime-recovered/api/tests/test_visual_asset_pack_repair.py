from __future__ import annotations

import json
from pathlib import Path
import tempfile

from PIL import Image

from atnos.core.config import Settings
from content.visual_asset_pack_repair import VisualAssetPackBrightnessRepairService


def test_brightness_repair_writes_repaired_scene_without_source_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asset_pack = root / "asset_pack"
        scenes = asset_pack / "scenes"
        scenes.mkdir(parents=True)
        source_image = scenes / "scene_01_hook.png"
        Image.new("RGB", (64, 96), (40, 40, 40)).save(source_image)
        (asset_pack / "visual_recreation_asset_pack_result.json").write_text(
            json.dumps(
                {
                    "source_short_dir": str(root / "source_short"),
                    "scene_assets": [
                        {
                            "scene_id": "scene_01_hook",
                            "image_path": str(source_image),
                            "status": "generated",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        settings = Settings(data_dir=root / "data")
        output_dir = root / "repair"
        result = VisualAssetPackBrightnessRepairService(settings).run(
            asset_pack_dir=asset_pack,
            output_dir=output_dir,
            brightness_factor=1.5,
            render_video=False,
        )

        assert result.status == "images_repaired"
        repaired = Path(result.repaired_scene_paths[0])
        assert repaired.exists()
        assert repaired != source_image
        with Image.open(source_image) as original, Image.open(repaired) as changed:
            assert changed.convert("RGB").getpixel((0, 0))[0] > original.convert("RGB").getpixel((0, 0))[0]
        assert (output_dir / "visual_asset_pack_brightness_repair_result.json").exists()
        assert "Public Shorts were used only as format references" in (
            output_dir / "visual_asset_pack_brightness_repair_report.md"
        ).read_text(encoding="utf-8")
