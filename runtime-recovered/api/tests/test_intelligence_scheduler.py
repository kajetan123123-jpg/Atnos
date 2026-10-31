from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.scheduler import IntelligenceRefreshService
from atnos.intelligence.storage import connect, init_intelligence_db, json_dumps, upsert_intelligence_account_config
from test_support import create_test_app


def _seed_youtube_signal(settings, *, account_id: str = "finance-pl") -> None:
    now = datetime.now(UTC).isoformat()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-scheduler-1', ?, 'finanse osobiste', 'PL', 'Inflacja i oszczednosci',
                150000, 4000, 120, ?, 'UC1', 'Finance', 'https://img/1.jpg', ?
            )
            """,
            (account_id, now, now),
        )
        db.commit()


def _seed_google_trend(settings) -> None:
    now = datetime.now(UTC).isoformat()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES ('inflacja oszczednosci', 'now 7-d', 'PL', 82, ?, '[]', 'ready', ?)
            """,
            (json_dumps(["lokaty", "konto oszczednosciowe"]), now),
        )
        db.commit()


def test_intelligence_scheduler_records_skip_when_topic_generation_has_partial_signals(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_youtube_signal(settings)

    asyncio.run(IntelligenceRefreshService(app.state.database, settings).refresh_due())

    with connect(settings) as db:
        topics = db.execute("SELECT COUNT(*) AS count FROM topic_queue WHERE account_id = 'finance-pl'").fetchone()
        run = db.execute("SELECT status, message FROM collection_runs WHERE run_key = 'topic_generation:finance-pl'").fetchone()

    assert topics["count"] == 0
    assert run["status"] == "skipped_no_real_signals"
    assert "yt_signals and gtrends_signals" in run["message"]


def test_intelligence_scheduler_records_generated_topics_when_real_signals_exist(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_youtube_signal(settings)
    _seed_google_trend(settings)

    async def fake_generate_topics(self, *, account_id: str, count: int = 20, geo: str = "PL"):
        return [
            {
                "id": "scheduled-topic-1",
                "title": "inflacja oszczednosci",
                "description": "Temat z realnych sygnalow.",
                "total_score": 81,
                "dimensions": {
                    "viral_potential": 22,
                    "search_demand": 16,
                    "competition_gap": 15,
                    "trend_velocity": 8,
                    "niche_fit": 10,
                    "freshness": 10,
                },
                "flags": {"clickbait_risk": False, "evergreen": True, "shorts_potential": True, "llm_unavailable": False},
            }
        ]

    monkeypatch.setattr("atnos.intelligence.scheduler.TopicScorer.generate_topics", fake_generate_topics)

    asyncio.run(IntelligenceRefreshService(app.state.database, settings).refresh_due())

    with connect(settings) as db:
        topic = db.execute("SELECT id, status, total_score FROM topic_queue WHERE id = 'scheduled-topic-1'").fetchone()
        run = db.execute("SELECT status, message FROM collection_runs WHERE run_key = 'topic_generation:finance-pl'").fetchone()

    assert topic["status"] == "pending"
    assert topic["total_score"] == 81
    assert run["status"] == "generated"
    assert "Generated 1 scheduled topics" in run["message"]


def test_intelligence_scheduler_refreshes_saved_youtube_and_google_trend_configs(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    upsert_intelligence_account_config(
        settings,
        account_id="finance-pl",
        niches=["finanse osobiste", "oszczedzanie"],
        region="PL",
        timeframe="now 7-d",
    )

    calls: dict[str, list] = {"youtube": [], "google": []}

    async def fake_youtube_trends(*, session, settings, account_id, niche, region="PL", force=False):
        calls["youtube"].append((account_id, niche, region, force))
        return {"status": "collected", "count": 0, "items": []}

    async def fake_google_trends(*, settings, keywords, timeframe="now 7-d", geo="PL"):
        calls["google"].append((tuple(keywords), timeframe, geo))
        return {"status": "collected", "count": len(keywords), "items": []}

    monkeypatch.setattr("atnos.intelligence.scheduler.collect_youtube_trends", fake_youtube_trends)
    monkeypatch.setattr("atnos.intelligence.scheduler.collect_google_trends", fake_google_trends)

    asyncio.run(IntelligenceRefreshService(app.state.database, settings).refresh_due())

    assert calls["youtube"] == [
        ("finance-pl", "finanse osobiste", "PL", False),
        ("finance-pl", "oszczedzanie", "PL", False),
    ]
    assert calls["google"] == [(("finanse osobiste", "oszczedzanie"), "now 7-d", "PL")]
    with connect(settings) as db:
        config = db.execute("SELECT last_refresh_at, next_refresh_at FROM intelligence_accounts WHERE account_id = 'finance-pl'").fetchone()
        run = db.execute("SELECT status, message FROM collection_runs WHERE run_key = 'intelligence_refresh:finance-pl'").fetchone()

    assert config["last_refresh_at"]
    assert config["next_refresh_at"]
    assert run["status"] == "refreshed"
    assert "2 niches" in run["message"]
