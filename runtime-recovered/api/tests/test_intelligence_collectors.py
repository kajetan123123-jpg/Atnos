from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence import competitor, google_trends, own_channel, reference_channels, youtube_trends
from atnos.intelligence import youtube_api
from atnos.intelligence.storage import connect, init_intelligence_db, read_collection_run
from atnos.services.youtube_connector_service import YouTubeConnectorError
from atnos.api.routes.intelligence import _build_intelligence_status
from test_support import build_test_settings
from test_support import create_test_app


class _Execute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _ErrorExecute:
    def __init__(self, error):
        self.error = error

    def execute(self):
        raise self.error


class _HttpResponse:
    def __init__(self, status: int):
        self.status = status
        self.reason = "test"


def _http_error(status: int, content: bytes) -> HttpError:
    return HttpError(_HttpResponse(status), content)


class _SearchResource:
    def list(self, **kwargs):
        if kwargs.get("type") == "channel":
            return _Execute(
                {
                    "items": [
                        {
                            "id": {"channelId": "UC_COMP"},
                            "snippet": {"channelId": "UC_COMP"},
                        }
                    ]
                }
            )
        return _Execute(
            {
                "items": [
                    {"id": {"videoId": "v1"}, "snippet": {"title": "A"}},
                    {"id": {"videoId": "v2"}, "snippet": {"title": "B"}},
                ]
            }
        )


class _ChannelsResource:
    def list(self, **kwargs):
        channel_id = kwargs.get("id") or "UC_COMP"
        return _Execute(
            {
                "items": [
                    {
                        "id": channel_id,
                        "snippet": {"title": "Competitor"},
                        "contentDetails": {},
                    }
                ]
            }
        )


class _VideosResource:
    def list(self, **kwargs):
        ids = str(kwargs.get("id") or "v1,v2").split(",")
        return _Execute(
            {
                "items": [
                    {
                        "id": video_id,
                        "snippet": {
                            "title": f"7 lekcji finansowych {video_id}",
                            "publishedAt": "2026-05-30T10:00:00Z",
                            "channelId": "UC_COMP",
                            "channelTitle": "Competitor",
                            "thumbnails": {"high": {"url": f"https://img/{video_id}.jpg"}},
                        },
                        "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "5"},
                        "contentDetails": {"duration": "PT58S"},
                    }
                    for video_id in ids
                    if video_id
                ]
            }
        )


class _YouTube:
    def search(self):
        return _SearchResource()

    def videos(self):
        return _VideosResource()

    def channels(self):
        return _ChannelsResource()


def test_intelligence_db_migration_creates_core_tables(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    asyncio.run(init_intelligence_db(settings))

    with connect(settings) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%signals' OR name='own_performance'"
            ).fetchall()
        }

    assert {"yt_signals", "gtrends_signals", "own_performance"}.issubset(tables)


def test_youtube_trends_collects_and_rate_limits(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    monkeypatch.setattr(youtube_trends, "build_youtube_data_client", lambda session, settings: _YouTube())

    result = asyncio.run(
        youtube_trends.collect_youtube_trends(
            session=object(),
            settings=settings,
            account_id="yt-finance",
            niche="finanse osobiste",
            region="PL",
        )
    )
    second = asyncio.run(
        youtube_trends.collect_youtube_trends(
            session=object(),
            settings=settings,
            account_id="yt-finance",
            niche="finanse osobiste",
            region="PL",
        )
    )

    assert result["status"] == "collected"
    assert result["count"] == 2
    assert second["status"] == "rate_limited"
    assert second["count"] == 2


def test_youtube_trends_records_needs_config_instead_of_crashing(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)

    def missing_config(_session, _settings):
        raise youtube_api.YouTubeCollectorError("YouTube OAuth provider is not configured.")

    monkeypatch.setattr(youtube_trends, "build_youtube_data_client", missing_config)

    result = asyncio.run(
        youtube_trends.collect_youtube_trends(
            session=object(),
            settings=settings,
            account_id="yt-finance",
            niche="finanse osobiste",
            region="PL",
        )
    )

    assert result["status"] == "needs_config"
    assert result["count"] == 0
    assert result["next_actions"]
    with connect(settings) as db:
        row = db.execute(
            "SELECT status, message FROM collection_runs WHERE run_key = ?",
            ("youtube_trends:yt-finance:finanse osobiste:PL",),
        ).fetchone()
    assert row["status"] == "needs_config"
    assert "not configured" in row["message"]


def test_youtube_data_client_uses_public_api_key_before_oauth(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.youtube_data_api_key = "youtube-data-key-test"
    captured = {}

    def fake_build(api_name, api_version, **kwargs):
        captured["api_name"] = api_name
        captured["api_version"] = api_version
        captured["kwargs"] = kwargs
        return _YouTube()

    def forbidden_oauth(*args, **kwargs):
        raise AssertionError("Public YouTube research should not require OAuth when API key is configured.")

    monkeypatch.setattr(youtube_api, "build", fake_build)
    monkeypatch.setattr(youtube_api.YouTubeConnectorService, "build_data_client", forbidden_oauth)

    client = youtube_api.build_youtube_data_client(session=object(), settings=settings)

    assert isinstance(client, _YouTube)
    assert captured["api_name"] == "youtube"
    assert captured["api_version"] == "v3"
    assert captured["kwargs"]["developerKey"] == "youtube-data-key-test"
    assert captured["kwargs"]["cache_discovery"] is False
    assert youtube_api.youtube_data_client_mode(settings) == "api_key"


def test_youtube_data_client_maps_missing_oauth_to_collector_error(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.youtube_data_api_key = ""

    class MissingOAuthConnector:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_data_client(self):
            raise YouTubeConnectorError("Connect YouTube Studio before running this action.")

    monkeypatch.setattr(youtube_api, "YouTubeConnectorService", MissingOAuthConnector)

    try:
        youtube_api.build_youtube_data_client(session=object(), settings=settings)
    except youtube_api.YouTubeCollectorError as exc:
        assert "Connect YouTube Studio" in str(exc)
    else:
        raise AssertionError("Expected missing OAuth to be normalized as YouTubeCollectorError.")


def test_intelligence_status_reports_research_blockers_without_quota_spend(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))

    with TestClient(app) as client:
        response = client.get(
            "/api/intelligence/status",
            params={"account_id": "yt-finance", "niches": "finanse osobiste", "region": "PL"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_for_topic_generation"] is False
    assert "youtube_data_api_key_or_oauth" in payload["blockers"]
    assert "yt_signals_empty" in payload["blockers"]
    assert "gtrends_signals_empty" in payload["blockers"]
    assert payload["credentials"]["api_key_configured"] is False
    assert payload["credentials"]["oauth_ready"] is False
    assert payload["credentials"]["collector_mode"] == "oauth"
    assert payload["credentials"]["can_collect_youtube_trends"] is False
    assert "YOUTUBE_DATA_API_KEY" in payload["credentials"]["required_one_of"]
    assert payload["counts"]["yt_signals"] == 0
    assert payload["yt_by_niche"][0]["count"] == 0
    assert payload["next_actions"]


def test_intelligence_status_includes_latest_collector_runs(tmp_path: Path) -> None:
    from atnos.intelligence.storage import write_collection_run

    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    write_collection_run(
        settings,
        run_key="youtube_trends:yt-finance:finanse osobiste:PL",
        collector="youtube_trends",
        status="needs_config",
        message="YouTube OAuth provider is not configured.",
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/intelligence/status",
            params={"account_id": "yt-finance", "niches": "finanse osobiste", "region": "PL"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["collector_runs"][0]["collector"] == "youtube_trends"
    assert payload["collector_runs"][0]["status"] == "needs_config"
    assert "not configured" in payload["collector_runs"][0]["message"]


def test_intelligence_status_allows_topic_generation_when_real_signal_tables_have_rows(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.youtube_data_api_key = "test-youtube-api-key"
    asyncio.run(init_intelligence_db(settings))
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-ready-1', 'yt-finance', 'finanse osobiste', 'PL', 'Budzet domowy w 60 sekund',
                100000, 4000, 120, '2026-06-01T10:00:00Z', 'UC1', 'Finance', 'https://img/1.jpg',
                '2026-06-02T07:00:00+00:00'
            )
            """
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'finanse osobiste', 'now 7-d', 'PL', 80, '["budzet domowy"]', '[]', 'ready',
                '2026-06-02T07:00:00+00:00'
            )
            """
        )
        db.commit()

    payload = _build_intelligence_status(
        settings=settings,
        database=app.state.database,
        account_id="yt-finance",
        niches="finanse osobiste",
        region="PL",
    )
    assert payload["youtube_client_mode"] == "api_key"
    assert payload["youtube_configured"] is True
    assert payload["ready_for_topic_generation"] is True
    assert payload["blockers"] == []
    assert payload["credentials"]["api_key_configured"] is True
    assert payload["credentials"]["collector_mode"] == "api_key"
    assert payload["credentials"]["can_collect_youtube_trends"] is True
    assert payload["counts"]["yt_signals"] == 1
    assert payload["counts"]["gtrends_signals"] == 1


def test_competitor_collector_stores_channel_and_videos(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    monkeypatch.setattr(competitor, "build_youtube_data_client", lambda session, settings: _YouTube())

    result = asyncio.run(
        competitor.collect_competitors(
            session=object(),
            settings=settings,
            account_id="yt-finance",
            channel_urls=["https://www.youtube.com/@competitor"],
        )
    )

    assert result["status"] == "collected"
    assert result["channels"][0]["channel_id"] == "UC_COMP"
    assert result["channels"][0]["top_performing_format"] == "numbered_list"
    assert len(result["videos"]) == 2


def test_competitor_collector_returns_needs_config_for_missing_youtube_credentials(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)

    def missing_credentials(_session, _settings):
        raise youtube_api.YouTubeCollectorError("Connect YouTube Studio before running this action.")

    monkeypatch.setattr(competitor, "build_youtube_data_client", missing_credentials)

    result = asyncio.run(
        competitor.collect_competitors(
            session=object(),
            settings=settings,
            account_id="yt-finance",
            channel_urls=["https://www.youtube.com/@competitor"],
        )
    )

    assert result["status"] == "needs_config"
    assert result["channels"] == []
    assert result["next_actions"]
    run = read_collection_run(settings, "competitor:yt-finance")
    assert run is not None
    assert run["status"] == "needs_config"


class _AnalyticsReports:
    def query(self, **kwargs):
        return _Execute(
            {
                "columnHeaders": [
                    {"name": "video"},
                    {"name": "views"},
                    {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"},
                    {"name": "averageViewPercentage"},
                    {"name": "subscribersGained"},
                    {"name": "likes"},
                    {"name": "comments"},
                    {"name": "shares"},
                    {"name": "cardClickRate"},
                ],
                "rows": [["v1", 1200, 300.5, 35.2, 61.0, 8, 80, 9, 4, 3.1]],
            }
        )


class _Analytics:
    def reports(self):
        return _AnalyticsReports()


class _FailingAnalyticsReports:
    def query(self, **kwargs):
        return _ErrorExecute(
            _http_error(
                403,
                b'{"error":{"errors":[{"reason":"accessNotConfigured"}],'
                b'"message":"YouTube Analytics API has not been used in project 265469749111 before or it is disabled. Enable it."}}',
            )
        )


class _FailingAnalytics:
    def reports(self):
        return _FailingAnalyticsReports()


class _OwnChannelsResource:
    def list(self, **kwargs):
        return _Execute(
            {
                "items": [
                    {
                        "id": "UC_OWN",
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU_OWN"}},
                    }
                ]
            }
        )


class _PlaylistItemsResource:
    def list(self, **kwargs):
        return _Execute(
            {
                "items": [
                    {"contentDetails": {"videoId": "own1"}},
                    {"contentDetails": {"videoId": "own2"}},
                ]
            }
        )


class _OwnFallbackYouTube:
    def channels(self):
        return _OwnChannelsResource()

    def playlistItems(self):
        return _PlaylistItemsResource()

    def videos(self):
        return _VideosResource()


def test_own_channel_collector_stores_analytics(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    monkeypatch.setattr(own_channel, "build_youtube_analytics_client", lambda session, settings: _Analytics())
    monkeypatch.setattr(own_channel, "build_youtube_data_client", lambda session, settings: _YouTube())

    result = asyncio.run(
        own_channel.collect_own_channel(session=object(), settings=settings, account_id="yt-finance")
    )

    assert result["status"] == "collected"
    assert result["items"][0]["video_id"] == "v1"
    assert result["items"][0]["avg_view_pct"] == 61.0
    assert result["items"][0]["ctr"] == 3.1
    run = read_collection_run(settings, "own_channel:yt-finance")
    assert run is not None
    assert run["status"] == "collected"
    with connect(settings) as db:
        account = db.execute("SELECT next_refresh_at FROM own_channel_accounts WHERE account_id = 'yt-finance'").fetchone()
    assert account is not None
    assert account["next_refresh_at"] is not None


def test_own_channel_collector_falls_back_to_data_api_when_analytics_api_disabled(
    monkeypatch, tmp_path: Path
) -> None:
    settings = build_test_settings(tmp_path)
    monkeypatch.setattr(own_channel, "build_youtube_analytics_client", lambda session, settings: _FailingAnalytics())
    monkeypatch.setattr(own_channel, "build_youtube_data_client", lambda session, settings: _OwnFallbackYouTube())

    result = asyncio.run(
        own_channel.collect_own_channel(session=object(), settings=settings, account_id="yt-finance")
    )

    assert result["status"] == "collected"
    assert result["degraded"] is True
    assert result["fallback_source"] == "youtube_data_api_v3"
    assert "averageViewPercentage" in result["metrics_missing"]
    assert result["count"] == 2
    assert result["items"][0]["video_id"] == "own1"
    assert result["items"][0]["views"] == 1000
    assert result["items"][0]["likes"] == 50
    assert result["items"][0]["comments"] == 5
    assert result["items"][0]["avg_view_pct"] == 0.0
    run = read_collection_run(settings, "own_channel:yt-finance")
    assert run is not None
    assert run["status"] == "collected_degraded"
    assert "limited metrics" in run["message"]

    with connect(settings) as db:
        rows = db.execute(
            "SELECT video_id, views, likes, comments, avg_view_pct FROM own_performance ORDER BY video_id"
        ).fetchall()
    assert [row["video_id"] for row in rows] == ["own1", "own2"]
    assert rows[0]["avg_view_pct"] == 0.0


def test_own_channel_collector_returns_needs_config_and_schedules_retry(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)

    def missing_oauth(_session, _settings):
        raise RuntimeError("YouTube OAuth token is missing or not configured.")

    monkeypatch.setattr(own_channel, "build_youtube_analytics_client", missing_oauth)

    result = asyncio.run(
        own_channel.collect_own_channel(session=object(), settings=settings, account_id="yt-finance")
    )

    assert result["status"] == "needs_config"
    assert result["next_actions"]
    run = read_collection_run(settings, "own_channel:yt-finance")
    assert run is not None
    assert run["status"] == "needs_config"
    assert run["next_retry_at"]
    with connect(settings) as db:
        account = db.execute("SELECT next_refresh_at FROM own_channel_accounts WHERE account_id = 'yt-finance'").fetchone()
    assert account is not None
    assert account["next_refresh_at"] == run["next_retry_at"]


def test_reference_collector_adds_hook_and_thumbnail_patterns(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    monkeypatch.setattr(reference_channels, "build_youtube_data_client", lambda session, settings: _YouTube())

    async def fake_summary(settings, *, titles, thumbnails):
        return {
            "hook_pattern_summary": "Numbered wealth lessons with direct payoff.",
            "thumbnail_style_tags": ["luxury", "bold_text"],
        }

    monkeypatch.setattr(reference_channels, "_ollama_reference_summary", fake_summary)

    result = asyncio.run(
        reference_channels.collect_reference_channels(
            session=object(),
            settings=settings,
            channel_urls=["https://www.youtube.com/@alux"],
        )
    )

    assert result["status"] == "collected"
    assert result["channels"][0]["hook_pattern_summary"].startswith("Numbered")
    assert result["channels"][0]["thumbnail_style_tags"] == ["luxury", "bold_text"]


def test_reference_collector_returns_needs_config_for_missing_youtube_credentials(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)

    def missing_credentials(_session, _settings):
        raise youtube_api.YouTubeCollectorError("Connect YouTube Studio before running this action.")

    monkeypatch.setattr(reference_channels, "build_youtube_data_client", missing_credentials)

    result = asyncio.run(
        reference_channels.collect_reference_channels(
            session=object(),
            settings=settings,
            channel_urls=["https://www.youtube.com/@alux"],
        )
    )

    assert result["status"] == "needs_config"
    assert result["channels"] == []
    assert result["next_actions"]
    run = read_collection_run(settings, "reference_channels")
    assert run is not None
    assert run["status"] == "needs_config"


def test_google_trends_rate_limit_marks_rows(monkeypatch, tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)

    def fake_collect(**kwargs):
        raise RuntimeError("HTTP 429 too many requests")

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(google_trends, "_collect_google_trends_sync", fake_collect)
    monkeypatch.setattr(google_trends.asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        google_trends.collect_google_trends(
            settings=settings,
            keywords=["finanse", "oszczedzanie"],
            timeframe="now 7-d",
            geo="PL",
        )
    )

    assert result["status"] == "rate_limited"
    assert {item["status"] for item in result["items"]} == {"rate_limited"}


def test_google_trends_import_helper_does_not_auto_install_in_tests(monkeypatch) -> None:
    def fake_import(_name):
        raise ImportError("missing pytrends")

    install_called = False

    def fake_install(_package):
        nonlocal install_called
        install_called = True
        return True

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    monkeypatch.setattr(google_trends.importlib, "import_module", fake_import)
    monkeypatch.setattr(google_trends, "_install_package", fake_install)

    try:
        google_trends.import_pytrends_trend_req()
    except RuntimeError as exc:
        assert "auto-install is disabled" in str(exc)
    else:
        raise AssertionError("Expected pytrends import helper to fail without installing in tests.")

    assert install_called is False


def test_google_trends_import_helper_installs_once_when_missing(monkeypatch) -> None:
    calls = {"import": 0, "install": 0}

    class FakeTrendReq:
        pass

    class FakeModule:
        TrendReq = FakeTrendReq

    def fake_import(_name):
        calls["import"] += 1
        if calls["import"] == 1:
            raise ImportError("missing pytrends")
        return FakeModule

    def fake_install(package):
        calls["install"] += 1
        assert package.startswith("pytrends")
        return True

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ATNOS_DISABLE_AUTO_INSTALL", raising=False)
    monkeypatch.setattr(google_trends.importlib, "import_module", fake_import)
    monkeypatch.setattr(google_trends, "_install_package", fake_install)

    assert google_trends.import_pytrends_trend_req() is FakeTrendReq
    assert calls == {"import": 2, "install": 1}
