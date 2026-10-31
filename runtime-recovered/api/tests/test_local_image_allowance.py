from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from PIL import Image
import pytest

from atnos.intelligence.storage import connect
from atnos.production.local_image_allowance import (
    MAX_LOCAL_IMAGE_REQUESTS,
    TABLE,
    effective_local_image_allowance,
    local_image_allowance_recovery_evidence,
    register_local_image_allowance,
)
from atnos.production.order_autonomy import enable, policy
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore
from atnos.production.project_profile import ProductionOrder, load_project_profile, profile_for_order
from test_support import build_test_settings


def _isolated_local_lane(temp: str, monkeypatch, *, delivery_mode="local_only", historical_attempts=17, provider="local"):
    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
                        lambda **_: {"blocked": False})
    root = Path(temp)
    settings = build_test_settings(root)
    with connect(settings) as db:
        db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')")
        db.commit()
    store = ProductionOrderStore(settings)
    order = ProductionOrder(topic="A simple savings decision", delivery_mode=delivery_mode, visual_style_id="A")
    job_id = store.create(order)["id"]
    context = store.context(job_id)
    reference = root / "reference.png"
    Image.new("RGB", (32, 32), "#203040").save(reference)
    from atnos.production.wb_image_api import register_image_lane, register_local_image_lane

    if provider == "local":
        lane = register_local_image_lane(settings, job_id, reference_path=reference, authorized=True)
    else:
        lane = register_image_lane(settings, job_id, reference_path=reference, authorized=True)
    if historical_attempts:
        with store.lease(job_id) as token:
            for index in range(historical_attempts):
                stage = f"assets/image_local/historical-{index:02d}"
                assert store.begin(job_id, token, stage, {"historical": index}, local_model_call=True) is None
                store.fail(job_id, token, stage, category="execution_failure", message="Recorded failed local candidate")
    enable(settings, job_id, reason="User authorizes bounded autonomous local-only film production.", max_recoveries=6)
    return settings, store, job_id, context, lane, reference


def _temp_root():
    root = Path(r"E:\Atnos\Temp")
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="local-image-allowance-", dir=root)


def _block_at_local_image_cap(store, job_id):
    message = "Authorized image request limit reached; history and cost remain preserved"
    with store.lease(job_id) as token:
        store.block(job_id, token, "image_generation", category="execution_failure", message=message)


def test_append_only_extension_preserves_original_lane_and_historical_accounting(monkeypatch):
    with _temp_root() as temp:
        settings, store, job_id, context, lane, _ = _isolated_local_lane(temp, monkeypatch)
        receipt = context.artifact_dir / "image_local_authorization.json"
        original_receipt = receipt.read_bytes()
        original = store.get(job_id)
        original_policy = policy(settings, job_id)

        extension = register_local_image_allowance(
            settings, job_id, 100,
            reason="User explicitly authorizes extending free local illustration attempts to finish the film.",
            authorized=True,
        )

        rows = store.get(job_id)
        assert extension["maximum"] == MAX_LOCAL_IMAGE_REQUESTS
        assert extension["consumed_attempts_at_registration"] == 17
        assert effective_local_image_allowance(settings, job_id, lane) == 100
        assert receipt.read_bytes() == original_receipt
        assert extension["lane_digest"] == lane["digest"]
        assert rows["profile_hash"] == original["profile_hash"]
        assert rows["provider_calls"] == original["provider_calls"] == 17
        assert rows["reserved_cost_units"] == original["reserved_cost_units"] == 0
        assert policy(settings, job_id)["recoveries_used"] == original_policy["recoveries_used"] == 0
        with connect(settings) as db:
            records = [dict(row) for row in db.execute(f"SELECT * FROM {TABLE} WHERE job_id=?", (job_id,))]
        assert len(records) == 1
        payload = json.loads(records[0]["payload_json"])
        assert payload["provider"] == "comfyui-local"
        assert payload["scope"] == "local_only_wealth_blueprint_comfyui"
        assert payload["cost_per_request"] == 0
        assert payload["provider_call_limit_changed"] is False


def test_effective_allowance_defaults_to_immutable_lane_cap_without_extension(monkeypatch):
    with _temp_root() as temp:
        settings, _, job_id, _, lane, _ = _isolated_local_lane(temp, monkeypatch, historical_attempts=0)
        assert effective_local_image_allowance(settings, job_id, lane) == lane["max_requests"] == 20


def test_allowance_extensions_form_append_only_digest_chain(monkeypatch):
    with _temp_root() as temp:
        settings, _, job_id, _, lane, _ = _isolated_local_lane(temp, monkeypatch, historical_attempts=0)
        first = register_local_image_allowance(
            settings, job_id, 40,
            reason="User explicitly authorizes a first finite local allowance extension.", authorized=True,
        )
        second = register_local_image_allowance(
            settings, job_id, 100,
            reason="User explicitly authorizes a second finite local allowance extension.", authorized=True,
        )
        assert second["previous_digest"] == first["digest"]
        assert second["sequence"] == 2
        assert effective_local_image_allowance(settings, job_id, lane) == 100


@pytest.mark.parametrize("maximum", [True, False, 17, 20, 101])
def test_rejects_boolean_nonextension_consumption_shortfall_and_overbound(maximum, monkeypatch):
    with _temp_root() as temp:
        settings, _, job_id, _, _, _ = _isolated_local_lane(temp, monkeypatch)
        with pytest.raises(ValueError):
            register_local_image_allowance(
                settings, job_id, maximum,
                reason="User explicitly authorizes a finite local allowance extension.",
                authorized=True,
            )


def test_rejects_missing_explicit_authorization_and_disabled_autonomy(monkeypatch):
    with _temp_root() as temp:
        settings, _, job_id, _, _, _ = _isolated_local_lane(temp, monkeypatch, historical_attempts=0)
        with pytest.raises(ValueError, match="Explicit authorization"):
            register_local_image_allowance(
                settings, job_id, 40,
                reason="User explicitly authorizes a finite local allowance extension.",
            )
        with connect(settings) as db:
            db.execute("UPDATE production_order_autonomy SET enabled=0 WHERE job_id=?", (job_id,))
            db.commit()
        with pytest.raises(OrderConflict, match="Enabled autonomous"):
            register_local_image_allowance(
                settings, job_id, 40,
                reason="User explicitly authorizes a finite local allowance extension.",
                authorized=True,
            )


def test_rejects_cloud_lane_nonlocal_delivery_and_other_channel(monkeypatch):
    with _temp_root() as temp:
        settings, store, job_id, _, cloud, _ = _isolated_local_lane(
            temp, monkeypatch, historical_attempts=0, provider="cloud")
        assert cloud["provider"] == "openai-cloud"
        with pytest.raises(OrderConflict, match="local ComfyUI"):
            register_local_image_allowance(
                settings, job_id, 40,
                reason="User explicitly authorizes a finite local allowance extension.", authorized=True,
            )

        project_policy_order = store.create(ProductionOrder(
            topic="A project policy order", delivery_mode="project_policy", visual_style_id="A"))
        with pytest.raises(OrderConflict, match="local-only Wealth Blueprint"):
            register_local_image_allowance(
                settings, project_policy_order["id"], 40,
                reason="User explicitly authorizes a finite local allowance extension.", authorized=True,
            )

        story_order = ProductionOrder(
            project="children_stories", production_scope="script_preview", delivery_mode="local_only",
            topic="A quiet bedtime story")
        account_id = profile_for_order(load_project_profile(settings, story_order.project), story_order)["account_id"]
        with connect(settings) as db:
            db.execute("INSERT OR IGNORE INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES(?,?,?,?)",
                       (account_id, '["stories"]', "US", "2026-09-06T00:00:00Z"))
            db.commit()
        story = store.create(story_order)
        with pytest.raises(OrderConflict, match="local-only Wealth Blueprint"):
            register_local_image_allowance(
                settings, story["id"], 40,
                reason="User explicitly authorizes a finite local allowance extension.", authorized=True,
            )


def test_tampered_allowance_record_fails_closed(monkeypatch):
    with _temp_root() as temp:
        settings, _, job_id, _, lane, _ = _isolated_local_lane(temp, monkeypatch)
        register_local_image_allowance(
            settings, job_id, 40,
            reason="User explicitly authorizes a finite local allowance extension.", authorized=True,
        )
        with connect(settings) as db:
            row = db.execute(f"SELECT payload_json FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone()
            payload = json.loads(row["payload_json"])
            payload["maximum"] = 100
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(f"UPDATE {TABLE} SET payload_json=? WHERE job_id=?", (json.dumps(payload), job_id))
            db.execute(f"DROP TRIGGER {TABLE}_no_update")
            db.execute(f"UPDATE {TABLE} SET payload_json=? WHERE job_id=?", (json.dumps(payload), job_id))
            db.commit()
        with pytest.raises(ProductionBlocked, match="integrity"):
            effective_local_image_allowance(settings, job_id, lane)


def test_monitor_evidence_requires_a_valid_extension_and_preserves_exact_cap_block(monkeypatch):
    with _temp_root() as temp:
        settings, store, job_id, _, lane, _ = _isolated_local_lane(temp, monkeypatch, historical_attempts=20)
        _block_at_local_image_cap(store, job_id)
        state = store.get(job_id)
        assert local_image_allowance_recovery_evidence(store, state) is None

        register_local_image_allowance(
            settings, job_id, 100,
            reason="User explicitly authorizes a finite local allowance extension.", authorized=True,
        )
        evidence = local_image_allowance_recovery_evidence(store, store.get(job_id))
        assert evidence == {
            "kind": "extended_local_image_allowance",
            "stage": "image_generation",
            "lane_digest": lane["digest"],
            "original_maximum": 20,
            "effective_maximum": 100,
            "consumed_attempts": 20,
            "uncertain_prior_dispatch": False,
            "normal_dispatch_only": True,
        }


def test_monitor_evidence_rejects_tampered_foreign_scope_and_pending_image_dispatch(monkeypatch):
    with _temp_root() as temp:
        settings, store, job_id, _, _, _ = _isolated_local_lane(temp, monkeypatch, historical_attempts=20)
        _block_at_local_image_cap(store, job_id)
        register_local_image_allowance(
            settings, job_id, 100,
            reason="User explicitly authorizes a finite local allowance extension.", authorized=True,
        )
        state = store.get(job_id)
        assert local_image_allowance_recovery_evidence(store, state) is not None

        foreign = dict(state)
        foreign["request"] = {**state["request"], "project": "children_stories"}
        assert local_image_allowance_recovery_evidence(store, foreign) is None

        pending = dict(state)
        pending["checkpoints"] = [*state["checkpoints"], {
            "stage": "assets/image_local/new-scene",
            "status": "running",
            "attempts": 1,
            "error_json": None,
        }]
        assert local_image_allowance_recovery_evidence(store, pending) is None

        with connect(settings) as db:
            row = db.execute(f"SELECT payload_json FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone()
            payload = json.loads(row["payload_json"])
            payload["profile_hash"] = "0" * 64
            db.execute(f"DROP TRIGGER {TABLE}_no_update")
            db.execute(f"UPDATE {TABLE} SET payload_json=? WHERE job_id=?", (json.dumps(payload), job_id))
            db.commit()
        assert local_image_allowance_recovery_evidence(store, state) is None


def test_monitor_dispatches_once_per_distinct_append_only_allowance(monkeypatch):
    from atnos.production.order_autonomy import recover_due

    with _temp_root() as temp:
        settings, store, job_id, context, lane, _ = _isolated_local_lane(
            temp, monkeypatch, historical_attempts=20)
        _block_at_local_image_cap(store, job_id)
        receipt = context.artifact_dir / "image_local_authorization.json"
        receipt_bytes = receipt.read_bytes()
        original = store.get(job_id)
        dispatches = []
        monkeypatch.setattr("atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch",
                            lambda self, current: dispatches.append(current) or {"id": current})

        assert recover_due(settings) == []
        assert dispatches == []
        register_local_image_allowance(
            settings, job_id, 40,
            reason="User explicitly authorizes a first finite local allowance extension.", authorized=True,
        )
        assert recover_due(settings) == [{"id": job_id}]
        assert recover_due(settings) == []
        after_first = store.get(job_id)
        assert dispatches == [job_id]
        assert after_first["provider_calls"] == original["provider_calls"]
        assert after_first["reserved_cost_units"] == original["reserved_cost_units"]
        assert after_first["profile_hash"] == original["profile_hash"]
        assert receipt.read_bytes() == receipt_bytes
        assert effective_local_image_allowance(settings, job_id, lane) == 40
        from atnos.production.wb_image_api import load_image_lane
        assert load_image_lane(settings, job_id)["digest"] == lane["digest"]
        first_reservations = [event for event in after_first["history"]
                              if event["event"] == "autonomous_recovery_reserved"]
        assert len(first_reservations) == 1
        first_failure_hash = first_reservations[0]["detail"]["failure_hash"]

        register_local_image_allowance(
            settings, job_id, 100,
            reason="User explicitly authorizes a second larger local allowance extension.", authorized=True,
        )
        assert recover_due(settings) == [{"id": job_id}]
        assert recover_due(settings) == []
        after_second = store.get(job_id)
        reservations = [event for event in after_second["history"]
                        if event["event"] == "autonomous_recovery_reserved"]
        assert dispatches == [job_id, job_id]
        assert len(reservations) == 2
        assert reservations[1]["detail"]["failure_hash"] != first_failure_hash
        assert reservations[0]["detail"]["kind"] == reservations[1]["detail"]["kind"] == "local_image_allowance_recovery"
        assert after_second["autonomous_recovery"]["used"] == 2
        assert after_second["provider_calls"] == original["provider_calls"]
        assert after_second["reserved_cost_units"] == original["reserved_cost_units"]
        assert after_second["profile_hash"] == original["profile_hash"]
        assert receipt.read_bytes() == receipt_bytes
        assert load_image_lane(settings, job_id)["digest"] == lane["digest"]
