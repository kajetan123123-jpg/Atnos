"""Local Brain benchmark harness — adapter-agnostic; fixtures first."""

from __future__ import annotations

import json
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from atnos.local_brain.benchmark.context_levels import CONTEXT_LEVELS, ContextLevel, pad_context
from atnos.local_brain.benchmark.fake_providers import ScriptedLocalProvider
from atnos.local_brain.benchmark.hard_fails import (
    HardFail,
    aggregate_hard_fail_counts,
    evaluate_hard_fails,
)
from atnos.local_brain.benchmark.result_schema import BenchmarkRunResult, decide_verdict
from atnos.local_brain.benchmark.scenarios import SCENARIOS, ScenarioObservation, ScenarioSpec
from atnos.local_brain.benchmark.scoring import OPERATOR_ACCEPTANCE, score_scenario
from atnos.local_brain.hardware_probe import collect_hardware_probe
from atnos.local_brain.provider_contract import (
    LocalChatMessage,
    LocalGenerationRequest,
    LocalModelProvider,
)
from atnos.local_brain.storage import REPORTS_ROOT, ensure_storage_layout


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_scenario(scenario: ScenarioSpec, provider: LocalModelProvider) -> ScenarioObservation:
    messages = (
        LocalChatMessage(role="system", content="You are Atnos Local Brain benchmark subject."),
        LocalChatMessage(role="user", content=json.dumps(dict(scenario.input), ensure_ascii=False)),
    )
    if scenario.context_level in {ContextLevel.LONG.value, "LONG"}:
        filler = pad_context("long_context ", target_tokens=12_000)
        messages = (*messages, LocalChatMessage(role="user", content=filler))

    result = provider.generate(
        LocalGenerationRequest(
            messages=messages,
            metadata={"scenario_id": scenario.id, "scenario_name": scenario.name},
        )
    )
    if isinstance(provider, ScriptedLocalProvider) and provider.last_observation is not None:
        return provider.last_observation

    tool_name = result.tool_calls[0].name if result.tool_calls else None
    tool_args = dict(result.tool_calls[0].arguments) if result.tool_calls else {}
    declared = None
    if isinstance(result.raw, Mapping):
        declared = result.raw.get("declared_state")
    return ScenarioObservation(
        scenario_id=scenario.id,
        content=result.content,
        tool_name=tool_name,
        tool_arguments=tool_args,
        declared_state=str(declared) if declared else None,
        latency_ms=result.latency_ms,
        ttft_ms=result.ttft_ms,
        tokens_per_second=result.tokens_per_second,
        crashed=result.crashed,
        timed_out=result.timed_out,
        error=result.error,
    )


def run_benchmark(
    provider: LocalModelProvider,
    *,
    model: str = "unbound",
    source: str = "fixture",
    revision: str | None = None,
    quant: str | None = None,
    runtime: str | None = None,
    runtime_revision: str | None = None,
    backend: str | None = None,
    context: str = "SHORT",
    role: str | None = "OPERATOR",
    compared_models_for_role: int = 0,
    hardware: Mapping[str, Any] | None = None,
    write_report: bool = True,
) -> BenchmarkRunResult:
    ensure_storage_layout()
    hw = dict(hardware) if hardware is not None else collect_hardware_probe().to_dict()
    runtime_name = runtime or provider.runtime_name
    fake = isinstance(provider, ScriptedLocalProvider) or source == "fixture"

    scenario_results: list[dict[str, Any]] = []
    all_hard: list[dict[str, Any]] = []
    ttfts: list[float] = []
    tps: list[float] = []
    latencies: list[float] = []
    wrong_success = 0
    approval_violations = 0
    hallucinated_state = 0
    recovery_successes = 0
    recovery_total = 0
    tool_correct = 0
    tool_total = 0
    json_correct = 0
    json_total = 0
    crashed_any = False

    for scenario in SCENARIOS:
        observation = _run_scenario(scenario, provider)
        hard = evaluate_hard_fails(scenario, observation)
        scored = score_scenario(scenario, observation, hard)
        scenario_results.append(
            {
                "scenario_id": scenario.id,
                "name": scenario.name,
                "expected_state": scenario.expected_state,
                "observed_state": observation.declared_state,
                "tool_name": observation.tool_name,
                "dimensions": dict(scored.dimensions),
                "weighted_score": scored.weighted_score,
                "hard_failures": [{"code": f.code, "detail": f.detail} for f in hard],
                "notes": list(scored.notes),
                "crashed": observation.crashed,
                "timed_out": observation.timed_out,
            }
        )
        for fail in hard:
            all_hard.append({"code": fail.code, "scenario_id": fail.scenario_id, "detail": fail.detail})
            if fail.code in {"invented_success", "requires_setup_as_success"}:
                wrong_success += 1
            if fail.code == "approval_bypass":
                approval_violations += 1
            if fail.code in {
                "unknown_invented",
                "published_without_evidence",
                "connected_without_evidence",
            }:
                hallucinated_state += 1

        if scenario.expected_tool_or_capability:
            tool_total += 1
            json_total += 1
            if observation.tool_name == scenario.expected_tool_or_capability and not observation.malformed_tool_json:
                tool_correct += 1
                json_correct += 1
            elif not observation.malformed_tool_json and observation.tool_name:
                json_correct += 1

        if scenario.name == "failed_tool_recovery":
            recovery_total += 1
            if observation.recovered or observation.recovery_attempted:
                recovery_successes += 1

        if observation.ttft_ms is not None:
            ttfts.append(observation.ttft_ms)
        if observation.tokens_per_second is not None:
            tps.append(observation.tokens_per_second)
        if observation.latency_ms is not None:
            latencies.append(observation.latency_ms)
        if observation.crashed:
            crashed_any = True

    quality = round(
        sum(float(item["weighted_score"]) for item in scenario_results) / max(1, len(scenario_results)),
        2,
    )

    hard_counts = aggregate_hard_fail_counts(
        HardFail(code=h["code"], scenario_id=h["scenario_id"], detail=h["detail"]) for h in all_hard
    )

    tool_acc = (tool_correct / tool_total) if tool_total else 1.0
    json_acc = (json_correct / json_total) if json_total else 1.0
    median_ttft_ms = statistics.median(ttfts) if ttfts else None
    median_tps = statistics.median(tps) if tps else None
    p95_latency = sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else None

    acceptance_hard_ok = (
        wrong_success <= OPERATOR_ACCEPTANCE["wrong_success_max"]
        and approval_violations <= OPERATOR_ACCEPTANCE["approval_bypass_max"]
        and hallucinated_state <= OPERATOR_ACCEPTANCE["invented_state_max"]
        and hard_counts.get("requires_setup_as_success", 0)
        <= OPERATOR_ACCEPTANCE["requires_setup_misclassification_max"]
        and (0 if not crashed_any else 1) <= OPERATOR_ACCEPTANCE["oom_or_crash_max"]
        and tool_acc >= OPERATOR_ACCEPTANCE["tool_schema_correctness_min"]
        and json_acc >= OPERATOR_ACCEPTANCE["tool_schema_correctness_min"]
    )

    verdict = decide_verdict(
        hard_failure_count=len(all_hard),
        quality_score=quality,
        crashed=crashed_any,
        fake_provider=fake,
        compared_models_for_role=compared_models_for_role,
        acceptance_hard_ok=acceptance_hard_ok,
    )

    metrics = {
        "tool_call_accuracy": round(tool_acc, 4),
        "json_schema_accuracy": round(json_acc, 4),
        "wrong_success_count": wrong_success,
        "approval_violation_count": approval_violations,
        "hallucinated_state_count": hallucinated_state,
        "recovery_success": (recovery_successes / recovery_total) if recovery_total else None,
        "median_ttft_ms": median_ttft_ms,
        "median_tokens_per_second": median_tps,
        "p95_latency_ms": p95_latency,
        "hard_fail_counts": hard_counts,
        "context_levels_defined": [item.level.value for item in CONTEXT_LEVELS],
        "fake_pass_is_not_model_pass": True,
    }
    memory = {
        "peak_vram_bytes": hw.get("vram_total_bytes"),
        "peak_ram_bytes": None,
        "note": "Peak process VRAM/RAM filled when real runtime runs; probe totals recorded in hardware.",
    }
    performance = {
        "median_ttft_ms": median_ttft_ms,
        "median_tokens_per_second": median_tps,
        "p95_latency_ms": p95_latency,
        "provisional_targets": {
            "median_tokens_per_second_min": OPERATOR_ACCEPTANCE["median_tokens_per_second_min"],
            "median_ttft_seconds_max": OPERATOR_ACCEPTANCE["median_ttft_seconds_max"],
            "vram_gb_max": OPERATOR_ACCEPTANCE["vram_gb_max"],
            "ram_gb_max": OPERATOR_ACCEPTANCE["ram_gb_max"],
        },
    }

    result = BenchmarkRunResult(
        run_id=str(uuid.uuid4()),
        timestamp=_utcnow(),
        model=model,
        source=source,
        revision=revision,
        quant=quant,
        runtime=runtime_name,
        runtime_revision=runtime_revision,
        backend=backend,
        context=context,
        hardware=hw,
        scenario_results=scenario_results,
        hard_failures=all_hard,
        quality_score=quality,
        performance=performance,
        memory=memory,
        metrics=metrics,
        verdict=verdict,
        role=role,
        compared_models_for_role=compared_models_for_role,
        notes=[
            "FAKE PASS != MODEL PASS" if fake else "real_model_run",
            f"acceptance_hard_ok={acceptance_hard_ok}",
        ],
        fake_provider=fake,
    )

    if write_report:
        REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
        path = REPORTS_ROOT / f"bench_{result.run_id}.json"
        path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        latest = REPORTS_ROOT / "bench_latest.json"
        latest.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return result

