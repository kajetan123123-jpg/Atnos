from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from typing import Any

from atnos.comfyui.client import ComfyUIClient, ensure_comfyui_running
from atnos.comfyui.workflow_builder import WorkflowBuilder
from atnos.core.config import Settings
from atnos.db.session import DatabaseManager
from atnos.intelligence.storage import read_intelligence_account_config
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.qa_gate import build_qa_report
from atnos.production.steps import asset_planner, hook_generator, script_generator, thumbnail_generator, voice_synthesis
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService, YouTubeQuotaExceededError
from atnos.pipeline.scene_renderer import SceneRenderer
from atnos.pipeline.video_assembler import VideoAssembler

logger = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class JobResult:
    job_id: str
    status: str
    video_url: str | None = None
    video_path: str | None = None
    error: str | None = None
    qa_report: dict[str, Any] | None = None
    upload_result: dict[str, Any] | None = None


async def run_production_pipeline(
    job_id: str,
    topic: dict[str, Any],
    channel_config: dict[str, Any],
    *,
    settings: Settings | None = None,
    event_sink: EventSink | None = None,
) -> JobResult:
    """Run one approved topic through real local production, QA and private upload."""
    settings = settings or Settings()
    account_id = str(channel_config.get("account_id") or topic.get("account_id") or "yt-finanse-pl")
    job_dir = settings.data_dir / "production-e2e" / account_id / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    approved_topic = ApprovedTopic(
        id=str(topic.get("id") or job_id),
        account_id=account_id,
        title=str(topic.get("title") or "Atnos test short"),
        description=str(topic.get("description") or topic.get("angle") or topic.get("hook") or ""),
        total_score=float(topic.get("estimated_virality") or topic.get("total_score") or 0),
    )
    context = ProductionContext(job_id=job_id, account_id=account_id, topic=approved_topic, artifact_dir=job_dir)

    async def log_stage(stage: str, status: str, message: str, progress: int) -> None:
        event = {
            "type": "pipeline_progress",
            "job_id": job_id,
            "stage": stage,
            "status": status,
            "message": message,
            "progress": progress,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        logger.info("[%s] %s: %s", job_id, stage, message)
        _append_jsonl(job_dir / "job_events.jsonl", event)
        _write_json(job_dir / "job_meta.json", event, merge=True)
        if event_sink is not None:
            maybe = event_sink(event)
            if asyncio.iscoroutine(maybe):
                await maybe

    try:
        await log_stage("hook", "running", "Generuję hooki...", 5)
        hook_result = await hook_generator.run(settings, context)
        context.artifacts["hook"] = hook_result.payload
        await log_stage("hook", "done", "Hook gotowy", 10)

        await log_stage("script", "running", "Generuję skrypt...", 12)
        script_result = await script_generator.run(settings, context)
        context.artifacts["script"] = script_result.payload
        script_text = _script_to_text(script_result.payload)
        (job_dir / "script.txt").write_text(script_text, encoding="utf-8")
        await log_stage("script", "done", f"Skrypt gotowy ({len(script_text.split())} słów)", 20)

        await log_stage("asset_plan", "running", "Planuję sceny...", 22)
        visual_result = await asset_planner.run(settings, context)
        context.artifacts["visuals"] = visual_result.payload
        await log_stage("asset_plan", "done", f"{len(visual_result.payload.get('scenes') or [])} scen zaplanowanych", 30)

        await log_stage("tts", "running", "Generuję narrację i napisy...", 32)
        voice_result = await voice_synthesis.run(settings, context)
        context.artifacts["voice"] = voice_result.payload
        narration_path = Path(str(voice_result.payload.get("output_file") or voice_result.payload.get("audio_path") or ""))
        subtitles_raw = str(voice_result.payload.get("subtitle_path") or "")
        subtitles_path = Path(subtitles_raw) if subtitles_raw else None
        await log_stage("tts", "done", f"Audio gotowe ({voice_result.payload.get('duration_sec', 0)}s)", 45)

        await log_stage("visuals", "running", "Renderuję sceny przez ComfyUI...", 47)
        comfyui_ok = await asyncio.to_thread(ensure_comfyui_running, settings)
        client = ComfyUIClient(base_url=settings.comfyui_base_url) if comfyui_ok else None
        builder = WorkflowBuilder(
            {
                "comfyui_base_url": settings.comfyui_base_url,
                "sd_model_checkpoint": settings.comfyui_default_checkpoint or "sd_xl_base_1.0_0.9vae.safetensors",
                "comfyui_negative_prompt": settings.comfyui_negative_prompt,
                "comfyui_steps": min(int(settings.comfyui_steps or 16), 8),
                "comfyui_cfg_scale": settings.comfyui_cfg_scale,
                "comfyui_sampler_name": settings.comfyui_sampler_name,
                "comfyui_scheduler": settings.comfyui_scheduler,
                "comfyui_render_width": 512,
                "comfyui_render_height": 896,
                "enable_video_workflow": False,
            },
            base_url=settings.comfyui_base_url,
        )
        renderer = SceneRenderer(client, builder, settings, event_sink=lambda event: _append_jsonl(job_dir / "render_events.jsonl", event))
        scenes = await asyncio.to_thread(renderer.render_all_scenes, visual_result.payload, job_dir)
        fallback_used = any(scene.method == "pil_fallback" for scene in scenes)
        context.artifacts["video"] = {
            "status": "frames_ready",
            "comfyui_ready": comfyui_ok,
            "scene_count": len(scenes),
            "methods": [scene.method for scene in scenes],
            "fallback_used": fallback_used,
        }
        await log_stage("visuals", "done", f"Sceny gotowe: {sorted(set(scene.method for scene in scenes))}", 65)

        await log_stage("thumbnail", "running", "Generuję miniaturę...", 67)
        thumb_result = await thumbnail_generator.run(settings, context)
        context.artifacts["thumbnail"] = thumb_result.payload
        thumbnail_path = Path(str(thumb_result.payload.get("thumbnail_path") or ""))
        await log_stage("thumbnail", "done", "Miniatura gotowa", 72)

        await log_stage("assembly", "running", "Składam short.mp4...", 74)
        final_video = await asyncio.to_thread(
            VideoAssembler().assemble,
            scenes,
            narration_path,
            subtitles_path if subtitles_path and subtitles_path.exists() else None,
            job_dir,
        )
        context.artifacts["assembly"] = {
            "status": "rendered",
            "output_file": str(final_video),
            "subtitle_file": str(subtitles_path) if subtitles_path else "",
            "render": {"ok": True},
        }
        context.artifacts["video"]["video_raw_path"] = str(final_video)
        await log_stage("assembly", "done", f"short.mp4 gotowy ({final_video.stat().st_size // 1024} KB)", 82)

        await log_stage("qa", "running", "Sprawdzam QA...", 85)
        qa_report = build_qa_report(context, settings=settings)
        _write_json(job_dir / "qa_report.json", qa_report)
        if not qa_report.get("auto_uploadable"):
            await log_stage("qa", "error", f"QA failed: {qa_report.get('blocking_failures')}", 85)
            return JobResult(job_id=job_id, status="qa_failed", video_path=str(final_video), qa_report=qa_report)
        await log_stage("qa", "done", "QA passed", 90)

        await log_stage("upload", "running", "Upload prywatny na YouTube...", 92)
        upload_result = await asyncio.to_thread(
            _upload_private,
            settings,
            account_id,
            final_video,
            thumbnail_path,
            subtitles_path if subtitles_path and subtitles_path.exists() else None,
            approved_topic,
            topic,
        )
        _write_json(job_dir / "upload_result.json", upload_result)
        youtube_url = str(upload_result.get("youtube_url") or "")
        await log_stage("upload", upload_result.get("status", "done"), youtube_url or upload_result.get("message", "upload done"), 100)
        return JobResult(
            job_id=job_id,
            status=str(upload_result.get("status") or "done"),
            video_url=youtube_url or None,
            video_path=str(final_video),
            qa_report=qa_report,
            upload_result=upload_result,
        )
    except Exception as exc:
        logger.exception("[%s] E2E pipeline crashed", job_id)
        await log_stage("pipeline", "error", str(exc), -1)
        _write_json(job_dir / "error.json", {"error": str(exc), "timestamp": datetime.now(UTC).isoformat()})
        return JobResult(job_id=job_id, status="error", error=str(exc))


def _upload_private(
    settings: Settings,
    account_id: str,
    video_path: Path,
    thumbnail_path: Path | None,
    captions_path: Path | None,
    topic: ApprovedTopic,
    raw_topic: dict[str, Any],
) -> dict[str, Any]:
    try:
        db = DatabaseManager(settings)
        db.initialize()
        with db.session_scope() as session:
            youtube = YouTubeConnectorService(
                session,
                settings,
                account_id=_youtube_oauth_account_id(settings, account_id),
            )
            status = youtube.get_status()
            if not status.ready:
                return {
                    "status": "connection_required",
                    "account_id": account_id,
                    "message": "YouTube Studio OAuth is not ready for private upload.",
                    "oauth": status.to_dict(),
                }
            result = youtube.upload_short(
                video_path=video_path,
                title=topic.title[:100],
                description=_description(raw_topic, topic),
                tags=[str(tag) for tag in raw_topic.get("tags", [])][:15],
                thumbnail_path=thumbnail_path if thumbnail_path and thumbnail_path.exists() else None,
                captions_path=captions_path,
                privacy_status="private",
            )
    except YouTubeQuotaExceededError as exc:
        return {"status": "quota_exceeded", "message": str(exc), "visibility": "private"}
    except YouTubeConnectorError as exc:
        return {"status": "connection_required", "message": str(exc), "visibility": "private"}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "visibility": "private"}

    video_id = str(result.get("video_id") or "").strip()
    return {
        "status": "uploaded_private",
        "youtube_video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        "visibility": "private",
        "upload": result,
    }


def _script_to_text(payload: dict[str, Any]) -> str:
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    if segments:
        return "\n\n".join(str(segment.get("text") or "").strip() for segment in segments if str(segment.get("text") or "").strip())
    parts = [payload.get("hook"), payload.get("opening_line"), *(payload.get("body") or []), payload.get("cta")]
    return "\n\n".join(str(part).strip() for part in parts if str(part or "").strip())


def _description(raw_topic: dict[str, Any], topic: ApprovedTopic) -> str:
    angle = str(raw_topic.get("angle") or topic.description or "").strip()
    tags = " ".join(f"#{str(tag).lstrip('#')}" for tag in raw_topic.get("tags", [])[:8])
    return f"{angle}\n\n{tags} #shorts".strip()[:5000]


def _youtube_oauth_account_id(settings: Settings, content_account_id: str) -> int | None:
    config = read_intelligence_account_config(settings, account_id=content_account_id) or {}
    raw = config.get("youtube_oauth_account_id")
    try:
        return int(raw) if raw is not None and str(raw).strip() else None
    except (TypeError, ValueError):
        return None


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_json(path: Path, payload: dict[str, Any], *, merge: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if merge and path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing.update(payload)
                payload = existing
        except (OSError, json.JSONDecodeError):
            pass
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
