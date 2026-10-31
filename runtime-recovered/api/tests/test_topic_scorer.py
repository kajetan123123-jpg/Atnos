from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient

from atnos.intelligence.storage import connect, init_intelligence_db, json_dumps
from atnos.api.routes.topics import _search_terms as route_search_terms
from atnos.scoring.topic_scorer import (
    NicheFitJudgement,
    TopicScorer,
    _fallback_candidates_from_signals,
    _search_terms as scorer_search_terms,
)
from test_support import create_test_app


def test_topic_search_terms_skip_real_polish_channel_stop_word() -> None:
    text = "Kanał finansowy: inflacja zjada oszczędności"

    scorer_terms = scorer_search_terms(text)
    route_terms = route_search_terms(text)

    assert "inflacja" in scorer_terms
    assert "oszczędności" in scorer_terms
    assert "inflacja" in route_terms
    assert "oszczędności" in route_terms
    assert "kanał" not in scorer_terms
    assert "kanał" not in route_terms


def test_topic_search_terms_handle_polish_unicode_without_mojibake() -> None:
    text = "Kanał finansowy: inflacja zjada oszczędności"

    scorer_terms = scorer_search_terms(text)
    route_terms = route_search_terms(text)

    assert "inflacja" in scorer_terms
    assert "oszczędności" in scorer_terms
    assert "inflacja" in route_terms
    assert "oszczędności" in route_terms
    assert not any("Ĺ" in term or "Ă" in term for term in (*scorer_terms, *route_terms))


def _seed_topic_signals(settings) -> None:
    now = datetime.now(UTC)
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-money-1', 'finance-pl', 'finanse osobiste', 'PL',
                'Jak inflacja niszczy twoje oszczednosci', 1200000, 42000, 900,
                ?, 'UC1', 'Finance Channel', 'https://img/1.jpg', ?
            )
            """,
            ((now - timedelta(days=2)).isoformat(), now.isoformat()),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'inflacja oszczednosci', 'now 7-d', 'PL', 86, ?, '[]', 'ready', ?
            )
            """,
            (json_dumps(["lokaty 2026", "ceny zywnosci", "realna inflacja", "konto oszczednosciowe"]), now.isoformat()),
        )
        db.execute(
            """
            INSERT INTO competitor_videos (
                video_id, account_id, channel_id, competitor_channel_id, title, views, likes,
                comments, published_at, duration, duration_seconds, thumbnail_url, collected_at
            )
            VALUES (
                'comp-money-1', 'finance-pl', 'UCCOMP', 'UCCOMP',
                'Inflacja i oszczednosci w Polsce', 18000, 600, 22, ?,
                'PT52S', 52, 'https://img/comp.jpg', ?
            )
            """,
            ((now - timedelta(days=3)).isoformat(), now.isoformat()),
        )
        db.commit()


def test_topic_scorer_uses_intelligence_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": True, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    score = asyncio.run(
        TopicScorer(settings).score_topic(
            topic="inflacja oszczednosci",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert score.total_score >= 65
    assert score.recommended is True
    assert score.dimensions["viral_potential"] > 20
    assert score.dimensions["search_demand"] == 17.2
    assert score.dimensions["trend_velocity"] == 6.0
    assert score.flags["evergreen"] is True
    assert score.evidence["yt_signal_count"] == 1
    assert score.evidence["competitor_match_count"] == 1


def test_topic_scorer_marks_neutral_niche_fit_fallback_as_llm_unavailable(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)

    async def fake_niche_fit_fallback(self, *, topic: str, niche: str) -> NicheFitJudgement:
        return NicheFitJudgement(
            score=5.0,
            source="neutral_fallback",
            model="mistral",
            llm_unavailable=True,
        )

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": True, "shorts_potential": True, "llm_unavailable": False}

    monkeypatch.setattr(TopicScorer, "_score_niche_fit_with_ollama", fake_niche_fit_fallback)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    score = asyncio.run(
        TopicScorer(settings).score_topic(
            topic="inflacja oszczednosci",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert score.dimensions["niche_fit"] == 5.0
    assert score.flags["llm_unavailable"] is True
    assert score.evidence["llm_unavailable"] is True
    assert score.evidence["niche_fit_source"] == "neutral_fallback"
    assert score.evidence["niche_fit_model"] == "mistral"


def test_topic_scorer_matches_google_related_rising_dict_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    now = datetime.now(UTC).isoformat()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-related-1', 'finance-pl', 'finanse osobiste', 'PL',
                'Lokaty i inflacja: co robi z gotowka bank', 850000, 21000, 500,
                ?, 'UC1', 'Finance Channel', 'https://img/1.jpg', ?
            )
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'inflacja oszczednosci', 'now 7-d', 'PL', 81, ?, '[]', 'ready', ?
            )
            """,
            (
                json_dumps([{"query": "lokaty 2026", "value": 450}, {"query": "realna inflacja", "value": 250}]),
                now,
            ),
        )
        db.commit()

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": False, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    score = asyncio.run(
        TopicScorer(settings).score_topic(
            topic="lokaty 2026",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert score.evidence["evidence_backed"] is True
    assert score.evidence["topic_google_signal_count"] == 1
    assert score.dimensions["search_demand"] > 0
    assert score.flags["shorts_potential"] is True


def test_fallback_candidates_extract_related_rising_query_text_from_dicts() -> None:
    candidates = _fallback_candidates_from_signals(
        count=3,
        yt_signals=[],
        gtrends=[
            {
                "keyword": "inflacja oszczednosci",
                "interest_score": 80,
                "related_rising": [{"query": "lokaty 2026", "value": 450}],
            }
        ],
    )

    titles = [item["title"] for item in candidates]
    assert "lokaty 2026" in titles
    assert all("{" not in title and "query" not in title for title in titles)


def test_topic_scorer_does_not_infer_competition_gap_without_competitor_data(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)
    with connect(settings) as db:
        db.execute("DELETE FROM competitor_videos")
        db.commit()

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": True, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    score = asyncio.run(
        TopicScorer(settings).score_topic(
            topic="inflacja oszczednosci",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert score.dimensions["competition_gap"] == 0.0
    assert score.evidence["competitor_match_count"] == 0
    assert score.evidence["evidence_backed"] is True


def test_topic_scorer_applies_post_publish_performance_feedback(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)
    now = datetime.now(UTC).isoformat()

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": True, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    scorer = TopicScorer(settings)
    baseline = asyncio.run(
        scorer.score_topic(
            topic="inflacja oszczednosci",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score,
                performance_score, last_performance_recommendation,
                last_youtube_video_id, analytics_updated_at, created_at, updated_at
            )
            VALUES (
                'perf-strong', 'finance-pl', 'inflacja oszczednosci',
                'Mocny opublikowany format o inflacji i oszczednosciach.', 'done', 82,
                88, 'Powtorz podobny hook i tempo.', 'yt-strong', ?, ?, ?
            )
            """,
            (now, now, now),
        )
        db.commit()

    boosted = asyncio.run(
        scorer.score_topic(
            topic="inflacja oszczednosci",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert boosted.total_score > baseline.total_score
    assert boosted.evidence["performance_feedback_count"] == 1
    assert boosted.evidence["performance_adjustment"] == 6.0
    assert boosted.evidence["performance_examples"][0]["youtube_video_id"] == "yt-strong"

    with connect(settings) as db:
        db.execute("UPDATE topic_queue SET performance_score = 22, last_performance_recommendation = 'Nie kopiuj formatu.' WHERE id = 'perf-strong'")
        db.commit()

    penalized = asyncio.run(
        scorer.score_topic(
            topic="inflacja oszczednosci",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert penalized.total_score < baseline.total_score
    assert penalized.evidence["performance_adjustment"] == -8.0


def test_topic_scorer_prefers_feedback_adjusted_score_over_raw_performance(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)
    now = datetime.now(UTC).isoformat()

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": True, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    scorer = TopicScorer(settings)
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score,
                performance_score, feedback_adjusted_score, last_performance_recommendation,
                last_youtube_video_id, analytics_updated_at, created_at, updated_at
            )
            VALUES (
                'perf-adjusted', 'finance-pl', 'inflacja oszczednosci',
                'Surowy performance byl slaby, ale adjusted score po petli feedbacku jest mocny.',
                'done', 80, 22, 82, 'Adjusted score wspiera podobny kierunek.',
                'yt-adjusted', ?, ?, ?
            )
            """,
            (now, now, now),
        )
        db.commit()

    score = asyncio.run(
        scorer.score_topic(
            topic="inflacja oszczednosci",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert score.evidence["performance_adjustment"] == 6.0
    assert score.evidence["performance_examples"][0]["performance_score"] == 22.0
    assert score.evidence["performance_examples"][0]["feedback_adjusted_score"] == 82.0


def test_topic_score_api_returns_sorted_scores(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0 if "inflacja" in topic else 0.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {
            "clickbait_risk": "losowy" in topic,
            "evergreen": True,
            "shorts_potential": "inflacja" in topic,
        }

    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    client = TestClient(app)
    response = client.post(
        "/api/topics/score",
        json={
            "account_id": "finance-pl",
            "topics": [
                {"id": "t-random", "title": "losowy temat", "description": "Niepowiazany temat"},
                {
                    "id": "t-inflation",
                    "title": "inflacja oszczednosci",
                    "description": "Dlaczego inflacja zjada oszczednosci i co widz powinien zrozumiec.",
                },
            ],
            "geo": "PL",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == "t-inflation"
    assert payload[0]["topic"] == "inflacja oszczednosci"
    assert payload[0]["dimensions"]["niche_fit"] == 10.0
    assert payload[0]["flags"]["shorts_potential"] is True
    assert payload[0]["evidence"]["max_google_interest"] == 86


def test_topic_generate_derives_candidates_from_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)

    async def fake_generate(self, *, account_id, count, niches, yt_signals, gtrends, **_kwargs):
        return [
            {
                "id": "gen-1",
                "title": "inflacja oszczednosci",
                "description": "Temat wyprowadzony z danych YouTube i Google Trends.",
            }
        ]

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": False, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_generate_candidates_with_ollama", fake_generate)
    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    response = TestClient(app).post("/api/topics/generate", json={"account_id": "finance-pl", "count": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == "gen-1"
    assert payload[0]["status"] == "pending"
    assert payload[0]["flags"]["shorts_potential"] is True
    assert payload[0]["total_score"] >= 65

    queue = TestClient(app).get("/api/topics/queue?account_id=finance-pl&status=pending").json()
    assert queue[0]["id"] == "gen-1"


def test_topic_generate_filters_llm_topics_without_signal_evidence(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)

    async def fake_generate(self, *, account_id, count, niches, yt_signals, gtrends, **_kwargs):
        return [
            {
                "id": "hallucinated-1",
                "title": "sekret milionera z kosmosu",
                "description": "Brak zwiazku z zebranymi sygnalami.",
            }
        ]

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": True, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_generate_candidates_with_ollama", fake_generate)
    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    response = TestClient(app).post("/api/topics/generate", json={"account_id": "finance-pl", "count": 5})

    assert response.status_code == 200
    payload = response.json()
    titles = {item["title"] for item in payload}
    assert "sekret milionera z kosmosu" not in titles
    assert payload
    assert all(item["inspired_by"] != "intelligence_signals" for item in payload)


def test_topic_generate_returns_diagnostic_conflict_when_no_intelligence_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))

    async def fake_generate(self, *, account_id, count, niches, yt_signals, gtrends, **_kwargs):
        raise AssertionError("Topic generation must not call LLM when no real signals exist.")

    monkeypatch.setattr(TopicScorer, "_generate_candidates_with_ollama", fake_generate)

    client = TestClient(app)
    response = client.post("/api/topics/generate", json={"account_id": "finance-pl", "count": 5})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["ready"] is False
    assert detail["counts"] == {"yt_signals": 0, "gtrends_signals": 0}
    assert "yt_signals_empty" in detail["blockers"]
    assert "gtrends_signals_empty" in detail["blockers"]
    assert detail["next_actions"]
    queue = client.get("/api/topics/queue?account_id=finance-pl&status=all").json()
    assert queue == []


def test_topic_generate_blocks_partial_research_without_youtube_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'finanse osobiste', 'now 7-d', 'PL', 72, '["budzet domowy"]', '[]', 'ready',
                '2026-06-02T07:00:00+00:00'
            )
            """
        )
        db.commit()

    async def fake_generate(self, *, account_id, count, niches, yt_signals, gtrends, **_kwargs):
        raise AssertionError("Topic generation must not call LLM without YouTube signals.")

    monkeypatch.setattr(TopicScorer, "_generate_candidates_with_ollama", fake_generate)

    response = TestClient(app).post("/api/topics/generate", json={"account_id": "finance-pl", "count": 5})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["counts"] == {"yt_signals": 0, "gtrends_signals": 1}
    assert detail["blockers"] == ["yt_signals_empty"]


def test_topic_generate_blocks_google_trends_from_unrelated_niche(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)
    with connect(settings) as db:
        db.execute("DELETE FROM gtrends_signals")
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'makijaz wieczorowy', 'now 7-d', 'PL', 91, '["kosmetyki viral"]', '[]', 'ready',
                '2026-06-02T07:00:00+00:00'
            )
            """
        )
        db.commit()

    async def forbidden_generate(self, *, account_id, count, niches, yt_signals, gtrends, **_kwargs):
        raise AssertionError("Topic generation must not call LLM with unrelated Google Trends signals.")

    monkeypatch.setattr(TopicScorer, "_generate_candidates_with_ollama", forbidden_generate)

    response = TestClient(app).post("/api/topics/generate", json={"account_id": "finance-pl", "count": 5})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["counts"] == {"yt_signals": 1, "gtrends_signals": 0}
    assert detail["blockers"] == ["gtrends_signals_empty"]


def test_topic_scorer_generate_topics_requires_youtube_and_google_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'finanse osobiste', 'now 7-d', 'PL', 72, '["budzet domowy"]', '[]', 'ready',
                '2026-06-02T07:00:00+00:00'
            )
            """
        )
        db.commit()

    async def forbidden_generate(self, *, account_id, count, niches, yt_signals, gtrends, **_kwargs):
        raise AssertionError("Direct scorer generation must not call LLM without YouTube and Google signals.")

    monkeypatch.setattr(TopicScorer, "_generate_candidates_with_ollama", forbidden_generate)

    gtrends_only = asyncio.run(TopicScorer(settings).generate_topics(account_id="finance-pl", count=5))

    assert gtrends_only == []

    with connect(settings) as db:
        db.execute("DELETE FROM gtrends_signals")
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-only-1', 'finance-pl', 'finanse osobiste', 'PL', 'Inflacja i oszczednosci',
                100000, 4000, 120, '2026-06-01T10:00:00Z', 'UC1', 'Finance', 'https://img/1.jpg',
                '2026-06-02T07:00:00+00:00'
            )
            """
        )
        db.commit()

    youtube_only = asyncio.run(TopicScorer(settings).generate_topics(account_id="finance-pl", count=5))

    assert youtube_only == []


def test_topic_scorer_generate_topics_filters_unrelated_google_trends(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic_signals(settings)
    with connect(settings) as db:
        db.execute("DELETE FROM gtrends_signals")
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'makijaz wieczorowy', 'now 7-d', 'PL', 91, '["kosmetyki viral"]', '[]', 'ready',
                '2026-06-02T07:00:00+00:00'
            )
            """
        )
        db.commit()

    async def forbidden_generate(self, *, account_id, count, niches, yt_signals, gtrends, **_kwargs):
        raise AssertionError("Direct scorer generation must not call LLM with unrelated Google Trends.")

    monkeypatch.setattr(TopicScorer, "_generate_candidates_with_ollama", forbidden_generate)

    topics = asyncio.run(TopicScorer(settings).generate_topics(account_id="finance-pl", count=5))

    assert topics == []


def test_topic_scorer_does_not_recommend_topic_without_real_signals(monkeypatch, tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))

    async def fake_niche_fit(self, *, topic: str, niche: str) -> float:
        return 10.0

    async def fake_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        return {"clickbait_risk": False, "evergreen": True, "shorts_potential": True}

    monkeypatch.setattr(TopicScorer, "_score_niche_fit", fake_niche_fit)
    monkeypatch.setattr(TopicScorer, "_judge_topic_flags", fake_flags)

    score = asyncio.run(
        TopicScorer(settings).score_topic(
            topic="sekret milionera z kosmosu",
            niche="finanse osobiste",
            account_id="finance-pl",
            geo="PL",
        )
    )

    assert score.evidence["evidence_backed"] is False
    assert score.recommended is False


def test_topic_queue_status_update_and_delete(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))

    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score,
                viral_potential, search_demand, competition_gap, trend_velocity,
                niche_fit, freshness, clickbait_risk, evergreen, shorts_potential,
                created_at, updated_at
            )
            VALUES (
                'topic-1', 'finance-pl', 'Test topic', 'Desc', 'pending', 72,
                20, 15, 12, 10, 8, 7, 0, 1, 1,
                '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            )
            """
        )
        db.commit()

    client = TestClient(app)
    updated = client.patch("/api/topics/topic-1", json={"status": "approved"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "approved"

    approved = client.get("/api/topics/queue?account_id=finance-pl&status=approved").json()
    assert approved[0]["id"] == "topic-1"

    deleted = client.delete("/api/topics/topic-1")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
