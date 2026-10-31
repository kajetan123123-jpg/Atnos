"""Composable channel prompt builders (no monolithic hardcoded prompts)."""

from __future__ import annotations

import re
from typing import Any

from atnos.production.comfy_channels.models import ChannelId, GenerationRequest

NEGATIVES: dict[str, str] = {
    "wealth_blueprint": (
        "malformed hands, inconsistent character identity, unreadable text, random letters, "
        "random numbers, charts with labels, UI screens with text, watermark, logo, "
        "cheap stock photo aesthetic, excessive clutter, distorted objects, duplicate objects, "
        "photorealistic corporate stock, neon fintech glow"
    ),
    "wonder_crew": (
        "uncanny valley, photorealistic human skin, malformed anatomy, inconsistent costume, "
        "wrong character colors, frightening child faces, distorted eyes, duplicate limbs, "
        "random accessories, horror imagery, gore, adult content, text, watermark"
    ),
    "dark_chronicles": (
        "cartoon, oversaturated, comedy expression, excessive gore, plastic skin, "
        "AI face distortion, duplicate people, malformed anatomy, random text, watermark, "
        "overexposed lighting, jump-scare meme aesthetic, cheap horror filter"
    ),
}

STYLE: dict[str, str] = {
    "wealth_blueprint": (
        "premium hand-drawn mature editorial illustration, designer's sketchbook with subtle 2.5D depth, "
        "varying-width navy graphite outlines, soft dimensional shadows, warm paper texture, "
        "restrained navy cream blueprint-blue muted teal and sparse warm gold, low-opacity blueprint grids, "
        "no photorealism, no neon fintech, no text in image, no charts, no numbers"
    ),
    "wonder_crew": (
        "high-quality stylized children's 3D feature concept art inspiration, friendly shapes, "
        "readable silhouettes, soft magical lighting, vibrant but soft colors, not photorealistic"
    ),
    "dark_chronicles": (
        "cinematic horror atmosphere, mature unsettling mood, high contrast low-key lighting, "
        "deep shadows, practical lights, fog, film composition, restrained desaturated palette"
    ),
}

CHARACTER_LOCK: dict[str, dict[str, str]] = {
    "wealth_blueprint": {
        "alex": (
            "Alex the Wealth Blueprint host: adult man, dark swept hair, navy overshirt over light shirt, "
            "realistic hand-drawn editorial proportions, consistent face and wardrobe from approved references"
        ),
    },
    "wonder_crew": {
        "niko": "Niko from Wonder Crew: keep locked face, hair, outfit and silhouette from approved reference sheet",
        "pif": "Pif from Wonder Crew: keep locked face, hair/ears, outfit and silhouette from approved reference sheet",
        "tika": "Tika from Wonder Crew: keep locked face, hair, outfit and silhouette from approved reference sheet",
        "mira": "Mira from Wonder Crew: keep locked face, hair, outfit and silhouette from approved reference sheet",
    },
    "dark_chronicles": {},
}


def negative_prompt(channel: ChannelId) -> str:
    return NEGATIVES[channel]


WB_PROMPT_COMPILER_VERSION = 'wb-weighted-still-life-v14-smartphone-single-prop'
WB_MEDIUM = {
    'A': '2D ink and watercolor illustration, visible hand-drawn navy outlines, cream paper colors, muted gold accents',
    'B': '2D editorial cut-paper collage illustration, layered cream paper shapes, navy ink contours, muted gold accents',
    'C': '2D technical pen-and-ink illustration, precise navy drafting lines, cross-hatched graphite shading, cream and muted gold',
    'D': '2D hand-drawn cinematic story illustration, expressive navy ink outlines, painted cream highlights, muted gold practical light',
}


def _positive_wb_text(text: str) -> str:
    # Negated objects still prime diffusion's positive conditioning. The same
    # exclusions are retained in the negative conditioning, not discarded.
    text = (text or '').split(' Avoid:')[0]
    text = re.sub(r'\b(?:Do not|No)\s+[^.]+(?:\.|$)', '', text, flags=re.I)
    return ', '.join(part.strip(' ,') for part in text.split(',')
                     if part.strip(' ,') and not part.strip().lower().startswith('no '))


def _literal_paper_description(text: str) -> str:
    # Diffusion interprets "slip" as clothing. Name the same physical paper
    # prop unambiguously; never add currency, text, counts or scene objects.
    # "Blank-faced" describes expressionless people in diffusion training.
    # Here it means an unprinted paper surface, not a face or a new person.
    text = re.sub(r'\bblank-faced(?=\s+(?:\w+\s+){0,3}paper\b)', 'unprinted', text, flags=re.I)
    return re.sub(r'\bpaper slips?\b', lambda m: (
        'thin horizontal rectangular paper cards' if m.group(0).casefold().endswith('s')
        else 'thin horizontal rectangular paper card'), text, flags=re.I)


def _weighted_wb_phrase(text: str, weight: float) -> str:
    """Use ComfyUI's native CLIP attention syntax for concise WB anchors."""
    return f'({text}:{weight:.2f})' if text else ''


def _remove_duplicate_subject_sentence(composition: str, subject: str) -> str:
    if not composition or not subject:
        return composition
    return re.sub(re.escape(subject) + r'\s*\.', '', composition, count=1, flags=re.I).strip(' ,')


def _wb_prop_framing(subject: str) -> str:
    """A declared single prop must not receive plural separation conditioning."""
    props = re.findall(
        r'\b(?:smartphones?|phones?|cards?|envelopes?|bags?|jars?|coins?|wallets?|scales?|cups?|banknotes?|notebooks?)\b',
        subject, re.I,
    )
    if (re.match(r'^one\b', subject.strip(), re.I) and len(props) == 1
            and not props[0].casefold().endswith('s')):
        return 'foreground still-life, close framing, one main prop centered and fully visible'
    return 'foreground still-life, close framing, props centered and separated'


def build_prompt(request: GenerationRequest, *, channel_style: str | None = None) -> str:
    channel = request.channel
    if request.visual_style_id and channel != "wealth_blueprint":
        raise ValueError("Wealth Blueprint visual style cannot be used on another channel")
    if request.visual_style_id and not request.visual_style_prompt:
        raise ValueError("Frozen visual style prompt is missing")
    style = request.visual_style_prompt or channel_style
    if style is None and channel == "wealth_blueprint":
        try:
            from atnos.production.channels.wealth_blueprint.visual_profile import comfy_style_from_visual_profile

            style = comfy_style_from_visual_profile()
        except Exception:
            style = STYLE[channel]
    if style is None:
        style = STYLE[channel]
    subject = (request.subject or "subject").strip().casefold()
    if channel == 'wealth_blueprint' and subject not in CHARACTER_LOCK[channel]:
        # ComfyUI's SDXL CLIPTextEncode parses `(phrase:weight)` natively. Give
        # the exact requested props and frozen medium a modest early weight;
        # long setting prose can otherwise turn a still-life into a room scene.
        composition = _literal_paper_description(_positive_wb_text(request.composition or ''))
        literal_subject=_literal_paper_description(request.subject or '')
        # An explicit medium travels in the frozen profile; old profiles retain
        # their historical compiler mapping even after the catalog is updated.
        explicit_medium = style.strip().startswith('2D ')
        medium = (style.split(',', 1)[0].strip() if explicit_medium else
                  WB_MEDIUM.get(request.visual_style_id or '', '2D hand-drawn editorial ink illustration'))
        physical_prop = re.search(r'\b(?:paper|smartphones?|phones?|cards?|envelopes?|bags?|jars?|coins?|wallets?|scales?|cups?|banknotes?)\b', literal_subject, re.I)
        person = re.search(r'\b(?:Alex|host|person|man|woman|people)\b', literal_subject, re.I)
        focus = (_wb_prop_framing(literal_subject)
                 if physical_prop and not person else '')
        parts = [
            _weighted_wb_phrase(literal_subject, 1.25),
            _weighted_wb_phrase(medium, 1.15),
            focus,
        ]
        if request.visual_style_id in WB_MEDIUM:
            # Keep exact frozen style-to-medium matching. Composition remains
            # intact after removing only a duplicate leading subject sentence.
            composition = _remove_duplicate_subject_sentence(composition, literal_subject)
        else:
            parts.append(_positive_wb_text(style))
        if explicit_medium:
            # Keep palette and rendering instructions from the actual snapshot,
            # rather than silently replacing them with a static A/B/C/D medium.
            remainder = style.partition(',')[2]
            parts.append(_positive_wb_text(remainder))
        parts.append(composition)
        if request.lighting:
            parts.append(request.lighting)
        if request.camera:
            parts.append(request.camera)
        return ', '.join(part.strip(' ,') for part in parts if part)
    parts: list[str] = [style]

    locks = CHARACTER_LOCK.get(channel) or {}
    if subject in locks:
        parts.append(locks[subject])
    elif request.subject:
        parts.append(f"subject: {request.subject}")

    if request.emotion:
        parts.append(f"emotion: {request.emotion}")
    if request.direction:
        parts.append(f"facing / direction: {request.direction}")
    if request.asset_type:
        parts.append(f"asset type: {request.asset_type}")
    if request.composition:
        composition = request.composition
        avoid_idx = composition.casefold().find(" avoid:")
        if avoid_idx >= 0:
            composition = composition[:avoid_idx].strip().rstrip(",")
        if composition:
            parts.append(f"composition: {composition}")
    elif channel == "dark_chronicles":
        parts.append("composition: cinematic establishing storytelling frame")
    if request.lighting:
        parts.append(f"lighting: {request.lighting}")
    elif channel == "dark_chronicles":
        parts.append("lighting: low-key practical lights, deep shadows")
    if request.camera:
        parts.append(f"camera: {request.camera}")

    if channel == "wealth_blueprint":
        parts.append("no text, no letters, no numbers, no charts, no UI labels")
    if channel == "wonder_crew":
        parts.append("safe for ages 4-7, warm friendly, not scary")
    if channel == "dark_chronicles":
        parts.append("believable location, environmental storytelling, no gore splash, no text")

    # Deduplicate while preserving order
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(part)
    return ", ".join(cleaned)


def extract_avoid_negatives(composition: str) -> str:
    """Pull QC regen 'Avoid: ...' tails into the negative prompt (not the positive)."""
    text = composition or ""
    idx = text.casefold().find(" avoid:")
    if idx < 0:
        return ""
    return text[idx + len(" Avoid:") :].strip(" ,")


def negative_prompt_with_request(channel: ChannelId, request: GenerationRequest) -> str:
    base = negative_prompt(channel)
    extras = [extract_avoid_negatives(request.composition or '')]
    if channel == 'wealth_blueprint':
        extras.append('photograph, photorealism, product photography, glossy 3D render, plastic CGI, text, letters, numbers, currency portraits')
        device_terms = re.findall(r'\b(?:smartphones?|phones?)\b', request.subject or '', re.I)
        if re.match(r'^\s*one\b', request.subject or '', re.I) and len(device_terms) == 1:
            extras.append('multiple phones, repeated smartphones, duplicate devices, phone grid, phone collage, device lineup, contact sheet')
        if re.search(r'\bpaper slips?\b',request.subject or '',re.I):
            extras.append('lingerie, dress, fashion model, birds, dogs, cats, standing poster, whiteboard, signboard, picture frame')
        if (re.search(r'\b(?:paper|cards?|envelopes?|bags?|jars?|coins?|wallets?|scales?|cups?|banknotes?)\b', request.subject or '', re.I)
                and not re.search(r'\b(?:Alex|host|person|man|woman|people)\b', request.subject or '', re.I)):
            extras.append('people, man, woman, faces, crowd')
        for text in (request.visual_style_prompt or '', request.composition or ''):
            extras.extend(re.findall(r'\bno\s+([^,.]+)', text, re.I))
            extras.extend(re.findall(r'\bdo not\s+(?:show|add|paint|include)\s+([^.]+)', text, re.I))
    extra = ', '.join(part for part in extras if part)
    if not extra:
        return base
    return f"{base}, {extra}"


def resolve_aspect(request: GenerationRequest, *, default_w: int, default_h: int) -> tuple[int, int]:
    if request.width and request.height:
        return int(request.width), int(request.height)
    ratio = (request.aspect_ratio or "1:1").strip()
    mapping = {
        "1:1": (1024, 1024),
        "9:16": (768, 1344),
        "16:9": (1344, 768),
        "3:4": (896, 1152),
        "4:3": (1152, 896),
    }
    return mapping.get(ratio, (default_w, default_h))
