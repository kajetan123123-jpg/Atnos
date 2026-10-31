from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from atnos.core.config import Settings
from atnos.db.session import DatabaseManager
from atnos.intelligence.competitor import collect_competitors, read_due_competitor_groups
from atnos.intelligence.own_channel import collect_own_channel, read_due_own_accounts
from atnos.intelligence.google_trends import collect_google_trends
from atnos.intelligence.storage import connect, utc_now_iso
from atnos.intelligence.storage import (
    mark_intelligence_account_refreshed,
    read_due_intelligence_account_configs,
    write_collection_run,
)
from atnos.intelligence.youtube_trends import collect_youtube_trends
from atnos.scoring.topic_scorer import TopicScorer

logger = logging.getLogger(__name__)


class IntelligenceRefreshService:
    """Lightweight background refresher for persisted intelligence collectors."""

    def __init__(self, database: DatabaseManager, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self.settings.app_env == "test":
            return
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run(), name="intelligence-refresh")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.refresh_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Intelligence refresh loop failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15 * 60)
            except asyncio.TimeoutError:
                pass

    async def refresh_due(self) -> None:
        competitor_groups = defaultdict(list)
        for row in read_due_competitor_groups(self.settings):
            competitor_groups[str(row["account_id"])].append(str(row["source_url"]))
        own_accounts = [str(row["account_id"]) for row in read_due_own_accounts(self.settings)]
        signal_configs = read_due_intelligence_account_configs(self.settings)
        topic_accounts = _read_due_topic_accounts(self.settings)

        if not competitor_groups and not own_accounts and not signal_configs and not topic_accounts:
            return

        with self.database.session_scope() as session:
            for config in signal_configs:
                account_id = str(config["account_id"])
                niches = [str(item) for item in config.get("niches", []) if str(item).strip()]
                region = str(config.get("region") or "PL")
                timeframe = str(config.get("timeframe") or "now 7-d")
                if not niches:
                    write_collection_run(
                        self.settings,
                        run_key=f"intelligence_refresh:{account_id}",
                        collector="intelligence_refresh",
                        status="skipped_no_niches",
                        message="Saved intelligence account has no niches configured.",
                    )
                    continue
                try:
                    for niche in niches:
                        await collect_youtube_trends(
                            session=session,
                            settings=self.settings,
                            account_id=account_id,
                            niche=niche,
                            region=region,
                            force=False,
                        )
                    await collect_google_trends(
                        settings=self.settings,
                        keywords=niches,
                        timeframe=timeframe,
                        geo=region,
                    )
                    mark_intelligence_account_refreshed(self.settings, account_id=account_id)
                    write_collection_run(
                        self.settings,
                        run_key=f"intelligence_refresh:{account_id}",
                        collector="intelligence_refresh",
                        status="refreshed",
                        message=f"Refreshed YouTube and Google Trends for {len(niches)} niches.",
                        success=True,
                    )
                except Exception as exc:
                    write_collection_run(
                        self.settings,
                        run_key=f"intelligence_refresh:{account_id}",
                        collector="intelligence_refresh",
                        status="error",
                        message=str(exc),
                    )
                    logger.warning("Signal intelligence refresh failed for %s: %s", account_id, exc)
            for account_id, urls in competitor_groups.items():
                try:
                    await collect_competitors(
                        session=session,
                        settings=self.settings,
                        account_id=account_id,
                        channel_urls=urls,
                    )
                except Exception as exc:
                    logger.warning("Competitor intelligence refresh failed for %s: %s", account_id, exc)
            for account_id in own_accounts:
                try:
                    await collect_own_channel(session=session, settings=self.settings, account_id=account_id)
                except Exception as exc:
                    logger.warning("Own-channel intelligence refresh failed for %s: %s", account_id, exc)
        for account_id in topic_accounts:
            try:
                generated = await TopicScorer(self.settings).generate_topics(account_id=account_id, count=20)
                if not generated:
                    write_collection_run(
                        self.settings,
                        run_key=f"topic_generation:{account_id}",
                        collector="topic_generation",
                        status="skipped_no_real_signals",
                        message=(
                            "Scheduled topic generation skipped. Both yt_signals and gtrends_signals "
                            "are required before Atnos can generate non-hallucinated topics."
                        ),
                    )
                    logger.info("Skipped scheduled topic generation for %s: missing complete real signals.", account_id)
                    continue
                _store_generated_topics(self.settings, account_id=account_id, topics=generated)
                write_collection_run(
                    self.settings,
                    run_key=f"topic_generation:{account_id}",
                    collector="topic_generation",
                    status="generated",
                    message=f"Generated {len(generated)} scheduled topics from real intelligence signals.",
                    success=True,
                )
                logger.info("Generated scheduled topic queue for %s | count=%s", account_id, len(generated))
            except Exception as exc:
                write_collection_run(
                    self.settings,
                    run_key=f"topic_generation:{account_id}",
                    collector="topic_generation",
                    status="error",
                    message=str(exc),
                )
                logger.warning("Scheduled topic generation failed for %s: %s", account_id, exc)


def _read_due_topic_accounts(settings: Settings) -> list[str]:
    now_warsaw = datetime.now(ZoneInfo("Europe/Warsaw"))
    # The production scheduler must wait until 06:00. Test applications invoke
    # refresh_due() explicitly and must not become dependent on wall-clock time.
    if settings.app_env != "test" and now_warsaw.hour < 6:
        return []
    day_start = now_warsaw.replace(hour=6, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT DISTINCT account_id
            FROM yt_signals
            WHERE account_id IS NOT NULL AND account_id != ''
            """
        ).fetchall()
        due: list[str] = []
        for row in rows:
            account_id = str(row["account_id"])
            recent = db.execute(
                """
                SELECT 1
                FROM topic_queue
                WHERE account_id = ? AND created_at >= ?
                LIMIT 1
                """,
                (account_id, day_start),
            ).fetchone()
            if recent is None:
                due.append(account_id)
        return due


def _store_generated_topics(settings: Settings, *, account_id: str, topics: list[dict[str, Any]]) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        for topic in topics:
            dimensions = topic.get("dimensions") if isinstance(topic.get("dimensions"), dict) else {}
            flags = topic.get("flags") if isinstance(topic.get("flags"), dict) else {}
            total_score = _float(topic.get("total_score"))
            topic_status = "rejected" if total_score < 30 else "pending"
            db.execute(
                """
                INSERT INTO topic_queue (
                    id, account_id, title, description, inspired_by, status, total_score,
                    viral_potential, search_demand, competition_gap, trend_velocity,
                    niche_fit, freshness, clickbait_risk, evergreen, shorts_potential,
                    llm_unavailable, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    str(topic.get("id") or f"topic-{account_id}-{now}"),
                    account_id,
                    str(topic.get("title") or topic.get("topic") or "").strip(),
                    str(topic.get("description") or "").strip(),
                    "scheduled_topic_generation",
                    topic_status,
                    total_score,
                    _float(dimensions.get("viral_potential")),
                    _float(dimensions.get("search_demand")),
                    _float(dimensions.get("competition_gap")),
                    _float(dimensions.get("trend_velocity")),
                    _float(dimensions.get("niche_fit")),
                    _float(dimensions.get("freshness")),
                    int(bool(flags.get("clickbait_risk"))),
                    int(bool(flags.get("evergreen"))),
                    int(bool(flags.get("shorts_potential"))),
                    int(bool(flags.get("llm_unavailable"))),
                    now,
                    now,
                ),
            )
        db.commit()


def _float(value: object) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0
