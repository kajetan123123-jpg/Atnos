from __future__ import annotations

import asyncio
import json

from atnos.core.config import Settings
from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.models import ProductionContext, StepResult
from atnos.production.qa_gate import build_qa_report
from atnos.production.steps.common import write_json
from atnos.production.visual_semantic_audit import audit_visual_semantics, extract_storyboard_contact_sheet


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    # Frame extraction and media analysis are CPU/subprocess heavy. Running them
    # on the event loop makes health checks time out and lets the watchdog kill
    # an otherwise healthy production job.
    semantic_audit = await asyncio.to_thread(_build_semantic_storyboard_audit, settings, context)
    context.artifacts["semantic_storyboard_audit"] = semantic_audit
    payload = await asyncio.to_thread(build_qa_report, context, settings=settings)
    payload["semantic_storyboard_audit"] = semantic_audit
    return write_json(context.artifact_dir / "qa_report.json", payload)


def _build_semantic_storyboard_audit(settings: Settings, context: ProductionContext) -> dict:
    assembly = context.artifacts.get("assembly") if isinstance(context.artifacts, dict) else None
    video_path = str((assembly or {}).get("output_file") or "") if isinstance(assembly, dict) else ""
    storyboard_path = context.artifact_dir / "qa_storyboard_contact_sheet.jpg"
    extracted = extract_storyboard_contact_sheet(video_path, storyboard_path)
    profile = resolve_channel_profile(context)
    audit = audit_visual_semantics(
        settings,
        extracted or storyboard_path,
        profile_key=profile.key,
        topic_title=context.topic.title,
        artifact_kind="storyboard_contact_sheet",
    )
    audit_path = context.artifact_dir / "semantic_storyboard_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    audit["audit_path"] = str(audit_path)
    audit["storyboard_path"] = str(extracted or storyboard_path)
    return audit
