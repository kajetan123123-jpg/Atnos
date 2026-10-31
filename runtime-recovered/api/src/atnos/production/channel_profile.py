from __future__ import annotations

from dataclasses import dataclass

from atnos.production.models import ProductionContext


@dataclass(frozen=True, slots=True)
class ProductionChannelProfile:
    key: str
    content_label: str
    language_instruction: str
    prompt_label: str
    visual_style: str
    hook_opening_visual: str
    intro_visual: str
    body_visual: str
    cta_visual: str
    default_cta: str
    title_options: tuple[str, str]
    tags: tuple[str, ...]
    upload_tags: tuple[str, ...]
    thumbnail_style: str
    thumbnail_prompt: str
    video_motion: str
    fallback_scene_prompt: str
    risk_notes: str
    language_code: str = "en"
    edge_tts_voice: str = ""
    edge_tts_rate: str = ""
    kokoro_voice: str = ""
    kokoro_speed: float = 1.0
    narration_style: str = "clear documentary narrator"
    target_loudness_lufs: float = -14.0


FINANCE_PROFILE = ProductionChannelProfile(
    key="finance",
    content_label="personal finance",
    language_instruction=(
        "English language for a US/global audience. Use clear, natural English. "
        "Do not use Polish words."
    ),
    prompt_label="faceless personal finance and money education short",
    visual_style="cinematic finance, dark premium, faceless educational",
    hook_opening_visual="A single coin trapped inside a glass jar under a hard spotlight, premium editorial macro photograph.",
    intro_visual="An open wallet beside an analog clock, a delivery bag, and a ceramic savings bowl on a dark desk.",
    body_visual="A concrete real-world spending consequence shown with one distinct prop and one clear action, without people or generated writing.",
    cta_visual="A mechanical coin sorter directing the final coin into a ceramic savings bowl, dark premium background.",
    default_cta="Save this and use it before one money decision today.",
    title_options=("Why You Keep Losing Money", "This Money Mistake Costs More Than You Think"),
    tags=("shorts", "personal finance", "education", "money", "budgeting"),
    upload_tags=("shorts", "personal finance", "education", "money", "budgeting", "youtube shorts"),
    thumbnail_style="premium faceless finance editorial, dark background, high contrast",
    thumbnail_prompt=(
        "High CTR YouTube Shorts thumbnail for English personal finance channel, bold simple composition, "
        "premium dark editorial, bright cyan accent, clear emotional focal point, no readable text"
    ),
    video_motion=(
        "camera slowly pushes in, subtle parallax, smooth motion, cinematic vertical composition, "
        "premium faceless finance editorial, high contrast, no readable text"
    ),
    fallback_scene_prompt="Vertical cinematic faceless finance scene about {topic}",
    risk_notes="Use generic visuals; do not imply financial advice.",
)

WEALTH_BLUEPRINT_PROFILE = ProductionChannelProfile(
    key="wealth_blueprint",
    content_label="personal finance for Gen Z and Millennials",
    language_instruction=(
        "English language for a US/global audience. Use clear, natural English. "
        "Do not use Polish words."
    ),
    prompt_label="hand-drawn editorial personal finance short for Wealth Blueprint",
    visual_style=(
        "premium hand-drawn mature editorial illustration, blueprint visual language, charcoal navy and paper cream, "
        "muted teal and sparse warm gold, subtle grids and dimensions, calm readable motion, Alex only when justified"
    ),
    hook_opening_visual=(
        "Hand-drawn blueprint desk with a paper income figure shifting upward, navy overshirt host optional, muted gold accent."
    ),
    intro_visual="Alex explaining beside a cream paper panel with a simple income-to-keep flow, navy background, subtle blueprint grid.",
    body_visual=(
        "A concrete personal-finance breakdown shown through hand-drawn tokens, icons, or comparison panels — no neon UI."
    ),
    cta_visual="Calm navy paper closing frame with one actionable takeaway typeset in English, muted gold underline.",
    default_cta="Save this and test it on one money decision today.",
    title_options=(
        "Why Your Savings Account Is Lying to You",
        "The Rule of 72: Double Your Money Explained",
    ),
    tags=(
        "shorts",
        "personal finance",
        "investing",
        "money habits",
        "budgeting",
        "index funds",
    ),
    upload_tags=(
        "shorts",
        "personal finance",
        "investing",
        "money",
        "budgeting",
        "index funds",
        "wealth building",
        "youtube shorts",
    ),
    thumbnail_style=(
        "hand-drawn mature finance thumbnail, navy or paper cream field, muted warm gold accent, max three bold English words "
        "added in post, one concrete metaphor, Alex only when host-led, no neon mint"
    ),
    thumbnail_prompt=(
        "High CTR YouTube Shorts thumbnail for Wealth Blueprint, premium hand-drawn mature editorial illustration, "
        "navy and paper cream palette with muted warm gold accent, subtle blueprint grid, coherent character if Alex is shown, "
        "no neon fintech glow, no readable text inside the image, serious calm composition"
    ),
    video_motion=(
        "calm readable camera push, subtle blueprint parallax, hand-drawn editorial motion, restrained warm gold accent, "
        "vertical composition, no glitch flashes, sharp foreground subject"
    ),
    fallback_scene_prompt=(
        "Vertical hand-drawn mature personal finance scene about {topic}, navy paper cream blueprint mood, muted gold accent"
    ),
    risk_notes=(
        "Educational only. Do not give specific investment advice, stock picks, crypto predictions, or "
        "get-rich-quick claims. Every numeric claim must be sourceable."
    ),
    language_code="en",
    edge_tts_voice="en-US-ChristopherNeural",
    edge_tts_rate="+2%",
    kokoro_voice="am_michael",
    # Kokoro at its near-default pace pushes the canonical 91-word Wealth
    # Blueprint script past the 45-second Golden Sample ceiling.
    kokoro_speed=1.18,
    narration_style="calm confident finance educator, precise and not salesy",
)

HISTORY_PROFILE = ProductionChannelProfile(
    key="history",
    content_label="historical curiosities",
    language_instruction=(
        "English language for a US/UK/global audience. Use clear documentary English. "
        "Do not use Polish words."
    ),
    prompt_label="faceless historical curiosity documentary short",
    visual_style="cinematic historical documentary, archival mood, dramatic maps and artifacts",
    hook_opening_visual="Dramatic archival opening, old map, candle light, high tension.",
    intro_visual="Historic map, archive room, parchment textures, slow documentary push.",
    body_visual="Cinematic reconstruction mood, artifacts, old streets, maps, no text in image.",
    cta_visual="Calm documentary ending, archive table, no text in generated image.",
    default_cta="Follow for short history stories most people miss.",
    title_options=("The History Detail Most People Miss", "This Moment Changed More Than You Think"),
    tags=("shorts", "historia", "history", "weird history", "history documentary", "historical facts"),
    upload_tags=("shorts", "history", "weird history", "history documentary", "historical facts", "youtube shorts"),
    thumbnail_style="cinematic historical documentary, archival, dramatic light, high contrast",
    thumbnail_prompt=(
        "High CTR YouTube Shorts thumbnail for English history curiosities channel, dramatic archival "
        "composition, old maps, artifacts, warm candle light, clear focal point, no readable text"
    ),
    video_motion=(
        "slow documentary camera push, archival parallax, dust in light, cinematic historical mood, "
        "maps and artifacts, high contrast, no readable text"
    ),
    fallback_scene_prompt="Vertical cinematic historical documentary scene about {topic}, old maps and archive artifacts",
    risk_notes="Keep visuals generic; do not invent exact historical dates, people, or symbols.",
)

DARK_CHRONICLES_PROFILE = ProductionChannelProfile(
    key="dark_chronicles",
    content_label="dark historical curiosities and cinematic documentary Shorts",
    language_instruction=(
        "English language for a US/UK/global adult audience. Use cinematic documentary wording. "
        "Do not use Polish words. Never present uncertain history as settled fact."
    ),
    prompt_label="faceless dark history documentary short for Dark Chronicles",
    visual_style=(
        "pure black backgrounds, deep crimson and burgundy accents, antique matte gold highlights, "
        "chiaroscuro lighting, gothic woodcut documentary atmosphere"
    ),
    hook_opening_visual=(
        "Immediate close historical reconstruction of the central person in visible distress or action, "
        "surrounded by period witnesses, authentic location, cinematic tension."
    ),
    intro_visual=(
        "Wide historical reconstruction of the actual event in a crowded period street, with the central action "
        "clearly visible and witnesses reacting, gothic documentary mood."
    ),
    body_visual=(
        "Chiaroscuro historical reconstruction, parchment, artifacts, old streets, maps, matte gold accents, "
        "no gore and no readable text in generated image."
    ),
    cta_visual="Dark archive table, gothic hourglass mood, antique gold accent, calm documentary ending.",
    default_cta="Follow for darker history told with sources, not myths.",
    title_options=(
        "The Dancing Plague of 1518",
        "They Vanished From the Map Overnight",
    ),
    tags=(
        "shorts",
        "dark history",
        "history documentary",
        "weird history",
        "medieval history",
        "historical mysteries",
    ),
    upload_tags=(
        "shorts",
        "dark history",
        "history documentary",
        "weird history",
        "medieval history",
        "historical mystery",
        "youtube shorts",
    ),
    thumbnail_style=(
        "cinematic chiaroscuro thumbnail, pure black background, deep crimson shadows, antique matte gold accent, "
        "single disturbing artifact, no red arrows, no emoji"
    ),
    thumbnail_prompt=(
        "High CTR YouTube Shorts thumbnail for Dark Chronicles dark history channel, cinematic chiaroscuro, "
        "single disturbing or intriguing historical artifact in sharp focus, 70 percent darkness, dramatic side light, "
        "deep crimson and burgundy shadows, antique matte gold accent, gothic woodcut documentary mood, no gore, "
        "no readable text, no red arrows, no emoji"
    ),
    video_motion=(
        "slow cinematic push-in, archival parallax, candle flicker, dust in light, dark gothic documentary mood, "
        "chiaroscuro lighting, deep crimson shadows, antique matte gold highlights, no readable text"
    ),
    fallback_scene_prompt=(
        "Vertical dark cinematic history documentary scene about {topic}, black background, crimson shadows, "
        "antique gold accent, gothic archival atmosphere"
    ),
    risk_notes=(
        "Every factual claim must be sourceable. Avoid pseudoscience, ancient-aliens framing, unverified claims as fact, "
        "religious mockery, Holocaust denial/minimization, glorification of violent regimes, and gore without clear "
        "educational context."
    ),
    language_code="en",
    edge_tts_voice="en-US-GuyNeural",
    edge_tts_rate="-4%",
    kokoro_voice="am_fenrir",
    kokoro_speed=0.94,
    narration_style="slow suspense documentary narrator, controlled tension and no melodrama",
)

BRAINROT_PROFILE = ProductionChannelProfile(
    key="brainrot",
    content_label="AI brainrot and absurd AI shorts",
    language_instruction=(
        "English language for a US/global Shorts audience. Use punchy, natural English. "
        "Do not use Polish words."
    ),
    prompt_label="surreal AI brainrot meme short",
    visual_style="surreal colorful AI brainrot, absurd meme energy, safe and playful",
    hook_opening_visual="Absurd colorful opening, surreal AI object, fast meme tension.",
    intro_visual="Weird AI-made scene, playful surreal motion, bright contrast.",
    body_visual="Surreal meme objects, impossible but safe scene, no logos, no text in image.",
    cta_visual="Playful surreal ending, looping meme energy, no text in generated image.",
    default_cta="Follow for more AI absurdity in under 60 seconds.",
    title_options=("AI Made Something That Should Not Exist", "This Brainrot Looks Like a Robot Dream"),
    tags=("shorts", "ai", "brainrot", "ai memes", "funny ai"),
    upload_tags=("shorts", "ai", "brainrot", "memy ai", "funny ai"),
    thumbnail_style="surreal AI meme thumbnail, colorful, safe absurdity, high contrast",
    thumbnail_prompt=(
        "High CTR YouTube Shorts thumbnail for English AI brainrot channel, surreal colorful meme scene, "
        "weird AI-generated object, playful absurdity, clear focal point, no readable text"
    ),
    video_motion=(
        "fast playful camera move, surreal object motion, smooth loopable meme energy, colorful AI brainrot, "
        "safe absurd scene, no readable text, no logos"
    ),
    fallback_scene_prompt="Vertical surreal AI brainrot scene about {topic}, colorful absurd safe meme energy",
    risk_notes="Keep it safe and playful; avoid violence, sexualized content, logos, or child-targeted claims.",
)

SIGMA_SQUAD_PROFILE = ProductionChannelProfile(
    key="sigma_squad",
    content_label="Gen Alpha brainrot meme Shorts",
    language_instruction=(
        "English language for a US/global Shorts audience. Use fast, natural Gen Alpha slang only when it fits. "
        "Do not use Polish words. Avoid profanity and sexual content."
    ),
    prompt_label="maximalist absurd Gen Alpha brainrot short for Sigma Squad",
    visual_style=(
        "full saturation chaos, hot pink, cyan, toxic green, neon yellow, deep-fried 3D renders, "
        "oversaturated Roblox meets reaction meme compilation"
    ),
    hook_opening_visual=(
        "Visually overwhelming first frame, Sigma or Ohio energy in the first half second, neon chaos, loud meme tension."
    ),
    intro_visual="Oversaturated animated character in chaotic Ohio-style scene, neon outlines, safe absurd humor.",
    body_visual=(
        "Deep-fried 3D meme simulation, Roblox/Minecraft-inspired abstract scene, neon outlines, no copyrighted characters."
    ),
    cta_visual="Chaotic looping end card mood, neon gradient, safe meme energy, no copyrighted audio or logos.",
    default_cta="Comment W or L if this was peak Sigma energy.",
    title_options=(
        "When your bro tries to mew in class",
        "NPC mode activated in Ohio",
    ),
    tags=(
        "shorts",
        "sigma",
        "ohio",
        "skibidi",
        "brainrot",
        "rizz",
        "gen alpha",
    ),
    upload_tags=(
        "shorts",
        "sigma",
        "ohio",
        "skibidi",
        "brainrot",
        "rizz",
        "gen alpha",
        "meme",
        "youtube shorts",
    ),
    thumbnail_style=(
        "maximalist visual chaos thumbnail, hot pink cyan toxic green, giant bold impact font mood, neon outline, "
        "shocked animated subject, red arrow/fire/lightning overlay allowed only if asset is original"
    ),
    thumbnail_prompt=(
        "High CTR YouTube Shorts thumbnail for Sigma Squad Gen Alpha brainrot channel, deep-fried oversaturated "
        "3D meme render, hot pink cyan toxic green neon palette, chaotic gradient background, shocked or laughing "
        "animated character, neon outline, fire and lightning energy, no copyrighted characters, no readable text"
    ),
    video_motion=(
        "very fast punchy motion, zoom bursts, jitter cuts, meme compilation pacing, neon outlines, deep-fried "
        "chaotic 3D scene, safe absurd humor, no copyrighted characters, no readable text"
    ),
    fallback_scene_prompt=(
        "Vertical maximalist Gen Alpha brainrot scene about {topic}, Sigma vs Ohio energy, neon chaos, "
        "deep-fried 3D meme render, safe absurd humor"
    ),
    risk_notes=(
        "Private-only until manual review. Avoid NSFW, profanity, dangerous challenges, cyberbullying, real-person "
        "targeting, political content, copyrighted audio, and copyrighted characters. Made-for-kids classification "
        "must be reviewed honestly per video."
    ),
    language_code="en",
    edge_tts_voice="en-US-AndrewNeural",
    edge_tts_rate="+10%",
    kokoro_voice="am_puck",
    kokoro_speed=1.12,
    narration_style="fast energetic meme narrator, punchy but still understandable",
)


def resolve_channel_profile(context: ProductionContext, style_guide: str | None = None) -> ProductionChannelProfile:
    # The named channel owns its visual identity even when a legacy account
    # record still carries the generic "finance" profile (or no profile).
    if str(context.account_id or "").strip().casefold() == "yt-wealth-blueprint":
        return WEALTH_BLUEPRINT_PROFILE
    explicit_profile = getattr(context.topic, "content_profile", "").strip().casefold()
    explicit_profiles = {
        "finance": FINANCE_PROFILE,
        "personal_finance": WEALTH_BLUEPRINT_PROFILE,
        "wealth_blueprint": WEALTH_BLUEPRINT_PROFILE,
        "history": HISTORY_PROFILE,
        "historical_curiosities": DARK_CHRONICLES_PROFILE,
        "dark_chronicles": DARK_CHRONICLES_PROFILE,
        "brainrot": BRAINROT_PROFILE,
        "gen_alpha_brainrot": SIGMA_SQUAD_PROFILE,
        "sigma_squad": SIGMA_SQUAD_PROFILE,
    }
    if explicit_profile in explicit_profiles:
        return explicit_profiles[explicit_profile]

    # Project settings may store a human-readable profile description rather
    # than only the stable key. Resolve known channel names first so the
    # surrounding niche text cannot silently downgrade a configured channel.
    explicit_descriptor = " ".join(explicit_profile.replace("_", " ").replace("-", " ").split())
    descriptor_profiles = (
        ("wealth blueprint", WEALTH_BLUEPRINT_PROFILE),
        ("dark chronicles", DARK_CHRONICLES_PROFILE),
        ("sigma squad", SIGMA_SQUAD_PROFILE),
        ("personal finance", WEALTH_BLUEPRINT_PROFILE),
        ("historical curiosities", DARK_CHRONICLES_PROFILE),
        ("gen alpha brainrot", SIGMA_SQUAD_PROFILE),
    )
    for marker, profile in descriptor_profiles:
        if marker in explicit_descriptor:
            return profile

    text = " ".join(
        [
            context.topic.title,
            context.topic.description,
            style_guide or "",
        ]
    ).casefold()
    if any(
        token in text
        for token in (
            "gen_alpha_brainrot",
            "gen alpha brainrot",
            "skibidi",
            "ohio",
            "rizz",
            "mewing",
            "fanum",
            "gyatt",
            "npc",
            "w/l ratio",
            "sigma",
        )
    ):
        return SIGMA_SQUAD_PROFILE
    if any(
        token in text
        for token in (
            "personal_finance",
            "personal finance",
            "compound interest",
            "index fund",
            "index funds",
            "etf",
            "roth ira",
            "traditional ira",
            "rule of 72",
            "dollar cost averaging",
            "us/global",
        )
    ):
        return WEALTH_BLUEPRINT_PROFILE
    if any(
        token in text
        for token in (
            "historical_curiosities",
            "dark history",
            "macabre",
            "mysterious disappearances",
            "dancing plague",
            "weird history",
            "voices of the past",
            "chiaroscuro",
            "gothic",
            "folklore",
        )
    ):
        return DARK_CHRONICLES_PROFILE
    if any(token in text for token in ("historia", "history", "starozyt", "sredniowie", "wojna", "archive")):
        return HISTORY_PROFILE
    if any(token in text for token in ("brainrot", "glupoty ai", "absurd", "memy ai", "funny ai", "surreal")):
        return BRAINROT_PROFILE
    return FINANCE_PROFILE
