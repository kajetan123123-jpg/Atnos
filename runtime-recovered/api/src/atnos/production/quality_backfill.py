from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads, utc_now_iso
from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps.video_generator import _quality_plan


QUALITY_BACKFILL_SOURCE = "legacy_visual_plan_backfill"


def backfill_visual_quality_plan_for_job(settings: Settings, job: dict[str, Any]) -> dict[str, Any] | None:
    """Uzupełnij brakujące pola quality_plan dla starszych jobów bez renderowania nowych mediów.

    Backfill korzysta wyłącznie z istniejących artefaktów joba: `video_manifest.json`
    oraz `visual_plan.json`. Niczego nie publikuje i nie uruchamia kosztownych providerów.
    """
    video_manifest = _manifest_dict(job.get("video_manifest"))
    if not video_manifest:
        return None
    if not _needs_quality_backfill(video_manifest):
        return video_manifest

    artifact_dir = Path(str(job.get("artifact_dir") or "")).expanduser()
    visual_plan_path = artifact_dir / "visual_plan.json"
    video_manifest_path = artifact_dir / "video_manifest.json"
    if not artifact_dir.exists() or not visual_plan_path.exists():
        return video_manifest

    visual_plan = _read_json_file(visual_plan_path)
    scenes = visual_plan.get("scenes") if isinstance(visual_plan.get("scenes"), list) else []
    clips = video_manifest.get("clips") if isinstance(video_manifest.get("clips"), list) else []
    if not scenes or not clips:
        return video_manifest

    context = ProductionContext(
        job_id=str(job.get("id") or job.get("job_id") or ""),
        account_id=str(job.get("account_id") or ""),
        topic=ApprovedTopic(
            id=str(job.get("topic_id") or ""),
            account_id=str(job.get("account_id") or ""),
            title=str(job.get("topic_title") or job.get("title") or ""),
            description=str(job.get("topic_description") or job.get("description") or ""),
            total_score=float(job.get("topic_total_score") or 0),
            content_profile=str(job.get("content_profile") or ""),
        ),
        artifact_dir=artifact_dir,
    )
    channel_profile = resolve_channel_profile(context, json.dumps(visual_plan.get("style_guide") or {}))
    updated_manifest = dict(video_manifest)
    updated_quality_plan = {
        **(video_manifest.get("quality_plan") if isinstance(video_manifest.get("quality_plan"), dict) else {}),
        **_quality_plan(settings, scenes, clips, channel_profile=channel_profile),
        "backfilled": True,
        "backfill_source": QUALITY_BACKFILL_SOURCE,
        "backfilled_at": utc_now_iso(),
    }
    updated_manifest["quality_plan"] = updated_quality_plan
    if video_manifest_path.exists():
        video_manifest_path.write_text(
            json.dumps(updated_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    _persist_video_manifest(settings, str(context.job_id), updated_manifest)
    return updated_manifest


def _needs_quality_backfill(video_manifest: dict[str, Any]) -> bool:
    quality_plan = video_manifest.get("quality_plan")
    if not isinstance(quality_plan, dict):
        return True
    required_keys = (
        "scene_concreteness_risk_count",
        "passes_scene_concreteness_floor",
        "passes_visual_relevance_floor",
        "topic_anchor_risk_count",
        "passes_topic_anchor_floor",
        "generated_image_text_risk_count",
        "passes_generated_image_text_floor",
        "generated_image_visual_risk_count",
        "passes_generated_image_visual_floor",
    )
    return any(key not in quality_plan or quality_plan.get(key) is None for key in required_keys)


def _manifest_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    parsed = json_loads(str(value or ""), {})
    return parsed if isinstance(parsed, dict) else {}


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _persist_video_manifest(settings: Settings, job_id: str, manifest: dict[str, Any]) -> None:
    if not job_id:
        return
    with connect(settings) as db:
        db.execute(
            """
            UPDATE pipeline_jobs
            SET video_manifest = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(manifest, ensure_ascii=False), utc_now_iso(), job_id),
        )
        db.commit()
