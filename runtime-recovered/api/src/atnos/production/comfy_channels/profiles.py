"""Load / validate three Comfy channel profiles against shared models root."""

from __future__ import annotations

import json
from pathlib import Path

from atnos.core.config import Settings
from atnos.production.comfy_channels.models import (
    ALIAS_TO_CANONICAL,
    CHANNEL_IDS,
    ChannelComfyProfile,
    ChannelId,
    LEGACY_PROJECT_KEYS,
    WorkflowSpec,
)

_WB_WORKFLOWS = [
    WorkflowSpec(
        workflow_id="wealth_character_v1",
        workflow_version="v1",
        title="WB_CHARACTER",
        asset_type="character",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1024,
        default_height=1024,
        require_references=True,
        allow_transparent_bg=True,
        description="Alex and character poses for Remotion stills",
    ),
    WorkflowSpec(
        workflow_id="wealth_object_v1",
        workflow_version="v1",
        title="WB_OBJECT",
        asset_type="object",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1024,
        default_height=1024,
        allow_transparent_bg=True,
        description="Finance objects / icons without text",
    ),
    WorkflowSpec(
        workflow_id="wealth_editorial_scene_v1",
        workflow_version="v1",
        title="WB_EDITORIAL_SCENE",
        asset_type="editorial_scene",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1024,
        default_height=1024,
        description="Symbolic editorial finance scenes (no charts/numbers)",
    ),
    WorkflowSpec(
        workflow_id="wealth_background_v1",
        workflow_version="v1",
        title="WB_BACKGROUND",
        asset_type="background",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1080,
        default_height=1920,
        description="Clean vertical backgrounds",
    ),
    WorkflowSpec(
        workflow_id="wealth_icon_or_symbol_v1",
        workflow_version="v1",
        title="WB_ICON_OR_SYMBOL",
        asset_type="icon",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=768,
        default_height=768,
        allow_transparent_bg=True,
        description="Simple finance symbols",
    ),
    WorkflowSpec(
        workflow_id="wealth_reference_variation_v1",
        workflow_version="v1",
        title="WB_REFERENCE_VARIATION",
        asset_type="character",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1024,
        default_height=1024,
        require_references=True,
        description="Controlled Alex variation from anchors",
    ),
]

_WC_WORKFLOWS = [
    WorkflowSpec(
        workflow_id="wonder_character_reference_v1",
        workflow_version="v1",
        title="CHARACTER_REFERENCE",
        asset_type="character_reference",
        backend_preference=["checkpoint", "z_image_turbo", "flux2_klein"],
        default_width=1024,
        default_height=1024,
        require_references=True,
        description="Niko/Pif/Tika/Mira reference for 3D pipeline",
    ),
    WorkflowSpec(
        workflow_id="wonder_character_expression_v1",
        workflow_version="v1",
        title="CHARACTER_EXPRESSION_SHEET",
        asset_type="expression",
        backend_preference=["checkpoint", "z_image_turbo"],
        default_width=1024,
        default_height=1024,
        require_references=True,
        description="Expression concept sheets",
    ),
    WorkflowSpec(
        workflow_id="wonder_character_pose_v1",
        workflow_version="v1",
        title="CHARACTER_POSE_REFERENCE",
        asset_type="pose",
        backend_preference=["checkpoint", "z_image_turbo"],
        default_width=1024,
        default_height=1024,
        require_references=True,
    ),
    WorkflowSpec(
        workflow_id="wonder_costume_concept_v1",
        workflow_version="v1",
        title="COSTUME_CONCEPT",
        asset_type="costume",
        backend_preference=["checkpoint", "z_image_turbo"],
        default_width=1024,
        default_height=1024,
    ),
    WorkflowSpec(
        workflow_id="wonder_environment_concept_v1",
        workflow_version="v1",
        title="ENVIRONMENT_CONCEPT",
        asset_type="environment",
        backend_preference=["checkpoint", "z_image_turbo", "flux2_klein"],
        default_width=1344,
        default_height=768,
        description="Magical island / world concepts for Blender",
    ),
    WorkflowSpec(
        workflow_id="wonder_island_concept_v1",
        workflow_version="v1",
        title="ISLAND_CONCEPT",
        asset_type="island",
        backend_preference=["checkpoint", "z_image_turbo"],
        default_width=1344,
        default_height=768,
    ),
    WorkflowSpec(
        workflow_id="wonder_prop_concept_v1",
        workflow_version="v1",
        title="PROP_CONCEPT",
        asset_type="prop",
        backend_preference=["checkpoint", "z_image_turbo"],
        default_width=1024,
        default_height=1024,
    ),
    WorkflowSpec(
        workflow_id="wonder_storyboard_frame_v1",
        workflow_version="v1",
        title="STORYBOARD_FRAME",
        asset_type="storyboard",
        backend_preference=["checkpoint", "z_image_turbo"],
        default_width=1280,
        default_height=720,
    ),
]

_DC_WORKFLOWS = [
    WorkflowSpec(
        workflow_id="dark_establishing_shot_v1",
        workflow_version="v1",
        title="DC_ESTABLISHING_SHOT",
        asset_type="establishing",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1344,
        default_height=768,
    ),
    WorkflowSpec(
        workflow_id="dark_location_v1",
        workflow_version="v1",
        title="DC_LOCATION",
        asset_type="location",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1344,
        default_height=768,
    ),
    WorkflowSpec(
        workflow_id="dark_character_reference_v1",
        workflow_version="v1",
        title="DC_CHARACTER_REFERENCE",
        asset_type="character",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1024,
        default_height=1024,
    ),
    WorkflowSpec(
        workflow_id="dark_object_v1",
        workflow_version="v1",
        title="DC_OBJECT",
        asset_type="object",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1024,
        default_height=1024,
    ),
    WorkflowSpec(
        workflow_id="dark_horror_event_v1",
        workflow_version="v1",
        title="DC_HORROR_EVENT",
        asset_type="event",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1344,
        default_height=768,
    ),
    WorkflowSpec(
        workflow_id="dark_storyboard_frame_v1",
        workflow_version="v1",
        title="DC_STORYBOARD_FRAME",
        asset_type="storyboard",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1280,
        default_height=720,
    ),
    WorkflowSpec(
        workflow_id="dark_atmosphere_v1",
        workflow_version="v1",
        title="DC_ATMOSPHERE",
        asset_type="atmosphere",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1344,
        default_height=768,
    ),
    WorkflowSpec(
        workflow_id="dark_thumbnail_concept_v1",
        workflow_version="v1",
        title="DC_THUMBNAIL_CONCEPT",
        asset_type="thumbnail",
        backend_preference=["flux2_klein", "checkpoint"],
        default_width=1280,
        default_height=720,
    ),
]


def config_comfy_channels_root(settings: Settings | None = None) -> Path:
    if settings and settings.app_root:
        return Path(settings.app_root) / "config" / "comfy_channels"
    return Path(__file__).resolve().parents[5] / "config" / "comfy_channels"


def projects_root(settings: Settings) -> Path:
    tools = settings.atnos_tools_dir or Path(r"E:\Atnos\Tools")
    return Path(tools) / "Comfy-Desktop" / "ComfyUI-Projects"


def shared_models_root(settings: Settings) -> Path:
    if settings.comfyui_models_dir:
        return Path(settings.comfyui_models_dir)
    tools = settings.atnos_tools_dir or Path(r"E:\Atnos\Tools")
    return Path(tools) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"


def generated_root(settings: Settings) -> Path:
    # Prefer APP data on E:
    if settings.app_root:
        return Path(settings.app_root) / "data" / "generated"
    return Path(settings.data_dir) / "generated"


def resolve_channel_id(value: str) -> ChannelId:
    key = str(value).strip().casefold().replace(" ", "_")
    if key not in ALIAS_TO_CANONICAL:
        raise ValueError(f"Unknown channel: {value}")
    return ALIAS_TO_CANONICAL[key]


def _refs_for(channel: ChannelId, settings: Settings) -> list[str]:
    root = Path(settings.app_root) / "config" / "channel_references" / "20260916" if settings.app_root else None
    if root is None or not root.is_dir():
        root = Path(r"E:\Atnos\App\config\channel_references\20260916")
    crops = root / "crops"
    # Prefer single-portrait crops for img2img conditioning; keep full sheets as secondary anchors.
    mapping: dict[str, list[str]] = {
        "wealth_blueprint": [
            "crops/alex_portrait_v1.png",
            "crops/alex_front_v1.png",
            "image-1.png",
            "image-2.png",
        ],
        "wonder_crew": [
            "crops/niko_portrait_v1.png",
            "crops/niko_front_v1.png",
            "image-3.png",
            "image-4.png",
            "image-5.png",
            "image-6.png",
            "archipelag-snow-world.png",
        ],
        "dark_chronicles": [
            "crops/dc_env_mood_anchor_v1.png",
        ],
    }
    out: list[str] = []
    for name in mapping[channel]:
        path = (root / name).resolve()
        if path.is_file():
            out.append(str(path))
    # Ensure crops exist when sheets are present
    if channel in {"wealth_blueprint", "wonder_crew"} and not any("portrait_v1" in p for p in out):
        try:
            from atnos.production.comfy_channels.reference_preprocess import build_portrait_crops

            build_portrait_crops()
            for name in mapping[channel]:
                path = (root / name).resolve()
                if path.is_file() and str(path) not in out:
                    out.append(str(path))
        except Exception:
            pass
    return out


def build_builtin_profiles(settings: Settings) -> dict[ChannelId, ChannelComfyProfile]:
    proj = projects_root(settings)
    wb_dir = proj / "wealth_blueprint"
    wc_dir = proj / "wonder_crew"
    # Prefer dedicated wonder_crew dir; fall back to children_stories
    if not wc_dir.is_dir():
        wc_dir = proj / "children_stories"
    dc_dir = proj / "dark_chronicles"
    if not dc_dir.is_dir():
        dc_dir = proj / "horror_stories"

    models_wb = [
        "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
        "checkpoints/sd_xl_base_1.0_0.9vae.safetensors",
    ]
    return {
        "wealth_blueprint": ChannelComfyProfile(
            channel_id="wealth_blueprint",
            display_name="Wealth Blueprint",
            legacy_project_key="wealth_blueprint",
            comfy_project_dir=str(wb_dir),
            role="2D motion-design reusable assets for Remotion (no AI charts/text)",
            style_summary="premium editorial finance illustration",
            primary_characters=["alex"],
            reference_files=_refs_for("wealth_blueprint", settings),
            image_models=models_wb,
            image_fallback="checkpoints/sd_xl_base_1.0_0.9vae.safetensors",
            workflows=_WB_WORKFLOWS,
            output_subdir="wealth_blueprint",
            negative_profile_id="wealth_blueprint",
            prompt_profile_id="wealth_blueprint",
        ),
        "wonder_crew": ChannelComfyProfile(
            channel_id="wonder_crew",
            display_name="Wonder Crew",
            legacy_project_key="children_stories",
            comfy_project_dir=str(wc_dir),
            role="3D pipeline concept/reference art (not final 3D renders)",
            style_summary="stylized children's feature concept art",
            primary_characters=["niko", "pif", "tika", "mira"],
            reference_files=_refs_for("wonder_crew", settings),
            image_models=["checkpoints/sd_xl_turbo_1.0_fp16.safetensors"],
            image_fallback="checkpoints/sd_xl_base_1.0_0.9vae.safetensors",
            workflows=_WC_WORKFLOWS,
            output_subdir="wonder_crew",
            negative_profile_id="wonder_crew",
            prompt_profile_id="wonder_crew",
        ),
        "dark_chronicles": ChannelComfyProfile(
            channel_id="dark_chronicles",
            display_name="Dark Chronicles",
            legacy_project_key="horror_stories",
            comfy_project_dir=str(dc_dir),
            role="cinematic horror concepts, locations, storyboards",
            style_summary="atmospheric cinematic horror",
            primary_characters=[],
            reference_files=_refs_for("dark_chronicles", settings),
            image_models=[
                "checkpoints/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
                "checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
            ],
            image_fallback="checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
            workflows=_DC_WORKFLOWS,
            output_subdir="dark_chronicles",
            negative_profile_id="dark_chronicles",
            prompt_profile_id="dark_chronicles",
        ),
    }


def load_profile(settings: Settings, channel: str) -> ChannelComfyProfile:
    channel_id = resolve_channel_id(channel)
    # Optional on-disk override
    override = config_comfy_channels_root(settings) / channel_id / "profile.json"
    if override.is_file():
        payload = json.loads(override.read_text(encoding="utf-8"))
        return ChannelComfyProfile.model_validate(payload)
    return build_builtin_profiles(settings)[channel_id]


def list_profiles(settings: Settings) -> dict[ChannelId, ChannelComfyProfile]:
    return {cid: load_profile(settings, cid) for cid in CHANNEL_IDS}


def find_workflow(profile: ChannelComfyProfile, workflow: str) -> WorkflowSpec:
    key = workflow.strip()
    for item in profile.workflows:
        if item.workflow_id == key or item.title == key or item.workflow_id.replace("_", "").lower() == key.replace("_", "").lower():
            return item
    # fuzzy title without version
    lowered = key.casefold()
    for item in profile.workflows:
        if item.title.casefold() == lowered or item.asset_type.casefold() == lowered:
            return item
    raise ValueError(f"Workflow '{workflow}' is not allowed for channel {profile.channel_id}")


def persist_builtin_profiles(settings: Settings) -> list[Path]:
    """Write profile.json + workflow index into App/config/comfy_channels (small JSON only)."""
    written: list[Path] = []
    for channel_id, profile in build_builtin_profiles(settings).items():
        root = config_comfy_channels_root(settings) / channel_id
        root.mkdir(parents=True, exist_ok=True)
        path = root / "profile.json"
        path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(path)
        wf_dir = root / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        for wf in profile.workflows:
            wf_path = wf_dir / f"{wf.workflow_id}.json"
            wf_path.write_text(wf.model_dump_json(indent=2) + "\n", encoding="utf-8")
            written.append(wf_path)
        # Mirror workflow index into Comfy project dir
        project = Path(profile.comfy_project_dir)
        (project / "workflows").mkdir(parents=True, exist_ok=True)
        index = project / "workflows" / "index.json"
        index.write_text(
            json.dumps(
                {
                    "channel_id": profile.channel_id,
                    "legacy_project_key": profile.legacy_project_key,
                    "shared_models": True,
                    "workflows": [w.model_dump(mode="json") for w in profile.workflows],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        written.append(index)
    return written
