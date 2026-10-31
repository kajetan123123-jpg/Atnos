from __future__ import annotations

import json

import pytest

from atnos.local_brain.benchmark.fake_providers import (
    approval_bypass_observation,
    assert_is_local_model_provider,
    build_perfect_provider,
    build_provider_with_overrides,
    crash_observation,
    failed_tool_no_recovery_observation,
    invented_success_observation,
    malformed_json_observation,
    misleading_trust_observation,
    permission_ignored_observation,
    requires_setup_as_success_observation,
    timeout_observation,
    unknown_invented_observation,
)
from atnos.local_brain.benchmark.harness import run_benchmark
from atnos.local_brain.benchmark.result_schema import BenchVerdict
from atnos.local_brain.benchmark.scenarios import SCENARIOS
from atnos.local_brain.provider_contract import LocalModelProvider
from atnos.local_brain.storage import REPORTS_ROOT, gate_download, probe_disk

_HW = {
    "gpu_name": "AMD Radeon RX 7600 XT",
    "vram_total_bytes": 16 * 1024**3,
    "runtimes": {"ollama": {"status": "fixture"}, "llama_cpp": {"status": "fixture"}},
}


def test_ten_scenarios_defined() -> None:
    assert len(SCENARIOS) == 10
    assert [s.name for s in SCENARIOS] == [
        "capability_selection",
        "requires_setup",
        "structured_tool_call",
        "failed_tool_recovery",
        "unknown_evidence",
        "multistep_agent",
        "approval_gate",
        "long_context",
        "persistence_recovery",
        "misleading_tool_result",
    ]


def test_perfect_fake_provider_satisfies_protocol_and_passes_harness() -> None:
    provider = build_perfect_provider()
    assert_is_local_model_provider(provider)
    assert isinstance(provider, LocalModelProvider)
    result = run_benchmark(
        provider,
        model="fake-perfect",
        source="fixture",
        hardware=_HW,
        write_report=True,
    )
    assert result.fake_provider is True
    assert result.hard_failures == []
    assert result.metrics["wrong_success_count"] == 0
    assert result.metrics["approval_violation_count"] == 0
    assert result.quality_score >= 90
    assert result.verdict is BenchVerdict.EXPERIMENTAL  # fake never ROLE_WINNER
    assert (REPORTS_ROOT / "bench_latest.json").is_file()
    payload = json.loads((REPORTS_ROOT / "bench_latest.json").read_text(encoding="utf-8"))
    assert payload["metrics"]["fake_pass_is_not_model_pass"] is True


@pytest.mark.parametrize(
    "scenario_name,script,expected_code",
    [
        ("requires_setup", requires_setup_as_success_observation, "invented_success"),
        ("approval_gate", approval_bypass_observation, "approval_bypass"),
        ("structured_tool_call", malformed_json_observation, "malformed_tool_unsafe"),
        ("unknown_evidence", unknown_invented_observation, "unknown_invented"),
        ("misleading_tool_result", misleading_trust_observation, "published_without_evidence"),
        ("approval_gate", permission_ignored_observation, "permission_denial_ignored"),
        ("requires_setup", invented_success_observation, "invented_success"),
        ("failed_tool_recovery", failed_tool_no_recovery_observation, "invented_success"),
    ],
)
def test_hard_fails_are_detected(scenario_name: str, script, expected_code: str) -> None:
    provider = build_provider_with_overrides(
        {scenario_name: script},
        provider_id=f"fake-{expected_code}",
    )
    result = run_benchmark(
        provider,
        model="fake-bad",
        source="fixture",
        hardware=_HW,
        write_report=False,
    )
    codes = {item["code"] for item in result.hard_failures}
    assert expected_code in codes
    assert result.verdict is BenchVerdict.REJECT
    assert result.quality_score < 100 or result.hard_failures


def test_timeout_and_crash_fail_acceptance() -> None:
    timeout_provider = build_provider_with_overrides(
        {"capability_selection": timeout_observation},
        provider_id="fake-timeout",
    )
    crash_provider = build_provider_with_overrides(
        {"capability_selection": crash_observation},
        provider_id="fake-crash",
    )
    t = run_benchmark(timeout_provider, hardware=_HW, write_report=False)
    c = run_benchmark(crash_provider, hardware=_HW, write_report=False)
    # Timeout alone may not be a hard-fail code, but crash must reject.
    assert c.verdict is BenchVerdict.REJECT
    assert any(item.get("crashed") for item in c.scenario_results)
    assert t.metrics["tool_call_accuracy"] < 1.0 or t.verdict in {BenchVerdict.REJECT, BenchVerdict.EXPERIMENTAL}


def test_download_gate_and_disk_probe() -> None:
    disk = probe_disk()
    assert disk.free_bytes > 0
    ok = gate_download(required_size_bytes=1_000_000, family="qwen")
    assert ok.allowed is True
    huge = gate_download(required_size_bytes=disk.free_bytes, family="qwen")
    assert huge.allowed is False


def test_role_winner_requires_two_real_models() -> None:
    from atnos.local_brain.benchmark.result_schema import decide_verdict

    assert (
        decide_verdict(
            hard_failure_count=0,
            quality_score=95,
            crashed=False,
            fake_provider=False,
            compared_models_for_role=1,
            acceptance_hard_ok=True,
        )
        is BenchVerdict.ROLE_CANDIDATE
    )
    assert (
        decide_verdict(
            hard_failure_count=0,
            quality_score=95,
            crashed=False,
            fake_provider=False,
            compared_models_for_role=2,
            acceptance_hard_ok=True,
        )
        is BenchVerdict.ROLE_WINNER
    )
