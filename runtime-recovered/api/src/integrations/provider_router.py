from __future__ import annotations

from integrations.integration_models import ProviderDecision


SAFETY_DEFAULTS = [
    "Do not publish without explicit approval.",
    "Do not spend money without explicit approval.",
    "Do not use API keys when required environment variables are empty.",
    "Browser SaaS bridges are fallback paths, not the core backend.",
]


def choose_provider(task: str) -> ProviderDecision:
    normalized = " ".join(task.lower().strip().split())

    if _has_any(normalized, ["ask all", "multi-model", "multimodel", "porownaj", "compare"]) and _has_any(
        normalized, ["gpt", "claude", "gemini", "models", "modele"]
    ):
        return ProviderDecision(
            provider_id="litellm_gateway",
            task_category="model_reasoning",
            confidence=0.92,
            reason="Multi-model reasoning should go through the central LiteLLM gateway.",
            fallback_provider_ids=["local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["reasoning", "model reasoning", "llm", "gpt", "claude", "gemini"]):
        return ProviderDecision(
            provider_id="litellm_gateway",
            task_category="model_reasoning",
            confidence=0.84,
            reason="Model routing belongs behind LiteLLM so Ollama, GPT, Claude, and Gemini stay swappable.",
            fallback_provider_ids=["local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["youtube", "upload", "publish", "publikuj", "analytics", "analityka"]):
        return ProviderDecision(
            provider_id="youtube_api",
            task_category="publishing",
            confidence=0.9,
            reason="Uploads and analytics belong to the official YouTube API integration.",
            requires_approval=True,
            fallback_provider_ids=[],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["zebracat", "premium video", "premium generation", "external video"]):
        return ProviderDecision(
            provider_id="zebracat_api",
            task_category="premium_video_generation",
            confidence=0.93,
            reason="The task explicitly asks for premium external video generation.",
            requires_approval=True,
            fallback_provider_ids=["local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["rendi", "cloud ffmpeg", "ffmpeg api", "remote ffmpeg", "external ffmpeg"]):
        return ProviderDecision(
            provider_id="rendi_ffmpeg_mcp",
            task_category="cloud_ffmpeg_rendering",
            confidence=0.91,
            reason="The task explicitly asks for Rendi or external FFmpeg processing.",
            requires_approval=True,
            fallback_provider_ids=["local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["subscription", "ui quality", "quality gate", "chatgpt ui", "claude ui", "gemini ui"]):
        return ProviderDecision(
            provider_id="chatgpt_ui_bridge",
            task_category="subscription_ui_quality_gate",
            confidence=0.74,
            reason="Subscription UI checks are fallback browser bridges with manual login and no auto-send by default.",
            requires_approval=True,
            fallback_provider_ids=["claude_ui_bridge", "gemini_ui_bridge"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["klap", "opuseclip", "opus clip", "repurpose", "long video to shorts"]):
        return ProviderDecision(
            provider_id="klap_browser_recipe",
            task_category="repurpose_saas_browser_workflow",
            confidence=0.84,
            reason="Klap is a browser-only SaaS workflow for extracting and captioning shorts from long videos.",
            requires_approval=True,
            fallback_provider_ids=["local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["autoshorts", "automaticshorts", "auto shorts", "auto-post faceless", "faceless autopilot"]):
        return ProviderDecision(
            provider_id="autoshorts_browser_recipe",
            task_category="faceless_series_saas_research",
            confidence=0.83,
            reason="AutoShorts-style tools are useful for series/workflow benchmarking, but auto-posting must stay disabled.",
            requires_approval=True,
            fallback_provider_ids=["autoaishorts_browser_recipe", "local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["autoaishorts", "auto ai shorts", "ai images", "dynamic captions", "faceless generator"]):
        return ProviderDecision(
            provider_id="autoaishorts_browser_recipe",
            task_category="faceless_video_saas_benchmark",
            confidence=0.82,
            reason="AutoAIShorts is a browser-only benchmark for AI images, voiceovers, b-roll and dynamic captions.",
            requires_approval=True,
            fallback_provider_ids=["local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["elevenlabs", "eleven labs", "voiceover", "premium voice", "tts"]):
        return ProviderDecision(
            provider_id="elevenlabs_browser_recipe",
            task_category="premium_voice_browser_workflow",
            confidence=0.86,
            reason="ElevenLabs should be used for approved premium narration/voice audition, not for silent publishing.",
            requires_approval=True,
            fallback_provider_ids=["local_short_pipeline"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["videdge", "facelessreels", "saas inspiration", "inspiration", "research niche"]):
        return ProviderDecision(
            provider_id="videdge_browser_recipe",
            task_category="saas_inspiration_research",
            confidence=0.78,
            reason="VidEdge and FacelessReels are browser-only research recipes, not core backend providers.",
            requires_approval=True,
            fallback_provider_ids=["facelessreels_browser_recipe"],
            safety_notes=SAFETY_DEFAULTS,
        )

    if _has_any(normalized, ["short", "shorta", "reel", "tiktok", "vertical video", "9:16", "render"]):
        return ProviderDecision(
            provider_id="local_short_pipeline",
            task_category="local_short_render",
            confidence=0.86,
            reason="Short-form video defaults to the local pipeline unless the user explicitly selects an external provider.",
            fallback_provider_ids=["zebracat_api"],
            safety_notes=SAFETY_DEFAULTS,
        )

    return ProviderDecision(
        provider_id="local_short_pipeline",
        task_category="default_local",
        confidence=0.55,
        reason="No external integration intent detected; keep execution local by default.",
        fallback_provider_ids=["litellm_gateway"],
        safety_notes=SAFETY_DEFAULTS,
    )


def _has_any(value: str, needles: list[str]) -> bool:
    return any(needle in value for needle in needles)
