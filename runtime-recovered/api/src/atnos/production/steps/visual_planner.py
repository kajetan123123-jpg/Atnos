from __future__ import annotations

import json
import re
from typing import Any

from atnos.core.config import Settings
from atnos.llm.role_registry import ModelRole, OllamaRoleRegistry
from atnos.memory.obsidian_style_guide import read_channel_style_guide
from atnos.production.channel_profile import ProductionChannelProfile, resolve_channel_profile
from atnos.production.faceless_playbook import faceless_playbook_prompt_block, faceless_playbook_summary
from atnos.production.models import ProductionContext, StepResult
from atnos.production.reference_profile import load_reference_shorts_profile
from atnos.production.steps.common import ask_ollama_json, clean_words, write_json
from atnos.production.visual_quality_contract import finance_prompt_has_text_surface, visual_quality_contract


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    script = context.artifacts["script"]
    style_guide = read_channel_style_guide(settings, context.account_id)
    profile = resolve_channel_profile(context, style_guide.content)
    style_block = _style_guide_prompt_block(style_guide.content)
    playbook = context.artifacts.get("faceless_playbook")
    playbook_block = faceless_playbook_prompt_block(playbook)
    production_brief = _topic_production_brief(context)
    production_brief_block = _production_brief_prompt_block(production_brief)
    quality_contract = visual_quality_contract(profile.key)
    reference_profile = load_reference_shorts_profile(settings)
    editorial_profile = _read_reference_editorial_profile(settings, profile.key)
    motion_block = _reference_motion_prompt_block(reference_profile.to_payload(), editorial_profile)
    prompt = (
        f"Create a visual plan for a faceless vertical YouTube Short in this lane: {profile.content_label}. "
        "Return JSON only: "
        "{\"visual_style\":\"...\",\"scenes\":[{\"scene_id\":0,\"script_lines\":[0],"
        "\"duration_s\":4,\"headline\":\"complete thought, max 5 words uppercase\",\"visual_prompt\":\"...\","
        "\"visual_subject\":\"...\",\"overlay_text\":\"...\",\"risk_notes\":\"...\"}]}. "
        "Headlines must express a complete thought in at most 5 uppercase words; rephrase instead of cutting "
        "off the last word. No text inside generated images. "
        "Every visual_prompt must name one concrete subject, one visible action, and at least two physical objects "
        "from the topic. Avoid generic background-only prompts, abstract charts, empty gradients, and unrelated symbols. "
        "The visual must literally communicate the script line and topic if the viewer watches muted: describe what "
        "the object/person is doing, why it relates to the line, and which prop proves the topic. "
        "The visual_subject and visual_prompt must describe the same main objects and the same action. "
        "Do not produce mood-only prompts like 'dark finance background', 'blurred chart', 'mysterious graph', or "
        "'cinematic abstract scene'. "
        "For Wealth Blueprint use mature hand-drawn editorial illustration with navy blueprint structure, cream "
        "paper and restrained warm gold. For generic finance use mature adult documentary scenes. Use physical, "
        "text-free props "
        "such as cash, coins, wallet, blank unbranded card, color-coded envelopes, shopping basket, glass savings jar, "
        "hourglass, balance scale, desk, and faceless adult hands making a decision. Never show a phone, screen, app, "
        "dashboard, receipt, statement, or readable document. A source-backed production brief overrides generic prop "
        "suggestions and may explicitly require a text-safe analog calculator, blank calendar grid, or illegible ledger. "
        "Never use childish, toy-like, mascot, sticker, cartoon, or blurry abstract background aesthetics for finance. "
        "Money direction must match the narrated event: a recurring expense takes money away from available cash; "
        "do not show a deposit into savings as the visual for an expense absorbing a raise. "
        "A savings decision must visibly direct money toward a separate goal. "
        f"Mandatory visual quality contract: {quality_contract} "
        f"Overlay/headline language rule: {profile.language_instruction} "
        "Follow the channel style guide when present, especially color, lighting, pacing, recurring motifs, "
        "and visual taboos. Do not copy competitor branding, logos, text overlays, or exact thumbnails. "
        f"Topic: {context.topic.title}. Script body: {script.get('body', [])}."
        f"{playbook_block}"
        f"{production_brief_block}"
        f"{motion_block}"
        f"{style_block}"
    )
    payload = await _llm_or_fallback(settings, context, prompt)
    payload["style_guide"] = {
        "status": style_guide.status,
        "source": style_guide.source,
        "used": bool(style_guide.content),
    }
    payload["faceless_playbook"] = faceless_playbook_summary(playbook if isinstance(playbook, dict) else {})
    payload["visual_quality_contract"] = {
        "profile_key": profile.key,
        "contract": quality_contract,
    }
    payload["production_brief"] = production_brief
    payload["reference_motion_profile"] = {
        "used": bool(reference_profile.available or editorial_profile.get("visual_sample_count")),
        "visual_sample_count": int(editorial_profile.get("visual_sample_count") or reference_profile.sample_count),
        "source_policy": str(editorial_profile.get("source_policy") or "aggregate measurements only"),
        "metrics": editorial_profile.get("motion_metrics") or {},
    }
    return write_json(context.artifact_dir / "visual_plan.json", payload)


async def _llm_or_fallback(settings: Settings, context: ProductionContext, prompt: str) -> dict[str, Any]:
    script = context.artifacts["script"]
    repair_request = context.artifacts.get("visual_repair_request")
    if isinstance(repair_request, dict):
        profile = resolve_channel_profile(context)
        scenes = _ensure_minimum_scenes(settings, context, [], profile=profile)
        if profile.key in {"wealth_blueprint", "finance"}:
            scenes = _rebuild_finance_repair_scenes(context, profile, scenes)
        scenes = _strengthen_scene_prompts(context, profile, scenes)
        for scene in scenes:
            scene["visual_generation_revision"] = 1
        return {
            "visual_style": profile.visual_style,
            "scenes": scenes,
            "llm_unavailable": False,
            "repair": {
                "mode": "concrete_fallback_rebuild",
                "reason": str(repair_request.get("reason") or "visual_quality_failed"),
                "blocking_failures": list(repair_request.get("blocking_failures") or []),
            },
        }
    try:
        planner_model = OllamaRoleRegistry(settings).resolve(ModelRole.VIDEO_EDIT_PLANNER).model
        planner_settings = settings.model_copy(update={"ollama_synthesis_model": planner_model})
        parsed = await ask_ollama_json(planner_settings, prompt, timeout_s=60.0)
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("scenes"), list) and parsed["scenes"]:
        parsed["scenes"] = [_normalize_scene(index, scene) for index, scene in enumerate(parsed["scenes"][:8])]
        profile = resolve_channel_profile(context)
        model_visual_style = str(parsed.get("visual_style") or "").strip()
        if model_visual_style and model_visual_style != profile.visual_style:
            parsed["model_visual_style"] = model_visual_style
        parsed["visual_style"] = profile.visual_style
        parsed.setdefault("llm_unavailable", False)
        parsed["scenes"] = _ensure_minimum_scenes(settings, context, parsed["scenes"], profile=profile)
        parsed["scenes"] = _strengthen_scene_prompts(context, profile, parsed["scenes"])
        return parsed

    profile = resolve_channel_profile(context)
    scenes = _ensure_minimum_scenes(settings, context, [], profile=profile)
    scenes = _strengthen_scene_prompts(context, profile, scenes)
    return {
        "visual_style": profile.visual_style,
        "scenes": scenes,
        "llm_unavailable": True,
    }


def _style_guide_prompt_block(style_guide: str | None) -> str:
    if not style_guide:
        return ""
    return (
        "\n\nCHANNEL VISUAL STYLE GUIDE FROM OBSIDIAN / MEMORY:\n"
        f"{style_guide[:6000]}\n"
        "Use this only for visual direction. Never include secrets or private account data in image prompts.\n"
        "END CHANNEL VISUAL STYLE GUIDE.\n"
    )


def _read_reference_editorial_profile(settings: Settings, profile_key: str) -> dict[str, Any]:
    path = (
        settings.data_dir
        / "learning"
        / "active-reference-shorts"
        / f"latest_editorial_profile_{profile_key}.json"
    )
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or str(payload.get("profile_key") or "") != profile_key:
        return {}
    return payload


def _reference_motion_prompt_block(
    reference_payload: dict[str, Any],
    editorial_profile: dict[str, Any],
) -> str:
    rules = [str(item).strip() for item in editorial_profile.get("motion_rules") or [] if str(item).strip()]
    if not rules:
        rules = [str(item).strip() for item in reference_payload.get("rules") or [] if str(item).strip()]
    sample_count = int(editorial_profile.get("visual_sample_count") or reference_payload.get("sample_count") or 0)
    if sample_count <= 0 or not rules:
        return ""
    metrics = editorial_profile.get("motion_metrics") if isinstance(editorial_profile.get("motion_metrics"), dict) else {}
    return (
        "\n\nREFERENCE MOTION CONTRACT (aggregate measurements only, never source-copy):\n"
        f"- Measured visual samples: {sample_count}.\n"
        f"- Median first cut: {metrics.get('median_first_cut_sec', reference_payload.get('median_first_cut_sec'))} seconds.\n"
        f"- Median meaningful change interval: {metrics.get('median_avg_cut_interval_sec', reference_payload.get('median_avg_cut_interval_sec'))} seconds.\n"
        + "".join(f"- {rule}\n" for rule in rules[:10])
        + "Hard originality rule: do not reuse source frames, branding, overlays, audio, or exact compositions.\n"
        "END REFERENCE MOTION CONTRACT.\n"
    )


def _topic_production_brief(context: ProductionContext) -> dict[str, Any]:
    script = context.artifacts.get("script")
    intelligence = script.get("topic_intelligence") if isinstance(script, dict) else None
    raw = intelligence.get("inspired_by") if isinstance(intelligence, dict) else None
    if isinstance(raw, dict):
        source_record = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        source_record = parsed if isinstance(parsed, dict) else {}
    else:
        source_record = {}
    brief = source_record.get("production_brief")
    if isinstance(brief, dict):
        return dict(brief)
    script_text = " ".join(
        str(segment.get("text") or "")
        for segment in (script.get("segments") if isinstance(script, dict) else []) or []
        if isinstance(segment, dict)
    )
    if _is_compound_interest_topic(context.topic.title, script_text):
        return {
            "image_subject": (
                "A mature financial planning desk with an analog calculator, blank calendar pages, transparent "
                "value layers, and a glass hourglass; no coins, piggy banks, or fantasy wealth imagery."
            ),
            "expected_scene_evidence": [
                "starting calculation",
                "larger calculation base",
                "time passing across repeated periods",
            ],
            "source": "wealth_blueprint_compound_interest_default",
        }
    return {}


def _production_brief_prompt_block(brief: dict[str, Any]) -> str:
    if not brief:
        return ""
    image_subject = str(brief.get("image_subject") or "").strip()
    evidence = [str(item).strip() for item in brief.get("expected_scene_evidence") or [] if str(item).strip()]
    return (
        "\n\nSOURCE-BACKED PRODUCTION BRIEF (higher priority than generic motif suggestions):\n"
        f"Image direction: {image_subject or 'not specified'}\n"
        f"Required evidence: {evidence}\n"
        "Do not introduce a prop explicitly forbidden by this brief.\n"
        "END SOURCE-BACKED PRODUCTION BRIEF.\n"
    )


def _production_brief_forbidden_visual_terms(brief: dict[str, Any]) -> list[str]:
    """Extract explicit prop bans so renderers cannot replace the reviewed brief."""
    image_subject = str(brief.get("image_subject") or "").casefold()
    forbidden: list[str] = []
    if "no coins" in image_subject or "without coins" in image_subject:
        forbidden.extend(["coin", "coins"])
    if "no piggy bank" in image_subject or "no coins, piggy banks" in image_subject:
        forbidden.extend(["piggy bank", "piggy banks"])
    if "no fantasy wealth" in image_subject or "fantasy wealth imagery" in image_subject:
        forbidden.append("fantasy wealth imagery")
    return list(dict.fromkeys(forbidden))


_HEADLINE_TRAILING_CONNECTORS = {
    "A",
    "AN",
    "AND",
    "FOR",
    "FROM",
    "OF",
    "OR",
    "THE",
    "TO",
    "WITH",
    "WITHOUT",
}


def _compact_headline(value: str, limit: int = 5) -> str:
    headline = clean_words(str(value or "").upper(), limit)
    words = headline.split()
    while len(words) > 1 and words[-1].rstrip(".,:;!?") in _HEADLINE_TRAILING_CONNECTORS:
        words.pop()
    return " ".join(words)


def _truncate_visual_prompt(value: str, limit: int) -> str:
    prompt = str(value or "").strip()
    if len(prompt) <= limit:
        return prompt
    sentence_end = prompt.rfind(". ", 0, limit + 1)
    if sentence_end >= int(limit * 0.65):
        return prompt[: sentence_end + 1].rstrip()
    word_end = prompt.rfind(" ", 0, limit + 1)
    return prompt[:word_end].rstrip(" ,;") if word_end > 0 else prompt[:limit]


def _normalize_scene(index: int, scene: Any) -> dict[str, Any]:
    data = scene if isinstance(scene, dict) else {}
    headline = _compact_headline(data.get("headline") or data.get("overlay_text") or "WAZNE")
    full_overlay = str(data.get("overlay_text") or "").strip().upper()
    # The model often gives a three-word prefix of a complete four-word
    # overlay ("RAISE DOESN'T MEAN" / "RAISE DOESN'T MEAN WEALTH").
    # Keep the complete short thought for the renderer instead of the fragment.
    if full_overlay and len(full_overlay.split()) <= 5 and full_overlay.startswith(headline) and full_overlay != headline:
        headline = full_overlay
    return {
        "scene_id": int(data.get("scene_id") or index),
        "segment_type": str(data.get("segment_type") or data.get("type") or "body").strip().lower(),
        "script_lines": data.get("script_lines") if isinstance(data.get("script_lines"), list) else [index],
        "duration_s": max(2, min(8, int(data.get("duration_s") or 4))),
        "headline": headline,
        "visual_prompt": _truncate_visual_prompt(data.get("visual_prompt") or "", 700),
        "visual_subject": str(data.get("visual_subject") or "").strip()[:240],
        "motion_style": str(data.get("motion_style") or "").strip()[:400],
        "overlay_text": clean_words(str(data.get("overlay_text") or headline), 8),
        "risk_notes": str(data.get("risk_notes") or "").strip(),
        "renderer_scene_type": str(data.get("renderer_scene_type") or "").strip(),
        "number_value": clean_words(str(data.get("number_value") or ""), 4),
        "chart_values": [float(value) for value in (data.get("chart_values") or []) if isinstance(value, (int, float))][
            :8
        ],
        "chart_labels": [clean_words(str(value), 3).upper() for value in (data.get("chart_labels") or [])][
            :8
        ],
        "comparison_left": clean_words(str(data.get("comparison_left") or ""), 4),
        "comparison_right": clean_words(str(data.get("comparison_right") or ""), 4),
        "semantic_overlay_kind": str(data.get("semantic_overlay_kind") or "").strip(),
    }


def _ensure_minimum_scenes(
    settings: Settings,
    context: ProductionContext,
    scenes: list[dict[str, Any]],
    *,
    profile: ProductionChannelProfile,
) -> list[dict[str, Any]]:
    min_scenes = max(1, int(getattr(settings, "shorts_visual_min_real_clips", 3) or 3))
    max_scenes = max(min_scenes, int(getattr(settings, "shorts_visual_max_generated_scenes", 4) or 4))
    candidates = _candidate_visual_lines(context.artifacts.get("script", {}))
    target_scene_count = min(max_scenes, max(min_scenes, len(candidates)))
    if candidates:
        # A minimum clip target cannot invent new script lines. Short scripts
        # should remain short and let downstream quality gates flag the gap.
        target_scene_count = min(target_scene_count, len(candidates))
    output: list[dict[str, Any]] = []
    for scene in scenes[:target_scene_count]:
        if candidates:
            valid_lines: list[int] = []
            for line in scene.get("script_lines") if isinstance(scene.get("script_lines"), list) else []:
                try:
                    line_index = int(line)
                except (TypeError, ValueError):
                    continue
                if 0 <= line_index < len(candidates) and line_index not in valid_lines:
                    valid_lines.append(line_index)
            if not valid_lines:
                continue
            scene = {**scene, "script_lines": valid_lines}
        output.append(scene)
    for scene in output:
        if str(scene.get("segment_type") or "").strip():
            continue
        script_lines = scene.get("script_lines") if isinstance(scene.get("script_lines"), list) else []
        candidate_index = script_lines[0] if script_lines else None
        if isinstance(candidate_index, int) and 0 <= candidate_index < len(candidates):
            scene["segment_type"] = str(candidates[candidate_index].get("type") or "body").strip().lower()
        else:
            scene["segment_type"] = "body"
    used_scene_ids = {int(scene.get("scene_id") or index) for index, scene in enumerate(output)}
    used_script_lines = {
        int(line)
        for scene in output
        for line in (scene.get("script_lines") if isinstance(scene.get("script_lines"), list) else [])
        if isinstance(line, int) or (isinstance(line, str) and line.isdigit())
    }
    next_id = 0
    for candidate_index, candidate in enumerate(candidates):
        if len(output) >= target_scene_count:
            break
        if candidate_index in used_script_lines:
            continue
        while next_id in used_scene_ids:
            next_id += 1
        output.append(_fallback_scene_from_candidate(context, profile, candidate_index, next_id, candidate))
        used_scene_ids.add(next_id)
        next_id += 1

    while len(output) < target_scene_count:
        while next_id in used_scene_ids:
            next_id += 1
        output.append(
            _fallback_scene_from_candidate(
                context,
                profile,
                len(output),
                next_id,
                {
                    "text": context.topic.title,
                    "visual_note": profile.fallback_scene_prompt.format(topic=context.topic.title),
                    "type": "body",
                },
            )
        )
        used_scene_ids.add(next_id)
        next_id += 1
    if candidates:
        # A rejected model scene can leave a gap that the fallback fills later.
        # Restore spoken order before passing the scenes to the renderer.
        output.sort(key=lambda scene: min(scene["script_lines"]))
    return output[:max_scenes]


def _candidate_visual_lines(script: dict[str, Any]) -> list[dict[str, str]]:
    segments = script.get("segments")
    if isinstance(segments, list) and segments:
        candidates: list[dict[str, str]] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            visual_note = str(segment.get("visual_note") or "").strip()
            segment_type = str(segment.get("type") or "body").strip()
            if text or visual_note:
                candidates.append({"text": text, "visual_note": visual_note, "type": segment_type})
        if candidates:
            return candidates

    candidates = []
    for key in ("hook", "opening_line", "cta"):
        text = str(script.get(key) or "").strip()
        if text:
            candidates.append({"text": text, "visual_note": "", "type": key})
    body = script.get("body") if isinstance(script.get("body"), list) else []
    for line in body:
        text = str(line or "").strip()
        if text:
            candidates.append({"text": text, "visual_note": "", "type": "body"})
    return candidates


def _fallback_scene_from_candidate(
    context: ProductionContext,
    profile: ProductionChannelProfile,
    candidate_index: int,
    scene_id: int,
    candidate: dict[str, str],
) -> dict[str, Any]:
    default_headline = _default_headline(profile.key)
    text = str(candidate.get("text") or context.topic.title).strip()
    visual_note = str(candidate.get("visual_note") or "").strip()
    headline = _compact_headline(text) or default_headline
    prompt_base = visual_note or _topic_specific_visual_seed(profile, context.topic.title, text)
    return {
        "scene_id": scene_id,
        "segment_type": str(candidate.get("type") or "body").strip().lower(),
        "script_lines": [candidate_index],
        "duration_s": 4,
        "headline": headline,
        "visual_subject": prompt_base,
        "visual_prompt": _compose_visual_prompt(profile, context.topic.title, text, prompt_base),
        "motion_style": profile.video_motion,
        "overlay_text": headline,
        "risk_notes": profile.risk_notes,
    }


def _default_headline(profile_key: str) -> str:
    if profile_key == "sigma_squad":
        return "SIGMA MODE"
    if profile_key == "dark_chronicles":
        return "HIDDEN TRUTH"
    if profile_key == "wealth_blueprint":
        return "IMPORTANT"
    return "WAZNE"


def _strengthen_scene_prompts(
    context: ProductionContext,
    profile: ProductionChannelProfile,
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = _candidate_visual_lines(context.artifacts.get("script", {}))
    production_brief = _topic_production_brief(context)
    forbidden_visual_terms = _production_brief_forbidden_visual_terms(production_brief)
    strengthened: list[dict[str, Any]] = []
    used_subject_keys: set[str] = set()
    used_subjects: set[str] = set()
    number_scene_used = False
    image_scene_count = 0
    chart_scene_used = False
    compound_source_text = " ".join(
        part
        for part in (
            str(context.topic.title or "").strip(),
            str(context.topic.description or "").strip(),
            " ".join(str(item.get("text") or "").strip() for item in candidates),
        )
        if part
    )
    compound_source_chart_values = _compound_chart_values(compound_source_text)
    for index, scene in enumerate(scenes):
        scene_text = _scene_text(scene, candidates, fallback=context.topic.title)
        prompt = str(scene.get("visual_prompt") or "").strip()
        subject = str(scene.get("visual_subject") or "").strip()
        if _is_compound_interest_topic(context.topic.title, scene_text):
            # A generic finance relevance score cannot distinguish compounding
            # from budgeting or lifestyle-creep imagery. Bind each scene to its
            # matching script segment and bypass generic finance rewrites.
            candidate = candidates[index] if index < len(candidates) else {}
            candidate_text = " ".join(
                part
                for part in (
                    str(candidate.get("text") or "").strip(),
                    str(candidate.get("visual_note") or "").strip(),
                )
                if part
            ).strip()
            spoken_candidate_text = str(candidate.get("text") or "").strip()
            scene_text = candidate_text or scene_text
            subject = _compound_interest_visual_seed(
                scene_text,
                index,
                production_brief=production_brief,
            )
            if subject in used_subjects:
                subject = _compound_interest_alternate_visual_seed(
                    index,
                    production_brief=production_brief,
                )
            prompt = _compose_visual_prompt(profile, context.topic.title, scene_text, subject)
            scene = {
                **scene,
                "script_lines": [index],
                "segment_type": str(candidate.get("type") or scene.get("segment_type") or "body").strip().lower(),
                "visual_subject": subject,
                "visual_prompt": prompt,
                "quality_notes": "visual_prompt rebuilt for compound-interest topic fidelity",
                "forbidden_visual_terms": forbidden_visual_terms,
            }
            segment_type = str(candidate.get("type") or scene.get("segment_type") or "body").strip().lower()
            chart_values = _compound_chart_values(scene_text)
            is_chart_summary = (
                not chart_scene_used
                and bool(compound_source_chart_values)
                and any(
                    token in (spoken_candidate_text or scene_text).casefold()
                    for token in ("mechanism keeps building", "$110.25")
                )
            )
            if (
                not chart_values
                and is_chart_summary
            ):
                # The verified progression is often narrated across several
                # segments. Render it once when the final balance is explained
                # instead of requiring every source value in one sentence.
                chart_values = compound_source_chart_values
            number_value = _compound_number_value(scene_text)
            is_mechanism_conclusion = (
                segment_type not in {"hook", "cta"}
                and (
                    ("mechanism" in scene_text.casefold() and "promise" in scene_text.casefold())
                    or "no guarantee" in scene_text.casefold()
                    or "no magic" in scene_text.casefold()
                )
            )
            if is_mechanism_conclusion:
                # Abstract props such as a "scenario block in a brass frame"
                # repeatedly produce incoherent AI stills. This conclusion is
                # clearer as a deterministic editorial reset before the CTA.
                scene.update(
                    {
                        "renderer_scene_type": "HookScene",
                        "headline": "MECHANISM, NOT PROMISE",
                        "overlay_text": "TEST THE PLAN",
                        "semantic_overlay_kind": "compound_scenario",
                        "quality_notes": "deterministic compound-interest conclusion",
                    }
                )
            elif segment_type not in {"hook", "cta"} and chart_values:
                scene.update(
                    {
                        "renderer_scene_type": "ChartScene",
                        "headline": "THE BASE COMPOUNDS",
                        "overlay_text": "$100 TO $110.25",
                        "chart_values": chart_values,
                        "chart_labels": ["START", "YEAR 1", "YEAR 2"],
                        "comparison_left": "",
                        "comparison_right": "",
                        "semantic_overlay_kind": "compound_reinvestment",
                        "quality_notes": "source-backed compound-interest chart",
                    }
                )
                chart_scene_used = True
            elif segment_type not in {"hook", "cta"} and number_value and not number_scene_used:
                scene.update(
                    {
                        "renderer_scene_type": "NumberScene",
                        "number_value": number_value,
                        "headline": _compound_interest_number_headline(scene_text, number_value),
                        "quality_notes": "source-backed single number scene",
                    }
                )
                number_scene_used = True
            else:
                # Repeating the same full-screen number card makes an otherwise
                # accurate explainer feel static. Keep one source-backed card,
                # then show subsequent mentions through their physical motif.
                if segment_type not in {"hook", "cta"} and number_value and number_scene_used:
                    is_year_one_balance = (
                        number_value == "$105"
                        and not any(token in scene_text for token in ("full $105", "not the original", "larger base"))
                    )
                    if is_year_one_balance:
                        scene["renderer_scene_type"] = "NumberScene"
                        scene["number_value"] = number_value
                        scene["headline"] = _compound_interest_number_headline(scene_text, number_value)
                        scene["quality_notes"] = "source-backed deterministic year-one balance scene"
                    elif image_scene_count < 2:
                        scene["renderer_scene_type"] = "ImageScene"
                        scene.pop("number_value", None)
                        image_scene_count += 1
                    else:
                        # Two documentary stills add grounded visual progression.
                        # Further numeric beats stay deterministic so generated
                        # props cannot replace or contradict sourced values.
                        scene["renderer_scene_type"] = "NumberScene"
                        scene["number_value"] = number_value
                        scene["headline"] = _compound_interest_number_headline(scene_text, number_value)
                        scene["quality_notes"] = "source-backed deterministic follow-up number scene"
                overlay_kind = _compound_interest_overlay_kind(scene_text)
                if overlay_kind:
                    scene["semantic_overlay_kind"] = overlay_kind
                    scene["quality_notes"] = f"deterministic {overlay_kind} explanation over verified imagery"
            if scene.get("renderer_scene_type") == "ImageScene":
                scene["forbidden_visual_terms"] = list(
                    dict.fromkeys(
                        [
                            *forbidden_visual_terms,
                            "plant",
                            "flower",
                            "flower pot",
                            "potted plant",
                            "calculator",
                            "split screen",
                            "diptych",
                            "collage",
                            "duplicated subject",
                        ]
                    )
                )
            anchor_terms = _topic_anchor_terms(profile, context.topic.title, scene_text)
            scene["visual_relevance_terms"] = _visual_relevance_terms(profile, f"{prompt} {subject}")
            scene["visual_relevance_score"] = _visual_relevance_score(profile, f"{prompt} {subject}")
            scene["channel_profile_key"] = profile.key
            scene["visual_quality_contract"] = visual_quality_contract(profile.key)
            scene["visual_topic_anchor_terms"] = list(anchor_terms)
            scene["visual_topic_anchor_matches"] = _topic_anchor_matches(anchor_terms, f"{prompt} {subject}")
            scene["visual_topic_anchor_score"] = _topic_anchor_score(anchor_terms, f"{prompt} {subject}")
            scene["topic_fidelity_enforced"] = True
            subject_key = _visual_subject_key(profile, subject)
            if subject_key:
                used_subject_keys.add(subject_key)
            used_subjects.add(subject)
            strengthened.append(scene)
            continue
        if profile.key in {"wealth_blueprint", "finance"} and not production_brief.get("image_subject"):
            # Bind common allocation beats to the direction of the money.
            # Topic relevance alone cannot detect an expense scene showing a
            # deposit into savings or a remaining-cash scene showing a barrier.
            allocation_seed = _raise_allocation_visual_seed(scene_text)
            if allocation_seed:
                subject = allocation_seed
                prompt = _compose_visual_prompt(profile, context.topic.title, scene_text, subject)
                scene = {
                    **scene,
                    "visual_subject": subject,
                    "visual_prompt": prompt,
                    "quality_notes": "visual action bound to the narrated money direction",
                }
        if _history_prompt_is_better_subject(profile, prompt, scene_text):
            subject = _prompt_subject(prompt)
        if not subject or _visual_relevance_score(profile, subject) < 0.34:
            subject = _topic_specific_visual_seed(profile, context.topic.title, scene_text)
        anchor_terms = _topic_anchor_terms(profile, context.topic.title, scene_text)
        anchor_matches = _topic_anchor_matches(anchor_terms, f"{prompt} {subject}")
        anchor_score = _topic_anchor_score(anchor_terms, f"{prompt} {subject}")
        if (
            _is_generic_visual_prompt(prompt, profile)
            or _visual_relevance_score(profile, f"{prompt} {subject}") < 0.58
            or _concrete_object_count(profile, f"{prompt} {subject}") < 2
            or (anchor_terms and anchor_score < 0.7)
        ):
            subject = _topic_specific_visual_seed(profile, context.topic.title, scene_text)
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual_prompt strengthened for topic relevance",
            }
        else:
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": prompt,
            }
        subject_key = _visual_subject_key(profile, str(scene.get("visual_subject") or ""))
        if subject_key and subject_key in used_subject_keys:
            subject = _alternate_topic_visual_seed(profile, context.topic.title, scene_text, index)
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual_prompt diversified after repeated visual subject key",
            }
            subject_key = _visual_subject_key(profile, subject)
        if anchor_terms and _topic_anchor_score(anchor_terms, f"{scene['visual_prompt']} {subject}") < 0.5:
            subject = _topic_anchor_visual_seed(profile, anchor_terms, context.topic.title, scene_text, index)
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual_prompt re-anchored to specific topic terms",
            }
            subject_key = _visual_subject_key(profile, subject)
        if profile.key in {"wealth_blueprint", "finance"} and finance_prompt_has_text_surface(
            f"{scene.get('visual_prompt') or ''} {scene.get('visual_subject') or ''}"
        ):
            subject = _safe_finance_visual_seed(context.topic.title, scene_text, index)
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual_prompt rebuilt to remove text-prone surfaces",
            }
            subject_key = _visual_subject_key(profile, subject)
        if (
            _visual_relevance_score(profile, f"{scene['visual_prompt']} {scene.get('visual_subject') or ''}") < 0.58
            or _concrete_object_count(profile, f"{scene['visual_prompt']} {scene.get('visual_subject') or ''}") < 2
        ):
            subject = _concrete_topic_visual_seed(profile, anchor_terms, context.topic.title, scene_text, index)
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual_prompt rebuilt for concrete topic-specific objects",
            }
            subject_key = _visual_subject_key(profile, subject)
        if profile.key in {"wealth_blueprint", "finance"} and finance_prompt_has_text_surface(
            f"{scene.get('visual_prompt') or ''} {scene.get('visual_subject') or ''}"
        ):
            subject = _safe_finance_visual_seed(context.topic.title, scene_text, index)
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual_prompt rebuilt to remove text-prone surfaces",
            }
            subject_key = _visual_subject_key(profile, subject)
        subject = str(scene.get("visual_subject") or "").strip()
        subject_key = _visual_subject_key(profile, subject)
        if subject_key and subject_key in used_subject_keys:
            subject = _unique_alternate_visual_seed(
                profile,
                anchor_terms,
                context.topic.title,
                scene_text,
                index,
                used_subject_keys,
            )
            scene = {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual_prompt diversified after repeated visual subject key",
            }
            subject_key = _visual_subject_key(profile, subject)
        if subject_key:
            used_subject_keys.add(subject_key)
        scene["visual_relevance_terms"] = _visual_relevance_terms(profile, f"{scene['visual_prompt']} {subject}")
        scene["visual_relevance_score"] = _visual_relevance_score(profile, f"{scene['visual_prompt']} {subject}")
        scene["channel_profile_key"] = profile.key
        scene["visual_quality_contract"] = visual_quality_contract(profile.key)
        scene["visual_topic_anchor_terms"] = list(anchor_terms)
        scene["visual_topic_anchor_matches"] = _topic_anchor_matches(
            anchor_terms,
            f"{scene['visual_prompt']} {scene.get('visual_subject') or ''}",
        )
        scene["visual_topic_anchor_score"] = _topic_anchor_score(
            anchor_terms,
            f"{scene['visual_prompt']} {scene.get('visual_subject') or ''}",
        )
        scene["topic_fidelity_enforced"] = _is_compound_interest_topic(context.topic.title, scene_text)
        strengthened.append(scene)
    return strengthened


_SCENE_ALIGNMENT_STOP_WORDS = {
    "about",
    "after",
    "again",
    "against",
    "began",
    "before",
    "during",
    "from",
    "into",
    "leaving",
    "that",
    "their",
    "there",
    "these",
    "this",
    "through",
    "under",
    "while",
    "with",
}


def _history_prompt_is_better_subject(
    profile: ProductionChannelProfile,
    prompt: str,
    scene_text: str,
) -> bool:
    """Prefer a concrete scene-aligned prompt over contradictory generic metadata."""
    if profile.key not in {"dark_chronicles", "history"} or _is_generic_visual_prompt(prompt, profile):
        return False
    shared = _semantic_tokens(scene_text) & _semantic_tokens(prompt)
    return (
        len(shared) >= 2
        and _visual_relevance_score(profile, prompt) >= 0.58
        and _concrete_object_count(profile, prompt) >= 2
    )


def _semantic_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", str(text or "").casefold())
        if len(token) >= 4 and token not in _SCENE_ALIGNMENT_STOP_WORDS
    }


def _prompt_subject(prompt: str) -> str:
    concise = re.split(r"(?<=[.!?])\s+", " ".join(str(prompt or "").split()), maxsplit=1)[0]
    return concise[:240].rstrip(" ,.;")


def _scene_text(scene: dict[str, Any], candidates: list[dict[str, str]], *, fallback: str) -> str:
    script_lines = scene.get("script_lines")
    if isinstance(script_lines, list):
        chunks: list[str] = []
        for line in script_lines:
            try:
                index = int(line)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(candidates):
                candidate = candidates[index]
                text = str(candidate.get("text") or "").strip()
                visual_note = str(candidate.get("visual_note") or "").strip()
                chunks.append(" ".join(part for part in (text, visual_note) if part).strip())
        text = " ".join(chunk for chunk in chunks if chunk).strip()
        if text:
            return text
    return str(scene.get("overlay_text") or scene.get("headline") or fallback).strip()


def _compose_visual_prompt(
    profile: ProductionChannelProfile,
    topic: str,
    scene_text: str,
    subject: str,
) -> str:
    line = " ".join(str(scene_text or topic).split())[:220]
    channel_contract = ""
    if profile.key == "wealth_blueprint":
        channel_contract = (
            "Mature hand-drawn personal-finance editorial illustration, realistic proportions, precise ink "
            "linework, cream paper and charcoal navy blueprint structure with restrained warm gold. "
            "Keep the setting and primary material visibly distinct from the surrounding beats. "
            "Use no text-bearing or display-like surfaces. Convey the narrative through physical objects "
            "and visible human action. "
        )
        line = "personal-finance decision shown through physical action"
    elif profile.key == "finance":
        channel_contract = (
            "Mature adult personal-finance documentary scene with a setting and primary material chosen for this "
            "specific beat, physical text-free money evidence, sharp foreground object, corporate modern composition. "
            "Keep the setting and primary material visibly distinct from the surrounding beats. "
            "Use no text-bearing or display-like surfaces. "
            "Convey the narrative only through physical objects and visible human action. "
        )
        line = "personal-finance decision shown through physical action"
    prompt = (
        f"{subject}. {channel_contract}Directly tied to the topic '{topic}'. Scene line: {line}. "
        f"{profile.video_motion}. 9:16 vertical, strong foreground subject, visible action, high detail, "
        "no readable text, no logos, no watermark."
    )
    return _truncate_visual_prompt(prompt, 900)


def _is_generic_visual_prompt(prompt: str, profile: ProductionChannelProfile) -> bool:
    cleaned = " ".join(prompt.split()).strip()
    if not cleaned:
        return True
    lowered = cleaned.casefold()
    if len(cleaned.split()) < 8:
        return True
    generic_markers = (
        "scene about {topic}",
        "abstract background",
        "abstract chart",
        "abstract graph",
        "blurred background",
        "blurry background",
        "bokeh background",
        "generic background",
        "generic shapes",
        "empty gradient",
    )
    if any(marker in lowered for marker in generic_markers):
        return True
    if profile.key in {"wealth_blueprint", "finance"} and any(
        marker in lowered
        for marker in ("childish", "cartoon", "cute mascot", "mascot", "toy-like", "toy style", "sticker", "emoji")
    ):
        return True
    if profile.key in {"wealth_blueprint", "finance"} and finance_prompt_has_text_surface(lowered):
        return True
    if "chart" in lowered and profile.key in {"wealth_blueprint", "finance"}:
        # A chart alone was the exact failure mode: topic-agnostic and visually dead.
        has_human_or_object = any(
            token in lowered
            for token in ("person", "hand", "phone", "receipt", "card", "cash", "wallet", "bill", "bank", "desk")
        )
        return not has_human_or_object
    return _visual_relevance_score(profile, cleaned) < 0.34


def _safe_finance_visual_seed(topic: str, scene_text: str, index: int) -> str:
    """Buduje scene finansowa bez powierzchni, na ktorych model tworzy pseudotekst."""
    if _is_compound_interest_topic(topic, scene_text):
        return _compound_interest_visual_seed(scene_text, index)
    semantic_seed = _finance_scene_visual_seed(topic, scene_text)
    if semantic_seed:
        return semantic_seed
    options = (
        "a sealed payday envelope entering a brass mail slot beside a closed leather wallet and a large analog clock, income protected before spending",
        "an unbranded premium sneaker, closed wireless-earbuds case, and plain takeaway cup stopped on a black checkout conveyor before the scanner",
        "a takeaway cup, delivery parcel, and wireless-earbuds case falling as a domino chain toward an open empty leather wallet",
        "a closed leather wallet behind a red velvet barrier while a plain shopping basket waits in the foreground, a purchase visibly paused",
        "a cracked clear acrylic tube spilling loose coins toward a dark floor drain beside one plain delivery bag",
        "a transparent savings jar receiving one coin beside a closed leather wallet and an unmarked hardback book, money redirected to saving",
        "a stainless-steel mechanical coin sorter dividing mixed coins into three separate trays for deliberate allocation",
        "a brass balance scale holding a small metal savings box opposite an empty shopping basket, long-term investing outweighing impulse spending",
    )
    return options[index % len(options)]


def _is_compound_interest_topic(topic: str, scene_text: str = "") -> bool:
    text = f"{topic} {scene_text}".casefold()
    return "compound interest" in text or "interest on interest" in text


def _is_consistent_contribution_beat(scene_text: str) -> bool:
    text = str(scene_text or "").casefold()
    return "consistent contribution" in text and "financial hype" in text


def _compound_interest_number_headline(scene_text: str, number_value: str) -> str:
    """Bind deterministic number cards to the narrated step instead of a generic label."""
    text = str(scene_text or "").casefold()
    if "second gain" in text or ("year two earns" in text and number_value == "$5.25"):
        return "YEAR TWO GAIN"
    if "start with" in text or "starting" in text:
        return "STARTING BALANCE"
    if "after year one" in text or "year one ends" in text:
        return "YEAR ONE BALANCE"
    if "year two" in text and ("full" in text or "base" in text):
        return "YEAR TWO BASE"
    if "mechanism" in text or "promised return" in text or "guaranteed return" in text:
        return "EXAMPLE, NOT A PROMISE"
    return "VERIFIED BALANCE"


def _compound_interest_overlay_kind(scene_text: str) -> str:
    """Dobiera deterministyczna warstwe wyjasniajaca do konkretnego etapu narracji."""
    text = str(scene_text or "").casefold()
    if "starting money" in text and "prior interest" in text:
        return "compound_layers"
    if "interest can earn interest" in text:
        return "compound_reinvestment"
    if "mechanism" in text and ("not a guaranteed" in text or "not a promised" in text):
        return "compound_layers"
    if "next calculation" in text and "larger base" in text:
        return "compound_reinvestment"
    if "time matters" in text and "one-day" in text:
        return "compound_time"
    if "starting earlier" in text and "more periods" in text:
        return "compound_early_start"
    if "long-term contribution scenario" in text:
        return "compound_scenario"
    return ""


def _compound_interest_visual_seed(
    scene_text: str,
    index: int,
    *,
    production_brief: dict[str, Any] | None = None,
) -> str:
    """Zwraca tekstowo bezpieczny, fizyczny kadr wyjasniajacy procent skladany."""
    text = str(scene_text or "").casefold()
    image_subject = str((production_brief or {}).get("image_subject") or "").casefold()
    if "no coins" in image_subject or "without coins" in image_subject:
        return _compound_interest_no_coin_visual_seed(text, index)
    if _is_consistent_contribution_beat(scene_text):
        return (
            "faceless adult hands placing two identical translucent contribution blocks onto a larger base of clear acrylic, "
            "with evenly spaced brass markers on a dark walnut desk"
        )
    if "$100" in text and "$110.25" in text and "without another deposit" in text:
        return (
            "faceless adult hand rolling one brass coin along a circular brass track toward a taller stack of "
            "transparent value blocks on a dark walnut desk"
        )
    if "$100" in text and "5%" in text:
        return (
            "overhead documentary photograph of ten matching smooth unmarked metal coins aligned inside one black "
            "financial calculation tray on a pale limestone work surface"
        )
    if any(token in text for token in ("full $105", "larger base", "not the original")):
        return (
            "side-view documentary photograph of a faceless adult hand placing one plain metal coin onto the tallest "
            "of three progressively higher coin stacks on a brushed-steel workbench, visibly continuing from the full "
            "larger stack to show compound growth"
        )
    if "$105" in text and "$110.25" not in text:
        return (
            "side profile of two plain metal coin towers on a charcoal stone plinth, with the second tower visibly "
            "one coin taller to show compound growth, photographed in soft directional window light"
        )
    if "$100" in text and "$105" in text and "$110.25" in text:
        return (
            "three distinct transparent value columns progressing across three positions on a brushed-metal rail, "
            "with small brass growth layers added to each stage on a mature editorial desk without labels"
        )
    if any(token in text for token in ("interest can earn interest", "prior interest", "joined the base")):
        return (
            "macro documentary photograph of a faceless adult hand moving two earned plain metal coins from a small "
            "brass dish onto the top of the same larger coin stack on matte black stone, visibly adding earnings back "
            "to the principal for the next compound growth period"
        )
    if any(token in text for token in ("long-term contribution", "investment decision", "scenario")):
        return (
            "one large clear hourglass centered on a pale stone shelf, with a curved row of matching plain metal "
            "coins leading toward it in warm directional side light"
        )
    if (
        "repeated periods" in text
        or "across repeated" in text
        or "more periods" in text
        or re.search(r"\btime\b", text)
    ):
        return (
            "three nested mechanical timing rings guiding transparent value blocks through several distinct growth "
            "stages on a premium planning table with restrained documentary lighting"
        )
    if any(token in text for token in ("not a guaranteed", "mechanism", "example explains")):
        return (
            "one brass balance scale holding a short stack of plain metal coins on one pan and one closed brass "
            "padlock on the other, placed on deep green felt, sober editorial finance photograph"
        )
    if any(token in text for token in ("save this", "test it", "money decision")):
        return (
            "adult hand locking one completed transparent scenario block into a brass planning frame beside an "
            "hourglass and a plain green marker, no brands or readable text"
        )
    options = (
        "overhead precision tray holding one ordered row of matching plain metal coins on pale limestone",
        "two plain coin towers at different heights on a charcoal stone plinth",
        "faceless adult hand placing one plain coin onto the tallest of three increasing coin stacks on brushed steel",
        "large clear hourglass beside a curved row of matching coins on a pale stone shelf",
    )
    return options[index % len(options)]


def _compound_chart_values(scene_text: str) -> list[float]:
    text = str(scene_text or "")
    if all(token in text for token in ("$100", "$105", "$110.25")):
        return [100.0, 105.0, 110.25]
    return []


def _compound_number_value(scene_text: str) -> str:
    text = str(scene_text or "")
    numeric = re.findall(r"\$\d+(?:\.\d+)?", text)
    if numeric:
        return numeric[-1]
    lowered = text.casefold().replace("-", " ")
    spoken_amounts = (
        ("one hundred ten dollars and twenty five cents", "$110.25"),
        ("one hundred five", "$105"),
        ("five dollars and twenty five cents", "$5.25"),
        ("one hundred dollars", "$100"),
        ("five dollars", "$5"),
    )
    for phrase, value in spoken_amounts:
        if phrase in lowered:
            return value
    return ""


def _compound_interest_no_coin_visual_seed(scene_text: str, index: int) -> str:
    if "$100" in scene_text and "$110.25" in scene_text and "without another deposit" in scene_text:
        return (
            "faceless adult hand placing one clear starting-value block at the beginning of a long brass timeline rail "
            "beside a large glass hourglass on a dark walnut planning desk"
        )
    if any(token in scene_text for token in ("full $105", "larger base", "not the original")):
        return (
            "documentary personal-finance photograph of a faceless adult hand moving two unlabelled budgeting envelopes "
            "together into one open leather wallet, one envelope large and neutral and the other slim and emerald, no "
            "writing or digits"
        )
    has_spoken_hundred = "$100" in scene_text or "one hundred dollars" in scene_text
    has_spoken_five_percent = "5%" in scene_text or "five percent" in scene_text
    if has_spoken_hundred and has_spoken_five_percent:
        return (
            "documentary personal-finance photograph of faceless adult hands holding one blank unbranded bank card "
            "directly above one closed leather wallet, realistic materials, natural side light, no writing or digits"
        )
    if "$105" in scene_text and "$110.25" not in scene_text:
        return (
            "close documentary personal-finance photograph of a faceless adult hand tucking one slim emerald budgeting "
            "envelope directly behind one larger neutral budgeting envelope, both envelopes blank and unlabelled, the "
            "small added envelope clearly visible, no writing or digits"
        )
    if "$100" in scene_text and "$105" in scene_text and "$110.25" in scene_text:
        return (
            "three clear acrylic value columns increasing across three positions on a brushed-metal planning rail, "
            "beside an analog calculator and a glass hourglass"
        )
    if any(token in scene_text for token in ("interest can earn interest", "prior interest", "joined the base")):
        return (
            "faceless adult hand transferring one small transparent growth layer back onto the same larger acrylic base "
            "block, visibly making the next calculation start higher"
        )
    if any(token in scene_text for token in ("calendar", "more periods", "repeated periods", "time allows")):
        return (
            "one large glass hourglass aligned with three distinct clear growth stages along a brushed brass timing "
            "rail on a clean dark planning table"
        )
    if any(token in scene_text for token in ("long-term contribution", "investment decision", "scenario")):
        return (
            "mature financial-planning desk with an analog calculator, a large glass hourglass, three unlabeled acrylic "
            "scenario blocks, and a closed leather planning folder"
        )
    if any(token in scene_text for token in ("not a guaranteed", "mechanism", "example explains")):
        return (
            "faceless adult hand comparing one clear scenario block with a closed brass padlock beside an analog "
            "calculator on deep green felt, sober editorial finance photograph"
        )
    if any(token in scene_text for token in ("save this", "test it", "money decision")):
        return (
            "faceless adult hand closing a leather planning folder beside an analog calculator and a large glass "
            "hourglass on a clean dark desk"
        )
    options = (
        "one clear starting-value block beside an analog calculator on a pale stone planning desk",
        "faceless adult hand adding one transparent growth layer to a larger acrylic base block",
        "three clear value columns increasing beside a large glass hourglass on brushed steel",
        "closed leather planning folder beside an analog calculator and a glass hourglass",
        "three transparent value layers crossing distinct positions on a brushed brass timing rail",
        "faceless adult hand returning one thin clear growth layer to the same larger acrylic base",
        "brass balance scale holding a clear scenario block opposite a closed padlock on deep green felt",
        "three unlabeled glass calculation stages arranged around one large mechanical metronome",
        "analog calculator beside a blank risk checklist and a closed leather planning folder",
        "faceless adult hand locking a completed clear scenario block into a brass planning frame",
    )
    return options[index % len(options)]


def _compound_interest_alternate_visual_seed(
    index: int,
    *,
    production_brief: dict[str, Any] | None = None,
) -> str:
    image_subject = str((production_brief or {}).get("image_subject") or "").casefold()
    if "no coins" in image_subject or "without coins" in image_subject:
        return _compound_interest_no_coin_visual_seed("", index)
    options = (
        "macro view of ten matching metal coins aligned in a black precision tray on pale limestone",
        "side profile of two plain coin towers at different heights on a charcoal stone plinth",
        "faceless adult hand placing one plain coin onto the tallest of three increasing coin stacks on brushed steel",
        "faceless adult hand moving two earned plain coins from a small brass dish onto one larger coin stack on black stone",
        "large clear hourglass beside a curved row of plain coins on a pale stone shelf",
        "brass balance scale with coins and a closed padlock on deep green felt",
        "three concentric timing rings around one coin on a dark museum pedestal",
        "four plain metal scenario markers spaced across an unlabeled charcoal timeline mat",
        "closed leather wallet beside a mechanical metronome on a warm oak shelf",
        "architectural staircase of brushed-steel steps beside a slow mechanical metronome",
    )
    return options[index % len(options)]


def _raise_allocation_visual_seed(scene_text: str) -> str:
    text = scene_text.casefold()
    if any(term in text for term in ("recurring expense", "new recurring bill", "monthly expense")) and any(
        term in text for term in ("absorb", "take", "eat", "consume", "extra cash", "raise")
    ):
        return (
            "faceless adult hand moving fresh coins from a plain pay envelope directly into a separate blank "
            "recurring-bill envelope, while the open wallet holds no extra coins and a glass savings jar stays "
            "untouched, the new obligation visibly taking the extra income"
        )
    if "savings goal" in text and any(term in text for term in ("choose", "before", "set aside", "allocate")):
        return (
            "faceless adult hand deliberately moving one selected group of coins from a plain pay envelope into "
            "a separate clear goal jar before touching a blank bill envelope, with the remaining coins still in "
            "an open leather wallet"
        )
    if any(term in text for term in ("what remains", "remaining money", "remaining cash")) and any(
        term in text for term in ("available", "choice", "everyday")
    ):
        return (
            "a closed clear goal jar set safely to one side while an open leather wallet visibly holds the "
            "remaining coins next to a plain everyday shopping basket, faceless adult hand choosing what to spend"
        )
    return ""


def _finance_scene_visual_seed(topic: str, scene_text: str) -> str:
    """Mapuje wypowiadany beat finansowy na jednoznaczna, fizyczna akcje."""
    text = f"{topic} {scene_text}".casefold()
    if _is_compound_interest_topic(topic, scene_text):
        return _compound_interest_visual_seed(scene_text, 0)
    allocation_seed = _raise_allocation_visual_seed(scene_text)
    if allocation_seed:
        return allocation_seed
    if "real gap starts" in text and any(token in text for token in ("earn it", "income", "paycheck")):
        return (
            "a sealed payday envelope feeding fresh cash and coins through a brass allocation gate toward one household "
            "spending bowl and one ceramic savings bowl, making the income split visible before any purchase"
        )
    if all(token in text for token in ("delivery", "subscription", "upgrade")):
        return (
            "one plain delivery bag, one subscription token, and one unbranded upgrade box arriving one by one "
            "on a black checkout conveyor, each pulling a separate coin from an open leather wallet"
        )
    if any(token in text for token in ("too small to matter", "together they become a pattern", "become a pattern")):
        return (
            "many small everyday purchase objects forming a domino chain across a dark walnut desk toward an open "
            "empty leather wallet, the combined spending pattern visibly blocking a ceramic savings bowl"
        )
    if any(token in text for token in ("assigned without a plan", "money did not vanish", "without a plan")):
        return (
            "mixed loose coins scattered before a stainless-steel mechanical coin sorter and three empty allocation "
            "trays, with an open wallet showing money left without a destination"
        )
    if any(token in text for token in ("friction before the first purchase", "regret after the fifth")):
        return (
            "a closed leather wallet behind a brass barrier while five plain everyday purchase objects queue on a "
            "checkout conveyor, the first purchase physically stopped before the scanner"
        )
    if any(token in text for token in ("out of autopilot", "before you think about investing", "autopilot")):
        return (
            "one coin redirected by a brass switch away from a moving shopping basket toward a ceramic investment "
            "bowl and small growing plant, with an hourglass marking the deliberate decision"
        )
    if any(token in text for token in ("boring pause", "keeps the rest of your paycheck alive", "paycheck alive")):
        return (
            "a sealed payday envelope and closed leather wallet protected behind a red velvet barrier beside a large "
            "analog clock, while a plain shopping basket remains stopped in the foreground"
        )
    if any(token in text for token in ("paycheck lands", "moment you feel richer")):
        return (
            "a sealed payday envelope landing beside a newly opened leather wallet and a large analog clock, with "
            "fresh coins still untouched on a dark walnut desk"
        )
    if "convenience gets louder" in text:
        return (
            "a paper delivery bag, takeaway cup, and plain parcel beginning a domino chain toward an open empty "
            "leather wallet while one coin rolls away"
        )
    if any(token in text for token in ("saving money will not make you rich", "saving alone")):
        return (
            "a nearly empty leather wallet at a physical fork between a shopping basket and a ceramic savings bowl, "
            "with one gold coin rolling toward the spending side"
        )
    if any(token in text for token in ("one money decision today", "save this and test it")):
        return (
            "a stainless-steel mechanical coin sorter directing one final coin from an open wallet into a ceramic "
            "savings bowl beside a small growing plant"
        )
    return ""


def _rebuild_finance_repair_scenes(
    context: ProductionContext,
    profile: ProductionChannelProfile,
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create a materially different finance storyboard after visual QA fails."""
    candidates = _candidate_visual_lines(context.artifacts.get("script", {}))
    rebuilt: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        scene_text = _scene_text(scene, candidates, fallback=context.topic.title)
        script_lines = scene.get("script_lines") if isinstance(scene.get("script_lines"), list) else []
        candidate_index = script_lines[0] if script_lines else index
        candidate = (
            candidates[candidate_index]
            if isinstance(candidate_index, int) and 0 <= candidate_index < len(candidates)
            else {}
        )
        # A repair must preserve the meaning of its own segment. The previous
        # index-only seed table could assign a checkout scene to an income line.
        subject = _finance_scene_visual_seed(context.topic.title, scene_text)
        if not subject:
            subject = str(candidate.get("visual_note") or "").strip()
        if not subject or _is_generic_visual_prompt(subject, profile):
            subject = _topic_specific_visual_seed(profile, context.topic.title, scene_text)
        rebuilt.append(
            {
                **scene,
                "visual_subject": subject,
                "visual_prompt": _compose_visual_prompt(profile, context.topic.title, scene_text, subject),
                "quality_notes": "visual storyboard rebuilt after QA with a distinct narrative concept",
                "repair_concept_index": index,
            }
        )
    return rebuilt


def _unique_alternate_visual_seed(
    profile: ProductionChannelProfile,
    anchor_terms: tuple[str, ...],
    topic: str,
    scene_text: str,
    index: int,
    used_subject_keys: set[str],
) -> str:
    for offset in range(12):
        if profile.key in {"wealth_blueprint", "finance"}:
            candidate = _finance_anchor_visual_seed(
                _safe_finance_visual_seed(topic, scene_text, index + offset),
                anchor_terms,
            )
        else:
            candidate = _alternate_topic_visual_seed(profile, topic, scene_text, index + offset)
        candidate_key = _visual_subject_key(profile, candidate)
        if candidate_key and candidate_key not in used_subject_keys:
            return candidate
    if profile.key in {"wealth_blueprint", "finance"}:
        return _finance_anchor_visual_seed(_safe_finance_visual_seed(topic, scene_text, index), anchor_terms)
    return _alternate_topic_visual_seed(profile, topic, scene_text, index)


def _finance_anchor_visual_seed(subject: str, anchor_terms: tuple[str, ...]) -> str:
    terms = set(anchor_terms)
    if terms & {"paycheck", "income", "salary"}:
        anchor = "the payday income is visibly protected before any purchase"
    elif terms & {"purchase", "spending", "shopping basket", "card"}:
        anchor = "the stopped shopping path makes purchase hesitation unmistakable"
    elif terms & {"savings", "bank", "account"}:
        anchor = "the sealed savings destination makes money redirection unmistakable"
    elif terms & {"investing", "index", "etf", "portfolio"}:
        anchor = "the balance and allocation trays communicate long-term investing"
    elif terms & {"debt", "credit card", "chain"}:
        anchor = "the closed wallet is physically constrained by a heavy debt chain"
    else:
        anchor = "two clearly separated physical paths communicate a budget choice"
    return f"{subject}, {anchor}"


def _topic_specific_visual_seed(profile: ProductionChannelProfile, topic: str, scene_text: str) -> str:
    text = f"{topic} {scene_text}".casefold()
    if profile.key in {"wealth_blueprint", "finance"}:
        if _is_compound_interest_topic(topic, scene_text):
            return _compound_interest_visual_seed(scene_text, 0)
        semantic_seed = _finance_scene_visual_seed(topic, scene_text)
        if semantic_seed:
            return semantic_seed
        if any(token in text for token in ("earn", "income", "salary", "paycheck", "zarob", "pensj")):
            return (
                "an open wallet beside a large analog clock, with loose cash and coins rolling toward a shopping basket "
                "and a ceramic savings bowl on a dark walnut desk"
            )
        if any(token in text for token in ("purchase", "spending", "temporary", "fix", "decision", "buy")):
            return (
                "a shopping basket stopped at a fork between a takeaway cup and a ceramic savings bowl, "
                "with cash and coins balanced on the edge beside a small growing plant"
            )
        if any(token in text for token in ("saving", "savings", "oszcz", "cash", "account")):
            return (
                "a mechanical coin sorter directing cash and coins from an open wallet into a transparent savings jar, "
                "with a small plant showing progress"
            )
        if any(token in text for token in ("invest", "etf", "index", "market", "compound", "72")):
            return (
                "a brass balance scale holding cash and coins opposite a small growing plant beside an hourglass on a dark desk, "
                "with three ceramic bowls showing long-term allocation"
            )
        return "a mechanical coin sorter splitting cash and coins between a shopping basket and a ceramic savings bowl on a dark premium desk"
    if profile.key in {"dark_chronicles", "history"}:
        if any(token in text for token in ("baptism", "baptized", "baptised", "sunday", "lord's day", "doctrine", "religious")):
            return (
                "torchlit medieval church interior with a stone baptismal font, priest, swaddled infant, "
                "sealed parchment decree, crucifix, and candle smoke"
            )
        if any(token in text for token in ("law", "medieval", "court", "rule", "king", "trial")):
            return (
                "torchlit medieval courtroom with a sealed parchment law, wax seal, wooden gavel, "
                "court clerk, and shadowed witnesses"
            )
        if any(token in text for token in ("vanish", "map", "lost", "disappear")):
            return "antique map on an archive table with one town scratched out under candlelight"
        if any(token in text for token in ("plague", "dance", "dancing", "died", "death")):
            return "empty cobblestone street with scattered shoes, candle smoke, and a church silhouette in darkness"
        return "single historical artifact on a dark archive table, parchment, old map, candle flame, dust in light"
    if profile.key in {"sigma_squad", "brainrot"}:
        if any(token in text for token in ("npc", "mode", "unlock", "activated")):
            return "glitchy 3D NPC avatar staring at a phone while neon game UI blocks explode around him"
        if any(token in text for token in ("bro", "class", "mew", "rizz", "ohio")):
            return "oversaturated classroom table with a neon meme avatar, floating phone reactions, and chaotic arrows"
        return "deep-fried neon 3D meme avatar reacting to a phone screen, glitch particles and chaotic UI shapes"
    return f"specific symbolic scene about {topic} with one clear subject, visible action, and topic-related props"


def _concrete_topic_visual_seed(
    profile: ProductionChannelProfile,
    anchor_terms: tuple[str, ...],
    topic: str,
    scene_text: str,
    index: int,
) -> str:
    if profile.key in {"dark_chronicles", "history"}:
        text = f"{topic} {scene_text} {' '.join(anchor_terms)}".casefold()
        if any(token in text for token in ("baptism", "baptized", "baptised", "sunday", "lord's day", "church", "priest")):
            options = (
                "stone baptismal font in a medieval church, priest holding a sealed parchment decree, candle smoke, crucifix, swaddled infant",
                "close-up of a wax-sealed church decree beside a wooden crucifix, candle, baptismal bowl, and gloved hand turning the page",
                "torchlit chapel doorway with a priest, worried parents, parchment law scroll, baptismal font, and shadowed witnesses",
            )
            return options[index % len(options)]
        if any(token in text for token in ("law", "court", "trial", "king", "medieval", "parchment")):
            options = (
                "medieval courtroom table with sealed parchment law, wax seal, wooden gavel, candle, and court clerk's hand pointing at the decree",
                "archive table covered with parchment law scroll, gavel, iron key, candle, and a shadowed magistrate figure in the background",
                "close-up of a red wax seal pressed onto a medieval law document beside a gavel, quill, candle, and dark courtroom bench",
            )
            return options[index % len(options)]
    return _topic_anchor_visual_seed(profile, anchor_terms, topic, scene_text, index)


def _alternate_topic_visual_seed(
    profile: ProductionChannelProfile,
    topic: str,
    scene_text: str,
    index: int,
) -> str:
    if profile.key in {"wealth_blueprint", "finance"}:
        return _safe_finance_visual_seed(topic, scene_text, index)
    if profile.key in {"dark_chronicles", "history"}:
        semantic_seed = _dark_history_scene_visual_seed(topic, scene_text)
        if semantic_seed:
            return semantic_seed
        options = (
            "worn dancing shoes abandoned on Strasbourg cobblestones while dust lifts around moving feet in the background",
            "a crowded sixteenth-century public square with townspeople dancing and alarmed witnesses watching from doorways",
            "musicians playing on a rough wooden platform while an exhausted crowd keeps moving below the church tower",
            "close-up of bruised feet beside torn leather shoes on sunlit cobblestones, restrained documentary framing without gore",
            "a worried family watching the growing dancing crowd through an open timber-frame window above the street",
            "a civic clerk moving wooden tally tokens beside a sealed chest while the dancing crowd blurs beyond the town-hall arch",
            "the Strasbourg church bell tower over a packed street at dusk, dancers moving below torch-bearing witnesses",
            "an exhausted dancer supported by two townspeople near the city gate at dawn while the square empties behind them",
        )
        return options[index % len(options)]
    if profile.key in {"sigma_squad", "brainrot"}:
        options = (
            "neon 3D avatar frozen mid-reaction while chaotic phone notifications explode around the frame",
            "surreal classroom table with a giant glowing phone, distorted meme stickers, and a faceless character pointing",
            "game UI scoreboard cracking open above an NPC avatar, glitch particles and bright reaction symbols",
            "oversaturated street scene with a meme avatar sprinting away from floating emojis and broken UI panels",
        )
        return options[index % len(options)]
    return _topic_specific_visual_seed(profile, topic, scene_text)


def _dark_history_scene_visual_seed(topic: str, scene_text: str) -> str:
    """Map a spoken history beat to one drawable action instead of scene index."""
    text = f"{topic} {scene_text}".casefold()
    if not any(token in text for token in ("dancing plague", "strasbourg", "dancer", "dancing")):
        return ""
    if any(token in text for token in ("accounts disagree", "records", "deaths", "panic")):
        return (
            "a civic clerk seated at a heavy wooden desk moving tally tokens beside closed wax-sealed "
            "leather-bound ledgers with blank covers while three worried officials argue behind him"
        )
    if any(token in text for token in ("psychogenic", "extreme stress", "modern theories")):
        return "a distressed dancer surrounded by tense townspeople whose fearful reactions spread through the group"
    if any(token in text for token in ("no explanation", "five centuries", "closes the case")):
        return "the Strasbourg city gate at dawn with abandoned worn shoes in the foreground and an uncertain empty street beyond"
    if any(token in text for token in ("follow for", "sources, not myths", "outro")):
        return "a closed wax-sealed archive chest beside an hourglass and extinguishing candle on a dark oak table"
    if any(token in text for token in ("authorities", "hired musicians", "opened halls")):
        return "civic leaders opening a wooden guild hall as musicians carry drums and string instruments toward exhausted dancers"
    if any(token in text for token in ("collapsed", "summer heat", "exhausted dancers")):
        return "an exhausted dancer supported by two townspeople at the edge of the square in harsh summer light"
    if any(token in text for token in ("neighbors joined", "became a crowd", "dozens")):
        return "a Strasbourg public square filling with off-balance dancers while alarmed witnesses lean from timber-framed windows"
    if any(token in text for token in ("one woman", "began dancing", "lone woman")):
        return "a lone woman dancing uncontrollably on a Strasbourg cobblestone street while worried neighbors watch from doorways"
    if any(token in text for token in ("days passed", "kept moving", "worn shoes")):
        return "worn leather shoes scraping cobblestones as a church shadow advances across the street"
    return ""


def _topic_anchor_visual_seed(
    profile: ProductionChannelProfile,
    anchor_terms: tuple[str, ...],
    topic: str,
    scene_text: str,
    index: int,
) -> str:
    terms = set(anchor_terms)
    if profile.key in {"wealth_blueprint", "finance"}:
        if _is_compound_interest_topic(topic, scene_text):
            return _compound_interest_visual_seed(scene_text, index)
        if terms & {"paycheck", "income", "salary"}:
            return (
                "an open wallet beside a large analog clock, with loose coins rolling toward a shopping basket "
                "and a ceramic savings bowl"
            )
        if terms & {"purchase", "spending", "receipt", "cart"}:
            return (
                "a shopping basket stopped at a fork between a takeaway cup and a ceramic savings bowl, "
                "with one coin balanced on the edge"
            )
        if terms & {"savings", "bank", "account"}:
            return (
                "a mechanical coin sorter directing coins from an open wallet into a transparent savings jar "
                "beside a small growing plant"
            )
        if terms & {"investing", "index", "etf", "portfolio"}:
            return (
                "a brass balance scale holding coins opposite a small growing plant beside an hourglass, "
                "with three ceramic bowls showing long-term allocation"
            )
        if terms & {"debt", "credit card", "bill", "statement"}:
            return (
                "a heavy chain wrapped around an empty wallet beside a shrinking coin stack on a dark desk"
            )
    if profile.key in {"dark_chronicles", "history"}:
        terms_text = " ".join(terms)
        if any(token in terms_text for token in ("baptism", "priest", "church", "infant")):
            return (
                "stone baptismal font in a medieval church, priest holding a sealed parchment decree, "
                "swaddled infant, crucifix, candle smoke, and shadowed parents"
            )
        if any(token in terms_text for token in ("courtroom", "gavel", "parchment", "medieval")):
            return (
                "medieval courtroom table with sealed parchment law, wax seal, wooden gavel, candle, "
                "court clerk's hand, and shadowed witnesses"
            )
        if any(token in terms_text for token in ("map", "archive", "scratched")):
            return (
                "archive table with an antique map, scratched route, parchment note, candle, and gloved hand "
                "pointing at the missing place"
            )
    return _alternate_topic_visual_seed(profile, topic, scene_text, index)


def _visual_subject_key(profile: ProductionChannelProfile, subject: str) -> str:
    lowered = subject.casefold()
    if profile.key in {"wealth_blueprint", "finance"}:
        compound_subjects = (
            ("transparent starting value block", "compound_start"),
            ("transparent green value block", "compound_year_one"),
            ("three distinct transparent value columns", "compound_progression"),
            ("complete first-year transparent block", "compound_reinvestment"),
            ("brass feedback mechanism", "compound_feedback"),
            ("long ribbon of blank calendar pages", "compound_timeline"),
            ("blank risk checklist", "compound_risk_check"),
            ("hypothetical long-term scenario", "compound_scenario"),
            ("closed planning folder", "compound_cta"),
            ("three distinct glass growth stages", "compound_growth_stages"),
        )
        for marker, key in compound_subjects:
            if marker in lowered:
                return key
        if any(token in lowered for token in ("allocation gate", "income split")):
            return "finance_income_allocation"
        if "mechanical coin sorter" in lowered:
            return "finance_allocation_sorter"
        if any(token in lowered for token in ("checkout conveyor", "conveyor", "scanner")):
            return "finance_checkout_choice"
        if any(token in lowered for token in ("velvet barrier", "visibly paused", "spending gate")):
            return "finance_purchase_pause"
        if "domino chain" in lowered:
            return "finance_micro_spending"
        if any(token in lowered for token in ("chain", "debt")):
            return "finance_debt_pressure"
        if any(token in lowered for token in ("hourglass", "analog clock")):
            return "finance_time_pressure"
        if any(token in lowered for token in ("floor drain", "leaking", "cracked transparent pipe")):
            return "finance_cash_leak"
        if any(token in lowered for token in ("balance scale", "long-term investing")):
            return "finance_investing_balance"
        if any(token in lowered for token in ("shopping basket", "purchase", "spending")):
            return "finance_purchase_decision"
        if any(token in lowered for token in ("savings jar", "coin sorter", "growing plant")):
            return "finance_saving_growth"
        if any(token in lowered for token in ("index", "fund", "chart", "calendar", "investor")):
            return "finance_investing_objects"
        if any(token in lowered for token in ("cash", "coin", "wallet", "bank statement")):
            return "finance_cash_wallet"
        if any(token in lowered for token in ("budget", "envelope", "wallet", "jar")):
            return "finance_budget_objects"
    if profile.key in {"dark_chronicles", "history"}:
        if any(token in lowered for token in ("dancing shoes", "bruised feet", "torn leather shoes")):
            return "history_dancing_shoes"
        if any(token in lowered for token in ("musicians", "wooden platform")):
            return "history_musicians_stage"
        if any(token in lowered for token in ("family watching", "timber-frame window")):
            return "history_window_witnesses"
        if any(token in lowered for token in ("civic clerk", "tally tokens", "town-hall")):
            return "history_civic_tally"
        if any(token in lowered for token in ("church bell tower", "packed street")):
            return "history_church_street"
        if any(token in lowered for token in ("city gate", "supported by two")):
            return "history_city_gate_dawn"
        if any(token in lowered for token in ("public square", "alarmed witnesses")):
            return "history_public_square"
        if any(token in lowered for token in ("baptism", "baptismal", "church", "priest", "infant", "crucifix")):
            return "history_church_baptism"
        if any(token in lowered for token in ("map", "route", "archive")):
            return "history_map_archive"
        if any(token in lowered for token in ("parchment", "law", "court", "gavel", "seal")):
            return "history_parchment_court"
        if any(token in lowered for token in ("candle", "artifact", "journal")):
            return "history_artifact_candle"
    if profile.key in {"sigma_squad", "brainrot"}:
        if any(token in lowered for token in ("avatar", "npc", "character")):
            return "brainrot_avatar"
        if any(token in lowered for token in ("phone", "notification", "reaction")):
            return "brainrot_phone_reaction"
        if any(token in lowered for token in ("game ui", "scoreboard", "glitch")):
            return "brainrot_game_ui"
    words = [word for word in lowered.replace(",", " ").split() if len(word) > 4]
    return "_".join(words[:3])


def _visual_relevance_terms(profile: ProductionChannelProfile, text: str) -> list[str]:
    lowered = text.casefold()
    terms_by_profile = {
        "wealth_blueprint": (
            "cash",
            "coin",
            "wallet",
            "credit card",
            "budget",
            "bank",
            "desk",
            "paycheck",
            "envelope",
            "plant",
            "jar",
            "hourglass",
            "cart",
            "basket",
            "purchase",
            "spending",
            "value block",
            "growth",
            "layer",
            "calculation tray",
            "balance scale",
            "contribution",
            "glass base",
            "scenario block",
        ),
        "finance": (
            "cash",
            "coin",
            "wallet",
            "credit card",
            "budget",
            "bank",
            "desk",
            "envelope",
            "plant",
            "jar",
            "hourglass",
            "cart",
            "basket",
            "purchase",
            "spending",
            "value block",
            "growth",
            "layer",
            "calculation tray",
            "balance scale",
            "contribution",
            "glass base",
            "scenario block",
        ),
        "dark_chronicles": (
            "woman",
            "dancer",
            "dancing",
            "crowd",
            "musician",
            "staff",
            "artifact",
            "map",
            "parchment",
            "candle",
            "courtroom",
            "gavel",
            "archive",
            "medieval",
            "cobblestone",
            "manuscript",
            "castle",
            "church",
            "priest",
            "baptismal",
            "font",
            "crucifix",
            "seal",
            "witness",
        ),
        "history": (
            "woman",
            "dancer",
            "dancing",
            "crowd",
            "musician",
            "staff",
            "artifact",
            "map",
            "parchment",
            "candle",
            "courtroom",
            "gavel",
            "archive",
            "medieval",
            "manuscript",
            "church",
            "priest",
            "baptismal",
            "font",
            "crucifix",
            "seal",
        ),
        "sigma_squad": (
            "phone",
            "npc",
            "avatar",
            "neon",
            "glitch",
            "game ui",
            "meme",
            "classroom",
            "reaction",
        ),
        "brainrot": (
            "phone",
            "avatar",
            "neon",
            "glitch",
            "meme",
            "surreal",
            "reaction",
        ),
    }
    terms = terms_by_profile.get(profile.key, ())
    return [term for term in terms if term in lowered]


def _visual_relevance_score(profile: ProductionChannelProfile, text: str) -> float:
    terms = _visual_relevance_terms(profile, text)
    if len(terms) >= 4:
        return 1.0
    if len(terms) == 3:
        return 0.78
    if len(terms) == 2:
        return 0.58
    if len(terms) == 1:
        return 0.36
    return 0.0


def _topic_anchor_terms(profile: ProductionChannelProfile, topic: str, scene_text: str) -> tuple[str, ...]:
    scene_text_normalized = str(scene_text or "").casefold().strip()
    text = scene_text_normalized or str(topic or "").casefold()
    if profile.key in {"wealth_blueprint", "finance"}:
        if _is_compound_interest_topic(topic, scene_text):
            return (
                "compound",
                "interest",
                "base",
                "growth",
                "coins",
                "coin",
                "hourglass",
                "layers",
                "periods",
                "tray",
                "tower",
                "gear",
                "flywheel",
                "balance scale",
                "padlock",
            )
        if any(token in text for token in ("assigned without a plan", "money did not vanish", "without a plan")):
            return ("budget", "cash", "coins", "wallet", "allocation", "trays", "sorter")
        if any(token in text for token in ("out of autopilot", "before you think about investing", "autopilot")):
            return ("investing", "portfolio", "cash", "coins", "bowl", "hourglass", "plant")
        if any(token in text for token in ("earn", "income", "salary", "paycheck", "zarob", "pensj")):
            return ("income", "paycheck", "cash", "coins", "envelope", "wallet", "desk", "shopping basket")
        if any(token in text for token in ("saving", "savings", "save", "oszcz", "cash stack", "bank account")):
            return ("savings", "cash", "coins", "wallet", "jar", "envelope", "plant")
        if any(token in text for token in ("purchase", "spending", "temporary", "fix", "decision", "buy", "cart")):
            return ("purchase", "spending", "shopping basket", "card", "cash", "wallet", "envelope")
        if any(token in text for token in ("invest", "etf", "index", "compound", "market", "portfolio")):
            return ("investing", "portfolio", "cash", "coins", "jar", "hourglass", "plant")
        if any(token in text for token in ("debt", "credit", "loan", "card")):
            return ("debt", "credit card", "chain", "coins", "wallet", "desk")
        return ("budget", "cash", "coins", "wallet", "envelope", "desk")
    if profile.key in {"dark_chronicles", "history"}:
        if any(token in text for token in ("baptism", "baptized", "baptised", "sunday", "lord's day", "doctrine", "religious")):
            return ("baptismal", "font", "church", "priest", "infant", "crucifix", "parchment", "candle", "medieval")
        if any(token in text for token in ("plague", "dance", "dancing", "death", "1518")):
            return ("dancing", "dancer", "woman", "crowd", "shoes", "cobblestone", "church", "street")
        if any(token in text for token in ("law", "trial", "court", "king", "medieval")):
            return ("courtroom", "gavel", "parchment", "candle", "medieval", "archive", "seal", "clerk")
        if any(token in text for token in ("vanish", "lost", "map", "disappear")):
            return ("map", "archive", "scratched", "parchment", "candle")
        return ("artifact", "map", "parchment", "candle", "archive")
    if profile.key in {"sigma_squad", "brainrot"}:
        if any(token in text for token in ("npc", "mode", "unlock", "activated")):
            return ("npc", "avatar", "phone", "glitch", "game ui")
        if any(token in text for token in ("rizz", "ohio", "mew", "sigma", "bro")):
            return ("avatar", "phone", "neon", "reaction", "classroom")
        return ("avatar", "phone", "neon", "glitch", "reaction")
    return ()


def _concrete_object_count(profile: ProductionChannelProfile, text: str) -> int:
    lowered = text.casefold()
    terms_by_profile = {
        "wealth_blueprint": (
            "person",
            "hand",
            "cash",
            "coin",
            "wallet",
            "card",
            "desk",
            "envelope",
            "plant",
            "jar",
            "hourglass",
            "basket",
            "cart",
            "purchase",
        ),
        "finance": (
            "person",
            "hand",
            "cash",
            "coin",
            "wallet",
            "card",
            "desk",
            "envelope",
            "plant",
            "jar",
            "hourglass",
            "basket",
            "cart",
            "purchase",
        ),
        "dark_chronicles": (
            "woman",
            "dancer",
            "dancing",
            "crowd",
            "musician",
            "staff",
            "artifact",
            "map",
            "parchment",
            "candle",
            "gavel",
            "courtroom",
            "street",
            "shoes",
            "manuscript",
            "castle",
            "archive table",
            "church",
            "priest",
            "baptismal",
            "font",
            "infant",
            "crucifix",
            "seal",
            "clerk",
            "witness",
        ),
        "history": (
            "woman",
            "dancer",
            "dancing",
            "crowd",
            "musician",
            "staff",
            "artifact",
            "map",
            "parchment",
            "candle",
            "gavel",
            "courtroom",
            "manuscript",
            "archive table",
            "church",
            "priest",
            "baptismal",
            "font",
            "infant",
            "crucifix",
            "seal",
            "clerk",
            "witness",
        ),
        "sigma_squad": (
            "avatar",
            "phone",
            "classroom",
            "table",
            "toy",
            "character",
            "face",
            "reaction",
            "neon blocks",
        ),
        "brainrot": (
            "avatar",
            "phone",
            "toy",
            "character",
            "face",
            "reaction",
            "neon blocks",
        ),
    }
    terms = terms_by_profile.get(profile.key, ("person", "hand", "phone", "object", "artifact", "desk", "map", "avatar"))
    return len({term for term in terms if term in lowered})


def _topic_anchor_matches(anchor_terms: tuple[str, ...], text: str) -> list[str]:
    lowered = text.casefold()
    return [term for term in anchor_terms if term in lowered]


def _topic_anchor_score(anchor_terms: tuple[str, ...], text: str) -> float:
    if not anchor_terms:
        return 1.0
    matches = _topic_anchor_matches(anchor_terms, text)
    if len(matches) >= 3:
        return 1.0
    if len(matches) == 2:
        return 0.7
    if len(matches) == 1:
        return 0.4
    return 0.0
