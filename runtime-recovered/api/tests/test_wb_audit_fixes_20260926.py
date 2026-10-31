"""Targeted regression tests for the 2026-09-26 audit findings."""

from __future__ import annotations

import sqlite3

import pytest

from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
from atnos.production.channels.wealth_blueprint.visual_decision import decide_visual
from atnos.production.project_content_brain.content_intelligence import (
    ContentIntelligenceStore,
    assert_provenance,
    evaluate_experiment,
    experiment_learning,
    normalize_analytics,
)


def variant(name, value):
    return {
        "id": name,
        "metrics": {
            "average_view_percentage": {
                "status": "UNKNOWN" if value is None else "observed",
                "value": value,
            }
        },
    }


def test_transfer_direction_is_not_reversed_by_checking_keyword():
    text = "Move money from savings to checking to cover this month's rent."
    decision = decide_visual(text)
    relation = decision["relation"]
    assert relation["kind"] == "transfer"
    assert relation["source"] == "savings"
    assert relation["destination"] == "checking"
    assert decision["secondary"] == "savings to checking"
    assert "checking to savings" not in decision["secondary"]


def test_forbidden_transfer_and_notebook_and_raise_split():
    blocked = parse_scene_relation("Do not transfer money from checking to savings this month.")
    assert blocked["kind"] == "transfer_forbidden"
    notebook = parse_scene_relation("Track your transfers with a budgeting app or notebook.")
    assert notebook["kind"] == "notebook_log"
    raise_split = parse_scene_relation(
        "When you receive a raise, allocate a specific portion to savings. The rest can cover current expenses.",
        concept="raise_absorption",
    )
    assert raise_split["kind"] == "raise_split"
    assert "→" in (raise_split["direction"] or "")


def test_unsupported_money_language_reports_gap():
    relation = parse_scene_relation("Something about checking money and a raise without a clear move.")
    assert relation["kind"] == "unsupported"
    assert relation["unsupported_gap"]


def test_experiment_tie_has_no_winner_and_no_higher_claim():
    base = {
        "experiment_id": "review-isolated",
        "same_concept": True,
        "single_variable": True,
        "same_window": True,
        "measurement_window": "2026-09-01/2026-09-07",
        "publication_ids": ["test-a", "test-b"],
        "variants": [variant("A", 40), variant("B", 40)],
    }
    result = evaluate_experiment(base)
    assert result["winner"] is None
    assert result["result"] == "TIE"
    assert experiment_learning(base) is None


def test_experiment_requires_every_variant_result():
    incomplete = {
        "experiment_id": "review-isolated",
        "same_concept": True,
        "single_variable": True,
        "same_window": True,
        "measurement_window": "2026-09-01/2026-09-07",
        "publication_ids": ["test-a", "test-b", "test-c"],
        "variants": [variant("A", 30), variant("B", 45), variant("C", None)],
    }
    result = evaluate_experiment(incomplete)
    assert result["winner"] is None
    assert result["status"] == "COLLECTING_DATA"


def test_project_isolation_and_idempotent_decision():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    store = ContentIntelligenceStore(db)
    insight = {
        "kind": "PERFORMANCE_INSIGHT",
        "source": "YOUTUBE_ANALYTICS",
        "status": "insufficient_data",
        "statement": "Insufficient data",
        "sample_size": 0,
    }
    first = store.save_insight("wealth_blueprint", insight)
    second = store.save_insight("kids", insight)
    assert first != second
    assert len(store.list_insights("wealth_blueprint")) == 1
    assert len(store.list_insights("kids")) == 1
    decision = {"decision": None, "reason": "No evidence"}
    store.save_decision("wealth_blueprint", decision)
    store.save_decision("kids", decision)
    again = store.save_decision("wealth_blueprint", decision)
    assert again


def test_unverified_model_evidence_ids_are_rejected():
    with pytest.raises(ValueError):
        assert_provenance(
            {
                "kind": "PERFORMANCE_INSIGHT",
                "source": "MODEL_INFERENCE",
                "statement": "Unverified test assertion",
                "evidence_video_ids": ["does-not-exist"],
            }
        )


def test_pending_analytics_is_not_live_and_ctr_is_not_annotation():
    pending = normalize_analytics(
        {"video_id": "test-pending", "status": "pending", "message": "YouTube Analytics has no data yet.", "window_days": 7},
        fetched_at="review",
    )
    assert pending["integration"] == "PENDING"
    assert pending["window_days"] == 7
    assert sum(v["status"] == "observed" for v in pending["metrics"].values()) == 0

    ready = normalize_analytics(
        {
            "video_id": "test",
            "status": "ready",
            "annotation_click_through_rate": 0.12,
            "start_date": "2026-09-19",
            "end_date": "2026-09-26",
        },
        fetched_at="review",
    )
    assert "ctr" not in ready["metrics"]
    assert ready["metrics"]["annotation_click_through_rate"]["value"] == 0.12
    assert ready["metrics"]["thumbnail_ctr"]["status"] == "UNKNOWN"
    assert ready["window_days"] == 7
