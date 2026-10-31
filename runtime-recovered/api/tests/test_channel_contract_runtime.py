from __future__ import annotations

import json
from pathlib import Path

import pytest

from atnos.intelligence.storage import connect
from atnos.production.channel_contract import ChannelContractError, load_and_validate
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore
from atnos.production.project_profile import ProductionOrder, load_project_profile
from test_support import build_test_settings


MANIFEST = Path(r"E:\Atnos\App\config\channel_comfy_projects.json")


def _configured_store(tmp_path: Path) -> ProductionOrderStore:
    settings = build_test_settings(tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / MANIFEST.name).write_text(MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    with connect(settings) as db:
        db.execute(
            "INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES(?,?,?,?)",
            ("yt-wealth-blueprint", '["finance"]', "US", "2026-09-06T00:00:00Z"),
        )
        db.commit()
    return ProductionOrderStore(settings)


def test_contract_freezes_project_models_voice_and_style(tmp_path: Path) -> None:
    store = _configured_store(tmp_path)
    job = store.create(ProductionOrder(topic="A concrete savings habit"))
    contract = job["profile"]["channel_contract"]
    assert contract["project"] == "wealth_blueprint"
    assert contract["style_contract_hash"]
    assert contract["voice_contract_hash"]
    assert contract["workflow_contract_hash"]
    assert all(Path(item["path"]).is_file() for item in contract["model_files"])


def test_changed_manifest_blocks_before_next_stage(tmp_path: Path) -> None:
    store = _configured_store(tmp_path)
    job = store.create(ProductionOrder(topic="A concrete savings habit"))["id"]
    path = tmp_path / "config" / MANIFEST.name
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["projects"]["wealth_blueprint"]["visual"]["style"] += ", altered"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProductionBlocked, match="channel_contract"):
        with store.lease(job) as token:
            store.begin(job, token, "script", {})
    assert json.loads(store.get(job)["error"])["category"] == "channel_contract"


def test_stage_output_cannot_mix_another_channel(tmp_path: Path) -> None:
    store = _configured_store(tmp_path)
    job = store.create(ProductionOrder(topic="A concrete savings habit"))["id"]
    with pytest.raises(ProductionBlocked, match="mixes channel"):
        with store.lease(job) as token:
            store.begin(job, token, "script", {})
            path = store.context(job).artifact_dir / "script.json"
            path.write_text("{}", encoding="utf-8")
            store.complete(job, token, "script", {"channel_profile_key": "children_stories"}, [path])
