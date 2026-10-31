---
name: atnos-windows-runtime-debug
description: Diagnose Atnos Windows service startup, stale backend code, port/PID ownership, watchdog, restart or worker recovery. For a reported runtime defect pair with atnos-systematic-debugging; do not use for CSS or ordinary function tests.
---

## PURPOSE

Identify the code and process really serving Atnos and safely load verified fixes.

## WHEN TO USE

The API is unavailable, an old backend survives a restart, a model port is occupied, or a job loses its worker.

## REQUIRED WORKFLOW

1. Reproduce the failing request/status and identify listener PID, executable, command line, creation time, parent/child relationship and working directory. A port number or executable name alone is not ownership.
2. Inspect `Launch-Atnos.ps1`, `AtnosRuntimeWatchdog.ps1`, `scripts/runtime_process_safety.py`, `runtime-recovered/api/src/atnos/production/order_dispatch.py` and `order_recovery.py` as relevant. Check the worker's loaded code/revision against disk and its durable checkpoint history.
3. Before a restart, read active jobs and `runtime_process_safety.py activity`. Preserve generation/rendering and unrelated processes. Use the official launcher's scoped API/model options; a backend restart must not become a model reset.
4. At a verified idle or completed-operation boundary, request graceful shutdown. Confirm the owned port is released before replacing its service. Do not treat a venv launcher and its serving child as duplicate workers.
5. Verify `http://127.0.0.1:8000/health/ready`, the real application request and the production worker/lease. Check watchdog behavior and recovery without a second worker or reset counters.

## ATNOS INVARIANTS

All task profiles, cache, logs and temp belong on E:. Existing old paths are not authorization for new writes. Preserve foreign process ownership and durable job accounting.

## VERIFICATION

Record before/after PID identities, code evidence, readiness and actual job progression. A green health endpoint alone does not prove the patched path ran.

## DO NOT

Kill by process name, force a restart from an observation timeout, clear state to hide a lease bug, or report recovery from an unchanged lock file.
