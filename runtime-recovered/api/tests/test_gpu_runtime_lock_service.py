from __future__ import annotations

import json
import os
from time import time

import psutil

import pytest

from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
from test_support import build_test_settings


def test_gpu_runtime_lock_blocks_second_owner_until_released(tmp_path):
    settings = build_test_settings(tmp_path)
    service = GpuRuntimeLockService(settings)

    lease = service.acquire(owner="first", timeout_s=0.1)
    try:
        with pytest.raises(TimeoutError):
            service.acquire(owner="second", timeout_s=0.1)
    finally:
        service.release(lease)

    second = service.acquire(owner="second", timeout_s=0.1)
    service.release(second)
    assert not service.lock_dir.exists()


def test_gpu_runtime_lock_clears_stale_owner(tmp_path):
    settings = build_test_settings(tmp_path)
    service = GpuRuntimeLockService(settings)
    service.lock_dir.mkdir(parents=True)
    (service.lock_dir / "owner.json").write_text(
        json.dumps({"owner": "old", "token": "old-token", "created_at_epoch": time() - 10_000}),
        encoding="utf-8",
    )

    lease = service.acquire(owner="fresh", timeout_s=0.1, stale_after_s=1.0)
    try:
        payload = json.loads((service.lock_dir / "owner.json").read_text(encoding="utf-8"))
        assert payload["owner"] == "fresh"
    finally:
        service.release(lease)


def test_gpu_runtime_lock_clears_fresh_owner_from_dead_process(tmp_path):
    settings = build_test_settings(tmp_path)
    service = GpuRuntimeLockService(settings)
    service.lock_dir.mkdir(parents=True)
    (service.lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "owner": "crashed-job",
                "token": "dead-token",
                "created_at_epoch": time(),
                "process_id": 999_999_999,
                "process_started_at_epoch": time(),
            }
        ),
        encoding="utf-8",
    )

    lease = service.acquire(owner="recovered", timeout_s=0.1)
    try:
        payload = json.loads((service.lock_dir / "owner.json").read_text(encoding="utf-8"))
        assert payload["owner"] == "recovered"
    finally:
        service.release(lease)


def test_gpu_runtime_lock_preserves_old_owner_while_process_is_alive(tmp_path):
    settings = build_test_settings(tmp_path)
    service = GpuRuntimeLockService(settings)
    service.lock_dir.mkdir(parents=True)
    (service.lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "owner": "long-running-render",
                "token": "live-token",
                "created_at_epoch": time() - 10_000,
                "process_id": os.getpid(),
                "process_started_at_epoch": psutil.Process(os.getpid()).create_time(),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(TimeoutError, match="long-running-render"):
        service.acquire(owner="must-wait", timeout_s=0.1, stale_after_s=1.0)

    payload = json.loads((service.lock_dir / "owner.json").read_text(encoding="utf-8"))
    assert payload["token"] == "live-token"


def test_gpu_runtime_lock_preserves_owner_when_process_access_is_denied(monkeypatch, tmp_path):
    settings = build_test_settings(tmp_path)
    service = GpuRuntimeLockService(settings)
    service.lock_dir.mkdir(parents=True)
    (service.lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "owner": "protected-runtime",
                "token": "protected-token",
                "created_at_epoch": time() - 10_000,
                "process_id": 4242,
                "process_started_at_epoch": time() - 10_000,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(psutil, "Process", lambda _pid: (_ for _ in ()).throw(psutil.AccessDenied(4242)))

    with pytest.raises(TimeoutError, match="protected-runtime"):
        service.acquire(owner="must-not-overlap", timeout_s=0.1, stale_after_s=1.0)

    assert json.loads((service.lock_dir / "owner.json").read_text(encoding="utf-8"))["token"] == "protected-token"


def test_gpu_runtime_lock_recovers_old_orphan_directory_without_owner_file(tmp_path):
    settings = build_test_settings(tmp_path)
    service = GpuRuntimeLockService(settings)
    service.lock_dir.mkdir(parents=True)
    old_time = time() - 10_000
    os.utime(service.lock_dir, (old_time, old_time))

    lease = service.acquire(owner="recovered-orphan", timeout_s=0.1, stale_after_s=1.0)
    try:
        payload = json.loads((service.lock_dir / "owner.json").read_text(encoding="utf-8"))
        assert payload["owner"] == "recovered-orphan"
    finally:
        service.release(lease)
