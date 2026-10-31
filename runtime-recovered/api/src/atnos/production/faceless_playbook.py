from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.production.channel_profile import ProductionChannelProfile, resolve_channel_profile
from atnos.production.models import ApprovedTopic, ProductionContext


def build_faceless_playbook(settings: Settings, context: ProductionContext) -> dict[str, Any]:
    """Build a production playbook that every faceless Short generation step can consume."""
    profile = resolve_channel_profile(context)
    source = _latest_side_research_report(Path(settings.data_dir).resolve())
    template_key = _template_key(profile)
    template = _template(profile, template_key)
    return {
        "status": "ready",
        "channel_profile": {
            "key": profile.key,
            "content_label": profile.content_label,
            "language_code": profile.language_code,
            "narration_style": profile.narration_style,
        },
        "template_key": template_key,
        "template_label": template["label"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source) if source else None,
            "exists": bool(source),
            "last_modified": datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc).isoformat()
            if source
            else None,
        },
        "hook_pattern": template["hook_pattern"],
        "script_structure": template["script_structure"],
        "voice_direction": template["voice_direction"],
        "visual_direction": template["visual_direction"],
        "pacing": template["pacing"],
        "thumbnail_rules": template["thumbnail_rules"],
        "qa_focus": template["qa_focus"],
        "forbidden": template["forbidden"],
        "automation_notes": template["automation_notes"],
    }


def build_faceless_playbook_for_project(
    settings: Settings,
    *,
    project_id: str,
    title: str = "",
    description: str = "",
    content_profile: str = "",
) -> dict[str, Any]:
    """Build the same playbook from a project id, for API/UI inspection before a job exists."""
    context = ProductionContext(
        job_id="preview",
        account_id=project_id,
        topic=ApprovedTopic(
            id="preview-topic",
            account_id=project_id,
            title=title or project_id,
            description=description,
            total_score=0.0,
            content_profile=content_profile,
        ),
        artifact_dir=Path(settings.data_dir) / "production" / "_preview",
    )
    return build_faceless_playbook(settings, context)


def faceless_playbook_prompt_block(playbook: dict[str, Any] | None, *, max_chars: int = 5000) -> str:
    """Return a compact prompt block with the active production playbook."""
    if not isinstance(playbook, dict) or not playbook:
        return ""
    compact = {
        "template_key": playbook.get("template_key"),
        "template_label": playbook.get("template_label"),
        "hook_pattern": playbook.get("hook_pattern"),
        "script_structure": playbook.get("script_structure"),
        "voice_direction": playbook.get("voice_direction"),
        "visual_direction": playbook.get("visual_direction"),
        "pacing": playbook.get("pacing"),
        "thumbnail_rules": playbook.get("thumbnail_rules"),
        "qa_focus": playbook.get("qa_focus"),
        "forbidden": playbook.get("forbidden"),
    }
    body = repr(compact)[:max_chars]
    return (
        "\n\nFACELESS CHANNEL PLAYBOOK - REQUIRED PRODUCTION INPUT:\n"
        f"{body}\n"
        "Use this playbook as hard direction, not optional inspiration. "
        "Do not generate against a different format.\n"
        "END FACELESS CHANNEL PLAYBOOK.\n"
    )


def faceless_playbook_summary(playbook: dict[str, Any] | None) -> dict[str, Any]:
    payload = playbook if isinstance(playbook, dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    return {
        "used": bool(payload),
        "status": str(payload.get("status") or "missing") if payload else "missing",
        "template_key": str(payload.get("template_key") or ""),
        "template_label": str(payload.get("template_label") or ""),
        "channel_profile": payload.get("channel_profile") if isinstance(payload.get("channel_profile"), dict) else {},
        "source": {
            "path": source.get("path"),
            "exists": bool(source.get("exists")),
            "last_modified": source.get("last_modified"),
        },
    }


def _latest_side_research_report(data_dir: Path) -> Path | None:
    reports_dir = data_dir / "learning" / "side_research"
    if not reports_dir.exists():
        return None
    reports = sorted(
        [
            path
            for path in reports_dir.rglob("*.md")
            if path.name.casefold() not in {"readme.md"}
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return reports[0] if reports else None


def _template_key(profile: ProductionChannelProfile) -> str:
    if profile.key in {"dark_chronicles", "history"}:
        return "mystery_documentary"
    if profile.key in {"brainrot", "sigma_squad"}:
        return "shorts_microstory"
    return "science_explainer"


def _template(profile: ProductionChannelProfile, template_key: str) -> dict[str, Any]:
    common_forbidden = [
        "No generic AI content-farm narration.",
        "No unsupported numbers, earnings, market claims, or fabricated analytics.",
        "No third-party clips, logos, celebrities, or copied competitor branding without rights proof.",
        "No readable text inside generated images; add captions in post-production only.",
    ]
    if template_key == "mystery_documentary":
        return {
            "label": "Mystery Documentary",
            "hook_pattern": {
                "type": "danger_tension_or_hidden_truth",
                "target_sec": 3,
                "rule": "Open with one disturbing verified detail, then delay the explanation.",
                "examples": ["They danced until they died", "History remembers the wrong detail"],
            },
            "script_structure": [
                "0-3s: unsettling verified hook",
                "3-8s: who/where/when setup",
                "8-25s: escalation with one source-backed detail",
                "25-45s: context or contradiction",
                "45-60s: payoff and sober takeaway",
            ],
            "voice_direction": {
                "style": profile.narration_style,
                "pace_wpm": "120-145",
                "delivery": "controlled suspense, no melodrama, small pauses before reveals",
            },
            "visual_direction": {
                "style": profile.visual_style,
                "scene_change_sec": "2-5",
                "must_include": ["artifact", "map/archive", "single strong foreground subject", "visible motion"],
                "avoid": ["gore", "empty dark gradients", "random symbols unrelated to the segment"],
            },
            "pacing": {
                "first_visual_change_sec": 2,
                "retention_beat_interval_sec": 10,
                "max_static_sec": 5,
            },
            "thumbnail_rules": [
                "One historical artifact or scene, not a collage.",
                "Dark contrast with one accent color.",
                "No fake dates or invented names.",
            ],
            "qa_focus": ["source-backed history", "no myth as fact", "visuals tied to segment"],
            "forbidden": [*common_forbidden, "No ancient-aliens or pseudoscience framing as fact."],
            "automation_notes": ["Use source map before script.", "Use archive-style visual prompts per segment."],
        }
    if template_key == "shorts_microstory":
        return {
            "label": "Shorts Microstory",
            "hook_pattern": {
                "type": "visual_shock_or_absurd_question",
                "target_sec": 2,
                "rule": "Start with a visual idea that is understandable before audio.",
                "examples": ["Sigma mode activated", "Who has more rizz here?"],
            },
            "script_structure": [
                "0-2s: immediate absurd visual hook",
                "2-8s: simple setup",
                "8-25s: escalation in 2-3 beats",
                "25-45s: twist/payoff",
                "45-60s: loopable final line or comment prompt",
            ],
            "voice_direction": {
                "style": profile.narration_style,
                "pace_wpm": "165-190",
                "delivery": "punchy, clean, understandable, no profanity dependency",
            },
            "visual_direction": {
                "style": profile.visual_style,
                "scene_change_sec": "1.5-3",
                "must_include": ["one meme subject", "visible action", "safe absurdity", "strong color contrast"],
                "avoid": ["copyrighted characters", "real-person targeting", "sexualized content", "dangerous challenges"],
            },
            "pacing": {
                "first_visual_change_sec": 1.5,
                "retention_beat_interval_sec": 6,
                "max_static_sec": 3,
            },
            "thumbnail_rules": [
                "First frame must work as thumbnail for Shorts.",
                "One oversized subject and one clear reaction.",
                "No copyrighted character likeness.",
            ],
            "qa_focus": ["safe meme chaos", "mobile readability", "platform policy"],
            "forbidden": [*common_forbidden, "No NSFW, bullying, dangerous challenges, or copyrighted characters."],
            "automation_notes": ["Prefer loopable endings.", "Keep slang sparse and natural."],
        }
    return {
        "label": "Science Explainer",
        "hook_pattern": {
            "type": "counterintuitive_fact_or_consequence",
            "target_sec": 3,
            "rule": "Open with one practical contradiction, then explain the mechanism.",
            "examples": ["Saving money will not make you rich", "Understand this before you invest"],
        },
        "script_structure": [
            "0-3s: counterintuitive hook",
            "3-8s: define the viewer problem",
            "8-25s: explain the mechanism with one visual metaphor",
            "25-45s: concrete example without invented numbers",
            "45-60s: safe takeaway and non-advisory CTA",
        ],
        "voice_direction": {
            "style": profile.narration_style,
            "pace_wpm": "145-165",
            "delivery": "calm, precise, not salesy, no hype voice",
        },
        "visual_direction": {
            "style": profile.visual_style,
            "scene_change_sec": "2-5",
            "must_include": ["human-scale object", "dashboard/receipt/chart metaphor", "clear foreground action"],
            "avoid": ["abstract chart-only scenes", "fake profit visuals", "luxury flex imagery"],
        },
        "pacing": {
            "first_visual_change_sec": 2,
            "retention_beat_interval_sec": 8,
            "max_static_sec": 5,
        },
        "thumbnail_rules": [
            "One clear financial metaphor.",
            "No guaranteed-profit promise.",
            "Clean dark premium style with one accent color.",
        ],
        "qa_focus": ["education-first", "no investment advice", "sourceable numbers", "visual relevance"],
        "forbidden": [*common_forbidden, "No buy/sell recommendation or guaranteed-return language."],
        "automation_notes": ["Use real signals as context, not proof of future earnings.", "Keep CTA educational."],
    }
