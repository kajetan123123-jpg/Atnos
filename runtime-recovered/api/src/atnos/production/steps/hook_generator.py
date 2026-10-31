from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.memory.obsidian_style_guide import read_channel_style_guide
from atnos.production.channel_profile import ProductionChannelProfile, resolve_channel_profile
from atnos.production.faceless_playbook import faceless_playbook_prompt_block, faceless_playbook_summary
from atnos.production.models import ProductionContext, StepResult
from atnos.production.stability_topics import canonical_stability_contracts
from atnos.production.steps.common import ask_ollama_json, clean_words, write_json


HOOK_GENERATION_CONTRACT: dict[str, Any] = {
    "module": "atnos.production.steps.hook_generator",
    "output_artifact": "hook.json",
    "variant_count": 3,
    "max_hook_words": 12,
    "max_opening_line_words": 20,
    "selection_mode": "auto_highest_tension",
    "requires_tension_score": True,
    "safe_clickbait_rule": "high tension without misleading claims",
    "style_guide_source_order": ["obsidian_vault", "local_channel_ops_fallback"],
    "style_guide_reader": "atnos.memory.obsidian_style_guide.read_channel_style_guide",
}


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    style_guide = read_channel_style_guide(settings, context.account_id)
    profile = resolve_channel_profile(context, style_guide.content)
    style_block = _style_guide_prompt_block(style_guide.content)
    playbook = context.artifacts.get("faceless_playbook")
    playbook_block = faceless_playbook_prompt_block(playbook)
    source_contract = _source_contract(context)
    source_facts = source_contract.get("source_facts") if isinstance(source_contract, dict) else []
    source_block = f" Verified source facts: {source_facts}." if source_facts else ""
    prompt = (
        f"Generate YouTube Shorts hook options for a {profile.prompt_label}. "
        "Return JSON only: {\"variants\":[{\"id\":\"h01\","
        "\"variant\":\"curiosity|shock|benefit|question|contrast\","
        "\"text\":\"max 12 words\",\"opening_line\":\"max 20 words\","
        "\"tension_score\":0-100,\"reason\":\"short reason\"}]}. "
        "Generate exactly 3 variants. Pick high tension, but do not mislead. "
        f"No misleading clickbait. {profile.language_instruction} "
        "Follow the channel style guide when present, especially tone, taboo claims, clickbait boundaries, "
        "and recurring retention devices. "
        f"Topic: {context.topic.title}. Description: {context.topic.description}.{source_block}"
        f"{playbook_block}"
        f"{style_block}"
    )
    payload = await _llm_or_fallback(settings, context, prompt, profile)
    payload["style_guide"] = {
        "status": style_guide.status,
        "source": style_guide.source,
        "used": bool(style_guide.content),
    }
    payload["channel_profile"] = {"key": profile.key, "language_code": profile.language_code}
    payload["faceless_playbook"] = faceless_playbook_summary(playbook if isinstance(playbook, dict) else {})
    return write_json(context.artifact_dir / "hook.json", payload)


async def _llm_or_fallback(
    settings: Settings,
    context: ProductionContext,
    prompt: str,
    profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    profile = profile or resolve_channel_profile(context)
    rejected_claims: list[dict[str, Any]] = []
    rejected_topic_fit: list[dict[str, Any]] = []
    try:
        parsed = await ask_ollama_json(settings, prompt, timeout_s=75.0)
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("variants"), list):
        variants = [_normalize_variant(index, item) for index, item in enumerate(parsed["variants"][:3])]
        variants = [item for item in variants if item["text"] and item["opening_line"]]
        safe_variants: list[dict[str, Any]] = []
        for item in variants:
            markers = _unsafe_hook_claim_markers(context, item, profile)
            if markers:
                rejected_claims.append({"id": item["id"], "markers": markers})
                continue
            topic_markers = _hook_topic_fit_markers(context, item, profile)
            if topic_markers:
                rejected_topic_fit.append({"id": item["id"], "markers": topic_markers})
                continue
            safe_variants.append(item)
        variants = safe_variants
        if len(variants) >= 3:
            selected = max(variants, key=lambda item: item["tension_score"])
            return {
                "variants": variants,
                "selected_hook_id": selected["id"],
                "selected": selected,
                "selection_mode": "auto_highest_tension",
                "llm_unavailable": False,
                "llm_rejected_unsafe_claims": bool(rejected_claims),
                "unsafe_claim_markers": rejected_claims,
                "llm_rejected_topic_mismatch": bool(rejected_topic_fit),
                "topic_mismatch_markers": rejected_topic_fit,
            }

    title = clean_words(context.topic.title, 8)
    variants = _fallback_variants(context, profile, title)
    selected = max(variants, key=lambda item: item["tension_score"])
    return {
        "variants": variants,
        "selected_hook_id": selected["id"],
        "selected": selected,
        "selection_mode": "auto_highest_tension",
        "llm_unavailable": parsed is None,
        "llm_rejected_unsafe_claims": bool(rejected_claims),
        "unsafe_claim_markers": rejected_claims,
        "llm_rejected_topic_mismatch": bool(rejected_topic_fit),
        "topic_mismatch_markers": rejected_topic_fit,
    }


def _unsafe_hook_claim_markers(
    context: ProductionContext,
    variant: dict[str, Any],
    profile: ProductionChannelProfile,
) -> list[str]:
    if profile.key != "wealth_blueprint":
        return []
    text = f"{variant.get('text') or ''} {variant.get('opening_line') or ''}".casefold()
    allowed_numbers = _source_numeric_tokens(context)
    numeric_tokens = _numeric_claim_tokens(text)
    unsupported_numbers = numeric_tokens - allowed_numbers
    patterns = {
        "unsupported_large_money_claim": r"\b(?:hundreds|thousands|millions|billions)\b",
        "guarantee_language": r"\b(?:guaranteed|guarantee|guarantees|get rich|future returns?)\b",
        "unsourced_loss_claim": r"\bcosting you\b",
    }
    markers: list[str] = []
    for marker, pattern in patterns.items():
        if re.search(pattern, text):
            markers.append(marker)
    if unsupported_numbers:
        markers.append("unsupported_numeric_claim")
    return markers


def _hook_topic_fit_markers(
    context: ProductionContext,
    variant: dict[str, Any],
    profile: ProductionChannelProfile,
) -> list[str]:
    if profile.key != "wealth_blueprint":
        return []
    topic_text = f"{context.topic.title} {context.topic.description}".casefold()
    if "compound interest" not in topic_text and not ("compound" in topic_text and "interest" in topic_text):
        return []
    hook_text = f"{variant.get('text') or ''} {variant.get('opening_line') or ''}".casefold()
    source_numbers = _source_numeric_tokens(context)
    if source_numbers:
        used_numbers = _numeric_claim_tokens(hook_text) & source_numbers
        if len(used_numbers) < 2:
            return ["compound_interest_hook_missing_source_example"]
    relevant = any(token in hook_text for token in ("compound", "interest", "time", "year", "period", "growth", "curve"))
    return [] if relevant else ["topic_mismatch_compound_interest"]


def _fallback_variants(
    context: ProductionContext,
    profile: ProductionChannelProfile,
    title: str,
) -> list[dict[str, Any]]:
    if profile.key == "history":
        return [
            {
                "id": "h01",
                "variant": "curiosity",
                "text": f"This history detail changes {title}",
                "opening_line": "It sounds like a minor detail, but it changes how the whole story feels.",
                "tension_score": 78,
                "reason": "History curiosity without invented facts.",
            },
            {
                "id": "h02",
                "variant": "contrast",
                "text": "History almost forgot this detail",
                "opening_line": "It is not about a famous battle, but a small clue most people miss.",
                "tension_score": 84,
                "reason": "Strong historical tension without false precision.",
            },
            {
                "id": "h03",
                "variant": "question",
                "text": "Why does this fact still matter?",
                "opening_line": "History often hides the strangest part inside one quiet moment.",
                "tension_score": 75,
                "reason": "Question hook fit for documentary Shorts.",
            },
        ]
    if profile.key == "dark_chronicles":
        topic_text = f"{title} {context.topic.title} {context.topic.description}".casefold()
        if any(token in topic_text for token in ("law", "court", "punish", "punishment", "fine", "fines")):
            return [
                {
                    "id": "h01",
                    "variant": "shock",
                    "text": "A trivial law made people suffer",
                    "opening_line": "The punishment was not just the fine. It was being seen.",
                    "tension_score": 88,
                    "reason": "Concrete dark-history hook tied to law and public shame.",
                },
                {
                    "id": "h02",
                    "variant": "curiosity",
                    "text": "The court turned shame into a weapon",
                    "opening_line": "One small rule could push a private mistake into the street.",
                    "tension_score": 84,
                    "reason": "Specific historical tension without invented dates.",
                },
                {
                    "id": "h03",
                    "variant": "contrast",
                    "text": "This law sounds fake, but it was not",
                    "opening_line": "The strange part is how public the punishment became.",
                    "tension_score": 80,
                    "reason": "Safe curiosity hook for a weird-law topic.",
                },
            ]
        if any(token in topic_text for token in ("dancing plague", "danced", "dance", "1518", "strasbourg")):
            return [
                {
                    "id": "h01",
                    "variant": "shock",
                    "text": "They danced until they died",
                    "opening_line": "In 1518, a city watched hundreds of people move until their bodies failed.",
                    "tension_score": 88,
                    "reason": "Disturbing historical fact with withheld explanation.",
                },
                {
                    "id": "h02",
                    "variant": "curiosity",
                    "text": "One dancer became a city panic",
                    "opening_line": "The street looked ordinary until the crowd would not stop moving.",
                    "tension_score": 84,
                    "reason": "Scene-first mystery hook tied to the topic.",
                },
                {
                    "id": "h03",
                    "variant": "contrast",
                    "text": "No army arrived. The city still broke.",
                    "opening_line": "The danger looked like music, heat, and bodies that kept moving.",
                    "tension_score": 82,
                    "reason": "Cinematic contrast hook without gore.",
                },
            ]
        if any(token in topic_text for token in ("vanished", "disappeared", "map overnight", "village")):
            return [
                {
                    "id": "h01",
                    "variant": "curiosity",
                    "text": "This village vanished overnight",
                    "opening_line": "The records survived, but the people disappeared from the map.",
                    "tension_score": 88,
                    "reason": "Mystery hook without unsupported certainty.",
                },
                {
                    "id": "h02",
                    "variant": "contrast",
                    "text": "The map kept what people lost",
                    "opening_line": "A quiet place can become terrifying when the archive goes thin.",
                    "tension_score": 82,
                    "reason": "Atmospheric evidence-first hook.",
                },
                {
                    "id": "h03",
                    "variant": "question",
                    "text": "Why did the trail go silent?",
                    "opening_line": "The strange part is not leaving. It is how little was left behind.",
                    "tension_score": 80,
                    "reason": "Question hook grounded in records.",
                },
            ]
        return [
            {
                "id": "h01",
                "variant": "contrast",
                "text": "History remembers the wrong detail",
                "opening_line": "The famous version is simple, but the archive tells a darker story.",
                "tension_score": 84,
                "reason": "Documentary tension with credibility framing.",
            },
            {
                "id": "h02",
                "variant": "curiosity",
                "text": "One small record changes everything",
                "opening_line": "The detail looks harmless until it changes the whole scene.",
                "tension_score": 80,
                "reason": "Generic but concrete dark-history hook.",
            },
            {
                "id": "h03",
                "variant": "question",
                "text": "Why did this story survive?",
                "opening_line": "Some records last because they still feel wrong centuries later.",
                "tension_score": 76,
                "reason": "Safe curiosity hook for uncertain topic.",
            },
        ]
    if profile.key == "brainrot":
        return [
            {
                "id": "h01",
                "variant": "shock",
                "text": "AI made something that makes no sense",
                "opening_line": "This looks like a robot dream after one render too many.",
                "tension_score": 82,
                "reason": "Absurd safe meme tension.",
            },
            {
                "id": "h02",
                "variant": "curiosity",
                "text": "This brainrot should not work",
                "opening_line": "Nothing here makes sense, but your brain still wants to keep watching.",
                "tension_score": 86,
                "reason": "Strong retention for meme format.",
            },
            {
                "id": "h03",
                "variant": "contrast",
                "text": f"{title} sounds like an AI mistake",
                "opening_line": "That is exactly why the scene has such a weird rhythm.",
                "tension_score": 74,
                "reason": "Topic-linked surreal hook.",
            },
        ]
    if profile.key == "wealth_blueprint":
        title_lower = title.casefold()
        topic_text = f"{title} {context.topic.title} {context.topic.description}".casefold()
        if "compound interest" in topic_text or ("compound" in topic_text and "interest" in topic_text):
            if _source_numeric_tokens(context):
                return [
                    {
                        "id": "h01",
                        "variant": "curiosity",
                        "text": "How $100 becomes $110.25",
                        "opening_line": (
                            "Year two earns $5.25, even though the rate never changed."
                        ),
                        "tension_score": 96,
                        "reason": "Concrete source-backed transformation with an immediate unanswered mechanism.",
                    },
                    {
                        "id": "h02",
                        "variant": "contrast",
                        "text": "$100, then $105, then $110.25",
                        "opening_line": "The second year earns interest on more than the original $100.",
                        "tension_score": 92,
                        "reason": "The official example replaces abstract hype with visible progression.",
                    },
                    {
                        "id": "h03",
                        "variant": "question",
                        "text": "Where did the extra growth come from?",
                        "opening_line": "$100 at 5% becomes $105, then $110.25. The base changed.",
                        "tension_score": 89,
                        "reason": "A direct question resolved by the exact verified values.",
                    },
                ]
            return [
                {
                    "id": "h01",
                    "variant": "contrast",
                    "text": "Compound interest rewards time, not hype",
                    "opening_line": "The second year grows from more than the money you started with.",
                    "tension_score": 92,
                    "reason": "Topic-specific time-versus-hype contrast grounded in how compounding works.",
                },
                {
                    "id": "h02",
                    "variant": "curiosity",
                    "text": "Your interest can start earning interest",
                    "opening_line": "That quiet second layer is where time changes the calculation.",
                    "tension_score": 88,
                    "reason": "Explains the mechanism without promising a return.",
                },
                {
                    "id": "h03",
                    "variant": "question",
                    "text": "Why does starting earlier change the curve?",
                    "opening_line": "Because each completed period can add a larger base for the next one.",
                    "tension_score": 84,
                    "reason": "Viewer-first question tied directly to compound growth.",
                },
            ]
        if any(token in title_lower for token in ("broke", "paycheck", "spending", "money habit", "salary")):
            return [
                {
                    "id": "h01",
                    "variant": "question",
                    "text": "Where does your paycheck go first?",
                    "opening_line": "Watch what happens before the balance has time to feel real.",
                    "tension_score": 84,
                    "reason": "Concrete viewer-first curiosity with an immediate visual question.",
                },
                {
                    "id": "h02",
                    "variant": "danger_tension",
                    "text": "Your paycheck starts disappearing in one hour",
                    "opening_line": "Not because of rent. Because convenience gets louder the moment you feel paid.",
                    "tension_score": 92,
                    "reason": "Specific time pressure, personal stakes, and a clear narrative conflict.",
                },
                {
                    "id": "h03",
                    "variant": "contrast",
                    "text": "The expensive part looks harmless",
                    "opening_line": "It arrives as a delivery, a subscription, and one tiny upgrade.",
                    "tension_score": 88,
                    "reason": "Pattern interrupt that promises a concrete reveal instead of generic advice.",
                },
            ]
        return [
            {
                "id": "h01",
                "variant": "question",
                "text": f"Why does {title} matter?",
                "opening_line": "It looks simple, but one money habit changes the outcome.",
                "tension_score": 78,
                "reason": "Clear curiosity hook for personal finance.",
            },
            {
                "id": "h02",
                "variant": "contrast",
                "text": "Saving money will not make you rich",
                "opening_line": "The real gap starts with how your money behaves after you earn it.",
                "tension_score": 86,
                "reason": "Counterintuitive but safe finance tension.",
            },
            {
                "id": "h03",
                "variant": "benefit",
                "text": "Understand this before you invest",
                "opening_line": "One simple rule can stop a bad money decision before it starts.",
                "tension_score": 74,
                "reason": "Practical educational hook without advice.",
            },
        ]


    if profile.key == "sigma_squad":
        return [
            {
                "id": "h01",
                "variant": "shock",
                "text": "BRO WAIT is this Ohio energy?",
                "opening_line": "This starts normal for half a second, then the Sigma meter breaks.",
                "tension_score": 88,
                "reason": "Fast absurd visual-first hook.",
            },
            {
                "id": "h02",
                "variant": "contrast",
                "text": "Sigma mode activated at 3AM",
                "opening_line": "One NPC move turns the whole scene into pure brainrot chaos.",
                "tension_score": 84,
                "reason": "Strong Gen Alpha meme pattern without profanity.",
            },
            {
                "id": "h03",
                "variant": "question",
                "text": "Who has more rizz here?",
                "opening_line": "The answer looks obvious until Ohio logic enters the chat.",
                "tension_score": 80,
                "reason": "Question hook using safe slang.",
            },
        ]
    return [
        {
            "id": "h01",
            "variant": "question",
            "text": f"Why does {title} matter?",
            "opening_line": "It looks simple, but one detail changes the whole decision.",
            "tension_score": 76,
            "reason": "Clear question with practical stakes.",
        },
        {
            "id": "h02",
            "variant": "contrast",
            "text": "Builders move first. Everyone else waits.",
            "opening_line": "The difference does not start with income. It starts with reaction speed.",
            "tension_score": 84,
            "reason": "Strong contrast without unsupported promise.",
        },
        {
            "id": "h03",
            "variant": "benefit",
            "text": "Protect your money in 60 seconds",
            "opening_line": "You do not need more theory. You need a simple decision filter.",
            "tension_score": 72,
            "reason": "Practical benefit for Shorts format.",
        },
    ]


def _source_contract(context: ProductionContext) -> dict[str, Any]:
    try:
        return dict(canonical_stability_contracts().get(context.topic.id) or {})
    except (OSError, ValueError):
        return {}


def _source_numeric_tokens(context: ProductionContext) -> set[str]:
    contract = _source_contract(context)
    source_text = " ".join(str(item) for item in contract.get("source_facts") or [])
    return _numeric_claim_tokens(source_text)


def _numeric_claim_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"(?<![\w.])-?\d+(?:[.,]\d+)?", str(text or "")):
        raw = match.group(0).replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        tokens.add(f"{value:.8f}".rstrip("0").rstrip("."))
    return tokens


def _normalize_variant(index: int, item: Any) -> dict[str, Any]:
    data = item if isinstance(item, dict) else {}
    hook_id = str(data.get("id") or f"h{index + 1:02d}")[:24]
    try:
        tension = int(float(data.get("tension_score") or data.get("score") or 50))
    except (TypeError, ValueError):
        tension = 50
    return {
        "id": hook_id or uuid4().hex[:8],
        "variant": str(data.get("variant") or "curiosity"),
        "text": clean_words(str(data.get("text") or ""), 12),
        "opening_line": clean_words(str(data.get("opening_line") or ""), 20),
        "tension_score": max(0, min(100, tension)),
        "reason": clean_words(str(data.get("reason") or ""), 18),
    }


def _style_guide_prompt_block(style_guide: str | None) -> str:
    if not style_guide:
        return ""
    return (
        "\n\nCHANNEL HOOK STYLE GUIDE FROM OBSIDIAN / MEMORY:\n"
        f"{style_guide[:6000]}\n"
        "Use this for hook tone, retention style, forbidden claims, and safe-clickbait boundaries. "
        "Never include secrets or private account data in generated hooks.\n"
        "END CHANNEL HOOK STYLE GUIDE.\n"
    )
