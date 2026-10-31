from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from content.hook_variants import HookVariantSet
from content.script_generator import ShortScript


@dataclass(frozen=True, slots=True)
class ShortAssetScene:
    scene_id: str
    start_sec: float
    duration_sec: float
    audio: str
    background: str
    overlay_text: str
    visual_prompt: str
    caption: str
    sfx: str
    risk_notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ShortAssetPlan:
    topic: str
    selected_hook_id: str
    scenes: tuple[ShortAssetScene, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "selected_hook_id": self.selected_hook_id,
            "scenes": [scene.to_dict() for scene in self.scenes],
        }


def build_short_asset_plan(
    *,
    script: ShortScript,
    topic: str,
    hook_variants: HookVariantSet,
) -> ShortAssetPlan:
    segments = [_clean(script.hook), *[_clean(item) for item in script.body if _clean(item)], _clean(script.cta)]
    if not segments:
        segments = [_clean(script.title) or topic]
    selected_hook = next((hook for hook in hook_variants.hooks if hook.selected), hook_variants.hooks[0])
    scenes: list[ShortAssetScene] = []
    start = 0.0
    for index, segment in enumerate(segments[:6], start=1):
        duration = 3.0 if index == 1 else 5.0
        overlay = selected_hook.visual_text if index == 1 else _overlay(segment)
        scene = ShortAssetScene(
            scene_id=f"scene_{index:02d}",
            start_sec=round(start, 2),
            duration_sec=duration,
            audio=segment,
            background=_background(index),
            overlay_text=overlay,
            visual_prompt=(
                f"Vertical 9:16 editorial AI short frame about {topic}. "
                f"Scene message: {segment}. High contrast, clean typography, no clutter."
            ),
            caption=segment,
            sfx="soft riser" if index == 1 else "subtle whoosh",
            risk_notes="Avoid unverifiable claims and misleading before/after visuals.",
        )
        scenes.append(scene)
        start += duration
    return ShortAssetPlan(topic=topic, selected_hook_id=hook_variants.selected_hook_id, scenes=tuple(scenes))


def render_visual_direction_markdown(*, topic: str, hook_variants: HookVariantSet, asset_plan: ShortAssetPlan) -> str:
    selected = next((hook for hook in hook_variants.hooks if hook.selected), hook_variants.hooks[0])
    return "\n".join(
        [
            "# Visual Direction",
            "",
            f"- topic: {topic}",
            f"- selected_hook: {selected.id} - {selected.text}",
            "- format: YouTube Shorts 1080x1920, safe-zone captions, mobile-first contrast",
            "- typography: bold condensed headline, simple secondary caption, no tiny body text",
            "- palette: dark editorial base, one warm accent, one cool accent, avoid random neon clutter",
            "- composition: one clear idea per scene, central focal point, top hook text, lower readable captions",
            "- motion: hard cuts on beat, subtle zooms, progress rhythm every 3-5 seconds",
            "- do: use specific visuals tied to the script beat",
            "- don't: use generic AI blobs, illegible text, fake UI claims, or overpacked frames",
            f"- scene_count: {len(asset_plan.scenes)}",
        ]
    ) + "\n"


def render_pre_render_quality_report(
    *,
    hook_variants: HookVariantSet,
    asset_plan: ShortAssetPlan,
    script_score: float,
    blockers: tuple[str, ...] = (),
) -> str:
    selected = next((hook for hook in hook_variants.hooks if hook.selected), hook_variants.hooks[0])
    lines = [
        "# Pre-render Quality Report",
        "",
        f"- selected_hook_id: {selected.id}",
        f"- selected_hook_score: {selected.score}",
        f"- script_score: {script_score}",
        f"- asset_plan_scenes: {len(asset_plan.scenes)}",
        f"- approval_readiness: {'blocked' if blockers else 'ready_for_local_render'}",
        "",
        "## Selected hook",
        selected.text,
        "",
        "## Blockers",
    ]
    lines.extend(f"- {item}" for item in blockers) if blockers else lines.append("- none")
    lines.extend(["", "## Asset plan validity", "- hooks: exactly 5 generated", "- scenes: ordered by start_sec"])
    return "\n".join(lines).strip() + "\n"


def _clean(value: str) -> str:
    return " ".join(str(value or "").replace("`", "").split()).strip()


def _overlay(value: str) -> str:
    words = _clean(value).upper().split()
    return " ".join(words[:3]) if words else "SCENA"


def _background(index: int) -> str:
    options = (
        "dark editorial gradient with single glowing focal shape",
        "clean desk/workflow visual metaphor",
        "abstract system map with highlighted path",
        "before/after split screen without fake numbers",
        "minimal checklist board with one active item",
        "closing frame with clear next action",
    )
    return options[(index - 1) % len(options)]
