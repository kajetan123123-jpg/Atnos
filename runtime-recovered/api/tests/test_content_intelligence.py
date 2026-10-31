"""Contract tests for the content loop. They do not call YouTube."""
from __future__ import annotations

import sqlite3

import pytest

from atnos.production.project_content_brain.content_intelligence import (
    ContentIntelligenceStore,
    classify_comparison,
    classify_oauth_record,
    cluster_comments,
    competitor_insights,
    content_decision,
    assert_youtube_read,
    evaluate_experiment,
    experiment_learning,
    normalize_analytics,
    performance_insights,
    present_youtube_integration,
    retrieve_for_task,
)


def memory_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    return db


def test_missing_metric_is_unknown_not_zero():
    snapshot = normalize_analytics(
        {"video_id": "abc", "status": "ready_degraded", "views": 10, "likes": None, "comments": 0,
         "metrics_available": ["views", "likes", "comments"], "average_view_percentage": None,
         "lifetime_totals": True},
        fetched_at="2026-09-25T00:00:00+00:00",
    )
    assert snapshot["metrics"]["lifetime_views"]["status"] == "observed"
    assert snapshot["metrics"]["lifetime_views"]["value"] == 10
    assert snapshot["metrics"]["views"]["status"] == "UNKNOWN"
    assert snapshot["metrics"]["average_view_percentage"]["status"] == "UNKNOWN"
    assert snapshot["metrics"]["average_view_percentage"]["value"] is None
    assert snapshot["metrics"]["impressions"]["status"] == "UNKNOWN"
    assert snapshot["integration"] == "DEGRADED"
    assert snapshot["window_days"] is None


def test_disconnected_youtube_is_requires_setup():
    snapshot = normalize_analytics(
        {"video_id": "abc", "status": "unavailable", "message": "Connect YouTube Studio before running this action.", "integration": "REQUIRES_SETUP"},
        fetched_at="2026-09-25T00:00:00+00:00",
    )
    assert snapshot["integration"] == "REQUIRES_SETUP"
    assert all(slot["status"] == "UNKNOWN" for slot in snapshot["metrics"].values())


def test_comments_cluster_and_do_not_duplicate():
    db = memory_db()
    store = ContentIntelligenceStore(db)
    comments = [
        {"comment_id": "c1", "video_id": "v1", "text": "Can you make part 2 about Friday transfers?"},
        {"comment_id": "c2", "video_id": "v1", "text": "Please do a part 2"},
        {"comment_id": "c3", "video_id": "v2", "text": "How does the Friday transfer work?"},
    ]
    store.save_comments("wealth_blueprint", comments)
    store.save_comments("wealth_blueprint", comments)
    assert store.comment_count("wealth_blueprint") == 3
    signals = cluster_comments(store.list_comments("wealth_blueprint"))
    sequel = next(item for item in signals if "kolejną część" in item["theme"])
    assert sequel["frequency"] == 2
    assert sequel["confidence"] == "LOW"
    assert sequel["source"] == "COMMENT_SIGNAL"


def test_one_film_does_not_create_a_strong_insight():
    insights = performance_insights([
        {"video_id": "v1", "metrics": {"average_view_percentage": {"value": 80, "status": "observed"}}},
    ])
    assert insights[0]["status"] == "insufficient_data"
    assert insights[0]["confidence"] == "LOW"
    assert insights[0]["sample_size"] == 1


def test_model_inference_without_evidence_is_rejected():
    db = memory_db()
    store = ContentIntelligenceStore(db)
    with pytest.raises(ValueError):
        store.save_insight("wealth_blueprint", {
            "source": "MODEL_INFERENCE",
            "statement": "This hook always wins.",
            "kind": "PERFORMANCE_INSIGHT",
        })


def test_decision_uses_comment_evidence_and_skips_duplicates():
    decision = content_decision(
        candidates=[
            {"topic": "Friday automation", "confidence": "MEDIUM", "source": "COMMENT_SIGNAL", "comment_ids": ["c1", "c2"], "reason": "Dwa komentarze proszą o część 2."},
            {"topic": "Friday automation", "confidence": "HIGH", "source": "MODEL_INFERENCE", "reason": "model said so"},
        ],
        duplicate_topics=["Friday automation"],
    )
    assert decision["decision"] is None
    assert decision["confidence"] == "LOW"


def test_persistence_survives_a_new_connection():
    db = memory_db()
    store = ContentIntelligenceStore(db)
    store.save_metrics("wealth_blueprint", "job-1", normalize_analytics(
        {"video_id": "vid", "status": "ready", "views": 3, "average_view_percentage": 40.0},
        fetched_at="2026-09-25T00:00:00+00:00",
    ))
    again = ContentIntelligenceStore(db)
    rows = again.list_metrics("wealth_blueprint")
    assert rows[0]["metrics"]["views"]["value"] == 3
    assert rows[0]["source"] == "YOUTUBE_ANALYTICS"


def test_competitor_titles_mark_saturation_without_copying():
    videos = [{"title": "Payday routine tips"}, {"title": "My payday routine"}, {"title": "Payday routine again"}]
    insights = competitor_insights(videos, own_topics=["emergency fund"])
    payday = next(item for item in insights if item.get("topic") == "payday")
    assert payday["kind"] == "SATURATION_WARNING"
    assert payday["already_covered_by_us"] is False


def test_expired_oauth_is_requires_setup_not_live():
    view = present_youtube_integration("TOKEN_REVOKED")
    assert view["status"] == "REQUIRES_SETUP"
    assert view["analytics"] == "unavailable"
    assert "Reconnect" in view["detail"]
    assert classify_oauth_record({"configured": True, "status": "error", "refresh_failure_code": "invalid_grant", "reconnect_required": True}) == "TOKEN_REVOKED"
    assert classify_oauth_record({"configured": True, "status": "pending"}) == "AWAITING_USER_AUTHORIZATION"
    assert classify_oauth_record({"configured": False}) == "NOT_CONFIGURED"
    assert classify_oauth_record({"configured": True, "refresh_available": True, "access_expired": True}) == "TOKEN_EXPIRED_REFRESHABLE"


def test_observational_pair_is_not_called_an_ab_test():
    kind = classify_comparison(same_concept=False, single_variable=False, same_window=False)
    result = evaluate_experiment({"kind": kind, "variants": [], "primary_metric": "average_view_percentage"})
    assert result["kind"] == "OBSERVATIONAL_COMPARISON"
    assert result["result"] == "INCONCLUSIVE"
    assert result["winner"] is None


def test_youtube_read_is_blocked_until_connected():
    for state in ("REQUIRES_SETUP", "STARTING_AUTH", "AWAITING_USER_AUTHORIZATION", "VALIDATING", "ERROR"):
        with pytest.raises(PermissionError, match="not connected"):
            assert_youtube_read(state)
    assert_youtube_read("CONNECTED", capability="can_read_videos", capabilities={"can_read_videos": True}) is None
    with pytest.raises(PermissionError, match="can_read_analytics"):
        assert_youtube_read("CONNECTED", capability="can_read_analytics", capabilities={"can_read_analytics": False})


def test_unknown_metric_and_one_variant_have_no_winner():
    unknown = evaluate_experiment({
        "same_concept": True, "single_variable": True, "same_window": True,
        "measurement_window": "7d", "publication_ids": ["a", "b"], "experiment_id": "e1",
        "variants": [
            {"id": "A", "metrics": {"average_view_percentage": {"status": "UNKNOWN", "value": None}}},
            {"id": "B", "metrics": {"average_view_percentage": {"status": "UNKNOWN", "value": None}}},
        ],
    })
    one = evaluate_experiment({
        "same_concept": True, "single_variable": True, "same_window": True,
        "measurement_window": "7d", "publication_ids": ["a", "b"],
        "variants": [
            {"id": "A", "metrics": {"average_view_percentage": {"status": "observed", "value": 40}}},
            {"id": "B", "metrics": {"average_view_percentage": {"status": "UNKNOWN", "value": None}}},
        ],
    })
    assert unknown["winner"] is None and one["winner"] is None
    assert experiment_learning({"variants": [], "youtube": "REQUIRES_SETUP"}) is None


def test_two_observed_metrics_can_name_a_winner_only_with_a_real_window():
    result = evaluate_experiment({
        "experiment_id": "exp-1",
        "same_concept": True, "single_variable": True, "same_window": True,
        "measurement_window": "2026-09-01/2026-09-07",
        "publication_ids": ["vid-a", "vid-b"],
        "variants": [
            {"id": "A", "metrics": {"average_view_percentage": {"status": "observed", "value": 30}}},
            {"id": "B", "metrics": {"average_view_percentage": {"status": "observed", "value": 45}}},
        ],
    })
    assert result["winner"] == "B"
    learning = experiment_learning({
        "experiment_id": "exp-1",
        "same_concept": True, "single_variable": True, "same_window": True,
        "measurement_window": "2026-09-01/2026-09-07",
        "publication_ids": ["vid-a", "vid-b"],
        "variants": result["variants"],
    })
    assert learning is not None
    assert learning["source"] == "EXPERIMENT"
    assert "vid-b" in learning["evidence_video_ids"]


def test_controlled_experiment_without_metrics_does_not_name_a_winner():
    result = evaluate_experiment({
        "same_concept": True,
        "single_variable": True,
        "same_window": True,
        "primary_metric": "average_view_percentage",
        "variants": [
            {"id": "A", "metrics": {"average_view_percentage": {"status": "UNKNOWN", "value": None}}},
            {"id": "B", "metrics": {"average_view_percentage": {"status": "UNKNOWN", "value": None}}},
        ],
    })
    assert result["kind"] == "CONTROLLED_EXPERIMENT"
    assert result["status"] == "COLLECTING_DATA"
    assert result["winner"] is None


def test_failed_sync_does_not_set_last_success():
    db = memory_db()
    store = ContentIntelligenceStore(db)
    first = store.record_sync("wealth_blueprint", status="REQUIRES_SETUP", detail="expired", success=False)
    assert first["last_success"] is None
    second = store.record_sync("wealth_blueprint", status="REQUIRES_SETUP", detail="expired again", success=False)
    assert second["last_success"] is None
    assert second["last_attempt"] >= first["last_attempt"]


def test_missing_channel_can_start_reconnect(tmp_path):
    from atnos.core.config import Settings
    from atnos.intelligence.storage import ensure_youtube_content_account, read_intelligence_account_config

    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    created = ensure_youtube_content_account(settings, account_id="youtube:channel-test")
    assert created["account_id"] == "youtube:channel-test"
    again = read_intelligence_account_config(settings, account_id="youtube:channel-test")
    assert again is not None
    assert again["account_id"] == "youtube:channel-test"


def test_retrieval_does_not_dump_unrelated_memory():
    insights = [
        {"theme": "friday transfer", "statement": "viewers ask about automation"},
        {"theme": "horror lighting", "statement": "unrelated"},
    ]
    picked = retrieve_for_task(insights, topic="automatic friday savings")
    assert picked[0]["theme"] == "friday transfer"
    assert all("horror" not in item["theme"] for item in picked)
