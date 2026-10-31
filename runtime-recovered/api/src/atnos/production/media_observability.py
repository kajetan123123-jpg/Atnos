"""Atnos Media Production observability — one aggregated status surface.

Reuses existing Comfy health, GPU lock, Asset Brain metrics, order store,
channel caches, and generation metadata. No Prometheus/Grafana required.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from atnos.core.config import Settings
from atnos.core.logging import redact_secrets
from atnos.production.comfy_channels.model_profiles import load_model_profile
from atnos.production.comfy_channels.profiles import CHANNEL_IDS, generated_root
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

AlertState = Literal["HEALTHY", "DEGRADED", "BLOCKED", "ERROR"]


@dataclass
class ObservabilitySimulate:
    """Test/fixture overrides — never used in production unless explicitly passed."""

    comfy_down: bool = False
    gpu_locked: bool = False
    gpu_owner: str = "sim:locked"
    cache_miss_only: bool = False
    asset_rejected: bool = False
    quality_gate_failure: bool = False
    model_fallback: bool = False


def _alert_rank(state: AlertState) -> int:
    return {"HEALTHY": 0, "DEGRADED": 1, "BLOCKED": 2, "ERROR": 3}[state]


def _worst(*states: AlertState) -> AlertState:
    return max(states, key=_alert_rank) if states else "HEALTHY"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _comfy_snapshot(settings: Settings, *, simulate: ObservabilitySimulate | None) -> dict[str, Any]:
    if simulate and simulate.comfy_down:
        return {
            "status": "ERROR",
            "alert": "ERROR",
            "reachable": False,
            "pid": None,
            "uptime_s": None,
            "last_restart": None,
            "port": None,
            "startup_time_s": None,
            "base_url": str(settings.comfyui_base_url),
            "detail": "simulated ComfyUI down",
        }
    runtime = ComfyUiRuntimeService(settings)
    reachable = False
    detail = ""
    try:
        reachable = bool(runtime._server_ready())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        detail = redact_secrets(str(exc))[:240]
    port = None
    try:
        from urllib.parse import urlparse

        port = urlparse(str(settings.comfyui_base_url)).port
    except Exception:
        port = None
    # Managed marker / process info when available
    pid = None
    uptime_s = None
    last_restart = None
    startup_time_s = None
    try:
        state = runtime.describe_install()
        marker = getattr(state, "instance_marker_path", None) or getattr(state, "marker_path", None)
        # Best-effort: probe process list via runtime helpers if present
        if hasattr(runtime, "managed_process_info"):
            info = runtime.managed_process_info()  # type: ignore[attr-defined]
            if isinstance(info, dict):
                pid = info.get("pid")
                uptime_s = info.get("uptime_s")
                last_restart = info.get("started_at")
                startup_time_s = info.get("startup_time_s")
        _ = marker
    except Exception:
        pass
    alert: AlertState = "HEALTHY" if reachable else "BLOCKED"
    return {
        "status": "up" if reachable else "down",
        "alert": alert,
        "reachable": reachable,
        "pid": pid,
        "uptime_s": uptime_s,
        "last_restart": last_restart,
        "port": port,
        "startup_time_s": startup_time_s,
        "base_url": str(settings.comfyui_base_url),
        "detail": detail or ("reachable" if reachable else "API not ready — HTTP 200 alone is insufficient for HEALTHY fleet"),
    }


def _gpu_snapshot(settings: Settings, *, simulate: ObservabilitySimulate | None) -> dict[str, Any]:
    lock = GpuRuntimeLockService(settings)
    snap = dict(lock.snapshot())
    if simulate and simulate.gpu_locked:
        snap = {
            "locked": True,
            "owner": simulate.gpu_owner,
            "process_id": None,
            "created_at_epoch": time.time() - 120,
            "duration_s": 120.0,
            "owner_alive": True,
            "summary": f"{simulate.gpu_owner} for 120s (simulated)",
        }
    alert: AlertState = "HEALTHY"
    if snap.get("locked"):
        # Long lock is DEGRADED (busy), dead owner is ERROR
        if snap.get("owner_alive") is False:
            alert = "ERROR"
        elif (snap.get("duration_s") or 0) > 900:
            alert = "DEGRADED"
        else:
            alert = "DEGRADED"  # busy is not HEALTHY idle — operator must see work in progress
    return {
        **snap,
        "alert": alert,
        "queue": [],  # single-slot lock — queue is implicit waiters
        "last_job": snap.get("owner"),
        "job_duration_s": snap.get("duration_s"),
        "current_owner": snap.get("owner"),
    }


def _cache_stats(settings: Settings, *, simulate: ObservabilitySimulate | None) -> dict[str, Any]:
    root = generated_root(settings)
    per_channel: dict[str, Any] = {}
    hits = misses = 0
    for channel in CHANNEL_IDS:
        cache_path = root / channel / "_cache.json"
        ch_hits = ch_total = 0
        if cache_path.is_file():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    ch_total = len(payload)
                    # Entries that still have output on disk count as lasting hits
                    for entry in payload.values():
                        if isinstance(entry, dict) and Path(str(entry.get("output_path") or "")).is_file():
                            ch_hits += 1
            except (OSError, json.JSONDecodeError):
                pass
        ch_misses = max(0, ch_total - ch_hits)
        hits += ch_hits
        misses += ch_misses
        per_channel[channel] = {
            "entries": ch_total,
            "hits": ch_hits,
            "misses": ch_misses,
            "hit_rate": round(ch_hits / ch_total, 4) if ch_total else None,
        }
    if simulate and simulate.cache_miss_only:
        hits, misses = 0, max(misses, 10)
        for channel in per_channel:
            per_channel[channel] = {**per_channel[channel], "hits": 0, "misses": 10, "hit_rate": 0.0}
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / total, 4) if total else None,
        "per_channel": per_channel,
        "alert": "DEGRADED" if (simulate and simulate.cache_miss_only) else "HEALTHY",
    }


def _asset_brain_stats(settings: Settings, *, simulate: ObservabilitySimulate | None) -> dict[str, Any]:
    try:
        from atnos.production.asset_brain.approval import AssetApprovalService
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain

        brain = WealthBlueprintAssetBrain(settings)
        metrics = AssetApprovalService(brain).metrics()
    except Exception as exc:  # noqa: BLE001
        return {
            "alert": "DEGRADED",
            "detail": redact_secrets(str(exc))[:200],
            "generated": 0,
            "awaiting_review": 0,
            "approved": 0,
            "rejected": 0,
            "approval_rate": None,
        }
    if simulate and simulate.asset_rejected:
        metrics = {
            **metrics,
            "rejected_count": max(int(metrics.get("rejected_count") or 0), 3),
            "approval_rate": 0.25,
        }
    approved = int(metrics.get("approved_count") or 0)
    rejected = int(metrics.get("rejected_count") or 0)
    awaiting = int(metrics.get("awaiting_review_count") or 0)
    rate = metrics.get("approval_rate")
    alert: AlertState = "HEALTHY"
    if rate is not None and rate < 0.4 and (approved + rejected) >= 5:
        alert = "DEGRADED"
    if simulate and simulate.asset_rejected:
        alert = "DEGRADED"
    # Character approval rate
    character_approved = character_decided = 0
    try:
        for asset in brain.catalog.assets:
            if asset.type != "character":
                continue
            if asset.quality_status == "APPROVED":
                character_approved += 1
                character_decided += 1
            elif asset.quality_status == "REJECTED":
                character_decided += 1
    except Exception:
        pass
    return {
        "alert": alert,
        "generated": int(metrics.get("generated_count") or 0),
        "awaiting_review": awaiting,
        "approved": approved,
        "rejected": rejected,
        "approval_rate": rate,
        "character_approval_rate": round(character_approved / character_decided, 4) if character_decided else None,
        "wb_asset_approval_rate": rate,
        "reject_reasons": metrics.get("reject_reasons") or {},
        "by_status": metrics.get("by_status") or {},
    }


def _quality_stats(settings: Settings, *, simulate: ObservabilitySimulate | None) -> dict[str, Any]:
    root = generated_root(settings)
    validated = fail = 0
    reject_reasons: dict[str, int] = {}
    per_channel: dict[str, Any] = {}
    dc_pass = dc_total = 0
    retries_before_accept: list[int] = []
    gen_seconds: list[float] = []

    for channel in CHANNEL_IDS:
        c_ok = c_fail = 0
        channel_root = root / channel
        if channel_root.is_dir():
            for meta_path in channel_root.rglob("metadata.json"):
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                settings_block = meta.get("generation_settings") if isinstance(meta, dict) else None
                if not isinstance(settings_block, dict):
                    continue
                gate = settings_block.get("quality_gate")
                verdict = str(settings_block.get("validation_verdict") or "")
                if isinstance(gate, dict):
                    if gate.get("passed") or verdict == "VALIDATED":
                        validated += 1
                        c_ok += 1
                    else:
                        fail += 1
                        c_fail += 1
                        for check in gate.get("checks") or []:
                            if isinstance(check, dict) and not check.get("ok"):
                                name = str(check.get("name") or "unknown")
                                reject_reasons[name] = reject_reasons.get(name, 0) + 1
                dc_qc = settings_block.get("dc_visual_qc")
                if channel == "dark_chronicles" and isinstance(dc_qc, dict):
                    dc_total += 1
                    if dc_qc.get("passed"):
                        dc_pass += 1
                    else:
                        for reason in dc_qc.get("failure_reasons") or []:
                            key = f"dc:{reason}"
                            reject_reasons[key] = reject_reasons.get(key, 0) + 1
                    attempt = int(settings_block.get("dc_visual_qc_attempt") or 1)
                    if dc_qc.get("passed"):
                        retries_before_accept.append(max(0, attempt - 1))
                if settings_block.get("fallback_used"):
                    reject_reasons["model_fallback"] = reject_reasons.get("model_fallback", 0) + 1
                for key in ("generation_time_ms", "elapsed_ms", "duration_ms"):
                    ms = _safe_float(settings_block.get(key))
                    if ms is not None and ms > 0:
                        gen_seconds.append(ms / 1000.0)
                        break
                else:
                    sec = _safe_float(settings_block.get("generation_time_s") or meta.get("duration_s"))
                    if sec is not None and sec > 0:
                        gen_seconds.append(sec)
        per_channel[channel] = {
            "validated": c_ok,
            "fail": c_fail,
            "fail_rate": round(c_fail / (c_ok + c_fail), 4) if (c_ok + c_fail) else None,
        }

    if simulate and simulate.quality_gate_failure:
        fail = max(fail, 5)
        validated = max(validated, 1)
        reject_reasons["simulated_fail"] = reject_reasons.get("simulated_fail", 0) + 5
        dc_total = max(dc_total, 5)
        dc_pass = min(dc_pass, 1)

    total = validated + fail
    qc_fail_rate = round(fail / total, 4) if total else None
    alert: AlertState = "HEALTHY"
    if qc_fail_rate is not None and qc_fail_rate > 0.35:
        alert = "DEGRADED"
    if simulate and simulate.quality_gate_failure:
        alert = "ERROR"

    avg_gen = round(sum(gen_seconds) / len(gen_seconds), 2) if gen_seconds else None
    return {
        "alert": alert,
        "validated": validated,
        "fail": fail,
        "qc_failure_rate": qc_fail_rate,
        "reject_reasons": reject_reasons,
        "per_channel": per_channel,
        "dc_visual_qc_pass_rate": round(dc_pass / dc_total, 4) if dc_total else None,
        "average_retries_before_accepted": (
            round(sum(retries_before_accept) / len(retries_before_accept), 3) if retries_before_accept else None
        ),
        "average_generation_seconds": avg_gen,
        "generation_seconds_samples": gen_seconds[:50],
    }


def _model_routing(settings: Settings, *, simulate: ObservabilitySimulate | None) -> dict[str, Any]:
    channels: dict[str, Any] = {}
    alert: AlertState = "HEALTHY"
    for channel in CHANNEL_IDS:
        prof = load_model_profile(settings, channel)  # type: ignore[arg-type]
        primary = str(prof.get("quality_model") or prof.get("primary_model") or prof.get("fast_model") or "")
        fast = str(prof.get("fast_model") or "")
        quality = str(prof.get("quality_model") or "")
        fallback = str(prof.get("quality_fallback_model") or prof.get("fallback_model") or "")
        last_failure = None
        if simulate and simulate.model_fallback and channel == "wealth_blueprint":
            last_failure = {
                "component": "model_routing",
                "timestamp": datetime.now(UTC).isoformat(),
                "error_code": "CHECKPOINT_FALLBACK",
                "short_message": "Primary quality checkpoint failed; fell back to Turbo",
                "job_request_id": "sim-fallback-1",
                "retry_count": 1,
            }
            alert = "DEGRADED"
        channels[channel] = {
            "active_primary_model": Path(primary).name if primary else None,
            "fast_model": Path(fast).name if fast else None,
            "quality_model": Path(quality).name if quality else None,
            "fallback_model": Path(fallback).name if fallback else None,
            "last_model_failure": last_failure,
            "no_flux_dependency": bool(prof.get("no_flux_dependency") or channel != "dark_chronicles"),
        }
    return {"alert": alert, "channels": channels}


def _orders_snapshot(settings: Settings, *, history_hours: int, max_jobs: int) -> dict[str, Any]:
    db = Path(settings.data_dir) / "intelligence.db"
    running = failed = done = 0
    durations: list[float] = []
    errors: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    detail: str | None = None
    if not db.is_file():
        return {
            "alert": "DEGRADED",
            "running": 0,
            "failed": 0,
            "done": 0,
            "average_processing_time_s": None,
            "recent_jobs": [],
            "errors": [],
            "detail": "intelligence.db missing",
        }
    since = (datetime.now(UTC) - timedelta(hours=history_hours)).isoformat()
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                str(r[0])
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "pipeline_jobs" not in tables:
                return {
                    "alert": "DEGRADED",
                    "running": 0,
                    "failed": 0,
                    "done": 0,
                    "average_processing_time_s": None,
                    "recent_jobs": [],
                    "errors": [],
                    "detail": "pipeline_jobs table missing",
                }
            rows = conn.execute(
                """
                SELECT id, status, created_at, updated_at, topic_id, current_step, error
                FROM pipeline_jobs
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max_jobs,),
            ).fetchall()
            for row in rows:
                status = str(row["status"] or "").casefold()
                item = {
                    "id": row["id"],
                    "status": row["status"],
                    "topic_id": row["topic_id"],
                    "current_step": row["current_step"],
                    "updated_at": row["updated_at"],
                }
                recent.append(item)
                if status in {"running", "leased", "dispatched", "in_progress", "queued"}:
                    running += 1
                elif status in {"failed", "blocked", "error"}:
                    failed += 1
                elif status in {"ready", "done", "completed", "script_ready"}:
                    done += 1
                try:
                    created = str(row["created_at"] or "")
                    updated = str(row["updated_at"] or "")
                    if created and updated and len(created) >= 19 and len(updated) >= 19:
                        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        u = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        durations.append(max(0.0, (u - c).total_seconds()))
                except Exception:
                    pass
            if "pipeline_job_events" in tables:
                event_rows = conn.execute(
                    """
                    SELECT job_id, stage, event, detail_json, created_at
                    FROM pipeline_job_events
                    WHERE created_at >= ?
                      AND lower(event) IN ('failed', 'blocked', 'error', 'fail')
                    ORDER BY created_at DESC
                    LIMIT 40
                    """,
                    (since,),
                ).fetchall()
                for ev in event_rows:
                    payload: dict[str, Any] = {}
                    try:
                        payload = json.loads(ev["detail_json"] or "{}")
                    except json.JSONDecodeError:
                        payload = {}
                    message = redact_secrets(
                        str(payload.get("message") or payload.get("detail") or ev["event"])
                    )[:240]
                    errors.append(
                        {
                            "component": str(ev["stage"] or "order"),
                            "timestamp": ev["created_at"],
                            "error_code": str(
                                payload.get("category") or payload.get("code") or str(ev["event"]).upper()
                            ),
                            "short_message": message,
                            "job_request_id": ev["job_id"],
                            "retry_count": int(payload.get("attempts") or payload.get("retry_count") or 0),
                        }
                    )
            else:
                detail = "pipeline_job_events table missing — job list available, event drilldown skipped"
    except sqlite3.Error as exc:
        return {
            "alert": "ERROR",
            "running": 0,
            "failed": 0,
            "done": 0,
            "average_processing_time_s": None,
            "recent_jobs": [],
            "errors": [
                {
                    "component": "orders_db",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "error_code": "DB_ERROR",
                    "short_message": redact_secrets(str(exc))[:200],
                    "job_request_id": None,
                    "retry_count": 0,
                }
            ],
        }

    alert: AlertState = "HEALTHY"
    if detail:
        alert = "DEGRADED"
    if failed and failed >= max(1, done):
        alert = "DEGRADED"
    if running > 3:
        alert = "DEGRADED"
    result: dict[str, Any] = {
        "alert": alert,
        "running": running,
        "failed": failed,
        "done": done,
        "average_processing_time_s": round(sum(durations) / len(durations), 1) if durations else None,
        "recent_jobs": recent[:max_jobs],
        "errors": errors,
        "history_hours": history_hours,
    }
    if detail:
        result["detail"] = detail
    return result


def _comfy_health_alert(payload: dict[str, Any]) -> AlertState:
    overall = str(payload.get("overall") or payload.get("status") or "").upper()
    if overall in {"HEALTHY", "OK"}:
        return "HEALTHY"
    if overall in {"DEGRADED"}:
        return "DEGRADED"
    if overall in {"BLOCKED", "BLOCKED_DEPENDENCY"}:
        return "BLOCKED"
    if overall in {"ERROR", "DOWN"}:
        return "ERROR"
    return "DEGRADED"


def _channel_views(
    settings: Settings,
    *,
    comfy: dict[str, Any],
    assets: dict[str, Any],
    quality: dict[str, Any],
    models: dict[str, Any],
    orders: dict[str, Any],
) -> dict[str, Any]:
    from atnos.production.comfy_channels.health import check_channel_comfy

    views: dict[str, Any] = {}
    # Wealth Blueprint
    try:
        from atnos.production.channels.wealth_blueprint.health import run_wealth_blueprint_video_engine_health

        wb_health = run_wealth_blueprint_video_engine_health(settings)
    except Exception as exc:  # noqa: BLE001
        wb_health = {"status": "ERROR", "detail": redact_secrets(str(exc))[:200]}
    try:
        wb_comfy = check_channel_comfy(settings, "wealth_blueprint")
    except Exception as exc:  # noqa: BLE001
        wb_comfy = {"overall": "ERROR", "detail": redact_secrets(str(exc))[:160]}
    remotion_ok = False
    if isinstance(wb_health, dict):
        for item in wb_health.get("checks") or []:
            if isinstance(item, dict) and item.get("name") == "remotion" and item.get("status") == "ok":
                remotion_ok = True
    wb_alert = _worst(
        "HEALTHY" if comfy.get("reachable") else "BLOCKED",
        _comfy_health_alert(wb_comfy if isinstance(wb_comfy, dict) else {}),
        assets.get("alert", "HEALTHY"),
        "DEGRADED" if not remotion_ok else "HEALTHY",
    )
    views["wealth_blueprint"] = {
        "alert": wb_alert,
        "model": (models.get("channels") or {}).get("wealth_blueprint"),
        "comfy_health": wb_comfy,
        "remotion_health": {"ok": remotion_ok, "raw_status": wb_health.get("status") if isinstance(wb_health, dict) else None},
        "asset_brain": {
            "approval_rate": assets.get("wb_asset_approval_rate"),
            "character_approval_rate": assets.get("character_approval_rate"),
            "awaiting_review": assets.get("awaiting_review"),
        },
        "qc": (quality.get("per_channel") or {}).get("wealth_blueprint"),
        "average_production_time_s": orders.get("average_processing_time_s"),
        "last_render": (orders.get("recent_jobs") or [None])[0],
        "approval_rate": assets.get("wb_asset_approval_rate"),
    }

    # Wonder Crew
    try:
        wc_comfy = check_channel_comfy(settings, "wonder_crew")
    except Exception as exc:  # noqa: BLE001
        wc_comfy = {"overall": "ERROR", "detail": redact_secrets(str(exc))[:160]}
    handoff = {"ready_count": 0, "approved_character_references": 0, "status": "unknown"}
    try:
        from atnos.production.wonder_crew_3d_handoff import list_packages

        packages = list_packages() if callable(list_packages) else []
        if isinstance(packages, list):
            handoff["ready_count"] = sum(1 for p in packages if (p or {}).get("status") == "READY_FOR_3D")
            handoff["status"] = "READY" if handoff["ready_count"] else "NOT_READY"
    except Exception:
        try:
            root = Path(settings.data_dir) / "wonder_crew_3d_handoff" / "packages"
            if not root.is_dir():
                root = Path(r"E:\Atnos\App\data\wonder_crew_3d_handoff\packages")
            if root.is_dir():
                for path in root.rglob("*.json"):
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if data.get("status") == "READY_FOR_3D":
                        handoff["ready_count"] += 1
                handoff["status"] = "READY" if handoff["ready_count"] else "NOT_READY"
            canon = Path(r"E:\Atnos\App\config\wonder_crew_3d_handoff\canonical_characters.json")
            if canon.is_file():
                payload = json.loads(canon.read_text(encoding="utf-8"))
                chars = payload.get("characters") if isinstance(payload, dict) else None
                if isinstance(chars, dict):
                    handoff["approved_character_references"] = len(chars)
                elif isinstance(chars, list):
                    handoff["approved_character_references"] = len(chars)
        except Exception as exc:  # noqa: BLE001
            handoff["detail"] = redact_secrets(str(exc))[:160]
    views["wonder_crew"] = {
        "alert": _worst(
            "HEALTHY" if comfy.get("reachable") else "BLOCKED",
            _comfy_health_alert(wc_comfy if isinstance(wc_comfy, dict) else {}),
        ),
        "comfy_reference_status": wc_comfy,
        "approved_character_references": handoff.get("approved_character_references"),
        "handoff_3d_readiness": handoff,
        "model": (models.get("channels") or {}).get("wonder_crew"),
    }

    # Dark Chronicles
    try:
        dc_comfy = check_channel_comfy(settings, "dark_chronicles")
    except Exception as exc:  # noqa: BLE001
        dc_comfy = {"overall": "ERROR", "detail": redact_secrets(str(exc))[:160]}
    views["dark_chronicles"] = {
        "alert": _worst(
            "ERROR" if quality.get("alert") == "ERROR" else "HEALTHY",
            "HEALTHY" if comfy.get("reachable") else "BLOCKED",
            _comfy_health_alert(dc_comfy if isinstance(dc_comfy, dict) else {}),
        ),
        "comfy": dc_comfy,
        "visual_qc_pass_rate": quality.get("dc_visual_qc_pass_rate"),
        "asset_acceptance_rate": quality.get("dc_visual_qc_pass_rate"),
        "qc": (quality.get("per_channel") or {}).get("dark_chronicles"),
        "model": (models.get("channels") or {}).get("dark_chronicles"),
    }
    return views


def _performance(
    cache: dict[str, Any],
    quality: dict[str, Any],
    orders: dict[str, Any],
    assets: dict[str, Any],
    *,
    history_hours: int,
) -> dict[str, Any]:
    avg_retries = quality.get("average_retries_before_accepted")
    approved = int(assets.get("approved") or 0)
    hours = max(1, int(history_hours or 24))
    accepted_per_hour = round(approved / hours, 3) if approved else None
    return {
        "average_generation_seconds": quality.get("average_generation_seconds"),
        "accepted_assets_per_hour": accepted_per_hour,
        "render_time_s": orders.get("average_processing_time_s"),
        "cache_hit_rate": cache.get("hit_rate"),
        "qc_failure_rate": quality.get("qc_failure_rate"),
        "retries": avg_retries,
        "queue_wait_s": None,
        "character_approval_rate": assets.get("character_approval_rate"),
        "wb_asset_approval_rate": assets.get("wb_asset_approval_rate"),
        "dc_visual_qc_pass_rate": quality.get("dc_visual_qc_pass_rate"),
        "average_retries_before_accepted": avg_retries,
        "note": "Quality + throughput together — do not optimize raw speed alone.",
    }


def build_media_observability(
    settings: Settings,
    *,
    history_hours: int = 24,
    max_jobs: int = 40,
    simulate: ObservabilitySimulate | None = None,
    live: bool = True,
) -> dict[str, Any]:
    """Aggregate Media Production observability for API + UI."""
    comfy = _comfy_snapshot(settings, simulate=simulate)
    gpu = _gpu_snapshot(settings, simulate=simulate)
    cache = _cache_stats(settings, simulate=simulate)
    assets = _asset_brain_stats(settings, simulate=simulate)
    quality = _quality_stats(settings, simulate=simulate)
    models = _model_routing(settings, simulate=simulate)
    orders = _orders_snapshot(settings, history_hours=history_hours, max_jobs=max_jobs)
    channels = _channel_views(
        settings, comfy=comfy, assets=assets, quality=quality, models=models, orders=orders
    )
    performance = _performance(cache, quality, orders, assets, history_hours=history_hours)

    errors: list[dict[str, Any]] = list(orders.get("errors") or [])
    if comfy.get("alert") == "ERROR" or (simulate and simulate.comfy_down):
        errors.insert(
            0,
            {
                "component": "comfyui",
                "timestamp": datetime.now(UTC).isoformat(),
                "error_code": "COMFY_DOWN",
                "short_message": comfy.get("detail") or "ComfyUI unreachable",
                "job_request_id": None,
                "retry_count": 0,
            },
        )
    for channel, routing in (models.get("channels") or {}).items():
        fail = (routing or {}).get("last_model_failure")
        if fail:
            errors.append(fail)

    overall = _worst(
        comfy.get("alert", "HEALTHY"),
        gpu.get("alert", "HEALTHY"),
        cache.get("alert", "HEALTHY"),
        assets.get("alert", "HEALTHY"),
        quality.get("alert", "HEALTHY"),
        models.get("alert", "HEALTHY"),
        orders.get("alert", "HEALTHY"),
    )
    # Required capabilities: Comfy reachable is required for media production fleet HEALTHY
    if not comfy.get("reachable"):
        overall = _worst(overall, "BLOCKED")

    return {
        "schema_version": "media-observability-1",
        "timestamp": datetime.now(UTC).isoformat(),
        "overall_alert": overall,
        "live": live,
        "comfy": comfy,
        "gpu": gpu,
        "cache": cache,
        "assets": assets,
        "quality": quality,
        "models": models,
        "orders": orders,
        "channels": channels,
        "errors": errors[:50],
        "performance": performance,
        "history": {
            "hours": history_hours,
            "max_jobs": max_jobs,
            "source": "intelligence.db pipeline_jobs + pipeline_job_events",
        },
    }
