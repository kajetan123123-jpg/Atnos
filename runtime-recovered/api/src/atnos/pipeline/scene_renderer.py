from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from atnos.comfyui.client import ComfyUIClient
from atnos.comfyui.workflow_builder import WorkflowBuilder, generate_fallback_frames

logger = logging.getLogger(__name__)

PipelineEventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class SceneResult:
    scene_id: str
    frames: list[Path]
    duration_seconds: float
    method: str
    video_path: Path | None = None


class SceneRenderer:
    """Render scenes from asset_plan.json into local frame/video assets."""

    def __init__(
        self,
        comfyui_client: ComfyUIClient | None,
        workflow_builder: WorkflowBuilder,
        config: Any,
        *,
        event_sink: PipelineEventSink | None = None,
    ) -> None:
        self.comfyui_client = comfyui_client
        self.workflow_builder = workflow_builder
        self.config = config
        self.event_sink = event_sink

    def render_all_scenes(self, asset_plan: dict[str, Any], job_dir: Path) -> list[SceneResult]:
        scenes = asset_plan.get("scenes") if isinstance(asset_plan.get("scenes"), list) else []
        if not scenes:
            scenes = [{"scene_id": 0, "duration_s": 4, "headline": "START", "visual_prompt": "cinematic dark intro"}]
        results: list[SceneResult] = []
        total = len(scenes)
        for index, scene in enumerate(scenes, start=1):
            scene_dir = job_dir / "frames" / f"scene_{index:02d}"
            scene_dir.mkdir(parents=True, exist_ok=True)
            result = self._render_scene(scene, scene_dir, index=index)
            results.append(result)
            self._emit(
                {
                    "type": "pipeline_progress",
                    "stage": "visuals",
                    "scene": index,
                    "total": total,
                    "method": result.method,
                }
            )
        return results

    def _render_scene(self, scene: dict[str, Any], scene_dir: Path, *, index: int) -> SceneResult:
        duration = float(scene.get("duration_seconds") or scene.get("duration_s") or 4.0)
        scene_id = str(scene.get("scene_id") or index - 1)
        if self.comfyui_client is not None and self.comfyui_client.is_alive():
            try:
                video_workflow = self.workflow_builder.build_video_workflow(scene, f"atnos_scene_{index:02d}")
                if video_workflow is not None:
                    prompt_id = self.comfyui_client.queue_prompt(video_workflow)
                    outputs = self.comfyui_client.download_outputs(prompt_id, scene_dir)
                    video_outputs = [path for path in outputs if path.suffix.lower() in {".mp4", ".webm", ".mov", ".gif"}]
                    if video_outputs:
                        return SceneResult(
                            scene_id=scene_id,
                            frames=[],
                            duration_seconds=duration,
                            method="comfyui_video",
                            video_path=video_outputs[0],
                        )
                    image_outputs = [path for path in outputs if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
                    if image_outputs:
                        return SceneResult(scene_id, image_outputs, duration, "comfyui_image")
            except Exception as exc:
                logger.warning("ComfyUI video workflow failed for scene %s: %s", scene_id, exc)

            try:
                workflow = self.workflow_builder.build_image_workflow(scene, f"atnos_scene_{index:02d}")
                prompt_id = self.comfyui_client.queue_prompt(workflow)
                outputs = self.comfyui_client.download_outputs(prompt_id, scene_dir)
                frames = [path for path in outputs if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
                if frames:
                    return SceneResult(scene_id, frames, duration, "comfyui_image")
            except Exception as exc:
                logger.warning("ComfyUI image workflow failed for scene %s: %s", scene_id, exc)

        frames = generate_fallback_frames(
            {
                **scene,
                "prompt": scene.get("prompt") or scene.get("visual_prompt") or "",
                "brand_color": _config_value(self.config, "shorts_brand_color") or "#C2E5FF",
                "dominant_color": _config_value(self.config, "shorts_brand_color_dark") or "#1a1a2e",
            },
            scene_dir,
            num_frames=1,
        )
        return SceneResult(scene_id, frames, duration, "pil_fallback")

    def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is None:
            logger.info("Pipeline event: %s", event)
            return
        try:
            self.event_sink(event)
        except Exception as exc:
            logger.debug("Pipeline event sink failed: %s", exc)


def _config_value(config: Any, key: str) -> Any:
    if isinstance(config, dict):
        return config.get(key)
    return getattr(config, key, None)
