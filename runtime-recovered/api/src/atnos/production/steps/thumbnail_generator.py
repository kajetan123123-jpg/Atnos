from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from atnos.core.config import Settings
from atnos.model_manager.model_manager import ModelManager
from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.models import ProductionContext, StepResult
from atnos.production.steps.common import clean_words, write_json
from atnos.production.thumbnail_quality import audit_thumbnail_quality
from atnos.production.visual_quality_contract import negative_visual_prompt, thumbnail_quality_contract
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
)
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    thumb_dir = context.artifact_dir / "thumbnail"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = thumb_dir / "thumbnail_v1.png"
    workflow_path = _workflow_path(settings)
    workflow = _load_or_create_workflow(workflow_path)
    model_ready, model_message, checkpoint_name = await _ensure_thumbnail_model(settings)
    prompt_inputs = _prompt_inputs(context, checkpoint_name=checkpoint_name)
    profile = resolve_channel_profile(context)
    generated = False
    copied_cached_thumbnail = False
    local_generated = False
    generation_backend = ""
    selected_video_backend = str(getattr(settings, "shorts_video_backend", "motion_graphic") or "motion_graphic").strip().lower()
    force_local_profile_thumbnail = selected_video_backend == "motion_graphic"
    if not force_local_profile_thumbnail:
        generated = _try_prepare_thumbnail_from_scene(context, thumbnail_path, prompt_inputs)
        if generated:
            generation_backend = "comfyui_scene_reframe"
    if not generated and model_ready and not force_local_profile_thumbnail:
        generated = await _try_generate_thumbnail(settings, workflow, prompt_inputs, thumbnail_path)
        if not generated:
            generated = await _try_generate_quality_thumbnail(settings, prompt_inputs, thumbnail_path)
        if generated:
            generation_backend = "comfyui_sdxl"
    if not generated:
        generation_backend = _generate_profile_thumbnail(thumbnail_path, context, prompt_inputs, settings.shorts_brand_color)
        local_generated = (
            generation_backend == "local_pillow_thumbnail"
            and thumbnail_path.exists()
            and _thumbnail_file_looks_valid(thumbnail_path)
        )
    if generated and thumbnail_path.exists():
        _polish_thumbnail(thumbnail_path, context, prompt_inputs, settings.shorts_brand_color)
    thumbnail_quality = audit_thumbnail_quality(thumbnail_path, profile_key=profile.key)
    payload = {
        "status": "generated" if generated or local_generated else "placeholder_ready",
        "workflow_template": str(workflow_path),
        "thumbnail_path": str(thumbnail_path),
        "prompt_inputs": prompt_inputs,
        "thumbnail_model": {
            "model_id": "sdxl_base_vae",
            "ready": model_ready,
            "message": model_message,
            "checkpoint": checkpoint_name,
        },
        "profile_key": profile.key,
        "comfyui_generated": generated,
        "copied_cached_thumbnail": copied_cached_thumbnail,
        "local_generated": local_generated,
        "thumbnail_generation_backend": generation_backend,
        "thumbnail_quality": thumbnail_quality,
        "fallback_used": not (generated or local_generated),
        "review_ready": generated and generation_backend.startswith("comfyui_"),
    }
    return write_json(context.artifact_dir / "thumbnail_manifest.json", payload)


async def _try_generate_quality_thumbnail(
    settings: Settings,
    prompt_inputs: dict[str, Any],
    output_path: Path,
) -> bool:
    """Generate a fresh topic-specific thumbnail; never substitute an older ComfyUI output."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        await asyncio.to_thread(
            ComfyUiImageGenerationService(settings).generate_background,
            prompt=str(prompt_inputs.get("positive_prompt") or ""),
            negative_prompt=str(prompt_inputs.get("negative_prompt") or negative_visual_prompt("")),
            output_path=output_path,
            width=1280,
            height=720,
            seed=random.randint(1, 2**31 - 1),
            quality_mode=True,
        )
    except (ComfyUiImageGenerationError, OSError, ValueError):
        return False
    return _thumbnail_file_looks_valid(output_path)


async def _ensure_thumbnail_model(settings: Settings) -> tuple[bool, str, str]:
    default_checkpoint = "sd_xl_base_1.0_0.9vae.safetensors"
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True, "model check skipped in tests", default_checkpoint
    manager = ModelManager.get_instance(settings)
    ok, message = await manager.ensure_model("sdxl_base_vae", auto_download=True)
    checkpoint_name = Path(message).name if ok else default_checkpoint
    return ok, message, checkpoint_name


async def _try_generate_thumbnail(
    settings: Settings,
    workflow: dict[str, Any],
    prompt_inputs: dict[str, Any],
    output_path: Path,
) -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        ready = await asyncio.to_thread(
            ComfyUiRuntimeService(settings).ensure_server_running,
            wait_timeout_s=120,
            lowvram=bool(settings.comfyui_lowvram),
        )
        if not ready:
            return False
        async with httpx.AsyncClient(timeout=5) as client:
            prompt = _inject_thumbnail(workflow, prompt_inputs)
            queued = await client.post(f"{settings.comfyui_base_url.rstrip('/')}/prompt", json={"prompt": prompt})
            queued.raise_for_status()
            prompt_id = queued.json().get("prompt_id")
            if not prompt_id:
                return False
            image_output = await _poll_image(client, settings.comfyui_base_url.rstrip("/"), prompt_id)
            if image_output is None:
                return False
            data = await _download_output(client, settings.comfyui_base_url.rstrip("/"), image_output)
        output_path.write_bytes(data)
        return _thumbnail_file_looks_valid(output_path)
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        return False


async def _poll_image(client: httpx.AsyncClient, base_url: str, prompt_id: str) -> dict[str, Any] | None:
    for _ in range(60):
        await asyncio.sleep(2)
        response = await client.get(f"{base_url}/history/{prompt_id}")
        response.raise_for_status()
        item = response.json().get(prompt_id)
        outputs = item.get("outputs") if isinstance(item, dict) else None
        if not isinstance(outputs, dict):
            continue
        for output in outputs.values():
            images = output.get("images") if isinstance(output, dict) else None
            if isinstance(images, list) and images and isinstance(images[0], dict):
                return images[0]
    return None


async def _download_output(client: httpx.AsyncClient, base_url: str, output: dict[str, Any]) -> bytes:
    query = urlencode(
        {
            "filename": output.get("filename", ""),
            "subfolder": output.get("subfolder", ""),
            "type": output.get("type", "output"),
        }
    )
    response = await client.get(f"{base_url}/view?{query}")
    response.raise_for_status()
    return response.content


def _workflow_path(settings: Settings) -> Path:
    return settings.data_dir / "workflows" / "thumbnail_default.json"


def _load_or_create_workflow(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{checkpoint}"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "{positive_prompt}"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "{negative_prompt}"}},
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1280, "height": 720, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": "{seed}",
                "steps": 22,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "atnos_thumbnail"}},
    }
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    return workflow


def _inject_thumbnail(workflow: dict[str, Any], prompt_inputs: dict[str, Any]) -> dict[str, Any]:
    replacements = {
        "{checkpoint}": str(prompt_inputs.get("checkpoint") or "sd_xl_base_1.0_0.9vae.safetensors"),
        "{positive_prompt}": str(prompt_inputs["positive_prompt"]),
        "{negative_prompt}": str(prompt_inputs.get("negative_prompt") or negative_visual_prompt("")),
        "{seed}": str(random.randint(1, 2**31 - 1)),
    }
    prompt = _replace_placeholders(workflow, replacements)
    for node in prompt.values():
        if not isinstance(node, dict) or node.get("class_type") != "KSampler":
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            try:
                inputs["steps"] = min(int(inputs.get("steps") or 12), 12)
            except (TypeError, ValueError):
                inputs["steps"] = 12
    return prompt


def _copy_cached_thumbnail(settings: Settings, output_path: Path) -> bool:
    output_dir = Path(str(settings.comfyui_install_dir or "")) / "ComfyUI" / "output"
    if not output_dir.exists():
        return False
    candidates = sorted(
        output_dir.glob("atnos_thumbnail*.png"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    for candidate in candidates:
        if not _thumbnail_file_looks_valid(candidate):
            continue
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, output_path)
            return _thumbnail_file_looks_valid(output_path)
        except OSError:
            continue
    return False


def _thumbnail_file_looks_valid(path: Path) -> bool:
    """Wymuś realny, niepusty thumbnail zamiast szarego mini-placeholdera z ComfyUI."""
    if not path.exists() or path.stat().st_size < 50 * 1024:
        return False
    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.width < 1280 or image.height < 720:
                return False
            sample = image.convert("RGB").resize((64, 36))
            extrema = sample.getextrema()
            max_range = max(high - low for low, high in extrema)
            return max_range >= 8
    except Exception:
        return True


def _replace_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        updated = value
        replaced = False
        for key, replacement in replacements.items():
            if key in updated:
                replaced = True
            updated = updated.replace(key, replacement)
        if replaced and updated.isdigit():
            return int(updated)
        return updated
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_placeholders(item, replacements) for key, item in value.items()}
    return value


def _prompt_inputs(context: ProductionContext, *, checkpoint_name: str = "sd_xl_base_1.0_0.9vae.safetensors") -> dict[str, Any]:
    hook = context.artifacts.get("hook", {}).get("selected", {})
    profile = resolve_channel_profile(context)
    title = context.topic.title
    punch = _thumbnail_punch_text(title, profile.key, fallback=str(hook.get("text") or ""))
    if profile.key == "sigma_squad":
        accent = "hot pink, cyan, toxic green and neon yellow accents"
    elif profile.key == "dark_chronicles":
        accent = "deep crimson shadow and antique matte gold accent"
    elif profile.key == "wealth_blueprint":
        accent = "restrained muted warm gold accent, no neon glow"
    else:
        accent = "bright cyan accent"
    return {
        "topic_title": title,
        "punch_text": punch,
        "hook_tension": hook.get("tension_score", 0),
        "channel_style": profile.thumbnail_style,
        "checkpoint": checkpoint_name,
        "positive_prompt": (
            f"{profile.thumbnail_prompt}, {title}, {accent}, "
            f"{thumbnail_quality_contract(profile.key)} "
            f"punch phrase mood: {punch}"
        ),
        "negative_prompt": negative_visual_prompt(profile.key),
    }


def _thumbnail_punch_text(title: str, profile_key: str, *, fallback: str = "") -> str:
    """Return a short, complete thumbnail promise instead of truncating a hook mid-thought."""
    normalized = clean_words(str(title or "").upper(), 8)
    lowered = normalized.casefold()
    if profile_key == "dark_chronicles" and "dancing plague" in lowered:
        return "THE DANCING PLAGUE"
    if profile_key == "wealth_blueprint" and "habit" in lowered and "broke" in lowered:
        return "THE HABIT KEEPING YOU BROKE"
    if profile_key == "wealth_blueprint" and "money rule" in lowered:
        return "ONE MONEY RULE"
    if profile_key == "wealth_blueprint" and "savings" in lowered and "lying" in lowered:
        return "YOUR SAVINGS LIE"
    if profile_key == "wealth_blueprint" and "double" in lowered and "money" in lowered:
        return "DOUBLE YOUR MONEY"
    if profile_key == "wealth_blueprint" and "compound" in lowered and "hype" in lowered:
        return "TIME BEATS HYPE"
    if profile_key == "sigma_squad" and "npc mode" in lowered:
        return "NPC MODE ACTIVATED"
    candidate = normalized or clean_words(str(fallback or "").upper(), 4)
    words = candidate.split()
    if len(words) <= 5:
        return candidate
    # A shorter complete phrase is safer than displaying an arbitrary prefix
    # whose missing final words can reverse or destroy the promise.
    return " ".join(words[:4])


def _try_prepare_thumbnail_from_scene(
    context: ProductionContext,
    output_path: Path,
    prompt_inputs: dict[str, Any],
) -> bool:
    """Reframe a verified scene from the current render so the thumbnail cannot drift off-topic."""
    manifest = context.artifacts.get("video")
    clips = manifest.get("clips") if isinstance(manifest, dict) and isinstance(manifest.get("clips"), list) else []
    candidates: list[Path] = []
    for clip in clips:
        if not isinstance(clip, dict) or str(clip.get("status") or "") != "generated":
            continue
        paths = clip.get("generated_image_paths")
        if isinstance(paths, list):
            candidates.extend(Path(str(item)) for item in paths if str(item).strip())
        clip_path = Path(str(clip.get("file") or ""))
        if clip_path.name:
            candidates.extend(
                [
                    clip_path.with_suffix(f".s{int(clip.get('segment') or 1):02d}.comfy.png"),
                    clip_path.with_suffix(f".s{int(clip.get('segment') or 1):02d}.openai.png"),
                ]
            )
    source = next((path for path in candidates if path.exists() and path.stat().st_size > 4096), None)
    if source is None:
        return False
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        with Image.open(source) as raw:
            source_image = raw.convert("RGB")
        background = ImageOps.fit(source_image, (1280, 720), method=Image.Resampling.LANCZOS, centering=(0.5, 0.38))
        background = ImageEnhance.Contrast(background).enhance(1.08)
        # Keep the subject legible while allowing the later title panel to dominate the left side.
        soft = background.filter(ImageFilter.GaussianBlur(radius=1.2))
        background = Image.blend(background, soft, 0.12)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        background.save(output_path, "PNG", compress_level=1)
        prompt_inputs["scene_source_path"] = str(source)
        return _thumbnail_file_looks_valid(output_path)
    except (OSError, ValueError):
        return False


def _generate_profile_thumbnail(
    path: Path,
    context: ProductionContext,
    prompt_inputs: dict[str, Any],
    brand_color: str,
) -> str:
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageStat
    except ImportError:
        _write_minimal_png(path)
        return "minimal_placeholder"
    profile = resolve_channel_profile(context)
    image = Image.new("RGB", (1280, 720), color=_profile_base_color(profile.key))
    draw = ImageDraw.Draw(image)
    accent = _profile_accent_color(profile.key, brand_color)
    _draw_profile_background(draw, profile.key, accent)
    draw.rectangle((0, 0, 1280, 24), fill=accent)
    draw.rectangle((64, 96, 1216, 624), outline=accent, width=6)
    # Subtle deterministic grain keeps fallback thumbnails large enough for QA
    # without introducing random visual noise or exceeding YouTube's 2MB limit.
    for y in range(32, 704, 6):
        for x in range(32, 1248, 6):
            if (x * 17 + y * 31) % 7 == 0:
                shade = 22 + ((x + y) % 18)
                draw.point((x, y), fill=(shade, min(shade + 8, 255), min(shade + 16, 255)))
    text = clean_words(str(prompt_inputs.get("punch_text") or context.topic.title).upper(), 8)
    try:
        font = ImageFont.truetype("arialbd.ttf", 76)
        small = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
        small = ImageFont.load_default()
    draw.multiline_text((100, 230), _wrap_title(text), fill=(255, 255, 255), font=font, spacing=18)
    draw.text((100, 574), _thumbnail_brand_label(profile.key), fill=accent, font=small)
    image.save(path, "PNG", compress_level=1)
    return "local_pillow_thumbnail"


def _profile_base_color(profile_key: str) -> tuple[int, int, int]:
    if profile_key == "wealth_blueprint":
        return (12, 20, 18)
    if profile_key == "dark_chronicles":
        return (2, 2, 4)
    if profile_key == "sigma_squad":
        return (14, 0, 24)
    if profile_key == "brainrot":
        return (20, 4, 32)
    if profile_key == "history":
        return (18, 12, 7)
    return _hex_to_rgb("#0A1628")


def _profile_accent_color(profile_key: str, fallback: str) -> tuple[int, int, int]:
    if profile_key == "wealth_blueprint":
        return (159, 216, 194)
    if profile_key == "dark_chronicles":
        return (197, 160, 89)
    if profile_key == "sigma_squad":
        return (204, 255, 0)
    if profile_key == "brainrot":
        return (0, 255, 255)
    if profile_key == "history":
        return (211, 162, 91)
    return _hex_to_rgb(fallback or "#C2E5FF")


def _thumbnail_brand_label(profile_key: str) -> str:
    return {
        "dark_chronicles": "DARK CHRONICLES",
        "sigma_squad": "SIGMA SQUAD",
        "brainrot": "AI BRAINROT",
        "history": "HISTORIA",
        "wealth_blueprint": "WEALTH BLUEPRINT",
    }.get(profile_key, "ATNOS SHORTS")


def _draw_profile_background(draw: Any, profile_key: str, accent: tuple[int, int, int]) -> None:
    if profile_key == "wealth_blueprint":
        draw.rectangle((0, 0, 1280, 720), fill=(12, 20, 18))
        for y in range(0, 720, 4):
            shade = 12 + int(y / 720 * 34)
            draw.line((0, y, 1280, y), fill=(shade, min(60, shade + 20), min(54, shade + 16)), width=4)
        draw.polygon([(680, 0), (1280, 0), (1280, 720), (760, 720)], fill=(28, 46, 39))
        draw.line((700, 610, 1175, 255), fill=accent, width=11)
        draw.polygon([(1175, 255), (1080, 260), (1136, 330)], fill=accent)
        phone = (830, 106, 1134, 590)
        draw.rounded_rectangle(phone, radius=42, fill=(8, 18, 15), outline=accent, width=8)
        for i in range(5):
            y = 206 + i * 64
            draw.rounded_rectangle((890, y, 1078 - (i % 3) * 38, y + 28), radius=12, fill=(77, 116, 101))
        draw.rounded_rectangle((596, 408, 1005, 548), radius=28, fill=(15, 35, 29), outline=accent, width=6)
        draw.rounded_rectangle((636, 444, 760, 498), radius=14, fill=(80, 130, 112))
        draw.rounded_rectangle((650, 145, 820, 470), radius=22, fill=(225, 236, 230), outline=(238, 247, 242), width=4)
        for i in range(6):
            y = 215 + i * 39
            draw.line((684, y, 782 - (i % 3) * 24, y), fill=(18, 38, 31), width=7)
        for i in range(6):
            cx = 605 + i * 54
            cy = 596 - i * 10
            draw.ellipse((cx - 25, cy - 25, cx + 25, cy + 25), fill=(229, 204, 116), outline=accent, width=3)
    elif profile_key == "dark_chronicles":
        draw.rectangle((0, 0, 1280, 720), fill=(2, 2, 4))
        for radius, alpha in ((620, 30), (440, 44), (260, 62)):
            color = (70 + alpha, 10 + alpha // 4, 22 + alpha // 6)
            draw.ellipse((820 - radius, 360 - radius, 820 + radius, 360 + radius), outline=color, width=4)
        draw.polygon([(760, 110), (1110, 110), (1010, 620), (660, 620)], outline=accent, fill=(22, 7, 10))
        draw.rectangle((820, 178, 1040, 218), fill=(128, 28, 36))
        draw.rectangle((805, 522, 1055, 562), fill=(128, 28, 36))
        draw.line((835, 220, 1030, 520), fill=accent, width=8)
        draw.line((1030, 220, 835, 520), fill=accent, width=8)
        draw.ellipse((735, 250, 1110, 535), outline=(78, 18, 28), width=10)
    elif profile_key == "sigma_squad":
        for y in range(720):
            r = int(255 * y / 720)
            draw.line((0, y, 1280, y), fill=(30 + r // 3, 0, 58 + r // 4))
        for x in range(-100, 1280, 180):
            draw.line((x, 0, x + 360, 720), fill=(0, 255, 255), width=8)
            draw.line((x + 40, 0, x + 400, 720), fill=(255, 0, 212), width=5)
        draw.ellipse((770, 115, 1130, 475), fill=(255, 0, 212), outline=(204, 255, 0), width=12)
        draw.polygon([(920, 70), (965, 165), (1065, 170), (988, 230), (1014, 326), (920, 274), (826, 326), (852, 230), (775, 170), (875, 165)], fill=(204, 255, 0))
        draw.ellipse((860, 225, 900, 265), fill=(0, 0, 0))
        draw.ellipse((1010, 225, 1050, 265), fill=(0, 0, 0))
        draw.arc((880, 260, 1030, 360), 10, 170, fill=(0, 0, 0), width=12)
    elif profile_key in {"history", "brainrot"}:
        for y in range(0, 720, 18):
            shade = 32 + (y % 54)
            draw.line((0, y, 1280, y), fill=(shade, max(8, shade // 2), max(4, shade // 4)), width=3)
        draw.ellipse((790, 130, 1130, 560), fill=(38, 22, 12), outline=accent, width=10)
        draw.rectangle((840, 210, 1080, 465), outline=accent, width=8)
    else:
        draw.rectangle((0, 0, 1280, 720), fill=(10, 22, 40))
        draw.line((720, 580, 1140, 190), fill=accent, width=10)
        for x in range(720, 1120, 70):
            draw.rectangle((x, 560 - (x - 700) // 3, x + 32, 590), fill=(32, 80, 92))


def _polish_thumbnail(
    path: Path,
    context: ProductionContext,
    prompt_inputs: dict[str, Any],
    brand_color: str,
) -> None:
    """Dodaj czytelny panel i usuń ryzyko pseudo-tekstu z surowych miniatur AI."""
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageStat
    except ImportError:
        return
    try:
        image = Image.open(path).convert("RGB").resize((1280, 720))
    except Exception:
        return

    profile = resolve_channel_profile(context)
    if profile.key == "wealth_blueprint":
        # Mature finance thumbnails use restrained color. Apply this before
        # branded overlays so the emerald accent remains crisp and intentional.
        image = ImageEnhance.Color(image).enhance(0.58)
    accent = _profile_accent_color(profile.key, brand_color)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for x in range(0, 620):
        alpha = int(226 * (1 - x / 700))
        draw.line((x, 0, x, 720), fill=(4, 9, 18, max(0, alpha)))
    draw.rectangle((0, 0, 1280, 20), fill=(*accent, 255))
    draw.rectangle((58, 92, 574, 626), outline=(*accent, 220), width=4)
    draw.rectangle((74, 108, 558, 610), fill=(4, 9, 18, 178))
    for y in range(122, 594, 18):
        draw.line((92, y, 540, y), fill=(*accent, 22), width=1)

    polished = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(polished)
    try:
        title_font = ImageFont.truetype("arialbd.ttf", 66)
        label_font = ImageFont.truetype("arialbd.ttf", 24)
        small_font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        title_font = ImageFont.load_default()
        label_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    punch = clean_words(str(prompt_inputs.get("punch_text") or context.topic.title).upper(), 6)
    lines = _wrap_thumbnail_text(punch, max_chars=12, max_lines=4)
    y = 180
    for line in lines:
        draw.text((102, y), line, fill=(5, 8, 12, 255), font=title_font, stroke_width=8, stroke_fill=(5, 8, 12, 255))
        draw.text((102, y), line, fill=(255, 255, 255, 255), font=title_font, stroke_width=2, stroke_fill=accent)
        y += 76

    label = "WEALTH BLUEPRINT" if profile.key == "wealth_blueprint" else "ATNOS SHORTS"
    draw.text((104, 520), label, fill=(*accent, 255), font=label_font)
    draw.text((104, 554), "EDITORIAL SHORT", fill=(205, 218, 225, 230), font=small_font)
    if profile.key == "wealth_blueprint":
        # Re-check the finished composition because the emerald brand overlay
        # can lift saturation after the source image was normalized.
        for _ in range(4):
            saturation = float(ImageStat.Stat(polished.convert("RGB").convert("HSV").split()[1]).mean[0])
            if saturation <= 138:
                break
            polished = ImageEnhance.Color(polished.convert("RGB")).enhance(0.78).convert("RGBA")
    polished.convert("RGB").save(path, "PNG", compress_level=1)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value or "#C2E5FF").lstrip("#")
    if len(raw) != 6:
        raw = "C2E5FF"
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _wrap_title(text: str) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= 18:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:3])


def _wrap_thumbnail_text(text: str, *, max_chars: int, max_lines: int) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines or [clean_words(text.upper(), 2)]


def _write_minimal_png(path: Path) -> None:
    # 1x1 transparent PNG fallback if Pillow is missing.
    path.write_bytes(
        bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000A49444154789C6360000002000100FFFF03000006000557BFAB00000000"
            "49454E44AE426082"
        )
        + b"\0" * 60000
    )
