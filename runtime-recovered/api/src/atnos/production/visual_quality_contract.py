from __future__ import annotations

import re


_COMMON_CONTRACT = (
    "Use one clear foreground subject, visible action, crisp scene depth, and physical objects tied to the topic. "
    "Reject blurry abstract backgrounds, empty gradients, generic charts, unrelated symbols, stock-looking filler, "
    "watermarks, logos, fake UI text, and readable text inside generated imagery. The viewer must understand the "
    "topic from the frame even with audio muted; do not use a mood-only background."
)

FINANCE_TEXT_SURFACE_TERMS: tuple[str, ...] = (
    "phone",
    "screen",
    "app",
    "app interface",
    "dashboard",
    "chart",
    "graph",
    "bank statement",
    "statement",
    "receipt",
    "bill statement",
    "document",
    "notebook",
    "calendar",
)

_CONTRACTS: dict[str, str] = {
    "wealth_blueprint": (
        "Hand-drawn mature editorial finance only (Visual Bible). Required anchors: Alex when layout justifies host, "
        "paper cream panels, navy blueprint grids, muted gold accents, physical money metaphors without readable text. "
        "Forbidden: neon mint / black-neon dashboards, photoreal stock finance, childish mascot stickers, "
        "blurred mood-only backgrounds, and invented chart labels inside generated imagery."
    ),
    "finance": (
        "Mature adult finance editorial only. Required visual anchors: wallet, blank unbranded bank card, cash, coins, "
        "color-coded envelopes, shopping basket, glass savings jar, hourglass, desk, or faceless adult hands. "
        "Use only physical, text-free props and exclude every text-bearing or display-like surface because generated "
        "writing is unreliable. Every scene must show a recognizable personal-finance situation, not just a finance "
        "mood. Forbidden: childish, toy-like, mascot, sticker, cartoon, blurred background-only frames, and abstract "
        "chart-only scenes."
    ),
    "dark_chronicles": (
        "Cinematic dark documentary only. Required visual anchors: artifact, map, parchment, candle, archive table, "
        "church, courtroom, wax seal, gavel, witness silhouette, or historical object. Forbidden: cute cartoon style, "
        "generic fog, empty black frames, gore bait, fake readable inscriptions, and unrelated occult symbols."
    ),
    "history": (
        "Cinematic historical documentary only. Required visual anchors: artifact, map, parchment, candle, archive "
        "table, church, courtroom, wax seal, gavel, witness silhouette, or historical object. Forbidden: generic fog, "
        "empty black frames, fake readable dates/text, and modern unrelated symbols."
    ),
    "sigma_squad": (
        "High-energy surreal meme style, but still readable and intentional. Required visual anchors: original avatar, "
        "phone, reaction pose, classroom/table, glitch object, or UI-shaped blocks without readable UI text. Forbidden: "
        "copyrighted characters, platform logos, real game screenshots, blank neon gradients, and unreadable visual mush."
    ),
    "brainrot": (
        "Surreal AI meme style, but still readable and intentional. Required visual anchors: original avatar, phone, "
        "reaction pose, toy-like object, glitch object, or UI-shaped blocks without readable UI text. Forbidden: "
        "copyrighted characters, platform logos, real game screenshots, blank neon gradients, and unreadable visual mush."
    ),
}

_NEGATIVE_PROMPTS: dict[str, str] = {
    "wealth_blueprint": (
        "neon mint glow, black neon UI, cyberpunk finance dashboard, photorealistic corporate stock, "
        "childish mascot sticker toy style, blurred background only, empty gradient, fake UI text, watermark"
    ),
    "finance": (
        "childish, toy-like, mascot, sticker, cartoon, cute, kids style, blurred background, bokeh background, "
        "abstract chart only, empty gradient, generic graph, decorative arrow, drawn icon, template thumbnail, fake UI text, "
        "phone, screen, app interface, dashboard, chart, graph, receipt, statement, document, notebook, calendar"
    ),
    "dark_chronicles": (
        "cute cartoon, childish, generic fog, empty black frame, gore bait, fake readable text, fake readable dates, "
        "unrelated occult symbols"
    ),
    "history": (
        "cute cartoon, childish, generic fog, empty black frame, fake readable text, fake readable dates, modern logo"
    ),
    "sigma_squad": (
        "copyrighted character, platform logo, game screenshot, blank neon gradient, unreadable visual mush, real brand"
    ),
    "brainrot": (
        "copyrighted character, platform logo, game screenshot, blank neon gradient, unreadable visual mush, real brand"
    ),
}


def visual_quality_contract(profile_key: str | None) -> str:
    """Zwróć wspólny kontrakt jakości wizualnej dla promptów i audytów."""
    key = str(profile_key or "").strip().casefold()
    profile_contract = _CONTRACTS.get(key, "Use a concrete, topic-specific editorial scene instead of filler.")
    return f"{profile_contract} {_COMMON_CONTRACT}"


def thumbnail_quality_contract(profile_key: str | None) -> str:
    """Zwróć krótszy kontrakt do promptów miniatur."""
    return (
        f"{visual_quality_contract(profile_key)} Thumbnail must work at phone size: one dominant subject, strong "
        "contrast, no clutter, no tiny details, no fake readable text."
    )


def negative_visual_prompt(profile_key: str | None) -> str:
    """Zwróć negatywny prompt dla generatorów obrazów/wideo."""
    key = str(profile_key or "").strip().casefold()
    profile_negative = _NEGATIVE_PROMPTS.get(key, "generic background, unrelated symbol, blurry, low quality")
    return (
        f"watermark, readable text, logo, blurry, low quality, horizontal, distorted faces, {profile_negative}"
    )


def finance_prompt_has_text_surface(value: str) -> bool:
    """Sprawdz, czy prompt finansowy zawiera powierzchnie prowokujace pseudotekst."""
    lowered = " ".join(str(value or "").casefold().split())
    return any(
        re.search(rf"(?<!\w){re.escape(term)}s?(?!\w)", lowered) is not None
        for term in FINANCE_TEXT_SURFACE_TERMS
    )
