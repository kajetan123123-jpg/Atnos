from __future__ import annotations

from atnos.local_brain.hardware_probe import collect_hardware_probe, write_hardware_probe
from atnos.local_brain.readiness import local_brain_readiness_report
from atnos.local_brain.storage import REPORTS_ROOT, ensure_storage_layout


def test_hardware_probe_collects_gpu_and_disk() -> None:
    ensure_storage_layout()
    probe = collect_hardware_probe()
    assert probe.gpu_name
    assert "Radeon" in probe.gpu_name or "AMD" in probe.gpu_name
    assert probe.vram_total_bytes and probe.vram_total_bytes >= 8 * 1024**3
    assert probe.disk["free_bytes"] > 0
    assert "ollama" in probe.runtimes
    assert "llama_cpp" in probe.runtimes
    path = write_hardware_probe()
    assert path.is_file()
    assert path.parent == REPORTS_ROOT


def test_readiness_report_ready_with_fixture_bench() -> None:
    report = local_brain_readiness_report(run_fixture_bench=True)
    assert report.CANONICAL_ROOT == r"E:\Atnos\App"
    assert report.SAFE_TO_CONTINUE is True
    assert report.ROLE_CONTRACTS["contracts_present"] is True
    assert report.HARNESS["scenario_count"] == 10
    assert report.TEST_RESULTS["hard_failures"] == 0
    assert report.FIRST_MODEL_RECOMMENDED["model_id"] == "Qwen3.5-9B"
    assert report.READY_TO_BENCHMARK is True
    assert report.missing == []
