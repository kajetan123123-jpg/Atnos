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

import atnos.research.research_aggregator as aggregator
from atnos.agents.topic_hook import RawTopic, TopicHookAgent, TopicRequest
from atnos.research.cache import get_cached, set_cached
from test_support import build_test_settings, make_temp_dir


def test_research_cache_round_trip(tmp_path: Path) -> None:
    cache_path = tmp_path / "research_cache.db"

    async def _run() -> None:
        await set_cached("topic:test", {"value": "finanse"}, ttl_hours=1, db_path=cache_path)
        cached = await get_cached("topic:test", db_path=cache_path)
        assert cached == {"value": "finanse"}

    asyncio.run(_run())


def test_find_best_topics_uses_research_sources_and_filters_history(monkeypatch) -> None:
    async def fake_trends(**kwargs):
        return [{"topic": "Budzet domowy 2026", "rank": 1}]

    async def fake_search(query, **kwargs):
        if kwargs.get("search_type") == "news":
            return [
                {
                    "title": "Dlaczego Polacy traca pieniadze przez drobne oplaty",
                    "snippet": "Nowe dane o oplatach i budzetach domowych.",
                }
            ]
        return [
            {
                "title": "Najprostsza zasada oszczedzania dla poczatkujacych",
                "snippet": "Temat dobrze nadaje sie na short finansowy.",
            }
        ]

    async def fake_score(topic, **kwargs):
        return {
            "Dlaczego Polacy traca pieniadze przez drobne oplaty": 0.91,
            "Najprostsza zasada oszczedzania dla poczatkujacych": 0.72,
        }.get(topic, 0.2)

    monkeypatch.setattr(aggregator, "get_trending_topics", fake_trends)
    monkeypatch.setattr(aggregator, "web_search", fake_search)
    monkeypatch.setattr(aggregator, "score_topic_by_trends", fake_score)

    topics = asyncio.run(
        aggregator.find_best_topics(
            "finanse osobiste Polska",
            topic_history=["Budzet domowy 2026"],
            count=2,
        )
    )

    assert [topic["source"] for topic in topics] == ["news", "search"]
    assert topics[0]["trends_score"] == 0.91
    assert all(topic["topic"] != "Budzet domowy 2026" for topic in topics)


def test_research_topic_builds_script_context(monkeypatch, tmp_path: Path) -> None:
    async def fake_facts(topic, **kwargs):
        return ["Inflacja zmniejsza realna wartosc oszczednosci."]

    async def fake_news(topic, **kwargs):
        return [{"title": "Nowe dane o inflacji", "snippet": "Kontekst makroekonomiczny."}]

    async def fake_related(topic, **kwargs):
        return {"rising": [{"query": "inflacja oszczednosci"}], "top": []}

    async def fake_wiki_search(query, **kwargs):
        return [{"title": "Inflacja"}]

    async def fake_summary(title, **kwargs):
        return {"extract": "Inflacja to wzrost ogolnego poziomu cen w gospodarce."}

    monkeypatch.setenv("ATNOS_RESEARCH_CACHE_DB", str(tmp_path / "research_cache.db"))
    monkeypatch.setattr(aggregator, "get_facts_for_topic", fake_facts)
    monkeypatch.setattr(aggregator, "get_trending_news", fake_news)
    monkeypatch.setattr(aggregator, "get_related_queries", fake_related)
    monkeypatch.setattr(aggregator, "search_wikipedia", fake_wiki_search)
    monkeypatch.setattr(aggregator, "get_article_summary", fake_summary)

    result = asyncio.run(aggregator.research_topic("inflacja", language="pl"))

    assert result["cached"] is False
    assert result["key_facts"] == ["Inflacja zmniejsza realna wartosc oszczednosci."]
    assert result["related_queries"] == ["inflacja oszczednosci"]
    assert result["wikipedia_summary"].startswith("Inflacja to")


def test_topic_hook_agent_can_use_research_module(monkeypatch) -> None:
    temp_dir = make_temp_dir()
    try:
        settings = build_test_settings(Path(temp_dir.name))
        agent = TopicHookAgent(settings)

        def fake_find_best_topics_sync(*args, **kwargs):
            return [
                {
                    "topic": "3 oplaty bankowe, ktore zjadaja budzet",
                    "source": "news",
                    "snippet": "Konkretna propozycja dla shorta finansowego.",
                    "trends_score": 0.88,
                }
            ]

        monkeypatch.setenv("ATNOS_ENABLE_RESEARCH_IN_TESTS", "1")
        monkeypatch.setattr(aggregator, "find_best_topics_sync", fake_find_best_topics_sync)

        raw_topics = agent._research_module_topics(
            TopicRequest(channel_id="finanse-pl", niche="finanse osobiste Polska"),
            target_count=5,
        )

        assert raw_topics == [
            RawTopic(
                title="3 oplaty bankowe, ktore zjadaja budzet",
                source="research-news",
                evidence=["Konkretna propozycja dla shorta finansowego.", "trends_score=0.88"],
            )
        ]
    finally:
        temp_dir.cleanup()
