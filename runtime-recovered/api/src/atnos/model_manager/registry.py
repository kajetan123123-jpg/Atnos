from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atnos.core.paths import atnos_state_dir

if TYPE_CHECKING:
    from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    name: str
    category: str
    hf_repo: str
    hf_filename: str
    local_path: Path
    size_gb: float
    required: bool = True
    sha256: str | None = None
    min_vram_gb: float = 0.0
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "hf_repo": self.hf_repo,
            "hf_filename": self.hf_filename,
            "local_path": str(self.local_path),
            "size_gb": self.size_gb,
            "required": self.required,
            "sha256": self.sha256,
            "min_vram_gb": self.min_vram_gb,
            "tags": list(self.tags),
            "notes": self.notes,
        }


def resolve_comfyui_root(settings: "Settings | None") -> Path:
    """Resolve the actual ComfyUI application root.

    Portable Windows installs usually set COMFYUI_INSTALL_DIR to the wrapper
    directory, with main.py under COMFYUI_INSTALL_DIR/ComfyUI. Source checkouts
    often point directly at the directory containing main.py. The model manager
    and runtime launcher must agree on this path or models get downloaded to a
    folder ComfyUI never scans.
    """
    if settings is not None:
        install_dir = settings.comfyui_install_dir
        if install_dir is not None:
            direct = install_dir.resolve()
            nested = (direct / "ComfyUI").resolve()
            if (direct / "main.py").exists():
                return direct
            if (nested / "main.py").exists():
                return nested
            # Preserve the portable layout default even before ComfyUI exists.
            return nested
        default_wrapper = (settings.app_root.parent / "ComfyUI").resolve()
        direct_default = (settings.app_root.parent / "ComfyUI").resolve()
        nested_default = (default_wrapper / "ComfyUI").resolve()
        if (direct_default / "main.py").exists():
            return direct_default
        return nested_default
    env_path = os.getenv("COMFYUI_PATH")
    if env_path:
        direct = Path(env_path).expanduser().resolve()
        nested = (direct / "ComfyUI").resolve()
        if (direct / "main.py").exists():
            return direct
        if (nested / "main.py").exists():
            return nested
        return direct
    return (Path.home() / "ComfyUI").resolve()


def _resolve_comfyui_root(settings: "Settings | None") -> Path:
    return resolve_comfyui_root(settings)


def resolve_comfyui_models_root(settings: "Settings | None") -> Path:
    """Resolve the canonical shared model library used by ComfyUI and Atnos."""
    if settings is not None and settings.comfyui_models_dir is not None:
        return settings.comfyui_models_dir.expanduser().resolve()
    env_path = os.getenv("COMFYUI_MODELS_DIR")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (_resolve_comfyui_root(settings) / "models").resolve()


def resolve_comfyui_cold_models_root(settings: "Settings | None") -> Path | None:
    """Resolve optional cold storage scanned by ComfyUI for retained models."""
    if settings is not None and settings.comfyui_cold_models_dir is not None:
        return settings.comfyui_cold_models_dir.expanduser().resolve()
    env_path = os.getenv("COMFYUI_COLD_MODELS_DIR")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return None


def _prefer_existing_model(primary: Path, cold: Path | None) -> Path:
    if primary.exists() or cold is None or not cold.exists():
        return primary
    return cold


def _resolve_atnos_models_root(settings: "Settings | None") -> Path:
    if settings is not None:
        return (settings.data_dir / "models").resolve()
    return (atnos_state_dir() / "models").resolve()


def _default_registry(settings: "Settings | None" = None) -> dict[str, ModelSpec]:
    comfyui_models = resolve_comfyui_models_root(settings)
    cold_models = resolve_comfyui_cold_models_root(settings)
    atnos_models = _resolve_atnos_models_root(settings)
    checkpoints = comfyui_models / "checkpoints"
    vae = comfyui_models / "vae"
    clip = comfyui_models / "clip"
    upscale = comfyui_models / "upscale_models"
    cold_checkpoints = cold_models / "checkpoints" if cold_models is not None else None
    cold_vae = cold_models / "vae" if cold_models is not None else None
    cold_clip = cold_models / "clip" if cold_models is not None else None

    return {
        "sdxl_base": ModelSpec(
            id="sdxl_base",
            name="SDXL Base 1.0",
            category="sdxl",
            hf_repo="stabilityai/stable-diffusion-xl-base-1.0",
            hf_filename="sd_xl_base_1.0.safetensors",
            local_path=checkpoints / "sd_xl_base_1.0.safetensors",
            size_gb=6.9,
            required=False,
            min_vram_gb=8.0,
            tags=["shorts", "thumbnails", "keyframes"],
            notes="Optional raw SDXL checkpoint; the production pipeline uses sdxl_base_vae by default.",
        ),
        "sdxl_base_vae": ModelSpec(
            id="sdxl_base_vae",
            name="SDXL Base 1.0 with VAE",
            category="sdxl",
            hf_repo="stabilityai/stable-diffusion-xl-base-1.0",
            hf_filename="sd_xl_base_1.0_0.9vae.safetensors",
            local_path=checkpoints / "sd_xl_base_1.0_0.9vae.safetensors",
            size_gb=6.9,
            min_vram_gb=8.0,
            tags=["shorts", "thumbnails"],
            notes="Preferred SDXL checkpoint for color stability.",
        ),
        "sdxl_vae": ModelSpec(
            id="sdxl_vae",
            name="SDXL VAE fp16",
            category="vae",
            hf_repo="madebyollin/sdxl-vae-fp16-fix",
            hf_filename="sdxl_vae.safetensors",
            local_path=vae / "sdxl_vae.safetensors",
            size_gb=0.33,
            required=False,
            tags=["sdxl"],
        ),
        "ltx_video": ModelSpec(
            id="ltx_video",
            name="LTX-Video 2B v0.9.5",
            category="ltx",
            hf_repo="Lightricks/LTX-Video",
            hf_filename="ltx-video-2b-v0.9.5.safetensors",
            local_path=_prefer_existing_model(
                checkpoints / "ltx-video-2b-v0.9.5.safetensors",
                cold_checkpoints / "ltx-video-2b-v0.9.5.safetensors" if cold_checkpoints is not None else None,
            ),
            size_gb=5.905,
            min_vram_gb=10.0,
            tags=["shorts", "video", "ltx"],
        ),
        "ltx_vae": ModelSpec(
            id="ltx_vae",
            name="LTX-Video VAE",
            category="vae",
            hf_repo="Lightricks/LTX-Video",
            hf_filename="vae/diffusion_pytorch_model.safetensors",
            local_path=_prefer_existing_model(
                vae / "ltx-video-2b-v0.9.5_vae.safetensors",
                cold_vae / "ltx-video-2b-v0.9.5_vae.safetensors" if cold_vae is not None else None,
            ),
            size_gb=1.56,
            tags=["ltx"],
            notes="Stored under ComfyUI VAE name; source file lives in the LTX diffusers vae/ folder.",
        ),
        "t5_encoder": ModelSpec(
            id="t5_encoder",
            name="T5-XXL Encoder fp8",
            category="clip",
            hf_repo="fmoraes2k/t5xxl_fp8_e4m3fn.safetensors",
            hf_filename="t5xxl_fp8_e4m3fn.safetensors",
            local_path=_prefer_existing_model(
                clip / "t5xxl_fp8_e4m3fn.safetensors",
                cold_clip / "t5xxl_fp8_e4m3fn.safetensors" if cold_clip is not None else None,
            ),
            size_gb=4.558,
            min_vram_gb=6.0,
            tags=["ltx"],
            notes="Public ComfyUI-ready T5 fp8 text encoder artifact.",
        ),
        "wan_video_1_3b": ModelSpec(
            id="wan_video_1_3b",
            name="Wan2.1 T2V 1.3B",
            category="wan",
            hf_repo="Wan-AI/Wan2.1-T2V-1.3B",
            hf_filename="diffusion_pytorch_model.safetensors",
            local_path=checkpoints / "wan2.1-t2v-1.3b.safetensors",
            size_gb=5.286,
            required=False,
            min_vram_gb=8.5,
            tags=["shorts", "video", "wan"],
            notes="Fallback text-to-video model for ComfyUI/Wan workflows.",
        ),
        "kokoro_v1": ModelSpec(
            id="kokoro_v1",
            name="Kokoro TTS v1.0",
            category="tts",
            hf_repo="onnx-community/Kokoro-82M-v1.0-ONNX",
            hf_filename="onnx/model_q8f16.onnx",
            local_path=atnos_models / "tts" / "model_q8f16.onnx",
            size_gb=0.086,
            required=False,
            tags=["tts", "shorts"],
            notes="Quantized ONNX Kokoro model; requires kokoro-onnx-compatible runtime wiring.",
        ),
        "kokoro_voices_pl": ModelSpec(
            id="kokoro_voices_pl",
            name="Kokoro default voice embedding",
            category="tts",
            hf_repo="onnx-community/Kokoro-82M-v1.0-ONNX",
            hf_filename="voices/af_heart.bin",
            local_path=atnos_models / "tts" / "af_heart.bin",
            size_gb=0.0005,
            required=False,
            tags=["tts"],
            notes="Default voice embedding for smoke-ready local TTS setup.",
        ),
        "whisper_medium": ModelSpec(
            id="whisper_medium",
            name="Whisper Medium",
            category="stt",
            hf_repo="openai/whisper-medium",
            hf_filename="model.safetensors",
            local_path=atnos_models / "stt" / "whisper-medium" / "model.safetensors",
            size_gb=1.5,
            required=False,
            min_vram_gb=4.0,
            tags=["subtitles", "transcription"],
        ),
        "realesrgan_x4": ModelSpec(
            id="realesrgan_x4",
            name="RealESRGAN x4+",
            category="upscaler",
            hf_repo="ai-forever/Real-ESRGAN",
            hf_filename="RealESRGAN_x4plus.pth",
            local_path=upscale / "RealESRGAN_x4plus.pth",
            size_gb=0.064,
            required=False,
            tags=["thumbnails", "quality"],
        ),
    }


def build_model_registry(settings: "Settings | None" = None) -> dict[str, ModelSpec]:
    registry = _default_registry(settings)
    overrides_path = _user_registry_path(settings)
    if overrides_path.exists():
        registry = _merge_user_registry(registry, overrides_path)
    return registry


def get_model(model_id: str, settings: "Settings | None" = None) -> ModelSpec | None:
    return build_model_registry(settings).get(model_id)


def get_models_by_tag(tag: str, settings: "Settings | None" = None) -> list[ModelSpec]:
    return [spec for spec in build_model_registry(settings).values() if tag in spec.tags]


def get_required_models(settings: "Settings | None" = None) -> list[ModelSpec]:
    return [spec for spec in build_model_registry(settings).values() if spec.required]


def _user_registry_path(settings: "Settings | None") -> Path:
    if settings is not None:
        return (settings.app_root / "config" / "models_registry.json").resolve()
    return (Path.cwd() / "config" / "models_registry.json").resolve()


def _merge_user_registry(
    registry: dict[str, ModelSpec],
    path: Path,
) -> dict[str, ModelSpec]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return registry

    raw_models = payload.get("models", payload)
    if isinstance(raw_models, dict):
        entries = [{"id": model_id, **value} for model_id, value in raw_models.items() if isinstance(value, dict)]
    elif isinstance(raw_models, list):
        entries = [item for item in raw_models if isinstance(item, dict)]
    else:
        return registry

    merged = dict(registry)
    for entry in entries:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        base = merged.get(model_id)
        if base is None:
            spec = _spec_from_user_entry(entry)
        else:
            spec = _override_spec(base, entry)
        if spec is not None:
            merged[spec.id] = spec
    return merged


def _spec_from_user_entry(entry: dict[str, Any]) -> ModelSpec | None:
    required_fields = ("id", "name", "category", "hf_repo", "hf_filename", "local_path", "size_gb")
    if any(not entry.get(field) for field in required_fields):
        return None
    return ModelSpec(
        id=str(entry["id"]),
        name=str(entry["name"]),
        category=str(entry["category"]),
        hf_repo=str(entry["hf_repo"]),
        hf_filename=str(entry["hf_filename"]),
        local_path=Path(str(entry["local_path"])).expanduser().resolve(),
        size_gb=float(entry["size_gb"]),
        required=bool(entry.get("required", True)),
        sha256=str(entry["sha256"]) if entry.get("sha256") else None,
        min_vram_gb=float(entry.get("min_vram_gb") or 0.0),
        tags=[str(tag) for tag in entry.get("tags", []) if str(tag).strip()],
        notes=str(entry.get("notes") or ""),
    )


def _override_spec(base: ModelSpec, entry: dict[str, Any]) -> ModelSpec:
    updates: dict[str, Any] = {}
    for field_name in (
        "name",
        "category",
        "hf_repo",
        "hf_filename",
        "size_gb",
        "required",
        "sha256",
        "min_vram_gb",
        "tags",
        "notes",
    ):
        if field_name in entry:
            updates[field_name] = entry[field_name]
    if "local_path" in entry:
        updates["local_path"] = Path(str(entry["local_path"])).expanduser().resolve()
    if "tags" in updates:
        updates["tags"] = [str(tag) for tag in updates["tags"] if str(tag).strip()]
    if "size_gb" in updates:
        updates["size_gb"] = float(updates["size_gb"])
    if "min_vram_gb" in updates:
        updates["min_vram_gb"] = float(updates["min_vram_gb"])
    if "required" in updates:
        updates["required"] = bool(updates["required"])
    return replace(base, **updates)
