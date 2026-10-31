from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from atnos.core.config import Settings


SQLITE_BUSY_TIMEOUT_S = 30.0
_INIT_LOCK = threading.RLock()
_INITIALIZED_DB_PATHS: set[Path] = set()


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def intelligence_db_path(settings: Settings) -> Path:
    return (settings.data_dir / "intelligence.db").resolve()


def utc_midnight_tomorrow_iso() -> str:
    now = utc_now()
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def normalize_key(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=SQLITE_BUSY_TIMEOUT_S)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_S * 1000)}")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _init_intelligence_db_sync(db_path: Path) -> None:
    resolved_path = db_path.resolve()
    if resolved_path in _INITIALIZED_DB_PATHS and db_path.exists():
        return
    with _INIT_LOCK:
        if resolved_path in _INITIALIZED_DB_PATHS and db_path.exists():
            return
        _init_intelligence_db_uncached(db_path)
        _INITIALIZED_DB_PATHS.add(resolved_path)


def _init_intelligence_db_uncached(db_path: Path) -> None:
    with closing(_connect(db_path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS yt_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT UNIQUE,
                account_id TEXT NOT NULL,
                niche TEXT NOT NULL,
                region TEXT NOT NULL,
                title TEXT NOT NULL,
                view_count INTEGER NOT NULL DEFAULT 0,
                like_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                published_at TEXT,
                channel_id TEXT,
                channel_title TEXT,
                thumbnail_url TEXT,
                collected_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_yt_signals_niche_collected
                ON yt_signals (account_id, niche, region, collected_at);

            CREATE TABLE IF NOT EXISTS gtrends_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                geo TEXT NOT NULL,
                interest_score INTEGER NOT NULL DEFAULT 0,
                related_rising TEXT NOT NULL DEFAULT '[]',
                related_topics TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'ready',
                collected_at TEXT NOT NULL,
                UNIQUE (keyword, timeframe, geo)
            );

            CREATE TABLE IF NOT EXISTS competitor_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                channel_id TEXT UNIQUE,
                source_url TEXT NOT NULL,
                channel_title TEXT,
                avg_views_7d REAL NOT NULL DEFAULT 0,
                posting_frequency REAL NOT NULL DEFAULT 0,
                posting_frequency_per_week REAL NOT NULL DEFAULT 0,
                top_performing_format TEXT,
                top_format TEXT,
                collected_at TEXT NOT NULL,
                last_refreshed TEXT,
                next_refresh_at TEXT,
                UNIQUE (account_id, channel_id)
            );

            CREATE TABLE IF NOT EXISTS competitor_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT UNIQUE,
                account_id TEXT NOT NULL,
                channel_id TEXT,
                competitor_channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                published_at TEXT,
                duration TEXT,
                duration_seconds INTEGER,
                thumbnail_url TEXT,
                collected_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_competitor_videos_channel
                ON competitor_videos (account_id, competitor_channel_id, published_at);

            CREATE TABLE IF NOT EXISTS own_channel_accounts (
                account_id TEXT PRIMARY KEY,
                last_requested_at TEXT NOT NULL,
                next_refresh_at TEXT
            );

            CREATE TABLE IF NOT EXISTS own_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT,
                views INTEGER NOT NULL DEFAULT 0,
                watch_time_min REAL NOT NULL DEFAULT 0,
                avg_view_duration_s REAL,
                avg_view_pct REAL,
                ctr REAL,
                subs_gained INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0,
                collected_at TEXT NOT NULL,
                UNIQUE(account_id, video_id, collected_at)
            );

            CREATE TABLE IF NOT EXISTS reference_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT UNIQUE,
                source_url TEXT NOT NULL,
                channel_title TEXT,
                avg_video_length_s REAL NOT NULL DEFAULT 0,
                avg_duration_sec INTEGER NOT NULL DEFAULT 0,
                posting_cadence REAL NOT NULL DEFAULT 0,
                posting_cadence_per_week REAL NOT NULL DEFAULT 0,
                engagement_rate REAL NOT NULL DEFAULT 0,
                avg_engagement_rate REAL NOT NULL DEFAULT 0,
                hook_patterns TEXT NOT NULL DEFAULT '[]',
                thumbnail_style_tags TEXT NOT NULL DEFAULT '[]',
                hook_pattern_summary TEXT,
                collected_at TEXT NOT NULL,
                last_refreshed TEXT
            );

            CREATE TABLE IF NOT EXISTS reference_videos (
                video_id TEXT PRIMARY KEY,
                reference_channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                published_at TEXT,
                duration TEXT,
                thumbnail_url TEXT,
                collected_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collection_runs (
                run_key TEXT PRIMARY KEY,
                collector TEXT NOT NULL,
                status TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                last_success_at TEXT,
                next_retry_at TEXT,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS intelligence_accounts (
                account_id TEXT PRIMARY KEY,
                niches TEXT NOT NULL DEFAULT '[]',
                region TEXT NOT NULL DEFAULT 'PL',
                timeframe TEXT NOT NULL DEFAULT 'now 7-d',
                youtube_oauth_account_id INTEGER,
                gmail_oauth_account_id INTEGER,
                channel_id TEXT,
                channel_title TEXT,
                content_profile TEXT,
                automation_mode TEXT NOT NULL DEFAULT 'manual',
                last_requested_at TEXT NOT NULL,
                last_refresh_at TEXT,
                next_refresh_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS topic_queue (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                inspired_by TEXT,
                status TEXT DEFAULT 'pending',
                total_score REAL,
                viral_potential REAL,
                search_demand REAL,
                competition_gap REAL,
                trend_velocity REAL,
                niche_fit REAL,
                freshness REAL,
                clickbait_risk INTEGER DEFAULT 0,
                evergreen INTEGER DEFAULT 0,
                shorts_potential INTEGER DEFAULT 0,
                llm_unavailable INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_topic_queue_account_status
                ON topic_queue (account_id, status, total_score);

            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                current_step TEXT,
                hooks TEXT,
                selected_hook_id TEXT,
                script TEXT,
                audio_timestamps TEXT,
                asset_plan TEXT,
                video_manifest TEXT,
                subtitle_path TEXT,
                thumbnail_path TEXT,
                qa_report TEXT,
                error TEXT,
                artifact_dir TEXT,
                qa_report_path TEXT,
                upload_package_path TEXT,
                youtube_video_id TEXT,
                youtube_url TEXT,
                upload_result TEXT,
                uploaded_at TEXT,
                analytics_followup_plan TEXT,
                review_status TEXT,
                review_notified_at TEXT,
                review_notification_path TEXT,
                quality_review_status TEXT,
                quality_review_result TEXT,
                quality_review_checked_at TEXT,
                public_publish_result TEXT,
                public_scheduled_at TEXT,
                public_published_at TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS pipeline_job_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                step TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                attempt INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                started_at TEXT,
                completed_at TEXT,
                UNIQUE(job_id, step)
            );

            CREATE TABLE IF NOT EXISTS pipeline_job_orders (
                job_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(id),
                request_key TEXT NOT NULL UNIQUE,
                request_hash TEXT NOT NULL,
                request_json TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                profile_hash TEXT NOT NULL,
                reserved_cost_units INTEGER NOT NULL DEFAULT 0,
                provider_calls INTEGER NOT NULL DEFAULT 0,
                lease_token TEXT,
                lease_pid INTEGER,
                lease_process_started REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pipeline_stage_checkpoints (
                job_id TEXT NOT NULL REFERENCES pipeline_jobs(id),
                stage TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                output_json TEXT,
                files_json TEXT,
                error_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id, stage)
            );

            CREATE TABLE IF NOT EXISTS pipeline_job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES pipeline_jobs(id),
                stage TEXT,
                event TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS production_publication_policies (
                project TEXT PRIMARY KEY,
                policy_json TEXT NOT NULL,
                authorized_after TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS production_publication_queue (
                job_id TEXT PRIMARY KEY REFERENCES pipeline_job_orders(job_id),
                project TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                publish_at TEXT NOT NULL,
                status TEXT NOT NULL,
                video_id TEXT UNIQUE,
                package_hash TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_check_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                lease_token TEXT,
                lease_pid INTEGER,
                lease_started REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(channel_id, publish_at)
            );

            CREATE TABLE IF NOT EXISTS production_analytics_followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                youtube_video_id TEXT NOT NULL,
                due_at TEXT NOT NULL,
                window_hours INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                analytics_json TEXT,
                score_update_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(job_id, window_hours)
            );

            CREATE TABLE IF NOT EXISTS production_cost_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                account_id TEXT,
                job_id TEXT,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                estimated_cost_pln REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'estimated',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_production_cost_ledger_created_at
                ON production_cost_ledger (created_at);

            CREATE INDEX IF NOT EXISTS idx_production_cost_ledger_account_job
                ON production_cost_ledger (account_id, job_id);

            CREATE TABLE IF NOT EXISTS investment_watchlist (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                asset_type TEXT NOT NULL DEFAULT 'stock',
                thesis TEXT NOT NULL DEFAULT '',
                risk_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS investment_paper_trades (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                quantity REAL NOT NULL CHECK (quantity > 0),
                price_pln REAL NOT NULL CHECK (price_pln > 0),
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'paper_executed'
            );

            CREATE INDEX IF NOT EXISTS idx_investment_paper_trades_symbol_created
                ON investment_paper_trades (symbol, created_at);

            CREATE TABLE IF NOT EXISTS investment_broker_accounts (
                provider_key TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                account_identifier TEXT NOT NULL,
                environment TEXT NOT NULL DEFAULT 'demo',
                access_mode TEXT NOT NULL DEFAULT 'read_only_candidate',
                status TEXT NOT NULL DEFAULT 'metadata_registered',
                credential_account_id INTEGER,
                login_url TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS investment_instruments (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                market TEXT NOT NULL,
                currency TEXT NOT NULL,
                provider_key TEXT NOT NULL,
                trade_mode TEXT NOT NULL DEFAULT 'paper_only',
                risk_bucket TEXT NOT NULL DEFAULT 'medium',
                enabled INTEGER NOT NULL DEFAULT 1,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_investment_instruments_provider_enabled
                ON investment_instruments (provider_key, enabled);

            CREATE TABLE IF NOT EXISTS project_content_brain (
                project TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_series (
                project TEXT NOT NULL,
                series_key TEXT NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project, series_key)
            );

            CREATE TABLE IF NOT EXISTS project_content_campaigns (
                campaign_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_plans (
                plan_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                record_json TEXT NOT NULL,
                stage TEXT NOT NULL,
                job_id TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_project_content_plans_project_stage
                ON project_content_plans (project, stage);

            CREATE TABLE IF NOT EXISTS project_content_history_decisions (
                job_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_trend_decisions (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_comment_clusters (
                cluster_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_competitor_knowledge (
                entry_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                record_json TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_experiments (
                experiment_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_publish_results (
                job_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                record_json TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_content_format_fatigue (
                project TEXT NOT NULL,
                format_key TEXT NOT NULL,
                record_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY (project, format_key)
            );

            CREATE TABLE IF NOT EXISTS project_content_characters (
                project TEXT NOT NULL,
                character_id TEXT NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project, character_id)
            );

            CREATE TABLE IF NOT EXISTS project_content_picker_log (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                plan_id TEXT,
                reason TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        _ensure_column(db, "pipeline_jobs", "hooks", "TEXT")
        _ensure_column(db, "pipeline_jobs", "selected_hook_id", "TEXT")
        _ensure_column(db, "pipeline_jobs", "script", "TEXT")
        _ensure_column(db, "pipeline_jobs", "audio_timestamps", "TEXT")
        _ensure_column(db, "pipeline_jobs", "asset_plan", "TEXT")
        _ensure_column(db, "pipeline_jobs", "video_manifest", "TEXT")
        _ensure_column(db, "pipeline_jobs", "subtitle_path", "TEXT")
        _ensure_column(db, "pipeline_jobs", "thumbnail_path", "TEXT")
        _ensure_column(db, "pipeline_jobs", "qa_report", "TEXT")
        _ensure_column(db, "pipeline_jobs", "youtube_video_id", "TEXT")
        _ensure_column(db, "pipeline_jobs", "youtube_url", "TEXT")
        _ensure_column(db, "pipeline_jobs", "upload_result", "TEXT")
        _ensure_column(db, "pipeline_jobs", "uploaded_at", "TEXT")
        _ensure_column(db, "pipeline_jobs", "analytics_followup_plan", "TEXT")
        _ensure_column(db, "pipeline_jobs", "review_status", "TEXT")
        _ensure_column(db, "pipeline_jobs", "review_notified_at", "TEXT")
        _ensure_column(db, "pipeline_jobs", "review_notification_path", "TEXT")
        _ensure_column(db, "pipeline_jobs", "quality_review_status", "TEXT")
        _ensure_column(db, "pipeline_jobs", "quality_review_result", "TEXT")
        _ensure_column(db, "pipeline_jobs", "quality_review_checked_at", "TEXT")
        _ensure_column(db, "pipeline_jobs", "public_publish_result", "TEXT")
        _ensure_column(db, "pipeline_jobs", "public_scheduled_at", "TEXT")
        _ensure_column(db, "pipeline_jobs", "public_published_at", "TEXT")
        _ensure_column(db, "topic_queue", "last_youtube_video_id", "TEXT")
        _ensure_column(db, "topic_queue", "last_analytics_json", "TEXT")
        _ensure_column(db, "topic_queue", "analytics_history_json", "TEXT")
        _ensure_column(db, "topic_queue", "performance_score", "REAL")
        _ensure_column(db, "topic_queue", "feedback_adjusted_score", "REAL")
        _ensure_column(db, "topic_queue", "last_performance_recommendation", "TEXT")
        _ensure_column(db, "topic_queue", "analytics_updated_at", "TEXT")
        _ensure_column(db, "intelligence_accounts", "last_refresh_at", "TEXT")
        _ensure_column(db, "intelligence_accounts", "next_refresh_at", "TEXT")
        _ensure_column(db, "intelligence_accounts", "enabled", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(db, "intelligence_accounts", "youtube_oauth_account_id", "INTEGER")
        _ensure_column(db, "intelligence_accounts", "gmail_oauth_account_id", "INTEGER")
        _ensure_column(db, "intelligence_accounts", "channel_id", "TEXT")
        _ensure_column(db, "intelligence_accounts", "channel_title", "TEXT")
        _ensure_column(db, "intelligence_accounts", "content_profile", "TEXT")
        _ensure_column(db, "intelligence_accounts", "automation_mode", "TEXT NOT NULL DEFAULT 'manual'")
        db.execute("CREATE VIEW IF NOT EXISTS production_jobs AS SELECT * FROM pipeline_jobs")
        db.commit()


def _ensure_column(db: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _normalize_automation_mode(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in {"manual", "auto_private", "auto_public"}:
        raise ValueError("automation_mode must be manual, auto_private, or auto_public")
    return normalized


async def init_intelligence_db(settings: Settings) -> None:
    await asyncio.to_thread(_init_intelligence_db_sync, intelligence_db_path(settings))


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    path = intelligence_db_path(settings)
    _init_intelligence_db_sync(path)
    connection = _connect(path)
    try:
        yield connection
    finally:
        connection.close()


def read_collection_run(settings: Settings, run_key: str) -> dict[str, Any] | None:
    with connect(settings) as db:
        row = db.execute("SELECT * FROM collection_runs WHERE run_key = ?", (run_key,)).fetchone()
        return dict(row) if row else None


def write_collection_run(
    settings: Settings,
    *,
    run_key: str,
    collector: str,
    status: str,
    message: str | None = None,
    next_retry_at: str | None = None,
    success: bool = False,
) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO collection_runs (
                run_key, collector, status, last_attempt_at, last_success_at, next_retry_at, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_key) DO UPDATE SET
                collector=excluded.collector,
                status=excluded.status,
                last_attempt_at=excluded.last_attempt_at,
                last_success_at=COALESCE(excluded.last_success_at, collection_runs.last_success_at),
                next_retry_at=excluded.next_retry_at,
                message=excluded.message
            """,
            (run_key, collector, status, now, now if success else None, next_retry_at, message),
        )
        db.commit()


def rate_limit_active(run: dict[str, Any] | None, *, interval: timedelta) -> bool:
    if not run or run.get("status") not in {"ready", "collected", "skipped"}:
        return False
    last_success = run.get("last_success_at")
    if not isinstance(last_success, str) or not last_success:
        return False
    try:
        last_dt = datetime.fromisoformat(last_success)
    except ValueError:
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=UTC)
    return utc_now() - last_dt.astimezone(UTC) < interval


def retry_blocked(run: dict[str, Any] | None) -> bool:
    if not run or not run.get("next_retry_at"):
        return False
    try:
        next_retry = datetime.fromisoformat(str(run["next_retry_at"]))
    except ValueError:
        return False
    if next_retry.tzinfo is None:
        next_retry = next_retry.replace(tzinfo=UTC)
    return utc_now() < next_retry.astimezone(UTC)


def upsert_intelligence_account_config(
    settings: Settings,
    *,
    account_id: str,
    niches: list[str],
    region: str = "PL",
    timeframe: str = "now 7-d",
    youtube_oauth_account_id: int | None = None,
    gmail_oauth_account_id: int | None = None,
    channel_id: str | None = None,
    channel_title: str | None = None,
    content_profile: str | None = None,
    automation_mode: str | None = None,
    next_refresh_at: str | None = None,
    preserve_existing_next_refresh: bool = True,
) -> None:
    normalized_account = str(account_id or "").strip()
    normalized_niches = list(dict.fromkeys(" ".join(str(item).split()) for item in niches if str(item).strip()))
    if not normalized_account or not normalized_niches:
        return
    from atnos.intelligence.youtube_trends import _normalize_region_code
    normalized_region = _normalize_region_code(region)
    normalized_timeframe = " ".join(str(timeframe or "now 7-d").replace("+", " ").split()) or "now 7-d"
    normalized_automation_mode = _normalize_automation_mode(automation_mode)
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            f"""
            INSERT INTO intelligence_accounts (
                account_id, niches, region, timeframe, youtube_oauth_account_id,
                gmail_oauth_account_id, channel_id, channel_title, content_profile,
                automation_mode, last_requested_at, next_refresh_at, enabled
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, (SELECT automation_mode FROM intelligence_accounts WHERE account_id = ?), 'manual'), ?, ?, 1)
            ON CONFLICT(account_id) DO UPDATE SET
                niches=excluded.niches,
                region=excluded.region,
                timeframe=excluded.timeframe,
                youtube_oauth_account_id=COALESCE(excluded.youtube_oauth_account_id, intelligence_accounts.youtube_oauth_account_id),
                gmail_oauth_account_id=COALESCE(excluded.gmail_oauth_account_id, intelligence_accounts.gmail_oauth_account_id),
                channel_id=COALESCE(excluded.channel_id, intelligence_accounts.channel_id),
                channel_title=COALESCE(excluded.channel_title, intelligence_accounts.channel_title),
                content_profile=COALESCE(excluded.content_profile, intelligence_accounts.content_profile),
                automation_mode=excluded.automation_mode,
                last_requested_at=excluded.last_requested_at,
                next_refresh_at={
                    "COALESCE(excluded.next_refresh_at, intelligence_accounts.next_refresh_at)"
                    if preserve_existing_next_refresh
                    else "excluded.next_refresh_at"
                },
                enabled=1
            """,
            (
                normalized_account,
                json_dumps(normalized_niches),
                normalized_region,
                normalized_timeframe,
                youtube_oauth_account_id,
                gmail_oauth_account_id,
                str(channel_id).strip() if channel_id else None,
                str(channel_title).strip() if channel_title else None,
                str(content_profile).strip() if content_profile else None,
                normalized_automation_mode,
                normalized_account,
                now,
                next_refresh_at,
            ),
        )
        db.commit()


def update_intelligence_youtube_oauth_account(
    settings: Settings,
    *,
    account_id: str,
    youtube_oauth_account_id: int,
) -> bool:
    """Przypisuje świeżo zatwierdzony OAuth wyłącznie do wskazanego kanału."""
    normalized_account = str(account_id or "").strip()
    if not normalized_account or youtube_oauth_account_id <= 0:
        return False

    with connect(settings) as db:
        cursor = db.execute(
            """
            UPDATE intelligence_accounts
            SET youtube_oauth_account_id = ?, last_requested_at = ?
            WHERE account_id = ?
            """,
            (youtube_oauth_account_id, utc_now_iso(), normalized_account),
        )
        db.commit()
    return bool(cursor.rowcount)


def set_intelligence_account_automation_mode(
    settings: Settings,
    *,
    account_id: str,
    automation_mode: str,
) -> dict[str, Any] | None:
    """Zapisuje jedyne dozwolone tryby automatyzacji dla pojedynczego kanalu."""
    normalized_account = str(account_id or "").strip()
    normalized_mode = _normalize_automation_mode(automation_mode)
    if not normalized_account or normalized_mode is None:
        return None
    with connect(settings) as db:
        cursor = db.execute(
            "UPDATE intelligence_accounts SET automation_mode = ?, last_requested_at = ? WHERE account_id = ?",
            (normalized_mode, utc_now_iso(), normalized_account),
        )
        db.commit()
    if not cursor.rowcount:
        return None
    return read_intelligence_account_config(settings, account_id=normalized_account)


def ensure_youtube_content_account(settings: Settings, *, account_id: str) -> dict[str, Any]:
    """Reconnect must create a pending OAuth row even if the channel was never registered."""
    existing = read_intelligence_account_config(settings, account_id=account_id)
    if existing is not None:
        return existing
    upsert_intelligence_account_config(
        settings,
        account_id=account_id,
        niches=["unspecified"],
        automation_mode="manual",
    )
    created = read_intelligence_account_config(settings, account_id=account_id)
    if created is None:
        raise ValueError("Selected content channel is not available for YouTube OAuth reconnect.")
    return created


def read_intelligence_account_config(settings: Settings, *, account_id: str) -> dict[str, Any] | None:
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
        return None
    with connect(settings) as db:
        row = db.execute(
            """
            SELECT account_id, niches, region, timeframe, last_requested_at,
                   last_refresh_at, next_refresh_at, enabled, youtube_oauth_account_id,
                   gmail_oauth_account_id, channel_id, channel_title, content_profile, automation_mode
            FROM intelligence_accounts
            WHERE account_id = ?
            """,
            (normalized_account,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["niches"] = [str(value) for value in json_loads(item.get("niches"), []) if str(value).strip()]
    item["enabled"] = bool(item.get("enabled"))
    item["automation_mode"] = _normalize_automation_mode(item.get("automation_mode")) or "manual"
    return item


def read_intelligence_account_configs(settings: Settings) -> list[dict[str, Any]]:
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT account_id, niches, region, timeframe, last_requested_at,
                   last_refresh_at, next_refresh_at, enabled, youtube_oauth_account_id,
                   gmail_oauth_account_id, channel_id, channel_title, content_profile, automation_mode
            FROM intelligence_accounts
            ORDER BY account_id ASC
            """
        ).fetchall()
    configs: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["niches"] = [str(value) for value in json_loads(item.get("niches"), []) if str(value).strip()]
        item["enabled"] = bool(item.get("enabled"))
        item["automation_mode"] = _normalize_automation_mode(item.get("automation_mode")) or "manual"
        configs.append(item)
    return configs


def set_intelligence_account_enabled(settings: Settings, *, account_id: str, enabled: bool) -> dict[str, Any] | None:
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
        return None
    with connect(settings) as db:
        db.execute(
            "UPDATE intelligence_accounts SET enabled = ? WHERE account_id = ?",
            (1 if enabled else 0, normalized_account),
        )
        db.commit()
    return read_intelligence_account_config(settings, account_id=normalized_account)


def read_due_intelligence_account_configs(settings: Settings) -> list[dict[str, Any]]:
    now = utc_now_iso()
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT account_id, niches, region, timeframe, last_requested_at, last_refresh_at, next_refresh_at,
                   youtube_oauth_account_id, gmail_oauth_account_id, channel_id, channel_title, content_profile,
                   automation_mode
            FROM intelligence_accounts
            WHERE enabled = 1 AND (next_refresh_at IS NULL OR next_refresh_at <= ?)
            ORDER BY COALESCE(next_refresh_at, last_requested_at) ASC
            """,
            (now,),
        ).fetchall()
    configs: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["niches"] = [str(value) for value in json_loads(item.get("niches"), []) if str(value).strip()]
        item["automation_mode"] = _normalize_automation_mode(item.get("automation_mode")) or "manual"
        configs.append(item)
    return configs


def mark_intelligence_account_refreshed(
    settings: Settings,
    *,
    account_id: str,
    interval: timedelta = timedelta(hours=6),
) -> None:
    now_dt = utc_now()
    now = now_dt.isoformat()
    next_refresh = (now_dt + interval).isoformat()
    with connect(settings) as db:
        db.execute(
            """
            UPDATE intelligence_accounts
            SET last_refresh_at = ?, next_refresh_at = ?
            WHERE account_id = ?
            """,
            (now, next_refresh, account_id),
        )
        db.commit()
