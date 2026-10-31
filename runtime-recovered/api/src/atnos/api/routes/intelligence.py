from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from atnos.intelligence.competitor import collect_competitors
from atnos.intelligence.google_trends import collect_google_trends
from atnos.intelligence.own_channel import collect_own_channel
from atnos.intelligence.reference_channels import collect_reference_channels
from atnos.intelligence.storage import (
    connect,
    json_loads,
    read_intelligence_account_config,
    read_intelligence_account_configs,
    rows_to_dicts,
    set_intelligence_account_automation_mode,
    set_intelligence_account_enabled,
    upsert_intelligence_account_config,
    utc_now_iso,
)
from atnos.intelligence.youtube_api import youtube_data_client_mode
from atnos.intelligence.youtube_trends import collect_youtube_trends
from atnos.services.google_oauth_client_secret_importer import persist_oauth_runtime_env
from atnos.services.youtube_connector_service import YouTubeConnectorError

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


class CompetitorRequest(BaseModel):
    account_id: str = Field(min_length=1)
    channel_urls: list[str] = Field(min_length=1)


class ReferenceRequest(BaseModel):
    channel_urls: list[str] = Field(min_length=1)


class IntelligenceRunRequest(BaseModel):
    account_id: str = Field(min_length=1)
    niches: list[str] = Field(default_factory=list)
    competitor_urls: list[str] = Field(default_factory=list)
    reference_urls: list[str] = Field(default_factory=list)
    region: str = "PL"


class IntelligenceAccountConfigRequest(BaseModel):
    account_id: str = Field(min_length=1)
    niches: list[str] = Field(min_length=1)
    region: str = "PL"
    timeframe: str = "now 7-d"
    youtube_oauth_account_id: int | None = Field(default=None, gt=0)
    gmail_oauth_account_id: int | None = Field(default=None, gt=0)
    channel_id: str | None = Field(default=None, max_length=120)
    channel_title: str | None = Field(default=None, max_length=160)
    content_profile: str | None = Field(default=None, max_length=400)
    automation_mode: str | None = Field(default=None, pattern="^(manual|auto_private|auto_public)$")
    refresh_now: bool = True


class IntelligenceAccountUpdateRequest(BaseModel):
    enabled: bool | None = None
    automation_mode: str | None = Field(default=None, pattern="^(manual|auto_private|auto_public)$")


class YouTubeDataApiKeyConfigRequest(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)


def _database(request: Request):
    return request.app.state.database


def _settings(request: Request):
    return request.app.state.settings


def _collector_error_payload(exc: Exception, *, fallback_status: str = "error") -> dict[str, Any]:
    message = str(exc)
    lowered = message.casefold()
    if "no such table" in lowered:
        return {
            "status": "unavailable",
            "message": "Brakuje tabel OAuth w lokalnej bazie runtime. Zrestartuj Atnosa, zeby uruchomic migracje bazy.",
        }
    if isinstance(exc, YouTubeConnectorError) or any(
        marker in lowered
        for marker in (
            "connect youtube studio",
            "oauth",
            "credential",
            "client secret",
            "unauthorized",
            "token",
        )
    ):
        return {"status": "needs_config", "message": message}
    if "quota" in lowered:
        return {"status": "quota_exhausted", "message": message}
    if "429" in lowered or "rate_limited" in lowered or "too many requests" in lowered:
        return {"status": "rate_limited", "message": message}
    return {"status": fallback_status, "message": message}


def _read_table(request: Request, table: str, limit: int = 100, niche: str | None = None) -> list[dict[str, Any]]:
    settings = _settings(request)
    with connect(settings) as db:
        if table == "yt_signals":
            params: list[Any] = []
            where = ""
            if niche:
                where = "WHERE niche LIKE ?"
                params.append(f"%{niche}%")
            rows = db.execute(
                f"""
                SELECT * FROM yt_signals
                {where}
                ORDER BY view_count DESC, collected_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            return rows_to_dicts(rows)
        if table == "gtrends_signals":
            rows = db.execute(
                "SELECT * FROM gtrends_signals ORDER BY collected_at DESC, interest_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
            items = rows_to_dicts(rows)
            for item in items:
                item["related_rising"] = json_loads(item.get("related_rising"), [])
                item["related_topics"] = json_loads(item.get("related_topics"), [])
            return items
        if table == "competitor_channels":
            rows = db.execute(
                "SELECT * FROM competitor_channels ORDER BY avg_views_7d DESC, last_refreshed DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return rows_to_dicts(rows)
        if table == "competitor_videos":
            rows = db.execute(
                "SELECT * FROM competitor_videos ORDER BY published_at DESC, views DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return rows_to_dicts(rows)
        if table == "own_performance":
            rows = db.execute(
                "SELECT * FROM own_performance ORDER BY collected_at DESC, views DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return rows_to_dicts(rows)
        if table == "reference_channels":
            rows = db.execute(
                "SELECT * FROM reference_channels ORDER BY last_refreshed DESC, avg_engagement_rate DESC LIMIT ?",
                (limit,),
            ).fetchall()
            items = rows_to_dicts(rows)
            for item in items:
                item["hook_patterns"] = json_loads(item.get("hook_patterns"), [])
                item["thumbnail_style_tags"] = json_loads(item.get("thumbnail_style_tags"), [])
            return items
    return []


@router.get("/trends")
async def youtube_trends(
    request: Request,
    account_id: str,
    niche: str,
    region: str = "PL",
    force: bool = False,
) -> dict[str, Any]:
    try:
        with _database(request).session_scope() as session:
            return await collect_youtube_trends(
                session=session,
                settings=_settings(request),
                account_id=account_id,
                niche=niche,
                region=region,
                force=force,
            )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/gtrends")
async def google_trends(
    request: Request,
    keywords: str,
    timeframe: str = "now 7-d",
    geo: str = "PL",
) -> dict[str, Any]:
    keyword_list = [keyword.strip() for keyword in keywords.split(",") if keyword.strip()]
    return await collect_google_trends(
        settings=_settings(request),
        keywords=keyword_list,
        timeframe=timeframe,
        geo=geo,
    )


@router.post("/competitors")
async def competitors(request: Request, payload: CompetitorRequest) -> dict[str, Any]:
    try:
        with _database(request).session_scope() as session:
            return await collect_competitors(
                session=session,
                settings=_settings(request),
                account_id=payload.account_id,
                channel_urls=payload.channel_urls,
            )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/own")
async def own_channel(request: Request, account_id: str = Query(min_length=1)) -> dict[str, Any]:
    try:
        with _database(request).session_scope() as session:
            return await collect_own_channel(
                session=session,
                settings=_settings(request),
                account_id=account_id,
            )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/reference")
async def reference_channels(request: Request, payload: ReferenceRequest) -> dict[str, Any]:
    try:
        with _database(request).session_scope() as session:
            return await collect_reference_channels(
                session=session,
                settings=_settings(request),
                channel_urls=payload.channel_urls,
            )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/run")
async def run_all_collectors(request: Request, payload: IntelligenceRunRequest) -> dict[str, Any]:
    return await _run_all_collectors(
        settings=_settings(request),
        database=_database(request),
        payload=payload,
    )


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_intelligence(
    request: Request,
    payload: IntelligenceRunRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Compatibility alias for autonomous research refresh jobs."""
    job_id = f"intelligence-refresh-{uuid4().hex[:12]}"
    _set_refresh_job_state(
        request.app,
        job_id,
        {
            "job_id": job_id,
            "status": "queued",
            "account_id": payload.account_id,
            "created_at": utc_now_iso(),
        },
    )
    background_tasks.add_task(_run_refresh_background_job, request.app, payload, job_id)
    return {
        "status": "queued",
        "job_id": job_id,
        "account_id": payload.account_id,
        "message": "Intelligence refresh queued.",
    }


async def _run_refresh_background_job(app: Any, payload: IntelligenceRunRequest, job_id: str) -> None:
    _set_refresh_job_state(
        app,
        job_id,
        {
            "job_id": job_id,
            "status": "running",
            "account_id": payload.account_id,
            "started_at": utc_now_iso(),
        },
    )
    try:
        result = await _run_all_collectors(
            settings=app.state.settings,
            database=app.state.database,
            payload=payload,
        )
    except Exception as exc:  # pragma: no cover - defensive background guard
        _set_refresh_job_state(
            app,
            job_id,
            {
                "job_id": job_id,
                "status": "error",
                "account_id": payload.account_id,
                "finished_at": utc_now_iso(),
                "message": str(exc),
            },
        )
        return
    _set_refresh_job_state(
        app,
        job_id,
        {
            "job_id": job_id,
            "status": result.get("status", "completed"),
            "account_id": payload.account_id,
            "finished_at": utc_now_iso(),
            "result": result,
        },
    )


def _set_refresh_job_state(app: Any, job_id: str, payload: dict[str, Any]) -> None:
    jobs = getattr(app.state, "intelligence_refresh_jobs", None)
    if not isinstance(jobs, dict):
        jobs = {}
        app.state.intelligence_refresh_jobs = jobs
    jobs[job_id] = payload
    if len(jobs) > 50:
        for stale_id in list(jobs)[:-50]:
            jobs.pop(stale_id, None)


async def _run_all_collectors(*, settings: Any, database: Any, payload: IntelligenceRunRequest) -> dict[str, Any]:
    results: dict[str, Any] = {}
    had_error = False
    niche_items = payload.niches or []
    if niche_items:
        upsert_intelligence_account_config(
            settings,
            account_id=payload.account_id,
            niches=niche_items,
            region=payload.region,
            timeframe="now 7-d",
        )
    with database.session_scope() as session:
        if niche_items:
            trend_results = []
            for niche in niche_items:
                try:
                    trend_results.append(
                        await collect_youtube_trends(
                            session=session,
                            settings=settings,
                            account_id=payload.account_id,
                            niche=niche,
                            region=payload.region,
                            force=True,
                        )
                    )
                except Exception as exc:
                    had_error = True
                    trend_results.append({"niche": niche, **_collector_error_payload(exc)})
            results["youtube_trends"] = trend_results
            try:
                results["google_trends"] = await collect_google_trends(
                    settings=settings,
                    keywords=niche_items,
                    timeframe="now 7-d",
                    geo=payload.region,
                )
            except Exception as exc:
                had_error = True
                results["google_trends"] = _collector_error_payload(exc)
        if payload.competitor_urls:
            try:
                results["competitors"] = await collect_competitors(
                    session=session,
                    settings=settings,
                    account_id=payload.account_id,
                    channel_urls=payload.competitor_urls,
                )
            except Exception as exc:
                had_error = True
                results["competitors"] = _collector_error_payload(exc)
        try:
            results["own_channel"] = await collect_own_channel(
                session=session,
                settings=settings,
                account_id=payload.account_id,
            )
            if results["own_channel"].get("status") not in {"collected", "invalid_request"}:
                had_error = True
        except Exception as exc:
            had_error = True
            results["own_channel"] = _collector_error_payload(exc)
        if payload.reference_urls:
            try:
                results["reference"] = await collect_reference_channels(
                    session=session,
                    settings=settings,
                    channel_urls=payload.reference_urls,
                )
            except Exception as exc:
                had_error = True
                results["reference"] = _collector_error_payload(exc)
    return {"status": "completed_with_errors" if had_error else "completed", "results": results}


@router.get("/accounts")
async def list_intelligence_accounts(request: Request) -> dict[str, Any]:
    """List saved autonomous intelligence refresh configs. Does not spend external quota."""
    return {"accounts": read_intelligence_account_configs(_settings(request))}


@router.post("/accounts")
async def configure_intelligence_account(request: Request, payload: IntelligenceAccountConfigRequest) -> dict[str, Any]:
    """Persist account niches for autonomous collector refresh without running collectors immediately."""
    next_refresh_at = utc_now_iso() if payload.refresh_now else _deferred_intelligence_refresh_at()
    upsert_intelligence_account_config(
        _settings(request),
        account_id=payload.account_id,
        niches=payload.niches,
        region=payload.region,
        timeframe=payload.timeframe,
        youtube_oauth_account_id=payload.youtube_oauth_account_id,
        gmail_oauth_account_id=payload.gmail_oauth_account_id,
        channel_id=payload.channel_id,
        channel_title=payload.channel_title,
        content_profile=payload.content_profile,
        automation_mode=payload.automation_mode,
        next_refresh_at=next_refresh_at,
        preserve_existing_next_refresh=False,
    )
    stored = read_intelligence_account_config(_settings(request), account_id=payload.account_id)
    if not stored:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid intelligence account config.")
    return {
        "status": "configured",
        "account": stored,
        "quota_spent": False,
        "scheduler": {
            "enabled": True,
            "due_now": bool(payload.refresh_now),
            "collector": "atnos.intelligence.scheduler.IntelligenceRefreshService.refresh_due",
        },
    }


@router.patch("/accounts/{account_id}")
async def update_intelligence_account_enabled(
    request: Request,
    account_id: str,
    payload: IntelligenceAccountUpdateRequest,
) -> dict[str, Any]:
    if payload.enabled is None and payload.automation_mode is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one account setting is required.")

    stored = read_intelligence_account_config(_settings(request), account_id=account_id)
    if stored and payload.enabled is not None:
        stored = set_intelligence_account_enabled(_settings(request), account_id=account_id, enabled=payload.enabled)
    if stored and payload.automation_mode is not None:
        stored = set_intelligence_account_automation_mode(
            _settings(request),
            account_id=account_id,
            automation_mode=payload.automation_mode,
        )
    if not stored:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intelligence account config not found.")
    return {"status": "updated", "account": stored}


def _deferred_intelligence_refresh_at() -> str:
    return (datetime.fromisoformat(utc_now_iso()) + timedelta(hours=6)).isoformat()


@router.post("/youtube-data-api-key")
async def configure_youtube_data_api_key(
    request: Request,
    payload: YouTubeDataApiKeyConfigRequest,
) -> dict[str, Any]:
    """Persist a YouTube Data API key for quota-light public trend collection."""
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="YouTube Data API key is empty.")
    if any(char.isspace() for char in api_key):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="YouTube Data API key cannot contain whitespace.")

    settings = _settings(request)
    env_path = persist_oauth_runtime_env(settings.app_root / ".env", {"YOUTUBE_DATA_API_KEY": api_key})
    settings.youtube_data_api_key = api_key
    return {
        "status": "configured",
        "youtube_data_api_key_configured": True,
        "collector_mode": "api_key",
        "masked_key": _mask_secret(api_key),
        "env_path": str(env_path),
        "quota_spent": False,
        "next_actions": [
            "Uruchom POST /api/intelligence/run z account_id, niches i region, zeby pobrac realne yt_signals.",
            "YouTube upload prywatny nadal wymaga Google OAuth client_secret.json i pierwszej autoryzacji.",
        ],
    }


@router.get("/status")
async def intelligence_status(
    request: Request,
    account_id: str = Query(min_length=1),
    niches: str = "",
    region: str = "PL",
) -> dict[str, Any]:
    """Return local research readiness without spending YouTube quota."""
    return _build_intelligence_status(
        settings=_settings(request),
        database=_database(request),
        account_id=account_id,
        niches=niches,
        region=region,
    )


def _build_intelligence_status(
    *,
    settings,
    database,
    account_id: str,
    niches: str,
    region: str,
) -> dict[str, Any]:
    niche_items = [item.strip() for item in niches.split(",") if item.strip()]
    normalized_region = region.strip().upper() or "PL"
    with connect(settings) as db:
        yt_count = db.execute(
            "SELECT COUNT(*) AS count FROM yt_signals WHERE account_id = ? AND region = ?",
            (account_id, normalized_region),
        ).fetchone()["count"]
        gt_count = db.execute("SELECT COUNT(*) AS count FROM gtrends_signals WHERE geo = ?", (normalized_region,)).fetchone()["count"]
        competitor_count = db.execute(
            "SELECT COUNT(*) AS count FROM competitor_videos WHERE account_id = ?",
            (account_id,),
        ).fetchone()["count"]
        own_count = db.execute(
            "SELECT COUNT(*) AS count FROM own_performance WHERE account_id = ?",
            (account_id,),
        ).fetchone()["count"]
        yt_by_niche = []
        run_keys = []
        for niche in niche_items:
            run_key = f"youtube_trends:{account_id}:{' '.join(niche.strip().casefold().split())}:{normalized_region}"
            run_keys.append(run_key)
            row = db.execute(
                """
                SELECT COUNT(*) AS count, MAX(collected_at) AS last_collected_at
                FROM yt_signals
                WHERE account_id = ? AND region = ? AND niche = ?
                """,
                (account_id, normalized_region, niche),
            ).fetchone()
            yt_by_niche.append(
                {
                    "niche": niche,
                    "count": int(row["count"] or 0),
                    "last_collected_at": row["last_collected_at"],
                }
            )
        if run_keys:
            placeholders = ",".join("?" for _ in run_keys)
            run_rows = db.execute(
                f"""
                SELECT run_key, collector, status, last_attempt_at, last_success_at, next_retry_at, message
                FROM collection_runs
                WHERE run_key IN ({placeholders})
                ORDER BY last_attempt_at DESC
                """,
                tuple(run_keys),
            ).fetchall()
        else:
            run_rows = db.execute(
                """
                SELECT run_key, collector, status, last_attempt_at, last_success_at, next_retry_at, message
                FROM collection_runs
                WHERE run_key LIKE ?
                ORDER BY last_attempt_at DESC
                LIMIT 10
                """,
                (f"youtube_trends:{account_id}:%:{normalized_region}",),
            ).fetchall()
        collector_runs = rows_to_dicts(run_rows)
        stored_config_row = db.execute(
            """
            SELECT account_id, niches, region, timeframe, last_requested_at, last_refresh_at, next_refresh_at, enabled
            FROM intelligence_accounts
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        stored_config = dict(stored_config_row) if stored_config_row else None
        if stored_config:
            stored_config["niches"] = json_loads(stored_config.get("niches"), [])
    youtube_mode = youtube_data_client_mode(settings)
    api_key_configured = bool(settings.youtube_data_api_key)
    oauth_ready = False
    oauth_message = ""
    if not api_key_configured:
        try:
            with database.session_scope() as session:
                from atnos.services.youtube_connector_service import YouTubeConnectorService

                oauth_status = YouTubeConnectorService(session, settings).get_status()
                oauth_ready = bool(oauth_status.ready)
                oauth_message = _youtube_oauth_status_message(oauth_status)
        except Exception as exc:
            oauth_ready = False
            oauth_message = str(exc)
    youtube_configured = api_key_configured or oauth_ready
    credentials = {
        "api_key_configured": api_key_configured,
        "oauth_ready": oauth_ready,
        "collector_mode": "api_key" if api_key_configured else "oauth",
        "can_collect_youtube_trends": youtube_configured,
        "oauth_message": oauth_message,
        "required_one_of": ["YOUTUBE_DATA_API_KEY", "YouTube Studio OAuth"],
    }
    blockers: list[str] = []
    if not youtube_configured:
        blockers.append("youtube_data_api_key_or_oauth")
    if yt_count <= 0:
        blockers.append("yt_signals_empty")
    if gt_count <= 0:
        blockers.append("gtrends_signals_empty")
    missing_niches = [item["niche"] for item in yt_by_niche if item["count"] <= 0]
    if missing_niches:
        blockers.append("yt_signals_missing_for_requested_niches")
    ready_for_topic_generation = not {"youtube_data_api_key_or_oauth", "yt_signals_empty", "gtrends_signals_empty"}.intersection(blockers)
    return {
        "account_id": account_id,
        "region": normalized_region,
        "youtube_client_mode": youtube_mode,
        "youtube_configured": youtube_configured,
        "credentials": credentials,
        "ready_for_topic_generation": ready_for_topic_generation,
        "blockers": blockers,
        "counts": {
            "yt_signals": int(yt_count or 0),
            "gtrends_signals": int(gt_count or 0),
            "competitor_videos": int(competitor_count or 0),
            "own_performance": int(own_count or 0),
        },
        "yt_by_niche": yt_by_niche,
        "collector_runs": collector_runs,
        "stored_config": stored_config,
        "next_actions": _intelligence_next_actions(blockers, account_id=account_id, niches=niche_items, region=normalized_region),
    }


def _intelligence_next_actions(
    blockers: list[str],
    *,
    account_id: str,
    niches: list[str],
    region: str,
) -> list[str]:
    actions: list[str] = []
    if "youtube_data_api_key_or_oauth" in blockers:
        actions.append(
            "Ustaw YOUTUBE_DATA_API_KEY przez POST /api/intelligence/youtube-data-api-key albo polacz YouTube Studio OAuth z zakresem youtube.readonly."
        )
    if "yt_signals_empty" in blockers or "yt_signals_missing_for_requested_niches" in blockers:
        niche_arg = ",".join(niches) if niches else "finanse osobiste"
        actions.append(
            f"Uruchom POST /api/intelligence/run dla account_id={account_id}, niches=[{niche_arg}], region={region}."
        )
    if "gtrends_signals_empty" in blockers:
        actions.append("Uruchom /api/intelligence/gtrends dla nisz kanalu, zeby zasilic search_demand i trend_velocity.")
    return actions


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _youtube_oauth_status_message(status: Any) -> str:
    if bool(getattr(status, "ready", False)):
        return "YouTube Studio OAuth is connected."
    warnings = getattr(status, "warnings", ()) or ()
    warning_text = "; ".join(str(item) for item in warnings if str(item).strip())
    if warning_text:
        return warning_text
    configured = bool(getattr(status, "configured", False))
    connected = bool(getattr(status, "connected", False))
    if not configured:
        return "YouTube Studio OAuth is not configured."
    if not connected:
        return "YouTube Studio OAuth token is not connected."
    return "YouTube Studio OAuth is not ready."


@router.get("/signals")
async def list_youtube_signals(
    request: Request,
    niche: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return _read_table(request, "yt_signals", limit=limit, niche=niche)


@router.get("/gtrends/signals")
async def list_google_trend_signals(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return _read_table(request, "gtrends_signals", limit=limit)


@router.get("/competitors")
async def list_competitors(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    return {
        "channels": _read_table(request, "competitor_channels", limit=limit),
        "videos": _read_table(request, "competitor_videos", limit=limit * 5),
    }


@router.get("/own/performance")
async def list_own_performance(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return _read_table(request, "own_performance", limit=limit)


@router.get("/reference")
async def list_reference_channels(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return _read_table(request, "reference_channels", limit=limit)
