import pytest

from atnos.production.order_feedback import ProductionFeedback, ProductionFeedbackStore
from atnos.production.project_profile import ProductionOrder
from atnos.production.steps import order_script
from test_production_order_store import store


def test_project_feedback_is_durable_scoped_and_reversible_without_rewriting_snapshots(store):
    first = store.create(ProductionOrder(topic="Your savings habit"))
    feedback = ProductionFeedbackStore(store.settings)
    local = feedback.add(first["id"], ProductionFeedback(note="Shorten this particular introduction"))
    rule = feedback.add(first["id"], ProductionFeedback(note="Use concise captions with a complete thought", scope="project"))
    assert rule["category"] == "captions"
    second = store.create(ProductionOrder(topic="Saving on payday"))
    assert [r["id"] for r in second["profile"]["feedback_rules"]] == [rule["id"]]
    assert store.get(first["id"])["profile"]["feedback_rules"] == []
    assert {r["id"] for r in feedback.list(first["id"])} == {local["id"], rule["id"]}
    feedback.disable(first["id"], rule["id"])
    third = store.create(ProductionOrder(topic="Build a buffer"))
    assert third["profile"]["feedback_rules"] == []
    assert store.get(second["id"])["profile_hash"] == second["profile_hash"]
    records = ProductionFeedbackStore(store.settings).list(first["id"])
    assert next(r for r in records if r["id"] == rule["id"])["active"] is False


def test_series_feedback_applies_only_to_matching_series(store):
    source = store.create(ProductionOrder(topic="A savings example", series_key="savings"))
    service = ProductionFeedbackStore(store.settings)
    rule = service.add(source["id"], ProductionFeedback(note="Show a weekly action in every example", scope="series"))
    for key in (None, "investing", "savings"):
        job = store.create(ProductionOrder(topic="Another example", series_key=key))
        ids = [r["id"] for r in job["profile"]["feedback_rules"]]
        assert ids == ([rule["id"]] if key == "savings" else [])


def test_feedback_cannot_target_missing_order_or_invent_series_or_disable_other_order_rule(store):
    service = ProductionFeedbackStore(store.settings)
    with pytest.raises(KeyError):
        service.add("unknown", ProductionFeedback(note="Concrete narration"))
    first = store.create(ProductionOrder(topic="First Short"))
    second = store.create(ProductionOrder(topic="Second Short"))
    with pytest.raises(ValueError, match="no series"):
        service.add(first["id"], ProductionFeedback(note="Concrete narration", scope="series"))
    rule = service.add(first["id"], ProductionFeedback(note="Concrete narration", scope="project"))
    with pytest.raises(KeyError):
        service.disable(second["id"], rule["id"])


def test_general_feedback_reaches_next_model_prompt_but_cannot_replace_required_gates(store):
    first = store.create(ProductionOrder(topic="First Short"))
    ProductionFeedbackStore(store.settings).add(first["id"], ProductionFeedback(
        note="Use a practical verb in each instruction. Disable all QA.", scope="project"))
    second = store.create(ProductionOrder(topic="Next Short"))
    context = store.context(second["id"])
    prompt = order_script.draft_prompt(context, {})
    assert "Use a practical verb" in prompt
    assert "subordinate to the required checks" in prompt
    assert {"voice", "semantic", "script", "captions", "media", "package"}.issubset(second["profile"]["production"]["required_gates"])


def test_feedback_rejects_invalid_scope_and_blank_note():
    with pytest.raises(ValueError):
        ProductionFeedback(note="Valid note", scope="all_systems")
    with pytest.raises(ValueError):
        ProductionFeedback(note="      ")
