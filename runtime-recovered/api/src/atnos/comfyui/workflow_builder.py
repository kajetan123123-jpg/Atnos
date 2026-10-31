from __future__ import annotations

import logging
import random
import textwrap
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class WorkflowBuilder:
    """Build ComfyUI API workflow dictionaries from Atnos scene plans."""

    def __init__(self, config: Any | None = None, *, base_url: str | None = None) -> None:
        self.config = config or {}
        self.base_url = (base_url or _config_value(config, "comfyui_base_url") or "http://127.0.0.1:8188").rstrip("/")

    def build_image_workflow(self, scene: dict[str, Any], output_filename: str) -> dict[str, Any]:
        """Build a static SD image workflow for a 1080x1920 Shorts frame."""
        checkpoint = (
            _config_value(self.config, "sd_model_checkpoint")
            or _config_value(self.config, "comfyui_default_checkpoint")
            or "v1-5-pruned-emaonly.ckpt"
        )
        style = ", ".join(str(item) for item in scene.get("style_keywords", []) if str(item).strip())
        prompt = str(scene.get("prompt") or scene.get("visual_prompt") or "").strip()
        if style:
            prompt = f"{prompt}, {style}"
        negative = (
            str(scene.get("negative_prompt") or "").strip()
            or str(_config_value(self.config, "comfyui_negative_prompt") or "").strip()
            or "low quality, blurry, watermark, text, logo, deformed, distorted"
        )
        width = int(_config_value(self.config, "comfyui_render_width") or 1080)
        height = int(_config_value(self.config, "comfyui_render_height") or 1920)
        return {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                    "seed": random.randint(1, 2**31 - 1),
                    "steps": int(_config_value(self.config, "comfyui_steps") or 20),
                    "cfg": float(_config_value(self.config, "comfyui_cfg_scale") or 7.0),
                    "sampler_name": str(_config_value(self.config, "comfyui_sampler_name") or "euler_ancestral"),
                    "scheduler": str(_config_value(self.config, "comfyui_scheduler") or "normal"),
                    "denoise": 1.0,
                },
            },
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": str(checkpoint)}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": output_filename}},
        }

    def build_video_workflow(self, scene: dict[str, Any], output_filename: str) -> dict[str, Any] | None:
        """Return a video workflow when known video nodes are available, otherwise None."""
        if _config_value(self.config, "enable_video_workflow") is False:
            return None
        object_info = self._object_info()
        class_names = set(object_info)
        if {"LTXVConditioning", "EmptyLTXVLatentVideo", "ModelSamplingLTXV"}.issubset(class_names):
            return self._build_ltx_workflow(scene, output_filename)
        if "AnimateDiffLoader" in class_names:
            logger.info("AnimateDiff is available, but no stable Atnos API workflow is configured yet.")
        if "LTXVVideoLoader" in class_names or "LTXVideoSampler" in class_names:
            logger.info("LTX video nodes are available, but exact installed graph is not recognized.")
        return None

    def _object_info(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/object_info")
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.debug("Could not load ComfyUI object_info: %s", exc)
            return {}

    def _build_ltx_workflow(self, scene: dict[str, Any], output_filename: str) -> dict[str, Any]:
        prompt = str(scene.get("prompt") or scene.get("visual_prompt") or "").strip()
        negative = str(scene.get("negative_prompt") or "watermark, text, low quality, blurry").strip()
        fps = 24
        frame_count = _ltx_frame_count(float(scene.get("duration_seconds") or scene.get("duration_s") or 1.25), fps=fps)
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn.safetensors", "type": "ltxv"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
            "5": {"class_type": "LTXVConditioning", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": fps}},
            "6": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": 512, "height": 896, "length": frame_count, "batch_size": 1}},
            "7": {"class_type": "ModelSamplingLTXV", "inputs": {"model": ["1", 0], "latent": ["6", 0], "max_shift": 2.05, "base_shift": 0.95}},
            "8": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["7", 0],
                    "positive": ["5", 0],
                    "negative": ["5", 1],
                    "latent_image": ["6", 0],
                    "seed": random.randint(1, 2**31 - 1),
                    "steps": 8,
                    "cfg": 3.5,
                    "sampler_name": "euler",
                    "scheduler": "beta",
                    "denoise": 1.0,
                },
            },
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
            "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": fps}},
            "11": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": output_filename, "format": "mp4", "codec": "h264"}},
        }


def generate_fallback_frames(scene: dict[str, Any], output_dir: Path, num_frames: int = 1) -> list[Path]:
    """Generate readable 1080x1920 placeholder frames through Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    color = _hex_to_rgb(str(scene.get("dominant_color") or scene.get("brand_color") or "#1a1a2e"))
    accent = _hex_to_rgb(str(scene.get("accent_color") or "#C2E5FF"))
    text = str(scene.get("hook_text") or scene.get("headline") or scene.get("prompt") or scene.get("visual_prompt") or "ATNOS SHORT")[:120]
    try:
        font = ImageFont.truetype("arialbd.ttf", 84)
        small = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
        small = ImageFont.load_default()

    for index in range(max(1, int(num_frames))):
        image = Image.new("RGB", (1080, 1920), color=color)
        draw = ImageDraw.Draw(image)
        for y in range(1920):
            ratio = y / 1919
            shade = tuple(max(0, min(255, int(channel * (1 - ratio * 0.45)))) for channel in color)
            draw.line((0, y, 1080, y), fill=shade)
        draw.rectangle((72, 132, 1008, 1788), outline=accent, width=6)
        draw.rectangle((0, 0, 1080, 22), fill=accent)
        wrapped = textwrap.fill(text, width=20)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=20)
        x = (1080 - (bbox[2] - bbox[0])) // 2
        y = (1920 - (bbox[3] - bbox[1])) // 2
        for dx, dy in ((4, 4), (3, 3), (2, 2)):
            draw.multiline_text((x + dx, y + dy), wrapped, fill=(0, 0, 0), font=font, spacing=20, align="center")
        draw.multiline_text((x, y), wrapped, fill=(255, 255, 255), font=font, spacing=20, align="center")
        draw.text((80, 1816), "ATNOS CONTENT MACHINE", fill=accent, font=small)
        path = output_dir / f"frame_{index:03d}.png"
        image.save(path, "PNG")
        frames.append(path)
    return frames


def _ltx_frame_count(duration_s: float, *, fps: int) -> int:
    target = max(25, int(round(min(duration_s, 4.0) * fps)))
    n = max(3, round((target - 1) / 8))
    return n * 8 + 1


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value or "#1a1a2e").strip().lstrip("#")
    if len(raw) != 6:
        raw = "1a1a2e"
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return (26, 26, 46)


def _config_value(config: Any, key: str) -> Any:
    if isinstance(config, dict):
        return config.get(key)
    return getattr(config, key, None)
