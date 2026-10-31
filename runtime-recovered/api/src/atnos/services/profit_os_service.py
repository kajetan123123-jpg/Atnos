from __future__ import annotations

from datetime import date, timedelta

PROJECT_TEMPLATES: tuple[dict[str, object], ...] = (
    {
        "key": "content_channel",
        "label": "Content Channel",
        "summary": "Project template for YouTube, Shorts, Reels, and content backlog planning.",
        "capability_focus": ("content_brief", "channel_plan", "growth_analysis"),
        "recommended_integrations": ("youtube", "instagram", "canva", "video-editor", "tts", "analytics-generic"),
    },
    {
        "key": "creator_monetization",
        "label": "Creator Monetization",
        "summary": "Project template for creator offer planning, premium content workflows, and compliance checklists.",
        "capability_focus": ("monetization_plan", "compliance_review", "content_calendar"),
        "recommended_integrations": ("onlyfans", "creator-platform-generic", "canva", "analytics-generic"),
    },
    {
        "key": "synthetic_creator_studio",
        "label": "Synthetic Creator Studio",
        "summary": "Project template for a consent-safe synthetic creator pipeline with identity consistency, short-form video, and human approval gates.",
        "capability_focus": ("synthetic_creator_plan", "identity_consistency", "short_form_pipeline"),
        "recommended_integrations": (
            "ollama-local",
            "kokoro-local",
            "comfyui",
            "pulid-identity",
            "ltx-video",
            "wan-video",
            "wav2lip",
            "ffmpeg-amf",
            "slack",
        ),
    },
    {
        "key": "market_lab",
        "label": "Market Lab",
        "summary": "Project template for trading research, paper trading, and risk-controlled experiment tracking.",
        "capability_focus": ("market_research_plan", "risk_checklist", "journaling"),
        "recommended_integrations": ("tradingview", "xtb", "broker-generic", "market-data-generic"),
    },
    {
        "key": "growth_experiment",
        "label": "Growth Experiment",
        "summary": "Project template for KPI tracking, content experiments, and manual analytics reviews.",
        "capability_focus": ("growth_hypothesis", "experiment_tracking", "roi_review"),
        "recommended_integrations": ("analytics-generic", "youtube", "instagram"),
    },
    {
        "key": "self_improvement",
        "label": "Self Improvement",
        "summary": "Project template for Improvement Notes, selected review loops, and self-coding drafts.",
        "capability_focus": ("selected_note_review", "self_coding_draft", "roadmap_planning"),
        "recommended_integrations": ("obsidian", "github", "notion"),
    },
)


class ProfitOsService:
    def list_project_templates(self) -> list[dict[str, object]]:
        return [dict(item) for item in PROJECT_TEMPLATES]

    def build_content_brief(
        self,
        *,
        platform: str,
        topic: str,
        audience: str,
        goal: str,
        tone: str | None,
        format_hint: str | None,
        project_id: int | None,
    ) -> dict[str, object]:
        hook = f"{self._platform_label(platform)} hook: start from the audience pain around {topic} and promise a concrete outcome in under 8 seconds."
        angle = f"Frame {topic} as a useful next step for {audience}, optimized around the goal: {goal}."
        outline = [
            "Open with a precise hook and one problem statement.",
            "Show the contrarian or practical angle quickly.",
            "Deliver 3-5 concrete points with examples or proof.",
            "Close with one next action for the viewer and one follow-up content idea.",
        ]
        script_sections = [
            "Hook",
            "Problem framing",
            "Core insight",
            "Examples / proof",
            "Call to action",
        ]
        asset_list = [
            "Reference notes or source bullets",
            "A-roll talking points",
            "B-roll or visuals list",
            "Thumbnail text options",
        ]
        title_options = [
            f"{self._title_case(topic)}: the practical playbook for {audience}",
            f"Stop guessing about {topic} and do this instead",
            f"{self._title_case(topic)} in a way that actually drives {goal}",
        ]
        risks = [
            "Do not imply guaranteed results, revenue, or growth.",
            "Do not claim platform access, analytics access, or publishing ability.",
            "Avoid bloated intros and unsupported claims.",
        ]
        return {
            "platform": platform,
            "topic": topic,
            "audience": audience,
            "goal": goal,
            "hook": hook,
            "angle": angle,
            "outline": outline,
            "script_sections": script_sections,
            "asset_list": asset_list,
            "thumbnail_brief": f"One bold visual around {topic}, one short promise line, and one contrast element that signals the angle immediately.",
            "title_options": title_options,
            "description_draft": f"Short value-first description for {audience}. Clarify what the piece covers, why it matters now, and what the viewer should do next.",
            "quality_checklist": [
                "Hook is specific inside the first 8 seconds.",
                "Every section supports the same audience and goal.",
                "No unsupported guarantees or fake urgency.",
                "Thumbnail and title match the real content angle.",
                "CTA is explicit but not spammy.",
            ],
            "external_apps_needed": self._content_apps_for_platform(platform),
            "risks": risks,
            "next_step": f"Turn this brief into a script draft and thumbnail brief for {self._platform_label(platform)}.",
            "safety_notes": [
                "Planning and drafting only. No publishing, upload, or public action in v0.",
                "No external app call is made by this endpoint.",
                f"Project context remains optional{' (project_id=' + str(project_id) + ')' if project_id else ''}.",
                f"Use tone `{tone or 'clear and practical'}` and format `{format_hint or 'standard short-form or long-form draft'}` only as writing guidance.",
            ],
        }

    def build_channel_plan(
        self,
        *,
        platform: str,
        niche: str,
        audience: str,
        monetization_goal: str,
        posting_frequency: str | None,
        language: str | None,
        project_id: int | None,
    ) -> dict[str, object]:
        cadence = posting_frequency or self._default_cadence_for_platform(platform)
        return {
            "niche": niche,
            "audience": audience,
            "positioning": f"A practical {self._platform_label(platform)} channel for {audience}, focused on {niche} and aligned to monetization goal: {monetization_goal}.",
            "content_pillars": [
                f"Foundational education in {niche}",
                f"Case-study or teardown content for {audience}",
                "Experiment logs and lessons learned",
                "Opinionated frameworks and checklists",
            ],
            "series_ideas": [
                f"{self._title_case(niche)} breakdowns",
                "Before / after experiments",
                "Weekly mistakes and lessons",
                "Playbook episodes",
            ],
            "first_10_video_ideas": [
                f"Why most people fail at {niche}",
                f"The 80/20 system for {audience}",
                f"Three myths about {niche}",
                f"A beginner workflow for {niche}",
                f"One week experiment: testing {niche}",
                f"What to stop doing if you want {monetization_goal}",
                f"Case study: auditing a weak {niche} strategy",
                f"The content engine behind consistent {niche} output",
                f"How to measure whether {niche} is working",
                f"My next 30 days plan for {niche}",
            ],
            "publishing_cadence": cadence,
            "monetization_paths": [
                "Audience trust -> offer ladder",
                "Low-ticket info or template product",
                "Service or consulting upsell",
                "Affiliate or sponsor alignment only after audience fit",
            ],
            "growth_experiments": [
                "Hook format A/B testing",
                "Title promise clarity testing",
                "Retention check for first 30 seconds",
                "Series consistency vs. one-off topics",
            ],
            "quality_bar": [
                "Every piece must fit one pillar.",
                "Every idea must be monetization-aware but not sales-first.",
                "No publishing automation or fake virality tactics.",
            ],
            "required_integrations": self._content_apps_for_platform(platform),
            "risks": [
                "Do not assume audience demand without testing.",
                "Do not conflate views with revenue quality.",
                "Do not create too many pillars in v0.",
            ],
            "next_7_days_plan": [
                "Define one niche sentence and one audience sentence.",
                "Pick 3 content pillars only.",
                "Draft the first 3 titles and hooks.",
                "Prepare one thumbnail brief template.",
                "Set one manual analytics review cadence.",
            ],
            "safety_notes": [
                "Planning only. No channel creation, no account creation, no posting, and no external calls.",
                f"Language guidance: {language or 'default to the project language'}.",
                f"Project context remains optional{' (project_id=' + str(project_id) + ')' if project_id else ''}.",
            ],
        }

    def build_creator_monetization_plan(
        self,
        *,
        platform: str,
        creator_positioning: str,
        audience: str,
        content_boundaries: str,
        monetization_goal: str,
        posting_frequency: str | None,
        project_id: int | None,
    ) -> dict[str, object]:
        cadence = posting_frequency or "4x weekly"
        sanitized_boundaries = (
            "Use creator-defined adult-safe boundaries only. Keep all output non-explicit, consensual, legal, and privacy-safe."
            if content_boundaries.strip()
            else "Define explicit legal and consent-safe boundaries before any monetization workflow."
        )
        brand_core = {
            "persona_hook": f"{creator_positioning} as a premium, high-retention 18+ creator brand built around anticipation, exclusivity, and repeat attention.",
            "emotional_promise": "The audience should feel noticed, teased, and pulled toward the next unlock without feeling spammed or manipulated.",
            "business_goal": monetization_goal,
        }
        bio_pack = [
            f"{creator_positioning} | soft tease, premium mood, no rushed energy",
            f"{creator_positioning} | late replies, sharp timing, premium access",
            f"{creator_positioning} | cute energy, high attention, members-first drops",
        ]
        voice_tone_pack = {
            "tone_words": ["playful", "warm", "confident", "selective", "premium"],
            "sentence_style": "Short, direct, intimate lines with a clear subtext and no over-explaining.",
            "dm_energy": "Escalate curiosity slowly. Do not sound desperate or robotic.",
            "brand_rule": "Every message should feel like a continuation of the same persona, not a random promo blast.",
        }
        visual_identity = {
            "color_palette": ["cream", "soft pink", "deep plum", "warm black"],
            "lighting": ["soft lamp glow", "late-night side light", "clean bathroom mirror light"],
            "texture_keywords": ["skin glow", "soft fabric", "glass reflection", "bedroom depth"],
            "camera_language": ["mirror selfie", "tight crop", "waist-up tease", "close face-and-hand framing"],
        }
        wardrobe_scene_direction = {
            "wardrobe": [
                "oversized hoodie with bare-leg contrast",
                "soft lounge set",
                "fitted crop top with low-key jewelry",
                "sleepwear-inspired premium basics",
            ],
            "scene_setups": [
                "bedroom lamp and soft sheets",
                "mirror + phone capture with shallow mess that still feels curated",
                "desk/setup night mood",
                "bathroom mirror or doorway reveal framing",
            ],
            "props": ["phone", "headphones", "blanket", "lip gloss", "energy drink", "controller"],
        }
        content_ideas = [
            "Soft morning mirror teaser",
            "Late-night hoodie reveal",
            "Close-up eye contact hook",
            "Mirror check with premium caption",
            "After-shower glow tease",
            "Bed-edge slow pan concept",
            "Gamergirl desk-night teaser",
            "Reply bait: pick my next look",
            "Locked-content countdown post",
            "Members-only weekend drop teaser",
            "Voice-note style caption post",
            "Low-light silhouette tease",
            "Cute but bratty text overlay concept",
            "One-room luxury mood set",
            "Towel/hair-wrap premium tease",
            "Soft focus couch pose concept",
            "Glass reflection close crop",
            "Comeback-after-quiet tease",
            "New set tonight countdown",
            "Poll-driven outfit pick teaser",
            "Playful brat challenge caption",
            "Feet-up lounge framing concept",
            "Doorway reveal still",
            "Hand-on-neck close crop",
            "Morning coffee + eye contact concept",
            "Rainy-night window mood post",
            "Red-light late-night set teaser",
            "Camera-ready bathroom mirror angle",
            "Members-first bundle reminder",
            "Last-call upsell teaser",
        ]
        content_calendar = [
            {"day": day, "focus": focus, "goal": goal}
            for day, focus, goal in [
                (1, "Teaser post", "Open the 14-day arc with one unmistakable visual hook."),
                (2, "Retention post", "Deepen persona attachment without heavy selling."),
                (3, "Paywall trigger", "Introduce scarcity around the next premium drop."),
                (4, "Soft BTS", "Make the creator feel real and ambient, not over-produced."),
                (5, "Poll / interaction", "Collect intent signals and segment warm fans."),
                (6, "Premium reminder", "Reframe the paid offer as access, not pressure."),
                (7, "Comeback post", "Reset attention with a stronger tease than day 1."),
                (8, "Mood post", "Build atmosphere and visual consistency."),
                (9, "Retention post", "Reward returning viewers with an inside-feeling update."),
                (10, "Bundle teaser", "Prime a higher-value offer without sounding salesy."),
                (11, "Character post", "Push the strongest personality trait harder."),
                (12, "Paywall trigger", "Run a sharper premium CTA with limited-time language."),
                (13, "Reactivation", "Pull back lapsed viewers with a cleaner promise."),
                (14, "Best-of closer", "Close the cycle and preview the next one."),
            ]
        ]
        funnel = {
            "teaser": "Use one striking still, one emotionally charged line, and one reason to come back tonight.",
            "retention_post": "Reward attention with intimacy, consistency, and a feeling of access.",
            "paywall_trigger": "Move from intrigue to offer with scarcity, timing, and a clear upgrade path.",
            "comeback_post": "Reactivate lapsed attention with a stronger image, cleaner promise, and simpler CTA.",
        }
        pricing_ladder = [
            "Base subscription entry point",
            "Premium recurring tier or bundle access",
            "PPV premium set",
            "Timed bundle offer",
            "High-intent custom-style premium request handled manually",
        ]
        upsell_ladder = [
            "Subscriber -> PPV opener",
            "PPV buyer -> bundle buyer",
            "Bundle buyer -> recurring premium buyer",
            "Lapsed buyer -> comeback offer",
        ]
        cta_library = [
            "Tonight gets better behind the wall.",
            "If you liked this mood, the full set is already waiting.",
            "The softer version stays public. The better version does not.",
            "You can stay curious, or you can unlock the rest.",
            "Members saw this first. They will see the next one first too.",
            "This is the warning shot, not the full drop.",
            "The next post is not for everyone.",
            "If you want the full vibe, you know where it is.",
        ]
        ppv_bundle_strategy = {
            "ppv_angle": "Use PPV for the sharpest visual payoff or strongest exclusivity moment.",
            "bundle_angle": "Stack related moods or looks into one higher-conviction premium package.",
            "cadence_rule": "Do not push PPV every day. Protect novelty.",
        }
        fan_segmentation = {
            "new": "Needs fast clarity about persona, mood, and what makes the paid layer better.",
            "warm": "Already responds to vibe; push routine and anticipation.",
            "high_intent": "Gets cleaner scarcity and stronger offer framing.",
            "lapsed": "Needs a comeback angle, not the same CTA repeated.",
        }
        churn_reduction_playbook = [
            "Rotate persona angles without breaking the core identity.",
            "Alternate tease, mood, and payoff instead of repeating one promo format.",
            "Use returning-viewer style callbacks in captions and bundles.",
            "Keep premium drops paced so fans do not feel milked.",
        ]
        promo_angles = [
            "soft luxury girlfriend energy",
            "late-night private mood",
            "you missed this set",
            "members-first access",
            "sweet face, sharper intent",
            "cute outside, selective inside",
        ]
        prompt_pack = [
            "Premium late-night bedroom lighting, soft lamp glow, cute girlfriend energy, shallow depth of field, eye contact, non-explicit, editorial-quality portrait",
            "Mirror selfie with oversized hoodie, warm pink and cream palette, premium creator mood, non-explicit tease, realistic skin texture, mobile-first framing",
            "Bathroom mirror glow, wet hair, minimal styling, intimate but non-explicit, polished creator-brand aesthetic, close crop",
            "Desk setup at night, gamer-girl comfort mood, soft neon spill, playful expression, non-explicit, premium social teaser frame",
            "Cozy bed-edge composition, blanket texture, relaxed pose, soft eye contact, high-retention creator branding, non-explicit",
        ]
        return {
            "platform": platform,
            "creator_positioning": creator_positioning,
            "audience": audience,
            "content_boundaries": sanitized_boundaries,
            "brand_core": brand_core,
            "bio_pack": bio_pack,
            "voice_tone_pack": voice_tone_pack,
            "visual_identity": visual_identity,
            "wardrobe_scene_direction": wardrobe_scene_direction,
            "offer_ladder": [
                "Entry subscription offer",
                "Premium recurring access",
                "Limited, policy-compliant bonus offer",
                "Retention-focused member experience",
            ],
            "content_calendar": [
                f"{cadence} planning cadence",
                "One recurring pillar for retention",
                "One behind-the-scenes or process update",
                "One conversion-focused but non-spammy post idea",
            ],
            "content_ideas": content_ideas,
            "content_calendar_14_day": content_calendar,
            "funnel": funnel,
            "pricing_ladder": pricing_ladder,
            "upsell_ladder": upsell_ladder,
            "cta_library": cta_library,
            "ppv_bundle_strategy": ppv_bundle_strategy,
            "fan_segmentation": fan_segmentation,
            "churn_reduction_playbook": churn_reduction_playbook,
            "promo_angles": promo_angles,
            "prompt_pack": prompt_pack,
            "fan_engagement_guidelines": [
                "Reply cadence should be manual and consent-aware.",
                "No automated fan messages in v0.",
                "Do not pressure, manipulate, or over-promise.",
            ],
            "compliance_checklist": [
                "18+ only",
                "Explicit consent required",
                "Legal in the relevant jurisdiction",
                "No impersonation or deceptive identity",
                "No platform rule bypassing",
            ],
            "privacy_checklist": [
                "Do not expose private addresses or personal identifiers",
                "Separate creator identity and operational identity when needed",
                "Review metadata, backgrounds, and upload artifacts before publishing",
                "Keep audience records and payment discussions off unsafe channels",
            ],
            "safety_notes": [
                "This endpoint does not generate explicit sexual content and keeps the output non-explicit.",
                "No public posting, no automated fan messaging, and no account connection in v0.",
                "Use this output only as business planning and compliance structure.",
                f"Project context remains optional{' (project_id=' + str(project_id) + ')' if project_id else ''}.",
            ],
            "required_integrations": ["onlyfans", "creator-platform-generic", "canva", "analytics-generic"],
            "risks": [
                "Platform compliance failure",
                "Privacy leakage",
                "Over-reliance on monetization before positioning is clear",
            ],
            "next_7_days_plan": [
                "Define positioning and safe content boundaries in writing.",
                "Create one offer ladder and one pricing hypothesis.",
                "Draft one manual posting calendar.",
                "Prepare one compliance review checklist before any public action.",
            ],
        }

    def build_synthetic_creator_plan(
        self,
        *,
        primary_platform: str,
        creator_concept: str,
        audience: str,
        visual_goal: str,
        content_boundaries: str,
        identity_mode: str,
        runtime_preference: str | None,
        project_id: int | None,
    ) -> dict[str, object]:
        normalized_runtime = runtime_preference or "windows_native"
        platform_label = self._platform_label(primary_platform) if primary_platform != "multi" else "multi-platform short-form"
        identity_guardrail = (
            "Use a consented digital twin only when written permission and platform verification requirements are satisfied."
            if identity_mode == "consented_digital_twin"
            else "Use a fully original synthetic persona. No real-person likeness, no celebrity likeness, and no minor-coded presentation."
        )
        priority_matrix = [
            {
                "component": "LangGraph orchestration",
                "impact": "critical",
                "difficulty": "medium",
                "recommendation": "Use as the workflow layer over existing ATNOS jobs, approvals, and artifact state.",
            },
            {
                "component": "Memory adapters",
                "impact": "high",
                "difficulty": "medium",
                "recommendation": "Keep current project memory plus Obsidian in M1; treat Mem0 or Genesys as future adapters, not hard dependencies.",
            },
            {
                "component": "Kokoro Polish TTS",
                "impact": "high",
                "difficulty": "low",
                "recommendation": "Use Kokoro-ONNX with a Polish voice profile for fast local speech and preview loops.",
            },
            {
                "component": "Local text model",
                "impact": "critical",
                "difficulty": "low",
                "recommendation": "Route scriptwriting through Ollama with a Bielik-class Polish profile when available, but keep the runtime model-configurable.",
            },
            {
                "component": "Fast video generation",
                "impact": "medium",
                "difficulty": "high",
                "recommendation": "Use LTX-Video as the fast teaser engine for 2-8 second short assets.",
            },
            {
                "component": "High-quality video generation",
                "impact": "medium",
                "difficulty": "high",
                "recommendation": "Keep Wan as an optional quality adapter, not a blocker for MVP delivery.",
            },
            {
                "component": "Official social APIs",
                "impact": "medium",
                "difficulty": "high",
                "recommendation": "Use official publish paths only after approval and connector readiness checks.",
            },
        ]
        runtime_stack = {
            "orchestration": {
                "primary": "LangGraph over existing ATNOS workflow services",
                "why": "Keeps campaign state deterministic without rewriting Chat.",
            },
            "memory": {
                "mvp": "Current project memory plus Obsidian visible memory",
                "future_adapter_path": ["Mem0", "Genesys"],
                "why": "Avoids introducing another central subsystem before the core creator loop is stable.",
            },
            "text_llm": {
                "primary": "Ollama local routing",
                "recommended_profile": "Bielik-class Polish model when installed",
                "fallback": "Current cloud or browser-fallback advisor path",
            },
            "voice": {
                "primary": "Kokoro-ONNX Polish pack",
                "recommended_voice": "pl_maja",
                "provider": "DmlExecutionProvider or Windows-native fallback",
            },
            "image_identity": {
                "primary": "ComfyUI + Flux + PuLID",
                "reference_input": "reference_portrait.jpg or a consented verified source image",
                "goal": "Keep facial identity, styling, and silhouette stable across stills.",
            },
            "video_fast": {
                "primary": "LTX-Video",
                "target_use": "Fast teaser loops and short-form iteration passes",
                "target_duration": "2-8 seconds",
            },
            "video_quality": {
                "primary": "Wan 2.x quality adapter",
                "status": "optional_experimental",
                "target_use": "Premium motion passes after the fast loop is approved",
            },
            "lip_sync": {
                "primary": "Wav2Lip",
                "target_use": "Talking-head or avatar sync after audio approval",
            },
            "finishing": {
                "primary": "FFmpeg h264_amf + film grain finishing pass",
                "goal": "Reduce the waxy AI look and keep export practical on Windows/AMD.",
            },
        }
        media_pipeline = [
            "Reference portrait and boundaries review",
            "Identity-stable still generation in ComfyUI",
            "Short script and hook generation",
            "Kokoro Polish voice generation",
            "Fast teaser generation with LTX-Video",
            "Optional premium pass with Wan after approval",
            "Lip-sync pass with Wav2Lip when speech is required",
            "AMF export and film-grain finishing",
            "Human approval before any publish or external routing",
        ]
        vram_policy = {
            "default_runtime": "windows_native",
            "runtime_preference": normalized_runtime,
            "ollama_unload_rule": "Unload large Ollama models before Wan or other heavy video passes.",
            "fast_path": "Prefer LTX-Video for iterative drafts.",
            "quality_path": "Use Wan only after the script, stills, and voice are approved.",
            "rocm_note": "ROCm can be explored as a second backend later; it is not required for MVP on this machine.",
        }
        approval_flow = {
            "draft_gate": "Approve still identity sheet before generating a batch of assets.",
            "audio_gate": "Approve voice tone before lip-sync or publish packaging.",
            "publish_gate": "Send a manual approval package through Slack first; Discord can be a webhook-based future option.",
            "platform_gate": "Use official publish connectors only after connector health and policy readiness are green.",
        }
        content_tracks = [
            "Identity-consistent photo sets",
            "Short teaser loops for YouTube Shorts, Reels, and TikTok",
            "Member-funnel preview assets",
            "Voice-led short clips with subtitles and a clean premium look",
        ]
        integrations_to_connect = [
            "ollama-local",
            "kokoro-local",
            "comfyui",
            "pulid-identity",
            "ltx-video",
            "wan-video",
            "wav2lip",
            "ffmpeg-amf",
            "slack",
            "discord-webhook",
            "obsidian",
            "analytics-generic",
        ]
        return {
            "primary_platform": primary_platform,
            "platform_label": platform_label,
            "creator_concept": creator_concept,
            "audience": audience,
            "visual_goal": visual_goal,
            "content_boundaries": "Keep outputs legal, consensual, age-gated, privacy-safe, and non-impersonating. This plan does not authorize explicit asset generation by default.",
            "identity_mode": identity_mode,
            "identity_guardrail": identity_guardrail,
            "priority_matrix": priority_matrix,
            "runtime_stack": runtime_stack,
            "media_pipeline": media_pipeline,
            "vram_policy": vram_policy,
            "approval_flow": approval_flow,
            "content_tracks": content_tracks,
            "launch_sequence": [
                "Define persona sheet, consent boundaries, and disclosure policy.",
                "Generate a stable still pack before any motion work.",
                "Validate hook, voice, and visual consistency with one teaser loop.",
                "Only then expand into a repeatable short-form batch.",
            ],
            "integrations_to_connect": integrations_to_connect,
            "required_integrations": integrations_to_connect,
            "risks": [
                "Identity drift across generated sets",
                "VRAM pressure from running text generation and video generation together",
                "Synthetic creator compliance drift if approval gates are skipped",
                "Platform rejection if disclosure or verification requirements are ignored",
            ],
            "next_14_days_plan": [
                "Day 1-2: define persona sheet, consent basis, and a verified reference portrait workflow.",
                "Day 3-4: connect ComfyUI, Kokoro, and Ollama local routing.",
                "Day 5-6: generate the first identity sheet and review stability.",
                "Day 7-8: generate one short teaser with subtitles and voice.",
                "Day 9-10: compare fast LTX output against a higher-quality pass.",
                "Day 11-12: wire Slack approval and artifact review.",
                "Day 13-14: prepare a manual-ready publish package and document lessons in Obsidian.",
            ],
            "safety_notes": [
                "This endpoint is for lawful planning only. It does not enable impersonation, minors, non-consensual content, or covert adult automation.",
                "Treat explicit generation, if ever considered later, as a separate gated policy question rather than a default capability.",
                "Use digital-twin flows only with written consent and platform-safe verification requirements.",
                f"Project context remains optional{' (project_id=' + str(project_id) + ')' if project_id else ''}.",
            ],
        }

    def build_market_research_plan(
        self,
        *,
        market: str,
        strategy_hypothesis: str,
        timeframe: str,
        risk_budget: str,
        project_id: int | None,
    ) -> dict[str, object]:
        review_date = (date.today() + timedelta(days=7)).isoformat()
        watchlist = {
            "crypto": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "forex": ["EURUSD", "GBPUSD", "USDJPY"],
            "indices": ["US100", "SPX500", "GER40"],
        }.get(market, ["PRIMARY-ASSET", "SECONDARY-ASSET", "WATCH-ASSET"])
        timeframes = [
            item.strip()
            for item in (timeframe.replace(",", "/").split("/") if timeframe.strip() else ["D1", "H4", "H1"])
            if item.strip()
        ]
        setup_families = [
            {
                "name": "trend_continuation",
                "conditions": "Higher timeframe trend remains intact and lower timeframe pullback stays controlled.",
                "entry_focus": "Continuation after reclaim, not blind chase.",
                "invalidation": "Structure break against the trend or weak reclaim volume.",
            },
            {
                "name": "range_rejection",
                "conditions": "Price rejects a range edge with visible failure to accept outside the level.",
                "entry_focus": "Take only clean rejection, not a midpoint guess.",
                "invalidation": "Acceptance outside the range or fast re-entry into chop.",
            },
            {
                "name": "breakout_retest",
                "conditions": "Clear break of key structure followed by a retest that holds.",
                "entry_focus": "Retest confirmation over impulse entry.",
                "invalidation": "Retest fails and price closes back inside prior structure.",
            },
        ]
        risk_framework = {
            "max_risk_per_trade": "0.5-1.0% of paper capital",
            "daily_loss_limit": "Stop after 2 invalid trades or 2R drawdown on the day.",
            "weekly_loss_limit": "Pause the setup family after 5R drawdown in a week.",
            "averaging_down_rule": "No averaging down unless a predefined multi-entry plan existed before the idea.",
            "execution_bias_rule": "No trade when the setup exists only because of urgency or FOMO.",
        }
        journal_template = {
            "before_trade": ["bias", "setup_family", "timeframe_alignment", "invalidation", "risk_size"],
            "during_trade": ["management_note", "did_structure_change", "emotional_state"],
            "after_trade": ["outcome", "mistake_or_strength", "what_repeats_next_time"],
        }
        weekly_review_template = [
            "List the cleanest setups and what made them clean.",
            "List the ugliest trades and which rule was ignored.",
            "Identify one setup family to keep pressing and one to pause.",
            "Rewrite one rule if the data shows a repeated failure mode.",
        ]
        paper_trading_playbook = [
            "Trade only one setup family at a time until 20 logged observations exist.",
            "Use screenshots before and after every idea.",
            "Grade every trade by process quality, not by PnL alone.",
            "If the setup is unclear, log the miss instead of forcing an entry.",
        ]
        return {
            "market": market,
            "hypothesis": strategy_hypothesis,
            "timeframe": timeframe,
            "risk_budget": risk_budget,
            "watchlist": watchlist,
            "timeframes": timeframes or ["D1", "H4", "H1"],
            "setup_families": setup_families,
            "risk_framework": risk_framework,
            "assumptions": [
                "The hypothesis has a measurable edge only if data supports it.",
                "Paper trading comes before capital deployment.",
                "Market regime can invalidate historical assumptions quickly.",
            ],
            "data_to_collect": [
                "Entry context screenshots or notes",
                "Outcome metrics per setup",
                "Regime notes and macro context",
                "Execution mistakes and emotional state",
            ],
            "indicators_to_review": [
                "Market structure",
                "Volume or liquidity context",
                "Volatility regime",
                "Timeframe alignment",
            ],
            "paper_trading_plan": [
                "Run the hypothesis on paper first",
                "Define one setup and one invalidation rule",
                "Track at least 20 observations before any escalation",
            ],
            "paper_trading_playbook": paper_trading_playbook,
            "journal_template": journal_template,
            "risk_checklist": [
                "Max loss per idea defined in advance",
                "No revenge behavior",
                "No leverage recommendation in v0",
                "No trade if the setup is unclear",
            ],
            "entry_checklist": [
                "Is the setup family clear?",
                "Is higher timeframe context aligned?",
                "Is invalidation defined before entry?",
                "Is risk size within the budget?",
                "Would I still take this trade if I had to screenshot and explain it later?",
            ],
            "exit_checklist": [
                "Did price hit the invalidation or target logic?",
                "Did I move the stop emotionally?",
                "Did I scale out according to plan or because of fear?",
                "Does the post-trade review match the original setup family?",
            ],
            "invalidation_rules": [
                "If the setup no longer matches the hypothesis, the idea is invalid.",
                "If risk budget is unclear, no action is taken.",
                "If data quality is weak, the experiment pauses.",
            ],
            "no_trade_conditions": [
                "News event risk is too high",
                "Liquidity is thin or spreads are abnormal",
                "The setup exists only because of FOMO",
                "The plan would violate the risk budget",
            ],
            "next_review_date": review_date,
            "weekly_review_template": weekly_review_template,
            "safety_notes": [
                "Research and paper trading only. No live trading and no brokerage connection in v0.",
                "This is not financial advice and does not guarantee profit.",
                "No buy/sell now instruction is generated by this endpoint.",
                f"Project context remains optional{' (project_id=' + str(project_id) + ')' if project_id else ''}.",
            ],
        }

    @staticmethod
    def _platform_label(platform: str) -> str:
        mapping = {
            "youtube": "YouTube",
            "youtube_short": "YouTube Short",
            "instagram_reel": "Instagram Reel",
            "tiktok": "TikTok",
        }
        return mapping.get(platform, platform)

    @staticmethod
    def _content_apps_for_platform(platform: str) -> list[str]:
        base = ["canva", "video-editor", "tts", "analytics-generic"]
        if platform in {"youtube", "youtube_short"}:
            return ["youtube", *base]
        if platform == "instagram_reel":
            return ["instagram", *base]
        if platform == "tiktok":
            return ["tiktok", *base]
        return base

    @staticmethod
    def _default_cadence_for_platform(platform: str) -> str:
        if platform in {"youtube_short", "instagram_reel", "tiktok"}:
            return "4-5 short-form posts per week"
        return "2 long-form videos per week"

    @staticmethod
    def _title_case(value: str) -> str:
        return " ".join(part.capitalize() for part in value.split())
