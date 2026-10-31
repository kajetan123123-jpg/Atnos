"""Accept an explicit edited storyboard without resetting failed model attempts."""
from __future__ import annotations

import json
from pathlib import Path

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.models import StepResult
from atnos.production.order_store import ProductionOrderStore, OrderConflict, ProductionBlocked, _event, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json


def admit(settings, job_id: str, source: Path, *, approved=False):
    from atnos.production.steps.order_assets import ScenePlan, validate_plan
    from atnos.production.steps.order_script import EditorialScript

    if not approved:
        raise ValueError("An explicit storyboard handoff is required")
    store = ProductionOrderStore(settings)
    state, context = store.get(job_id), store.context(job_id)
    if state["worker_live"] or state["status"] != "blocked" or state["current_step"] != "asset_planning":
        raise OrderConflict("Only an idle production blocked on its storyboard can accept this edit")
    rows = {row["stage"]: row for row in state["checkpoints"]}
    for stage in ("script", "asset_library", "asset_ranking"):
        row = rows.get(stage, {})
        if row.get("status") != "done" or not store._valid_files(context.artifact_dir, row.get("files_json", "[]")):
            raise ValueError(f"Missing intact {stage} evidence")
    if any(stage.startswith(("voice", "render", "semantic")) for stage in rows):
        raise OrderConflict("Cannot change a storyboard after media production started")
    document = json.loads(source.read_bytes())
    if document["job_id"] != job_id or not document.get("editor") or not document.get("reason"):
        raise ValueError("Storyboard edit must identify its existing order, editor and reason")
    script = EditorialScript.model_validate({key: rows["script"]["output"][key] for key in ("title", "description", "segments", "claims")})
    plan = ScenePlan.model_validate({"scenes": document["scenes"]})
    validate_plan(plan, script, rows["asset_library"]["output"])
    with store.lease(job_id) as token, connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        store._owner(db, job_id, token)
        db.execute("CREATE TABLE IF NOT EXISTS production_scene_revisions(job_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,file_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)")
        if db.execute("SELECT 1 FROM production_scene_revisions WHERE job_id=?", (job_id,)).fetchone():
            raise OrderConflict("An edited storyboard was already accepted")
        old = dict(db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage='asset_planning'", (job_id,)).fetchone())
        if old["status"] != "failed":
            raise OrderConflict("Only failed storyboard generation can be superseded")
        payload = {"scenes": plan.model_dump(mode="json")["scenes"], "superseded": old,
            "script_hash": content_hash(script.model_dump(mode="json")),
            "library_hash": content_hash(rows["asset_library"]["output"]),
            "source_sha256": file_hash(source), "editor": document["editor"], "reason": document["reason"],
            "plan_validation": "passed", "visual_qa": "not_started", "attempts_reset": False}
        path = context.artifact_dir / "editorial" / "storyboard-accepted.json"
        write_json(path, payload)
        db.execute("INSERT INTO production_scene_revisions VALUES(?,?,?,?)", (job_id,json_dumps(payload),file_hash(path),utc_now_iso()))
        db.execute("DELETE FROM pipeline_stage_checkpoints WHERE job_id=? AND stage='asset_planning'", (job_id,))
        db.execute("UPDATE pipeline_job_steps SET status='superseded' WHERE job_id=? AND step='asset_planning'", (job_id,))
        db.execute("UPDATE pipeline_jobs SET status='queued',current_step='storyboard_accepted',error=NULL,updated_at=? WHERE id=?", (utc_now_iso(),job_id))
        _event(db,job_id,"storyboard_accepted","edited_storyboard_accepted",{"source_sha256":file_hash(source),"archived_attempts":old["attempts"],"budgets_reset":False})
        db.commit()
    return {"job_id": job_id,"status":"accepted","scenes":len(plan.scenes)}


async def produce_accepted(executor, script, library, ranking):
    from atnos.production.steps.order_assets import ScenePlan, validate_plan, write_scene_plan

    with connect(executor.store.settings) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_scene_revisions'").fetchone():
            return None
        row = db.execute("SELECT * FROM production_scene_revisions WHERE job_id=?",(executor.job_id,)).fetchone()
    if not row:
        return None
    path = executor.context.artifact_dir / "editorial" / "storyboard-accepted.json"
    payload = json.loads(row["payload_json"])
    if not path.is_file() or file_hash(path) != row["file_sha256"] or json.loads(path.read_text(encoding="utf-8")) != payload:
        raise ProductionBlocked("Edited storyboard evidence is missing or changed")
    if payload["script_hash"] != content_hash(script.model_dump(mode="json")) or payload["library_hash"] != content_hash(library):
        raise ProductionBlocked("Edited storyboard inputs changed")
    plan = ScenePlan.model_validate({"scenes":payload["scenes"]})
    validate_plan(plan, script, library)
    async def emit():
        result = write_scene_plan(executor.context, script, plan, {**ranking,"method":"explicit_editorial_storyboard_with_verified_library"})
        return StepResult(result.artifact_name,result.artifact_path,result.payload,result.message,(path,))
    return await executor.run("asset_planning",{"accepted_storyboard_sha256":file_hash(path)},emit)
