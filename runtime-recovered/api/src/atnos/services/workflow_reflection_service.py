from __future__ import annotations

from dataclasses import dataclass

from atnos.core.config import Settings
from atnos.services.hardware_guard_service import HardwareGuardService


@dataclass(frozen=True, slots=True)
class WorkflowReflectionPlan:
    stage_key: str
    error_message: str
    reason: str
    retry_allowed: bool
    retry_label: str
    adjustments: dict[str, object]
    gpu_release: dict[str, object] | None
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_key": self.stage_key,
            "error_message": self.error_message,
            "reason": self.reason,
            "retry_allowed": self.retry_allowed,
            "retry_label": self.retry_label,
            "adjustments": dict(self.adjustments),
            "gpu_release": dict(self.gpu_release) if self.gpu_release is not None else None,
            "notes": list(self.notes),
        }


class WorkflowReflectionService:
    WATCHDOG_MARKERS = ("watchdog", "timed out", "timeout", "stuck render")
    VRAM_MARKERS = ("vram", "out of memory", "memory", "lowvram", "low vram")
    CODEC_MARKERS = ("codec", "ffmpeg", "h264_amf", "amf", "aac", "encoder")
    COMFYUI_MARKERS = ("comfyui", "prompt queue failed", "generation timed out", "checkpoint")

    def __init__(self, settings: Settings, hardware_guard: HardwareGuardService) -> None:
        self.settings = settings
        self.hardware_guard = hardware_guard

    def reflect_render_failure(
        self,
        *,
        stage_key: str,
        error_message: str,
        attempt: int,
        current_render_profile: str | None = None,
        cutaway_count: int | None = None,
    ) -> WorkflowReflectionPlan | None:
        if attempt >= 3:
            return None

        normalized = (error_message or "").strip().lower()
        if not normalized:
            return None
        if not any(marker in normalized for marker in (*self.WATCHDOG_MARKERS, *self.VRAM_MARKERS, *self.CODEC_MARKERS)):
            return None

        needs_gpu_release = any(marker in normalized for marker in (*self.WATCHDOG_MARKERS, *self.VRAM_MARKERS))
        gpu_release = self.hardware_guard.release_gpu_pressure() if needs_gpu_release else None
        current_profile = str(current_render_profile or "balanced").strip().lower()
        next_profile = "fast_recovery"
        if current_profile == "quality":
            next_profile = "balanced"
        elif current_profile == "fast_recovery":
            return None

        adjustments: dict[str, object] = {"render_profile": next_profile}
        notes: list[str] = []
        if gpu_release is not None:
            notes.append(str(gpu_release.get("message") or "GPU pressure release was attempted."))
        if current_profile == "quality" and next_profile == "balanced":
            notes.append("Reflector downgraded the next retry from quality to balanced render profile.")
        else:
            notes.append("Reflector switched the next retry to a lighter render profile.")

        if stage_key == "synthetic_motion_reel":
            if cutaway_count is not None and cutaway_count > 1:
                adjustments["max_cutaways"] = max(1, cutaway_count - 1)
                notes.append("Reflector reduced the cutaway count for the next retry.")
            adjustments["cutaway_display_duration_sec"] = 0.82
            adjustments["target_duration_cap_sec"] = 18.0
            notes.append("Reflector tightened pacing and duration for the next synthetic reel retry.")

        reason = "Watchdog, codec or VRAM pressure signaled that a lighter retry path is safer."
        return WorkflowReflectionPlan(
            stage_key=stage_key,
            error_message=error_message,
            reason=reason,
            retry_allowed=True,
            retry_label="lighter_retry",
            adjustments=adjustments,
            gpu_release=gpu_release,
            notes=tuple(notes),
        )

    def reflect_image_generation_failure(
        self,
        *,
        stage_key: str,
        error_message: str,
        attempt: int,
    ) -> WorkflowReflectionPlan | None:
        if attempt >= 2:
            return None

        normalized = (error_message or "").strip().lower()
        if not normalized:
            return None
        if not any(marker in normalized for marker in (*self.VRAM_MARKERS, *self.COMFYUI_MARKERS, *self.WATCHDOG_MARKERS)):
            return None

        gpu_release = self.hardware_guard.release_gpu_pressure()
        reduced_steps = max(8, int(self.settings.comfyui_steps) - 6)
        reduced_cfg = max(4.5, float(self.settings.comfyui_cfg_scale) - 1.0)
        notes = [
            str(gpu_release.get("message") or "GPU pressure release was attempted."),
            f"Reflector reduced ComfyUI steps to {reduced_steps} and cfg scale to {reduced_cfg:.1f}.",
        ]
        return WorkflowReflectionPlan(
            stage_key=stage_key,
            error_message=error_message,
            reason="ComfyUI failure matched the VRAM/timeout recovery path.",
            retry_allowed=True,
            retry_label="reduced_comfyui_load",
            adjustments={
                "comfyui_steps": reduced_steps,
                "comfyui_cfg_scale": reduced_cfg,
            },
            gpu_release=gpu_release,
            notes=tuple(notes),
        )
