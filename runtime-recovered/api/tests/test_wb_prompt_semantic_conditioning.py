"""WB SDXL prompts retain literal props while weighting their medium and framing."""

import re

import pytest

from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style
from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.prompts import (
    WB_MEDIUM,
    WB_PROMPT_COMPILER_VERSION,
    build_prompt,
    negative_prompt_with_request,
)


@pytest.mark.parametrize("style_id", list("ABCD"))
def test_wb_sdxl_literal_subject_and_exact_medium_lead_long_setting(style_id):
    style = freeze_visual_style(style_id)
    subject = "one blank-faced teal paper slip beside one small unbranded shopping bag"
    composition = (
        "One blank-faced teal paper slip beside one small unbranded shopping bag. "
        "Keep both visible with a clear gap; leave the paper face blank for a later overlay. "
        "Place the pair on a home-office desk with quiet caption areas. "
        "Warm-gold practical light, deep charcoal-navy shadows, cream-paper surfaces, restrained teal, "
        "soft painted 2.5D depth and paper texture. A complete home-office remains visible. "
        "Avoid: banknotes, bills, currency portraits, unrelated desk props, text, letters, numbers."
    )
    request = GenerationRequest(
        channel="wealth_blueprint",
        workflow="WEALTH_BACKGROUND",
        subject=subject,
        composition=composition,
        visual_style_id=style_id,
        visual_style_prompt=style["prompt"],
        visual_style_tags=[style["asset_tag"]],
        visual_style_definition_hash=style["definition_hash"],
    )

    prompt = build_prompt(request)
    literal_subject = "one unprinted teal thin horizontal rectangular paper card beside one small unbranded shopping bag"
    weighted_subject = f"({literal_subject}:1.25)"
    medium = (style['prompt'].split(',', 1)[0] if style['prompt'].startswith('2D ')
              else WB_MEDIUM[style_id])
    weighted_medium = f"({medium}:1.15)"

    assert WB_PROMPT_COMPILER_VERSION == "wb-weighted-still-life-v14-smartphone-single-prop"
    assert prompt.startswith(f"{weighted_subject}, {weighted_medium}, foreground still-life")
    assert prompt.count("paper card") == 1
    assert prompt.count("small unbranded shopping bag") == 1
    assert prompt.count("unprinted teal") == 1
    assert "blank-faced" not in prompt
    assert prompt.index("foreground still-life") < prompt.index("home-office desk")
    assert "Place the pair on a home-office desk" in prompt
    assert "complete home-office remains visible" in prompt
    assert "quiet caption areas" in prompt
    assert "Warm-gold practical light" in prompt
    assert "deep charcoal-navy shadows" in prompt
    assert "cream-paper surfaces" in prompt
    assert "restrained teal" in prompt
    assert "soft painted 2.5D depth" in prompt
    assert "later overlay" in prompt
    assert style["prompt"] not in prompt  # The exact frozen style maps to its one compiler medium.
    assert medium in weighted_medium
    assert all(WB_MEDIUM[other] not in prompt for other in set("ABCD") - {style_id})
    assert not re.search(r"\b(?:banknotes?|bills?|currency portraits|printed text|numbers)\b", prompt, re.I)

    negative = negative_prompt_with_request("wealth_blueprint", request).casefold()
    for exclusion in ("banknotes", "bills", "currency portraits", "unrelated desk props", "text", "letters", "numbers"):
        assert exclusion in negative


def test_wb_prompt_keeps_explicit_negative_exclusions_and_does_not_add_room_bans():
    style = freeze_visual_style("D")
    request = GenerationRequest(
        channel="wealth_blueprint",
        workflow="WEALTH_BACKGROUND",
        subject="one clear glass savings jar",
        composition="Place the jar on a home-office desk. Avoid: extra bags, additional paper props.",
        visual_style_id="D",
        visual_style_prompt=style["prompt"],
        visual_style_tags=[style["asset_tag"]],
        visual_style_definition_hash=style["definition_hash"],
    )

    prompt = build_prompt(request)
    negative = negative_prompt_with_request("wealth_blueprint", request).casefold()
    assert "one clear glass savings jar" in prompt
    assert "home-office desk" in prompt
    assert "extra bags" in negative
    assert "additional paper props" in negative
    assert "home-office" not in negative
    assert "desk" not in negative


def test_environment_request_keeps_its_establishing_frame():
    style = freeze_visual_style("D")
    request = GenerationRequest(channel="wealth_blueprint", workflow="WEALTH_BACKGROUND",
        subject="a quiet evening home office", composition="Wide establishing view of the office.",
        visual_style_id="D", visual_style_prompt=style["prompt"], visual_style_tags=[style["asset_tag"]])
    prompt = build_prompt(request)
    assert "Wide establishing view of the office" in prompt
    assert "foreground still-life" not in prompt
    assert "tight subject-led framing" not in prompt


def test_paper_conditioning_does_not_exclude_a_required_host():
    style = freeze_visual_style("D")
    request = GenerationRequest(channel="wealth_blueprint", workflow="WEALTH_BACKGROUND",
        subject="Alex holds an unprinted paper card", visual_style_id="D",
        visual_style_prompt=style["prompt"], visual_style_tags=[style["asset_tag"]])
    assert "people, man, woman, faces, crowd" not in negative_prompt_with_request("wealth_blueprint", request)
