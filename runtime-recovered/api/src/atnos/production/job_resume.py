from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.orchestrator import EventSink, ProductionOrchestrator
from atnos.production.project_manifest import persist_context_manifest
from atnos.production.scene_checkpoint import mark_scene_checkpoint_interrupted
from atnos.production.steps import assembler, quality_gate, thumbnail_generator, upload_package, video_generator


logger = logging.getLogger(__name__)


async def resume_video_job(
    settings: Settings,
    job_id: str,
    *,
    event_sink: EventSink | None = None,
) -> dict[str, Any]:
    """Resume one job under an atomic cross-process execution lease."""
    context = await asyncio.to_thread(_load_video_resume_context, settings, job_id)
    lease = await asyncio.to_thread(_acquire_job_execution_lease, context.artifact_dir, job_id)
    try:
        return await _resume_video_job_locked(settings, job_id, event_sink=event_sink)
    finally:
        await asyncio.to_thread(_release_job_execution_lease, lease)


async def _resume_video_job_locked(
    settings: Settings,
    job_id: str,
    *,
    event_sink: EventSink | None = None,
) -> dict[str, Any]:
    """Wznów ten sam job od wideo, zachowując trwałe checkpointy ukończonych scen."""
    context = await asyncio.to_thread(_load_video_resume_context, settings, job_id)
    orchestrator = ProductionOrchestrator(settings, event_sink=event_sink)
    steps = (
        ("video", "video", video_generator.run),
        ("assembly", "assembly", assembler.run),
        ("thumbnail", "thumbnail", thumbnail_generator.run),
        ("qa", "qa", quality_gate.run),
        ("upload_package", "upload_package", upload_package.run),
    )
    await asyncio.to_thread(_mark_resume_running, settings, job_id)
    await asyncio.to_thread(
        persist_context_manifest,
        context,
        current_step="video",
        status="running",
    )
    await orchestrator._emit(job_id, "video", "running", 0.55, "resuming production from durable scene checkpoint")
    try:
        for index, (step_name, artifact_key, runner) in enumerate(steps, start=1):
            result = await orchestrator._run_step_with_runtime_guard(
                context,
                step_name,
                runner,
                0.55 + (index - 1) * 0.08,
            )
            context.artifacts[artifact_key] = result.payload
            await asyncio.to_thread(orchestrator._persist_artifact_payload, job_id, artifact_key, result.payload)
            if artifact_key == "qa" and orchestrator._qa_needs_visual_repair(result.payload, context):
                result = await orchestrator._repair_visual_quality(context, result.payload)
                context.artifacts[artifact_key] = result.payload
                await asyncio.to_thread(orchestrator._persist_artifact_payload, job_id, artifact_key, result.payload)
            await asyncio.to_thread(orchestrator._update_job, job_id, "running", step_name, None, None, None)
            await asyncio.to_thread(
                persist_context_manifest,
                context,
                current_step=step_name,
                status="running",
            )
    except Exception as exc:
        message = str(exc)
        await asyncio.to_thread(orchestrator._update_job, job_id, "error", "video", message, None, None)
        await asyncio.to_thread(orchestrator._restore_topic_for_retry, context.topic.id)
        await asyncio.to_thread(
            mark_scene_checkpoint_interrupted,
            context.artifact_dir / "video" / "scene_checkpoint.json",
            message,
        )
        await asyncio.to_thread(
            persist_context_manifest,
            context,
            current_step="video",
            status="error",
            error=message,
        )
        await asyncio.to_thread(_mark_resume_finished, settings, job_id, "resume_failed")
        await orchestrator._emit(job_id, "video", "error", 0.55, message)
        logger.exception("Production job %s failed while resuming from its video checkpoint.", job_id)
        return orchestrator._job_payload(job_id)

    final_status = "qa_passed" if context.artifacts.get("qa", {}).get("auto_uploadable") else "qa_failed"
    if final_status == "qa_failed":
        await asyncio.to_thread(orchestrator._restore_topic_for_retry, context.topic.id)
    await asyncio.to_thread(
        orchestrator._update_job,
        job_id,
        final_status,
        "upload_package",
        None,
        str(context.artifact_dir / "qa_report.json"),
        str(context.artifact_dir / "youtube_private_upload_package.json"),
    )
    await asyncio.to_thread(
        persist_context_manifest,
        context,
        current_step="upload_package",
        status=final_status,
    )
    await asyncio.to_thread(
        _mark_resume_finished,
        settings,
        job_id,
        "resume_completed" if final_status == "qa_passed" else "resume_qa_failed",
    )
    await orchestrator._emit(job_id, "done", final_status, 1.0, "resumed production job finished")
    return orchestrator._job_payload(job_id)


def _acquire_job_execution_lease(artifact_dir: Path, job_id: str) -> dict[str, Any]:
    """Acquire an atomic lease so the same production job cannot run twice."""
    lock_path = artifact_dir / ".production-execution.lock"
    token = uuid4().hex
    payload = {"job_id": job_id, "pid": os.getpid(), "token": token, "created_at": utc_now_iso()}
    for _ in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_json_file(lock_path)
            existing_pid = int(existing.get("pid") or 0)
            if existing_pid > 0 and _pid_is_alive(existing_pid):
                raise RuntimeError(
                    f"Production job {job_id} is already running in process {existing_pid}."
                )
            lock_path.unlink(missing_ok=True)
            continue
        try:
            os.write(descriptor, json.dumps(payload, sort_keys=True).encode("utf-8"))
        finally:
            os.close(descriptor)
        return {**payload, "path": str(lock_path)}
    raise RuntimeError(f"Could not acquire the production execution lease for {job_id}.")


def _release_job_execution_lease(lease: dict[str, Any]) -> None:
    lock_path = Path(str(lease.get("path") or ""))
    if not lock_path.is_file():
        return
    existing = _read_json_file(lock_path)
    if existing.get("token") == lease.get("token"):
        lock_path.unlink(missing_ok=True)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_video_resume_context(settings: Settings, job_id: str) -> ProductionContext:
    with connect(settings) as db:
        if db.execute("SELECT 1 FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone():
            raise ValueError("This production uses durable order checkpoints; video-only resume is not supported")
        job = db.execute("SELECT * FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"Production job {job_id} was not found.")
        topic = db.execute(
            """
            SELECT t.*, COALESCE(a.content_profile, '') AS resolved_content_profile
            FROM topic_queue t
            LEFT JOIN intelligence_accounts a ON a.account_id = t.account_id
            WHERE t.id = ?
            """,
            (job["topic_id"],),
        ).fetchone()
        if topic is None:
            raise ValueError(f"Topic {job['topic_id']} was not found.")

    artifact_dir = Path(str(job["artifact_dir"] or ""))
    if not artifact_dir.is_dir():
        raise RuntimeError(f"Production artifact directory is missing: {artifact_dir}")
    voice = _json_object(job["audio_timestamps"])
    narration_path = Path(str(voice.get("output_file") or ""))
    if not narration_path.is_file() or narration_path.stat().st_size <= 0:
        raise RuntimeError("The interrupted job has no reusable narration output.")
    visuals = _json_object(job["asset_plan"])
    if not isinstance(visuals.get("scenes"), list) or not visuals["scenes"]:
        raise RuntimeError("The interrupted job has no reusable visual scene plan.")

    return ProductionContext(
        job_id=job_id,
        account_id=str(job["account_id"]),
        topic=ApprovedTopic(
            id=str(topic["id"]),
            account_id=str(topic["account_id"]),
            title=str(topic["title"]),
            description=str(topic["description"] or ""),
            total_score=float(topic["total_score"] or 0.0),
            content_profile=str(topic["resolved_content_profile"] or ""),
        ),
        artifact_dir=artifact_dir,
        artifacts={
            "hook": _json_object(job["hooks"]),
            "script": _json_object(job["script"]),
            "voice": voice,
            "visuals": visuals,
            "operating_standards": _load_file(artifact_dir / "operating_standards.json"),
            "faceless_playbook": _load_file(artifact_dir / "faceless_playbook.json"),
        },
    )


def _mark_resume_running(settings: Settings, job_id: str) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            UPDATE pipeline_jobs
            SET status = 'running', current_step = 'video', error = NULL,
                review_status = 'resuming_checkpoint', updated_at = ?
            WHERE id = ?
            """,
            (now, job_id),
        )
        db.execute(
            """
            UPDATE pipeline_job_steps
            SET status = 'running', message = 'Resuming from durable scene checkpoint',
                completed_at = NULL
            WHERE job_id = ? AND step = 'video'
            """,
            (job_id,),
        )
        db.commit()


def _mark_resume_finished(settings: Settings, job_id: str, review_status: str) -> None:
    with connect(settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET review_status = ?, updated_at = ? WHERE id = ?",
            (review_status, utc_now_iso(), job_id),
        )
        db.commit()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_file(path: Path) -> dict[str, Any]:
    try:
        return _json_object(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
