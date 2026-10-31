"""The film worker must let Comfy acquire the GPU lock during image production."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production import order_execution
from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
from atnos.production.steps import (
    order_assets,
    order_delivery,
    order_media,
    order_repair,
    order_voice,
)
from atnos.services import gpu_runtime_lock_service


@pytest.mark.parametrize("asset_status", [None, ("awaiting_approval",), ("missing",),
                                          ("awaiting_approval", "missing")])
def test_order_releases_gpu_for_remotion_asset_generation_and_reacquires_for_qa(
    monkeypatch, tmp_path, asset_status,
):
    held = set()
    seen = []
    job_id = "prod-gpu-handoff"

    class Store:
        def __init__(self):
            self.settings = SimpleNamespace()
            self.state = {"request": {"project": "wealth_blueprint"}, "status": "queued",
                          "can_resume": True, "worker_live": False}
            self.ctx = SimpleNamespace(
                job_id=job_id, artifact_dir=tmp_path,
                artifacts={"production_order": self.state["request"], "project_profile": {},
                           "package": {"output_file": str(tmp_path / "package.json")}},
            )

        def get(self, job):
            assert job == job_id
            return self.state

        def context(self, job):
            assert job == job_id
            return self.ctx

        @contextmanager
        def lease(self, job, **_kwargs):
            assert job == job_id
            yield "worker-token"

        def set_phase(self, job, token, phase):
            assert (job, token) == (job_id, "worker-token")
            seen.append(("phase", phase))

        def finish(self, job, token, package):
            assert (job, token, package) == (job_id, "worker-token", Path(self.ctx.artifacts["package"]["output_file"]))
            self.state["status"] = "ready"
            return self.state

        def block(self, job, token, stage, *, category, message):
            assert asset_status is not None
            pending_review = all(status == "awaiting_approval" for status in asset_status)
            assert (job, token, stage, category) == (
                job_id, "worker-token",
                "asset_review" if pending_review else "asset_planning",
                "awaiting_asset_review" if pending_review else "required_assets_missing",
            )
            assert "scene_production_assets.json" in message
            self.state.update(status="blocked", current_step=stage, error=category)

    store = Store()

    class Lock:
        def __init__(self, _settings):
            pass

        def acquire(self, *, owner, timeout_s):
            assert owner == f"production-order:{job_id}"
            assert timeout_s == 600
            assert not held
            token = object()
            held.add(token)
            seen.append(("acquire", owner))
            return token

        def release(self, token):
            assert token in held
            held.remove(token)
            seen.append(("release", job_id))

    async def preflight(_executor):
        return None

    def stage(name, *, must_hold):
        async def run(_executor):
            assert bool(held) is must_hold, f"wrong GPU lock state during {name}"
            seen.append(("operation", name))
            if name == "render" and asset_status is not None:
                raise SceneAssetsNotReady([
                    {"scene_id": f"s{index:02}", "acquisition": {"status": status}}
                    for index, status in enumerate(asset_status, 1)
                ])
            return {}
        return run

    monkeypatch.setattr(order_execution, "ProductionOrderStore", lambda _settings: store)
    # This fixture exercises renderer lock handoff, without a paid/local image
    # lane authorization DB. Keep that independent capability out of the stub.
    monkeypatch.setattr('atnos.production.wb_image_api.load_image_lane', lambda *args: None)
    monkeypatch.setattr(order_execution, "preflight_order", preflight)
    monkeypatch.setattr(gpu_runtime_lock_service, "GpuRuntimeLockService", Lock)
    monkeypatch.setattr(order_execution, "produce_script", stage("script", must_hold=True))
    monkeypatch.setattr(order_assets, "produce_assets", stage("assets", must_hold=True))
    monkeypatch.setattr(order_voice, "produce_voice", stage("voice", must_hold=True))
    monkeypatch.setattr(order_media, "produce_captions", stage("captions", must_hold=True))
    monkeypatch.setattr(order_media, "produce_edit", stage("render", must_hold=False))
    monkeypatch.setattr(order_delivery, "produce_covers", stage("covers", must_hold=True))
    monkeypatch.setattr(order_repair, "produce_final_quality", stage("qa", must_hold=True))
    monkeypatch.setattr(order_delivery, "produce_package", stage("package", must_hold=True))

    result = asyncio.run(order_execution.run_order(store.settings, job_id))
    assert not held
    render_index = seen.index(("operation", "render"))
    assert seen[render_index - 2] == ("release", job_id)
    if asset_status is not None:
        assert result["status"] == "blocked"
        assert result["current_step"] == (
            "asset_review" if all(status == "awaiting_approval" for status in asset_status) else "asset_planning"
        )
        assert seen.count(("release", job_id)) == 1
    else:
        assert result["status"] == "ready"
        assert seen.count(("release", job_id)) == 2
        assert seen[render_index + 1] == ("acquire", f"production-order:{job_id}")
