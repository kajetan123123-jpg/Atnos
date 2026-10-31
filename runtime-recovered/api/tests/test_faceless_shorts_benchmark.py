from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.faceless_shorts_benchmark import (
    DEFAULT_FACELESS_AI_QUERIES,
    FacelessShortsBenchmarkService,
    build_youtube_search_fallback_source,
    build_youtube_discovery_source,
    build_replication_brief,
    classify_faceless_pattern,
    score_candidate_rank,
    score_faceless_format,
    _entries_from_youtube_results_html,
)


def test_default_queries_include_hashtag_pages_for_high_view_shorts_discovery() -> None:
    assert any(query.startswith("hashtag:") for query in DEFAULT_FACELESS_AI_QUERIES)
    assert "hashtag:aicatstory" in DEFAULT_FACELESS_AI_QUERIES


def test_youtube_discovery_source_maps_hashtag_alias_to_playlist_url() -> None:
    source, is_playlist = build_youtube_discovery_source("hashtag:aicatstory", 8)

    assert source == "https://www.youtube.com/hashtag/aicatstory"
    assert is_playlist is True


def test_youtube_discovery_source_keeps_search_queries_as_ytsearch() -> None:
    source, is_playlist = build_youtube_discovery_source('"#aicat" "#shorts"', 8)

    assert source == 'ytsearch8:"#aicat" "#shorts"'
    assert is_playlist is False


def test_youtube_search_fallback_source_keeps_hashtag_runs_from_blocking_forever() -> None:
    source = build_youtube_search_fallback_source("hashtag:aicatstory", 8)

    assert source == "ytsearch8:#aicatstory #shorts AI"


def test_youtube_results_html_parser_extracts_public_video_metadata_fast() -> None:
    html = """
    <html><script>
    var ytInitialData = {"contents":[{"videoRenderer":{
      "videoId":"abc123",
      "title":{"runs":[{"text":"Poor cat saves robot #shorts #ai"}]},
      "ownerText":{"runs":[{"text":"Cute Cat"}]},
      "viewCountText":{"simpleText":"1.2M views"},
      "lengthText":{"simpleText":"0:47"},
      "thumbnail":{"thumbnails":[{"url":"https://i.ytimg.com/vi/abc123/hqdefault.jpg"}]}
    }}]};
    </script></html>
    """

    entries = _entries_from_youtube_results_html(html, limit=3, query="hashtag:aicat", default_duration_s=45)

    assert entries == [
        {
            "title": "Poor cat saves robot #shorts #ai",
            "channel": "Cute Cat",
            "uploader": "Cute Cat",
            "view_count": 1_200_000,
            "duration": 47,
            "id": "abc123",
            "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "url": "https://www.youtube.com/watch?v=abc123",
            "thumbnail": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
            "query": "hashtag:aicat",
        }
    ]


def test_classifies_ai_cat_story_as_animal_microstory() -> None:
    pattern = classify_faceless_pattern("Poor cat became a fashion designer #catshorts #ai #aicat")

    assert pattern == "ai_animal_microstory"


def test_scoring_prioritizes_high_view_ai_short_format() -> None:
    score = score_faceless_format(
        title="AI Cat Story #shorts viral kitten adventure",
        duration_s=49,
        query="AI cat story #shorts",
        pattern="ai_animal_microstory",
    )
    rank = score_candidate_rank(
        view_count=2_700_000,
        duration_s=49,
        format_score=score,
        pattern="ai_animal_microstory",
    )

    assert score >= 0.8
    assert rank > 0.85


def test_replication_brief_keeps_copying_forbidden() -> None:
    candidate = _entry(
        title="Poor cat became a fashion designer #catshorts #ai",
        channel="Catstory",
        view_count=2_000_000,
        duration=55,
    )
    service = FacelessShortsBenchmarkService(_settings(Path(".")), fetcher=lambda _query, _limit: [candidate])
    run_candidate = service._candidate_from_entry(candidate, query="AI cat story #shorts")
    assert run_candidate is not None

    brief = build_replication_brief(run_candidate, index=1)

    assert brief.source_pattern == "ai_animal_microstory"
    assert brief.source_video_id == "abc123"
    assert "Nie kopiuj exact script" in " ".join(brief.forbidden_copying)
    assert "Oryginalny" in brief.legal_recreation_topic
    assert brief.format_recipe
    assert "bez kopiowania" in brief.recreation_prompt


def test_benchmark_filters_long_tutorials_because_goal_is_shorts() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            _entry(
                title="How to make viral faceless AI shorts tutorial",
                channel="AI Channel",
                view_count=300_000,
                duration=620,
                video_id="tutorial-1",
            ),
            _entry(
                title="Poor cat became a fashion designer #catshorts #ai",
                channel="Catstory",
                view_count=2_700_000,
                duration=55,
                video_id="cat-1",
            ),
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)

        run = service.run(queries=("AI cat story #shorts",), per_query_limit=2, top_limit=2, brief_limit=1)

        assert run.candidate_count == 1
        assert run.top_candidates[0].video_id == "cat-1"


def test_benchmark_filters_short_tutorials_because_goal_is_content_not_tutorials() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            _entry(
                title="How to create viral faceless AI shorts in CapCut #shorts",
                channel="AI Tutorial",
                view_count=900_000,
                duration=45,
                video_id="tutorial-short-1",
            ),
            _entry(
                title="Kitten Loves Bunny #cat #ai #aicat #shorts #story",
                channel="AI Cat Story",
                view_count=120_000,
                duration=65,
                video_id="cat-story-short-1",
            ),
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)

        run = service.run(queries=('"#aicat" "#shorts"',), per_query_limit=2, top_limit=2, brief_limit=1)

        assert run.candidate_count == 1
        assert run.top_candidates[0].video_id == "cat-story-short-1"


def test_benchmark_filters_long_form_monetization_videos_even_when_query_mentions_shorts() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            _entry(
                title="how i make $36,438/mo posting YouTube Shorts (using AI)",
                channel="Kellan Henneberry",
                view_count=893_903,
                duration=1503,
                video_id="long-money-1",
            ),
            _entry(
                title="Poor cat became a fashion designer #catshorts #ai",
                channel="Catstory",
                view_count=318_224,
                duration=55,
                video_id="cat-short-1",
            ),
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)

        run = service.run(queries=("faceless AI shorts story",), per_query_limit=2, top_limit=2, brief_limit=1)

        assert run.candidate_count == 1
        assert run.top_candidates[0].video_id == "cat-short-1"


def test_benchmark_filters_unknown_duration_entries_in_strict_mode() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            {
                "title": "Live Mama Cat And Kitten #shorts #aicat",
                "channel": "Cat Live",
                "view_count": 7_000,
                "duration": None,
                "id": "live-1",
                "webpage_url": "https://www.youtube.com/watch?v=live-1",
            },
            _entry(
                title="Kitten Loves Bunny #cat #ai #aicat #shorts #story",
                channel="AI Cat Story",
                view_count=120_000,
                duration=65,
                video_id="cat-story-short-1",
            ),
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)

        run = service.run(queries=('"#aicat" "#shorts"',), per_query_limit=2, top_limit=2, brief_limit=1)

        assert run.candidate_count == 1
        assert run.top_candidates[0].video_id == "cat-story-short-1"


def test_benchmark_filters_duration_anomalies_by_default() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            _entry(
                title="Poor cat became a fashion designer #catshorts #ai #aicat #catstory",
                channel="mini meownmeow",
                view_count=740_000,
                duration=311,
                video_id="cat-ref-1",
            )
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)

        run = service.run(queries=("AI cat story #shorts",), per_query_limit=1, top_limit=1, brief_limit=1)

        assert run.candidate_count == 0


def test_benchmark_can_keep_non_tutorial_hashtag_short_reference_when_explicitly_allowed() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            _entry(
                title="Poor cat became a fashion designer #catshorts #ai #aicat #catstory",
                channel="mini meownmeow",
                view_count=740_000,
                duration=311,
                video_id="cat-ref-1",
            )
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)

        run = service.run(
            queries=("AI cat story #shorts",),
            per_query_limit=1,
            top_limit=1,
            brief_limit=1,
            allow_duration_anomalies=True,
        )

        assert run.candidate_count == 1
        assert run.top_candidates[0].source_kind == "hashtag_short_reference"
        assert run.top_candidates[0].duration_anomaly is True
        assert "duration anomalies" in run.replication_briefs[0].source_observation


def test_benchmark_run_writes_json_markdown_and_briefs() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            _entry(
                title="Poor cat became a fashion designer #catshorts #ai",
                channel="Catstory",
                view_count=2_700_000,
                duration=55,
                video_id="cat-1",
            ),
            _entry(
                title="How to make viral faceless AI shorts tutorial",
                channel="AI Channel",
                view_count=300_000,
                duration=620,
                video_id="tutorial-1",
            ),
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)

        run = service.run(queries=("AI cat story #shorts",), per_query_limit=2, top_limit=2, brief_limit=1)

        assert run.candidate_count == 1
        assert Path(run.json_path).exists()
        assert Path(run.markdown_path).exists()
        assert Path(run.briefs_path).exists()
        assert run.top_candidates[0].pattern == "ai_animal_microstory"
        assert run.channel_insights[0].channel == "Catstory"
        assert run.visual_observations[0].status == "missing_thumbnail"
        assert run.frame_observations[0].status == "missing_reference"
        assert run.visual_recreation_plans[0].brief_id == "benchmark-brief-01-ai_animal_microstory"
        assert run.visual_recreation_plans[0].character_sheet_prompt
        assert Path(run.visual_recreation_plans_path).exists()
        assert Path(run.frame_observations_path).exists()
        assert (Path(run.output_dir) / "visual_recreation_plans").exists()
        assert (Path(run.output_dir) / "frame_observations").exists()
        payload = json.loads(Path(run.json_path).read_text(encoding="utf-8"))
        assert payload["replication_briefs"][0]["source_pattern"] == "ai_animal_microstory"
        assert payload["channel_insights"][0]["channel"] == "Catstory"
        assert payload["visual_observations"][0]["status"] == "missing_thumbnail"
        assert payload["frame_observations"][0]["status"] == "missing_reference"
        assert payload["visual_recreation_plans"][0]["scene_prompts"]
        log_path = settings.data_dir / "logs" / "faceless_shorts_learning.jsonl"
        assert "faceless_ai_shorts_benchmark_recorded" in log_path.read_text(encoding="utf-8")


def test_benchmark_emits_query_level_progress_events() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root)
        entries = [
            _entry(
                title="Poor cat became a fashion designer #catshorts #ai",
                channel="Catstory",
                view_count=2_700_000,
                duration=55,
                video_id="cat-1",
            )
        ]
        service = FacelessShortsBenchmarkService(settings, fetcher=lambda _query, _limit: entries)
        events: list[dict[str, object]] = []

        run = service.run(
            queries=("hashtag:aicat", '"#aicat" "#shorts"'),
            per_query_limit=1,
            top_limit=2,
            brief_limit=1,
            progress_callback=events.append,
        )

        phases = [event["phase"] for event in events]
        assert run.candidate_count == 1
        assert phases.count("benchmark_query") == 2
        assert phases.count("benchmark_query_complete") == 2
        assert "benchmark_rank" in phases
        assert phases[-1] == "benchmark_written"
        assert events[0]["query"] == "hashtag:aicat"
        assert events[-1]["candidate_count"] == 1


def _settings(root: Path) -> Settings:
    return Settings(app_root=root, data_dir=root / "data")


def _entry(
    *,
    title: str,
    channel: str,
    view_count: int,
    duration: int,
    video_id: str = "abc123",
) -> dict[str, object]:
    return {
        "title": title,
        "channel": channel,
        "view_count": view_count,
        "duration": duration,
        "id": video_id,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
    }
