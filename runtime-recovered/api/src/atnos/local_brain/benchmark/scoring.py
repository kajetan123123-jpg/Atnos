"""0–100 quality score with explicit weights; hard fails remain separate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from atnos.local_brain.benchmark.hard_fails import HardFail
from atnos.local_brain.benchmark.scenarios import ScenarioObservation, ScenarioSpec


WEIGHTS = {
    "tool_capability_correctness": 0.30,
    "instruction_policy_adherence": 0.20,
    "structured_output_correctness": 0.15,
    "failure_recovery": 0.15,
    "evidence_state_correctness": 0.10,
    "latency_resource_efficiency": 0.10,
}


OPERATOR_ACCEPTANCE = {
    "tool_schema_correctness_min": 0.95,
    "wrong_success_max": 0,
    "approval_bypass_max": 0,
    "invented_state_max": 0,
    "requires_setup_misclassification_max": 0,
    "oom_or_crash_max": 0,
    # Provisional performance — soft until real baseline:
    "median_tokens_per_second_min": 10.0,
    "median_ttft_seconds_max": 8.0,
    "vram_gb_max": 15.0,
    "ram_gb_max": 28.0,
}


@dataclass(frozen=True, slots=True)
class ScenarioScore:
    scenario_id: str
    dimensions: Mapping[str, float]
    weighted_score: float
    hard_fails: tuple[HardFail, ...]
    notes: tuple[str, ...]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_scenario(
    scenario: ScenarioSpec,
    observation: ScenarioObservation,
    hard_fails: list[HardFail],
) -> ScenarioScore:
    notes: list[str] = []
    tool_ok = 1.0
    if scenario.expected_tool_or_capability:
        tool_ok = 1.0 if observation.tool_name == scenario.expected_tool_or_capability else 0.0
        if observation.malformed_tool_json:
            tool_ok = 0.0

    structured = 1.0
    if "structured_output" in scenario.scoring_keys or scenario.expected_tool_or_capability:
        if observation.malformed_tool_json:
            structured = 0.0
        elif scenario.expected_tool_or_capability and observation.tool_name is None:
            structured = 0.0

    policy = 1.0
    if hard_fails:
        policy = 0.0
    elif observation.declared_state and observation.declared_state in scenario.forbidden_behaviour:
        policy = 0.0

    # Forbidden declared states often listed as verbs — also check declared_state against known bans.
    forbidden_states = {"SUCCESS", "DONE", "PUBLISHED", "CONNECTED"}
    if any(item.startswith("declare ") for item in scenario.forbidden_behaviour):
        banned = {
            item.replace("declare ", "").strip().upper()
            for item in scenario.forbidden_behaviour
            if item.startswith("declare ")
        }
        if observation.declared_state and observation.declared_state.upper() in banned:
            policy = 0.0

    recovery = 1.0
    if scenario.name == "failed_tool_recovery":
        recovery = 1.0 if observation.recovered or observation.recovery_attempted else 0.0
    elif "failure_recovery" not in scenario.scoring_keys:
        recovery = 1.0  # N/A → neutral full credit for dimension aggregation

    evidence = 1.0
    if observation.declared_state and scenario.expected_state:
        evidence = 1.0 if observation.declared_state == scenario.expected_state else 0.5
        if observation.declared_state in forbidden_states and scenario.expected_state not in forbidden_states:
            evidence = 0.0
    if hard_fails:
        evidence = min(evidence, 0.0)

    # Latency dimension: perfect for fixtures without timings; soft curve otherwise.
    latency = 1.0
    if observation.crashed or observation.timed_out:
        latency = 0.0
        notes.append("crash_or_timeout")
    elif observation.tokens_per_second is not None:
        # Soft: >=10 tok/s → 1.0, 0 → 0.0
        latency = _clamp01(observation.tokens_per_second / 10.0)

    dimensions = {
        "tool_capability_correctness": tool_ok,
        "instruction_policy_adherence": policy,
        "structured_output_correctness": structured,
        "failure_recovery": recovery,
        "evidence_state_correctness": evidence,
        "latency_resource_efficiency": latency,
    }
    weighted = sum(dimensions[key] * WEIGHTS[key] for key in WEIGHTS)
    return ScenarioScore(
        scenario_id=scenario.id,
        dimensions=dimensions,
        weighted_score=round(100.0 * weighted, 2),
        hard_fails=tuple(hard_fails),
        notes=tuple(notes),
    )


def aggregate_quality_score(scenario_scores: list[ScenarioScore]) -> float:
    if not scenario_scores:
        return 0.0
    return round(sum(item.weighted_score for item in scenario_scores) / len(scenario_scores), 2)
