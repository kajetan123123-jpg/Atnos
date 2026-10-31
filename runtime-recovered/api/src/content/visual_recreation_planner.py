from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class VisualScenePrompt:
    scene_id: str
    timing: str
    goal: str
    image_prompt: str
    motion_prompt: str
    overlay_text: str
    camera: str
    negative_prompt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VisualRecreationPlan:
    plan_id: str
    brief_id: str
    source_pattern: str
    source_title: str
    source_channel: str
    source_url: str
    source_video_id: str
    source_thumbnail_path: str | None
    reference_visual_summary: str
    safe_similarity_target: str
    character_sheet_prompt: str
    scene_prompts: tuple[VisualScenePrompt, ...]
    thumbnail_prompt: str
    caption_style: str
    color_palette: tuple[str, ...]
    camera_style: str
    repair_checklist: tuple[str, ...]
    forbidden_copying: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scene_prompts"] = [scene.to_dict() for scene in self.scene_prompts]
        return payload


def build_visual_recreation_plans(
    *,
    briefs: Iterable[dict[str, Any]],
    observations: Iterable[dict[str, Any]],
    frame_observations: Iterable[dict[str, Any]] = (),
) -> list[VisualRecreationPlan]:
    observation_by_video_id = {
        str(item.get("video_id") or ""): item
        for item in observations
        if isinstance(item, dict) and str(item.get("video_id") or "")
    }
    frame_observation_by_video_id = {
        str(item.get("source_ref") or ""): item
        for item in frame_observations
        if isinstance(item, dict) and str(item.get("source_ref") or "")
    }
    plans: list[VisualRecreationPlan] = []
    for index, brief in enumerate((item for item in briefs if isinstance(item, dict)), start=1):
        video_id = str(brief.get("source_video_id") or "").strip()
        observation = observation_by_video_id.get(video_id, {})
        frame_observation = frame_observation_by_video_id.get(video_id, {})
        plans.append(
            build_visual_recreation_plan(
                brief=brief,
                observation=observation,
                frame_observation=frame_observation,
                index=index,
            )
        )
    return plans


def build_visual_recreation_plan(
    *,
    brief: dict[str, Any],
    observation: dict[str, Any] | None = None,
    frame_observation: dict[str, Any] | None = None,
    index: int = 1,
) -> VisualRecreationPlan:
    observation = observation or {}
    frame_observation = frame_observation or {}
    brief_id = str(brief.get("brief_id") or f"brief-{index:02d}")
    pattern = str(brief.get("source_pattern") or "general_ai_faceless")
    title = str(brief.get("source_title") or "")
    channel = str(brief.get("source_channel") or "")
    source_url = str(brief.get("source_url") or "")
    video_id = str(brief.get("source_video_id") or observation.get("video_id") or "")
    thumbnail_path = str(observation.get("thumbnail_path") or "").strip() or None
    contrast = _safe_float(observation.get("contrast_score"))
    saturation = _safe_float(observation.get("saturation_score"))
    hook = str(brief.get("hook_template") or "").strip() or "Ten kadr zatrzymuje scroll."
    visual_notes = tuple(str(item) for item in observation.get("visual_notes") or () if str(item).strip())

    if pattern == "ai_animal_microstory":
        reference_summary = _animal_reference_summary(
            title=title,
            contrast=contrast,
            saturation=saturation,
            visual_notes=visual_notes,
        ) + _frame_reference_suffix(frame_observation)
        character_prompt = (
            "Original anthropomorphic cat protagonist, not copied from the source, cinematic 3D animation style, "
            "large expressive eyes, readable silhouette, soft fur detail, dramatic outfit progression from poor street look "
            "to confident designer/agent look, vertical 9:16 composition, warm city lights, high contrast, no watermark, no text."
        )
        scene_prompts = _animal_scene_prompts(hook)
        thumbnail_prompt = (
            "Vertical Shorts thumbnail, original anthropomorphic cat in dramatic before/after fashion transformation, "
            "one poor street outfit on the left, confident designer/agent outfit on the right, bright rim light, saturated warm colors, "
            "clear emotional face, no copied source character, no logo, no watermark, optional 2-3 word Polish headline."
        )
        palette = ("deep teal", "warm orange", "gold highlight", "soft cream", "charcoal")
        camera_style = "cinematic close-ups, fast push-ins, 2-3 second shot changes, clear before/after contrast"
    elif pattern == "motivational_faceless":
        reference_summary = (
            "Faceless motivational format: high contrast typography, simple symbolic visuals, one strong thesis, "
            "no fake wealth promises, fast caption rhythm."
        ) + _frame_reference_suffix(frame_observation)
        character_prompt = (
            "No human host, abstract faceless workspace, dark premium background, bold kinetic typography, "
            "one practical symbol per scene, clean modern composition, no luxury bait, no platform logos."
        )
        scene_prompts = _motivational_scene_prompts(hook)
        thumbnail_prompt = (
            "Vertical thumbnail, black background, one bold contrast symbol, oversized Polish headline, high readability on phone, "
            "clean premium faceless style, no fake money screenshots, no copyrighted logos."
        )
        palette = ("near black", "white", "electric green", "warning red", "steel gray")
        camera_style = "static bold cards with kinetic text cuts and short zoom pulses"
    else:
        reference_summary = (
            "Faceless explainer format: before/after process, visible artifacts, clean proof on screen, strong caption pacing."
        ) + _frame_reference_suffix(frame_observation)
        character_prompt = (
            "Faceless AI workflow workspace, clean dashboard cards, folders, QA checklist, render timeline, "
            "modern high-contrast UI visuals, no copied app branding, no platform logos."
        )
        scene_prompts = _workflow_scene_prompts(hook)
        thumbnail_prompt = (
            "Vertical thumbnail with before/after workflow: chaos folder on left, clean QA-approved package on right, "
            "large readable headline, high contrast, original UI shapes, no copied interface."
        )
        palette = ("midnight blue", "cyan", "amber", "white", "slate")
        camera_style = "screen-like macro close-ups, quick zooms, timeline cards sliding into place"

    return VisualRecreationPlan(
        plan_id=f"visual-plan-{index:02d}-{_safe_slug(pattern)}",
        brief_id=brief_id,
        source_pattern=pattern,
        source_title=title,
        source_channel=channel,
        source_url=source_url,
        source_video_id=video_id,
        source_thumbnail_path=thumbnail_path,
        reference_visual_summary=reference_summary,
        safe_similarity_target=(
            "Match format mechanics, scene rhythm, color energy, subject readability and emotional arc. "
            "Do not match exact character identity, exact outfit, exact composition, script, audio, watermark or channel branding."
        ),
        character_sheet_prompt=character_prompt,
        scene_prompts=scene_prompts,
        thumbnail_prompt=thumbnail_prompt,
        caption_style=(
            "Polish captions, max 4 words per cue, large bold white letters, dark outline, bottom safe zone, "
            "one caption layer only, no title spam across every frame."
        ),
        color_palette=palette,
        camera_style=camera_style,
        repair_checklist=(
            "First frame must communicate the subject in under 1 second.",
            "Main character or central symbol must be larger than background details.",
            "Every scene must change the story state, not just restate the same idea.",
            "If the frame looks generic/vector-only, regenerate with more cinematic detail and stronger character focus.",
            "If captions cover the character face or key object, move captions to safe zone and shorten the cue.",
            "If output resembles the source too closely, change character design, outfit, environment and composition while keeping beat structure.",
        ),
        forbidden_copying=tuple(str(item) for item in brief.get("forbidden_copying") or ()),
    )


def render_visual_recreation_plan_markdown(plan: VisualRecreationPlan) -> str:
    lines = [
        f"# {plan.plan_id}",
        "",
        f"- brief_id: `{plan.brief_id}`",
        f"- source_pattern: `{plan.source_pattern}`",
        f"- source_channel: {plan.source_channel}",
        f"- source_url: {plan.source_url}",
        f"- source_thumbnail_path: `{plan.source_thumbnail_path}`",
        "",
        "## Reference Visual Summary",
        "",
        plan.reference_visual_summary,
        "",
        "## Safe Similarity Target",
        "",
        plan.safe_similarity_target,
        "",
        "## Character Sheet Prompt",
        "",
        plan.character_sheet_prompt,
        "",
        "## Scene Prompts",
        "",
    ]
    for scene in plan.scene_prompts:
        lines.extend(
            [
                f"### {scene.scene_id} / {scene.timing}",
                f"- goal: {scene.goal}",
                f"- image_prompt: {scene.image_prompt}",
                f"- motion_prompt: {scene.motion_prompt}",
                f"- overlay_text: {scene.overlay_text}",
                f"- camera: {scene.camera}",
                f"- negative_prompt: {scene.negative_prompt}",
                "",
            ]
        )
    lines.extend(
        [
            "## Thumbnail Prompt",
            "",
            plan.thumbnail_prompt,
            "",
            "## Caption Style",
            "",
            plan.caption_style,
            "",
            "## Color Palette",
            "",
            *[f"- {item}" for item in plan.color_palette],
            "",
            "## Repair Checklist",
            "",
            *[f"- {item}" for item in plan.repair_checklist],
            "",
            "## Forbidden Copying",
            "",
            *[f"- {item}" for item in plan.forbidden_copying],
            "",
        ]
    )
    return "\n".join(lines)


def _animal_reference_summary(
    *,
    title: str,
    contrast: float | None,
    saturation: float | None,
    visual_notes: tuple[str, ...],
) -> str:
    title_lower = title.casefold()
    subject = "cat transformation microstory"
    if "fashion" in title_lower or "designer" in title_lower:
        subject = "cat fashion-designer transformation microstory"
    if "poor" in title_lower:
        subject = f"poor-to-confident {subject}"
    scores = []
    if contrast is not None:
        scores.append(f"contrast={contrast:.3f}")
    if saturation is not None:
        scores.append(f"saturation={saturation:.3f}")
    score_text = ", ".join(scores) if scores else "thumbnail metrics unavailable"
    notes = " ".join(visual_notes[:2]) if visual_notes else "No visual notes available."
    return (
        f"Observed format: {subject}. Visual signal: cute/emotional anthropomorphic cat, strong before/after contrast, "
        f"bright cinematic color, instantly readable character pose. Thumbnail metrics: {score_text}. {notes}"
    )


def _frame_reference_suffix(frame_observation: dict[str, Any]) -> str:
    if not frame_observation:
        return ""
    summary = str(frame_observation.get("safe_learning_summary") or "").strip()
    pacing = str(frame_observation.get("motion_pacing_hypothesis") or "").strip()
    notes = frame_observation.get("aggregate_notes")
    note_text = ""
    if isinstance(notes, list):
        note_text = " ".join(str(item) for item in notes[:2] if str(item).strip())
    parts = [part for part in (summary, pacing, note_text) if part]
    if not parts:
        return ""
    return " Frame observation: " + " ".join(parts)


def _animal_scene_prompts(hook: str) -> tuple[VisualScenePrompt, ...]:
    negative = "copied source character, same outfit as source, watermark, logo, blurry face, extra limbs, unreadable text"
    return (
        VisualScenePrompt(
            scene_id="scene_01_hook",
            timing="0-2s",
            goal="Stop scroll with emotional animal protagonist and clear problem.",
            image_prompt=(
                "vertical 9:16 cinematic 3D, original small black cat agent standing in a rainy neon alley beside a failed short storyboard, "
                "large expressive eyes, orange rim light, dramatic readable silhouette, high contrast, no copied character"
            ),
            motion_prompt="fast push-in on the cat's face, subtle rain particles, storyboard red warning blinking",
            overlay_text=hook,
            camera="low-angle close-up, subject fills lower-right third",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_02_problem",
            timing="2-8s",
            goal="Show the failed version visibly, not as narration.",
            image_prompt=(
                "vertical frame, original cat points at a broken faceless short timeline, messy captions, dull frame, warning icons, "
                "dark teal control room, cinematic lighting, clear before state"
            ),
            motion_prompt="timeline cards shake once, red QA marks appear one by one",
            overlay_text="BEZ PLANU",
            camera="medium shot, cat plus failed timeline side by side",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_03_escalation",
            timing="8-20s",
            goal="Escalate with two visible stakes: weak hook and boring frame.",
            image_prompt=(
                "vertical cinematic 3D, split-screen: left weak faceless short with tiny captions, right cat agent holding a bright hook card, "
                "warm orange highlights, strong contrast, original character design"
            ),
            motion_prompt="split-screen slides in, weak side dims, hook card pops forward",
            overlay_text="HOOK ALBO SCROLL",
            camera="quick punch-in from split screen to hook card",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_04_payoff",
            timing="20-35s",
            goal="Show the QA transformation and clean final package.",
            image_prompt=(
                "vertical frame, cat agent activates a glowing QA checklist, storyboard turns from red messy cards into clean golden approved scenes, "
                "cinematic 3D, polished faceless content studio, high readability"
            ),
            motion_prompt="red cards flip into green approved cards, progress bar fills",
            overlay_text="PLAN + QA",
            camera="over-shoulder shot from cat to glowing checklist",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_05_loop",
            timing="35-45s",
            goal="Close with a loop that invites the next format test.",
            image_prompt=(
                "vertical cinematic close-up, original cat agent holds a finished private upload package, behind it three new blank story cards, "
                "warm glow, confident expression, no platform logos"
            ),
            motion_prompt="camera pulls back to reveal next blank story card, subtle loop back to first failed card",
            overlay_text="NASTĘPNY FORMAT?",
            camera="close-up to pullback, centered subject",
            negative_prompt=negative,
        ),
    )


def _motivational_scene_prompts(hook: str) -> tuple[VisualScenePrompt, ...]:
    negative = "fake money, luxury car, copyrighted logo, unreadable text, clutter, human influencer face"
    return (
        VisualScenePrompt(
            scene_id="scene_01_hook",
            timing="0-2s",
            goal="Deliver one sharp thesis.",
            image_prompt="vertical black premium background, giant white Polish headline, one red broken automation icon, minimal faceless style",
            motion_prompt="headline slams in, red icon cracks",
            overlay_text=hook,
            camera="static centered card with micro zoom",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_02_consequence",
            timing="2-12s",
            goal="Show cost of low-quality automation.",
            image_prompt="vertical symbolic scene, conveyor belt producing weak short cards, red reject stamps, dark studio, high contrast",
            motion_prompt="cards move left to right, reject stamps land on beat",
            overlay_text="AUTOMAT ≠ JAKOŚĆ",
            camera="top-down to close-up transition",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_03_rule",
            timing="12-28s",
            goal="Give a practical rule instead of generic motivation.",
            image_prompt="vertical clean checklist card, Plan, Hook, Visual, QA, Review, electric green approved ticks on black background",
            motion_prompt="ticks appear one by one with punchy cuts",
            overlay_text="NAJPIERW QA",
            camera="macro close-up on checklist",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_04_close",
            timing="28-40s",
            goal="End with clear next action.",
            image_prompt="vertical final card, private upload package locked behind approval shield, clean faceless dashboard, premium dark UI",
            motion_prompt="approval shield closes, final card glows",
            overlay_text="PUBLIKUJ PO REVIEW",
            camera="slow push-in",
            negative_prompt=negative,
        ),
    )


def _workflow_scene_prompts(hook: str) -> tuple[VisualScenePrompt, ...]:
    negative = "real app logo, copied interface, cluttered text, tiny unreadable captions, watermark"
    return (
        VisualScenePrompt(
            scene_id="scene_01_hook",
            timing="0-2s",
            goal="Show clear before/after process hook.",
            image_prompt="vertical high contrast UI-style visual, chaos folder versus clean render package, bold headline, dark blue background",
            motion_prompt="chaos folder shakes, clean package snaps into place",
            overlay_text=hook,
            camera="front-facing dashboard card",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_02_process",
            timing="2-15s",
            goal="Show pipeline steps as visual artifacts.",
            image_prompt="vertical dashboard with five cards: topic, hook, asset plan, voice, render QA, cyan and amber highlights, no real logos",
            motion_prompt="cards light up in sequence",
            overlay_text="PROCES > NARZĘDZIA",
            camera="slight angled macro over cards",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_03_quality",
            timing="15-30s",
            goal="Show QA blocking weak output.",
            image_prompt="vertical QA gate, weak video card blocked in red, improved card approved in green, crisp typography, dark background",
            motion_prompt="red card stops at gate, green card passes",
            overlay_text="SŁABE BLOKUJ",
            camera="centered gate shot",
            negative_prompt=negative,
        ),
        VisualScenePrompt(
            scene_id="scene_04_package",
            timing="30-45s",
            goal="Show final private-ready package.",
            image_prompt="vertical clean file package with final.mp4, script.md, thumbnail.png, render_qa.json, private upload lock icon, no platform logos",
            motion_prompt="files stack neatly, lock icon appears",
            overlay_text="PACZKA DO REVIEW",
            camera="slow zoom on file package",
            negative_prompt=negative,
        ),
    )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.casefold()).strip("-") or "visual"
