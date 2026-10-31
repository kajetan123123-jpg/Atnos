from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserSaasRecipe:
    id: str
    name: str
    login_required: bool
    browser_only: bool
    use_cases: list[str]
    not_for: list[str]
    steps: list[str]


BROWSER_SAAS_RECIPES = {
    "klap_browser_recipe": BrowserSaasRecipe(
        id="klap_browser_recipe",
        name="Klap Browser Recipe",
        login_required=True,
        browser_only=True,
        use_cases=[
            "long-video-to-shorts repurposing",
            "dynamic captions inspiration",
            "AI reframing benchmark",
            "short candidate discovery",
        ],
        not_for=["silent upload", "billing changes", "unreviewed exports", "core local rendering"],
        steps=[
            "open Klap",
            "verify login or return HUMAN_REQUIRED",
            "use only approved source video or URL",
            "generate preview/candidates if user approved the input",
            "download/export only after human approval",
            "summarize reusable pacing, caption and framing patterns",
        ],
    ),
    "autoshorts_browser_recipe": BrowserSaasRecipe(
        id="autoshorts_browser_recipe",
        name="AutoShorts/AutomaticShorts Browser Recipe",
        login_required=True,
        browser_only=True,
        use_cases=[
            "faceless series structure research",
            "topic-to-video UX benchmarking",
            "style and series inspiration",
            "manual preview comparison",
        ],
        not_for=["auto-posting", "billing changes", "hands-free channel operation", "silent social publishing"],
        steps=[
            "open AutoShorts or AutomaticShorts",
            "inspect public examples and series workflow",
            "if logged in, create only draft/preview after approval",
            "do not connect social accounts without explicit approval",
            "record style, voice, pacing, visual grammar and editability notes",
        ],
    ),
    "autoaishorts_browser_recipe": BrowserSaasRecipe(
        id="autoaishorts_browser_recipe",
        name="AutoAIShorts Browser Recipe",
        login_required=True,
        browser_only=True,
        use_cases=[
            "faceless video visual style benchmark",
            "AI image + voice + caption workflow inspiration",
            "sample analysis",
        ],
        not_for=["auto-publishing", "billing changes", "silent export"],
        steps=[
            "open AutoAIShorts",
            "inspect examples and feature workflow",
            "create drafts, generate previews, or export only after human approval",
            "capture notes about AI images, b-roll, captions, voices and music",
            "compare against Atnos local output",
            "produce a reusable production checklist",
        ],
    ),
    "elevenlabs_browser_recipe": BrowserSaasRecipe(
        id="elevenlabs_browser_recipe",
        name="ElevenLabs Browser Recipe",
        login_required=True,
        browser_only=True,
        use_cases=[
            "premium narration generation",
            "voice style audition",
            "voiceover quality comparison",
        ],
        not_for=["voice cloning without consent", "billing changes", "publishing", "unreviewed celebrity voices"],
        steps=[
            "open ElevenLabs",
            "verify login or return HUMAN_REQUIRED",
            "paste approved script only",
            "generate or audition narration only after approval",
            "download audio for local render after approval",
            "store voice settings and audio path in the production report",
        ],
    ),
    "videdge_browser_recipe": BrowserSaasRecipe(
        id="videdge_browser_recipe",
        name="VidEdge Browser Recipe",
        login_required=True,
        browser_only=True,
        use_cases=["research", "niche discovery", "script ideas", "thumbnail inspiration"],
        not_for=["core rendering", "automated publishing", "silent paid actions"],
        steps=["open VidEdge", "collect niche signals", "capture script/thumbnail references", "summarize findings"],
    ),
    "facelessreels_browser_recipe": BrowserSaasRecipe(
        id="facelessreels_browser_recipe",
        name="FacelessReels Browser Recipe",
        login_required=True,
        browser_only=True,
        use_cases=["prompt-to-video exploration", "workflow inspiration", "format benchmarking"],
        not_for=["core backend rendering", "automated purchase", "silent export"],
        steps=["open FacelessReels", "paste prompt only after approval", "inspect generated structure", "summarize reusable patterns"],
    ),
}


def get_browser_saas_recipe(recipe_id: str) -> BrowserSaasRecipe:
    return BROWSER_SAAS_RECIPES[recipe_id]
