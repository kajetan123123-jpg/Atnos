from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import psutil

from atnos.intelligence.storage import connect
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore
from atnos.production.dispatch_lock import dispatch_lock


class ProductionOrderDispatcher:
    """The persisted order is authoritative; the subprocess is a replaceable worker."""

    def __init__(self, settings):
        self.settings = settings
        self.store = ProductionOrderStore(settings)

    def dispatch(self, job_id: str) -> dict:
        lock_path = self.store.context(job_id).artifact_dir / "worker_logs" / "dispatch.lock"
        with dispatch_lock(lock_path):
            return self._dispatch_locked(job_id)

    def _dispatch_locked(self, job_id: str) -> dict:
        state = self.store.get(job_id)
        from atnos.production.studio_script_recovery import repair_execution_scope
        scope = repair_execution_scope(self.store, job_id)
        command = 'prepare-script' if scope == 'script_only' else 'run'
        if state["request"].get("intake") is not None and not state["can_resume"]:
            return {**order_status(state), "worker_requested": False}
        if state["worker_live"] or state["status"] in {"ready", "script_ready"}:
            return {**order_status(state), "worker_requested": False}
        if not state["can_resume"]:
            try:
                error = json.loads(state.get("error") or "{}")
            except (ValueError, TypeError):
                error = {}
            if isinstance(error, dict) and error.get("category") == "execution_outcome_unknown":
                raise ProductionBlocked("Nie można wznowić: wynik wysłanej operacji wymaga sprawdzenia. Nie uruchomiono drugiego workera.")
            raise ProductionBlocked("Limit produkcji zostal wyczerpany. Potrzebna jest ocena przyczyny, nie reset licznika.")
        api_root = Path(__file__).resolve().parents[3]
        worker = api_root / "scripts" / "run_production_order.py"
        if not worker.is_file():
            raise FileNotFoundError("Production worker entry point is missing")
        # Recover the Popen-to-record crash window, including an importing child.
        for candidate in psutil.process_iter():
            argv = []
            try:
                argv = candidate.cmdline()
                if len(argv) < 4 or argv[-2:] != [command, job_id] or Path(argv[1]).resolve() != worker:
                    continue
                child_data = candidate.environ().get("DATA_DIR")
                if not child_data or Path(child_data).resolve() != self.settings.data_dir.resolve():
                    continue
                self.store.record_dispatched_worker(job_id, candidate.pid)
                return {**order_status(self.store.get(job_id)), "worker_requested": False,
                        "reattached_pid": candidate.pid}
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                if len(argv) >= 4 and argv[-2:] == [command, job_id]:
                    raise ProductionBlocked("Existing worker identity cannot be verified") from exc
        log_path = self.store.context(job_id).artifact_dir / "worker_logs" / f"{uuid4().hex}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["DATA_DIR"] = str(self.settings.data_dir.resolve())
        env["PYTHONUNBUFFERED"] = "1"
        # Secrets stay in the environment/vault; never serialize Settings into job files or argv.
        with log_path.open("ab") as log:
            process = subprocess.Popen([sys.executable, str(worker), command, job_id], cwd=str(api_root), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.store.record_dispatched_worker(job_id, process.pid)
        return {**order_status(self.store.get(job_id)), "worker_requested": True, "launch_pid": process.pid}

    def list_orders(self, *, limit=30) -> list[dict]:
        with connect(self.settings) as db:
            ids = [r["job_id"] for r in db.execute("SELECT job_id FROM pipeline_job_orders ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),))]
        from atnos.production.order_recovery import recover_interrupted_orders

        recover_interrupted_orders(self.settings, job_ids=ids)
        return [order_status(self.store.get(job)) for job in ids]


def order_status(state: dict) -> dict:
    from atnos.production.remotion_quality_repair import correction_progress
    blockers = _blockers_from_error(state.get("error")) if state["status"] != "ready" else []
    steps = [{"step": row["stage"], "status": row["status"], "progress": 1 if row["status"] == "done" else 0,
              "attempt": row["attempts"], "message": row["error_json"], "started_at": None,
              "completed_at": row["updated_at"] if row["status"] == "done" else None} for row in state["checkpoints"]]
    from atnos.production.project_content_brain import resolve_publication_status
    pub = state.get("profile", {}).get("publication_status") or resolve_publication_status(
        production_status=state.get("status"),
        delivery_mode=(state.get("request") or {}).get("delivery_mode"),
    )
    history = (state.get("profile") or {}).get("history_decision")
    return {key: state[key] for key in ("id", "account_id", "topic_id", "status", "current_step", "request",
        "profile_hash", "worker_live", "can_resume", "reserved_cost_units", "provider_calls", "created_at",
        "updated_at", "error", "artifact_dir", "upload_package_path")} | {
        "topic_title": state["request"].get("topic"),
        "topic_description": state["request"].get("direction"),
        "budget_limit_usd": state["profile"]["production"]["max_cost_usd"],
        "max_provider_calls": state.get('max_provider_calls', state['profile']['production']['max_provider_calls']),
        "local_work_unlimited": state.get('local_work_unlimited', False),
        "limited_provider_calls": state.get('limited_provider_calls', state['provider_calls']),
        "can_extend_call_limit": state.get('can_extend_call_limit', False),
        "autonomous_recovery": state.get('autonomous_recovery', {'enabled':False}),
        "ready_for_review": state["status"] == "ready",
        "ready_for_private_upload": False,
        "ready_for_public_promotion": False,
        "review_status": "ready" if state["status"] == "ready" else None,
        "quality_review_status": "passed" if state["status"] == "ready" else state.get("current_step"),
        "private_upload_blockers": blockers or [str(state.get("current_step") or "not_ready")],
        "public_promotion_blockers": ["manual_publication_disabled_by_profile"],
        "publication_status": pub.get("publication_status") if isinstance(pub, dict) else pub,
        "publication_status_detail": pub if isinstance(pub, dict) else None,
        "history_decision": history,
        "completed_stages": [row["stage"] for row in state["checkpoints"] if row["status"] == "done"],
        "stages": [{"stage": row["stage"], "status": row["status"], "attempts": row["attempts"],
                    "error": row["error_json"]} for row in state["checkpoints"]],
        "steps": steps,
        "current_correction": correction_progress(state),
        "package": next((r["output"] for r in state["checkpoints"] if r["stage"] == "package" and r["status"] == "done"), None)
                   if state["status"] == "ready" else None,
    }


def _blockers_from_error(value) -> list[str]:
    if not value:
        return []
    if not isinstance(value, str):
        return [str(value)]
    if not value.startswith("{"):
        return [value]
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed.get("failures"), list):
        return [str(item) for item in parsed["failures"] if str(item).strip()]
    message = parsed.get("message")
    if isinstance(message, str):
        try:
            nested = json.loads(message)
        except json.JSONDecodeError:
            return [message]
        if isinstance(nested, list):
            return [f"{item.get('code', 'blocked')}: {item.get('detail', '')}".strip() for item in nested if isinstance(item, dict)]
    return [str(parsed.get("category") or "blocked")]
