"""Testy Project Content Brain: historia, duplikacja, status publikacji, fakty, UNKNOWN metryki."""
from __future__ import annotations

from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production.order_dispatch import order_status
from atnos.production.order_feedback import ProductionFeedback, ProductionFeedbackStore
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_content_brain import (
    CampaignPiece,
    CampaignRecord,
    CommentClusterRecord,
    ExperimentRecord,
    FormatFatigueRecord,
    HostCharacterProfile,
    ProjectContentBrainStore,
    PublishResultRecord,
    SeriesRecord,
    TrendDecisionRecord,
    classify_script_claims,
    evaluate_history,
    pick_next_plan,
    plan_from_order,
    resolve_publication_status,
    warn_near_duplicate_plans,
)
from atnos.production.project_profile import ProductionOrder
from test_production_order_store import store  # noqa: F401 — shared fixture


def test_history_decision_marks_near_duplicate_and_stores_on_order(store):
    first = store.create(ProductionOrder(topic="Build an emergency fund this month", series_key="cash-buffer"))
    assert first["profile"]["history_decision"]["decision"] == "new"
    assert first["profile"]["publication_status"]["publication_status"] in {"IN_PRODUCTION", "BLOCKED"}
    with connect(store.settings) as db:
        brain = ProjectContentBrainStore(db)
        decision = brain.get_history_decision(first["id"])
        assert decision["decision"] == "new"
        series = brain.get_series("wealth_blueprint", "cash-buffer")
        assert series is not None
        assert "Build an emergency fund this month" in series["prior_topics"]
        plans = brain.list_plans("wealth_blueprint", stages=["in_production"])
        assert any(p.get("job_id") == first["id"] for p in plans)

    second = store.create(ProductionOrder(topic="Build an emergency fund this month", series_key="cash-buffer"))
    hist = second["profile"]["history_decision"]
    assert hist["decision"] in {"already_used", "needs_rephrase", "similar_angle"}
    assert hist["duplication_warning"] is True
    assert hist["confidence"] > 0.4
    assert hist["reason"]
    status = order_status(store.get(second["id"]))
    assert status["history_decision"]["decision"] == hist["decision"]
    assert status["publication_status"] == second["profile"]["publication_status"]["publication_status"]


def test_local_only_ready_does_not_imply_publish_consent(store):
    job = store.create(ProductionOrder(topic="Local export only topic", delivery_mode="local_only"))
    pub = job["profile"]["publication_status"]
    assert pub["local_only"] is True
    assert pub["publication_status"] == "BLOCKED"
    assert pub["ready_production_implies_publish"] is False
    mapped = resolve_publication_status(production_status="ready", delivery_mode="local_only")
    assert mapped["publication_status"] == "BLOCKED"
    ready = resolve_publication_status(production_status="ready", delivery_mode="project_policy")
    assert ready["publication_status"] == "NEEDS_REVIEW"
    assert ready["ready_production_implies_publish"] is False


def test_content_plan_warns_on_near_duplicate_active_ideas(store):
    with connect(store.settings) as db:
        brain = ProjectContentBrainStore(db)
        history = evaluate_history(brain, project="wealth_blueprint", topic="Payday savings automation")
        plan = plan_from_order(
            project="wealth_blueprint",
            topic="Payday savings automation",
            series_key=None,
            job_id="prod-testplan01",
            channel="Wealth Blueprint",
            history=history,
        )
        brain.upsert_plan(plan)
        warning = warn_near_duplicate_plans(brain, project="wealth_blueprint", idea="Payday savings automation plan")
        assert warning["duplication_warning"] is True
        assert warning["matches"]


def test_picker_stores_reason_and_prefers_non_duplicate(store):
    with connect(store.settings) as db:
        brain = ProjectContentBrainStore(db)
        now = utc_now_iso()
        from atnos.production.project_content_brain.types import ContentPlanRecord

        brain.upsert_plan(
            ContentPlanRecord(
                plan_id="plan-dup",
                idea="Same tired compounding hook again",
                priority=90,
                project="wealth_blueprint",
                stage="planned",
                channel="Wealth Blueprint",
                duplication_warning=True,
                duplication_reason="similar",
                publication_status="PLANNED",
                created_at=now,
                updated_at=now,
            )
        )
        brain.upsert_plan(
            ContentPlanRecord(
                plan_id="plan-clean",
                idea="Fresh paycheck buffer checklist",
                priority=40,
                project="wealth_blueprint",
                stage="idea",
                channel="Wealth Blueprint",
                duplication_warning=False,
                publication_status="IDEA",
                created_at=now,
                updated_at=now,
            )
        )
        picked = pick_next_plan(brain, project="wealth_blueprint")
        assert picked["status"] == "picked"
        assert picked["plan"]["plan_id"] == "plan-clean"
        assert "duplikacji" in picked["reason"].casefold() or "duplication" in picked["reason"].casefold() or "bez ostrzeżenia" in picked["reason"]
        assert picked["confidence"] >= 0.7


def test_fact_classification_uses_existing_issues_never_invents_source():
    script = {
        "title": "Save first",
        "description": "A practical buffer",
        "segments": [
            {"id": "s01", "type": "hook", "text": "Most people skip a cash buffer.", "on_screen": "Buffer", "visual_intent": "problem shot", "claim_ids": [], "fact_ids": []},
            {"id": "s02", "type": "practical", "text": "Save two hundred dollars this month.", "on_screen": "$200", "visual_intent": "action shot", "claim_ids": [], "fact_ids": []},
            {"id": "s03", "type": "cta", "text": "Start tonight.", "on_screen": "Start", "visual_intent": "cta shot here", "claim_ids": [], "fact_ids": []},
        ],
        "claims": [],
    }
    issues = [
        {"code": "unverified_number", "segment_ids": ["s02"], "detail": "needs claim"},
        {"code": "unsupported_promise", "segment_ids": ["s03"], "detail": "bad"},
    ]
    rows = classify_script_claims(script, issues, trusted_facts={})
    by_id = {r["segment_id"]: r for r in rows}
    assert by_id["s02"]["classification"] == "needs_source"
    assert by_id["s02"]["invented_source"] is False
    assert by_id["s03"]["classification"] == "risky_promise"
    assert by_id["s01"]["classification"] == "opinion"


def test_confirmed_claim_classification_with_numeric_claim():
    script = {
        "title": "Two hundred",
        "description": "Example sum",
        "segments": [
            {"id": "s01", "type": "hook", "text": "Here is a simple sum.", "on_screen": "Sum", "visual_intent": "hook visual", "claim_ids": [], "fact_ids": []},
            {"id": "s02", "type": "practical", "text": "two hundred dollars total.", "on_screen": "$200", "visual_intent": "number visual", "claim_ids": ["c1"], "fact_ids": []},
            {"id": "s03", "type": "cta", "text": "Track it tonight.", "on_screen": "Track", "visual_intent": "cta visual", "claim_ids": [], "fact_ids": []},
        ],
        "claims": [
            {
                "id": "c1",
                "operation": "sum",
                "operands": [100.0, 100.0],
                "result": 200.0,
                "unit": "dollars",
                "spoken_result": "two hundred",
                "assumptions": "illustrative only",
            }
        ],
    }
    rows = classify_script_claims(script, [], trusted_facts={})
    assert next(r for r in rows if r["segment_id"] == "s02")["classification"] == "confirmed"


def test_metrics_unknown_paths_do_not_invent_winners_or_fatigue(store):
    with connect(store.settings) as db:
        brain = ProjectContentBrainStore(db)
        now = utc_now_iso()
        exp = brain.save_experiment(
            ExperimentRecord(
                experiment_id="exp1",
                project="wealth_blueprint",
                hypothesis="Shorter hooks lift retention",
                variable="hook_length",
                variants=["short", "long"],
                metrics_status="UNKNOWN",
                status="running",
                created_at=now,
                updated_at=now,
            )
        )
        assert exp["winner"] is None
        assert exp["metrics_status"] == "UNKNOWN"
        pub = brain.save_publish_result(
            PublishResultRecord(job_id="prod-x", project="wealth_blueprint", metrics_status="UNKNOWN", observed_at=now)
        )
        assert pub["ctr"] is None and pub["retention"] is None
        fatigue = brain.save_format_fatigue(
            FormatFatigueRecord(
                project="wealth_blueprint",
                format_key="talking_head",
                verdict="real_decline",
                sample_size=1,
                metrics_status="UNKNOWN",
                reason="attempted declare",
                confidence=0.9,
                observed_at=now,
            )
        )
        assert fatigue["verdict"] == "insufficient_data"
        cluster = brain.save_comment_cluster(
            CommentClusterRecord(
                cluster_id="c1",
                project="wealth_blueprint",
                theme="How much emergency fund?",
                comment_count=0,
                created_at=now,
            )
        )
        assert cluster["status"] == "observe"
        assert cluster["metrics_status"] == "UNKNOWN"
        trend = brain.save_trend_decision(
            TrendDecisionRecord(
                signal_id="t1",
                label="AI budgeting apps",
                decision="watch",
                metrics_status="UNKNOWN",
                reason="Brak własnych metryk dopasowania — tylko obserwacja.",
                confidence=0.3,
                project="wealth_blueprint",
                decided_at=now,
            )
        )
        assert trend["decision"] == "watch"
        assert trend["metrics_status"] == "UNKNOWN"


def test_campaign_series_and_character_consent(store):
    with connect(store.settings) as db:
        brain = ProjectContentBrainStore(db)
        now = utc_now_iso()
        campaign = brain.upsert_campaign(
            CampaignRecord(
                campaign_id="camp1",
                project="wealth_blueprint",
                name="Buffer funnel",
                premise="Move viewers from worry to first transfer",
                pieces=[
                    CampaignPiece(role="hook", topic="Why empty accounts sting"),
                    CampaignPiece(role="problem", topic="Lifestyle creep after payday"),
                    CampaignPiece(role="solution", topic="Automate a buffer transfer"),
                    CampaignPiece(role="offer", topic="One checklist for tonight"),
                ],
                created_at=now,
                updated_at=now,
            )
        )
        assert len(campaign["pieces"]) == 4
        series = brain.upsert_series(
            SeriesRecord(
                series_key="buffer-101",
                project="wealth_blueprint",
                name="Buffer 101",
                premise="First cash cushion",
                format="youtube_short",
                next_episode_number=1,
                prior_topics=[],
                created_at=now,
                updated_at=now,
            )
        )
        assert series["series_key"] == "buffer-101"
        character = brain.upsert_character(
            HostCharacterProfile(
                character_id="WB_ALEX_01",
                project="wealth_blueprint",
                display_name="Alex",
                is_real_person=False,
                updated_at=now,
            )
        )
        assert character["character_id"] == "WB_ALEX_01"
        try:
            brain.upsert_character(
                HostCharacterProfile(
                    character_id="REAL_HOST",
                    project="wealth_blueprint",
                    display_name="operator",
                    is_real_person=True,
                    likeness_consent=False,
                    voice_consent=True,
                    reference_consent=True,
                    updated_at=now,
                )
            )
            raised = False
        except ValueError:
            raised = True
        assert raised is True


def test_global_feedback_scope_applies_across_orders(store):
    first = store.create(ProductionOrder(topic="Feedback source film"))
    ProductionFeedbackStore(store.settings).add(
        first["id"], ProductionFeedback(note="Keep CTAs free of bio-link promises", scope="global")
    )
    second = store.create(ProductionOrder(topic="Another unrelated savings tip"))
    notes = [r["note"] for r in second["profile"]["feedback_rules"]]
    assert any("bio-link" in n or "CTAs" in n for n in notes)
