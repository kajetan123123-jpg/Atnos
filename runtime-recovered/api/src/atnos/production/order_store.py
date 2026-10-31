from __future__ import annotations

from contextlib import contextmanager
import copy
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import psutil

from atnos.core.config import Settings
from atnos.core.logging import redact_secrets
from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.order_feedback import snapshot_feedback
from atnos.production.order_intake import INTAKE_HOLD, intake_blockers, intake_error
from atnos.production.project_content_brain import (
    ProjectContentBrainStore,
    advance_series_topic,
    ensure_series_for_order,
    evaluate_history,
    plan_from_order,
    resolve_publication_status,
)
from atnos.production.project_profile import ProductionOrder, content_hash, load_project_profile, profile_for_order
from atnos.production.channel_contract import ChannelContractError, load_and_validate, validate_frozen

REQUIRED_GATE_STAGES = {"script": "script", "voice": "voice", "captions": "captions",
                        "media": "media_qa", "semantic": "semantic_qa", "package": "package"}


def local_operation_unlimited(settings, *, provider_call=False, external_tool_call=False, estimated_cost_usd=0):
    """Only classified local, zero-cost work is exempt from lifetime quotas."""
    return (settings.atnos_local_work_unlimited and not provider_call
            and not external_tool_call and Decimal(str(estimated_cost_usd)) == 0)


def _limited_calls(total, events):
    # Keep legacy unclassified calls bounded; do not rewrite historical usage.
    exempt = sum(1 for row in events if row['event'] == 'stage_started'
                 and (detail := json.loads(row['detail_json'])).get('quota_exempt') is True
                 and detail.get('counted_call') is True)
    return max(0, total - exempt)


def _local_resume_evidence(stage, events):
    for row in reversed(events):
        if row['stage'] == stage and row['event'] in {'stage_started', 'blocked'}:
            detail = json.loads(row['detail_json'])
            if 'local_operation' in detail:
                return detail['local_operation'] is True
    return stage == 'script_draft' or stage.startswith(('script_review/', 'script_repair/', 'script_scoped/'))


def checkpoint_fingerprints(checkpoints: list[dict]) -> dict:
    return {row["stage"]: content_hash({key: row.get(key) for key in
            ("status", "input_hash", "output_json", "files_json")})
            for row in checkpoints if row["stage"] != "package"}


def _single_pass_voice_proof(checkpoints: list[dict]) -> dict | None:
    """Evidence for retiring a segment branch, never a substitute for speech QA."""
    names = ("script", "voice/narration", "voice_qa/narration", "voice")
    rows = {row["stage"]: row for row in checkpoints if row["stage"] in names}
    if set(rows) != set(names) or any(rows[name]["status"] != "done" for name in names):
        return None
    try:
        outputs = {name: json.loads(rows[name]["output_json"] or "{}") for name in names}
        script, recording, check, voice = (outputs[name] for name in names)
        from atnos.production.steps.order_script import EditorialScript
        story = EditorialScript.model_validate({key: script[key] for key in EditorialScript.model_fields})
        script_qa = script.get("qa", {})
        segments = story.model_dump(mode="json")["segments"]
        ids = [segment["id"] for segment in segments]
        text = "\n\n".join(segment["text"].strip() for segment in segments)
        source = voice.get("source_recording", {})
        if (script_qa.get("status") != "passed" or type(script_qa.get("revision")) is not int
                or script_qa.get("script_hash") != content_hash(story.model_dump(mode="json"))
                or recording.get("single_pass") is not True or recording.get("text") != text
                or recording.get("provider", {}).get("model_id") != "eleven_v3"
                or not recording.get("provider", {}).get("voice_id")
                or check.get("status") != "passed" or check.get("scope") != "full_narration"
                or check.get("segment_ids") != ids
                or voice.get("single_pass") is not True or voice.get("production_ready") is not True
                or voice.get("qa", {}).get("status") != "passed"
                or voice.get("qa", {}).get("segments") != [check]
                or voice.get("normalization", {}).get("status") != "normalized"
                or voice.get("provider") != recording.get("provider")
                or voice.get("timestamps") != recording.get("timestamps")
                or voice.get("word_timestamps") != recording.get("word_timestamps")
                or voice.get("duration_seconds") != recording.get("duration_seconds")
                or [row["segment_id"] for row in recording["timestamps"]] != ids
                or [row["text"] for row in recording["timestamps"]] != [segment["text"] for segment in segments]
                or any(row.get("single_pass") is not True for row in recording["timestamps"])
                or any(source.get(key) != recording.get(key) or not source.get(key)
                       for key in ("output_file", "sha256", "provider_output_file", "provider_sha256"))):
            return None
        return {"mode": "eleven_v3_single_pass", "stage": "voice",
                "voice_id": recording["provider"]["voice_id"],
                "script_hash": script_qa["script_hash"],
                "dependencies": {name: {"input_hash": rows[name]["input_hash"],
                                         "output_hash": content_hash(outputs[name]),
                                         "files_hash": content_hash(json.loads(rows[name]["files_json"] or "[]"))}
                                 for name in names}}
    except (KeyError, TypeError, ValueError):
        return None


def _fresh_cover_proof(checkpoints: list[dict], orientation: str) -> dict | None:
    """A new rendered base can retire a failed text repair only after fresh QA."""
    rows = {row['stage']:row for row in checkpoints}
    cover, qa, media = (rows.get(name) for name in ('covers', f'semantic/cover_{orientation}', 'media_qa'))
    if not all(row and row['status'] == 'done' for row in (cover, qa, media)):
        return None
    try:
        base=json.loads(cover['output_json']); review=json.loads(qa['output_json']); audio_video=json.loads(media['output_json'])
        visual=base.get('visual') or {}; verdict=review.get('verdict') or {}
        if (visual.get('renderer') not in {'remotion_reserve_frame', 'remotion_research_frame'} or review.get('status') != 'passed'
                or verdict.get('status') != 'pass' or verdict.get('passed') is not True
                or review.get('sha256') != base.get('sha256',{}).get(orientation)
                or audio_video.get('status') != 'passed'
                or not visual.get('source_sha256') or visual['source_sha256'] != audio_video.get('video_sha256')
                or not visual.get('manifest_sha256')):
            return None
        return {'stage':qa['stage'],'input_hash':qa['input_hash'],'sha256':review['sha256'],
                'covers_input_hash':cover['input_hash'],'media_input_hash':media['input_hash'],
                'video_sha256':visual['source_sha256'],'manifest_sha256':visual['manifest_sha256']}
    except (KeyError, TypeError, ValueError):
        return None


def _scoped_script_proof(checkpoints: list[dict]) -> dict | None:
    """Bind inherited retired branches to a real accepted scoped successor."""
    rows={row['stage']:row for row in checkpoints}
    current=rows.get('script')
    if not current or current['status']!='done':
        return None
    try:
        payload=json.loads(current['output_json']); qa=payload.get('qa',{})
        digest=qa.get('feedback_hash','')
        if not re.fullmatch('[0-9a-f]{64}',digest) or qa.get('status')!='passed' or qa.get('issues'):
            return None
        if payload.get('provenance',{}).get('scoped_revision',{}).get('feedback_hash')!=digest:
            return None
        accepted=rows.get(f'script_scoped/{digest[:12]}/accepted')
        if not accepted or accepted['status']!='done' or json.loads(accepted['output_json'])!=payload:
            return None
        from atnos.production.steps.order_script import EditorialScript
        script=EditorialScript.model_validate({key:payload[key] for key in EditorialScript.model_fields})
        if qa.get('script_hash')!=content_hash(script.model_dump(mode='json')):
            return None
        for name,row in rows.items():
            if not re.fullmatch(f'script_scoped/{digest[:12]}/review/[0-9]+',name) or row['status']!='done':
                continue
            review=json.loads(row['output_json'])
            if (review.get('status')=='passed' and not review.get('issues')
                    and review.get('feedback_hash')==digest and review.get('script_hash')==qa['script_hash']
                    and review.get('revision')==qa.get('revision')):
                return {'stage':'script','input_hash':current['input_hash'],'script_hash':qa['script_hash'],
                        'feedback_hash':digest,'accepted_stage':accepted['stage'],'accepted_input_hash':accepted['input_hash'],
                        'review_stage':name,'review_input_hash':row['input_hash']}
    except (KeyError,TypeError,ValueError):
        return None
    return None


def checkpoint_resolved(row: dict, checkpoints: list[dict]) -> bool:
    if row["status"] == "done":
        return True
    if row['status'] == 'failed' and re.fullmatch(r'assets/image_local/flux2-trial/[^/]+/[0-9a-f]{16}', row['stage']):
        try:
            error = json.loads(row.get('error_json') or '{}')
            if error.get('category') != 'local_operation_abandoned' or error.get('flux_retry_permitted') is not False:
                return False
            gate = next((item for item in checkpoints if item['stage']=='image_generation' and item['status']=='done'),None)
            if gate is None:
                return False
            inventory=json.loads(gate.get('output_json') or '{}').get('qualified_assets') or []
            scene_id=row['stage'].split('/')[3]
            expected={'stage':row['stage'],'input_hash':row['input_hash'],'attempts':row['attempts'],
                      'reconciliation_sha256':error['evidence_sha256']}
            for item in checkpoints:
                if item['status']!='done' or not item['stage'].startswith('assets/image_local/'+scene_id+'/'):
                    continue
                output=json.loads(item.get('output_json') or '{}')
                files=json.loads(item.get('files_json') or '[]')
                if (output.get('replaces_local_operation')==expected and output.get('asset_id')
                        and any(selected.get('scene_id')==scene_id and selected.get('asset_id')==output['asset_id']
                                and selected.get('quality_status')=='APPROVED'
                                and selected.get('image_sha256')==output.get('image_sha256')
                                for selected in inventory)
                        and any(entry.get('sha256')==error['evidence_sha256']
                                and str(error['evidence_path']).replace('\\','/').endswith('/'+entry['path'].replace('\\','/'))
                                for entry in files)):
                    return True
        except (KeyError,TypeError,ValueError):
            return False
        return False
    cover_match=re.fullmatch(r'cover_quality/(portrait|landscape)/[1-9][0-9]*',row['stage'])
    if row['status']=='superseded' and cover_match:
        try:
            audit=json.loads(row['output_json'] or '{}')
            proof=_fresh_cover_proof(checkpoints,cover_match[1])
            return (proof is not None and audit.get('superseded_by')==proof
                    and audit.get('previous_status')=='failed' and audit.get('previous_input_hash')==row['input_hash']
                    and audit.get('attempts_preserved')==row['attempts']
                    and audit.get('previous_files_hash')==content_hash(json.loads(row['files_json'] or '[]')))
        except (TypeError,ValueError):
            return False
    if row["status"] == "superseded" and re.fullmatch(r"(?:voice|voice_qa)/[a-zA-Z0-9_-]+", row["stage"]) and not row["stage"].endswith("/narration"):
        try:
            audit = json.loads(row["output_json"] or "{}")
            proof = _single_pass_voice_proof(checkpoints)
            return (proof is not None and audit.get("superseded_by") == proof
                    and audit.get("previous_status") in {"done", "failed"}
                    and audit.get("previous_input_hash") == row["input_hash"]
                    and audit.get("attempts_preserved") == row["attempts"]
                    and audit.get("previous_files_hash") == content_hash(json.loads(row["files_json"] or "[]")))
        except (TypeError, ValueError):
            return False
    match = re.fullmatch(r"script_(?:repair|review)/(\d+)", row["stage"])
    if row["status"] != "superseded" or not match:
        return False
    script = next((item for item in checkpoints if item["stage"] == "script" and item["status"] == "done"), None)
    if not script:
        return False
    payload = json.loads(script["output_json"] or "{}")
    qa = payload.get("qa", {})
    proof = json.loads(row["output_json"] or "{}").get("superseded_by", {})
    scoped=_scoped_script_proof(checkpoints)
    if scoped is not None and proof==scoped:
        audit=json.loads(row['output_json'] or '{}')
        return (audit.get('previous_input_hash')==row['input_hash']
                and audit.get('attempts_preserved')==row['attempts'])
    return (qa.get("status") == "passed" and type(qa.get("revision")) is int
            and int(match[1]) > qa["revision"] and bool(qa.get("script_hash"))
            and proof == {"stage": "script", "input_hash": script["input_hash"], "script_hash": qa["script_hash"]})


class OrderConflict(ValueError):
    pass


class ProductionBlocked(RuntimeError):
    pass


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cost_units(value: float) -> int:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Cost reservation must be finite and non-negative")
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def _event(db, job_id, stage, event, detail):
    db.execute("INSERT INTO pipeline_job_events(job_id,stage,event,detail_json,created_at) VALUES(?,?,?,?,?)",
               (job_id, stage, event, json_dumps(redact_secrets(detail)), utc_now_iso()))


def _same_process(pid, started) -> bool:
    if not pid or started is None:
        return False
    try:
        process = psutil.Process(int(pid))
        return process.is_running() and abs(process.create_time() - float(started)) < .01
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # An unobservable owner is not evidence that it stopped.
        return True


def _approved_call_limit(db, job_id, default):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_call_authorizations'").fetchone():
        return default
    row = db.execute("SELECT max_calls FROM production_call_authorizations WHERE job_id=?", (job_id,)).fetchone()
    return row["max_calls"] if row else default


def _approved_attempt_limit(db, job_id, stage, default):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_attempt_authorizations'").fetchone():
        return default
    row=db.execute('SELECT max_attempts FROM production_attempt_authorizations WHERE job_id=? AND stage=?',(job_id,stage)).fetchone()
    return row['max_attempts'] if row else default


class ProductionOrderStore:
    """Persist request snapshots and stage checkpoints on the existing production jobs."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def authorize_call_limit(self, job_id: str, max_calls: int, *, reason: str):
        """Apply explicit user approval to one order, preserving costs, retries and the frozen profile."""
        if type(max_calls) is not int or not 1 <= max_calls <= 100000 or len(reason.strip()) < 15:
            raise ValueError("A bounded call limit and explicit approval reason are required")
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Production order not found")
            state = db.execute("SELECT status FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone()
            if state["status"] in {"ready", "script_ready", "cancelled"}:
                raise OrderConflict("Nie można zmienić limitu ukończonej lub anulowanej produkcji.")
            if _same_process(row["lease_pid"], row["lease_process_started"]):
                raise OrderConflict("Do not change authorization while a worker is active")
            config = json.loads(row["profile_json"])["production"]
            old = _approved_call_limit(db, job_id, config["max_provider_calls"])
            if max_calls < max(old, row["provider_calls"]):
                raise ValueError("This approval only extends the call limit, never resets consumed work")
            if old != max_calls:
                db.execute("CREATE TABLE IF NOT EXISTS production_call_authorizations(job_id TEXT PRIMARY KEY,max_calls INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL)")
                db.execute("INSERT INTO production_call_authorizations VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET max_calls=excluded.max_calls,reason=excluded.reason,created_at=excluded.created_at",
                           (job_id,max_calls,redact_secrets(reason),utc_now_iso()))
                _event(db,job_id,None,"call_limit_authorized",{"previous":old,"approved":max_calls,"reason":reason,
                    "max_cost_usd_unchanged":config["max_cost_usd"],"attempt_limit_unchanged":config["max_attempts_per_element"],
                    "consumed_calls_preserved":row["provider_calls"]})
                db.execute("UPDATE pipeline_jobs SET status='queued',error=NULL,updated_at=? WHERE id=? AND error='provider_call_limit'", (utc_now_iso(),job_id))
            db.commit()
        return self.get(job_id)

    def create(self, order: ProductionOrder, *, request_key: str | None = None) -> dict:
        request_key = request_key or uuid4().hex
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", request_key):
            raise ValueError("Invalid idempotency key")
        request = order.model_dump(mode="json")
        if order.brand_kit is None:
            request.pop('brand_kit')
        if order.visual_style_id is None:
            request.pop('visual_style_id')
        if order.voice_candidate is None:
            request.pop('voice_candidate')
        if order.production_scope == 'film':
            request.pop('production_scope')
        # Preserve the exact legacy request hash when the new optional field is absent.
        if order.intake is None:
            request.pop("intake")
        if order.delivery_mode == "project_policy":
            request.pop("delivery_mode")
        identity = content_hash(request)
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT job_id,request_hash FROM pipeline_job_orders WHERE request_key=?", (request_key,)).fetchone()
            if previous:
                if previous["request_hash"] != identity:
                    raise OrderConflict("Idempotency key already belongs to different production inputs")
                job_id = previous["job_id"]
            else:
                profile = profile_for_order(load_project_profile(self.settings, order.project), order)
                channel_contract = load_and_validate(self.settings, order.project, profile)
                if channel_contract is not None:
                    profile["channel_contract"] = channel_contract
                if order.brand_kit:
                    from atnos.production.brand_contract import freeze_brand
                    profile['brand_kit'] = freeze_brand(self.settings, order.brand_kit)
                profile["feedback_rules"] = snapshot_feedback(db, order.project, order.series_key)
                brain = ProjectContentBrainStore(db)
                brain.load_or_create_snapshot(order.project, profile=profile)
                ensure_series_for_order(brain, project=order.project, series_key=order.series_key, topic=order.topic)
                history = evaluate_history(
                    brain,
                    project=order.project,
                    topic=order.topic,
                    direction=order.direction,
                    series_key=order.series_key,
                )
                profile["history_decision"] = history.model_dump(mode="json")
                profile["publication_status"] = resolve_publication_status(
                    production_status="queued",
                    delivery_mode=order.delivery_mode,
                )
                job_id, topic_id, now = f"prod-{uuid4().hex[:12]}", f"topic-{uuid4().hex}", utc_now_iso()
                image_setup_pending = order.image_provider == "comfyui-local"
                voice_setup_pending = order.voice_candidate in {'Ryan-B', 'Ryan-neutral'} and not image_setup_pending
                initial_status = "blocked" if image_setup_pending or voice_setup_pending else "queued"
                initial_step = "image_setup" if image_setup_pending else 'voice_setup' if voice_setup_pending else "queued"
                initial_error = (json_dumps({
                    "category": "local_image_setup_pending",
                    "message": "Oczekiwanie na zapisanie autoryzacji lokalnego ComfyUI przed uruchomieniem workera.",
                }) if image_setup_pending else None)
                if voice_setup_pending:
                    initial_error = json_dumps({'category': 'local_voice_setup_pending',
                        'message': 'Oczekiwanie na trwałe przypisanie wybranego lokalnego głosu przed uruchomieniem workera.'})
                account = profile["account_id"]
                if not db.execute("SELECT 1 FROM intelligence_accounts WHERE account_id=?", (account,)).fetchone():
                    raise ValueError("The project profile has no existing production account")
                directory = self.settings.data_dir / "production" / order.project / job_id
                db.execute("INSERT INTO topic_queue(id,account_id,title,description,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (topic_id, account, order.topic, order.direction, "in_production", now, now))
                db.execute("INSERT INTO pipeline_jobs(id,account_id,topic_id,status,current_step,artifact_dir,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (job_id, account, topic_id, initial_status, initial_step, str(directory), initial_error, now, now))
                db.execute("INSERT INTO pipeline_job_orders(job_id,request_key,request_hash,request_json,profile_json,profile_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                           (job_id, request_key, identity, json_dumps(request), json_dumps(profile), content_hash(profile), now))
                brain.save_history_decision(job_id, order.project, history)
                plan = plan_from_order(
                    project=order.project,
                    topic=order.topic,
                    series_key=order.series_key,
                    job_id=job_id,
                    channel=str(profile.get("channel_name") or order.project),
                    history=history,
                )
                brain.upsert_plan(plan)
                advance_series_topic(brain, project=order.project, series_key=order.series_key, topic=order.topic)
                _event(db, job_id, "queued", "order_created", {
                    "profile_hash": content_hash(profile),
                    "history_decision": history.decision,
                    "duplication_warning": history.duplication_warning,
                    "publication_status": profile["publication_status"]["publication_status"],
                    "content_plan_id": plan.plan_id,
                })
                if image_setup_pending:
                    _event(db, job_id, "image_setup", "blocked", json.loads(initial_error))
                if voice_setup_pending:
                    _event(db, job_id, 'voice_setup', 'blocked', json.loads(initial_error))
                if order.intake is not None:
                    error = intake_error(request["intake"])
                    db.execute("UPDATE pipeline_jobs SET status='blocked',current_step='intake_review',error=? WHERE id=?",
                               (json_dumps(error), job_id))
                    _event(db, job_id, "intake_review", "intake_held", error)
            db.commit()
        if (self.settings.shorts_order_autonomy_enabled and order.delivery_mode == 'local_only'
                and order.production_scope == 'film'):
            from atnos.production.order_autonomy import enable, policy
            if policy(self.settings,job_id) is None:
                enable(self.settings,job_id,reason='Configured local production autonomy: bounded recovery, preserved budgets and quality gates, no publication.')
        return self.get(job_id)

    def authorize_stage_attempts(self,job_id,stage,max_attempts,*,reason):
        """An explicit operator authorization extends a single stage without resetting history or cost/call limits."""
        if type(max_attempts) is not int or not 1<=max_attempts<=100 or len(reason.strip())<15:
            raise ValueError('A bounded stage limit and explicit authorization reason are required')
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT o.*,j.status,j.current_step,j.error FROM pipeline_job_orders o JOIN pipeline_jobs j ON j.id=o.job_id WHERE job_id=?',(job_id,)).fetchone()
            checkpoint=db.execute('SELECT attempts FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?',(job_id,stage)).fetchone()
            if not row or not checkpoint:raise ValueError('An existing order and stage are required')
            if row['status'] in {'ready','script_ready','cancelled'} or _same_process(row['lease_pid'],row['lease_process_started']):
                raise OrderConflict('Cannot extend an active or completed production')
            old=_approved_attempt_limit(db,job_id,stage,json.loads(row['profile_json'])['production']['max_attempts_per_element'])
            if max_attempts<=max(old,checkpoint['attempts']):raise ValueError('This authorization only extends the limit')
            db.execute('CREATE TABLE IF NOT EXISTS production_attempt_authorizations(job_id TEXT NOT NULL,stage TEXT NOT NULL,max_attempts INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(job_id,stage))')
            db.execute('INSERT INTO production_attempt_authorizations VALUES(?,?,?,?,?) ON CONFLICT(job_id,stage) DO UPDATE SET max_attempts=excluded.max_attempts,reason=excluded.reason,created_at=excluded.created_at',(job_id,stage,max_attempts,redact_secrets(reason),utc_now_iso()))
            _event(db,job_id,stage,'stage_attempts_authorized',{'previous':old,'approved':max_attempts,'consumed_preserved':checkpoint['attempts'],'reason':reason,'cost_and_call_limits':'unchanged'})
            if row['current_step']==stage and row['error']=='attempt_limit':
                db.execute("UPDATE pipeline_jobs SET status='queued',error=NULL,updated_at=? WHERE id=?",(utc_now_iso(),job_id))
            db.commit()
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with connect(self.settings) as db:
            row = db.execute("SELECT j.*,o.request_json,o.profile_json,o.profile_hash,o.reserved_cost_units,o.provider_calls,o.lease_pid,o.lease_process_started FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Production order not found")
            max_calls = _approved_call_limit(db, job_id, json.loads(row['profile_json'])['production']['max_provider_calls'])
            stages = db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? ORDER BY rowid", (job_id,)).fetchall()
            events = db.execute("SELECT id,stage,event,detail_json,created_at FROM pipeline_job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        payload = dict(row)
        payload["request"] = json.loads(payload.pop("request_json"))
        payload["profile"] = json.loads(payload.pop("profile_json"))
        contract = payload["profile"].get("channel_contract")
        if isinstance(contract, dict):
            payload["channel_contract"] = {
                key: contract.get(key)
                for key in ("project", "project_dir", "model_files", "voice", "style_contract_hash",
                            "voice_contract_hash", "workflow_contract_hash", "contract_hash")
            }
        payload["worker_live"] = _same_process(payload.pop("lease_pid"), payload.pop("lease_process_started"))
        error_code = payload["error"]
        if error_code and str(error_code).startswith("{"):
            try:
                error_code = json.loads(error_code).get("category")
            except (ValueError, AttributeError):
                pass
        intake = payload["request"].get("intake")
        supported = intake is None or not intake_blockers(intake)
        payload['local_work_unlimited'] = self.settings.atnos_local_work_unlimited
        payload['limited_provider_calls'] = _limited_calls(row['provider_calls'], events)
        # Old script stages are local by contract. Other legacy stages with
        # unknown billing stay bounded until an actual operation is classified.
        local_count_block = (self.settings.atnos_local_work_unlimited
            and error_code in {'attempt_limit', 'provider_call_limit', 'cost_limit', 'studio_script_action_limit'}
            and _local_resume_evidence(payload['current_step'], events))
        resume_blocked = error_code in {'execution_outcome_unknown', 'attempt_limit', 'cost_limit', 'provider_call_limit',
                                       'script_quality_limit', 'local_image_setup_pending', 'local_voice_setup_pending',
                                       'independent_semantic_review_required', 'independent_semantic_review_rejected',
                                       'studio_script_operation_budget', 'controlled_script_complete'}
        payload["can_resume"] = (supported and not payload["worker_live"]
            and payload["status"] not in {"ready", "script_ready", "cancelled"}
            and (not resume_blocked or local_count_block))
        # Both ordinary topic briefs and supplied scripts use produce_script.
        # A script-only lease still rejects media and paid stage admission.
        payload["can_prepare_script"] = bool(
            payload["request"].get("project") == "wealth_blueprint" and payload["can_resume"])
        payload["max_provider_calls"] = max_calls
        payload["can_extend_call_limit"] = (payload['status'] == 'blocked' and error_code == 'provider_call_limit'
                                            and not payload['worker_live'] and max_calls < 100000)
        payload["checkpoints"] = [{**dict(s), "output": json.loads(s["output_json"] or "{}"),
                                    "files": json.loads(s["files_json"] or "[]")} for s in stages]
        payload["history"] = [{**dict(e), "detail": json.loads(e["detail_json"])} for e in events]
        from atnos.production.order_autonomy import policy
        autonomy = policy(self.settings,job_id)
        payload['autonomous_recovery'] = ({
            'enabled':bool(autonomy['enabled']), 'used':autonomy['recoveries_used'],
            'maximum':None if self.settings.atnos_local_work_unlimited else autonomy['max_recoveries'],
            'limit_reached':not self.settings.atnos_local_work_unlimited and autonomy['recoveries_used'] >= autonomy['max_recoveries'],
            'publication_enabled':False,
        } if autonomy else {'enabled':False})
        return payload

    def context(self, job_id: str) -> ProductionContext:
        order = self.get(job_id)
        request = order["request"]
        root = Path(order["artifact_dir"]).resolve()
        if not root.is_relative_to((self.settings.data_dir / "production").resolve()):
            raise ValueError("Production directory escapes Atnos storage")
        root.mkdir(parents=True, exist_ok=True)
        return ProductionContext(job_id=job_id, account_id=order["account_id"],
            topic=ApprovedTopic(id=order["topic_id"], account_id=order["account_id"], title=request["topic"],
                description=request["direction"], total_score=0, content_profile=request["project"]),
            artifact_dir=root, artifacts={"production_order": request, "project_profile": order["profile"]})

    def is_dispatched_worker(self, job_id: str) -> bool:
        """Only the launched process (or Windows venv launcher's child) may adopt startup."""
        with connect(self.settings) as db:
            row = db.execute('SELECT lease_token,lease_pid,lease_process_started FROM pipeline_job_orders WHERE job_id=?',(job_id,)).fetchone()
        if not row or row['lease_token'] or not _same_process(row['lease_pid'],row['lease_process_started']):
            return False
        current=psutil.Process()
        candidates=[current,current.parent()]
        return any(p is not None and p.pid==row['lease_pid'] and abs(p.create_time()-row['lease_process_started'])<.01 for p in candidates)

    @contextmanager
    def lease(self, job_id: str, *, script_only: bool = False):
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Production order not found")
            intake = json.loads(row["request_json"]).get("intake")
            if intake is not None and intake_blockers(intake):
                raise ProductionBlocked(INTAKE_HOLD)
            if row["lease_token"] and _same_process(row["lease_pid"], row["lease_process_started"]):
                raise OrderConflict("This production already has a live worker")
            # Resolve the durable action under the same write transaction as
            # lease acquisition. A registration cannot race into a full AV lease.
            from atnos.production.studio_script_recovery import repair_execution_scope
            script_only = script_only or repair_execution_scope(self, job_id) == 'script_only'
            token = ("script-only:" if script_only else "") + uuid4().hex
            db.execute("UPDATE pipeline_job_orders SET lease_token=?,lease_pid=?,lease_process_started=? WHERE job_id=?",
                       (token, os.getpid(), psutil.Process().create_time(), job_id))
            _event(db, job_id, None, "worker_acquired", {})
            db.commit()
        try:
            yield token
        finally:
            with connect(self.settings) as db:
                db.execute("UPDATE pipeline_job_orders SET lease_token=NULL,lease_pid=NULL,lease_process_started=NULL WHERE job_id=? AND lease_token=?", (job_id, token))
                db.commit()

    def record_dispatched_worker(self, job_id: str, pid: int) -> None:
        """Track the actual launched process while it imports modules, before lease acquisition."""
        try:
            started = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:
            # A process that already exited must not leave a fabricated running state.
            return
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT lease_token,lease_pid,lease_process_started FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Production order not found")
            if not row['lease_token'] and not _same_process(row['lease_pid'], row['lease_process_started']):
                # Never replace the execution lease if the child has already acquired it.
                # lease() may adopt this tokenless process observation when imports finish.
                db.execute("UPDATE pipeline_job_orders SET lease_pid=?,lease_process_started=? WHERE job_id=?", (pid, started, job_id))
                db.execute("UPDATE pipeline_jobs SET status='queued',current_step='worker_starting',error=NULL,updated_at=? WHERE id=? AND status NOT IN ('ready','script_ready','cancelled')",
                           (utc_now_iso(), job_id))
                _event(db, job_id, None, 'worker_launched', {'pid': pid, 'process_started': started})
            db.commit()

    def set_phase(self, job_id: str, token: str, phase: str) -> None:
        """Report live work even when the next operation only reuses checkpoints."""
        if phase not in {"preflight", "waiting_for_runtime", "scripting", "asset_planning", "image_generation",
                         "voice_generation", "captions", "rendering", "covers", "qa",
                         "package", "branded_production"}:
            raise ValueError("Unknown production phase")
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, job_id, token)
            db.execute("UPDATE pipeline_jobs SET status='running',current_step=?,error=NULL,updated_at=? WHERE id=?",
                       (phase, utc_now_iso(), job_id))
            _event(db, job_id, phase, "phase_started", {})
            db.commit()

    @staticmethod
    def _owner(db, job_id, token):
        row = db.execute("SELECT * FROM pipeline_job_orders WHERE job_id=? AND lease_token=?", (job_id, token)).fetchone()
        if not token or row is None or row["lease_pid"] != os.getpid() or not _same_process(row["lease_pid"], row["lease_process_started"]):
            raise OrderConflict("Production execution lease is missing or no longer owned")
        intake = json.loads(row["request_json"]).get("intake")
        if intake is not None and intake_blockers(intake):
            raise ProductionBlocked(INTAKE_HOLD)
        return row

    def _files(self, root: Path, paths: list[Path]) -> list[dict]:
        entries = []
        for path in paths:
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.stat().st_size == 0:
                raise ValueError("A stage output is missing, empty or outside the production directory")
            entries.append({"path": resolved.relative_to(root).as_posix(), "sha256": file_hash(resolved)})
        return entries

    def _valid_files(self, root: Path, encoded: str) -> bool:
        try:
            expected = json.loads(encoded)
            return bool(expected) and self._files(root, [root / item["path"] for item in expected]) == expected
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _validate_stage_contract_output(self, job_id: str, output: dict) -> None:
        """Reject output that declares another channel or a mixed style.

        Stages may add their metadata at different nesting levels, so inspect
        the complete JSON payload while keeping legacy payloads valid when no
        channel contract is attached to the order.
        """
        order = self.get(job_id)
        contract = order["profile"].get("channel_contract")
        if not contract:
            return
        expected = str(order["request"].get("project") or "").casefold()
        expected_hashes = {
            "style_contract_hash": contract.get("style_contract_hash"),
            "voice_contract_hash": contract.get("voice_contract_hash"),
            "workflow_contract_hash": contract.get("workflow_contract_hash"),
        }
        def walk(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)
        for item in walk(output):
            for key in ("project", "channel", "channel_profile_key", "channel_key"):
                if key not in item or not isinstance(item[key], str):
                    continue
                declared = item[key].strip().casefold().replace(" ", "_")
                if declared in {"wealth_blueprint", "children_stories", "horror_stories"} and declared != expected:
                    raise ChannelContractError(f"Stage output mixes channel {declared} into {expected}")
            for key, value in expected_hashes.items():
                if key in item and item[key] != value:
                    raise ChannelContractError(f"Stage output has an incompatible {key}")

    def begin(self, job_id: str, token: str, stage: str, inputs: dict, *, estimated_cost_usd: float = 0,
              provider_call: bool = False, local_model_call: bool = False, external_tool_call: bool = False) -> dict | None:
        if not re.fullmatch(r"[a-z][a-z0-9_:/-]{0,139}", stage):
            raise ValueError("Invalid stage key")
        cost = cost_units(estimated_cost_usd)
        if provider_call and cost == 0:
            raise ValueError("A provider call requires a positive upper-bound cost reservation")
        if local_model_call and (provider_call or cost):
            raise ValueError("Local inference cannot reserve a paid-provider call")
        if external_tool_call and (provider_call or local_model_call or cost):
            raise ValueError('An external tool relay has separately reported billing; do not invent an API price')
        counted_call = provider_call or local_model_call or external_tool_call
        root = self.context(job_id).artifact_dir
        digest, blocked = content_hash(inputs), None
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            owner = self._owner(db, job_id, token)
            if token.startswith("script-only:"):
                script_stage = re.fullmatch(
                    r"script(?:_context|_calculation_plan|_calculations|_draft|_review/\d+|_repair/(?:\d+|studio-[0-9a-f]{12})|_candidate/[0-9a-f]{12})?", stage)
                scoped = re.fullmatch(r"script_scoped/([0-9a-f]{12})/(?:draft/\d+|review/\d+|accepted)", stage)
                if not script_stage and scoped and not (provider_call or external_tool_call or cost):
                    from atnos.production.scoped_script_revision import load_feedback
                    feedback = load_feedback(self, job_id)
                    script_stage = bool(feedback and feedback.get('mode') == 'revise_existing'
                        and feedback['feedback_hash'][:12] == scoped[1]
                        and inputs.get('feedback_hash') == feedback['feedback_hash'])
                if provider_call or external_tool_call or cost or not script_stage:
                    raise ProductionBlocked("Script-only admission cannot run paid, media or publication stages")
            if stage == 'script':
                from atnos.production.studio_script_recovery import require_candidate_acceptance
                require_candidate_acceptance(self, job_id, script=inputs.get('script'))
            config = json.loads(owner["profile_json"])["production"]
            try:
                validate_frozen(self.settings, json.loads(owner["request_json"]).get("project", ""), json.loads(owner["profile_json"]))
            except ChannelContractError as exc:
                db.execute("UPDATE pipeline_jobs SET status='blocked',current_step=?,error=?,updated_at=? WHERE id=?",
                           (stage, json_dumps({"category": "channel_contract", "message": str(exc)}), utc_now_iso(), job_id))
                _event(db, job_id, stage, "blocked", {"category": "channel_contract", "message": str(exc)})
                db.commit()
                raise ProductionBlocked(f"channel_contract: {exc}") from exc
            # Wealth Blueprint: do not spend GPU/media on FACT_CHECK_BLOCKED scripts.
            request_payload = json.loads(owner["request_json"])
            project_key = str(request_payload.get("project") or "").casefold().replace(" ", "_")
            if project_key == "wealth_blueprint":
                from atnos.integrations.claim_evidence_gate import ensure_media_claim_gate

                script_payload = None
                script_row = db.execute(
                    "SELECT output_json FROM pipeline_stage_checkpoints WHERE job_id=? AND stage='script' AND status='done'",
                    (job_id,),
                ).fetchone()
                if script_row and script_row["output_json"]:
                    try:
                        script_payload = json.loads(script_row["output_json"])
                    except json.JSONDecodeError:
                        script_payload = None
                hypothetical_gate = {}
                if (request_payload.get("intake") or {}).get("kind") == "user_hypothetical":
                    calculations_row = db.execute(
                        "SELECT output_json,files_json FROM pipeline_stage_checkpoints WHERE job_id=? AND stage='script_calculations' AND status='done'",
                        (job_id,),
                    ).fetchone()
                    receipt = None
                    if (calculations_row and json.loads(calculations_row["files_json"] or "[]")
                            and self._valid_files(root, calculations_row["files_json"] or "[]")):
                        try:
                            receipt = json.loads(calculations_row["output_json"] or "null")
                        except json.JSONDecodeError:
                            pass
                    hypothetical_gate = {"hypothetical_intake": request_payload["intake"],
                                         "calculation_receipt": receipt}
                claim_block = ensure_media_claim_gate(
                    project="wealth_blueprint",
                    production_id=job_id,
                    stage=stage,
                    script=script_payload if isinstance(script_payload, dict) else None,
                    **hypothetical_gate,
                )
                if claim_block.get("blocked"):
                    detail = {
                        "category": "FACT_CHECK_BLOCKED",
                        "message": claim_block.get("detail"),
                        "blocked_claims": claim_block.get("blocked_claims") or [],
                    }
                    db.execute(
                        "UPDATE pipeline_jobs SET status='blocked',current_step=?,error=?,updated_at=? WHERE id=?",
                        (stage, json_dumps(detail), utc_now_iso(), job_id),
                    )
                    _event(db, job_id, stage, "blocked", detail)
                    db.commit()
                    raise ProductionBlocked(
                        "FACT_CHECK_BLOCKED: fix or remove unsupported high-risk claims before media/GPU stages. "
                        + str(claim_block.get("detail") or "")
                    )
            row = db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?", (job_id, stage)).fetchone()
            from atnos.production.voice_revision import guard_durable_voice_revision
            try:
                guard_durable_voice_revision(self, db, job_id, root, row, stage, inputs,
                    local_model_call=local_model_call, provider_call=provider_call,
                    external_tool_call=external_tool_call, estimated_cost_usd=estimated_cost_usd)
            except ProductionBlocked as exc:
                detail = {'category': 'local_voice_revision_scope', 'message': str(exc)}
                db.execute("UPDATE pipeline_jobs SET status='blocked',current_step=?,error=?,updated_at=? WHERE id=?",
                           (stage, json_dumps(detail), utc_now_iso(), job_id))
                _event(db, job_id, stage, 'blocked', detail)
                db.commit()
                raise
            if row and row["status"] == "done" and row["input_hash"] == digest and self._valid_files(root, row["files_json"] or "[]"):
                _event(db, job_id, stage, "cache_reused", {})
                db.commit()
                return json.loads(row["output_json"])
            attempts = int(row["attempts"]) if row else 0
            unlimited = local_operation_unlimited(self.settings, provider_call=provider_call,
                external_tool_call=external_tool_call, estimated_cost_usd=estimated_cost_usd)
            call_usage = owner['provider_calls']
            if self.settings.atnos_local_work_unlimited and not unlimited and counted_call:
                events = db.execute('SELECT stage,event,detail_json FROM pipeline_job_events WHERE job_id=?', (job_id,)).fetchall()
                call_usage = _limited_calls(call_usage, events)
            if not unlimited and attempts >= _approved_attempt_limit(db,job_id,stage,config["max_attempts_per_element"]):
                blocked = "attempt_limit"
            elif not unlimited and owner["reserved_cost_units"] + cost > cost_units(config["max_cost_usd"]):
                blocked = "cost_limit"
            elif not unlimited and counted_call and call_usage >= _approved_call_limit(db, job_id, config["max_provider_calls"]):
                blocked = "provider_call_limit"
            if blocked:
                db.execute("UPDATE pipeline_jobs SET status='blocked',current_step=?,error=?,updated_at=? WHERE id=?", (stage, blocked, utc_now_iso(), job_id))
                _event(db, job_id, stage, "blocked", {"category": blocked, "attempts": attempts,
                    "local_operation": not provider_call and not external_tool_call and cost == 0})
            else:
                now = utc_now_iso()
                db.execute("UPDATE pipeline_job_orders SET reserved_cost_units=reserved_cost_units+?,provider_calls=provider_calls+? WHERE job_id=?", (cost, int(counted_call), job_id))
                db.execute("INSERT INTO pipeline_stage_checkpoints(job_id,stage,input_hash,status,attempts,updated_at) VALUES(?,?,?,'running',?,?) ON CONFLICT(job_id,stage) DO UPDATE SET input_hash=excluded.input_hash,status='running',attempts=excluded.attempts,error_json=NULL,updated_at=excluded.updated_at",
                           (job_id, stage, digest, attempts + 1, now))
                db.execute("UPDATE pipeline_jobs SET status='running',current_step=?,error=NULL,updated_at=? WHERE id=?", (stage, now, job_id))
                db.execute("INSERT INTO pipeline_job_steps(job_id,step,status,attempt,started_at) VALUES(?,?,'running',?,?) ON CONFLICT(job_id,step) DO UPDATE SET status='running',attempt=excluded.attempt,completed_at=NULL", (job_id, stage, attempts + 1, now))
                _event(db, job_id, stage, "stage_started", {"attempt": attempts + 1, "reserved_cost_units": cost,
                    "local_operation": not provider_call and not external_tool_call and cost == 0,
                    "counted_call": bool(counted_call), "quota_exempt": unlimited})
            db.commit()
        if blocked:
            raise ProductionBlocked(blocked)
        return None

    def repair_renderer_review_evidence(self, job_id: str, token: str, action_digest: str):
        """Relocate the known v1/v2 review filename collision, preserving exact hashes.

        Rehydration is allowed only when deterministic serialization of the
        durable output reproduces its ORIGINAL recorded SHA-256. No verdict,
        status, counter, canonical or model result is changed.
        """
        from hashlib import sha256
        import os
        import re
        from atnos.production.steps.common import write_json
        if not re.fullmatch('[a-f0-9]{64}', action_digest):
            raise OrderConflict('Invalid renderer review action')
        root=self.context(job_id).artifact_dir
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE');self._owner(db,job_id,token)
            action=db.execute("SELECT output_json FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=? AND status='done'",
                (job_id,'remotion_refresh/'+action_digest[:12])).fetchone()
            if not action or json.loads(action['output_json']).get('digest')!=action_digest:
                raise OrderConflict('Review evidence repair requires its registered renderer action')
            rows=db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage LIKE ? AND status='done'",
                (job_id,'remotion_script_review/'+action_digest[:12]+'/%')).fetchall()
            changes=[]
            legacy='renderer_refresh/'+action_digest[:12]+'/script-review.json'
            for row in rows:
                entries=json.loads(row['files_json']);payload=json.loads(row['output_json'])
                if len(entries)!=1 or entries[0]['path']!=legacy:continue
                digest=entries[0]['sha256']
                # write_json uses a text handle with the host newline convention.
                encoded=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True).replace('\n',os.linesep).encode('utf8')
                if (payload.get('action_digest')!=action_digest or payload.get('scope')!='review_only'
                        or sha256(encoded).hexdigest()!=digest):
                    raise OrderConflict('Recorded review cannot be restored byte for byte')
                destination=root/'renderer_refresh'/action_digest[:12]/('script-review-'+digest+'.json')
                if destination.exists() and file_hash(destination)!=digest:
                    raise OrderConflict('Conflicting immutable review evidence')
                if not destination.exists():write_json(destination,payload)
                if file_hash(destination)!=digest:raise OrderConflict('Restored review hash mismatch')
                new=[{'path':destination.relative_to(root).as_posix(),'sha256':digest}]
                db.execute('UPDATE pipeline_stage_checkpoints SET files_json=? WHERE job_id=? AND stage=?',
                    (json_dumps(new),job_id,row['stage']))
                changes.append({'stage':row['stage'],'from':legacy,'to':new[0]['path'],'sha256':digest})
            if changes:_event(db,job_id,'remotion_refresh/'+action_digest[:12],'review_evidence_paths_repaired',{'changes':changes,'outputs_statuses_counters_unchanged':True})
            db.commit()
        return changes

    def complete(self, job_id: str, token: str, stage: str, output: dict, files: list[Path]):
        try:
            self._validate_stage_contract_output(job_id, output)
        except ChannelContractError as exc:
            raise ProductionBlocked(f"channel_contract: {exc}") from exc
        entries = self._files(self.context(job_id).artifact_dir, files)
        if not entries:
            raise ValueError("A completed stage requires tangible file evidence")
        content_hash(output)
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, job_id, token)
            cursor = db.execute("UPDATE pipeline_stage_checkpoints SET status='done',output_json=?,files_json=?,error_json=NULL,updated_at=? WHERE job_id=? AND stage=? AND status='running'",
                (json_dumps(output), json_dumps(entries), utc_now_iso(), job_id, stage))
            if cursor.rowcount != 1:
                raise OrderConflict("Only a running stage may be completed")
            db.execute("UPDATE pipeline_job_steps SET status='done',progress=1,completed_at=? WHERE job_id=? AND step=?", (utc_now_iso(), job_id, stage))
            db.execute("UPDATE pipeline_jobs SET updated_at=? WHERE id=?", (utc_now_iso(), job_id))
            _event(db, job_id, stage, "stage_completed", {"files": entries})
            db.commit()

    def persist_legacy_artifact(self, job_id: str, token: str, key: str, payload: dict):
        columns = {"script": "script", "voice": "audio_timestamps", "visuals": "asset_plan", "thumbnail": "thumbnail"}
        if key not in columns:
            raise ValueError("Unsupported production artifact")
        with connect(self.settings) as db:
            self._owner(db, job_id, token)
            if key == "thumbnail":
                db.execute("UPDATE pipeline_jobs SET thumbnail_path=?,updated_at=? WHERE id=?",
                           (str(payload.get("landscape") or payload.get("portrait") or ""), utc_now_iso(), job_id))
            else:
                db.execute(f"UPDATE pipeline_jobs SET {columns[key]}=?,updated_at=? WHERE id=?",
                           (json_dumps(payload), utc_now_iso(), job_id))
            db.commit()

    def reconcile_cover_checkpoints(self, job_id: str, token: str):
        """Preserve abandoned repair attempts after a separately qualified new base."""
        root=self.context(job_id).artifact_dir
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            self._owner(db,job_id,token)
            rows=[dict(row) for row in db.execute('SELECT * FROM pipeline_stage_checkpoints WHERE job_id=?',(job_id,))]
            by_stage={row['stage']:row for row in rows}
            for old in rows:
                match=re.fullmatch(r'cover_quality/(portrait|landscape)/[1-9][0-9]*',old['stage'])
                if old['status'] not in {'failed','superseded'} or not match:
                    continue
                proof=_fresh_cover_proof(rows,match[1])
                if not proof:
                    continue
                previous_audit=json.loads(old['output_json'] or '{}') if old['status']=='superseded' else None
                if previous_audit is not None:
                    if (previous_audit.get('previous_status')!='failed'
                            or previous_audit.get('previous_input_hash')!=old['input_hash']
                            or previous_audit.get('attempts_preserved')!=old['attempts']
                            or previous_audit.get('previous_files_hash')!=content_hash(json.loads(old['files_json'] or '[]'))):
                        raise ProductionBlocked('Existing cover supersession audit is inconsistent')
                    if previous_audit.get('superseded_by')==proof:
                        continue
                if any(not self._valid_files(root,by_stage[name]['files_json'] or '[]')
                       for name in ('covers','media_qa',proof['stage'])):
                    raise ProductionBlocked('Cover supersession requires intact current rendered cover and independent QA')
                audit={'previous_status':old['status'],'previous_input_hash':old['input_hash'],
                       'previous_output':json.loads(old['output_json'] or '{}'),
                       'previous_error':json.loads(old['error_json'] or '{}'),
                       'previous_files_hash':content_hash(json.loads(old['files_json'] or '[]')),
                       'attempts_preserved':old['attempts'],'superseded_by':proof}
                if previous_audit is not None:
                    audit={**previous_audit,'superseded_by':proof,
                           'supersession_history':[*previous_audit.get('supersession_history',[]),previous_audit['superseded_by']]}
                now=utc_now_iso()
                db.execute("UPDATE pipeline_stage_checkpoints SET status='superseded',output_json=?,updated_at=? WHERE job_id=? AND stage=?",
                           (json_dumps(audit),now,job_id,old['stage']))
                db.execute("UPDATE pipeline_job_steps SET status='superseded',message=?,completed_at=? WHERE job_id=? AND step=?",
                           ('Replaced by a separately reviewed current Remotion-frame cover; failed attempts retained.',now,job_id,old['stage']))
                _event(db,job_id,old['stage'],'cover_branch_superseded',audit)
            db.commit()

    def reconcile_script_checkpoints(self, job_id: str, token: str):
        """Retire only abandoned later script branches, never a required QA gate or its counters."""
        root = self.context(job_id).artifact_dir
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, job_id, token)
            row = db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage='script'", (job_id,)).fetchone()
            if not row or row["status"] != "done" or not self._valid_files(root, row["files_json"] or "[]"):
                raise ProductionBlocked("Cannot resolve script branches without an intact final script")
            qa = json.loads(row["output_json"] or "{}").get("qa", {})
            if qa.get("status") != "passed" or type(qa.get("revision")) is not int or not qa.get("script_hash"):
                raise ProductionBlocked("Cannot resolve script branches without the final review")
            checkpoints=[dict(item) for item in db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=?",(job_id,))]
            scoped=_scoped_script_proof(checkpoints)
            if scoped:
                by_stage={item['stage']:item for item in checkpoints}
                if any(not self._valid_files(root,by_stage[name]['files_json'] or '[]')
                       for name in (scoped['accepted_stage'],scoped['review_stage'])):
                    raise ProductionBlocked('Scoped successor requires intact accepted candidate and current review')
            proof = scoped or {"stage": "script", "input_hash": row["input_hash"], "script_hash": qa["script_hash"]}
            for old in checkpoints:
                match = re.fullmatch(r"script_(?:repair|review)/(\d+)", old["stage"])
                if not match or (int(match[1]) <= qa["revision"] and not (scoped and old['status']=='superseded')):
                    continue
                output = json.loads(old["output_json"] or "{}")
                if old["status"] == "superseded" and output.get("superseded_by") == proof:
                    continue
                audit = {"previous_status": old["status"], "previous_input_hash": old["input_hash"],
                         "previous_output": output, "previous_error": json.loads(old["error_json"] or "{}"),
                         "attempts_preserved": old["attempts"], "superseded_by": proof}
                now = utc_now_iso()
                db.execute("UPDATE pipeline_stage_checkpoints SET status='superseded',output_json=?,updated_at=? WHERE job_id=? AND stage=?",
                           (json_dumps(audit), now, job_id, old["stage"]))
                db.execute("UPDATE pipeline_job_steps SET status='superseded',message=?,completed_at=? WHERE job_id=? AND step=?",
                           ("Replaced by a separately validated script branch; previous attempts retained.", now, job_id, old["stage"]))
                _event(db, job_id, old["stage"], "script_branch_superseded", audit)
            db.commit()

    def reconcile_single_pass_voice_checkpoints(self, job_id: str, token: str):
        """Retire old v3 clips only after independently validated full narration."""
        root = self.context(job_id).artifact_dir
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            owner = self._owner(db, job_id, token)
            profile = json.loads(owner["profile_json"])["production"]
            if profile["voice"]["model"] != "eleven_v3":
                raise ProductionBlocked("Only the frozen v3 voice can replace its legacy segment branch")
            rows = [dict(row) for row in db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=?", (job_id,))]
            legacy = [row for row in rows if re.fullmatch(r"(?:voice|voice_qa)/[a-zA-Z0-9_-]+", row["stage"])
                      and not row["stage"].endswith("/narration")]
            if not legacy:
                return 0
            proof = _single_pass_voice_proof(rows)
            by_stage = {row["stage"]: row for row in rows}
            if (not proof or proof["voice_id"] != profile["voice"]["voice_id"]
                    or any(not self._valid_files(root, by_stage[name]["files_json"] or "[]") for name in proof["dependencies"])):
                raise ProductionBlocked("Cannot retire voice clips without intact script, recording, independent QA and final voice evidence")
            voice = json.loads(by_stage["voice"]["output_json"])
            segments = json.loads(by_stage["script"]["output_json"])["segments"]
            duration = voice["duration_seconds"]
            practical_ids = {segment["id"] for segment in segments if segment["type"] == "practical"}
            practical_share = sum(t["duration_sec"] for t in voice["timestamps"] if t["segment_id"] in practical_ids) / duration
            low, high = profile["duration_seconds"]
            if not low <= duration <= high or practical_share < profile["minimum_practical_share"]:
                raise ProductionBlocked("Single-pass voice still fails measured editorial gates")
            from atnos.integrations.claim_evidence_gate import ensure_media_claim_gate
            request_payload = json.loads(owner["request_json"])
            hypothetical_gate = {}
            if (request_payload.get("intake") or {}).get("kind") == "user_hypothetical":
                calculations_row = by_stage.get("script_calculations") or {}
                receipt = None
                if (calculations_row.get("status") == "done" and json.loads(calculations_row.get("files_json") or "[]")
                        and self._valid_files(root, calculations_row.get("files_json") or "[]")):
                    try:
                        receipt = json.loads(calculations_row.get("output_json") or "null")
                    except json.JSONDecodeError:
                        pass
                hypothetical_gate = {"hypothetical_intake": request_payload["intake"], "calculation_receipt": receipt}
            gate = ensure_media_claim_gate(project=request_payload["project"], production_id=job_id,
                                           stage="voice", script=json.loads(by_stage["script"]["output_json"]), **hypothetical_gate)
            if gate.get("blocked"):
                raise ProductionBlocked("Claim evidence does not allow this voice branch replacement")
            ids = {segment["id"] for segment in segments}
            replaced = 0
            for old in legacy:
                if old["stage"].split("/", 1)[1] not in ids:
                    raise ProductionBlocked("An unknown voice dependency cannot be retired by this narration")
                if old["status"] == "superseded" and checkpoint_resolved(old, rows):
                    continue
                prior_audit = None
                if old["status"] == "superseded":
                    prior_audit = json.loads(old["output_json"] or "{}")
                    previous_event = db.execute("SELECT detail_json FROM pipeline_job_events WHERE job_id=? AND stage=? AND event='voice_branch_superseded' ORDER BY rowid DESC LIMIT 1",
                                                (job_id, old["stage"])).fetchone()
                    fingerprint = content_hash(prior_audit)
                    # The general log redactor can mistake long digit runs in a
                    # SHA256 for card data. Eight-character groups keep this
                    # nonsecret audit fingerprint intact without relaxing it.
                    fingerprint = ".".join(fingerprint[index:index + 8] for index in range(0, 64, 8))
                    event_proof = {**prior_audit, "audit_fingerprint": fingerprint}
                    if (previous_event is None or json.loads(previous_event["detail_json"]) != redact_secrets(event_proof)
                            or prior_audit.get("previous_status") not in {"done", "failed"}
                            or prior_audit.get("previous_input_hash") != old["input_hash"]
                            or prior_audit.get("attempts_preserved") != old["attempts"]
                            or prior_audit.get("previous_files_hash") != content_hash(json.loads(old["files_json"] or "[]"))):
                        raise ProductionBlocked("Unproven voice branch retirement cannot be rebound to another recording")
                elif old["status"] not in {"done", "failed"}:
                    raise ProductionBlocked("An active or unresolved voice branch cannot be superseded")
                if old["files_json"] and json.loads(old["files_json"]) and not self._valid_files(root, old["files_json"]):
                    raise ProductionBlocked("Legacy voice evidence changed before branch retirement")
                if prior_audit is not None:
                    audit = {**prior_audit, "superseded_by": proof,
                             "superseded_history": [*prior_audit.get("superseded_history", []), prior_audit["superseded_by"]]}
                else:
                    audit = {"previous_status": old["status"], "previous_input_hash": old["input_hash"],
                             "previous_output": json.loads(old["output_json"] or "{}"),
                             "previous_error": json.loads(old["error_json"] or "{}"),
                             "previous_files_hash": content_hash(json.loads(old["files_json"] or "[]")),
                             "attempts_preserved": old["attempts"], "superseded_by": proof}
                now = utc_now_iso()
                db.execute("UPDATE pipeline_stage_checkpoints SET status='superseded',output_json=?,updated_at=? WHERE job_id=? AND stage=?",
                           (json_dumps(audit), now, job_id, old["stage"]))
                db.execute("UPDATE pipeline_job_steps SET status='superseded',message=?,completed_at=? WHERE job_id=? AND step=?",
                           ("Replaced by independently verified complete v3 narration; earlier attempts and errors retained.", now, job_id, old["stage"]))
                fingerprint = content_hash(audit)
                fingerprint = ".".join(fingerprint[index:index + 8] for index in range(0, 64, 8))
                _event(db, job_id, old["stage"], "voice_branch_superseded", {**audit, "audit_fingerprint": fingerprint})
                replaced += 1
            db.commit()
            return replaced

    def accept_revalidated_scene_plan(self, job_id: str, token: str, *, expected_input_hash: str,
                                      output: dict, files: list[Path], proof: dict):
        entries = self._files(self.context(job_id).artifact_dir, files)
        if not entries or not proof.get("source_sha256") or not proof.get("validator_sha256"):
            raise ValueError("Revalidation requires the original response and new validator evidence")
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, job_id, token)
            row = db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage='asset_planning'", (job_id,)).fetchone()
            if (row is None or row["status"] != "failed" or row["input_hash"] != expected_input_hash
                    or json.loads(row["error_json"] or "{}").get("category") != "invalid_output"):
                raise OrderConflict("Only the same rejected scene plan can be revalidated")
            now = utc_now_iso()
            db.execute("UPDATE pipeline_stage_checkpoints SET status='done',output_json=?,files_json=?,error_json=NULL,updated_at=? WHERE job_id=? AND stage='asset_planning'",
                       (json_dumps(output), json_dumps(entries), now, job_id))
            db.execute("UPDATE pipeline_job_steps SET status='done',progress=1,message=NULL,completed_at=? WHERE job_id=? AND step='asset_planning'", (now, job_id))
            db.execute("UPDATE pipeline_jobs SET status='queued',error=NULL,updated_at=? WHERE id=?", (now, job_id))
            _event(db, job_id, "asset_planning", "output_revalidated", proof)
            db.commit()

    def restore_overwritten_asset_plan(self, job_id: str, *, reason: str) -> dict:
        """Repair only a proven historical dependency-block overwrite, without dispatch.

        This is explicitly invoked maintenance. It does not approve scene assets,
        clear the order's dependency block, regenerate output or refund attempts.
        """
        if not reason or len(reason.strip()) < 8:
            raise ValueError("A checkpoint restoration requires an audit reason")
        categories = {"required_assets_missing", "awaiting_asset_review"}
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT * FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
            job = db.execute("SELECT * FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone()
            if owner is None or job is None:
                raise KeyError("Production order not found")
            if _same_process(owner["lease_pid"], owner["lease_process_started"]):
                raise OrderConflict("Cannot restore a checkpoint while a worker is active")
            row = db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage='asset_planning'",
                             (job_id,)).fetchone()
            if row is not None and row["status"] == "done":
                return {"restored": False, "reason": "already_done", "job_id": job_id}
            try:
                job_error = json.loads(job["error"] or "{}")
                checkpoint_error = json.loads(row["error_json"] or "{}") if row else {}
                output = json.loads(row["output_json"] or "{}") if row else {}
                files = json.loads(row["files_json"] or "[]") if row else []
            except (ValueError, TypeError) as exc:
                raise OrderConflict("Checkpoint restoration evidence is malformed") from exc
            renderer_dependency = (
                isinstance(checkpoint_error, dict) and checkpoint_error.get('category') == 'execution_failure'
                and re.fullmatch(r'Renderer-only correction requires intact cached upstream stage: asset_observation/[a-zA-Z0-9_-]{1,100}',
                                 str(checkpoint_error.get('message') or '')) is not None)
            if (job["status"] != "blocked" or row is None or row["status"] != "failed"
                    or not isinstance(job_error, dict) or not isinstance(checkpoint_error, dict)
                    or (checkpoint_error.get("category") not in categories and not renderer_dependency)
                    or job_error.get("category") != checkpoint_error.get("category")):
                raise OrderConflict("Only a dependency-block overwrite can restore the scene plan")
            if not isinstance(output, dict) or not output or not isinstance(files, list) or not files:
                raise OrderConflict("Completed scene plan output and file evidence are required")
            root = Path(job["artifact_dir"]).resolve()
            if not root.is_relative_to((self.settings.data_dir / "production").resolve()):
                raise OrderConflict("Scene plan directory is outside production storage")
            if not self._valid_files(root, row["files_json"]):
                raise OrderConflict("Scene plan files no longer match their completed hashes")
            if renderer_dependency:
                # The known outer-phase failure changed only status/error of
                # an already completed plan. Require the real registered scope
                # and every original upstream byte/attempt before restoring it.
                from atnos.production.remotion_quality_repair import (
                    _latest_refresh, _verified_refresh_row, renderer_refresh_upstream_hash)
                from atnos.production.channels.wealth_blueprint.remotion_order_edit import renderer_source_fingerprint
                state = self.get(job_id)
                registered = _latest_refresh(state)
                if registered is None:
                    raise OrderConflict('Renderer dependency overwrite requires its registered correction')
                refresh = _verified_refresh_row(self, root, registered, state)
                normalized = copy.deepcopy(state['checkpoints'])
                next(item for item in normalized if item['stage'] == 'asset_planning')['status'] = 'done'
                if (refresh['renderer_source_fingerprint'] != renderer_source_fingerprint()
                        or renderer_refresh_upstream_hash(normalized) !=
                           renderer_refresh_upstream_hash(refresh.get('before_checkpoints', []))):
                    raise OrderConflict('Renderer dependency overwrite changed original upstream evidence')
            events = db.execute(
                "SELECT * FROM pipeline_job_events WHERE job_id=? AND stage='asset_planning' "
                "AND event IN ('stage_started','stage_completed','stage_failed') ORDER BY id", (job_id,)
            ).fetchall()
            completed = next((event for event in reversed(events) if event["event"] == "stage_completed"), None)
            failed = events[-1] if events else None
            try:
                completed_detail = json.loads(completed["detail_json"]) if completed else {}
                failed_detail = json.loads(failed["detail_json"]) if failed else {}
            except (ValueError, TypeError) as exc:
                raise OrderConflict("Scene plan completion history is malformed") from exc
            if (completed is None or failed is None or failed["event"] != "stage_failed"
                    or failed["id"] <= completed["id"]
                    or not isinstance(completed_detail, dict) or completed_detail.get("files") != files
                    or not isinstance(failed_detail, dict) or failed_detail != checkpoint_error
                    or any(event["event"] == "stage_started" and event["id"] > completed["id"] for event in events)):
                raise OrderConflict("No matching uninterrupted completion precedes the dependency overwrite")
            step = db.execute("SELECT * FROM pipeline_job_steps WHERE job_id=? AND step='asset_planning'",
                              (job_id,)).fetchone()
            if step is None or step["attempt"] != row["attempts"]:
                raise OrderConflict("Scene plan attempt history does not match its checkpoint")
            proof = {
                "reason": reason.strip(), "previous_error": checkpoint_error,
                "completion_event_id": completed["id"], "overwrite_event_id": failed["id"],
                "input_hash_preserved": row["input_hash"], "output_hash": content_hash(output),
                "files": files, "attempts_preserved": row["attempts"],
                "provider_calls_preserved": owner["provider_calls"],
                "reserved_cost_units_preserved": owner["reserved_cost_units"],
                "dependency_block_preserved": True,
            }
            now = utc_now_iso()
            db.execute("UPDATE pipeline_stage_checkpoints SET status='done',error_json=NULL,updated_at=? "
                       "WHERE job_id=? AND stage='asset_planning'", (now, job_id))
            db.execute("UPDATE pipeline_job_steps SET status='done',progress=1,message=NULL,completed_at=? "
                       "WHERE job_id=? AND step='asset_planning'", (completed["created_at"], job_id))
            _event(db, job_id, "asset_planning", "dependency_overwrite_restored", proof)
            db.commit()
        return {"restored": True, "job_id": job_id, "proof": proof}

    def finish_script_preview(self, job_id: str, token: str):
        root = self.context(job_id).artifact_dir
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            owner = self._owner(db, job_id, token)
            request = json.loads(owner['request_json'])
            if request.get('production_scope') != 'script_preview' or request.get('delivery_mode') != 'local_only':
                raise ProductionBlocked('Only an explicitly local story preview can finish without media gates')
            rows = [dict(row) for row in db.execute('SELECT * FROM pipeline_stage_checkpoints WHERE job_id=?', (job_id,))]
            script = next((r for r in rows if r['stage'] == 'script' and r['status'] == 'done'), None)
            if (not script or owner['reserved_cost_units'] != 0
                    or any(not checkpoint_resolved(row, rows) or not self._valid_files(root, row['files_json'] or '[]') for row in rows)):
                raise ProductionBlocked('Story preview has missing, stale or paid generation evidence')
            payload = json.loads(script['output_json'])
            if (payload.get('qa', {}).get('status') != 'passed' or payload.get('media_generated') is not False
                    or payload.get('production_scope') != 'script_preview'):
                raise ProductionBlocked('Story preview has not passed its script review')
            from atnos.production.fiction_script import StoryScript, script_issues
            story = StoryScript.model_validate({key: payload[key] for key in StoryScript.model_fields})
            qa = payload['qa']
            review = next((r for r in rows if r['stage'] == f"script_review/{qa.get('revision')}" and r['status'] == 'done'), None)
            if (qa.get('script_hash') != content_hash(story.model_dump(mode='json')) or not review
                    or json.loads(review['output_json']) != qa
                    or script_issues(story, json.loads(owner['profile_json'])['production'], request)):
                raise ProductionBlocked('Story preview review does not match the final script')
            db.execute("UPDATE pipeline_jobs SET status='script_ready',current_step='script_ready',error=NULL,updated_at=? WHERE id=?", (utc_now_iso(), job_id))
            _event(db, job_id, 'script', 'script_preview_completed', {'media_generated': False, 'publish': False})
            db.commit()
        return self.get(job_id)

    def finish(self, job_id: str, token: str, package_path: Path):
        root = self.context(job_id).artifact_dir
        package = self._files(root, [package_path])
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            owner = self._owner(db, job_id, token)
            if json.loads(owner['request_json']).get('production_scope') == 'script_preview':
                raise ProductionBlocked('A story script preview cannot become a completed film')
            if token.startswith("script-only:"):
                raise ProductionBlocked("Script-only preparation is not a completed production")
            required = json.loads(owner["profile_json"])["production"]["required_gates"]
            failures = []
            for gate in required:
                stage = REQUIRED_GATE_STAGES.get(gate)
                row = db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?", (job_id, stage)).fetchone() if stage else None
                if row is None or row["status"] != "done" or not self._valid_files(root, row["files_json"] or "[]"):
                    failures.append(f"{gate}: missing or stale evidence")
                    continue
                output = json.loads(row["output_json"])
                verdict = output.get("qa", output)
                if not isinstance(verdict, dict) or verdict.get("status") != "passed":
                    failures.append(f"{gate}: not passed")
            rows = [dict(row) for row in db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=?", (job_id,))]
            package_row = next((row for row in rows if row["stage"] == "package"), None)
            if package_row:
                packaged = json.loads(package_row["output_json"] or "{}")
                if packaged.get("checkpoint_fingerprints") != checkpoint_fingerprints(rows):
                    failures.append("package: dependency evidence changed after packaging")
                if package[0] not in json.loads(package_row["files_json"] or "[]"):
                    failures.append("package: supplied file was not verified by the package stage")
                packaged_files = json.loads(package_row["files_json"] or "[]")
                for row in rows:
                    if row["stage"] == "package":
                        continue
                    if not checkpoint_resolved(row, rows):
                        failures.append(f"package: unfinished dependency {row['stage']}")
                    if any(entry not in packaged_files for entry in json.loads(row["files_json"] or "[]")):
                        failures.append(f"package: missing dependency files for {row['stage']}")
            if failures:
                db.execute("UPDATE pipeline_jobs SET status='blocked',current_step='qa',error=?,updated_at=? WHERE id=?",
                           (json_dumps({"category": "qa_incomplete", "failures": failures}), utc_now_iso(), job_id))
                _event(db, job_id, "qa", "completion_rejected", {"failures": failures})
            else:
                db.execute("UPDATE pipeline_jobs SET status='ready',current_step='ready',error=NULL,upload_package_path=?,updated_at=? WHERE id=?",
                           (str(package_path), utc_now_iso(), job_id))
                _event(db, job_id, "ready", "production_ready", {"gates": REQUIRED_GATE_STAGES, "package": package})
            db.commit()
        if failures:
            raise ProductionBlocked("Required QA gates did not pass: " + "; ".join(failures))
        return self.get(job_id)

    def block(self, job_id: str, token: str, current_step: str, *, category: str, message: str):
        """Report an unmet dependency without invalidating completed stage evidence.

        The UI step can name an earlier phase (for example asset_planning) even
        when that phase's plan has already been completed. Unlike fail(), this
        does not describe a failed execution attempt or alter its checkpoint.
        """
        error = redact_secrets({"category": category, "message": message[:2000]})
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, job_id, token)
            db.execute("UPDATE pipeline_jobs SET status='blocked',current_step=?,error=?,updated_at=? WHERE id=?",
                       (current_step, json_dumps(error), utc_now_iso(), job_id))
            _event(db, job_id, current_step, "blocked", error)
            db.commit()

    def reconcile_abandoned_local_stage(self, job_id, token, stage, *, expected_input_hash,
                                       evidence_path: Path, evidence_sha256: str):
        """Consume a verified local one-shot outcome without another begin/dispatch."""
        root = self.context(job_id).artifact_dir
        evidence_path = Path(evidence_path).resolve()
        if not evidence_path.is_relative_to(root) or file_hash(evidence_path) != evidence_sha256:
            raise OrderConflict('Reconciliation evidence changed or escaped the order')
        record = json.loads(evidence_path.read_text('utf8'))
        if (record.get('version') != 'wb-flux-outcome-reconciliation-v1'
                or record.get('outcome') != 'abandoned_without_product'
                or record.get('flux_retry_permitted') is not False
                or record.get('job_id') != job_id or record.get('stage') != stage
                or record.get('digest') != content_hash({k:v for k,v in record.items() if k != 'digest'})):
            raise OrderConflict('Invalid reconciliation evidence contract')
        with connect(self.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            owner = self._owner(db, job_id, token)
            job = db.execute('SELECT status,error FROM pipeline_jobs WHERE id=?', (job_id,)).fetchone()
            row = db.execute('SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?', (job_id,stage)).fetchone()
            if (json.loads(owner['request_json']).get('delivery_mode') != 'local_only'
                    or not job or job['status'] != 'blocked'
                    or json.loads(job['error'] or '{}').get('category') != 'execution_outcome_unknown'
                    or not row or row['status'] != 'running' or row['input_hash'] != expected_input_hash
                    or row['attempts'] != record['attempts'] or record['input_hash'] != expected_input_hash
                    or file_hash(evidence_path) != evidence_sha256):
                raise OrderConflict('Interrupted local stage changed before reconciliation')
            error = {'category':'local_operation_abandoned',
                     'message':'Lokalna generacja została przerwana bez zapisanego produktu. Stara próba pozostaje zużyta; wznowienie może użyć autoryzowanej ścieżki lokalnej.',
                     'evidence_path':str(evidence_path), 'evidence_sha256':evidence_sha256,
                     'flux_retry_permitted':False}
            now = utc_now_iso()
            db.execute("UPDATE pipeline_stage_checkpoints SET status='failed',error_json=?,updated_at=? WHERE job_id=? AND stage=?", (json_dumps(error),now,job_id,stage))
            db.execute("UPDATE pipeline_job_steps SET status='error',message=?,completed_at=? WHERE job_id=? AND step=?", (json_dumps(error),now,job_id,stage))
            db.execute("UPDATE pipeline_jobs SET error=?,updated_at=? WHERE id=?", (json_dumps(error),now,job_id))
            _event(db,job_id,stage,'local_operation_reconciled',error)
            db.commit()

    def fail(self, job_id: str, token: str, stage: str, *, category: str, message: str):
        error = redact_secrets({"category": category, "message": message[:2000]})
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, job_id, token)
            db.execute("UPDATE pipeline_stage_checkpoints SET status='failed',error_json=?,updated_at=? WHERE job_id=? AND stage=?", (json_dumps(error), utc_now_iso(), job_id, stage))
            db.execute("UPDATE pipeline_jobs SET status='blocked',current_step=?,error=?,updated_at=? WHERE id=?", (stage, json_dumps(error), utc_now_iso(), job_id))
            db.execute("UPDATE pipeline_job_steps SET status='error',message=?,completed_at=? WHERE job_id=? AND step=?", (json_dumps(error), utc_now_iso(), job_id, stage))
            _event(db, job_id, stage, "stage_failed", error)
            db.commit()
