from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from atnos.core.paths import atnos_state_dir


def resolve_repo_root_fallback() -> Path:
    current_file = Path(__file__).resolve()

    for candidate in (current_file.parent, *current_file.parents):
        if (candidate / "frontend").exists() and (candidate / "runtime-recovered").exists():
            return candidate
        if (candidate / ".git").exists() and ((candidate / "apps").exists() or (candidate / "frontend").exists()):
            return candidate
        if (candidate / "Launch-Atnos.ps1").exists() and (candidate / "desktop-web").exists():
            return candidate

    return Path.cwd().resolve()


def load_runtime_env_files() -> tuple[Path, ...]:
    """Load only the external runtime env, never a secret file from the repository."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return ()

    candidates: list[Path] = []
    configured_env = str(os.environ.get("ATNOS_ENV_FILE") or "").strip()
    if configured_env:
        candidates.append(Path(configured_env).expanduser())
    else:
        candidates.append(atnos_state_dir().parent / "config" / ".env")

    seen: set[Path] = set()
    loaded: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        load_dotenv(resolved, override=False)
        loaded.append(resolved)

    if loaded and "ATNOS_LOADED_ENV_FILES" not in os.environ:
        os.environ["ATNOS_LOADED_ENV_FILES"] = os.pathsep.join(str(path) for path in loaded)
    return tuple(loaded)


def _normalize_hex_color(value: str, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    if len(normalized) != 7:
        return fallback
    try:
        int(normalized[1:], 16)
    except ValueError:
        return fallback
    return normalized.upper()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Atnos API"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    background_automation_enabled: bool = Field(default=True, validation_alias="ATNOS_BACKGROUND_AUTOMATION_ENABLED")
    deployment_mode: Literal["local-first"] = "local-first"
    atnos_local_work_unlimited: bool = Field(default=False, validation_alias="ATNOS_LOCAL_WORK_UNLIMITED")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    local_host_capability_token: SecretStr | None = Field(
        default=None,
        validation_alias="ATNOS_LOCAL_CAPABILITY_TOKEN",
    )
    local_host_capability_expires_at: int | None = Field(
        default=None,
        validation_alias="ATNOS_LOCAL_CAPABILITY_EXPIRES_AT",
    )
    local_host_raw_powershell_enabled: bool = Field(
        default=False,
        validation_alias="ATNOS_LOCAL_HOST_RAW_POWERSHELL_ENABLED",
    )
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", validation_alias="OLLAMA_BASE_URL")
    ollama_timeout_s: float = Field(default=600, validation_alias="OLLAMA_TIMEOUT_S")
    ollama_health_timeout_s: float = Field(default=2, validation_alias="OLLAMA_HEALTH_TIMEOUT_S")
    ollama_main_model: str | None = Field(default=None, validation_alias="OLLAMA_MAIN_MODEL")
    ollama_chat_model: str | None = Field(default=None, validation_alias="OLLAMA_CHAT_MODEL")
    ollama_document_model: str | None = Field(default=None, validation_alias="OLLAMA_DOCUMENT_MODEL")
    ollama_synthesis_model: str | None = Field(default=None, validation_alias="OLLAMA_SYNTHESIS_MODEL")
    ollama_orchestration_model: str | None = Field(default=None, validation_alias="OLLAMA_ORCHESTRATION_MODEL")
    ollama_vision_model: str | None = Field(default=None, validation_alias="OLLAMA_VISION_MODEL")
    ollama_fast_model: str | None = Field(default=None, validation_alias="OLLAMA_FAST_MODEL")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_tts_model: str = Field(default="gpt-4o-mini-tts", validation_alias="OPENAI_TTS_MODEL")
    openai_tts_voice: str = Field(default="marin", validation_alias="OPENAI_TTS_VOICE")
    openai_tts_voice_wealth_blueprint: str = Field(default="marin", validation_alias="OPENAI_TTS_VOICE_WEALTH_BLUEPRINT")
    openai_tts_voice_dark_chronicles: str = Field(default="onyx", validation_alias="OPENAI_TTS_VOICE_DARK_CHRONICLES")
    openai_tts_voice_sigma_squad: str = Field(default="verse", validation_alias="OPENAI_TTS_VOICE_SIGMA_SQUAD")
    openai_tts_response_format: str = Field(default="mp3", validation_alias="OPENAI_TTS_RESPONSE_FORMAT")
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.6-flash", validation_alias="GEMINI_MODEL")
    gemini_daily_request_limit: int = Field(default=1500, ge=0, validation_alias="GEMINI_DAILY_REQUEST_LIMIT")
    gemini_daily_warning_threshold: int = Field(default=1400, ge=0, validation_alias="GEMINI_DAILY_WARNING_THRESHOLD")
    provider_priority_order: tuple[str, ...] = Field(
        default=("openai-cloud", "grok-cloud", "anthropic-cloud", "ollama-local"),
        validation_alias="ATNOS_PROVIDER_PRIORITY_ORDER",
    )
    provider_health_cache_ttl_s: int = Field(
        default=30,
        ge=1,
        validation_alias="ATNOS_PROVIDER_HEALTH_CACHE_TTL_S",
    )
    comfyui_base_url: str = Field(default="http://127.0.0.1:8188", validation_alias="COMFYUI_BASE_URL")
    comfyui_health_timeout_s: float = Field(default=2, validation_alias="COMFYUI_HEALTH_TIMEOUT_S")
    comfyui_install_dir: Path | None = Field(default=None, validation_alias="COMFYUI_INSTALL_DIR")
    comfyui_models_dir: Path | None = Field(default=None, validation_alias="COMFYUI_MODELS_DIR")
    comfyui_cold_models_dir: Path | None = Field(default=None, validation_alias="COMFYUI_COLD_MODELS_DIR")
    comfyui_allow_managed_launch: bool = Field(
        default=False,
        validation_alias="COMFYUI_ALLOW_MANAGED_LAUNCH",
    )
    comfyui_shared_model_paths: Path | None = Field(
        default=None,
        validation_alias="COMFYUI_SHARED_MODEL_PATHS",
    )
    comfyui_input_dir: Path | None = Field(default=None, validation_alias="COMFYUI_INPUT_DIR")
    comfyui_output_dir: Path | None = Field(default=None, validation_alias="COMFYUI_OUTPUT_DIR")
    comfyui_cache_classic: bool = Field(default=False, validation_alias="COMFYUI_CACHE_CLASSIC")
    comfyui_reserve_vram_gb: float = Field(
        default=1.0,
        ge=0.0,
        le=8.0,
        validation_alias="COMFYUI_RESERVE_VRAM_GB",
    )
    comfyui_lowvram: bool = Field(default=True, validation_alias="COMFYUI_LOWVRAM")
    comfyui_default_checkpoint: str | None = Field(default=None, validation_alias="COMFYUI_DEFAULT_CHECKPOINT")
    comfyui_negative_prompt: str = Field(default="", validation_alias="COMFYUI_NEGATIVE_PROMPT")
    comfyui_generation_timeout_s: int = Field(default=1200, validation_alias="COMFYUI_GENERATION_TIMEOUT_S")
    comfyui_video_retry_attempts: int = Field(default=2, ge=1, le=4, validation_alias="COMFYUI_VIDEO_RETRY_ATTEMPTS")
    comfyui_video_output_grace_s: int = Field(default=75, ge=0, le=300, validation_alias="COMFYUI_VIDEO_OUTPUT_GRACE_S")
    comfyui_steps: int = Field(default=16, validation_alias="COMFYUI_STEPS")
    comfyui_cfg_scale: float = Field(default=6.5, validation_alias="COMFYUI_CFG_SCALE")
    comfyui_sampler_name: str = Field(default="euler", validation_alias="COMFYUI_SAMPLER_NAME")
    comfyui_scheduler: str = Field(default="normal", validation_alias="COMFYUI_SCHEDULER")
    ltx_video_model_repo: str = Field(default="Lightricks/LTX-Video", validation_alias="LTX_VIDEO_MODEL_REPO")
    ltx_video_model_dir: Path | None = Field(default=None, validation_alias="LTX_VIDEO_MODEL_DIR")
    wan_video_model_repo: str = Field(
        default="Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        validation_alias="WAN_VIDEO_MODEL_REPO",
    )
    wan_video_model_dir: Path | None = Field(default=None, validation_alias="WAN_VIDEO_MODEL_DIR")
    video_generation_steps: int = Field(default=8, validation_alias="VIDEO_GENERATION_STEPS")
    video_generation_guidance_scale: float = Field(default=3.0, validation_alias="VIDEO_GENERATION_GUIDANCE_SCALE")
    video_generation_negative_prompt: str = Field(
        default="worst quality, inconsistent motion, blurry, jittery, distorted",
        validation_alias="VIDEO_GENERATION_NEGATIVE_PROMPT",
    )
    shorts_video_backend: Literal[
        "motion_graphic",
        "comfy_image_motion",
        "auto",
        "ltx",
        "wan",
        "openai_image_motion",
    ] = Field(
        default="motion_graphic",
        validation_alias="SHORTS_VIDEO_BACKEND",
    )
    shorts_visual_max_generated_scenes: int = Field(default=11, ge=1, le=16, validation_alias="SHORTS_VISUAL_MAX_GENERATED_SCENES")
    shorts_visual_min_real_clips: int = Field(default=6, ge=1, le=8, validation_alias="SHORTS_VISUAL_MIN_REAL_CLIPS")
    shorts_visual_clip_duration_s: float = Field(default=1.35, ge=1.0, le=3.0, validation_alias="SHORTS_VISUAL_CLIP_DURATION_S")
    shorts_require_real_visuals: bool = Field(default=True, validation_alias="SHORTS_REQUIRE_REAL_VISUALS")
    shorts_edge_tts_voice_pl: str = Field(default="pl-PL-MarekNeural", validation_alias="SHORTS_EDGE_TTS_VOICE_PL")
    shorts_edge_tts_voice_en: str = Field(default="en-US-GuyNeural", validation_alias="SHORTS_EDGE_TTS_VOICE_EN")
    shorts_edge_tts_rate: str = Field(default="+4%", validation_alias="SHORTS_EDGE_TTS_RATE")
    piper_exe: str | None = Field(default=None, validation_alias="PIPER_EXE")
    piper_pl_model_path: Path | None = Field(default=None, validation_alias="PIPER_PL_MODEL_PATH")
    piper_pl_config_path: Path | None = Field(default=None, validation_alias="PIPER_PL_CONFIG_PATH")
    whisper_cpp_exe: str | None = Field(default=None, validation_alias="WHISPER_CPP_EXE")
    whisper_model_path: Path | None = Field(default=None, validation_alias="WHISPER_MODEL_PATH")
    whisper_language: str = Field(default="en", validation_alias="WHISPER_LANGUAGE")
    atnos_local_shorts_enabled: bool = Field(default=True, validation_alias="ATNOS_LOCAL_SHORTS_ENABLED")
    shorts_output_dir: Path | None = Field(default=None, validation_alias="SHORTS_OUTPUT_DIR")
    final_delivery_root: Path | None = Field(default=None, validation_alias="ATNOS_FINAL_DELIVERY_ROOT")
    shorts_require_human_approval_before_render: bool = Field(
        default=False,
        validation_alias="SHORTS_REQUIRE_HUMAN_APPROVAL_BEFORE_RENDER",
    )
    shorts_require_human_approval_before_upload: bool = Field(
        default=True,
        validation_alias="SHORTS_REQUIRE_HUMAN_APPROVAL_BEFORE_UPLOAD",
    )
    shorts_review_email_enabled: bool = Field(default=True, validation_alias="SHORTS_REVIEW_EMAIL_ENABLED")
    shorts_review_email_send_immediately: bool = Field(
        default=True,
        validation_alias="SHORTS_REVIEW_EMAIL_SEND_IMMEDIATELY",
    )
    shorts_autopilot_enabled: bool = Field(default=True, validation_alias="SHORTS_AUTOPILOT_ENABLED")
    shorts_autopilot_interval_minutes: int = Field(
        default=15,
        ge=1,
        le=240,
        validation_alias="SHORTS_AUTOPILOT_INTERVAL_MINUTES",
    )
    shorts_autopilot_max_jobs_per_tick: int = Field(
        default=1,
        ge=1,
        le=3,
        validation_alias="SHORTS_AUTOPILOT_MAX_JOBS_PER_TICK",
    )
    shorts_autopilot_min_topic_buffer: int = Field(
        default=5,
        ge=0,
        le=50,
        validation_alias="SHORTS_AUTOPILOT_MIN_TOPIC_BUFFER",
    )
    shorts_autopilot_generate_count: int = Field(
        default=20,
        ge=1,
        le=100,
        validation_alias="SHORTS_AUTOPILOT_GENERATE_COUNT",
    )
    shorts_autopilot_auto_approve_min_score: float = Field(
        default=75.0,
        ge=0.0,
        le=100.0,
        validation_alias="SHORTS_AUTOPILOT_AUTO_APPROVE_MIN_SCORE",
    )
    shorts_autopilot_best_effort_approval_enabled: bool = Field(
        default=True,
        validation_alias="SHORTS_AUTOPILOT_BEST_EFFORT_APPROVAL_ENABLED",
    )
    shorts_autopilot_best_effort_min_score: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        validation_alias="SHORTS_AUTOPILOT_BEST_EFFORT_MIN_SCORE",
    )
    shorts_autopilot_auto_reject_below_score: float = Field(
        default=30.0,
        ge=0.0,
        le=100.0,
        validation_alias="SHORTS_AUTOPILOT_AUTO_REJECT_BELOW_SCORE",
    )
    shorts_autopilot_require_shorts_potential: bool = Field(
        default=True,
        validation_alias="SHORTS_AUTOPILOT_REQUIRE_SHORTS_POTENTIAL",
    )
    shorts_autopilot_block_clickbait_risk: bool = Field(
        default=True,
        validation_alias="SHORTS_AUTOPILOT_BLOCK_CLICKBAIT_RISK",
    )
    investment_paper_autopilot_enabled: bool = Field(
        default=True,
        validation_alias="INVESTMENT_PAPER_AUTOPILOT_ENABLED",
    )
    investment_paper_autopilot_interval_minutes: int = Field(
        default=240,
        ge=5,
        le=1440,
        validation_alias="INVESTMENT_PAPER_AUTOPILOT_INTERVAL_MINUTES",
    )
    investment_paper_autopilot_max_trades_per_tick: int = Field(
        default=3,
        ge=1,
        le=10,
        validation_alias="INVESTMENT_PAPER_AUTOPILOT_MAX_TRADES_PER_TICK",
    )
    investment_paper_autopilot_alerts_enabled: bool = Field(
        default=True,
        validation_alias="INVESTMENT_PAPER_AUTOPILOT_ALERTS_ENABLED",
    )
    investment_paper_autopilot_alert_on_fallback: bool = Field(
        default=True,
        validation_alias="INVESTMENT_PAPER_AUTOPILOT_ALERT_ON_FALLBACK",
    )
    investment_paper_autopilot_alert_on_unusual_decision: bool = Field(
        default=True,
        validation_alias="INVESTMENT_PAPER_AUTOPILOT_ALERT_ON_UNUSUAL_DECISION",
    )
    shorts_quality_review_scheduler_enabled: bool = Field(
        default=True,
        validation_alias="SHORTS_QUALITY_REVIEW_SCHEDULER_ENABLED",
    )
    shorts_quality_review_interval_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        validation_alias="SHORTS_QUALITY_REVIEW_INTERVAL_MINUTES",
    )
    shorts_quality_review_max_jobs_per_tick: int = Field(
        default=10,
        ge=1,
        le=50,
        validation_alias="SHORTS_QUALITY_REVIEW_MAX_JOBS_PER_TICK",
    )
    shorts_review_require_public_quality: bool = Field(
        default=True,
        validation_alias="SHORTS_REVIEW_REQUIRE_PUBLIC_QUALITY",
    )
    shorts_semantic_visual_audit_enabled: bool = Field(
        default=True,
        validation_alias="SHORTS_SEMANTIC_VISUAL_AUDIT_ENABLED",
    )
    shorts_semantic_visual_audit_timeout_s: float = Field(
        default=120.0,
        ge=1.0,
        le=120.0,
        validation_alias="SHORTS_SEMANTIC_VISUAL_AUDIT_TIMEOUT_S",
    )
    shorts_semantic_visual_audit_min_score: float = Field(
        default=7.0,
        ge=0.0,
        le=10.0,
        validation_alias="SHORTS_SEMANTIC_VISUAL_AUDIT_MIN_SCORE",
    )
    shorts_semantic_visual_audit_block_on_unavailable: bool = Field(
        default=True,
        validation_alias="SHORTS_SEMANTIC_VISUAL_AUDIT_BLOCK_ON_UNAVAILABLE",
    )
    shorts_order_autonomy_enabled: bool = Field(default=False, validation_alias="SHORTS_ORDER_AUTONOMY_ENABLED")
    shorts_quality_auto_remediation_enabled: bool = Field(
        default=True,
        validation_alias="SHORTS_QUALITY_AUTO_REMEDIATION_ENABLED",
    )
    shorts_quality_auto_remediation_interval_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,
        validation_alias="SHORTS_QUALITY_AUTO_REMEDIATION_INTERVAL_MINUTES",
    )
    shorts_quality_auto_remediation_max_jobs_per_tick: int = Field(
        default=1,
        ge=1,
        le=3,
        validation_alias="SHORTS_QUALITY_AUTO_REMEDIATION_MAX_JOBS_PER_TICK",
    )
    shorts_quality_auto_remediation_run_jobs: bool = Field(
        default=True,
        validation_alias="SHORTS_QUALITY_AUTO_REMEDIATION_RUN_JOBS",
    )
    shorts_stale_job_after_minutes: int = Field(
        default=60,
        ge=1,
        le=1440,
        validation_alias="SHORTS_STALE_JOB_AFTER_MINUTES",
    )
    shorts_min_quality_score: float = Field(default=7.0, validation_alias="SHORTS_MIN_QUALITY_SCORE")
    shorts_tts_backend: Literal[
        "f5_tts_http",
        "kokoro_local",
        "coqui_tts",
        "pyttsx3",
        "piper",
        "elevenlabs",
        "openai_tts",
    ] = Field(
        default="f5_tts_http",
        validation_alias="SHORTS_TTS_BACKEND",
    )
    shorts_allow_cloud_tts: bool = Field(default=False, validation_alias="SHORTS_ALLOW_CLOUD_TTS")
    shorts_allow_cloud_visuals: bool = Field(default=False, validation_alias="SHORTS_ALLOW_CLOUD_VISUALS")
    # Enforce configured external-cost limits by default; zero-cost local work is unaffected.
    atnos_cost_guard_enabled: bool = Field(default=True, validation_alias="ATNOS_COST_GUARD_ENABLED")
    atnos_monthly_spend_limit_pln: float = Field(
        default=100000.0,
        ge=0.0,
        validation_alias="ATNOS_MONTHLY_SPEND_LIMIT_PLN",
    )
    atnos_daily_spend_limit_pln: float = Field(
        default=100000.0,
        ge=0.0,
        validation_alias="ATNOS_DAILY_SPEND_LIMIT_PLN",
    )
    atnos_cost_warning_threshold_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        validation_alias="ATNOS_COST_WARNING_THRESHOLD_RATIO",
    )
    atnos_estimated_cost_per_premium_short_pln: float = Field(
        default=4.0,
        ge=0.0,
        validation_alias="ATNOS_ESTIMATED_COST_PER_PREMIUM_SHORT_PLN",
    )
    atnos_estimated_tts_cost_per_short_pln: float = Field(
        default=0.5,
        ge=0.0,
        validation_alias="ATNOS_ESTIMATED_TTS_COST_PER_SHORT_PLN",
    )
    atnos_estimated_visual_cost_per_short_pln: float = Field(
        default=3.5,
        ge=0.0,
        validation_alias="ATNOS_ESTIMATED_VISUAL_COST_PER_SHORT_PLN",
    )
    atnos_estimated_topic_generation_batch_cost_pln: float = Field(
        default=0.5,
        ge=0.0,
        validation_alias="ATNOS_ESTIMATED_TOPIC_GENERATION_BATCH_COST_PLN",
    )
    shorts_tts_live_generation_probe: bool = Field(default=False, validation_alias="SHORTS_TTS_LIVE_GENERATION_PROBE")
    shorts_brand_color: str = Field(default="#C2E5FF", validation_alias="SHORTS_BRAND_COLOR")
    shorts_brand_color_dark: str = Field(default="#0A1628", validation_alias="SHORTS_BRAND_COLOR_DARK")
    shorts_show_watermark: bool = Field(default=False, validation_alias="SHORTS_SHOW_WATERMARK")
    shorts_watermark_text: str = Field(default="", validation_alias="SHORTS_WATERMARK_TEXT")
    elevenlabs_api_key: str | None = Field(default=None, validation_alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str | None = Field(default=None, validation_alias="ELEVENLABS_VOICE_ID")
    elevenlabs_voice_id_wealth_blueprint: str | None = Field(
        default=None,
        validation_alias="ELEVENLABS_VOICE_ID_WEALTH_BLUEPRINT",
    )
    elevenlabs_voice_id_dark_chronicles: str | None = Field(
        default=None,
        validation_alias="ELEVENLABS_VOICE_ID_DARK_CHRONICLES",
    )
    elevenlabs_voice_id_sigma_squad: str | None = Field(
        default=None,
        validation_alias="ELEVENLABS_VOICE_ID_SIGMA_SQUAD",
    )
    elevenlabs_model_id: str = Field(default="eleven_multilingual_v2", validation_alias="ELEVENLABS_MODEL_ID")
    elevenlabs_base_url: str = Field(default="https://api.elevenlabs.io", validation_alias="ELEVENLABS_BASE_URL")
    elevenlabs_language_code: str = Field(default="en", validation_alias="ELEVENLABS_LANGUAGE_CODE")
    elevenlabs_output_format: str = Field(default="mp3_44100_128", validation_alias="ELEVENLABS_OUTPUT_FORMAT")
    elevenlabs_stability: float = Field(default=0.62, ge=0.0, le=1.0, validation_alias="ELEVENLABS_STABILITY")
    elevenlabs_similarity_boost: float = Field(
        default=0.78,
        ge=0.0,
        le=1.0,
        validation_alias="ELEVENLABS_SIMILARITY_BOOST",
    )
    elevenlabs_style: float = Field(default=0.0, ge=0.0, le=1.0, validation_alias="ELEVENLABS_STYLE")
    elevenlabs_speed: float = Field(default=1.0, ge=0.7, le=1.2, validation_alias="ELEVENLABS_SPEED")
    elevenlabs_use_speaker_boost: bool = Field(
        default=True,
        validation_alias="ELEVENLABS_USE_SPEAKER_BOOST",
    )
    f5_tts_base_url: str = Field(default="http://localhost:7860/api/predict", validation_alias="F5_TTS_BASE_URL")
    f5_tts_transport: Literal["gradio", "cli"] = Field(default="gradio", validation_alias="F5_TTS_TRANSPORT")
    f5_tts_install_dir: Path | None = Field(default=None, validation_alias="F5_TTS_INSTALL_DIR")
    f5_tts_runtime_python: Path | None = Field(default=None, validation_alias="F5_TTS_RUNTIME_PYTHON")
    f5_tts_ffmpeg_dir: Path | None = Field(default=None, validation_alias="F5_TTS_FFMPEG_DIR")
    f5_tts_gradio_temp_dir: Path | None = Field(default=None, validation_alias="F5_TTS_GRADIO_TEMP_DIR")
    f5_tts_launch_command: str = Field(default="", validation_alias="F5_TTS_LAUNCH_COMMAND")
    f5_tts_ref_audio_path: Path | None = Field(default=None, validation_alias="F5_TTS_REF_AUDIO_PATH")
    f5_tts_ref_text: str = Field(
        default="Some call me nature, others call me mother nature.",
        validation_alias="F5_TTS_REF_TEXT",
    )
    f5_tts_api_name: str = Field(default="basic_tts", validation_alias="F5_TTS_API_NAME")
    f5_tts_nfe_steps: int = Field(default=32, ge=4, le=64, validation_alias="F5_TTS_NFE_STEPS")
    f5_tts_speed: float = Field(default=1.0, ge=0.3, le=2.0, validation_alias="F5_TTS_SPEED")
    f5_tts_cross_fade_duration: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        validation_alias="F5_TTS_CROSS_FADE_DURATION",
    )
    f5_tts_generation_timeout_sec: float = Field(
        default=300.0,
        ge=30.0,
        le=1800.0,
        validation_alias="F5_TTS_GENERATION_TIMEOUT_SEC",
    )
    f5_tts_single_pass_narration: bool = Field(default=False, validation_alias="F5_TTS_SINGLE_PASS_NARRATION")
    coqui_tts_model: str = Field(default="tts_models/pl/mai_female/vits", validation_alias="COQUI_TTS_MODEL")
    youtube_data_api_key: str = Field(default="", validation_alias="YOUTUBE_DATA_API_KEY")
    youtube_client_secret_file: Path | None = Field(default=None, validation_alias="YOUTUBE_CLIENT_SECRET_FILE")
    youtube_upload_privacy: str = Field(default="private", validation_alias="YOUTUBE_UPLOAD_PRIVACY")
    youtube_default_language: str = Field(default="en", validation_alias="YOUTUBE_DEFAULT_LANGUAGE")
    youtube_default_audio_language: str = Field(default="en", validation_alias="YOUTUBE_DEFAULT_AUDIO_LANGUAGE")
    youtube_category_id: str = Field(default="28", validation_alias="YOUTUBE_CATEGORY_ID")
    allow_public_upload: bool = Field(default=False, validation_alias="ALLOW_PUBLIC_UPLOAD")
    learning_mode: bool = Field(default=False, validation_alias="LEARNING_MODE")
    youtube_write_block: bool = Field(default=False, validation_alias="YOUTUBE_WRITE_BLOCK")
    youtube_upload_enabled: bool = Field(default=True, validation_alias="YOUTUBE_UPLOAD_ENABLED")
    youtube_edit_enabled: bool = Field(default=True, validation_alias="YOUTUBE_EDIT_ENABLED")
    youtube_delete_enabled: bool = Field(default=True, validation_alias="YOUTUBE_DELETE_ENABLED")
    youtube_comment_enabled: bool = Field(default=True, validation_alias="YOUTUBE_COMMENT_ENABLED")
    youtube_playlist_write_enabled: bool = Field(default=True, validation_alias="YOUTUBE_PLAYLIST_WRITE_ENABLED")
    youtube_scope_escalation_allowed: bool = Field(
        default=True,
        validation_alias="YOUTUBE_SCOPE_ESCALATION_ALLOWED",
    )
    external_ai_transfer_allowed: bool = Field(default=True, validation_alias="EXTERNAL_AI_TRANSFER_ALLOWED")
    cloud_llm_allowed: bool = Field(default=True, validation_alias="CLOUD_LLM_ALLOWED")
    local_processing_only: bool = Field(default=False, validation_alias="LOCAL_PROCESSING_ONLY")
    shorts_require_premium_voice_before_publication: bool = Field(
        default=True,
        validation_alias="SHORTS_REQUIRE_PREMIUM_VOICE_BEFORE_PUBLICATION",
    )
    shorts_require_premium_voice_before_render: bool = Field(
        default=False,
        validation_alias="SHORTS_REQUIRE_PREMIUM_VOICE_BEFORE_RENDER",
    )
    require_upload_approval: bool = Field(default=True, validation_alias="REQUIRE_UPLOAD_APPROVAL")
    n8n_enabled: bool = Field(default=True, validation_alias="N8N_ENABLED")
    n8n_base_url: str = Field(default="http://127.0.0.1:5678", validation_alias="N8N_BASE_URL")
    n8n_data_dir: Path | None = Field(default=None, validation_alias="N8N_DATA_DIR")
    n8n_youtube_automation_mode: Literal["approval_required", "disabled"] = Field(
        default="approval_required",
        validation_alias="N8N_YOUTUBE_AUTOMATION_MODE",
    )
    n8n_atnos_webhook_token: str | None = Field(default=None, validation_alias="N8N_ATNOS_WEBHOOK_TOKEN")
    slack_webhook_url: str | None = Field(default=None, validation_alias="SLACK_WEBHOOK_URL")
    slack_notifications_enabled: bool = Field(default=True, validation_alias="SLACK_NOTIFICATIONS_ENABLED")
    atnos_ubuntu_host: str | None = Field(default=None, validation_alias="ATNOS_UBUNTU_HOST")
    atnos_ubuntu_user: str = Field(default="atnos", validation_alias="ATNOS_UBUNTU_USER")
    atnos_ubuntu_key: Path | None = Field(default=None, validation_alias="ATNOS_UBUNTU_KEY")
    atnos_ubuntu_workdir: str = Field(default="/home/atnos/atnos", validation_alias="ATNOS_UBUNTU_WORKDIR")
    atnos_ubuntu_allow_sudo: bool = Field(default=True, validation_alias="ATNOS_UBUNTU_ALLOW_SUDO")
    atnos_ubuntu_require_approval_for_sudo: bool = Field(
        default=False,
        validation_alias="ATNOS_UBUNTU_REQUIRE_APPROVAL_FOR_SUDO",
    )
    atnos_agent_autonomy_level: Literal["assisted", "full_sandbox"] = Field(
        default="full_sandbox",
        validation_alias="ATNOS_AGENT_AUTONOMY_LEVEL",
    )
    atnos_autonomy_mode: Literal["assisted", "supervised", "full_auto_sandbox"] | None = Field(
        default=None,
        validation_alias="ATNOS_AUTONOMY_MODE",
    )
    atnos_sandbox_host: str | None = Field(default=None, validation_alias="ATNOS_SANDBOX_HOST")
    atnos_sandbox_user: str = Field(default="atnos", validation_alias="ATNOS_SANDBOX_USER")
    atnos_sandbox_allow_sudo: bool = Field(default=True, validation_alias="ATNOS_SANDBOX_ALLOW_SUDO")
    atnos_sandbox_log_everything: bool = Field(default=True, validation_alias="ATNOS_SANDBOX_LOG_EVERYTHING")
    atnos_main_pc_high_risk_approval: bool = Field(
        default=True,
        validation_alias="ATNOS_MAIN_PC_HIGH_RISK_APPROVAL",
    )
    atnos_publication_approval: bool = Field(default=True, validation_alias="ATNOS_PUBLICATION_APPROVAL")
    atnos_api_billing_approval: bool = Field(default=True, validation_alias="ATNOS_API_BILLING_APPROVAL")
    atnos_secret_access_approval: bool = Field(default=True, validation_alias="ATNOS_SECRET_ACCESS_APPROVAL")
    atnos_log_all_actions: bool = Field(default=True, validation_alias="ATNOS_LOG_ALL_ACTIONS")
    atnos_require_approval_for_publication: bool = Field(
        default=True,
        validation_alias="ATNOS_REQUIRE_APPROVAL_FOR_PUBLICATION",
    )
    atnos_require_approval_for_main_pc_high_risk: bool = Field(
        default=True,
        validation_alias="ATNOS_REQUIRE_APPROVAL_FOR_MAIN_PC_HIGH_RISK",
    )
    atnos_sandbox_allow_high_risk: bool = Field(
        default=True,
        validation_alias="ATNOS_SANDBOX_ALLOW_HIGH_RISK",
    )
    agent_decisions_log_path: Path | None = Field(default=None, validation_alias="ATNOS_AGENT_DECISIONS_LOG_PATH")
    agent_tool_calls_log_path: Path | None = Field(default=None, validation_alias="ATNOS_AGENT_TOOL_CALLS_LOG_PATH")
    kokoro_model_path: Path | None = Field(default=None, validation_alias="KOKORO_MODEL_PATH")
    kokoro_voices_path: Path | None = Field(default=None, validation_alias="KOKORO_VOICES_PATH")
    kokoro_voice_name: str = Field(default="af_heart", validation_alias="KOKORO_VOICE_NAME")
    wav2lip_custom_node_dir: Path | None = Field(default=None, validation_alias="WAV2LIP_CUSTOM_NODE_DIR")
    wav2lip_checkpoint_path: Path | None = Field(default=None, validation_alias="WAV2LIP_CHECKPOINT_PATH")
    wav2lip_face_detector_path: Path | None = Field(default=None, validation_alias="WAV2LIP_FACE_DETECTOR_PATH")
    wav2lip_timeout_s: int = Field(default=600, ge=60, le=3600, validation_alias="WAV2LIP_TIMEOUT_S")
    wav2lip_fps: int = Field(default=30, ge=12, le=60, validation_alias="WAV2LIP_FPS")
    wav2lip_face_detect_batch: int = Field(default=4, ge=1, le=64, validation_alias="WAV2LIP_FACE_DETECT_BATCH")
    obsidian_vault_path: Path | None = Field(default=None, validation_alias="OBSIDIAN_VAULT_PATH")
    obsidian_export_enabled: bool = Field(default=False, validation_alias="OBSIDIAN_EXPORT_ENABLED")
    atnos_obsidian_agent_dir: str = Field(default="Atnos/Agent Logs", validation_alias="ATNOS_OBSIDIAN_AGENT_DIR")
    atnos_obsidian_decision_dir: str = Field(
        default="Atnos/Decision Log",
        validation_alias="ATNOS_OBSIDIAN_DECISION_DIR",
    )
    atnos_obsidian_shorts_dir: str = Field(default="Atnos/Shorts", validation_alias="ATNOS_OBSIDIAN_SHORTS_DIR")
    atnos_write_obsidian_logs: bool = Field(default=True, validation_alias="ATNOS_WRITE_OBSIDIAN_LOGS")
    max_upload_size_bytes: int = 10 * 1024 * 1024
    voice_stt_engine: Literal[
        "browser-speech",
        "whisper-local",
        "faster-whisper-local",
    ] = "browser-speech"
    voice_stt_model_path: str | None = None
    voice_stt_device: str = "cpu"
    account_secret_storage_backend: Literal["auto", "aes_256_gcm", "os_keyring", "metadata_only"] = Field(
        default="auto",
        validation_alias="ACCOUNT_SECRET_STORAGE_BACKEND",
    )
    trace_retention_enabled: bool = Field(
        default=False,
        validation_alias="TRACE_RETENTION_ENABLED",
    )
    trace_retention_days: int = Field(
        default=30,
        ge=1,
        validation_alias="TRACE_RETENTION_DAYS",
    )
    render_watchdog_timeout_s: int = Field(
        default=240,
        ge=30,
        le=1800,
        validation_alias="RENDER_WATCHDOG_TIMEOUT_S",
    )
    oauth_enabled: bool = Field(default=False, validation_alias="OAUTH_ENABLED")
    oauth_redirect_base_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="OAUTH_REDIRECT_BASE_URL",
    )
    oauth_github_client_id: str | None = Field(default=None, validation_alias="OAUTH_GITHUB_CLIENT_ID")
    oauth_github_client_secret_ref: str | None = Field(
        default=None,
        validation_alias="OAUTH_GITHUB_CLIENT_SECRET_REF",
    )
    oauth_google_client_id: str | None = Field(default=None, validation_alias="OAUTH_GOOGLE_CLIENT_ID")
    oauth_google_client_secret_ref: str | None = Field(
        default=None,
        validation_alias="OAUTH_GOOGLE_CLIENT_SECRET_REF",
    )
    scheduled_tasks_enabled: bool = Field(default=True, validation_alias="SCHEDULED_TASKS_ENABLED")
    app_root: Path | None = Field(default=None, validation_alias="APP_ROOT")
    data_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("DATA_DIR", "ATNOS_STATE_DIR"),
    )
    uploads_dir: Path | None = Field(default=None, validation_alias="UPLOADS_DIR")
    account_secret_vault_dir: Path | None = Field(default=None, validation_alias="ACCOUNT_SECRET_VAULT_DIR")
    sqlite_file: Path | None = Field(default=None, validation_alias="SQLITE_FILE")
    runtime_dir_override: Path | None = Field(default=None, validation_alias="RUNTIME_DIR")
    quarantine_dir: Path | None = Field(default=None, validation_alias="QUARANTINE_DIR")
    browser_profile_dir: Path | None = Field(default=None, validation_alias="BROWSER_PROFILE_DIR")
    browser_downloads_dir: Path | None = Field(default=None, validation_alias="BROWSER_DOWNLOADS_DIR")
    browser_trace_dir: Path | None = Field(default=None, validation_alias="BROWSER_TRACE_DIR")
    playwright_browsers_path: Path | None = Field(default=None, validation_alias="PLAYWRIGHT_BROWSERS_PATH")
    browser_enabled: bool = Field(default=True, validation_alias="BROWSER_ENABLED")
    browser_headless: bool = Field(default=False, validation_alias="BROWSER_HEADLESS")
    browser_user_data_dir: Path | None = Field(default=None, validation_alias="BROWSER_USER_DATA_DIR")
    browser_operator_download_dir: Path | None = Field(default=None, validation_alias="BROWSER_DOWNLOAD_DIR")
    browser_screenshot_dir: Path | None = Field(default=None, validation_alias="BROWSER_SCREENSHOT_DIR")
    browser_log_path: Path | None = Field(default=None, validation_alias="BROWSER_LOG_PATH")
    browser_require_approval_for_high_risk: bool = Field(
        default=True,
        validation_alias="BROWSER_REQUIRE_APPROVAL_FOR_HIGH_RISK",
    )
    browser_allow_auto_submit: bool = Field(
        default=False,
        validation_alias="BROWSER_ALLOW_AUTO_SUBMIT",
    )
    browser_default_timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        validation_alias="BROWSER_DEFAULT_TIMEOUT_SECONDS",
    )
    ffmpeg_exe: str | None = Field(default=None, validation_alias="FFMPEG_EXE")
    atnos_tools_dir: Path | None = Field(default=None, validation_alias="ATNOS_TOOLS_DIR")
    atnos_node_exe: Path | None = Field(default=None, validation_alias="ATNOS_NODE")
    remotion_browser_executable: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("ATNOS_REMOTION_BROWSER", "REMOTION_BROWSER_EXECUTABLE"),
    )
    remotion_renderer_root: Path | None = Field(default=None, validation_alias="ATNOS_REMOTION_RENDERER_ROOT")
    wealth_blueprint_channel_references_dir: Path | None = Field(
        default=None,
        validation_alias="WEALTH_BLUEPRINT_CHANNEL_REFERENCES_DIR",
    )
    wealth_blueprint_require_whisper_alignment: bool = Field(
        default=True,
        validation_alias="WEALTH_BLUEPRINT_REQUIRE_WHISPER_ALIGNMENT",
    )
    wealth_blueprint_require_comfy_for_missing_assets: bool = Field(
        default=True,
        validation_alias="WEALTH_BLUEPRINT_REQUIRE_COMFY_FOR_MISSING_ASSETS",
    )
    visual_qa_dir: Path | None = Field(default=None, validation_alias="ATNOS_VISUAL_QA_DIR")
    visual_qa_log_path: Path | None = Field(default=None, validation_alias="ATNOS_VISUAL_QA_LOG_PATH")
    visual_qa_allow_cloud_default: bool = Field(
        default=False,
        validation_alias="ATNOS_VISUAL_QA_ALLOW_CLOUD",
    )
    self_improvement_allow_self_apply: bool = Field(
        default=False,
        validation_alias="ATNOS_SELF_IMPROVEMENT_ALLOW_SELF_APPLY",
    )
    self_improvement_final_reviewer: str = Field(
        default="codex",
        validation_alias="ATNOS_SELF_IMPROVEMENT_FINAL_REVIEWER",
    )
    self_improvement_handoff_dir: Path | None = Field(
        default=None,
        validation_alias="ATNOS_SELF_IMPROVEMENT_HANDOFF_DIR",
    )
    self_improvement_log_path: Path | None = Field(
        default=None,
        validation_alias="ATNOS_SELF_IMPROVEMENT_LOG_PATH",
    )
    codex_cli_root: Path | None = Field(
        default=None,
        validation_alias="ATNOS_CODEX_CLI_ROOT",
    )
    codex_node_executable: Path | None = Field(
        default=None,
        validation_alias="ATNOS_CODEX_NODE_EXECUTABLE",
    )
    codex_adapter_timeout_s: int = Field(
        default=180,
        ge=15,
        le=900,
        validation_alias="ATNOS_CODEX_ADAPTER_TIMEOUT_S",
    )
    workspace_staging_dir: Path | None = Field(default=None, validation_alias="WORKSPACE_STAGING_DIR")
    knowledge_dir: Path | None = Field(default=None, validation_alias="KNOWLEDGE_DIR")
    knowledge_workspace_dir: Path | None = Field(default=None, validation_alias="KNOWLEDGE_WORKSPACE_DIR")
    learning_archive_dir: Path | None = Field(
        default=None,
        validation_alias="ATNOS_LEARNING_ARCHIVE_DIR",
    )
    semantic_knowledge_index_path: Path | None = Field(
        default=None,
        validation_alias="SEMANTIC_KNOWLEDGE_INDEX_PATH",
    )
    semantic_knowledge_embedding_model: str = Field(
        default="bge-m3",
        validation_alias="SEMANTIC_KNOWLEDGE_EMBEDDING_MODEL",
    )
    browser_runtime_executable: Path | None = Field(
        default=None,
        validation_alias="BROWSER_RUNTIME_EXECUTABLE",
    )
    atnos_desktop_shell: Path | None = Field(default=None, validation_alias="ATNOS_DESKTOP_SHELL")
    database_url: str | None = None
    cors_allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "http://127.0.0.1:4174",
        "http://localhost:4174",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )

    @field_validator("obsidian_vault_path", mode="before")
    @classmethod
    def normalize_empty_obsidian_vault_path(cls, value):
        if value == "":
            return None
        return value

    @field_validator("provider_priority_order", mode="before")
    @classmethod
    def parse_provider_priority_order(cls, value):
        if value is None:
            return value
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, (list, tuple)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return value

    @model_validator(mode="after")
    def resolve_paths(self) -> "Settings":
        resolved_root = (self.app_root or resolve_repo_root_fallback()).resolve()
        resolved_data_dir = (self.data_dir or atnos_state_dir()).resolve()
        resolved_uploads_dir = (self.uploads_dir or (resolved_data_dir / "uploads")).resolve()
        resolved_account_secret_vault_dir = (
            self.account_secret_vault_dir or (resolved_data_dir / "account-secrets")
        ).resolve()
        resolved_sqlite_file = (self.sqlite_file or (resolved_data_dir / "atnos.db")).resolve()
        resolved_runtime_dir = (
            self.runtime_dir_override or (resolved_data_dir.parent / "runtime")
        ).resolve()
        resolved_quarantine_dir = (
            self.quarantine_dir or (resolved_data_dir / "quarantine")
        ).resolve()
        resolved_browser_profile_dir = (
            self.browser_profile_dir or (resolved_data_dir / "browser-profile")
        ).resolve()
        resolved_browser_downloads_dir = (
            self.browser_downloads_dir or (resolved_data_dir / "browser-downloads")
        ).resolve()
        resolved_browser_trace_dir = (
            self.browser_trace_dir or (resolved_data_dir / "browser-traces")
        ).resolve()
        resolved_playwright_browsers_path = (
            self.playwright_browsers_path or (resolved_data_dir / "playwright-browsers")
        ).resolve()
        resolved_browser_user_data_dir = (
            self.browser_user_data_dir or (resolved_data_dir / "browser_profiles" / "default")
        ).resolve()
        resolved_browser_operator_download_dir = (
            self.browser_operator_download_dir or (resolved_data_dir / "browser_downloads")
        ).resolve()
        resolved_browser_screenshot_dir = (
            self.browser_screenshot_dir or (resolved_data_dir / "browser_screenshots")
        ).resolve()
        resolved_browser_log_path = (
            self.browser_log_path or (resolved_data_dir / "logs" / "browser_actions.jsonl")
        ).resolve()
        resolved_visual_qa_dir = (
            self.visual_qa_dir or (resolved_data_dir / "visual-qa")
        ).resolve()
        resolved_visual_qa_log_path = (
            self.visual_qa_log_path or (resolved_data_dir / "logs" / "visual_qa.jsonl")
        ).resolve()
        resolved_self_improvement_handoff_dir = (
            self.self_improvement_handoff_dir
            or (resolved_data_dir / "self-improvement" / "codex-handoff")
        ).resolve()
        resolved_self_improvement_log_path = (
            self.self_improvement_log_path
            or (resolved_data_dir / "logs" / "self_improvement_governance.jsonl")
        ).resolve()
        default_codex_cli_root = (
            Path("E:/Atnos/Tools/CodexCLI")
            if os.name == "nt"
            else (resolved_data_dir / "tool-caches" / "codex-cli")
        )
        resolved_codex_cli_root = (self.codex_cli_root or default_codex_cli_root).expanduser().resolve()
        resolved_codex_node_executable = (
            self.codex_node_executable.expanduser().resolve()
            if self.codex_node_executable is not None
            else None
        )
        resolved_agent_decisions_log_path = (
            self.agent_decisions_log_path or (resolved_data_dir / "logs" / "agent_decisions.jsonl")
        ).resolve()
        resolved_agent_tool_calls_log_path = (
            self.agent_tool_calls_log_path or (resolved_data_dir / "logs" / "tool_calls.jsonl")
        ).resolve()
        resolved_shorts_output_dir = (
            self.shorts_output_dir or Path("E:/Rolki")
        ).resolve()
        resolved_final_delivery_root = (
            self.final_delivery_root or Path("E:/Rolki")
        ).resolve()
        resolved_youtube_client_secret_file = (
            self.youtube_client_secret_file
            or (resolved_data_dir.parent / "config" / "oauth" / "youtube_client_secret.json")
        ).resolve()
        resolved_n8n_data_dir = (
            self.n8n_data_dir or (resolved_data_dir / "n8n")
        ).resolve()
        resolved_workspace_staging_dir = (
            self.workspace_staging_dir or (resolved_data_dir / "workspace-staging")
        ).resolve()
        resolved_knowledge_dir = (
            self.knowledge_dir or (resolved_root / "runtime-recovered" / "api" / "knowledge")
        ).resolve()
        default_knowledge_workspace_dir = (resolved_data_dir / "knowledge-workspace").resolve()
        resolved_knowledge_workspace_dir = (
            self.knowledge_workspace_dir or default_knowledge_workspace_dir
        ).resolve()
        resolved_learning_archive_dir = (
            self.learning_archive_dir or (resolved_data_dir / "learning")
        ).resolve()
        resolved_semantic_knowledge_index_path = (
            self.semantic_knowledge_index_path
            or (resolved_data_dir / "knowledge" / "semantic-index.sqlite")
        ).resolve()
        resolved_kokoro_model_path = (
            self.kokoro_model_path or (resolved_data_dir / "models" / "kokoro" / "model.onnx")
        ).resolve()
        resolved_kokoro_voices_path = (
            self.kokoro_voices_path or (resolved_data_dir / "models" / "kokoro" / "voices-v1.0.bin")
        ).resolve()
        resolved_piper_pl_model_path = (
            self.piper_pl_model_path
            or (resolved_data_dir / "models" / "piper" / "pl_PL-darkman-medium.onnx")
        ).resolve()
        resolved_piper_pl_config_path = (
            self.piper_pl_config_path
            or (resolved_data_dir / "models" / "piper" / "pl_PL-darkman-medium.onnx.json")
        ).resolve()
        resolved_whisper_model_path = (
            self.whisper_model_path
            or (resolved_data_dir / "models" / "whisper" / "ggml-medium.bin")
        ).resolve()
        resolved_comfyui_install_dir = (
            self.comfyui_install_dir or (resolved_root.parent / "ComfyUI")
        ).resolve()
        resolved_ltx_video_model_dir = (
            self.ltx_video_model_dir or (resolved_data_dir / "models" / "video" / "ltx-video")
        ).resolve()
        resolved_wan_video_model_dir = (
            self.wan_video_model_dir or (resolved_data_dir / "models" / "video" / "wan-video")
        ).resolve()
        resolved_wav2lip_custom_node_dir = (
            self.wav2lip_custom_node_dir
            or (resolved_comfyui_install_dir / "ComfyUI" / "custom_nodes" / "ComfyUI_wav2lip")
        ).resolve()
        resolved_wav2lip_checkpoint_path = (
            self.wav2lip_checkpoint_path
            or (resolved_wav2lip_custom_node_dir / "Wav2Lip" / "checkpoints" / "wav2lip_gan.pth")
        ).resolve()
        resolved_wav2lip_face_detector_path = (
            self.wav2lip_face_detector_path
            or (
                resolved_wav2lip_custom_node_dir
                / "Wav2Lip"
                / "face_detection"
                / "detection"
                / "sfd"
                / "s3fd.pth"
            )
        ).resolve()

        self.app_root = resolved_root
        if self.ollama_main_model is not None:
            self.ollama_main_model = self.ollama_main_model.strip() or None
        if self.ollama_main_model:
            self.ollama_chat_model = self.ollama_chat_model or self.ollama_main_model
            self.ollama_synthesis_model = self.ollama_synthesis_model or self.ollama_main_model
            self.ollama_orchestration_model = self.ollama_orchestration_model or self.ollama_main_model
            self.ollama_document_model = self.ollama_document_model or self.ollama_main_model
        if self.ollama_fast_model is not None:
            self.ollama_fast_model = self.ollama_fast_model.strip() or None
        if self.ollama_vision_model is not None:
            self.ollama_vision_model = self.ollama_vision_model.strip() or None
        self.gemini_api_key = self.gemini_api_key.strip()
        self.gemini_model = self.gemini_model.strip() or "gemini-3.6-flash"
        if not self.provider_priority_order:
            self.provider_priority_order = ("openai-cloud", "grok-cloud", "anthropic-cloud", "ollama-local")
        self.data_dir = resolved_data_dir
        self.uploads_dir = resolved_uploads_dir
        self.account_secret_vault_dir = resolved_account_secret_vault_dir
        self.sqlite_file = resolved_sqlite_file
        self.runtime_dir_override = resolved_runtime_dir
        self.quarantine_dir = resolved_quarantine_dir
        self.browser_profile_dir = resolved_browser_profile_dir
        self.browser_downloads_dir = resolved_browser_downloads_dir
        self.browser_trace_dir = resolved_browser_trace_dir
        self.playwright_browsers_path = resolved_playwright_browsers_path
        self.browser_user_data_dir = resolved_browser_user_data_dir
        self.browser_operator_download_dir = resolved_browser_operator_download_dir
        self.browser_screenshot_dir = resolved_browser_screenshot_dir
        self.browser_log_path = resolved_browser_log_path
        self.visual_qa_dir = resolved_visual_qa_dir
        self.visual_qa_log_path = resolved_visual_qa_log_path
        self.self_improvement_final_reviewer = self.self_improvement_final_reviewer.strip() or "codex"
        self.self_improvement_handoff_dir = resolved_self_improvement_handoff_dir
        self.self_improvement_log_path = resolved_self_improvement_log_path
        self.codex_cli_root = resolved_codex_cli_root
        self.codex_node_executable = resolved_codex_node_executable
        self.agent_decisions_log_path = resolved_agent_decisions_log_path
        self.agent_tool_calls_log_path = resolved_agent_tool_calls_log_path
        self.shorts_output_dir = resolved_shorts_output_dir
        self.final_delivery_root = resolved_final_delivery_root
        self.shorts_tts_backend = self.shorts_tts_backend.strip().lower() or "f5_tts_http"
        self.shorts_brand_color = _normalize_hex_color(self.shorts_brand_color, fallback="#C2E5FF")
        self.shorts_brand_color_dark = _normalize_hex_color(self.shorts_brand_color_dark, fallback="#0A1628")
        self.shorts_watermark_text = self.shorts_watermark_text.strip()
        if self.elevenlabs_api_key is not None:
            self.elevenlabs_api_key = self.elevenlabs_api_key.strip() or None
        if self.elevenlabs_voice_id is not None:
            self.elevenlabs_voice_id = self.elevenlabs_voice_id.strip() or None
        self.elevenlabs_model_id = self.elevenlabs_model_id.strip() or "eleven_multilingual_v2"
        self.elevenlabs_base_url = self.elevenlabs_base_url.strip().rstrip("/") or "https://api.elevenlabs.io"
        self.elevenlabs_language_code = self.elevenlabs_language_code.strip() or "en"
        self.elevenlabs_output_format = self.elevenlabs_output_format.strip() or "mp3_44100_128"
        self.f5_tts_base_url = self.f5_tts_base_url.strip() or "http://localhost:7860/api/predict"
        self.f5_tts_transport = self.f5_tts_transport.strip().lower() or "gradio"
        if self.f5_tts_install_dir is not None:
            self.f5_tts_install_dir = self.f5_tts_install_dir.expanduser().resolve()
        if self.f5_tts_runtime_python is not None:
            self.f5_tts_runtime_python = self.f5_tts_runtime_python.expanduser().resolve()
        if self.f5_tts_ffmpeg_dir is not None:
            self.f5_tts_ffmpeg_dir = self.f5_tts_ffmpeg_dir.expanduser().resolve()
        if self.f5_tts_gradio_temp_dir is not None:
            self.f5_tts_gradio_temp_dir = self.f5_tts_gradio_temp_dir.expanduser().resolve()
        self.f5_tts_launch_command = self.f5_tts_launch_command.strip()
        if self.f5_tts_ref_audio_path is not None:
            self.f5_tts_ref_audio_path = self.f5_tts_ref_audio_path.expanduser().resolve()
        self.f5_tts_ref_text = self.f5_tts_ref_text.strip() or "Some call me nature, others call me mother nature."
        self.f5_tts_api_name = self.f5_tts_api_name.strip().strip("/") or "basic_tts"
        self.coqui_tts_model = self.coqui_tts_model.strip() or "tts_models/pl/mai_female/vits"
        self.youtube_data_api_key = self.youtube_data_api_key.strip()
        self.youtube_client_secret_file = resolved_youtube_client_secret_file
        self.youtube_upload_privacy = self.youtube_upload_privacy.strip().lower() or "private"
        if self.youtube_upload_privacy not in {"private", "unlisted", "public"}:
            self.youtube_upload_privacy = "private"
        self.youtube_default_language = self.youtube_default_language.strip() or "en"
        self.youtube_default_audio_language = self.youtube_default_audio_language.strip() or "en"
        self.youtube_category_id = self.youtube_category_id.strip() or "28"
        self.n8n_base_url = self.n8n_base_url.strip().rstrip("/") or "http://127.0.0.1:5678"
        self.n8n_data_dir = resolved_n8n_data_dir
        if self.n8n_atnos_webhook_token is not None:
            self.n8n_atnos_webhook_token = self.n8n_atnos_webhook_token.strip() or None
        if self.slack_webhook_url is not None:
            self.slack_webhook_url = self.slack_webhook_url.strip() or None
        self.workspace_staging_dir = resolved_workspace_staging_dir
        self.knowledge_dir = resolved_knowledge_dir
        self.knowledge_workspace_dir = resolved_knowledge_workspace_dir
        self.learning_archive_dir = resolved_learning_archive_dir
        self.semantic_knowledge_index_path = resolved_semantic_knowledge_index_path
        self.semantic_knowledge_embedding_model = (
            self.semantic_knowledge_embedding_model.strip() or "bge-m3"
        )
        self.piper_pl_model_path = resolved_piper_pl_model_path
        self.piper_pl_config_path = resolved_piper_pl_config_path
        self.whisper_model_path = resolved_whisper_model_path
        self.kokoro_model_path = resolved_kokoro_model_path
        self.kokoro_voices_path = resolved_kokoro_voices_path
        self.comfyui_install_dir = resolved_comfyui_install_dir
        if self.comfyui_models_dir is not None:
            self.comfyui_models_dir = self.comfyui_models_dir.resolve()
        if self.comfyui_cold_models_dir is not None:
            self.comfyui_cold_models_dir = self.comfyui_cold_models_dir.resolve()
        if self.comfyui_shared_model_paths is not None:
            self.comfyui_shared_model_paths = self.comfyui_shared_model_paths.resolve()
        if self.comfyui_input_dir is not None:
            self.comfyui_input_dir = self.comfyui_input_dir.resolve()
        if self.comfyui_output_dir is not None:
            self.comfyui_output_dir = self.comfyui_output_dir.resolve()
        self.ltx_video_model_repo = self.ltx_video_model_repo.strip() or "Lightricks/LTX-Video"
        self.ltx_video_model_dir = resolved_ltx_video_model_dir
        self.wan_video_model_repo = self.wan_video_model_repo.strip() or "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
        self.wan_video_model_dir = resolved_wan_video_model_dir
        self.video_generation_negative_prompt = (
            self.video_generation_negative_prompt.strip()
            or "worst quality, inconsistent motion, blurry, jittery, distorted"
        )
        self.wav2lip_custom_node_dir = resolved_wav2lip_custom_node_dir
        self.wav2lip_checkpoint_path = resolved_wav2lip_checkpoint_path
        self.wav2lip_face_detector_path = resolved_wav2lip_face_detector_path
        if self.piper_exe is not None:
            self.piper_exe = str(self.piper_exe).strip() or None
        if self.whisper_cpp_exe is not None:
            self.whisper_cpp_exe = str(self.whisper_cpp_exe).strip() or None
        if self.ffmpeg_exe is not None:
            self.ffmpeg_exe = str(self.ffmpeg_exe).strip() or None
        if self.atnos_ubuntu_host is not None:
            self.atnos_ubuntu_host = self.atnos_ubuntu_host.strip() or None
        self.atnos_ubuntu_user = self.atnos_ubuntu_user.strip() or "atnos"
        self.atnos_ubuntu_workdir = self.atnos_ubuntu_workdir.strip() or "/home/atnos/atnos"
        if self.atnos_ubuntu_key is not None:
            self.atnos_ubuntu_key = self.atnos_ubuntu_key.expanduser().resolve()
        if self.atnos_sandbox_host is not None:
            self.atnos_sandbox_host = self.atnos_sandbox_host.strip() or None
        if self.atnos_sandbox_host is None and self.atnos_ubuntu_host is not None:
            self.atnos_sandbox_host = self.atnos_ubuntu_host
        self.atnos_sandbox_user = self.atnos_sandbox_user.strip() or self.atnos_ubuntu_user
        self.atnos_obsidian_agent_dir = self.atnos_obsidian_agent_dir.strip() or "Atnos/Agent Logs"
        self.atnos_obsidian_decision_dir = self.atnos_obsidian_decision_dir.strip() or "Atnos/Decision Log"
        self.atnos_obsidian_shorts_dir = self.atnos_obsidian_shorts_dir.strip() or "Atnos/Shorts"
        if self.browser_runtime_executable is not None:
            self.browser_runtime_executable = self.browser_runtime_executable.resolve()
        if self.atnos_desktop_shell is not None:
            self.atnos_desktop_shell = self.atnos_desktop_shell.resolve()
        return self

    @model_validator(mode="after")
    def validate_local_first(self) -> "Settings":
        effective_database_url = self.database_url or f"sqlite:///{self.sqlite_file.resolve().as_posix()}"
        if self.deployment_mode == "local-first" and not effective_database_url.startswith("sqlite:///"):
            raise ValueError("Local-first MVP requires a SQLite database URL.")
        return self

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.sqlite_file.resolve().as_posix()}"

    @property
    def database_backend(self) -> str:
        return self.resolved_database_url.split(":", maxsplit=1)[0]

    @property
    def runtime_dir(self) -> Path:
        return (self.runtime_dir_override or (self.data_dir.parent / "runtime")).resolve()

    @property
    def runtime_env_file(self) -> Path:
        # Test applications must never inherit the operator's production env path.
        # OAuth configuration tests intentionally persist sample client IDs.
        if self.app_env == "test":
            return (self.data_dir.parent / "config" / ".env").resolve()
        configured = str(os.environ.get("ATNOS_ENV_FILE") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return (self.data_dir.parent / "config" / ".env").resolve()

    @property
    def startup_status_file(self) -> Path:
        return (self.runtime_dir / "api-startup-status.json").resolve()

    @property
    def startup_events_file(self) -> Path:
        return (self.runtime_dir / "api-startup-events.jsonl").resolve()

    @property
    def llm_models_status_file(self) -> Path:
        return (self.data_dir / "runtime" / "llm_models_status.json").resolve()

    @property
    def llm_usage_file(self) -> Path:
        return (self.data_dir / "runtime" / "usage.json").resolve()

    @property
    def resolved_account_secret_storage_backend(self) -> Literal["aes_256_gcm", "os_keyring", "metadata_only"]:
        if self.account_secret_storage_backend == "auto":
            return "aes_256_gcm"
        if self.account_secret_storage_backend == "aes_256_gcm":
            return "aes_256_gcm"
        if self.account_secret_storage_backend == "os_keyring" and sys.platform != "win32":
            return "metadata_only"
        return "metadata_only" if self.account_secret_storage_backend == "metadata_only" else "os_keyring"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "memory").mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.account_secret_vault_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_file.parent.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self.browser_downloads_dir.mkdir(parents=True, exist_ok=True)
        self.browser_trace_dir.mkdir(parents=True, exist_ok=True)
        self.playwright_browsers_path.mkdir(parents=True, exist_ok=True)
        self.browser_user_data_dir.mkdir(parents=True, exist_ok=True)
        self.browser_operator_download_dir.mkdir(parents=True, exist_ok=True)
        self.browser_screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.browser_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.browser_log_path.touch(exist_ok=True)
        self.visual_qa_dir.mkdir(parents=True, exist_ok=True)
        (self.visual_qa_dir / "captures").mkdir(parents=True, exist_ok=True)
        (self.visual_qa_dir / "recordings").mkdir(parents=True, exist_ok=True)
        (self.visual_qa_dir / "reviews").mkdir(parents=True, exist_ok=True)
        (self.visual_qa_dir / "codex-handoff").mkdir(parents=True, exist_ok=True)
        self.visual_qa_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.visual_qa_log_path.touch(exist_ok=True)
        self.self_improvement_handoff_dir.mkdir(parents=True, exist_ok=True)
        self.self_improvement_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.self_improvement_log_path.touch(exist_ok=True)
        self.agent_decisions_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.agent_decisions_log_path.touch(exist_ok=True)
        self.agent_tool_calls_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.agent_tool_calls_log_path.touch(exist_ok=True)
        (self.data_dir / "logs" / "agent_memory.jsonl").touch(exist_ok=True)
        (self.data_dir / "quality-runs").mkdir(parents=True, exist_ok=True)
        self.shorts_output_dir.mkdir(parents=True, exist_ok=True)
        self.final_delivery_root.mkdir(parents=True, exist_ok=True)
        for channel_folder in (
            "Wealth-Blueprint",
            "Archipelag-Snow",
            "Horror",
            "_inbox",
        ):
            (self.final_delivery_root / channel_folder).mkdir(parents=True, exist_ok=True)
        self.n8n_data_dir.mkdir(parents=True, exist_ok=True)
        (self.n8n_data_dir / "youtube" / "approvals" / "pending").mkdir(parents=True, exist_ok=True)
        (self.n8n_data_dir / "youtube" / "approvals" / "approved").mkdir(parents=True, exist_ok=True)
        (self.n8n_data_dir / "youtube" / "results").mkdir(parents=True, exist_ok=True)
        self.workspace_staging_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.semantic_knowledge_index_path.parent.mkdir(parents=True, exist_ok=True)
        self.knowledge_workspace_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.llm_models_status_file.parent.mkdir(parents=True, exist_ok=True)
        self.llm_usage_file.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    load_runtime_env_files()
    return Settings()
