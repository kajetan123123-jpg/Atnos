import json
from pathlib import Path

import pytest

from atnos.production.order_store import ProductionBlocked, REQUIRED_GATE_STAGES, checkpoint_fingerprints
from test_production_order_store import store, create


def stage(store, job, token, name, status="passed", extra=None, files=()):
    path = store.context(job).artifact_dir / f"{name}.json"
    payload = {"status": status, **(extra or {})}
    store.begin(job, token, name, {})
    path.write_text(json.dumps(payload), encoding="utf-8")
    store.complete(job, token, name, payload, [path, *files])
    return path


def package_stage(store, job, token):
    rows = store.get(job)["checkpoints"]
    files = [store.context(job).artifact_dir / file["path"] for row in rows for file in row["files"]]
    return stage(store, job, token, "package", extra={"checkpoint_fingerprints": checkpoint_fingerprints(rows)}, files=files)


@pytest.mark.parametrize("defect", ["missing", "failed", "tampered"])
def test_missing_failed_or_changed_gate_never_yields_ready(store, defect):
    job = create(store)["id"]
    gates = REQUIRED_GATE_STAGES
    with store.lease(job) as token:
        for gate, name in gates.items():
            if gate == "package" or (gate == "semantic" and defect == "missing"):
                continue
            path = stage(store, job, token, name, "failed" if gate == "semantic" and defect == "failed" else "passed")
        package = package_stage(store, job, token)
        if defect == "tampered":
            (store.context(job).artifact_dir / "semantic_qa.json").write_text("modified after inspection")
        with pytest.raises(ProductionBlocked, match="semantic"):
            store.finish(job, token, package)
    assert store.get(job)["status"] == "blocked"


def test_ready_requires_every_gate_and_evidence(store):
    job = create(store)["id"]
    with store.lease(job) as token:
        for name in REQUIRED_GATE_STAGES.values():
            if name != "package":
                stage(store, job, token, name)
        path = package_stage(store, job, token)
        result = store.finish(job, token, path)
    assert result["status"] == "ready"
    assert result["can_resume"] is False


@pytest.mark.parametrize("defect", ["borrowed_gate", "unverified_file", "changed_dependency"])
def test_completion_requires_canonical_gates_and_same_packaged_dependencies(store, defect, monkeypatch):
    # This fixture isolates the completion/evidence contract. Financial admission
    # has its own real-ledger tests; otherwise borrowed_gate stops before finish.
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',
                        lambda **_: {'blocked': False})
    job = create(store)["id"]
    with store.lease(job) as token:
        names = ["media_qa"] if defect == "borrowed_gate" else list(REQUIRED_GATE_STAGES.values())
        for name in names:
            if name != "package":
                stage(store, job, token, name)
        path = package_stage(store, job, token)
        if defect == "unverified_file":
            path = path.with_name("not_the_package.json")
            path.write_text("{}")
        if defect == "changed_dependency":
            stage(store, job, token, "new_scene", extra={"status": "passed"})
        with pytest.raises(ProductionBlocked):
            store.finish(job, token, path)
    assert store.get(job)["status"] == "blocked"
