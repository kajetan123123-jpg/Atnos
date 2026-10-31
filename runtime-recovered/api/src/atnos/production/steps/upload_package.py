from __future__ import annotations

from atnos.core.config import Settings
from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.faceless_playbook import faceless_playbook_summary
from atnos.production.models import ProductionContext, StepResult
from atnos.production.publication_quality import public_quality_blockers, public_quality_evidence
from atnos.production.steps.common import clean_words, write_json


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    script = context.artifacts["script"]
    qa = context.artifacts["qa"]
    assembly = context.artifacts["assembly"]
    thumbnail = context.artifacts.get("thumbnail", {})
    operating_standards = context.artifacts.get("operating_standards", {})
    faceless_playbook = context.artifacts.get("faceless_playbook", {})
    profile = resolve_channel_profile(context)
    title = clean_words(context.topic.title, 12)
    public_quality_job = _public_quality_job(context)
    public_blockers = public_quality_blockers(settings, public_quality_job)
    public_evidence = public_quality_evidence(public_quality_job)
    public_ready = not public_blockers
    payload = {
        "review_id": f"review-{context.job_id}",
        "channel_account_id": context.account_id,
        "topic_id": context.topic.id,
        "video_file_path": str(assembly["output_file"]),
        "thumbnail_file_path": str(thumbnail.get("thumbnail_path") or ""),
        "subtitle_file_path": str(assembly.get("subtitle_file") or ""),
        "qa_report_path": str(context.artifact_dir / "qa_report.json"),
        "operating_standards_path": str(context.artifact_dir / "operating_standards.json"),
        "faceless_playbook_path": str(context.artifact_dir / "faceless_playbook.json"),
        "operating_standards": _standards_summary(operating_standards if isinstance(operating_standards, dict) else {}),
        "faceless_playbook": faceless_playbook_summary(faceless_playbook if isinstance(faceless_playbook, dict) else {}),
        "qa_recommendation": qa.get("overall"),
        "auto_uploadable": bool(qa.get("auto_uploadable")),
        "upload_decision": qa.get("upload_decision")
        or {
            "status": "ready_for_private_upload" if bool(qa.get("auto_uploadable")) else "blocked",
            "auto_uploadable": bool(qa.get("auto_uploadable")),
            "private_upload_allowed": bool(qa.get("auto_uploadable")),
            "required_failures": list(qa.get("blocking_failures") or []),
            "reason": "Legacy QA report without upload_decision.",
        },
        "blocking_failure_details": list(qa.get("blocking_failure_details") or []),
        "public_quality_ready": public_ready,
        "public_quality_blockers": public_blockers,
        "public_quality_evidence": public_evidence,
        "public_quality_next_action": _public_quality_next_action(public_blockers),
        "upload_metadata": {
            "title": title[:100],
            "description": _description(script),
            "tags": _tags(context.topic.title, profile.upload_tags),
            "language": profile.language_code,
            "audio_language": profile.language_code,
            "scheduled_time": None,
            "visibility": "private",
            "made_for_kids": False,
        },
        "status": "awaiting_human_review",
        "public_upload_allowed": False,
    }
    return write_json(context.artifact_dir / "youtube_private_upload_package.json", payload)


def _public_quality_job(context: ProductionContext) -> dict[str, Any]:
    return {
        "audio_timestamps": context.artifacts.get("voice", {}),
        "hooks": context.artifacts.get("hook", {}),
        "script": context.artifacts.get("script", {}),
        "asset_plan": context.artifacts.get("visuals", {}),
        "video_manifest": context.artifacts.get("video", {}),
        "qa_report": context.artifacts.get("qa", {}),
    }


def _public_quality_next_action(blockers: list[str]) -> str:
    if not blockers:
        return "human_review_then_private_upload_or_schedule_public"
    if "premium_visual_backend_required_for_public" in blockers:
        return "configure_premium_visual_backend_and_regenerate"
    if "premium_voice_required_for_public" in blockers:
        return "configure_premium_voice_and_regenerate"
    if "qa_not_passed_for_public" in blockers or "qa_not_auto_uploadable_for_public" in blockers:
        return "fix_qa_failures_and_regenerate"
    if "style_guide_missing_for_public" in blockers or "style_guide_evidence_missing_for_public" in blockers:
        return "attach_channel_style_guide_and_regenerate"
    return "inspect_public_quality_blockers"


def _standards_summary(payload: dict) -> dict:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    return {
        "used": bool(payload),
        "status": str(payload.get("status") or "missing") if payload else "missing",
        "project_type": str(payload.get("project_type") or ""),
        "source": {
            "path": source.get("path"),
            "exists": bool(source.get("exists")),
            "last_modified": source.get("last_modified"),
        },
        "section_keys": [
            str(section.get("key") or "")
            for section in sections
            if isinstance(section, dict) and str(section.get("key") or "").strip()
        ],
        "required_count": int(metrics.get("required_count") or 0),
        "blocked_until_configured_count": int(metrics.get("blocked_until_configured_count") or 0),
    }


def _description(script: dict) -> str:
    lines = [script.get("hook", ""), script.get("opening_line", ""), *script.get("body", []), script.get("cta", "")]
    return "\n".join(str(line).strip() for line in lines if str(line).strip())


def _tags(title: str, defaults: tuple[str, ...] | list[str] | None = None) -> list[str]:
    base = [word.strip(".,:;!?").casefold() for word in title.split() if len(word.strip(".,:;!?")) > 3]
    tags = [*(defaults or ("shorts", "youtube shorts")), *base]
    deduped: list[str] = []
    for tag in tags:
        if tag and tag not in deduped:
            deduped.append(tag)
    return deduped[:15]
