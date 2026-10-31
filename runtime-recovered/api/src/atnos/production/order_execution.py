from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from atnos.core.logging import redact_secrets
from atnos.production.models import StepResult
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps import order_script
from atnos.production.steps.common import write_json
from atnos.production.steps.common import OllamaResourceUnavailable, RegisteredFieldRequirementsMissing
from atnos.production.narration_revision import NarrationRevisionRequired


def _runtime_code_identity():
    root=Path(__file__).parent
    paths=('order_execution.py','order_store.py','wb_image_review.py','wb_image_api.py','wb_local_flux_trial.py','asset_brain/catalog.py',
           'comfy_channels/prompts.py','steps/order_script.py','steps/common.py',
           'channels/wealth_blueprint/remotion_order_edit.py','remotion_quality_repair.py','model_runtime_startup.py','local_stage_recovery.py',
           'local_image_allowance.py','wb_flux_refinement.py','wb_review_evidence_repair.py','wb_flux_style_candidate.py')
    paths += ('studio_script_recovery.py','order_autonomy.py','chart_series.py','steps/order_assets.py',
              'channels/wealth_blueprint/deterministic.py',
              'channels/wealth_blueprint/scene_production_assets.py')
    paths += ('../core/config.py', 'scoped_script_revision.py', 'cover_repair.py', 'order_comfy_routing.py')
    paths += ('order_dispatch.py', '../api/routes/production.py', 'native_reserve_script_review.py')
    paths += ('local_image_dispatch_budget.py','comfy_channels/service.py','channels/wealth_blueprint/visual_story.py')
    paths += ('local_voice_lane.py', 'project_profile.py', 'channel_contract.py')
    paths += ('../services/comfyui_runtime_service.py', '../services/comfyui_image_generation_service.py')
    paths += ('voice_revision.py', 'steps/order_voice.py', 'steps/order_delivery.py', 'asset_observation.py',
              '../services/qwen_tts_local_runtime_service.py')
    from atnos.production.order_store import file_hash
    return content_hash({name:file_hash(root/name) for name in paths})


class StageExecutor:
    """Run concrete production steps with the job's durable budgets and output hashes."""

    def __init__(self, store: ProductionOrderStore, job_id: str, token: str):
        self.store, self.job_id, self.token = store, job_id, token
        self.context = store.context(job_id)
        self.runtime_code_identity=_runtime_code_identity()

    async def run(self, stage: str, inputs: dict, operation: Callable[[], Awaitable[StepResult]], *,
                  local_model_call=False, provider_call=False, estimated_cost_usd=0, external_tool_call=False) -> dict:
        if self.context.artifacts.get('production_order', {}).get('provider_policy') == 'strict_local' and (
                provider_call or external_tool_call or estimated_cost_usd):
            raise ProductionBlocked('STRICT_LOCAL: płatni lub zewnętrzni dostawcy są wyłączeni dla tego zlecenia. Nie wykonano operacji.')
        previous = next((s for s in self.store.get(self.job_id)["checkpoints"] if s["stage"] == stage), {})
        prior_error = json.loads(previous.get("error_json") or "{}")
        if prior_error.get('category') == 'registered_field_retry_paused':
            await asyncio.to_thread(self.store.block, self.job_id, self.token, stage,
                category='registered_field_retry_paused', message='The registered field writer exhausted its three attempts; no automatic replay.')
            raise ProductionBlocked(stage + ': registered_field_retry_paused')
        self.context.artifacts["model_retry_feedback"] = prior_error.get("message", "")
        dependency_failures = 0
        local_failures = 0
        while True:
            # A deployment may replace code while this worker is generating.
            # Finish the active operation, then hand off BEFORE starting another
            # request. Never terminate a model, render or other application's PID.
            if _runtime_code_identity()!=self.runtime_code_identity:
                from atnos.production.order_autonomy import policy
                autonomy=policy(self.store.settings,self.job_id)
                if autonomy and autonomy['enabled']:
                    await asyncio.to_thread(self.store.block,self.job_id,self.token,stage,
                        category='worker_interrupted',
                        message='Production code changed. Active operation finished; safe checkpoint handoff to a fresh worker, with counters and outputs preserved.')
                    raise ProductionBlocked('Safe worker update at a completed operation boundary')
            from atnos.production.studio_script_recovery import consume_operation
            try:
                await asyncio.to_thread(consume_operation, self, stage, inputs,
                    provider_call=provider_call, external_tool_call=external_tool_call,
                    estimated_cost_usd=estimated_cost_usd)
            except ProductionBlocked as exc:
                controlled = self.context.artifacts.get('studio_script_recovery', {}).get('operation_budget')
                await asyncio.to_thread(self.store.block, self.job_id, self.token, stage,
                    category='studio_script_operation_budget' if controlled else 'studio_script_action_limit', message=str(exc))
                raise
            voice_revision = self.context.artifacts.get('_renderer_refresh', {}).get('local_voice_revision')
            if voice_revision is not None:
                from atnos.production.voice_revision import guard_local_voice_revision
                try:
                    guard_local_voice_revision(self, stage, inputs, local_model_call=local_model_call,
                        provider_call=provider_call, external_tool_call=external_tool_call,
                        estimated_cost_usd=estimated_cost_usd)
                except ProductionBlocked as exc:
                    await asyncio.to_thread(self.store.block, self.job_id, self.token, stage,
                        category='local_voice_revision_scope', message=str(exc))
                    raise
            elif self.context.artifacts.get('_renderer_refresh') and not stage.startswith((
                    'remotion', 'covers', 'semantic', 'media_qa', 'package', 'edit', 'cover_quality')):
                # Presentation-only repairs must reuse upstream evidence, never
                # accidentally restart authoring, images, audio or paid work.
                row=next((r for r in self.store.get(self.job_id)['checkpoints'] if r['stage']==stage),None)
                if (not row or row['status']!='done' or row['input_hash']!=content_hash(inputs)
                        or not self.store._valid_files(self.context.artifact_dir,row['files_json'])):
                    raise ProductionBlocked('Renderer-only correction requires intact cached upstream stage: '+stage)
            if self.context.artifacts.get('_renderer_refresh') and (provider_call or external_tool_call or estimated_cost_usd):
                raise ProductionBlocked('Renderer-only correction prohibits paid or external relays')
            cached = await asyncio.to_thread(
                self.store.begin, self.job_id, self.token, stage, inputs,
                local_model_call=local_model_call, provider_call=provider_call,
                estimated_cost_usd=estimated_cost_usd,
                external_tool_call=external_tool_call,
            )
            if cached is not None:
                return cached
            try:
                result = await operation()
                await asyncio.to_thread(self.store.complete, self.job_id, self.token, stage,
                                        result.payload, [result.artifact_path, *result.evidence_paths])
                return result.payload
            except NarrationRevisionRequired as exc:
                await asyncio.to_thread(self.store.fail, self.job_id, self.token, stage,
                    category="script_revision_required", message=json.dumps(exc.report))
                self.context.artifacts["script_revision_request"] = exc.report
                raise
            except (OrderConflict, ProductionBlocked):
                raise
            except Exception as exc:
                from atnos.production.local_stage_recovery import local_ffmpeg_timeout_message
                local_timeout = local_ffmpeg_timeout_message(exc, stage, self.context.artifact_dir)
                if local_timeout is not None:
                    await asyncio.to_thread(self.store.fail, self.job_id, self.token, stage,
                        category='local_tool_timeout', message=local_timeout)
                    raise ProductionBlocked(f'{stage}: local_tool_timeout') from exc
                # Model stages may regenerate output using model_retry_feedback.
                # Repeating a local validation/assembly operation with the same
                # inputs cannot repair those inputs and only burns its budget.
                invalid_output = isinstance(exc, ValueError) and (local_model_call or provider_call)
                deterministic_failure = (
                    "Cannot allocate a complete narration" in str(exc)
                    or "Cannot allocate complete thoughts" in str(exc)
                    or "script_repair deterministic" in str(exc)
                    or "Repair still has" in str(exc)
                    or "supplied calculation IDs" in str(exc)
                    or "must not invent numbers" in str(exc)
                )
                retryable = (
                    not deterministic_failure
                    and (invalid_output or isinstance(exc, (httpx.TransportError, TimeoutError)))
                    and not isinstance(exc, httpx.ConnectError)
                )
                if isinstance(exc, httpx.HTTPStatusError):
                    retryable = exc.response.status_code in {429, 500, 502, 503, 504}
                category = ("invalid_output" if invalid_output else "invalid_input") if isinstance(exc, ValueError) else "dependency_failure"
                if isinstance(exc, OllamaResourceUnavailable):
                    category = 'resource_unavailable'
                    retryable = False
                from atnos.production.order_store import local_operation_unlimited
                if retryable and (isinstance(exc, RegisteredFieldRequirementsMissing) or local_operation_unlimited(self.store.settings,
                        provider_call=provider_call, external_tool_call=external_tool_call,
                        estimated_cost_usd=estimated_cost_usd)):
                    local_failures += 1
                    if isinstance(exc, RegisteredFieldRequirementsMissing):
                        current = next(row for row in self.store.get(self.job_id)['checkpoints'] if row['stage'] == stage)
                        local_failures = max(local_failures, current['attempts'])
                    if local_failures >= 3:
                        retryable = False
                        category = ('registered_field_retry_paused' if isinstance(exc, RegisteredFieldRequirementsMissing)
                                    else 'local_retry_paused')
                if retryable and not invalid_output:
                    dependency_failures += 1
                    if dependency_failures >= 3:
                        retryable = False
                        category = 'dependency_circuit_open'
                await asyncio.to_thread(self.store.fail, self.job_id, self.token, stage,
                                        category=category, message=str(exc))
                self.context.artifacts["model_retry_feedback"] = redact_secrets(str(exc))[:1500]
                if not retryable:
                    raise ProductionBlocked(f"{stage}: {category}") from exc
                # All retries re-enter begin(), including after a process restart.
                delay = .25
                if not invalid_output:
                    delay = min(30.0, 2.0 ** dependency_failures)
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            delay = min(30.0, max(delay, float(exc.response.headers.get('Retry-After', '0'))))
                        except ValueError:
                            pass
                await asyncio.sleep(delay)


async def produce_script(executor: StageExecutor) -> dict:
    try:
        return await _produce_script(executor)
    finally:
        executor.context.artifacts.pop("_active_script_model_lane", None)
        executor.context.artifacts.pop("_active_script_review_model_lane", None)


async def _produce_script(executor: StageExecutor) -> dict:
    from atnos.production.studio_script_recovery import prepare_recovery_context, apply_recovery_guidance
    await asyncio.to_thread(prepare_recovery_context, executor)
    controlled = executor.context.artifacts.get('studio_script_recovery', {}).get('execution_scope') == 'script_only'
    context = executor.context
    settings = executor.store.settings
    config = context.artifacts["project_profile"]["production"]
    # An optional, explicitly registered trial affects only the standard WB
    # authoring path. The receipt is checked against the stopped order and the
    # installed local model before any trial writer call; frozen profile stays intact.
    from atnos.production.script_model_lane import load_script_model_lane
    lane = await asyncio.to_thread(load_script_model_lane, settings, context.job_id, verify_runtime=True)
    if lane is not None:
        if lane.get("execution_scope") != "normal_produce_script_authoring":
            raise ProductionBlocked("Registered script model lane is outside its supported execution scope.")
        context.artifacts["_active_script_model_lane"] = lane
    lane_checkpoint = ({"script_model_lane_digest": lane["digest"]} if lane is not None else {})
    from atnos.production.script_review_model_lane import load_script_review_model_lane
    review_lane = await asyncio.to_thread(load_script_review_model_lane, settings, context.job_id, verify_runtime=True)
    if review_lane is not None:
        if review_lane.get("execution_scope") != "normal_produce_script_review":
            raise ProductionBlocked("Registered script review model lane is outside its supported execution scope.")
        context.artifacts["_active_script_review_model_lane"] = review_lane
    review_lane_checkpoint = ({"script_review_model_lane_digest": review_lane["digest"]}
                              if review_lane is not None else {})
    # Scoped corrections are part of normal produce_script authoring as well.
    # Admit the immutable lanes BEFORE their early-return branch so they cannot
    # silently use the frozen default model instead of the registered writer.
    from atnos.production.scoped_script_revision import produce_requested
    scoped = None if controlled else await produce_requested(executor)
    if scoped is not None:
        return scoped
    apply_recovery_guidance(executor.context)
    from atnos.production.editorial_revision import load_accepted, produce_accepted
    accepted = None if controlled else load_accepted(executor.store, executor.job_id)
    if accepted:
        return await produce_accepted(executor, accepted)

    async def gather():
        evidence = await asyncio.to_thread(order_script.gather_script_context, settings, context)
        return write_json(context.artifact_dir / "script_context.json", evidence)

    # Recovery guidance changes on every assessment, but gathering reference
    # and knowledge does not use it. Bind this cache to the frozen profile and
    # order instead of consuming another context attempt for transient notes.
    frozen_profile = executor.store.get(context.job_id)['profile']
    evidence = await executor.run("script_context", {"profile": frozen_profile,
                                                    "request": context.artifacts["production_order"]}, gather)
    # Old frozen orders retain their original contract; new orders compile math before authoring.
    calculations = None
    from atnos.production.hypothetical_script_intake import is_hypothetical
    if is_hypothetical(context.artifacts["production_order"].get("intake")):
        from atnos.production.hypothetical_script_intake import produce_supplied_calculations
        calculations = await produce_supplied_calculations(executor)
    elif "example_currency" in config and not context.artifacts["production_order"].get("intake"):
        from atnos.production.calculation_plan import produce_calculations
        calculations = await produce_calculations(executor, evidence)
    draft_inputs = {"request": context.artifacts["production_order"], "profile": config, "evidence": evidence,
                    "contract_version": order_script.SCRIPT_PROMPT_VERSION, **lane_checkpoint}
    if calculations is not None:
        draft_inputs["calculations"] = calculations
    from atnos.production.studio_script_recovery import hypothetical_recovery_source, validate_hypothetical_repair
    raw = hypothetical_recovery_source(context)
    if raw is None:
        raw = await executor.run("script_draft", draft_inputs, lambda: order_script.run(settings, context, evidence), local_model_call=True)
    script = order_script.normalize_narration(order_script.EditorialScript.model_validate(raw))
    studio = context.artifacts.get('studio_script_recovery')
    if studio and studio['mode'] == 'hypothetical_review':
        # A registered rejection is an instruction to the writer, not merely
        # optional context for a second reviewer. Keep the exact source and
        # calculation receipt, repair the recorded targets, then review anew.
        # The precise rate-wording defect can already be resolved in the latest
        # source; never resurrect that stale finding after normalization.
        current = order_script.script_issues(script, context.artifacts['project_profile'],
            trusted_facts=order_script.reviewed_source_facts(context),
            request=context.artifacts['production_order'])
        current_wording_ids = {sid for issue in current
            if issue['code'] == 'hypothetical_assumption_wording' for sid in issue['segment_ids']}
        recorded = [{key: issue[key] for key in ('code', 'segment_ids', 'detail')}
            for issue in studio['issues']
            if issue['code'] != 'hypothetical_assumption_wording'
            or set(issue['segment_ids']).issubset(current_wording_ids)]
        if recorded and studio.get('origin') != 'autonomous_recovery':
            source = script

            async def repair_recorded():
                fixed = await order_script.repair(settings, context, source, recorded)
                validate_hypothetical_repair(context, fixed.model_dump(mode='json'))
                return write_json(context.artifact_dir / 'script' / 'studio-repaired-source.json',
                                  fixed.model_dump(mode='json'))

            repaired = await executor.run('script_repair/studio-' + studio['digest'][:12],
                {'source': source.model_dump(mode='json'), 'issues': recorded,
                 'studio_recovery_digest': studio['digest'],
                 'repair_prompt_version': order_script.SCRIPT_REPAIR_PROMPT_VERSION,
                 'calculations': calculations, **lane_checkpoint}, repair_recorded, local_model_call=True)
            script = order_script.EditorialScript.model_validate(repaired)
    max_revisions = min(config["max_attempts_per_element"], studio['max_rounds']) if studio else config["max_attempts_per_element"]
    rejected_fingerprints = set()
    for revision in range(max_revisions):
        async def review():
            verdict = await order_script.review(settings, context, script)
            if context.artifacts.get('scoped_script_guidance'):
                from atnos.production.scoped_script_revision import feedback_issues
                verdict['issues'] = [*verdict.get('issues', []), *feedback_issues(script, context.artifacts['scoped_script_guidance'])]
                verdict['status'] = 'failed' if verdict['issues'] else 'passed'
            verdict.update({"script_hash": content_hash(script.model_dump(mode="json")), "revision": revision})
            if studio:
                verdict.update(studio_recovery_digest=studio['digest'], human_review='not_performed')
                if studio.get('base_feedback_hash'):
                    verdict.update(feedback_hash=studio['base_feedback_hash'], feedback_checks='performed')
            return write_json(context.artifact_dir / "script" / f"review_{revision}.json", verdict)

        review_stage = f"script_review/{revision}"
        inputs = {"script": script.model_dump(mode="json"), "config": config, "request": draft_inputs["request"],
                  "validator_version": order_script.SCRIPT_VALIDATOR_VERSION, **review_lane_checkpoint}
        if calculations is not None:
            inputs["calculations"] = calculations
        if studio:
            inputs['studio_recovery_digest'] = studio['digest']
        verdict = await executor.run(review_stage, inputs, review,
                                     local_model_call=order_script.needs_semantic_review(order_script.script_issues(
                                         script, context.artifacts["project_profile"], trusted_facts=order_script.reviewed_source_facts(context), request=context.artifacts.get('production_order'))))
        if verdict["status"] == "passed":
            break
        rejected_fingerprints.add(content_hash(script.model_dump(mode="json")))
        if revision + 1 == max_revisions:
            await asyncio.to_thread(executor.store.fail, context.job_id, executor.token, review_stage,
                                    category="script_quality_limit", message=json.dumps(verdict["issues"]))
            raise ProductionBlocked("Script still fails validation after bounded selective repair")

        async def repair():
            fixed = await order_script.repair(settings, context, script, verdict["issues"])
            validate_hypothetical_repair(context, fixed.model_dump(mode="json"))
            return write_json(context.artifact_dir / "script" / f"revision_{revision + 1}.json", fixed.model_dump(mode="json"))

        fixed = await executor.run(f"script_repair/{revision + 1}",
            {**inputs, "issues": verdict["issues"], "repair_prompt_version": order_script.SCRIPT_REPAIR_PROMPT_VERSION,
             **lane_checkpoint},
            repair, local_model_call=True)
        repaired = order_script.EditorialScript.model_validate(fixed)
        before_fp = order_script.script_text_fingerprint(script)
        after_fp = order_script.script_text_fingerprint(repaired)
        if not studio and after_fp == before_fp and verdict["issues"] and all(
            item.get("code") == "narrative_rules_violation" for item in verdict["issues"]
        ):
            break
        if after_fp == before_fp:
            await asyncio.to_thread(
                executor.store.fail,
                context.job_id,
                executor.token,
                f"script_repair/{revision + 1}",
                category="script_repair_stalled",
                message="Repair returned the same script fingerprint. Stopping instead of retrying the same result.",
            )
            raise ProductionBlocked(
                "script_repair stalled: the retry fingerprint matches the script already rejected"
            )
        if content_hash(repaired.model_dump(mode="json")) in rejected_fingerprints:
            await asyncio.to_thread(
                executor.store.fail, context.job_id, executor.token,
                f"script_repair/{revision + 1}", category="script_repair_cycle",
                message="Repair returned an earlier rejected script fingerprint. Stopping the cycle with history and counters preserved.",
            )
            raise ProductionBlocked("script_repair cycle: an earlier rejected script returned")
        script = repaired

    from atnos.production.studio_script_recovery import hold_script_candidate
    verdict = await hold_script_candidate(executor, script.model_dump(mode='json'), verdict,
        {'writer': lane['model'] if lane else config['script_model']['generator'],
         'reviewer': review_lane['model'] if review_lane else config['script_model']['reviewer']})

    async def finalize():
        from atnos.production.studio_script_recovery import require_candidate_acceptance
        require_candidate_acceptance(executor.store, context.job_id, script=script.model_dump(mode='json'))
        words = sum(len(s.text.split()) for s in script.segments)
        practical = sum(len(s.text.split()) for s in script.segments if s.type == "practical")
        provenance = {"reference_source": evidence["reference_source"], "usage": evidence["usage"],
                      "generator": lane["model"] if lane is not None else config["script_model"]["generator"],
                      "reviewer": review_lane["model"] if review_lane is not None else config["script_model"]["reviewer"]}
        if lane is not None:
            provenance["frozen_generator"] = config["script_model"]["generator"]
            provenance["script_model_lane"] = {"receipt_digest": lane["digest"],
                "model_digest": lane["model_digest"], "execution_scope": lane["execution_scope"]}
        if review_lane is not None:
            provenance["frozen_reviewer"] = config["script_model"]["reviewer"]
            provenance["script_review_model_lane"] = {"receipt_digest": review_lane["digest"],
                "model_digest": review_lane["model_digest"], "execution_scope": review_lane["execution_scope"],
                "same_model_as_writer": bool(lane and lane["model_digest"] == review_lane["model_digest"])}
        payload = {**script.model_dump(mode="json"), "text": " ".join(s.text for s in script.segments),
                   "word_count": words, "qa": {**verdict, "practical_word_share": practical / words},
                   "provenance": provenance}
        if studio:
            payload['provenance']['studio_recovery'] = {
                'request_digest': studio['digest'], 'request_key': studio['request_key'],
                'base_feedback_hash': studio.get('base_feedback_hash'),
                'attempts_reset': False, 'budget_reset': False, 'user_appearance_approved': False}
        return write_json(context.artifact_dir / "script.json", payload)

    result = await executor.run("script", {"script": script.model_dump(mode="json"), "review": verdict,
                                             **lane_checkpoint, **review_lane_checkpoint}, finalize)
    await asyncio.to_thread(executor.store.reconcile_script_checkpoints, context.job_id, executor.token)
    await asyncio.to_thread(executor.store.persist_legacy_artifact, context.job_id, executor.token, "script", result)
    context.artifacts["script"] = result
    # The lane is scoped to this producer; later voice/media stages may reuse
    # the executor context but must continue using their frozen normal routing.
    context.artifacts.pop("_active_script_model_lane", None)
    context.artifacts.pop("_active_script_review_model_lane", None)
    return result


def _voice_preflight_settings(executor, config):
    from atnos.production.media_tools import resolve_ffmpeg
    from atnos.production.steps.order_voice import voice_settings
    from atnos.production.local_voice_lane import load_local_voice_lane, verify_runtime_availability
    from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService

    settings = voice_settings(executor.store.settings, config["voice"])
    if not resolve_ffmpeg() or not WhisperCppSubtitleService(settings).runtime_availability().available:
        raise ProductionBlocked("Preflight: FFmpeg or independent Whisper speech validation is unavailable")
    local_lane = load_local_voice_lane(executor.store.settings, executor.context.job_id)
    if getattr(executor.context, 'artifacts', {}).get('production_order', {}).get('voice_candidate') in {'Ryan-B', 'Ryan-neutral'} and local_lane is None:
        raise ProductionBlocked('Preflight: wybrany Ryan B nie ma prawidłowej rejestracji lokalnej; fallback jest wyłączony')
    if local_lane is not None:
        availability = verify_runtime_availability()
        if not availability.get("available"):
            raise ProductionBlocked("Preflight: approved local voice runtime or alignment is unavailable")
    elif not getattr(settings, "shorts_allow_cloud_tts", False):
        raise ProductionBlocked("Preflight: cloud TTS is disabled and this order has no authorized local voice")
    elif not settings.elevenlabs_api_key:
        raise ProductionBlocked("Preflight: ElevenLabs is not configured")
    return settings


def _preflight_full_media_runtime(executor, *, script_only: bool) -> None:
    """Fail before script/model/media work if the required WB renderer is unavailable."""
    if script_only or executor.context.artifacts.get("production_order", {}).get("project") != "wealth_blueprint":
        return
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import (
        RemotionRequiredError,
        _preflight_remotion,
    )

    try:
        evidence = _preflight_remotion(executor.store.settings)
    except RemotionRequiredError as exc:
        raise ProductionBlocked(f"Preflight: required Wealth Blueprint Remotion runtime: {exc}") from exc
    executor.context.artifacts["_early_remotion_preflight"] = evidence


def _record_production_readiness(executor, *, required_models: set[str] | None = None,
                                 model_status: str = "pending") -> dict | None:
    """Persist one E:-resident, order-scoped snapshot using the existing hardware guard."""
    context = executor.context
    order = context.artifacts.get("production_order", {})
    if order.get("project") != "wealth_blueprint":
        return None
    settings = executor.store.settings
    artifact_dir = Path(context.artifact_dir).resolve()
    app_root = Path(settings.app_root).resolve()
    if artifact_dir.drive.casefold() != "e:" or app_root.drive.casefold() != "e:":
        raise ProductionBlocked("Preflight: production readiness evidence and application data must remain on E:")
    from atnos.services.hardware_guard_service import HardwareGuardService

    payload = context.artifacts.get("_production_readiness_payload")
    if not isinstance(payload, dict) or payload.get("job_id") != context.job_id:
        try:
            snapshot = HardwareGuardService(None, settings).capture(persist=False).to_dict()
        except Exception as exc:  # diagnostics must identify unavailable measurements, not invent zero
            snapshot = {
                "free_ram_mb": None, "total_ram_mb": None, "free_vram_mb": None,
                "total_vram_mb": None, "free_disk_gb": None, "disk_path": str(app_root),
                "status": "unavailable", "notes": f"{type(exc).__name__}: {exc}"[:300],
            }
        missing_measurements = [name for name in ("free_ram_mb", "free_vram_mb", "free_disk_gb")
                                if snapshot.get(name) is None]
        payload = {
            "schema_version": 1,
            "job_id": context.job_id,
            "stage": "preflight",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "resource_snapshot": snapshot,
            "missing_measurements": missing_measurements,
            "required_models": [],
            "model_status": "pending",
            "readiness_status": "degraded_capacity_requirements_not_profiled",
            "renderer": context.artifacts.get("_early_remotion_preflight"),
            "capacity_decision": "unknown_stage_requirements_not_fully_profiled",
        }
    payload = {
        **payload,
        "required_models": sorted(required_models or set()),
        "model_status": model_status,
        "readiness_status": "degraded_capacity_requirements_not_profiled",
        "renderer": context.artifacts.get("_early_remotion_preflight"),
        "input_hash": content_hash({
            "job_id": context.job_id,
            "order": order,
            "project_profile": context.artifacts.get("project_profile", {}),
            "required_models": sorted(required_models or set()),
        }),
        "attempt": 0,
        "provider_call_id": None,
        "result_status": model_status,
    }
    target = artifact_dir / "diagnostics" / "runtime-readiness.json"
    write_json(target, payload)
    evidence = {"path": str(target), "sha256": file_hash(target), "status": payload["readiness_status"],
                "model_status": model_status,
                "missing_measurements": payload["missing_measurements"]}
    context.artifacts["_production_readiness"] = evidence
    context.artifacts["_production_readiness_payload"] = payload
    return evidence


async def preflight_order(executor: StageExecutor, *, allow_model_start=True, script_only=False):
    from atnos.production.project_profile import VisualQAProfile

    config = executor.context.artifacts["project_profile"]["production"]
    from atnos.production.channel_contract import ChannelContractError, validate_frozen
    project = executor.context.artifacts["production_order"].get("project", "")
    try:
        contract = validate_frozen(executor.store.settings, project, executor.context.artifacts["project_profile"])
    except ChannelContractError as exc:
        raise ProductionBlocked(f"Preflight: channel contract: {exc}") from exc
    settings = executor.store.settings if script_only else _voice_preflight_settings(executor, config)
    # WB is REMOTION_REQUIRED. Check browser, fonts/render dependencies and
    # FFmpeg before running the script writer or spending any image/audio work.
    _preflight_full_media_runtime(executor, script_only=script_only)
    readiness = None if script_only else _record_production_readiness(executor)
    # Registered lanes are the actual authoring/review configuration, not just
    # provenance. Their loaders validate the immutable receipt and the pinned
    # model on their dedicated endpoint without starting inference. Requiring
    # an overridden frozen model on the default endpoint rejects a valid lane.
    from atnos.production.script_model_lane import load_script_model_lane
    from atnos.production.script_review_model_lane import load_script_review_model_lane
    writer_lane = await asyncio.to_thread(load_script_model_lane, settings, executor.context.job_id,
                                         verify_runtime=True)
    reviewer_lane = await asyncio.to_thread(load_script_review_model_lane, settings, executor.context.job_id,
                                           verify_runtime=True)
    required = set()
    if writer_lane is None:
        required.add(config["script_model"]["generator"])
    if reviewer_lane is None:
        required.add(config["script_model"]["reviewer"])
    if not script_only:
        required.update({config["assets"]["embedding_model"], VisualQAProfile.model_validate(config.get("visual_qa", {})).model})
    if contract and not script_only:
        for role, item in (contract.get("ollama_models") or {}).items():
            if not isinstance(item, dict) or not item.get("required"):
                continue
            model = str(item.get("model") or "").strip()
            # The contract's shared author/reviewer role is replaced only by
            # BOTH verified lanes. Other required roles (including a fallback)
            # remain requirements even if they happen to name the old model.
            if (role == "script_and_editorial" and writer_lane is not None
                    and reviewer_lane is not None
                    and model == config["script_model"]["generator"]
                    and model == config["script_model"]["reviewer"]):
                continue
            required.add(model)
        required.discard("")
    if required:
        if readiness is not None:
            readiness = _record_production_readiness(
                executor, required_models=required, model_status="checking")
        async with httpx.AsyncClient(timeout=10) as client:
            from atnos.production.model_runtime_startup import ensure_model_endpoint
            response = await ensure_model_endpoint(executor, client, allow_start=allow_model_start)
        installed = {entry["name"] for entry in response.json().get("models", [])}
        missing = {name for name in required if name not in installed and f"{name}:latest" not in installed}
        if missing:
            if readiness is not None:
                _record_production_readiness(
                    executor, required_models=required, model_status="missing:" + ",".join(sorted(missing)))
            raise ProductionBlocked("Preflight: missing local models: " + ", ".join(sorted(missing)))
    if readiness is not None:
        _record_production_readiness(
            executor, required_models=required, model_status="verified" if required else "not_required")
    paths = [config["reference_profile"]] + ([] if script_only else config["assets"]["manifests"])
    for value in paths:
        path = (settings.data_dir / value).resolve()
        if not path.is_relative_to(settings.data_dir.resolve()) or not path.is_file() or not path.stat().st_size:
            raise ProductionBlocked("Preflight: a configured reference/asset manifest is missing or outside data storage")


async def run_order(settings, job_id: str, *, script_only: bool = False):
    """Execute/resume an existing production, without owning it in a web request."""
    from pathlib import Path

    from atnos.production.steps.order_assets import produce_assets
    from atnos.production.steps.order_delivery import produce_covers, produce_package
    from atnos.production.steps.order_media import produce_captions, produce_edit
    from atnos.production.steps.order_repair import produce_final_quality
    from atnos.production.steps.order_voice import produce_voice
    from atnos.production.wb_image_api import load_image_lane, produce_image_candidates
    from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

    store = ProductionOrderStore(settings)
    before = store.get(job_id)
    from atnos.production.studio_script_recovery import repair_execution_scope
    controlled = repair_execution_scope(store, job_id) == 'script_only'
    script_only = script_only or controlled
    if (before["request"].get("intake") is not None and not before["can_resume"]
            and not script_only and not store.is_dispatched_worker(job_id)):
        return before
    if before["status"] in {"ready", "script_ready"}:
        return before
    if not ((before["can_prepare_script"] or store.is_dispatched_worker(job_id)) if script_only else (before["can_resume"] or store.is_dispatched_worker(job_id))):
        raise ProductionBlocked("This order cannot resume: a worker is live or a persistent limit was reached")
    with store.lease(job_id, script_only=script_only) as token:
        executor = StageExecutor(store, job_id, token)
        from atnos.production.remotion_quality_repair import load_renderer_refresh
        refresh=await asyncio.to_thread(load_renderer_refresh,executor)
        if refresh:executor.context.artifacts['_renderer_refresh']=refresh
        lock, gpu_lease, phase = GpuRuntimeLockService(settings), None, "preflight"
        try:
            await asyncio.to_thread(store.set_phase, job_id, token, phase)
            if before['request'].get('brand_kit'):
                if script_only:
                    raise ProductionBlocked('Brand trials use their own bounded script and asset checkpoints.')
                from atnos.production.branded_production import produce_branded
                phase = 'branded_production'
                await asyncio.to_thread(store.set_phase, job_id, token, phase)
                return await produce_branded(executor)
            if before['request'].get('production_scope') == 'script_preview':
                from atnos.production.fiction_production import produce_script as produce_story
                config = executor.context.artifacts['project_profile']['production']
                required = {config['script_model']['generator'], config['script_model']['reviewer']}
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
                    response.raise_for_status()
                installed = {entry['name'] for entry in response.json().get('models', [])}
                if any(name not in installed and name + ':latest' not in installed for name in required):
                    raise ProductionBlocked('Brakuje lokalnego modelu do scenariusza lub recenzji opowieści.')
                phase = 'waiting_for_runtime'
                await asyncio.to_thread(store.set_phase, job_id, token, phase)
                gpu_lease = await asyncio.to_thread(lock.acquire, owner=f'production-order:{job_id}', timeout_s=600)
                from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
                await asyncio.to_thread(ProductionGpuResidencyService(settings).prepare_for_llm)
                phase = 'scripting'
                await asyncio.to_thread(store.set_phase, job_id, token, phase)
                await produce_story(executor)
                return await asyncio.to_thread(store.finish_script_preview, job_id, token)
            # Read-only availability checks do not consume production retries or provider budget.
            if script_only:
                await preflight_order(executor, script_only=True)
            elif refresh and refresh.get('local_voice_revision') is not None:
                await preflight_order(executor, allow_model_start=False)
            else:
                await preflight_order(executor)
            phase = "waiting_for_runtime"
            await asyncio.to_thread(store.set_phase, job_id, token, phase)
            gpu_lease = await asyncio.to_thread(lock.acquire, owner=f"production-order:{job_id}", timeout_s=600)
            from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
            await asyncio.to_thread(ProductionGpuResidencyService(settings).prepare_for_llm)
            if script_only:
                phase = "scripting"
                await asyncio.to_thread(store.set_phase, job_id, token, phase)
                await produce_script(executor)
                await asyncio.to_thread(store.fail, job_id, token, "intake_review",
                    category="controlled_script_complete" if controlled else "script_preview_complete",
                    message="Assessed script finalized; registered script-only scope still prohibits media." if controlled else "Script QA completed. This script-only run did not request paid generation; resume to measure voice and run media QA.")
                return store.get(job_id)
            image_lane = (None if refresh and refresh.get('local_voice_revision') is not None
                          else load_image_lane(settings, job_id))
            # A registered voice change reuses the verified visual assets. It
            # must not enter any independent image-provider workflow.
            image_operations = (("image_generation", produce_image_candidates),) if image_lane else ()
            for phase, operation in (
                ("scripting", produce_script), ("asset_planning", produce_assets),
            ) + image_operations + (
                ("voice_generation", produce_voice), ("captions", produce_captions),
                ("rendering", produce_edit), ("covers", produce_covers),
                ("qa", produce_final_quality),
                ("package", produce_package),
            ):
                if phase in {"rendering", "image_generation"} and gpu_lease is not None:
                    # Comfy owns the GPU lock itself; the API image lane needs no
                    # GPU. Neither should monopolize the order's model lease.
                    await asyncio.to_thread(lock.release, gpu_lease)
                    gpu_lease = None
                await asyncio.to_thread(store.set_phase, job_id, token, phase)
                if phase == "asset_planning" and image_lane:
                    # Validate the exact deterministic image plan immediately
                    # after script QA, before asset observation/ranking/model calls.
                    from atnos.production.wb_image_api import preflight_image_plan_for_order
                    await asyncio.to_thread(preflight_image_plan_for_order, executor.context, image_lane)
                await operation(executor)
                if phase in {"rendering", "image_generation"}:
                    # Voice validation, covers and semantic QA may use local
                    # models. Re-enter after independent image/render work.
                    gpu_lease = await asyncio.to_thread(
                        lock.acquire, owner=f"production-order:{job_id}", timeout_s=600
                    )
                    await asyncio.to_thread(ProductionGpuResidencyService(settings).prepare_for_llm)
            return await asyncio.to_thread(store.finish, job_id, token, Path(executor.context.artifacts["package"]["output_file"]))
        except Exception as exc:
            # Preserve specific bounded-retry failures already recorded by the stage executor.
            current = store.get(job_id)
            if current["status"] != "blocked":
                from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady

                if isinstance(exc, SceneAssetsNotReady):
                    waiting = exc.waiting
                    # The image lane writes its review receipt before stopping;
                    # the regular render asset resolver uses a different file.
                    review_receipt = executor.context.artifact_dir / (
                        "image_api_review.json" if phase == "image_generation"
                        else "scene_production_assets.json"
                    )
                    pending_review = bool(waiting) and all(
                        row.get("acquisition", {}).get("status") == "awaiting_approval"
                        for row in waiting
                    )
                    await asyncio.to_thread(
                        store.block, job_id, token,
                        "asset_review" if pending_review else "asset_planning",
                        category="awaiting_asset_review" if pending_review else "required_assets_missing",
                        message=(
                            f"{len(waiting)} required scene assets "
                            f"{'await review' if pending_review else 'are unavailable'}. "
                            f"Details: {review_receipt}"
                        ),
                    )
                else:
                    # A dependency guard can fail before a replacement plan
                    # begins. Preserve the actual completed upstream checkpoint.
                    preserve_plan = phase == 'asset_planning' and any(
                        row['stage'] == 'asset_planning' and row['status'] == 'done'
                        for row in current['checkpoints'])
                    record_failure = store.block if preserve_plan else store.fail
                    await asyncio.to_thread(record_failure, job_id, token, phase,
                        category="dependency_unavailable" if phase == "preflight" else "execution_failure", message=str(exc) or type(exc).__name__)
            return store.get(job_id)
        finally:
            if gpu_lease:
                await asyncio.to_thread(lock.release, gpu_lease)
