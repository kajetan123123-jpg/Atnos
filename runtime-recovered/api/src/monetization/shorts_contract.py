from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


REQUIRED_SHORT_ARTIFACTS = (
    "hook_variants.json",
    "short_asset_plan.json",
    "visual_direction.md",
    "quality_report.md",
)

HOOK_FIELDS = (
    "id",
    "text",
    "visual_text",
    "psychographic_target",
    "score",
    "reason",
    "selected",
)

SCENE_FIELDS = (
    "scene_id",
    "start_sec",
    "duration_sec",
    "audio",
    "background",
    "overlay_text",
    "visual_prompt",
    "caption",
    "sfx",
    "risk_notes",
)


@dataclass(frozen=True, slots=True)
class ShortPreRenderContractResult:
    valid: bool
    issues: tuple[str, ...] = ()
    artifacts: dict[str, str] = field(default_factory=dict)
    selected_hook_id: str | None = None
    scene_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": list(self.issues),
            "artifacts": dict(self.artifacts),
            "selected_hook_id": self.selected_hook_id,
            "scene_count": self.scene_count,
        }


def validate_short_pre_render_contract(output_dir: str | Path) -> ShortPreRenderContractResult:
    root = Path(output_dir)
    issues: list[str] = []
    artifacts = {name: str((root / name).resolve()) for name in REQUIRED_SHORT_ARTIFACTS}

    for name in REQUIRED_SHORT_ARTIFACTS:
        path = root / name
        if not path.exists():
            issues.append(f"{name} is missing")
        elif path.stat().st_size <= 0:
            issues.append(f"{name} is empty")

    selected_hook_id: str | None = None
    if (root / "hook_variants.json").exists():
        hook_payload = _load_json(root / "hook_variants.json", issues)
        hooks = hook_payload.get("hooks") if isinstance(hook_payload, dict) else None
        if not isinstance(hooks, list):
            issues.append("hook_variants.json must contain hooks list")
        else:
            if len(hooks) != 5:
                issues.append("hook_variants.json must contain exactly 5 hooks")
            selected = []
            for index, hook in enumerate(hooks, start=1):
                if not isinstance(hook, dict):
                    issues.append(f"hook {index} must be an object")
                    continue
                for field_name in HOOK_FIELDS:
                    if field_name not in hook:
                        issues.append(f"hook {index} missing {field_name}")
                if hook.get("selected") is True:
                    selected.append(str(hook.get("id") or ""))
                try:
                    score = float(hook.get("score"))
                except (TypeError, ValueError):
                    issues.append(f"hook {index} score must be numeric")
                else:
                    if not 0 <= score <= 10:
                        issues.append(f"hook {index} score must be between 0 and 10")
            if len(selected) != 1:
                issues.append("hook_variants.json must contain exactly one selected hook")
            else:
                selected_hook_id = selected[0]

    scene_count = 0
    if (root / "short_asset_plan.json").exists():
        plan_payload = _load_json(root / "short_asset_plan.json", issues)
        scenes = plan_payload.get("scenes") if isinstance(plan_payload, dict) else None
        if not isinstance(scenes, list) or not scenes:
            issues.append("short_asset_plan.json must contain non-empty scenes list")
        else:
            scene_count = len(scenes)
            last_start = -1.0
            for index, scene in enumerate(scenes, start=1):
                if not isinstance(scene, dict):
                    issues.append(f"scene {index} must be an object")
                    continue
                for field_name in SCENE_FIELDS:
                    if field_name not in scene:
                        issues.append(f"scene {index} missing {field_name}")
                start_sec = _float_or_none(scene.get("start_sec"))
                duration_sec = _float_or_none(scene.get("duration_sec"))
                if start_sec is None or start_sec < 0:
                    issues.append(f"scene {index} start_sec must be >= 0")
                elif start_sec < last_start:
                    issues.append(f"scene {index} start_sec must be ordered")
                else:
                    last_start = start_sec
                if duration_sec is None or duration_sec <= 0:
                    issues.append(f"scene {index} duration_sec must be > 0")

    quality_report_path = root / "quality_report.md"
    if quality_report_path.exists():
        text = quality_report_path.read_text(encoding="utf-8", errors="ignore").casefold()
        if "pre-render" not in text and "prerender" not in text:
            issues.append("quality_report.md must describe pre-render QA")

    return ShortPreRenderContractResult(
        valid=not issues,
        issues=tuple(issues),
        artifacts=artifacts,
        selected_hook_id=selected_hook_id,
        scene_count=scene_count,
    )


def _load_json(path: Path, issues: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(f"{path.name} is invalid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        issues.append(f"{path.name} must be a JSON object")
        return {}
    return payload


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
