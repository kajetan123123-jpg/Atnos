"""Benchmark shortlist metadata — no downloads.

Order is intentional for RX 7600 XT 16 GB. Models are candidates, not winners.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ShortlistEntry:
    order: int
    family: str
    model_id: str
    display_name: str
    role_targets: tuple[str, ...]
    purpose: str
    preferred_quant: str
    estimated_disk_bytes: int | None
    estimated_vram_bytes: int | None
    notes: str
    official_source_hint: str
    download_authorized: bool = False


# Estimated sizes are planning hints only (UNKNOWN if unsure — kept None or conservative).
INITIAL_SHORTLIST: tuple[ShortlistEntry, ...] = (
    ShortlistEntry(
        order=1,
        family="qwen",
        model_id="Qwen3.5-9B",
        display_name="Qwen3.5-9B",
        role_targets=("CHAT", "OPERATOR"),
        purpose="Baseline speed/quality/tool behaviour for Local Brain v1.",
        preferred_quant="Q4_K_M",
        estimated_disk_bytes=6 * 1024**3,
        estimated_vram_bytes=8 * 1024**3,
        notes="First download candidate after READY_TO_BENCHMARK=YES and explicit user approval.",
        official_source_hint="https://huggingface.co/Qwen/Qwen3.5-9B",
    ),
    ShortlistEntry(
        order=2,
        family="qwen",
        model_id="Qwen3-Coder-30B-A3B-Instruct",
        display_name="Qwen3-Coder-30B-A3B-Instruct",
        role_targets=("OPERATOR", "CODING"),
        purpose="Operator/coding challenger (MoE; total weights matter, not only active params).",
        preferred_quant="Q4_K_M",
        estimated_disk_bytes=18 * 1024**3,
        estimated_vram_bytes=16 * 1024**3,
        notes="May require partial CPU offload on 16 GB — measure before calling real-time capable.",
        official_source_hint="https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct",
    ),
    ShortlistEntry(
        order=3,
        family="qwen",
        model_id="Qwen3.6-27B",
        display_name="Qwen3.6-27B",
        role_targets=("OPERATOR", "CHAT"),
        purpose="High-quality general/operator challenger; expect heavy quant/offload on 16 GB.",
        preferred_quant="Q4_K_M",
        estimated_disk_bytes=16 * 1024**3,
        estimated_vram_bytes=16 * 1024**3,
        notes="Not a light default; include GPU_OFFLOAD/CPU_OFFLOAD in results if used.",
        official_source_hint="https://huggingface.co/Qwen/Qwen3.6-27B",
    ),
    ShortlistEntry(
        order=4,
        family="deepseek",
        model_id="DeepSeek-R1-Distill-Qwen-14B",
        display_name="DeepSeek-R1-Distill-Qwen-14B",
        role_targets=("OPERATOR",),
        purpose="Reasoning/control comparison point (not first tool-operator pick).",
        preferred_quant="Q4_K_M",
        estimated_disk_bytes=9 * 1024**3,
        estimated_vram_bytes=10 * 1024**3,
        notes="Distill on Qwen2.5 lineage — useful control, not assumed winner for tools.",
        official_source_hint="https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
    ),
)


QUANTIZATION_POLICY = {
    "preferred": "Q4_K_M",
    "acceptable": ("Q4_K_M", "Q5_K_M", "Q5_0", "Q6_K"),
    "avoid_as_default": ("IQ1", "IQ2", "Q2_K", "Q3_K_S"),
    "rule": (
        "Do not pick extremely low quant only to force a fit. "
        "If model does not fit VRAM, report GPU_OFFLOAD/CPU_OFFLOAD and measured impact; "
        "do not label real-time capable without measurement."
    ),
}


def shortlist_as_dicts() -> list[dict[str, Any]]:
    return [asdict(item) for item in INITIAL_SHORTLIST]


def first_recommended_model() -> ShortlistEntry:
    return INITIAL_SHORTLIST[0]
