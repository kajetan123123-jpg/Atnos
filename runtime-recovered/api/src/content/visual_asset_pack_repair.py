from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any, Protocol

from PIL import Image, ImageEnhance

from atnos.core.config import Settings
from content.motion_render_ffmpeg import MotionShortRenderService
from content.render_ffmpeg import ShortRenderService
from content.render_qa import RenderQaService


class AssetPackRenderer(Protocol):
    def render(
        self,
        *,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
        output_dir: Path,
    ) -> tuple[Path, dict[str, Any]]:
        ...


class AssetPackMotionRenderer(Protocol):
    def render_motion(
        self,
        *,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
        output_dir: Path,
    ) -> tuple[Path, dict[str, Any]]:
        ...


@dataclass(frozen=True, slots=True)
class VisualAssetPackBrightnessRepairResult:
    status: str
    source_asset_pack_dir: str
    output_dir: str
    repaired_scene_paths: tuple[str, ...]
    source_short_dir: str | None
    final_mp4: str | None
    render_qa_path: str | None
    brightness_factor: float
    contrast_factor: float
    sharpness_factor: float
    created_at: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VisualAssetPackBrightnessRepairService:
    """Repairs generated visual packs without copying reference Shorts.

    The public reference stays analysis-only. This pass edits Atnos-generated
    frames: brightness, contrast and sharpness, then optionally rerenders the
    video with the already generated local narration/subtitles.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        renderer: AssetPackRenderer | None = None,
        motion_renderer: AssetPackMotionRenderer | None = None,
        render_qa: RenderQaService | None = None,
    ) -> None:
        self.settings = settings
        self.renderer = renderer or ShortRenderService(settings)
        self.motion_renderer = motion_renderer or MotionShortRenderService(settings)
        self.render_qa = render_qa or RenderQaService()

    def run(
        self,
        *,
        asset_pack_dir: str | Path,
        output_dir: str | Path,
        brightness_factor: float = 1.28,
        contrast_factor: float = 1.08,
        sharpness_factor: float = 1.04,
        render_video: bool = True,
    ) -> VisualAssetPackBrightnessRepairResult:
        source_dir = Path(asset_pack_dir).resolve()
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        result_payload = self._load_asset_pack_result(source_dir)
        scene_paths = self._scene_image_paths(result_payload, source_dir)
        notes = [
            "Applied brightness repair to Atnos-generated scene frames.",
            "Safety: no source footage, source audio, source script, watermark or branding copied.",
        ]
        if not scene_paths:
            result = self._result(
                status="missing_scene_images",
                source_dir=source_dir,
                output_dir=output_path,
                repaired_scene_paths=(),
                source_short_dir=_optional_str(result_payload.get("source_short_dir")),
                final_mp4=None,
                render_qa_path=None,
                brightness_factor=brightness_factor,
                contrast_factor=contrast_factor,
                sharpness_factor=sharpness_factor,
                notes=tuple([*notes, "No generated scene images found in asset pack result."]),
            )
            self._write_result(result, output_path)
            return result

        self._copy_pack_metadata(source_dir, output_path)
        repaired_scene_paths = self._repair_images(
            scene_paths=scene_paths,
            output_dir=output_path / "scenes",
            brightness_factor=brightness_factor,
            contrast_factor=contrast_factor,
            sharpness_factor=sharpness_factor,
        )

        source_short_dir = _optional_path(result_payload.get("source_short_dir"))
        final_mp4: Path | None = None
        render_qa_path: Path | None = None
        if render_video:
            if source_short_dir is None:
                notes.append("Video render skipped: source_short_dir missing in asset pack result.")
            else:
                required = {
                    "audio": source_short_dir / "narration.wav",
                    "subtitles": source_short_dir / "subtitles.srt",
                }
                missing = [name for name, path in required.items() if not path.exists()]
                if missing:
                    notes.append(f"Video render skipped: missing {', '.join(missing)}.")
                else:
                    final_mp4, render_qa_path = self._render_video(
                        output_dir=output_path,
                        frame_paths=repaired_scene_paths,
                        audio_path=required["audio"],
                        subtitles_path=required["subtitles"],
                    )

        status = "video_rendered" if final_mp4 is not None else "images_repaired"
        result = self._result(
            status=status,
            source_dir=source_dir,
            output_dir=output_path,
            repaired_scene_paths=tuple(str(path) for path in repaired_scene_paths),
            source_short_dir=str(source_short_dir) if source_short_dir is not None else None,
            final_mp4=str(final_mp4) if final_mp4 is not None else None,
            render_qa_path=str(render_qa_path) if render_qa_path is not None else None,
            brightness_factor=brightness_factor,
            contrast_factor=contrast_factor,
            sharpness_factor=sharpness_factor,
            notes=tuple(notes),
        )
        self._write_result(result, output_path)
        return result

    @staticmethod
    def _load_asset_pack_result(source_dir: Path) -> dict[str, Any]:
        result_path = source_dir / "visual_recreation_asset_pack_result.json"
        if not result_path.exists():
            raise FileNotFoundError(f"Missing asset pack result: {result_path}")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("visual_recreation_asset_pack_result.json must contain an object.")
        return payload

    @staticmethod
    def _scene_image_paths(payload: dict[str, Any], source_dir: Path) -> list[Path]:
        paths: list[Path] = []
        for item in payload.get("scene_assets") or []:
            if not isinstance(item, dict):
                continue
            image_path = str(item.get("image_path") or "").strip()
            if not image_path:
                continue
            path = Path(image_path)
            if not path.is_absolute():
                path = source_dir / path
            if path.exists() and path.suffix.lower() == ".png":
                paths.append(path.resolve())
        return paths

    @staticmethod
    def _copy_pack_metadata(source_dir: Path, output_dir: Path) -> None:
        for name in (
            "source_visual_recreation_plan.json",
            "tool_sequence.json",
            "comfyui_prompts.json",
            "runway_prompts.md",
            "capcut_edit_plan.md",
            "visual_recreation_asset_pack_result.json",
        ):
            source = source_dir / name
            if source.exists():
                shutil.copyfile(source, output_dir / name)

    @staticmethod
    def _repair_images(
        *,
        scene_paths: list[Path],
        output_dir: Path,
        brightness_factor: float,
        contrast_factor: float,
        sharpness_factor: float,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        repaired: list[Path] = []
        for index, scene_path in enumerate(scene_paths, start=1):
            target = output_dir / scene_path.name
            with Image.open(scene_path) as source:
                image = source.convert("RGB")
                image = ImageEnhance.Brightness(image).enhance(brightness_factor)
                image = ImageEnhance.Contrast(image).enhance(contrast_factor)
                image = ImageEnhance.Sharpness(image).enhance(sharpness_factor)
                image.save(target)
            repaired.append(target.resolve())
        return repaired

    def _render_video(
        self,
        *,
        output_dir: Path,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
    ) -> tuple[Path, Path]:
        try:
            final_mp4, render_metadata = self.motion_renderer.render_motion(
                frame_paths=frame_paths,
                audio_path=audio_path,
                subtitles_path=subtitles_path,
                output_dir=output_dir,
            )
        except Exception:
            final_mp4, render_metadata = self.renderer.render(
                frame_paths=frame_paths,
                audio_path=audio_path,
                subtitles_path=subtitles_path,
                output_dir=output_dir,
            )
        qa = self.render_qa.build(
            final_mp4=final_mp4,
            render_metadata=render_metadata,
            subtitles_path=subtitles_path,
            audio_path=audio_path,
        )
        render_qa_path = output_dir / "render_qa.json"
        render_qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        return final_mp4, render_qa_path

    @staticmethod
    def _result(
        *,
        status: str,
        source_dir: Path,
        output_dir: Path,
        repaired_scene_paths: tuple[str, ...],
        source_short_dir: str | None,
        final_mp4: str | None,
        render_qa_path: str | None,
        brightness_factor: float,
        contrast_factor: float,
        sharpness_factor: float,
        notes: tuple[str, ...],
    ) -> VisualAssetPackBrightnessRepairResult:
        return VisualAssetPackBrightnessRepairResult(
            status=status,
            source_asset_pack_dir=str(source_dir),
            output_dir=str(output_dir),
            repaired_scene_paths=repaired_scene_paths,
            source_short_dir=source_short_dir,
            final_mp4=final_mp4,
            render_qa_path=render_qa_path,
            brightness_factor=brightness_factor,
            contrast_factor=contrast_factor,
            sharpness_factor=sharpness_factor,
            created_at=datetime.now(UTC).isoformat(),
            notes=notes,
        )

    @staticmethod
    def _write_result(result: VisualAssetPackBrightnessRepairResult, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "visual_asset_pack_brightness_repair_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "visual_asset_pack_brightness_repair_report.md").write_text(
            _render_report(result),
            encoding="utf-8",
        )


def _optional_path(value: object) -> Path | None:
    text = _optional_str(value)
    return Path(text).resolve() if text else None


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _render_report(result: VisualAssetPackBrightnessRepairResult) -> str:
    notes = "\n".join(f"- {item}" for item in result.notes) or "- none"
    scenes = "\n".join(f"- `{path}`" for path in result.repaired_scene_paths) or "- none"
    return "\n".join(
        [
            "# Visual Asset Pack Brightness Repair",
            "",
            f"- status: `{result.status}`",
            f"- final_mp4: `{result.final_mp4 or 'not_rendered'}`",
            f"- render_qa: `{result.render_qa_path or 'not_run'}`",
            f"- brightness_factor: `{result.brightness_factor}`",
            f"- contrast_factor: `{result.contrast_factor}`",
            f"- sharpness_factor: `{result.sharpness_factor}`",
            "",
            "## Repaired Scenes",
            scenes,
            "",
            "## Notes",
            notes,
            "",
            "## Safety",
            "- This pass edits only Atnos-generated assets.",
            "- Public Shorts were used only as format references.",
            "- Upload remains private/manual and approval-gated.",
            "",
        ]
    )
