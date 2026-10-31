from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


TESTS_DIR = Path(__file__).resolve().parent
API_ROOT = TESTS_DIR.parents[0]
SRC_ROOT = API_ROOT / "src"
SCRIPTS_ROOT = API_ROOT / "scripts"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.intelligence.storage import connect, init_intelligence_db
from test_support import build_test_settings

import asyncio
import run_intelligence_collectors


def test_research_readiness_reports_missing_youtube_signals_and_credentials(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.youtube_data_api_key = ""
    settings.oauth_google_client_id = ""
    settings.oauth_google_client_secret_ref = None
    asyncio.run(init_intelligence_db(settings))

    readiness = run_intelligence_collectors._research_readiness(
        settings,
        account_id="yt-finanse-pl",
        niches=["finanse osobiste"],
        region="PL",
    )

    assert readiness["ready_for_topic_scoring"] is False
    assert "youtube_data_api_key_or_oauth" in readiness["blockers"]
    assert "yt_signals_empty" in readiness["blockers"]
    assert readiness["counts"]["yt_signals"] == 0
    assert readiness["niches"][0]["yt_signal_count"] == 0
    assert readiness["next_actions"]


def test_research_readiness_accepts_real_signal_rows(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.youtube_data_api_key = "youtube-data-key-test"
    asyncio.run(init_intelligence_db(settings))
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-1', 'yt-finanse-pl', 'finanse osobiste', 'PL', 'Budzet domowy',
                100000, 3000, 100, '2026-06-01T00:00:00Z', 'UC1', 'Finance', 'https://img/1.jpg',
                '2026-06-02T08:00:00+00:00'
            )
            """
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'finanse osobiste', 'now 7-d', 'PL', 75, '["budzet"]', '[]', 'ready',
                '2026-06-02T08:00:00+00:00'
            )
            """
        )
        db.commit()

    readiness = run_intelligence_collectors._research_readiness(
        settings,
        account_id="yt-finanse-pl",
        niches=["finanse osobiste"],
        region="PL",
    )

    assert readiness["ready_for_topic_scoring"] is True
    assert readiness["blockers"] == []
    assert readiness["youtube_data_api_key_configured"] is True
    assert readiness["counts"]["yt_signals"] == 1
    assert readiness["counts"]["gtrends_signals"] == 1


def test_run_records_youtube_needs_config_collection_runs(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.youtube_data_api_key = ""
    settings.oauth_google_client_id = ""
    settings.oauth_google_client_secret_ref = None

    async def fake_google_trends(**_kwargs):
        return {"status": "collected", "count": 1, "items": []}

    monkeypatch.setattr(run_intelligence_collectors, "load_runtime_env_files", lambda: None)
    monkeypatch.setattr(run_intelligence_collectors, "get_settings", lambda: settings)
    monkeypatch.setattr(run_intelligence_collectors, "collect_google_trends", fake_google_trends)

    payload = asyncio.run(
        run_intelligence_collectors._run(
            SimpleNamespace(
                account_id="yt-finanse-pl",
                niches="finanse osobiste,oszczedzanie",
                region="PL",
                timeframe="now 7-d",
                force=True,
            )
        )
    )

    assert payload["status"] == "needs_youtube_signals"
    assert payload["youtube_trends"][0]["status"] == "needs_config"
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT run_key, status
            FROM collection_runs
            WHERE collector = 'youtube_trends'
            ORDER BY run_key
            """
        ).fetchall()
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"needs_config"}
    assert "youtube_data_api_key_or_oauth" in payload["research_readiness_after"]["blockers"]
