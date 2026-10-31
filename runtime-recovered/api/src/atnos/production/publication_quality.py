from __future__ import annotations

from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import json_loads


PUBLIC_PREMIUM_VOICE_TIERS = {"premium_cloud", "primary"}
PUBLIC_PREMIUM_VISUAL_BACKENDS = {"ltx", "wan", "openai_image_motion"}
PUBLIC_STYLE_GUIDE_ARTIFACTS = ("hooks", "script", "asset_plan")


def public_quality_blockers(settings: Settings, job: dict[str, Any]) -> list[str]:
    """Return blockers that prevent a private Short from being promoted publicly."""
    blockers: list[str] = []
    if bool(getattr(settings, "shorts_require_premium_voice_before_publication", True)):
        voice = _voice_manifest(job)
        if not voice:
            blockers.append("voice_manifest_missing_for_public")
        elif str(voice.get("quality_tier") or "") not in PUBLIC_PREMIUM_VOICE_TIERS:
            blockers.append("premium_voice_required_for_public")

    qa_report = _qa_report(job)
    if qa_report and str(qa_report.get("overall") or "") != "passed":
        blockers.append("qa_not_passed_for_public")
    if qa_report and not bool(qa_report.get("auto_uploadable")):
        blockers.append("qa_not_auto_uploadable_for_public")
    blockers.extend(_style_guide_blockers(job))
    visual = _video_manifest(job)
    if not visual:
        blockers.append("video_manifest_missing_for_public")
    elif not _has_public_ready_visual_backend(visual):
        blockers.append("premium_visual_backend_required_for_public")
    elif not _visual_variety_passed(visual, qa_report):
        blockers.extend(_visual_variety_blockers(visual, qa_report))
    elif not _scene_concreteness_passed(visual):
        blockers.extend(_scene_concreteness_blockers(visual))
    elif not _visual_relevance_passed(visual):
        blockers.extend(_visual_relevance_blockers(visual))
    elif not _generated_image_audit_passed(visual):
        blockers.extend(_generated_image_audit_blockers(visual))
    return list(dict.fromkeys(blockers))


def public_quality_evidence(job: dict[str, Any]) -> dict[str, Any]:
    """Expose the quality evidence used by the public publication gate."""
    voice = _voice_manifest(job)
    visual = _video_manifest(job)
    qa_report = _qa_report(job)
    return {
        "voice_quality_tier": voice.get("quality_tier") if voice else None,
        "voice_backend_grade": voice.get("voice_backend_grade") if voice else None,
        "voice_profile": voice.get("voice_profile") if voice else None,
        "style_guide_used": _style_guide_used(job),
        "style_guide_artifacts": _style_guide_evidence(job),
        "operating_standards": _operating_standards_evidence(qa_report),
        "video_backend": _video_backend(visual) if visual else None,
        "clip_backends": _clip_backends(visual) if visual else [],
        "generated_clip_count": _quality_plan_value(visual, "generated_clip_count"),
        "unique_composition_count": _quality_plan_value(visual, "unique_composition_count"),
        "composition_diversity_ratio": _quality_plan_value(visual, "composition_diversity_ratio"),
        "actual_visual_variety": _actual_visual_variety(visual, qa_report),
        "scene_concreteness_risk_count": _quality_plan_value(visual, "scene_concreteness_risk_count"),
        "passes_scene_concreteness_floor": _quality_plan_value(visual, "passes_scene_concreteness_floor"),
        "avg_visual_relevance_score": _quality_plan_value(visual, "avg_visual_relevance_score"),
        "passes_visual_relevance_floor": _quality_plan_value(visual, "passes_visual_relevance_floor"),
        "topic_anchor_risk_count": _quality_plan_value(visual, "topic_anchor_risk_count"),
        "passes_topic_anchor_floor": _quality_plan_value(visual, "passes_topic_anchor_floor"),
        "generated_image_text_risk_count": _quality_plan_value(visual, "generated_image_text_risk_count"),
        "generated_image_visual_risk_count": _quality_plan_value(visual, "generated_image_visual_risk_count"),
        "passes_generated_image_text_floor": _quality_plan_value(visual, "passes_generated_image_text_floor"),
        "passes_generated_image_visual_floor": _quality_plan_value(visual, "passes_generated_image_visual_floor"),
        "public_ready_visual_backend": _has_public_ready_visual_backend(visual) if visual else False,
        "qa_overall": qa_report.get("overall") if qa_report else None,
        "qa_auto_uploadable": qa_report.get("auto_uploadable") if qa_report else None,
    }


def _voice_manifest(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get("audio_timestamps")
    if isinstance(value, dict):
        return value
    parsed = json_loads(str(value or ""), {})
    return parsed if isinstance(parsed, dict) else {}


def _qa_report(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get("qa_report")
    if isinstance(value, dict):
        return value
    parsed = json_loads(str(value or ""), {})
    return parsed if isinstance(parsed, dict) else {}


def _operating_standards_evidence(qa_report: dict[str, Any]) -> dict[str, Any]:
    standards = qa_report.get("operating_standards") if isinstance(qa_report, dict) else None
    if not isinstance(standards, dict):
        return {
            "used": False,
            "status": "missing",
            "project_type": "",
            "source": {},
            "section_keys": [],
            "required_count": 0,
            "blocked_until_configured_count": 0,
        }
    return {
        "used": bool(standards.get("used")),
        "status": str(standards.get("status") or "missing"),
        "project_type": str(standards.get("project_type") or ""),
        "source": standards.get("source") if isinstance(standards.get("source"), dict) else {},
        "section_keys": list(standards.get("section_keys") or []),
        "required_count": int(standards.get("required_count") or 0),
        "blocked_until_configured_count": int(standards.get("blocked_until_configured_count") or 0),
    }


def _video_manifest(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get("video_manifest")
    if isinstance(value, dict):
        return value
    parsed = json_loads(str(value or ""), {})
    return parsed if isinstance(parsed, dict) else {}


def _style_guide_blockers(job: dict[str, Any]) -> list[str]:
    evidence = _style_guide_evidence(job)
    if any(item is None for item in evidence.values()):
        return ["style_guide_evidence_missing_for_public"]
    if not _style_guide_used(job):
        return ["style_guide_missing_for_public"]
    return []


def _style_guide_used(job: dict[str, Any]) -> bool:
    evidence = _style_guide_evidence(job)
    return bool(evidence) and all(
        isinstance(item, dict) and bool(item.get("used"))
        for item in evidence.values()
    )


def _style_guide_evidence(job: dict[str, Any]) -> dict[str, Any]:
    return {
        artifact: _style_guide_from_artifact(job, artifact)
        for artifact in PUBLIC_STYLE_GUIDE_ARTIFACTS
    }


def _style_guide_from_artifact(job: dict[str, Any], artifact: str) -> dict[str, Any] | None:
    value = job.get(artifact)
    if isinstance(value, dict):
        payload = value
    else:
        parsed = json_loads(str(value or ""), {})
        payload = parsed if isinstance(parsed, dict) else {}
    style = payload.get("style_guide")
    if not isinstance(style, dict):
        return None
    return {
        "used": bool(style.get("used")),
        "status": str(style.get("status") or ""),
        "source": str(style.get("source") or "") or None,
    }


def _video_backend(video_manifest: dict[str, Any]) -> str:
    backend = video_manifest.get("backend")
    if isinstance(backend, dict):
        return str(backend.get("mode") or "")
    return str(backend or "")


def _clip_backends(video_manifest: dict[str, Any]) -> list[str]:
    clips = video_manifest.get("clips") if isinstance(video_manifest.get("clips"), list) else []
    return [
        str(clip.get("backend") or "")
        for clip in clips
        if isinstance(clip, dict) and str(clip.get("backend") or "")
    ]


def _has_public_ready_visual_backend(video_manifest: dict[str, Any]) -> bool:
    clip_backends = _clip_backends(video_manifest)
    if not clip_backends:
        return False
    return all(backend in PUBLIC_PREMIUM_VISUAL_BACKENDS for backend in clip_backends)


def _generated_image_audit_passed(video_manifest: dict[str, Any]) -> bool:
    return not _generated_image_audit_blockers(video_manifest)


def _scene_concreteness_passed(video_manifest: dict[str, Any]) -> bool:
    return not _scene_concreteness_blockers(video_manifest)


def _visual_relevance_passed(video_manifest: dict[str, Any]) -> bool:
    return not _visual_relevance_blockers(video_manifest)


def _visual_variety_passed(video_manifest: dict[str, Any], qa_report: dict[str, Any]) -> bool:
    return not _visual_variety_blockers(video_manifest, qa_report)


def _visual_variety_blockers(video_manifest: dict[str, Any], qa_report: dict[str, Any]) -> list[str]:
    actual = _actual_visual_variety(video_manifest, qa_report)
    if not isinstance(actual, dict):
        return ["actual_visual_variety_missing_for_public"]
    status = str(actual.get("status") or "").strip().lower()
    if status != "pass":
        return ["actual_visual_variety_failed_for_public"]
    return []


def _scene_concreteness_blockers(video_manifest: dict[str, Any]) -> list[str]:
    risk = _quality_plan_value(video_manifest, "scene_concreteness_risk_count")
    floor = _quality_plan_value(video_manifest, "passes_scene_concreteness_floor")
    risk_value = _risk_count(risk)
    if risk_value is None or floor is None:
        return ["scene_concreteness_audit_missing_for_public"]
    if risk_value > 0 or floor is not True:
        return ["scene_concreteness_risk_for_public"]
    return []


def _visual_relevance_blockers(video_manifest: dict[str, Any]) -> list[str]:
    floor = _quality_plan_value(video_manifest, "passes_visual_relevance_floor")
    topic_floor = _quality_plan_value(video_manifest, "passes_topic_anchor_floor")
    topic_risk = _risk_count(_quality_plan_value(video_manifest, "topic_anchor_risk_count"))
    if floor is None:
        return ["visual_relevance_audit_missing_for_public"]
    if floor is not True:
        if topic_floor is False or (topic_risk is not None and topic_risk > 0):
            return ["topic_anchor_risk_for_public"]
        return ["visual_relevance_risk_for_public"]
    return []


def _generated_image_audit_blockers(video_manifest: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    text_risk = _quality_plan_value(video_manifest, "generated_image_text_risk_count")
    visual_risk = _quality_plan_value(video_manifest, "generated_image_visual_risk_count")
    text_floor = _quality_plan_value(video_manifest, "passes_generated_image_text_floor")
    visual_floor = _quality_plan_value(video_manifest, "passes_generated_image_visual_floor")

    text_risk_value = _risk_count(text_risk)
    visual_risk_value = _risk_count(visual_risk)

    if text_risk_value is None or text_floor is None:
        blockers.append("generated_image_text_audit_missing_for_public")
    elif text_risk_value > 0 or text_floor is not True:
        blockers.append("generated_image_text_risk_for_public")

    if visual_risk_value is None or visual_floor is None:
        blockers.append("generated_image_visual_audit_missing_for_public")
    elif visual_risk_value > 0 or visual_floor is not True:
        blockers.append("generated_image_visual_risk_for_public")
    return blockers


def _risk_count(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _quality_plan_value(video_manifest: dict[str, Any], key: str) -> Any:
    quality_plan = video_manifest.get("quality_plan")
    if not isinstance(quality_plan, dict):
        return None
    return quality_plan.get(key)


def _actual_visual_variety(video_manifest: dict[str, Any], qa_report: dict[str, Any]) -> dict[str, Any] | None:
    required_checks = qa_report.get("required_checks") if isinstance(qa_report, dict) else None
    if isinstance(required_checks, list):
        for check in required_checks:
            if not isinstance(check, dict) or str(check.get("name") or "") != "Visual variety":
                continue
            value = check.get("value") if isinstance(check.get("value"), dict) else {}
            actual = value.get("actual_visual_variety") if isinstance(value, dict) else None
            if isinstance(actual, dict):
                return actual
    quality_plan = video_manifest.get("quality_plan")
    if not isinstance(quality_plan, dict):
        return None
    actual = quality_plan.get("actual_visual_variety")
    return actual if isinstance(actual, dict) else None
