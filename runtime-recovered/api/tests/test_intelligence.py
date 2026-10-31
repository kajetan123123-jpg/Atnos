from __future__ import annotations

import asyncio
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient

from atnos.api.routes.intelligence import _collector_error_payload
from atnos.intelligence.storage import connect, init_intelligence_db
from test_support import create_test_app


def test_intelligence_oauth_migration_message_has_no_mojibake() -> None:
    payload = _collector_error_payload(RuntimeError("no such table: oauth_connections"))
    message = payload["message"]

    assert payload["status"] == "unavailable"
    assert "zeby uruchomic migracje bazy" in message
    assert "Ĺ" not in message
    assert "Ä" not in message


def test_intelligence_schema_matches_public_tables(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))

    expected_columns = {
        "yt_signals": {"id", "video_id", "title", "view_count", "like_count", "comment_count", "niche", "collected_at"},
        "gtrends_signals": {"id", "keyword", "interest_score", "related_rising", "timeframe", "geo", "collected_at"},
        "competitor_channels": {
            "id",
            "account_id",
            "channel_id",
            "channel_title",
            "avg_views_7d",
            "posting_frequency_per_week",
            "top_format",
            "last_refreshed",
        },
        "competitor_videos": {"id", "channel_id", "video_id", "title", "views", "duration_seconds", "thumbnail_url"},
        "own_performance": {"id", "account_id", "video_id", "views", "watch_time_min", "avg_view_pct", "ctr"},
        "reference_channels": {
            "id",
            "channel_id",
            "channel_title",
            "hook_patterns",
            "avg_duration_sec",
            "posting_cadence_per_week",
            "avg_engagement_rate",
            "last_refreshed",
        },
        "intelligence_accounts": {
            "account_id",
            "niches",
            "region",
            "timeframe",
            "last_requested_at",
            "last_refresh_at",
            "next_refresh_at",
            "enabled",
        },
    }

    with connect(settings) as db:
        for table, columns in expected_columns.items():
            actual = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
            assert columns.issubset(actual), table


def test_intelligence_read_endpoints_return_sqlite_payloads(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))

    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, collected_at
            )
            VALUES ('v1', 'a1', 'finanse', 'PL', 'Test video', 999, 10, 1, '2026-06-01T00:00:00+00:00')
            """
        )
        db.execute(
            """
            INSERT INTO reference_channels (
                channel_id, source_url, channel_title, hook_patterns, avg_duration_sec,
                posting_cadence_per_week, avg_engagement_rate, collected_at, last_refreshed
            )
            VALUES (
                'UCREF', 'https://youtube.com/@ref', 'Ref', '["7 rzeczy"]', 58, 4.5, 0.04,
                '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )
        db.commit()

    client = TestClient(app)
    signals = client.get("/api/intelligence/signals?niche=finanse").json()
    references = client.get("/api/intelligence/reference").json()

    assert signals[0]["video_id"] == "v1"
    assert references[0]["hook_patterns"] == ["7 rzeczy"]


def test_intelligence_run_marks_missing_oauth_runtime_table_as_unavailable(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    client = TestClient(app)
    response = client.post(
        "/api/intelligence/run",
        json={
            "account_id": "yt-finanse-pl",
            "niches": [],
            "competitor_urls": [],
            "reference_urls": [],
            "region": "PL",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_errors"
    assert payload["results"]["own_channel"]["status"] == "needs_config"
    assert payload["results"]["own_channel"]["next_retry_at"]
    assert payload["results"]["own_channel"]["next_actions"]


def test_intelligence_run_persists_niche_config_for_scheduler(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    async def fake_youtube_trends(**_kwargs):
        return {"status": "needs_config", "count": 0, "items": []}

    async def fake_gtrends(**_kwargs):
        return {"status": "collected", "count": 0, "items": []}

    import atnos.api.routes.intelligence as route

    monkeypatch.setattr(route, "collect_youtube_trends", fake_youtube_trends)
    monkeypatch.setattr(route, "collect_google_trends", fake_gtrends)

    client = TestClient(app)
    response = client.post(
        "/api/intelligence/run",
        json={
            "account_id": "yt-finanse-pl",
            "niches": ["finanse osobiste", "oszczedzanie"],
            "competitor_urls": [],
            "reference_urls": [],
            "region": "PL",
        },
    )

    assert response.status_code == 200
    with connect(app.state.settings) as db:
        row = db.execute("SELECT * FROM intelligence_accounts WHERE account_id = 'yt-finanse-pl'").fetchone()

    assert row["region"] == "PL"
    assert row["timeframe"] == "now 7-d"
    assert "finanse osobiste" in row["niches"]
    assert "oszczedzanie" in row["niches"]


def test_intelligence_refresh_alias_queues_background_job(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    async def fake_youtube_trends(**_kwargs):
        return {"status": "collected", "count": 0, "items": []}

    async def fake_gtrends(**_kwargs):
        return {"status": "collected", "count": 0, "items": []}

    async def fake_own(**_kwargs):
        return {"status": "invalid_request", "count": 0}

    import atnos.api.routes.intelligence as route

    monkeypatch.setattr(route, "collect_youtube_trends", fake_youtube_trends)
    monkeypatch.setattr(route, "collect_google_trends", fake_gtrends)
    monkeypatch.setattr(route, "collect_own_channel", fake_own)

    client = TestClient(app)
    response = client.post(
        "/api/intelligence/refresh",
        json={
            "account_id": "yt-finanse-pl",
            "niches": ["finanse osobiste"],
            "competitor_urls": [],
            "reference_urls": [],
            "region": "PL",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["job_id"].startswith("intelligence-refresh-")
    jobs = app.state.intelligence_refresh_jobs
    assert payload["job_id"] in jobs
    assert jobs[payload["job_id"]]["status"] in {"completed", "completed_with_errors"}


def test_intelligence_account_config_endpoint_persists_scheduler_config_without_collecting(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    client = TestClient(app)
    response = client.post(
        "/api/intelligence/accounts",
        json={
            "account_id": "yt-finanse-pl",
            "niches": ["finanse osobiste", "oszczedzanie", "finanse osobiste"],
            "region": "pl",
            "timeframe": "now+7-d",
            "refresh_now": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "configured"
    assert payload["quota_spent"] is False
    assert payload["scheduler"]["due_now"] is True
    assert payload["account"]["account_id"] == "yt-finanse-pl"
    assert payload["account"]["niches"] == ["finanse osobiste", "oszczedzanie"]
    assert payload["account"]["region"] == "PL"
    assert payload["account"]["timeframe"] == "now 7-d"
    assert payload["account"]["next_refresh_at"]
    with connect(app.state.settings) as db:
        yt_count = db.execute("SELECT COUNT(*) AS count FROM yt_signals").fetchone()["count"]
        gt_count = db.execute("SELECT COUNT(*) AS count FROM gtrends_signals").fetchone()["count"]
    assert yt_count == 0
    assert gt_count == 0

    listed = client.get("/api/intelligence/accounts").json()
    assert listed["accounts"][0]["account_id"] == "yt-finanse-pl"
    assert listed["accounts"][0]["enabled"] is True


def test_intelligence_account_config_can_disable_scheduler_account(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    client = TestClient(app)
    client.post(
        "/api/intelligence/accounts",
        json={
            "account_id": "yt-finanse-pl",
            "niches": ["finanse osobiste"],
            "region": "PL",
            "refresh_now": False,
        },
    )
    response = client.patch("/api/intelligence/accounts/yt-finanse-pl", json={"enabled": False})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "updated"
    assert payload["account"]["enabled"] is False


def test_intelligence_youtube_data_api_key_endpoint_persists_without_leaking_secret(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.youtube_data_api_key = ""
    asyncio.run(init_intelligence_db(app.state.settings))

    client = TestClient(app)
    api_key = "AIzaSyTestYouTubeDataApiKey1234567890"
    response = client.post("/api/intelligence/youtube-data-api-key", json={"api_key": api_key})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "configured"
    assert payload["youtube_data_api_key_configured"] is True
    assert payload["collector_mode"] == "api_key"
    assert payload["masked_key"] == "AIza...7890"
    assert api_key not in response.text
    assert app.state.settings.youtube_data_api_key == api_key

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"YOUTUBE_DATA_API_KEY={api_key}" in env_text

    status_payload = client.get(
        "/api/intelligence/status",
        params={"account_id": "yt-finanse-pl", "niches": "finanse osobiste", "region": "PL"},
    ).json()
    assert status_payload["youtube_configured"] is True
    assert status_payload["credentials"]["api_key_configured"] is True
    assert status_payload["credentials"]["collector_mode"] == "api_key"
    assert "youtube_data_api_key_or_oauth" not in status_payload["blockers"]


def test_intelligence_cli_reports_missing_youtube_config_without_fake_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.youtube_data_api_key = ""
    settings.oauth_google_client_id = ""
    settings.oauth_google_client_secret_ref = ""
    asyncio.run(init_intelligence_db(settings))

    async def fake_gtrends(*, settings, keywords, timeframe="now 7-d", geo="PL"):
        with connect(settings) as db:
            db.execute(
                """
                INSERT INTO gtrends_signals (
                    keyword, timeframe, geo, interest_score, related_rising,
                    related_topics, status, collected_at
                )
                VALUES (?, ?, ?, 10, '[]', '[]', 'ready', '2026-06-01T00:00:00+00:00')
                """,
                (keywords[0], timeframe, geo),
            )
            db.commit()
        return {"status": "collected", "count": 1, "items": [{"keyword": keywords[0]}]}

    import scripts.run_intelligence_collectors as cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_runtime_env_files", lambda: ())
    monkeypatch.setattr(cli, "collect_google_trends", fake_gtrends)

    args = type(
        "Args",
        (),
        {
            "account_id": "yt-finanse-pl",
            "niches": "finanse osobiste",
            "region": "PL",
            "timeframe": "now 7-d",
            "force": True,
        },
    )()

    payload = asyncio.run(cli._run(args))

    assert payload["status"] == "needs_youtube_signals"
    assert payload["youtube_trends"][0]["status"] == "needs_config"
    assert payload["after"]["gtrends_signals"] == 1
    assert payload["after"]["yt_signals"] == 0
