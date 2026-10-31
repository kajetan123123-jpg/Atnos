"""Local Brain readiness gate — READY_TO_BENCHMARK only when definition-of-done holds."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from atnos.local_brain.benchmark.fake_providers import build_perfect_provider
from atnos.local_brain.benchmark.harness import run_benchmark
from atnos.local_brain.benchmark.scenarios import SCENARIOS
from atnos.local_brain.hardware_probe import collect_hardware_probe
from atnos.local_brain.role_contracts import ROLE_CONTRACTS, BrainModelRole, unbound_role_bindings
from atnos.local_brain.shortlist import first_recommended_model, shortlist_as_dicts
from atnos.local_brain.storage import ensure_storage_layout, probe_disk


CANONICAL_ROOT = r"E:\Atnos\App"
RUNTIME_RECOVERED_ROLE = (
    "Active API/backend/remotion source tree inside the App git worktree "
    "(not a discarded orphan). Canonical docs live under E:\\Atnos\\App\\docs\\; "
    "Python package under runtime-recovered\\api\\src\\atnos\\."
)


@dataclass(slots=True)
class ReadinessReport:
    CANONICAL_ROOT: str
    RUNTIME_RECOVERED_ROLE: str
    SAFE_TO_CONTINUE: bool
    ROLE_CONTRACTS: dict[str, Any]
    HARNESS: dict[str, Any]
    TEST_RESULTS: dict[str, Any]
    HARDWARE_PROBE: dict[str, Any]
    DISK_FREE: dict[str, Any]
    RUNTIME_OPTIONS: dict[str, Any]
    READY_TO_BENCHMARK: bool
    FIRST_MODEL_RECOMMENDED: dict[str, Any]
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def local_brain_readiness_report(*, run_fixture_bench: bool = True) -> ReadinessReport:
    ensure_storage_layout()
    missing: list[str] = []
    notes: list[str] = [
        "No model download performed.",
        "Production Chat / Ollama / CLOUD_LLM_ALLOWED untouched by this report.",
        "FAKE PASS != MODEL PASS.",
    ]

    roles_ok = set(ROLE_CONTRACTS) == set(BrainModelRole) and len(ROLE_CONTRACTS) == 4
    if not roles_ok:
        missing.append("role_contracts")

    harness_ok = len(SCENARIOS) >= 10
    if not harness_ok:
        missing.append("harness_scenarios")

    hw = collect_hardware_probe().to_dict()
    disk = probe_disk().to_dict()

    test_results: dict[str, Any] = {"fixture_bench_ran": False}
    if run_fixture_bench:
        result = run_benchmark(
            build_perfect_provider(),
            model="fake-perfect",
            source="fixture",
            hardware=hw,
            write_report=True,
        )
        test_results = {
            "fixture_bench_ran": True,
            "run_id": result.run_id,
            "verdict": result.verdict.value,
            "quality_score": result.quality_score,
            "hard_failures": len(result.hard_failures),
            "fake_provider": result.fake_provider,
            "note": "FAKE PASS != MODEL PASS",
        }
        if result.hard_failures:
            missing.append("fixture_bench_hard_fails")
        if result.verdict.value == "REJECT":
            missing.append("fixture_bench_reject")

    runtime_options = hw.get("runtimes") or {}
    ready = (
        roles_ok
        and harness_ok
        and not missing
        and bool(disk.get("free_bytes", 0) > 0)
        and hw.get("gpu_name") is not None
    )

    return ReadinessReport(
        CANONICAL_ROOT=CANONICAL_ROOT,
        RUNTIME_RECOVERED_ROLE=RUNTIME_RECOVERED_ROLE,
        SAFE_TO_CONTINUE=True,
        ROLE_CONTRACTS={
            "roles": [role.value for role in BrainModelRole],
            "bindings": unbound_role_bindings(),
            "contracts_present": roles_ok,
        },
        HARNESS={
            "scenario_count": len(SCENARIOS),
            "scenario_ids": [s.id for s in SCENARIOS],
            "shortlist": shortlist_as_dicts(),
        },
        TEST_RESULTS=test_results,
        HARDWARE_PROBE=hw,
        DISK_FREE=disk,
        RUNTIME_OPTIONS=runtime_options,
        READY_TO_BENCHMARK=ready,
        FIRST_MODEL_RECOMMENDED=asdict_safe(first_recommended_model()),
        missing=missing,
        notes=notes,
    )


def asdict_safe(entry: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(entry)
