"""Topic ProductionOrchestrator — LEGACY / SECONDARY for Wealth Blueprint.

Canonical Wealth Blueprint film path is Production Orders → produce_edit
(editorial default; remotion explicit). This orchestrator remains for topic jobs
and must not be extended as a competing WB Remotion orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections.abc import Callable, Awaitable
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.memory.obsidian_writer import write_production_log
from atnos.production.cost_guard import CostGuard
from atnos.production.faceless_playbook import build_faceless_playbook
from atnos.production.models import ApprovedTopic, ProductionContext, StepResult
from atnos.production.operating_standards import build_operating_standards
from atnos.production.project_manifest import MANIFEST_FILENAME, persist_context_manifest
from atnos.production.scene_checkpoint import mark_scene_checkpoint_interrupted
from atnos.production.steps import assembler, hook_generator, quality_gate, script_generator
from atnos.production.steps import asset_planner, thumbnail_generator, upload_package
from atnos.production.steps import video_generator, voice_synthesis
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
from atnos.services.f5_tts_runtime_service import F5TTSRuntimeService
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
from atnos.services.hardware_guard_service import HardwareGuardService
from atnos.services.slack_webhook_notifier import SlackWebhookNotifier

EventSink = Callable[[dict[str, Any]], None | Awaitable[None]]
StepRunner = Callable[[Settings, ProductionContext], Awaitable[StepResult]]
logger = logging.getLogger(__name__)
LEGACY_SECONDARY_ORCHESTRATOR = True

PRODUCTION_STEP_TIMEOUTS_S: dict[str, float] = {
    "hook": 90.0,
    "script": 90.0,
    "voice": 420.0,
    "visuals": 90.0,
    # Quality mode renders and vision-checks multiple candidates per ImageScene.
    # On the local AMD runtime one scene can take about three minutes, so the
    # former ten-minute budget aborted valid six-to-eight scene jobs midway.
    "video": 1800.0,
    # The signed-browser 1080x1920 renderer has its own 900 s process timeout.
    # Keep the orchestration budget above it so wait_for never starts a second
    # non-cancellable renderer thread while the first process is still valid.
    "assembly": 1020.0,
    "thumbnail": 240.0,
    "qa": 240.0,
    "upload_package": 60.0,
}
PRODUCTION_STEP_HEARTBEAT_INTERVAL_S = 15.0


class ProductionOrchestrator:
    """Runs an approved topic through the local production readiness pipeline."""

    def __init__(self, settings: Settings, *, event_sink: EventSink | None = None, max_retries: int = 3) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self.max_retries = max(1, max_retries)

    async def run_order(self, job_id: str) -> dict[str, Any]:
        """Resume a profile-driven order through the same production component stack."""
        from atnos.production.order_execution import run_order

        return await run_order(self.settings, job_id)

    async def start(self, *, account_id: str, topic_id: str) -> dict[str, Any]:
        context = await self.prepare(account_id=account_id, topic_id=topic_id)
        try:
            return await self.run_job(context)
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self._update_job,
                context.job_id,
                "cancelled",
                "cancelled",
                "production job cancelled before completion",
                None,
                None,
            )
            await asyncio.to_thread(self._restore_topic_for_retry, context.topic.id)
            await asyncio.to_thread(
                persist_context_manifest,
                context,
                current_step="cancelled",
                status="cancelled",
            )
            await self._emit(
                context.job_id,
                "cancelled",
                "cancelled",
                0.0,
                "production job cancelled; topic restored for retry",
            )
            raise

    async def prepare(self, *, account_id: str, topic_id: str) -> ProductionContext:
        topic = await asyncio.to_thread(self._load_approved_topic, account_id, topic_id)
        job_id = f"prod-{uuid4().hex[:12]}"
        await asyncio.to_thread(
            CostGuard(self.settings).reserve_production_job,
            account_id=account_id,
            topic_id=topic_id,
            job_id=job_id,
        )
        artifact_dir = self.settings.data_dir / "production" / _safe_path_segment(account_id) / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._create_job, job_id, topic, artifact_dir)
        operating_standards = build_operating_standards(self.settings, project_id=account_id)
        provisional_context = ProductionContext(
            job_id=job_id,
            account_id=account_id,
            topic=topic,
            artifact_dir=artifact_dir,
            artifacts={"operating_standards": operating_standards},
        )
        faceless_playbook = build_faceless_playbook(self.settings, provisional_context)
        context = ProductionContext(
            job_id=job_id,
            account_id=account_id,
            topic=topic,
            artifact_dir=artifact_dir,
            artifacts={"operating_standards": operating_standards, "faceless_playbook": faceless_playbook},
        )
        await asyncio.to_thread(self._write_operating_standards, context)
        await asyncio.to_thread(self._write_faceless_playbook, context)
        await asyncio.to_thread(
            persist_context_manifest,
            context,
            current_step="queued",
            status="queued",
        )
        await self._emit(job_id, "queued", "queued", 0.0, "production job queued")
        return context

    async def run_job(self, context: ProductionContext) -> dict[str, Any]:
        await asyncio.to_thread(self._update_job, context.job_id, "running", "hook", None, None, None)
        await asyncio.to_thread(
            persist_context_manifest,
            context,
            current_step="hook",
            status="running",
        )
        steps: list[tuple[str, str, StepRunner]] = [
            ("hook", "hook", hook_generator.run),
            ("script", "script", script_generator.run),
            ("voice", "voice", voice_synthesis.run),
            ("visuals", "visuals", asset_planner.run),
            ("video", "video", video_generator.run),
            ("assembly", "assembly", assembler.run),
            ("thumbnail", "thumbnail", thumbnail_generator.run),
            ("qa", "qa", quality_gate.run),
            ("upload_package", "upload_package", upload_package.run),
        ]
        for index, (step_name, artifact_key, runner) in enumerate(steps, start=1):
            progress_base = (index - 1) / len(steps)
            try:
                result = await self._run_step_with_runtime_guard(context, step_name, runner, progress_base)
            except Exception as exc:
                message = str(exc)
                await asyncio.to_thread(self._update_job, context.job_id, "error", step_name, message, None, None)
                await asyncio.to_thread(self._restore_topic_for_retry, context.topic.id)
                if step_name == "video":
                    await asyncio.to_thread(
                        mark_scene_checkpoint_interrupted,
                        context.artifact_dir / "video" / "scene_checkpoint.json",
                        message,
                    )
                try:
                    await asyncio.to_thread(
                        persist_context_manifest,
                        context,
                        current_step=step_name,
                        status="error",
                        error=message,
                    )
                except Exception:
                    logger.exception("Failed to persist the production manifest after step %s failed.", step_name)
                await self._emit(context.job_id, step_name, "error", progress_base, message)
                return self._job_payload(context.job_id)
            context.artifacts[artifact_key] = result.payload
            await asyncio.to_thread(
                self._persist_artifact_payload,
                context.job_id,
                artifact_key,
                result.payload,
            )
            if artifact_key == "qa" and self._qa_needs_visual_repair(result.payload, context):
                result = await self._repair_visual_quality(context, result.payload)
                context.artifacts[artifact_key] = result.payload
                await asyncio.to_thread(
                    self._persist_artifact_payload,
                    context.job_id,
                    artifact_key,
                    result.payload,
                )
            await asyncio.to_thread(
                self._update_job,
                context.job_id,
                "running",
                step_name,
                None,
                str(context.artifact_dir / "qa_report.json") if artifact_key == "qa" else None,
                str(context.artifact_dir / "youtube_private_upload_package.json")
                if artifact_key == "upload_package"
                else None,
            )
            await asyncio.to_thread(
                persist_context_manifest,
                context,
                current_step=step_name,
                status="running",
            )

        final_status = "qa_passed" if context.artifacts.get("qa", {}).get("auto_uploadable") else "qa_failed"
        context.artifacts["final_status"] = final_status
        if final_status == "qa_failed":
            await asyncio.to_thread(self._restore_topic_for_retry, context.topic.id)
        await asyncio.to_thread(
            self._update_job,
            context.job_id,
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
        await self._emit(
            context.job_id,
            "done",
            final_status,
            1.0,
            (
                "QA passed; private upload package is ready for human review"
                if final_status == "qa_passed"
                else "QA failed; review package is ready but private upload is blocked"
            ),
        )
        await asyncio.to_thread(write_production_log, self.settings, context)
        return self._job_payload(context.job_id)

    async def _prepare_gpu_runtime_for_step(self, context: ProductionContext, step_name: str) -> None:
        if self.settings.app_env == "test":
            return
        if step_name in {"voice", "video", "thumbnail"}:
            result = await asyncio.to_thread(
                HardwareGuardService(None, self.settings).release_gpu_pressure  # type: ignore[arg-type]
            )
            self._record_runtime_transition(context, step_name, "ollama_release_before_gpu_step", result)
            if result.get("status") in {"failed", "partial", "deferred"} and not bool(result.get("verified_released")):
                raise RuntimeError(
                    "GPU step is blocked because Ollama did not release resident model memory. "
                    f"{result.get('message') or result.get('status')}"
                )
        if step_name == "voice" and str(self.settings.shorts_tts_backend).strip().lower() == "f5_tts_http":
            result = await asyncio.to_thread(
                ComfyUiRuntimeService(self.settings).stop_server,
                require_idle=True,
                wait_timeout_s=20,
            )
            self._record_runtime_transition(context, step_name, "comfyui_stop_before_f5", result)
            if not bool(result.get("stopped")):
                raise RuntimeError(
                    "F5-TTS voice step is blocked because ComfyUI still has active or pending work."
                )
        if step_name in {"video", "thumbnail"}:
            status = await asyncio.to_thread(F5TTSRuntimeService(self.settings).stop, wait_timeout_s=20)
            result = status.to_dict()
            self._record_runtime_transition(context, step_name, "f5_stop_before_comfyui", result)
            if bool(result.get("endpoint_ready")):
                raise RuntimeError("ComfyUI step is blocked because F5-TTS did not release the GPU.")

    async def _release_gpu_runtime_after_step(self, context: ProductionContext, step_name: str) -> None:
        if self.settings.app_env == "test":
            return
        if step_name == "thumbnail":
            result = await asyncio.to_thread(
                ComfyUiRuntimeService(self.settings).stop_server,
                require_idle=True,
                wait_timeout_s=20,
            )
            self._record_runtime_transition(context, step_name, "comfyui_stop_before_semantic_qa", result)
            if not bool(result.get("stopped")):
                raise RuntimeError(
                    "Semantic visual QA is blocked because ComfyUI did not release the GPU after thumbnail generation."
                )
            return
        if step_name != "voice":
            return
        status = await asyncio.to_thread(F5TTSRuntimeService(self.settings).stop, wait_timeout_s=20)
        result = status.to_dict()
        self._record_runtime_transition(context, step_name, "f5_stop_after_voice", result)
        if bool(result.get("endpoint_ready")):
            raise RuntimeError("F5-TTS did not stop after voice generation; video generation remains blocked.")

    async def _run_step_with_runtime_guard(
        self,
        context: ProductionContext,
        step_name: str,
        runner: StepRunner,
        progress_base: float,
    ) -> StepResult:
        if self.settings.app_env == "test" or step_name not in {"voice", "video", "thumbnail"}:
            await self._prepare_gpu_runtime_for_step(context, step_name)
            try:
                return await self._run_step_with_retries(context, step_name, runner, progress_base)
            finally:
                await self._release_gpu_runtime_after_step(context, step_name)

        lock_service = GpuRuntimeLockService(self.settings)
        lease = await asyncio.to_thread(
            lock_service.acquire,
            owner=f"production:{context.job_id}:{step_name}",
            timeout_s=600.0,
        )
        try:
            await self._prepare_gpu_runtime_for_step(context, step_name)
            try:
                return await self._run_step_with_retries(context, step_name, runner, progress_base)
            finally:
                await self._release_gpu_runtime_after_step(context, step_name)
        finally:
            await asyncio.to_thread(lock_service.release, lease)

    @staticmethod
    def _record_runtime_transition(
        context: ProductionContext,
        step_name: str,
        action: str,
        result: dict[str, Any],
    ) -> None:
        transitions = context.artifacts.setdefault("runtime_transitions", [])
        if not isinstance(transitions, list):
            transitions = []
            context.artifacts["runtime_transitions"] = transitions
        transitions.append(
            {
                "step": step_name,
                "action": action,
                "result": result,
                "recorded_at": utc_now_iso(),
            }
        )
        path = context.artifact_dir / "runtime_transitions.json"
        path.write_text(json.dumps(transitions, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _run_step_with_retries(
        self,
        context: ProductionContext,
        step_name: str,
        runner: StepRunner,
        progress_base: float,
    ) -> StepResult:
        last_error: Exception | None = None
        attempts_run = 0
        for attempt in range(1, self.max_retries + 1):
            attempts_run = attempt
            await asyncio.to_thread(
                self._upsert_step,
                context.job_id,
                step_name,
                "running",
                progress_base,
                attempt,
                None,
            )
            await asyncio.to_thread(self._touch_running_step, context.job_id, step_name)
            await self._emit(context.job_id, step_name, "running", progress_base, f"attempt {attempt}")
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                self._heartbeat_running_step(context.job_id, step_name, heartbeat_stop)
            )
            try:
                timeout_s = self._step_timeout_s(context, step_name)
                result = await asyncio.wait_for(
                    runner(self.settings, context),
                    timeout=timeout_s,
                )
            except TimeoutError as exc:
                heartbeat_stop.set()
                await heartbeat_task
                last_error = RuntimeError(
                    f"{step_name} exceeded {self._step_timeout_s(context, step_name):.1f}s timeout"
                )
                message = str(last_error)
                await asyncio.to_thread(
                    self._upsert_step,
                    context.job_id,
                    step_name,
                    "error",
                    progress_base,
                    attempt,
                    message,
                )
                if attempt < self.max_retries and self._step_error_is_retryable(step_name, message):
                    await self._emit(
                        context.job_id,
                        step_name,
                        "retrying",
                        progress_base,
                        f"attempt {attempt} timed out; retrying: {message}",
                    )
                    await asyncio.sleep(0.2 * attempt)
                    continue
                break
            except Exception as exc:
                heartbeat_stop.set()
                await heartbeat_task
                last_error = exc
                message = str(exc)
                await asyncio.to_thread(
                    self._upsert_step,
                    context.job_id,
                    step_name,
                    "error",
                    progress_base,
                    attempt,
                    message,
                )
                if attempt < self.max_retries and self._step_error_is_retryable(step_name, message):
                    await self._emit(
                        context.job_id,
                        step_name,
                        "retrying",
                        progress_base,
                        f"attempt {attempt} failed; retrying: {message}",
                    )
                    await asyncio.sleep(0.2 * attempt)
                    continue
                break
            heartbeat_stop.set()
            await heartbeat_task
            await asyncio.to_thread(
                self._upsert_step,
                context.job_id,
                step_name,
                "done",
                min(progress_base + 0.14, 1.0),
                attempt,
                result.message,
            )
            await self._emit(context.job_id, step_name, "done", min(progress_base + 0.14, 1.0), result.message)
            return result
        raise RuntimeError(f"{step_name} failed after {attempts_run} attempts: {last_error}")

    @staticmethod
    def _step_error_is_retryable(step_name: str, message: str) -> bool:
        """Avoid retrying work that may still run in a non-cancellable worker thread."""
        normalized = str(message or "").casefold()
        if step_name == "voice" and "f5-tts" in normalized and "timed out" in normalized:
            return False
        if step_name == "assembly" and "exceeded" in normalized and "timeout" in normalized:
            return False
        return True

    async def _heartbeat_running_step(self, job_id: str, step_name: str, stop_event: asyncio.Event) -> None:
        interval = max(0.01, float(PRODUCTION_STEP_HEARTBEAT_INTERVAL_S))
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self._touch_running_step, job_id, step_name)
                except Exception:
                    logger.warning("Production heartbeat failed for job %s step %s.", job_id, step_name, exc_info=True)

    def _touch_running_step(self, job_id: str, step_name: str) -> None:
        self._update_job(job_id, "running", step_name, None, None, None)

    def _step_timeout_s(self, context: ProductionContext, step_name: str) -> float:
        base_timeout = float(PRODUCTION_STEP_TIMEOUTS_S.get(step_name, 180.0))
        if step_name == "voice":
            return max(base_timeout, self._planned_voice_segment_count(context) * 210.0 + 120.0)
        if step_name != "video":
            return base_timeout
        scene_count = self._planned_video_scene_count(context)
        per_clip_timeout = max(1200.0, float(getattr(self.settings, "comfyui_generation_timeout_s", 1200) or 1200))
        return max(base_timeout, scene_count * per_clip_timeout + 300.0)

    @staticmethod
    def _planned_voice_segment_count(context: ProductionContext) -> int:
        script = context.artifacts.get("script") if isinstance(context.artifacts, dict) else None
        segments = script.get("segments") if isinstance(script, dict) else None
        if not isinstance(segments, list) or not segments:
            return 1
        return max(1, len(segments))

    @staticmethod
    def _planned_video_scene_count(context: ProductionContext) -> int:
        visuals = context.artifacts.get("visuals") if isinstance(context.artifacts, dict) else None
        scenes = visuals.get("scenes") if isinstance(visuals, dict) else None
        if not isinstance(scenes, list) or not scenes:
            return 1
        return max(1, len(scenes))

    def _qa_needs_visual_repair(self, qa_payload: dict[str, Any], context: ProductionContext) -> bool:
        if bool(context.artifacts.get("visual_auto_repair_done")):
            return False
        if bool(qa_payload.get("auto_uploadable")):
            return False
        failures = {str(item) for item in (qa_payload.get("blocking_failures") or [])}
        repairable = {
            "Visual variety",
            "Scene concreteness",
            "Visual topic relevance",
            "Generated image topic match",
            "Semantic storyboard quality",
        }
        semantic_check = next(
            (
                check
                for check in (qa_payload.get("checks") or [])
                if isinstance(check, dict) and str(check.get("name") or "") == "Semantic storyboard quality"
            ),
            None,
        )
        semantic_value = semantic_check.get("value") if isinstance(semantic_check, dict) else None
        if (
            isinstance(semantic_value, dict)
            and str(semantic_value.get("status") or "") == "unavailable"
        ):
            failures.discard("Semantic storyboard quality")
        return bool(failures & repairable)

    async def _repair_visual_quality(self, context: ProductionContext, qa_payload: dict[str, Any]) -> StepResult:
        failures = [str(item) for item in (qa_payload.get("blocking_failures") or [])]
        context.artifacts["visual_auto_repair_done"] = True
        targeted_video = await asyncio.to_thread(
            video_generator.repair_generated_image_visual_floor,
            self.settings,
            context,
        )
        if targeted_video is not None:
            context.artifacts["video"] = targeted_video.payload
            await asyncio.to_thread(
                self._persist_artifact_payload,
                context.job_id,
                "video",
                targeted_video.payload,
            )
            context.artifacts["visual_repair_request"] = {
                "reason": "targeted_generated_image_visual_floor",
                "blocking_failures": failures,
                "segments": (targeted_video.payload.get("targeted_visual_repair") or {}).get("segments", []),
            }
            await self._emit(
                context.job_id,
                "visual_repair",
                "running",
                0.82,
                "QA found dark generated images; rebuilding only affected clips and final assembly.",
            )
            qa_result: StepResult | None = None
            for index, (step_name, artifact_key, runner) in enumerate(
                (
                    ("assembly", "assembly", assembler.run),
                    ("qa", "qa", quality_gate.run),
                ),
                start=1,
            ):
                result = await self._run_step_with_runtime_guard(
                    context,
                    step_name,
                    runner,
                    0.84 + (index - 1) * 0.05,
                )
                context.artifacts[artifact_key] = result.payload
                await asyncio.to_thread(
                    self._persist_artifact_payload,
                    context.job_id,
                    artifact_key,
                    result.payload,
                )
                if artifact_key == "qa":
                    qa_result = result
            context.artifacts.pop("visual_repair_request", None)
            final_payload = qa_result.payload if qa_result is not None else qa_payload
            await self._emit(
                context.job_id,
                "visual_repair",
                "done" if bool(final_payload.get("auto_uploadable")) else "warning",
                0.94,
                (
                    "Targeted visual repair passed QA."
                    if bool(final_payload.get("auto_uploadable"))
                    else "Targeted visual repair completed, but QA still blocks upload."
                ),
            )
            return qa_result if qa_result is not None else StepResult(
                artifact_name="qa",
                artifact_path=context.artifact_dir / "qa_report.json",
                message="QA unchanged after targeted visual repair.",
                payload=qa_payload,
            )
        context.artifacts["visual_repair_request"] = {
            "reason": "qa_visual_quality_failed",
            "blocking_failures": failures,
        }
        await self._emit(
            context.job_id,
            "visual_repair",
            "running",
            0.78,
            "QA found repairable visual failures; rebuilding visual plan with concrete scene fallback.",
        )
        repair_steps: list[tuple[str, str, StepRunner]] = [
            ("visuals", "visuals", asset_planner.run),
            ("video", "video", video_generator.run),
            ("assembly", "assembly", assembler.run),
            ("thumbnail", "thumbnail", thumbnail_generator.run),
            ("qa", "qa", quality_gate.run),
        ]
        qa_result: StepResult | None = None
        for index, (step_name, artifact_key, runner) in enumerate(repair_steps, start=1):
            result = await self._run_step_with_runtime_guard(context, step_name, runner, 0.78 + (index - 1) * 0.03)
            context.artifacts[artifact_key] = result.payload
            await asyncio.to_thread(self._persist_artifact_payload, context.job_id, artifact_key, result.payload)
            if artifact_key == "qa":
                qa_result = result
        context.artifacts.pop("visual_repair_request", None)
        final_auto_uploadable = bool((qa_result.payload if qa_result else qa_payload).get("auto_uploadable"))
        await self._emit(
            context.job_id,
            "visual_repair",
            "done" if final_auto_uploadable else "warning",
            0.94,
            (
                "Visual auto-repair passed QA."
                if final_auto_uploadable
                else "Visual auto-repair completed, but QA still blocks upload."
            ),
        )
        return qa_result if qa_result is not None else StepResult(
            artifact_name="qa",
            artifact_path=context.artifact_dir / "qa_report.json",
            message="QA unchanged after visual repair.",
            payload=qa_payload,
        )

    def _load_approved_topic(self, account_id: str, topic_id: str) -> ApprovedTopic:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT
                    t.id,
                    t.account_id,
                    t.title,
                    t.description,
                    t.total_score,
                    t.status,
                    a.content_profile
                FROM topic_queue t
                LEFT JOIN intelligence_accounts a ON a.account_id = t.account_id
                WHERE t.id = ? AND t.account_id = ?
                """,
                (topic_id, account_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Topic {topic_id} was not found for account {account_id}.")
            if row["status"] != "approved":
                raise ValueError(f"Topic {topic_id} must be approved before production.")
            return ApprovedTopic(
                id=str(row["id"]),
                account_id=str(row["account_id"]),
                title=str(row["title"]),
                description=str(row["description"] or ""),
                total_score=float(row["total_score"] or 0),
                content_profile=str(row["content_profile"] or ""),
            )

    def _create_job(self, job_id: str, topic: ApprovedTopic, artifact_dir: Path) -> None:
        now = utc_now_iso()
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO pipeline_jobs (
                    id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, topic.account_id, topic.id, "queued", "queued", str(artifact_dir), now, now),
            )
            db.execute(
                "UPDATE topic_queue SET status = ?, updated_at = ? WHERE id = ?",
                ("in_production", now, topic.id),
            )
            db.commit()

    def _restore_topic_for_retry(self, topic_id: str) -> None:
        """A failed render/QA run should not leave the approved topic stuck in production."""
        with connect(self.settings) as db:
            db.execute(
                """
                UPDATE topic_queue
                SET status = ?, updated_at = ?
                WHERE id = ? AND status = 'in_production'
                """,
                ("approved", utc_now_iso(), topic_id),
            )
            db.commit()

    def _write_operating_standards(self, context: ProductionContext) -> None:
        payload = context.artifacts.get("operating_standards")
        if not isinstance(payload, dict):
            payload = build_operating_standards(self.settings, project_id=context.account_id)
            context.artifacts["operating_standards"] = payload
        target = context.artifact_dir / "operating_standards.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _write_faceless_playbook(self, context: ProductionContext) -> None:
        payload = context.artifacts.get("faceless_playbook")
        if not isinstance(payload, dict):
            payload = build_faceless_playbook(self.settings, context)
            context.artifacts["faceless_playbook"] = payload
        target = context.artifact_dir / "faceless_playbook.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _persist_artifact_payload(self, job_id: str, artifact_key: str, payload: dict[str, Any]) -> None:
        mapping = {
            "hook": ("hooks", json_dumps(payload), "selected_hook_id", payload.get("selected_hook_id")),
            "script": ("script", json_dumps(payload), None, None),
            "voice": ("audio_timestamps", json_dumps(payload), None, None),
            "visuals": ("asset_plan", json_dumps(payload), None, None),
            "video": ("video_manifest", json_dumps(payload), None, None),
            "assembly": ("subtitle_path", str(payload.get("subtitle_file") or ""), None, None),
            "thumbnail": ("thumbnail_path", str(payload.get("thumbnail_path") or ""), None, None),
            "qa": ("qa_report", json_dumps(payload), None, None),
        }
        if artifact_key not in mapping:
            return
        column, value, extra_column, extra_value = mapping[artifact_key]
        assignments = [f"{column} = ?", "updated_at = ?"]
        params: list[Any] = [value, utc_now_iso()]
        if extra_column is not None:
            assignments.append(f"{extra_column} = ?")
            params.append(str(extra_value or ""))
        params.append(job_id)
        with connect(self.settings) as db:
            db.execute(f"UPDATE pipeline_jobs SET {', '.join(assignments)} WHERE id = ?", params)
            db.commit()

    def _update_job(
        self,
        job_id: str,
        status: str,
        current_step: str,
        error: str | None,
        qa_report_path: str | None,
        upload_package_path: str | None,
    ) -> None:
        assignments = ["status = ?", "current_step = ?", "updated_at = ?"]
        params: list[Any] = [status, current_step, utc_now_iso()]
        if error is not None:
            assignments.append("error = ?")
            params.append(error)
        if qa_report_path is not None:
            assignments.append("qa_report_path = ?")
            params.append(qa_report_path)
        if upload_package_path is not None:
            assignments.append("upload_package_path = ?")
            params.append(upload_package_path)
        params.append(job_id)
        with connect(self.settings) as db:
            db.execute(f"UPDATE pipeline_jobs SET {', '.join(assignments)} WHERE id = ?", params)
            db.commit()

    def _upsert_step(
        self,
        job_id: str,
        step: str,
        status: str,
        progress: float,
        attempt: int,
        message: str | None,
    ) -> None:
        now = utc_now_iso()
        completed_at = now if status in {"done", "error"} else None
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO pipeline_job_steps (
                    job_id, step, status, progress, attempt, message, started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, step) DO UPDATE SET
                    status=excluded.status,
                    progress=excluded.progress,
                    attempt=excluded.attempt,
                    message=excluded.message,
                    completed_at=excluded.completed_at
                """,
                (job_id, step, status, progress, attempt, message, now, completed_at),
            )
            db.commit()

    def _job_payload(self, job_id: str) -> dict[str, Any]:
        with connect(self.settings) as db:
            row = db.execute("SELECT * FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
            steps = db.execute(
                "SELECT step, status, progress, attempt, message FROM pipeline_job_steps WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        payload = dict(row) if row else {"id": job_id, "status": "unknown"}
        payload["steps"] = [dict(step) for step in steps]
        artifact_dir = Path(str(payload.get("artifact_dir") or ""))
        manifest_path = artifact_dir / MANIFEST_FILENAME if str(artifact_dir) not in {"", "."} else None
        payload["project_manifest_path"] = str(manifest_path) if manifest_path and manifest_path.is_file() else None
        return payload

    async def _emit(self, job_id: str, step: str, status: str, progress: float, message: str) -> None:
        event = {
            "type": "pipeline_step",
            "job_id": job_id,
            "step": step,
            "status": status,
            "progress": round(float(progress), 3),
            "message": message,
            "timestamp": utc_now_iso(),
        }
        log_path = self.settings.data_dir / "logs" / "production" / f"{job_id}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        if self.event_sink is None:
            await asyncio.to_thread(SlackWebhookNotifier(self.settings).notify_pipeline_event, event)
            return
        result = self.event_sink(event)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.to_thread(SlackWebhookNotifier(self.settings).notify_pipeline_event, event)


def _safe_path_segment(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip(".-_")
    return safe[:120] or "account"
