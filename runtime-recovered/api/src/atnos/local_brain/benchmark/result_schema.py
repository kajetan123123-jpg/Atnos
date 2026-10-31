"""Benchmark result schema and verdict rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class BenchVerdict(str, Enum):
    REJECT = "REJECT"
    EXPERIMENTAL = "EXPERIMENTAL"
    ROLE_CANDIDATE = "ROLE_CANDIDATE"
    ROLE_WINNER = "ROLE_WINNER"


@dataclass(slots=True)
class BenchmarkRunResult:
    run_id: str
    timestamp: str
    model: str
    source: str
    revision: str | None
    quant: str | None
    runtime: str
    runtime_revision: str | None
    backend: str | None
    context: str
    hardware: Mapping[str, Any]
    scenario_results: list[dict[str, Any]]
    hard_failures: list[dict[str, Any]]
    quality_score: float
    performance: Mapping[str, Any]
    memory: Mapping[str, Any]
    metrics: Mapping[str, Any]
    verdict: BenchVerdict
    role: str | None = None
    compared_models_for_role: int = 0
    notes: list[str] = field(default_factory=list)
    fake_provider: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        return payload


def decide_verdict(
    *,
    hard_failure_count: int,
    quality_score: float,
    crashed: bool,
    fake_provider: bool,
    compared_models_for_role: int,
    acceptance_hard_ok: bool,
) -> BenchVerdict:
    """ROLE_WINNER requires >=2 real models compared for the same role."""
    if fake_provider:
        # Fixtures validate harness only.
        if hard_failure_count or crashed or not acceptance_hard_ok:
            return BenchVerdict.REJECT
        return BenchVerdict.EXPERIMENTAL

    if hard_failure_count or crashed or not acceptance_hard_ok:
        return BenchVerdict.REJECT

    if quality_score < 70:
        return BenchVerdict.EXPERIMENTAL

    if compared_models_for_role >= 2 and quality_score >= 85:
        return BenchVerdict.ROLE_WINNER

    return BenchVerdict.ROLE_CANDIDATE
