from __future__ import annotations

import json
import re
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads
from atnos.memory.obsidian_style_guide import read_channel_style_guide
from atnos.production.channel_profile import ProductionChannelProfile, resolve_channel_profile
from atnos.production.faceless_playbook import faceless_playbook_prompt_block, faceless_playbook_summary
from atnos.production.models import ProductionContext, StepResult
from atnos.production.stability_topics import canonical_stability_contracts, parse_stability_source_record
from atnos.production.steps.common import ask_ollama_json, clean_words, write_json


SCRIPT_GENERATION_CONTRACT: dict[str, Any] = {
    "module": "atnos.production.steps.script_writer",
    "compat_module": "atnos.production.steps.script_generator",
    "output_artifact": "script.json",
    "requires_hook_artifact": True,
    "target_duration_seconds": [45, 60],
    "minimum_segments": 5,
    "topic_intelligence_source": "SQLite intelligence.db",
    "style_guide_source_order": ["obsidian_vault", "local_channel_ops_fallback"],
    "style_guide_reader": "atnos.memory.obsidian_style_guide.read_channel_style_guide",
    "faceless_playbook_source": "atnos.production.faceless_playbook.build_faceless_playbook",
    "forbidden_claim_policy": "do not invent market numbers, earnings, or performance claims",
    "records_usage_metadata": ["style_guide", "topic_intelligence", "faceless_playbook"],
}

WEALTH_BLUEPRINT_MIN_SPOKEN_WORDS = 90
WEALTH_BLUEPRINT_MAX_SPOKEN_WORDS = 120
ESTIMATED_SPOKEN_WORDS_PER_SECOND = 2.0


HUMAN_COPY_RULES = (
    "Human copy rules: write spoken narration, not an explainer essay. "
    "Every Short needs a concrete micro-scene, a small conflict, and a payoff. "
    "Use ordinary human details: paycheck, receipt, street, courtroom, door, crowd, card, app, habit, witness. "
    "Avoid generic filler such as 'this topic matters', 'history is full of surprises', "
    "'this is education, not advice', and vague abstract advice. "
    "Do not put legal disclaimers in spoken narration; keep compliance in metadata and claim safety. "
    "Lines should sound like a real narrator could say them naturally in one breath. "
    "Use contractions and connective phrases where a native speaker would use them. "
    "Never make every sentence the same length, and never split one natural thought into isolated fact cards. "
    "Write currency and decimal amounts the way they should be pronounced by a narrator."
)

CONCRETE_WORDS_BY_PROFILE: dict[str, tuple[str, ...]] = {
    "wealth_blueprint": (
        "paycheck",
        "receipt",
        "subscription",
        "delivery",
        "card",
        "bank",
        "app",
        "wallet",
        "rent",
        "week",
        "habit",
        "spending",
        "purchase",
        "compound",
        "interest",
        "investment",
        "calendar",
        "calculator",
        "contribution",
        "year",
        "growth",
        "ledger",
    ),
    "dark_chronicles": (
        "court",
        "street",
        "archive",
        "map",
        "candle",
        "prison",
        "punishment",
        "crowd",
        "witness",
        "door",
        "law",
        "shame",
        "record",
    ),
    "sigma_squad": (
        "npc",
        "ohio",
        "sigma",
        "meter",
        "screen",
        "chat",
        "move",
        "glitch",
        "scene",
        "energy",
    ),
}


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    hook = context.artifacts["hook"]["selected"]
    style_guide = read_channel_style_guide(settings, context.account_id)
    profile = resolve_channel_profile(context, style_guide.content)
    style_block = _style_guide_prompt_block(style_guide.content)
    playbook = context.artifacts.get("faceless_playbook")
    playbook_block = faceless_playbook_prompt_block(playbook)
    intelligence = _read_topic_intelligence(settings, context)
    intelligence_block = _topic_intelligence_prompt_block(intelligence)
    reference_language_profile = _read_reference_language_profile(settings, profile.key)
    reference_language_block = _reference_language_prompt_block(reference_language_profile)
    prompt = (
        "Write a YouTube Shorts script from the approved topic and hook. Return JSON only: "
        "{\"segments\":[{\"id\":\"s01\",\"type\":\"hook|intro|body|transition|cta|outro\","
        "\"text\":\"...\",\"duration_target_sec\":int,\"visual_note\":\"...\"}],"
        "\"total_duration_target_sec\":int,\"title_options\":[...],"
        "\"description_draft\":\"...\",\"tags\":[...]}. "
        f"Content type: {profile.content_label}. "
        f"Target 45-60 seconds. {profile.language_instruction} Short sentences. No unsupported claims. "
        "For Wealth Blueprint, write 90-120 spoken narration words total. Do not pad scene durations "
        "to disguise a short script. "
        f"{HUMAN_COPY_RULES} "
        "Follow the channel style guide when present. "
        "Use the topic intelligence block as factual context; do not invent market numbers, earnings, "
        "or performance claims that are not present in the signals. "
        f"Topic: {context.topic.title}. Description: {context.topic.description}. "
        f"Approved hook: {hook['text']}. Opening line: {hook['opening_line']}."
        f"{intelligence_block}"
        f"{reference_language_block}"
        f"{playbook_block}"
        f"{style_block}"
    )
    payload = await _llm_or_fallback(settings, context, prompt, hook, intelligence, profile)
    payload["style_guide"] = {
        "status": style_guide.status,
        "source": style_guide.source,
        "used": bool(style_guide.content),
    }
    payload["topic_intelligence"] = {
        "used": bool(intelligence.get("evidence_backed")),
        "evidence_backed": bool(intelligence.get("evidence_backed")),
        "source_count": len(intelligence.get("youtube_examples") or []) + len(intelligence.get("google_trends") or []),
        "inspired_by": intelligence.get("inspired_by") or "",
        "score": intelligence.get("score"),
    }
    payload["faceless_playbook"] = faceless_playbook_summary(playbook if isinstance(playbook, dict) else {})
    payload["reference_language_profile"] = {
        "used": bool(reference_language_profile.get("sample_count")),
        "sample_count": int(reference_language_profile.get("sample_count") or 0),
        "source_policy": str(reference_language_profile.get("source_policy") or ""),
        "metrics": reference_language_profile.get("metrics") or {},
    }
    return write_json(context.artifact_dir / "script.json", payload)


def _read_reference_language_profile(settings: Settings, profile_key: str) -> dict[str, Any]:
    root = settings.data_dir / "learning" / "active-reference-shorts"
    paths = [
        root / f"latest_editorial_profile_{profile_key}.json",
        root / f"latest_script_language_profile_{profile_key}.json",
    ]
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and str(payload.get("profile_key") or "") == profile_key:
            return payload
    return {}


def _reference_language_prompt_block(profile: dict[str, Any]) -> str:
    sample_count = int(profile.get("sample_count") or 0)
    if sample_count <= 0:
        return ""
    metrics = profile.get("metrics") if isinstance(profile.get("metrics"), dict) else {}
    rules = [str(item).strip() for item in profile.get("generation_rules") or [] if str(item).strip()]
    return (
        "\nREFERENCE LANGUAGE MEASUREMENTS (aggregate patterns only, never source copy):\n"
        f"- Measured transcript samples: {sample_count}.\n"
        f"- Median spoken sentence length: {metrics.get('median_sentence_words', 'unknown')} words.\n"
        f"- Median short/medium/long sentence mix: {metrics.get('median_short_sentence_ratio', 'unknown')} / "
        f"{metrics.get('median_medium_sentence_ratio', 'unknown')} / {metrics.get('median_long_sentence_ratio', 'unknown')}.\n"
        f"- Median fragment ratio: {metrics.get('median_fragment_sentence_ratio', 'unknown')}.\n"
        f"- Median delivery estimate: {metrics.get('median_estimated_wpm', 'unknown')} WPM.\n"
        f"- Median spoken transition density: {metrics.get('median_transitions_per_100_words', 'unknown')} per 100 words.\n"
        f"- Observed opening types: {json.dumps(metrics.get('opening_patterns') or {}, ensure_ascii=False)}.\n"
        f"- Observed spoken transitions: {json.dumps(metrics.get('transition_types') or {}, ensure_ascii=False)}.\n"
        + "".join(f"- {rule}\n" for rule in rules[:8])
        + "Hard originality rule: do not reuse any source sentence, signature phrase, anecdote, title, or claim. "
        "Use measurements only to improve rhythm, clarity, tension, and conversational delivery.\n"
    )


async def _llm_or_fallback(
    settings: Settings,
    context: ProductionContext,
    prompt: str,
    hook: dict[str, Any],
    intelligence: dict[str, Any],
    profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    profile = profile or resolve_channel_profile(context)
    try:
        parsed = await ask_ollama_json(settings, prompt, timeout_s=80.0)
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("segments"), list):
        normalized = _normalize_segments(context, hook, parsed)
        if len(normalized["segments"]) >= 5:
            normalized = await _retry_short_wealth_script(
                settings,
                context,
                hook,
                intelligence,
                profile,
                normalized,
            )
            return _apply_script_gates(context, hook, normalized, intelligence, profile)
    if isinstance(parsed, dict) and isinstance(parsed.get("body"), list):
        body = [clean_words(str(item), 20) for item in parsed["body"] if str(item).strip()][:8]
        if len(body) >= 4:
            normalized = _legacy_body_to_segments(
                context,
                hook,
                body,
                str(parsed.get("cta") or profile.default_cta),
                profile,
            )
            return _apply_script_gates(context, hook, normalized, intelligence, profile)

    return _safe_fallback_script(context, hook, profile)


async def _retry_short_wealth_script(
    settings: Settings,
    context: ProductionContext,
    hook: dict[str, Any],
    intelligence: dict[str, Any],
    profile: ProductionChannelProfile,
    normalized: dict[str, Any],
) -> dict[str, Any]:
    """Daje lokalnemu LLM jedną szansę naprawy wyłącznie zbyt krótkiego draftu."""
    if profile.key != "wealth_blueprint":
        return normalized
    markers = _script_quality_markers(normalized, profile)
    if markers != ["wealth_blueprint_narration_too_short"]:
        return normalized
    if _unsupported_numeric_claims(normalized, intelligence, context=context):
        return normalized
    draft = [
        {
            "id": segment.get("id"),
            "type": segment.get("type"),
            "text": segment.get("text"),
            "visual_note": segment.get("visual_note"),
        }
        for segment in normalized.get("segments") or []
        if isinstance(segment, dict)
    ]
    retry_prompt = (
        "Rewrite this Wealth Blueprint YouTube Short draft as natural spoken English. Return JSON only with "
        "the same segments schema. Produce 95-115 spoken words total. Preserve the approved topic and factual "
        "meaning, but add concrete human moments, tension, and one useful action. Do not repeat the hook in the "
        "second line. Do not add statistics, returns, prices, legal disclaimers, or generic essay phrases. "
        "Vary sentence length and use natural transitions such as but, so, because, now, or instead. "
        f"Approved topic: {context.topic.title}. Approved hook: {hook.get('text')}. Draft: "
        f"{json.dumps({'segments': draft}, ensure_ascii=False)}"
    )
    try:
        parsed = await ask_ollama_json(settings, retry_prompt, timeout_s=80.0)
    except Exception:
        return normalized
    if not isinstance(parsed, dict) or not isinstance(parsed.get("segments"), list):
        return normalized
    candidate = _normalize_segments(context, hook, parsed)
    return candidate if len(candidate.get("segments") or []) >= 5 else normalized


def _apply_script_gates(
    context: ProductionContext,
    hook: dict[str, Any],
    normalized: dict[str, Any],
    intelligence: dict[str, Any],
    profile: ProductionChannelProfile,
) -> dict[str, Any]:
    """Apply identical safety and quality gates to every accepted LLM response shape."""
    profile_mismatch = _profile_mismatch_markers(normalized, profile)
    if profile_mismatch:
        fallback = _safe_fallback_script(context, hook, profile)
        fallback["claim_safety"] = {
            "status": "fallback_rewritten_profile_mismatch",
            "unsupported_claims": [],
            "profile_mismatch_markers": profile_mismatch,
            "policy": "script must match the active content account profile",
        }
        return fallback
    unsupported_claims = _unsupported_numeric_claims(normalized, intelligence, context=context)
    if unsupported_claims:
        fallback = _safe_fallback_script(context, hook, profile)
        fallback["claim_safety"] = {
            "status": "fallback_rewritten_unsupported_claims",
            "unsupported_claims": unsupported_claims,
            "policy": SCRIPT_GENERATION_CONTRACT["forbidden_claim_policy"],
        }
        return fallback
    script_quality_markers = _script_quality_markers(normalized, profile)
    script_quality_markers.extend(_topic_fit_markers(context, normalized, profile))
    script_quality_markers = list(dict.fromkeys(script_quality_markers))
    duration_markers = {
        "wealth_blueprint_narration_too_short",
        "wealth_blueprint_narration_too_long",
    }
    non_duration_quality_markers = [
        marker for marker in script_quality_markers if marker not in duration_markers
    ]
    if non_duration_quality_markers:
        fallback = _safe_fallback_script(context, hook, profile)
        fallback["claim_safety"] = {
            "status": "fallback_rewritten_script_quality",
            "unsupported_claims": [],
            "script_quality_markers": script_quality_markers,
            "policy": "script must sound like spoken narration, not prompt filler",
        }
        return fallback
    if script_quality_markers:
        fallback = _safe_fallback_script(context, hook, profile)
        fallback["claim_safety"] = {
            "status": "fallback_rewritten_script_quality",
            "unsupported_claims": [],
            "script_quality_markers": script_quality_markers,
            "policy": "script must sound like spoken narration, not prompt filler",
        }
        return fallback
    normalized["claim_safety"] = {
        "status": "passed",
        "unsupported_claims": [],
        "policy": SCRIPT_GENERATION_CONTRACT["forbidden_claim_policy"],
    }
    normalized["script_quality"] = {
        "status": "passed",
        "markers": [],
        "policy": HUMAN_COPY_RULES,
    }
    return normalized


def _safe_fallback_script(
    context: ProductionContext,
    hook: dict[str, Any],
    profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    profile = profile or resolve_channel_profile(context)
    body = _fallback_body(context, profile, hook)
    payload = _legacy_body_to_segments(context, hook, body, profile.default_cta, profile)
    payload.setdefault(
        "claim_safety",
        {
            "status": (
                "fallback_source_backed_numbers"
                if _canonical_source_numeric_tokens(context)
                else "fallback_safe_no_specific_numbers"
            ),
            "unsupported_claims": [],
            "policy": SCRIPT_GENERATION_CONTRACT["forbidden_claim_policy"],
        },
    )
    payload.setdefault(
        "script_quality",
        {
            "status": "fallback_human_copy",
            "markers": [],
            "policy": HUMAN_COPY_RULES,
        },
    )
    return payload


def _legacy_body_to_segments(
    context: ProductionContext,
    hook: dict[str, Any],
    body: list[str],
    cta: str,
    profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    profile = profile or resolve_channel_profile(context)
    clean_body = [clean_words(item, 20) for item in body if item.strip()]
    is_compound_wealth = profile.key == "wealth_blueprint" and _is_compound_interest_topic(context)
    minimum_body_lines = 8 if is_compound_wealth else 6
    while len(clean_body) < minimum_body_lines:
        if is_compound_wealth:
            compound_fillers = (
                "Each new period starts from the balance left by the period before it.",
                "That is the mechanism: interest can begin earning interest of its own.",
            )
            clean_body.append(compound_fillers[len(clean_body) % len(compound_fillers)])
        elif profile.key in {"wealth_blueprint", "sigma_squad", "dark_chronicles"}:
            clean_body.append("One small detail changes how the whole scene feels.")
        else:
            clean_body.append("Najprostsza wersja wygrywa, bo da sie ja wykonac od razu.")
    clean_body = clean_body[:9]
    clean_cta = clean_words(cta, 16)
    hook_visual, intro_visual = _opening_visual_notes(context, profile, hook)
    segments = [
        {
            "id": "s01",
            "type": "hook",
            "text": hook["text"],
            "duration_target_sec": 3,
            "visual_note": hook_visual,
        },
        {
            "id": "s02",
            "type": "intro",
            "text": _non_repeating_opening_line(hook, profile),
            "duration_target_sec": 5,
            "visual_note": intro_visual,
        },
    ]
    for index, line in enumerate(clean_body, start=3):
        visual_note = (
            _compound_interest_visual_note(line, index - 3)
            if profile.key == "wealth_blueprint" and _is_compound_interest_topic(context)
            else _fallback_visual_note(profile, line, index - 3)
        )
        segments.append(
            {
                "id": f"s{index:02d}",
                "type": "body",
                "text": line,
                "duration_target_sec": 4 if is_compound_wealth else 6,
                "visual_note": visual_note,
            }
        )
    segments.append(
        {
            "id": f"s{len(segments) + 1:02d}",
            "type": "cta",
            "text": clean_cta,
            "duration_target_sec": 5,
            "visual_note": profile.cta_visual,
        }
    )
    return _finalize_script(context, segments, profile)


def _non_repeating_opening_line(hook: dict[str, Any], profile: ProductionChannelProfile) -> str:
    opening = " ".join(str(hook.get("opening_line") or "").split()).strip()
    hook_text = " ".join(str(hook.get("text") or "").split()).strip()
    hook_words = {word for word in re.findall(r"[a-z']+", hook_text.casefold()) if len(word) > 2}
    opening_words = {word for word in re.findall(r"[a-z']+", opening.casefold()) if len(word) > 2}
    overlap = len(hook_words & opening_words) / max(1, min(len(hook_words), len(opening_words)))
    if overlap < 0.5:
        return opening
    if profile.key == "wealth_blueprint":
        return "You feel paid, so every small yes suddenly feels harmless."
    if profile.key == "dark_chronicles":
        return "The first detail sounds small. Then the record turns darker."
    return "The first detail creates the tension. The next one changes the story."


def _opening_visual_notes(
    context: ProductionContext,
    profile: ProductionChannelProfile,
    hook: dict[str, Any],
) -> tuple[str, str]:
    topic_text = " ".join(
        (
            context.topic.title,
            context.topic.description,
            str(hook.get("text") or ""),
            str(hook.get("opening_line") or ""),
        )
    ).casefold()
    if profile.key == "dark_chronicles" and any(
        token in topic_text for token in ("dancing plague", "danced", "dance", "1518", "strasbourg")
    ):
        return (
            "Close action shot of an exhausted woman dancing uncontrollably in a dramatic off-balance step, "
            "her worn shoes scraping cobblestones while alarmed 1518 Strasbourg witnesses recoil behind her.",
            "Wide crowded 1518 Strasbourg square with dozens of townspeople dancing in visible exhaustion while "
            "families watch from timber-framed windows and civic guards try to control the street.",
        )
    if profile.key == "wealth_blueprint":
        if _is_compound_interest_topic(context):
            return (
                "A mature financial planning desk with a blank calendar, analog calculator, and handwritten ledger, "
                "one contribution marker placed at the start of a long timeline, no readable text or digits.",
                "Close editorial shot of one starting contribution passing through two calendar periods, with the "
                "second growth layer visibly building on the first, no labels, text, or fabricated chart values.",
            )
        return (
            "A nearly empty leather wallet beside a trail of small everyday purchases on a dark walnut desk, "
            "one gold coin rolling away toward the edge, mature premium finance documentary.",
            "A visual sequence of delivery bag, subscription token, and upgrade box draining separate coins from "
            "one household budget jar, charcoal and emerald editorial lighting, no people or text.",
        )
    if profile.key == "sigma_squad":
        return (
            "Three original toy-like office characters suddenly freeze into the same absurd looping walk at once, "
            "neon cyan and toxic green room, exaggerated surprise, no branded game assets.",
            "A surreal school hallway where original colorful characters repeat identical motions like a broken "
            "simulation while one confused character tries to interrupt the loop.",
        )
    return profile.hook_opening_visual, profile.intro_visual


def _fallback_visual_note(profile: ProductionChannelProfile, line: str, index: int) -> str:
    """Nadaje kolejnym zdaniom osobne motywy zamiast kopiowac jeden prompt."""
    if profile.key in {"dark_chronicles", "history"}:
        options = (
            "A lone woman dancing on a sixteenth-century Strasbourg street while worried neighbors watch from timber doorways.",
            "Worn shoes moving across sunlit cobblestones as the church shadow advances across the empty street.",
            "A public square filling with dancers while witnesses lean from windows and point toward the growing crowd.",
            "Civic leaders opening a wooden hall as musicians carry drums and string instruments toward the dancers.",
            "An exhausted dancer supported by two townspeople at the edge of the square in harsh summer light.",
            "A civic clerk moving wooden tally tokens beside sealed records while worried officials argue behind him.",
            "A tense group of townspeople gathered around one distressed dancer, visualizing collective fear without supernatural imagery.",
            "The Strasbourg city gate at dawn with abandoned shoes in the foreground and an uncertain empty street beyond.",
        )
        return options[index % len(options)]
    if profile.key not in {"wealth_blueprint", "finance"}:
        return profile.body_visual
    options = (
        "An unopened wallet beside a large analog clock at the exact moment a paycheck would arrive, no text, no people.",
        "A paper delivery bag, takeaway cup, and parcel forming the first three links of a domino chain toward an empty wallet.",
        "A premium sneaker, wireless headphones, and takeaway cup appearing one by one on a checkout conveyor, no labels.",
        "One small coin rolling toward a dark floor drain beside a delivery bag, cinematic macro shot.",
        "Many tiny purchase objects piling into a barrier across a clean path leading toward a small green plant.",
        "A cracked transparent pipe leaking coins into three ordinary purchases: delivery bag, remote control, and headphones.",
        "An hourglass filled with coins above a nearly empty wallet, sharp editorial still life.",
        "A mechanical coin sorter splitting coins between a shopping basket and a ceramic savings bowl.",
    )
    lowered = line.casefold()
    if "delivery" in lowered or "subscription" in lowered:
        return options[2]
    if "leak" in lowered:
        return options[5]
    if "future" in lowered or "together" in lowered:
        return options[4]
    if "hour" in lowered or "paycheck" in lowered:
        return options[0]
    return options[index % len(options)]


def _compound_interest_visual_note(line: str, index: int) -> str:
    options = (
        "Macro documentary shot of a real hand placing one banknote into a plain glass jar on a walnut desk, soft window light, shallow depth of field.",
        "Premium three-dimensional motion graphic: a small emerald layer settles on top of an ivory base, then the combined block becomes the next base, no text.",
        "A restrained time-lapse of one tree ring becoming several rings, photographed like a premium financial editorial, dark neutral background.",
        "Two physical stacks on a studio table: the second is visibly the first stack plus one thin new layer, controlled softbox lighting, no printed digits.",
        "Overhead shot of an adult hand advancing a single marker across two clean calendar squares, realistic paper texture, no readable labels.",
        "Close shot of an hourglass reflected in a dark glass desk while a small stack grows beside it, consistent exposure, no flare or bloom.",
        "A person at a kitchen table testing one scenario with a calculator and blank notebook, candid natural posture, no readable screen or text.",
    )
    lowered = line.casefold()
    if "interest on" in lowered or "prior interest" in lowered:
        return options[1]
    if "year" in lowered or "period" in lowered:
        return options[3]
    if "time" in lowered or "early" in lowered:
        return options[2]
    if "contribution" in lowered:
        return options[4]
    if "calculate" in lowered or "scenario" in lowered:
        return options[6]
    return options[index % len(options)]


def _normalize_segments(context: ProductionContext, hook: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    profile = resolve_channel_profile(context)
    segments: list[dict[str, Any]] = []
    for index, raw in enumerate(parsed.get("segments", []), start=1):
        if not isinstance(raw, dict):
            continue
        text = clean_words(str(raw.get("text") or ""), 24)
        if not text:
            continue
        try:
            duration = int(raw.get("duration_target_sec") or 5)
        except (TypeError, ValueError):
            duration = 5
        segments.append(
            {
                "id": str(raw.get("id") or f"s{index:02d}")[:12],
                "type": str(raw.get("type") or "body"),
                "text": text,
                "duration_target_sec": max(2, min(10, duration)),
                "visual_note": str(raw.get("visual_note") or profile.body_visual)[:400],
            }
        )
    if segments and segments[0]["type"] != "hook":
        segments.insert(
            0,
            {
                "id": "s00",
                "type": "hook",
                "text": hook["text"],
                "duration_target_sec": 3,
                "visual_note": profile.hook_opening_visual,
            },
        )
    title_options = parsed.get("title_options") if isinstance(parsed.get("title_options"), list) else []
    result = _finalize_script(context, segments, profile)
    if title_options:
        result["title_options"] = [clean_words(str(item), 12) for item in title_options[:3]]
    if parsed.get("description_draft"):
        result["description_draft"] = str(parsed["description_draft"])[:1200]
    if isinstance(parsed.get("tags"), list):
        result["tags"] = [clean_words(str(item), 4) for item in parsed["tags"][:15] if str(item).strip()]
    return result


def _finalize_script(
    context: ProductionContext,
    segments: list[dict[str, Any]],
    profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    profile = profile or resolve_channel_profile(context)
    if segments:
        first = dict(segments[0])
        first["text"] = _fit_fast_hook(str(first.get("text") or ""), profile)
        first["duration_target_sec"] = min(3, int(first.get("duration_target_sec") or 3))
        segments = [first, *segments[1:]]
    total_duration = sum(int(segment["duration_target_sec"]) for segment in segments)
    total_words = sum(len(str(segment["text"]).split()) for segment in segments)
    estimated_duration = (
        _estimated_spoken_duration_seconds(total_words)
        if profile.key == "wealth_blueprint"
        else max(30, min(60, total_duration))
    )
    profile_titles = _profile_title_options(context, profile)
    return {
        "segments": segments,
        "channel_profile": {
            "key": profile.key,
            "language_code": profile.language_code,
            "content_label": profile.content_label,
        },
        "language_code": profile.language_code,
        "total_duration_target_sec": estimated_duration,
        "title_options": [
            clean_words(context.topic.title, 12),
            profile_titles[0],
            profile_titles[1],
        ],
        "description_draft": "\n".join(segment["text"] for segment in segments),
        "tags": list(profile.tags),
        "hook": segments[0]["text"] if segments else "",
        "opening_line": segments[1]["text"] if len(segments) > 1 else "",
        "body": [segment["text"] for segment in segments if segment["type"] == "body"],
        "cta": next((segment["text"] for segment in segments if segment["type"] == "cta"), ""),
        "total_words": total_words,
        "estimated_duration_s": estimated_duration,
    }


def _estimated_spoken_duration_seconds(total_words: int) -> int:
    """Estimate narration length from words instead of trusting LLM scene timings."""
    if total_words <= 0:
        return 0
    return max(1, round(total_words / ESTIMATED_SPOKEN_WORDS_PER_SECOND))


def _profile_title_options(
    context: ProductionContext,
    profile: ProductionChannelProfile,
) -> tuple[str, str]:
    topic = f"{context.topic.title} {context.topic.description}".casefold()
    if profile.key == "wealth_blueprint" and _is_compound_interest_topic(context):
        return (
            "Compound Interest Rewards Time, Not Hype",
            "How Interest Starts Earning Interest",
        )
    if profile.key == "wealth_blueprint":
        if any(token in topic for token in ("paycheck", "spending", "budget", "purchase")):
            return (
                "The Small Spending Pattern Draining Your Week",
                "Stop Letting Convenience Spend Your Paycheck",
            )
        if "inflation" in topic:
            return (
                "Why Inflation Quietly Shrinks Your Money",
                "What Rising Prices Do to Your Savings",
            )
        return (
            "The Money Habit Most People Miss",
            "A Better Way to Plan Your Money",
        )
    if profile.key == "dark_chronicles":
        if any(token in topic for token in ("dancing plague", "1518", "strasbourg")):
            return profile.title_options
        if any(token in topic for token in ("law", "court", "punish", "fine")):
            return (
                "The Medieval Law That Turned Shame Public",
                "When the Street Became a Courtroom",
            )
        return (
            "The Record That Turned the Story Darker",
            "The Detail History Almost Lost",
        )
    return profile.title_options


def _fit_fast_hook(text: str, profile: ProductionChannelProfile) -> str:
    """Skraca pierwszy hook tak, żeby realnie mieścił się w pierwszych 3 sekundach."""
    cleaned = " ".join(text.split()).strip()
    words = cleaned.split()
    if not cleaned:
        return cleaned
    lowered = cleaned.casefold()
    if profile.key in {"dark_chronicles", "history"} and any(
        token in lowered for token in ("dancing plague", "danced", "dance", "feet bled")
    ):
        return "They danced until their feet bled"
    if "?" in cleaned and len(words) <= 14:
        return cleaned
    first_clause = re.match(r"^(.{8,80}?[.!?])(?:\s|$)", cleaned)
    if first_clause and len(first_clause.group(1).split()) <= 8:
        return first_clause.group(1)
    if len(words) <= 8:
        while words and words[-1].rstrip(".?!,;:").casefold() in {"and", "or", "but", "with", "to", "because"}:
            words.pop()
        return " ".join(words) or cleaned

    if profile.key in {"wealth_blueprint", "finance"}:
        if "skip" in lowered and "rule" in lowered and ("money" in lowered or "pien" in lowered):
            return "Most people skip this money rule"
        if "paycheck" in lowered and ("disappear" in lowered or "vanish" in lowered):
            return "Your paycheck disappears first"
        if "saving" in lowered and "rich" in lowered:
            return "Saving money is not enough"
        if "invest" in lowered and "first" in lowered:
            return "Do this before investing"
    if profile.key in {"dark_chronicles", "history"}:
        if "law" in lowered and "suffer" in lowered:
            return "A trivial law made people suffer"
        if "punish" in lowered and ("court" in lowered or "courts" in lowered):
            return "The court turned punishment public"
        if any(token in lowered for token in ("hidden", "secret", "forgotten")):
            return clean_words(cleaned, 7)
        if lowered.startswith(("a ", "an ", "the ", "they ", "this ")):
            return clean_words(cleaned, 7).rstrip(".?!")
        if "?" not in cleaned:
            return f"Why {clean_words(cleaned, 6)}?"
    if profile.key in {"sigma_squad", "brainrot"}:
        return clean_words(cleaned, 6)
    shortened = clean_words(cleaned, 7)
    words = shortened.rstrip(".?!,;:").split()
    while words and words[-1].casefold() in {"and", "or", "but", "with", "to", "because"}:
        words.pop()
    return " ".join(words) or shortened


def _fallback_body(
    context: ProductionContext,
    profile: ProductionChannelProfile,
    hook: dict[str, Any] | None = None,
) -> list[str]:
    if profile.key == "history":
        return [
            "This story does not need fake dates. It needs the right context.",
            "First, look at the place, the people, and the decision that started the chain.",
            "Then separate the record from the legend, because that is where the tension lives.",
            "The strongest detail is usually not loud. It is the one that keeps repeating.",
            "That is why one forgotten moment can change how the entire story feels.",
            "If the evidence is thin, say less, but make every line honest.",
        ]
    if profile.key == "dark_chronicles":
        hook = hook or {}
        topic_text = f"{context.topic.title} {context.topic.description} {hook.get('text') or ''} {hook.get('opening_line') or ''}".casefold()
        if any(token in topic_text for token in ("dancing plague", "danced", "dance", "1518", "strasbourg")):
            return [
                "In July 1518, one woman began dancing in a Strasbourg street.",
                "Days passed, and she kept moving.",
                "Then neighbors joined until the street became a crowd.",
                "Authorities opened halls and hired musicians, hoping movement would cure it.",
                "Instead, exhausted dancers collapsed in the summer heat.",
                "Historical accounts disagree about deaths, but not about the panic.",
                "Modern theories point to extreme stress and mass psychogenic illness.",
                "No explanation fully closes the case five centuries later.",
            ]
        if any(token in topic_text for token in ("vanished", "disappeared", "map overnight", "village")):
            return [
                "The map kept the place longer than the people did.",
                "At first, it looked like a quiet village story.",
                "Then the names stopped behaving like normal records.",
                "A door closes, a road changes, and suddenly the trail goes thin.",
                "The eerie part is not the disappearance itself.",
                "It is how little noise the archive makes afterward.",
                "Some stories end with a body.",
                "This one ends with an empty space on a map.",
            ]
        if any(token in topic_text for token in ("law", "court", "courts", "punish", "punishment", "fine", "fines")):
            return [
                "The street became the courtroom.",
                "Someone broke a rule that sounds tiny now.",
                "Then the court turned the street into a warning.",
                "The fine mattered, but the shame mattered more.",
                "People did not just hear about the law. They watched it happen.",
                "That is why old rules can feel darker than old weapons.",
                "They were not only punishing one person.",
                "They were teaching everyone else to be afraid.",
            ]
        return [
            "The first detail should feel small enough to believe.",
            "Then the record turns, and the scene stops feeling normal.",
            "A street, a witness, or an object becomes the center of the story.",
            "The danger is not always loud.",
            "Sometimes it is a pattern people noticed too late.",
            "Keep the myth outside the frame until the evidence is clear.",
            "The mood can be dark, but the claim has to stay honest.",
            "End on the one detail that refuses to leave the room.",
        ]
    if profile.key == "brainrot":
        return [
            "It works because it looks like a glitch that suddenly found a rhythm.",
            "First you see something absurd, then your brain tries to explain it.",
            "This is not a normal scene. It is safe chaos built for a fast scroll.",
            "The best brainrot has one simple idea and a few weird movements.",
            "If the scene becomes too logical, it loses the whole joke.",
            "So keep the absurdity, but avoid violence, logos, and useless text.",
        ]
    if profile.key == "wealth_blueprint":
        if _is_compound_interest_topic(context):
            if not _canonical_source_numeric_tokens(context):
                return [
                    "Compound interest means earning interest on your starting money and on prior interest.",
                    "After one period, that prior interest joins the base for the next calculation.",
                    "The next period can therefore begin from a larger base than the original amount.",
                    "More time gives the same mechanism more periods to repeat.",
                    "Starting earlier gives the same process more periods to repeat.",
                    "This explains a mechanism, not a guaranteed return.",
                    "Calculate one long-term contribution scenario before making an investment decision.",
                ]
            return [
                "Imagine you put one hundred dollars in an account earning a hypothetical five percent a year.",
                "After the first year, you earn five dollars, so now you have one hundred five.",
                "Here's the part people miss.",
                "In the second year, that five percent is calculated on one hundred five, not the original hundred.",
                "So you earn five dollars and twenty-five cents.",
                "That extra quarter is interest earning interest.",
                "It feels tiny over two years, but the same mechanism keeps building on a larger base.",
                "No magic and no guarantee. Try the math with one realistic amount you could actually leave alone.",
            ]
        return [
            "Your paycheck lands, and for one moment you feel richer than yesterday.",
            "Then convenience gets louder.",
            "One delivery. One subscription. One tiny upgrade.",
            "Each feels too small to matter, but together they become a pattern.",
            "By Friday, the money did not vanish. It was assigned without a plan.",
            "So create friction before the first purchase, not regret after the fifth.",
            "Move one decision out of autopilot before you think about investing.",
            "The boring pause is what keeps the rest of your paycheck alive.",
        ]
    if profile.key == "sigma_squad":
        return [
            "The first frame has to feel chaotic before anyone thinks.",
            "Start with one absurd rule, then make the scene escalate fast.",
            "Use Sigma, Ohio, rizz, or NPC energy as the joke, not as random filler.",
            "Keep every line clean, loud, and easy to understand on a fast scroll.",
            "No real-person targeting, no dangerous challenge, and no copyrighted character names.",
            "The goal is safe meme chaos that feels native, not an adult forcing slang.",
        ]
    return [
        "This problem looks bigger than it is in practice.",
        "First, separate the emotion from one concrete decision.",
        "Then check the numbers, but only the numbers that change the outcome.",
        "Most people react too late because they wait for the perfect moment.",
        "A better move is a small test, a quick measurement, and one correction.",
        "That way you stop guessing and start seeing what actually works.",
    ]


def _unsupported_numeric_claims(
    script: dict[str, Any],
    intelligence: dict[str, Any],
    *,
    context: ProductionContext | None = None,
) -> list[str]:
    allowed = _allowed_numeric_claim_tokens(intelligence)
    if context is not None:
        allowed.update(_canonical_source_numeric_tokens(context))
    text_parts: list[str] = []
    for segment in script.get("segments") or []:
        if isinstance(segment, dict):
            text_parts.append(str(segment.get("text") or ""))
    text_parts.extend(
        [
            str(script.get("description_draft") or ""),
            " ".join(str(item) for item in script.get("title_options") or []),
        ]
    )
    unsupported: list[str] = []
    for token in _numeric_claim_tokens(" ".join(text_parts)):
        normalized = _normalize_numeric_claim(token)
        if normalized and normalized not in allowed and normalized not in unsupported:
            unsupported.append(normalized)
    return unsupported


def _profile_mismatch_markers(script: dict[str, Any], profile: ProductionChannelProfile) -> list[str]:
    text_parts: list[str] = []
    for segment in script.get("segments") or []:
        if isinstance(segment, dict):
            text_parts.append(str(segment.get("text") or ""))
            text_parts.append(str(segment.get("visual_note") or ""))
    text_parts.extend(
        [
            str(script.get("description_draft") or ""),
            " ".join(str(item) for item in script.get("title_options") or []),
            " ".join(str(item) for item in script.get("tags") or []),
        ]
    )
    text = " ".join(text_parts).casefold()
    mismatches: list[str] = []
    if profile.key not in {"finance", "wealth_blueprint"}:
        for marker in ("finanse", "pieniadze", "budzet", "zarobk", "portfel", "bank", "financial", "finance"):
            if marker in text and marker not in mismatches:
                mismatches.append(marker)
    if profile.key not in {"history", "dark_chronicles"}:
        for marker in ("historia polski", "sredniowie", "starozyt", "archiw", "bitwa", "historyczny"):
            if marker in text and marker not in mismatches:
                mismatches.append(marker)
    if profile.key not in {"brainrot", "sigma_squad"}:
        for marker in ("brainrot", "memy ai", "glupoty ai", "surreal", "absurd ai"):
            if marker in text and marker not in mismatches:
                mismatches.append(marker)
    return mismatches[:8]


def _script_quality_markers(script: dict[str, Any], profile: ProductionChannelProfile) -> list[str]:
    markers: list[str] = []
    spoken_text_parts: list[str] = []
    for segment in script.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        text = " ".join(str(segment.get("text") or "").split())
        if text:
            spoken_text_parts.append(text)
        lowered = text.casefold()
        if profile.key in {"wealth_blueprint", "dark_chronicles", "sigma_squad"}:
            bad_phrases = (
                "the topic ",
                "this topic matters",
                "this topic is important",
                "topic why ",
                "topic while ",
                "content type:",
                "approved hook",
                "opening line:",
                "history is full of surprises",
                "in today's video",
                "in this short",
                "in this video",
                "this is education, not",
                "not a stock pick",
                "promise of future returns",
                "make sure to like and subscribe",
                "what other strange",
                "shaped the medieval world",
            )
        else:
            bad_phrases = (
                "temat ",
                "ten temat ",
                "typ tresci:",
                "typ treści:",
                "zatwierdzony hook",
                "linia otwarcia:",
            )
        for phrase in bad_phrases:
            if lowered.startswith(phrase) or f" {phrase}" in lowered:
                markers.append(phrase.strip(": "))
                break
    spoken_text = " ".join(spoken_text_parts).casefold()
    spoken_word_count = len(spoken_text.split())
    robotic_phrases = (
        "it is important to",
        "it is worth noting",
        "in conclusion",
        "the key takeaway is",
        "let's dive in",
        "let us explore",
        "understanding this is crucial",
        "this demonstrates that",
        "this illustrates how",
        "as we can see",
    )
    if any(phrase in spoken_text for phrase in robotic_phrases):
        markers.append("robotic_explainer_phrase")

    spoken_connectors = (" but ", " so ", " because ", " instead", "here's", " now ", " then ")
    if profile.key == "wealth_blueprint" and spoken_word_count >= 70:
        if not any(connector in f" {spoken_text} " for connector in spoken_connectors):
            markers.append("finance_copy_missing_spoken_connectors")
        segment_lengths = [len(text.split()) for text in spoken_text_parts if text]
        if len(segment_lengths) >= 5 and max(segment_lengths) - min(segment_lengths) <= 2:
            markers.append("finance_copy_monotone_sentence_rhythm")
    concrete_words = CONCRETE_WORDS_BY_PROFILE.get(profile.key, ())
    if concrete_words:
        concrete_hits = sum(1 for word in concrete_words if word in spoken_text)
        if concrete_hits < 2:
            markers.append("too_abstract_no_concrete_scene")
    if profile.key == "wealth_blueprint":
        if spoken_word_count < WEALTH_BLUEPRINT_MIN_SPOKEN_WORDS:
            markers.append("wealth_blueprint_narration_too_short")
        elif spoken_word_count > WEALTH_BLUEPRINT_MAX_SPOKEN_WORDS:
            markers.append("wealth_blueprint_narration_too_long")
        if "you" not in spoken_text and "your" not in spoken_text:
            markers.append("finance_copy_not_viewer_direct")
        if "behavior" in spoken_text and "receipt" not in spoken_text and "subscription" not in spoken_text:
            markers.append("finance_copy_too_abstract_behavior")
    if profile.key == "dark_chronicles":
        if "history is full" in spoken_text or "strange laws shaped" in spoken_text:
            markers.append("dark_history_generic_school_closer")
        if "archive" not in spoken_text and "court" not in spoken_text and "street" not in spoken_text:
            markers.append("dark_history_no_documentary_scene")
    return list(dict.fromkeys(markers))[:8]


def _is_compound_interest_topic(context: ProductionContext) -> bool:
    topic_text = f"{context.topic.title} {context.topic.description}".casefold()
    return "compound interest" in topic_text or ("compound" in topic_text and "interest" in topic_text)


def _topic_fit_markers(
    context: ProductionContext,
    script: dict[str, Any],
    profile: ProductionChannelProfile,
) -> list[str]:
    """Reject a fluent script when its body silently switches to another topic."""
    if profile.key != "wealth_blueprint" or not _is_compound_interest_topic(context):
        return []
    segments = [segment for segment in script.get("segments") or [] if isinstance(segment, dict)]
    script_text = " ".join(
        str(segment.get("text") or "")
        for segment in segments
    ).casefold()
    explanatory_text = " ".join(
        str(segment.get("text") or "")
        for segment in segments
        if str(segment.get("type") or "").casefold() not in {"hook", "cta", "outro"}
    ).casefold()
    mechanism_present = "compound interest" in explanatory_text or (
        "interest" in explanatory_text
        and any(token in explanatory_text for token in ("prior", "previous", "base", "compounds"))
    )
    time_present = any(
        token in explanatory_text for token in ("time", "year", "period", "earlier", "repeat", "growth")
    )
    required_numbers = _canonical_source_numeric_tokens(context)
    used_numbers = {_normalize_numeric_claim(token) for token in _numeric_claim_tokens(script_text)}
    source_example_present = not required_numbers or required_numbers.issubset(used_numbers)
    markers: list[str] = []
    if not mechanism_present or not time_present:
        markers.append("topic_mismatch_compound_interest")
    if not source_example_present:
        markers.append("compound_interest_missing_source_example")
    return markers


def _canonical_source_numeric_tokens(context: ProductionContext) -> set[str]:
    try:
        contract = canonical_stability_contracts().get(context.topic.id) or {}
    except (OSError, ValueError):
        return set()
    tokens: set[str] = set()
    for fact in contract.get("source_facts") or []:
        tokens.update(_normalize_numeric_claim(token) for token in _numeric_claim_tokens(str(fact)))
    return {token for token in tokens if token}


def _allowed_numeric_claim_tokens(intelligence: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for row in intelligence.get("youtube_examples") or []:
        if not isinstance(row, dict):
            continue
        for key in ("view_count", "like_count", "comment_count"):
            value = row.get(key)
            if value is not None:
                allowed.add(_normalize_numeric_claim(str(value)))
    for row in intelligence.get("google_trends") or []:
        if not isinstance(row, dict):
            continue
        value = row.get("interest_score")
        if value is not None:
            allowed.add(_normalize_numeric_claim(str(value)))
    score = intelligence.get("score")
    if score is not None:
        allowed.add(_normalize_numeric_claim(str(score)))
    source_record = _stability_source_record(intelligence)
    for fact in source_record.get("source_facts") or []:
        for token in _numeric_claim_tokens(str(fact)):
            allowed.add(_normalize_numeric_claim(token))
    return {item for item in allowed if item}


def _stability_source_record(intelligence: dict[str, Any]) -> dict[str, Any]:
    candidate = intelligence.get("source_record")
    return parse_stability_source_record(candidate) or {}


def _normalize_number_separators(value: str) -> str:
    number = value.replace(" ", "")
    if not number:
        return ""
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            return number.replace(".", "").replace(",", ".")
        return number.replace(",", "")
    if "," in number:
        parts = number.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            return "".join(parts)
        return number.replace(",", ".")
    if "." in number:
        parts = number.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3):
            return "".join(parts)
    return number


def _numeric_claim_tokens(text: str) -> list[str]:
    text = re.sub(
        r"([$\u20ac])\s*(\d{1,3}(?:[ ,.\u00a0]\d{3})+|\d+(?:[,.]\d+)?)",
        lambda match: f"{match.group(2)} {'usd' if match.group(1) == '$' else 'eur'}",
        text,
    )
    candidates = re.findall(
        r"(?<![A-Za-z0-9])(?:\d{1,3}(?:[ .]\d{3})+|\d+(?:[,.]\d+)?)(?:\s?(?:%|proc\.?|zl|zł|zloty|złoty|pln|usd|\$|€|eur))?",
        text,
        flags=re.IGNORECASE,
    )
    tokens: list[str] = []
    for candidate in candidates:
        normalized = _normalize_numeric_claim(candidate)
        if not normalized:
            continue
        tokens.append(candidate)
    return tokens


def _normalize_numeric_claim(value: str) -> str:
    text = str(value or "").casefold().strip()
    if not text:
        return ""
    unit = ""
    if "%" in text or "proc" in text:
        unit = "%"
    elif "zl" in text or "zł" in text or "zloty" in text or "złoty" in text or "pln" in text:
        unit = "pln"
    elif "$" in text or "usd" in text:
        unit = "usd"
    elif "€" in text or "eur" in text:
        unit = "eur"
    number_match = re.search(r"\d{1,3}(?:[ .]\d{3})+|\d+(?:[,.]\d+)?", text)
    number = _normalize_number_separators(number_match.group(0) if number_match else "")
    if not number:
        return ""
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    return f"{number}{unit}"


def _style_guide_prompt_block(style_guide: str | None) -> str:
    if not style_guide:
        return ""
    return (
        "\n\nCHANNEL STYLE GUIDE FROM OBSIDIAN / MEMORY:\n"
        f"{style_guide[:6000]}\n"
        "END CHANNEL STYLE GUIDE.\n"
    )


def _read_topic_intelligence(settings: Settings, context: ProductionContext) -> dict[str, Any]:
    search_terms = _topic_search_terms(context.topic.title, context.topic.description)
    with connect(settings) as db:
        topic = db.execute(
            """
            SELECT *
            FROM topic_queue
            WHERE id = ? AND account_id = ?
            """,
            (context.topic.id, context.account_id),
        ).fetchone()
        topic_payload = dict(topic) if topic else {}
        inspired_by = str(topic_payload.get("inspired_by") or "")
        for term in _topic_search_terms(inspired_by, ""):
            if term not in search_terms:
                search_terms.append(term)
        yt_clauses = " OR ".join("(title LIKE ? OR niche LIKE ?)" for _ in search_terms)
        yt_params: list[Any] = [context.account_id]
        for term in search_terms:
            like = _like(term)
            yt_params.extend([like, like])
        yt_rows = db.execute(
            f"""
            SELECT video_id, title, view_count, like_count, comment_count, channel_title, published_at
            FROM yt_signals
            WHERE account_id = ?
              AND ({yt_clauses if yt_clauses else "1=1"})
            ORDER BY view_count DESC, collected_at DESC
            LIMIT 5
            """,
            yt_params,
        ).fetchall()
        if not yt_rows:
            yt_rows = db.execute(
                """
                SELECT video_id, title, view_count, like_count, comment_count, channel_title, published_at
                FROM yt_signals
                WHERE account_id = ?
                ORDER BY view_count DESC, collected_at DESC
                LIMIT 3
                """,
                (context.account_id,),
            ).fetchall()
        trend_clauses = " OR ".join("(keyword LIKE ? OR related_rising LIKE ? OR related_topics LIKE ?)" for _ in search_terms)
        trend_params: list[Any] = []
        for term in search_terms:
            like = _like(term)
            trend_params.extend([like, like, like])
        trend_rows = db.execute(
            f"""
            SELECT keyword, interest_score, related_rising, timeframe, geo
            FROM gtrends_signals
            WHERE {trend_clauses if trend_clauses else "1=1"}
            ORDER BY interest_score DESC, collected_at DESC
            LIMIT 5
            """,
            trend_params,
        ).fetchall()
        if not trend_rows:
            trend_rows = db.execute(
                """
                SELECT keyword, interest_score, related_rising, timeframe, geo
                FROM gtrends_signals
                ORDER BY interest_score DESC, collected_at DESC
                LIMIT 3
                """
            ).fetchall()

    source_record = parse_stability_source_record(topic_payload.get("inspired_by")) or {}
    return {
        "score": _float(topic_payload.get("total_score"), context.topic.total_score),
        "dimensions": {
            "viral_potential": _float(topic_payload.get("viral_potential"), 0.0),
            "search_demand": _float(topic_payload.get("search_demand"), 0.0),
            "competition_gap": _float(topic_payload.get("competition_gap"), 0.0),
            "trend_velocity": _float(topic_payload.get("trend_velocity"), 0.0),
            "niche_fit": _float(topic_payload.get("niche_fit"), 0.0),
            "freshness": _float(topic_payload.get("freshness"), 0.0),
        },
        "flags": {
            "clickbait_risk": bool(topic_payload.get("clickbait_risk")),
            "evergreen": bool(topic_payload.get("evergreen")),
            "shorts_potential": bool(topic_payload.get("shorts_potential")),
            "llm_unavailable": bool(topic_payload.get("llm_unavailable")),
        },
        "inspired_by": str(topic_payload.get("inspired_by") or ""),
        "source_record": source_record,
        "evidence_backed": bool(
            topic_payload.get("inspired_by")
            or _float(topic_payload.get("viral_potential"), 0.0) > 0
            or _float(topic_payload.get("search_demand"), 0.0) > 0
        ),
        "youtube_examples": [dict(row) for row in yt_rows],
        "google_trends": [
            {
                **dict(row),
                "related_rising": json_loads(row["related_rising"], []),
            }
            for row in trend_rows
        ],
    }


def _topic_intelligence_prompt_block(intelligence: dict[str, Any]) -> str:
    if not intelligence.get("evidence_backed"):
        return (
            "\n\nTOPIC INTELLIGENCE:\n"
            "- Brak twardych sygnalow dla tego tematu. Pisz ostroznie, bez danych liczbowych i bez obietnic.\n"
            "END TOPIC INTELLIGENCE.\n"
        )
    examples = []
    for row in (intelligence.get("youtube_examples") or [])[:3]:
        examples.append(
            {
                "title": row.get("title"),
                "views": row.get("view_count"),
                "likes": row.get("like_count"),
                "comments": row.get("comment_count"),
                "channel": row.get("channel_title"),
                "published_at": row.get("published_at"),
            }
        )
    trends = []
    for row in (intelligence.get("google_trends") or [])[:3]:
        trends.append(
            {
                "keyword": row.get("keyword"),
                "interest_score": row.get("interest_score"),
                "related_rising": (row.get("related_rising") or [])[:5],
                "timeframe": row.get("timeframe"),
                "geo": row.get("geo"),
            }
        )
    source_record = _stability_source_record(intelligence)
    primary_source = (
        {
            "source_name": source_record.get("source_name"),
            "source_url": source_record.get("source_url"),
            "source_facts": source_record.get("source_facts") or [],
            "forbidden_claims": source_record.get("forbidden_claims") or [],
            "visual_test_focus": source_record.get("visual_test_focus") or [],
            "production_brief": source_record.get("production_brief") or {},
        }
        if source_record
        else None
    )
    return (
        "\n\nTOPIC INTELLIGENCE FROM SQLITE intelligence.db:\n"
        f"- topic_score: {intelligence.get('score')}\n"
        f"- score_dimensions: {intelligence.get('dimensions')}\n"
        f"- flags: {intelligence.get('flags')}\n"
        f"- inspired_by: {intelligence.get('inspired_by') or 'none'}\n"
        f"- primary_source_contract: {primary_source or 'none'}\n"
        f"- youtube_examples: {examples}\n"
        f"- google_trends: {trends}\n"
        "Rules: treat these as context signals, not proof of future earnings. Use exact numbers only when they appear in "
        "primary_source_contract.source_facts or in the signal rows above. Follow the production brief and never use a forbidden claim.\n"
        "END TOPIC INTELLIGENCE.\n"
    )


def _like(value: str) -> str:
    words = [word for word in str(value or "").split() if len(word) >= 4]
    return f"%{(words[0] if words else value).strip()}%"


def _topic_search_terms(title: str, description: str) -> list[str]:
    raw = f"{title} {description}".casefold()
    terms: list[str] = []
    full_title = " ".join(str(title or "").split())
    if full_title:
        terms.append(full_title)
    stop = {"dlaczego", "ktore", "które", "temat", "short", "youtube", "jeden", "jedna"}
    for word in raw.replace(":", " ").replace(",", " ").split():
        cleaned = "".join(char for char in word if char.isalnum())
        if len(cleaned) >= 4 and cleaned not in stop and cleaned not in terms:
            terms.append(cleaned)
    return terms[:8]


def _float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
