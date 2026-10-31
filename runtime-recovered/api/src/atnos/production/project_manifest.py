from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

from atnos.production.models import ApprovedTopic, ProductionContext


SCHEMA_VERSION = "1.0"
MANIFEST_FILENAME = "project_manifest.json"


class SceneType(StrEnum):
    HOOK = "HookScene"
    IMAGE = "ImageScene"
    NUMBER = "NumberScene"
    CHART = "ChartScene"
    CTA = "CTAScene"


SemanticOverlayKind = Literal[
    "reserve_readiness",
    "income_split",
    "payday_arrival",
    "convenience_chain",
    "cumulative_spending",
    "allocation_sorter",
    "auto_invest",
    "purchase_pause",
    "raise_absorption",
    "savings_account",
    "weekly_transfer",
    "notebook_log",
    "habit_repeat",
    "compound_layers",
    "compound_reinvestment",
    "compound_time",
    "compound_early_start",
    "compound_scenario",
]

SEMANTIC_OVERLAY_KINDS: frozenset[str] = frozenset(
    {
        "reserve_readiness",
        "income_split",
        "payday_arrival",
        "convenience_chain",
        "cumulative_spending",
        "allocation_sorter",
        "auto_invest",
        "purchase_pause",
        "raise_absorption",
        "savings_account",
        "weekly_transfer",
        "notebook_log",
        "habit_repeat",
        "compound_layers",
        "compound_reinvestment",
        "compound_time",
        "compound_early_start",
        "compound_scenario",
    }
)

LEGACY_SEMANTIC_OVERLAY_ALIASES: dict[str, SemanticOverlayKind] = {
    "spending_routes": "allocation_sorter",
    "decision_fork": "auto_invest",
}


class QAAction(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    REGENERATE_SCENE = "REGENERATE_SCENE"
    REGENERATE_AUDIO = "REGENERATE_AUDIO"
    BLOCK_FINAL = "BLOCK_FINAL"


class StrictManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(StrictManifestModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "TimeRange":
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        return self


class QAResult(StrictManifestModel):
    status: Literal["pending", "passed", "warning", "failed"] = "pending"
    action: QAAction = QAAction.WARN
    score: float | None = Field(default=None, ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class AssetProvenance(StrictManifestModel):
    provider: str
    model: str = ""
    workflow: str = ""
    workflow_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    workflow_payload: dict[str, Any] = Field(default_factory=dict)
    prompt: str = ""
    seed: int | None = None
    generation_parameters: dict[str, Any] = Field(default_factory=dict)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SceneAsset(StrictManifestModel):
    asset_id: str
    role: Literal["image_candidate", "selected_image", "scene_video", "audio", "caption"]
    path: str
    exists: bool
    provenance: AssetProvenance
    qa: QAResult = Field(default_factory=QAResult)


class CaptionWord(StrictManifestModel):
    """Spoken subtitle word with absolute timing from the captions/voice stage."""

    text: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    segment_id: str = ""

    @model_validator(mode="after")
    def validate_interval(self) -> "CaptionWord":
        if self.end_sec <= self.start_sec:
            raise ValueError("caption word end_sec must be greater than start_sec")
        if not self.text.strip():
            raise ValueError("caption word text must be non-empty")
        return self


class SceneManifest(StrictManifestModel):
    scene_id: str
    scene_type: SceneType
    timing: TimeRange
    narration: str
    caption: str
    headline: str = ""
    overlay_text: str = ""
    layout_id: str = ""
    animation_presets: list[str] = Field(default_factory=list)
    caption_words: list[CaptionWord] = Field(default_factory=list)
    number_value: str | None = None
    chart_values: list[float] = Field(default_factory=list)
    chart_labels: list[str] = Field(default_factory=list)
    chart_unit: str = ""
    claim_ids: list[str] = Field(default_factory=list, max_length=4)
    verified_series: dict | None = None
    comparison_left: str = ""
    comparison_right: str = ""
    semantic_overlay_kind: SemanticOverlayKind | None = None
    visual_prompt: str = ""
    visual_story: dict | None = None
    visual_relation: dict | None = None
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    assets: list[SceneAsset] = Field(default_factory=list)
    qa: QAResult = Field(default_factory=QAResult)


class GlobalArtifact(StrictManifestModel):
    artifact_id: str
    role: Literal["narration", "subtitles", "raw_video", "final_video", "thumbnail", "upload_package"]
    path: str
    exists: bool
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_asset_ids: list[str] = Field(default_factory=list)
    provenance: AssetProvenance


class ProductionVersions(StrictManifestModel):
    brand: str
    renderer: str
    workflow: str
    voice: str


class BrandSpec(StrictManifestModel):
    comparisonStyle: Literal['painted_editorial', 'illustrated_explainer', 'pigment_motion', 'editorial_magazine'] | None = None
    visualVariant: Literal['professional', 'professional_host', 'editorial_motion'] | None = None
    illustrationSystem: Literal['wb_editorial_vector_v1', 'wb_planning_desk_v1', 'wb_planning_desk_painted_v1'] | None = None
    presentation: Literal['standard', 'educational_illustration'] = 'standard'
    @model_serializer(mode='wrap')
    def preserve_legacy_brand_hash(self, handler):
        payload = handler(self)
        if self.comparisonStyle is None:
            payload.pop('comparisonStyle', None)
        if self.visualVariant is None:
            payload.pop('visualVariant', None)
        if self.illustrationSystem is None:
            payload.pop('illustrationSystem', None)
        if self.presentation == 'standard':
            payload.pop('presentation', None)
        return payload
    # Defaults match wealth_blueprint_visual_v1 (VISUAL-BIBLE). Neon mint is superseded.
    background: str = "#142633"
    panel: str = "#1C3344"
    accent: str = "#D5AD6C"
    accentSoft: str = "#426579"
    text: str = "#F0E5D2"
    muted: str = "#4B7977"
    wordmark: str = "WEALTH BLUEPRINT"
    captionStyle: str = "paper_karaoke"


class ProjectManifest(StrictManifestModel):
    review_mode: str = "production"
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    manifest_id: str
    job_id: str
    project_id: str
    account_id: str
    topic_id: str
    topic_title: str
    channel: str
    language_code: str
    brand_profile_version: str
    visual_style_id: Literal["A", "B", "C", "D"] | None = None
    visual_style_version: str | None = None
    visual_style_definition_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    renderer_version: str
    workflow_version: str
    voice_version: str
    brand: BrandSpec = Field(default_factory=BrandSpec)
    status: str
    current_step: str
    scenes: list[SceneManifest] = Field(default_factory=list)
    calculation_claims: list[dict] = Field(default_factory=list)
    artifacts: list[GlobalArtifact] = Field(default_factory=list)
    qa: QAResult = Field(default_factory=QAResult)
    error: str | None = None
    created_at: str
    updated_at: str
    content_hash: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")

    @model_validator(mode="after")
    def validate_unique_scene_ids(self) -> "ProjectManifest":
        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene_id values must be unique")
        from atnos.production.chart_series import validate_compound_checkpoints
        for scene in self.scenes:
            if scene.verified_series is not None:
                series = validate_compound_checkpoints(scene.verified_series, self.calculation_claims)
                if (scene.scene_type != SceneType.CHART or scene.claim_ids != [series['claim_id']]
                        or scene.chart_values != series['values']
                        or scene.chart_labels != [f'YEAR {year}' for year in series['years']]
                        or scene.chart_unit != series['unit']):
                    raise ValueError('Derived compound series is not bound to its chart scene')
        return self


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def deterministic_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_manifest_hash(manifest: ProjectManifest) -> str:
    payload = manifest.model_dump(mode="json")
    return compute_manifest_payload_hash(payload)


def compute_manifest_payload_hash(payload: dict[str, Any]) -> str:
    """Hash the fields that were physically persisted in a manifest revision."""
    payload = dict(payload)
    payload.pop("content_hash", None)
    return deterministic_hash(payload)


def _compute_legacy_manifest_payload_hash(payload: dict[str, Any]) -> str:
    legacy = dict(payload)
    legacy.pop("content_hash", None)
    legacy.pop("created_at", None)
    legacy.pop("updated_at", None)
    return deterministic_hash(legacy)


def save_project_manifest(path: Path, manifest: ProjectManifest) -> ProjectManifest:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = manifest.model_copy(update={"content_hash": compute_manifest_hash(manifest)})
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(stamped.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return stamped


def load_project_manifest(path: Path, *, verify_hash: bool = True) -> ProjectManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Project manifest must contain a JSON object: {path}")
    if verify_hash:
        stored_hash = str(payload.get("content_hash") or "")
        current_hash = compute_manifest_payload_hash(payload)
        legacy_hash = _compute_legacy_manifest_payload_hash(payload)
        if stored_hash not in {current_hash, legacy_hash}:
            raise ValueError(f"Project manifest hash mismatch: {path}")
    # Verify the exact persisted revision before Pydantic supplies defaults for
    # fields introduced by a backward-compatible schema extension. Legacy
    # identifiers are normalized only after that verification, so migration
    # cannot hide a modified or corrupted persisted revision.
    manifest = ProjectManifest.model_validate(_normalize_legacy_manifest_payload(payload))
    return manifest


def _normalize_legacy_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list):
        return normalized
    scenes: list[Any] = []
    for raw_scene in raw_scenes:
        if not isinstance(raw_scene, dict):
            scenes.append(raw_scene)
            continue
        scene = dict(raw_scene)
        if str(scene.get("scene_type") or "") == "ComparisonScene":
            scene["scene_type"] = SceneType.CHART.value
        stored_kind = scene.get("semantic_overlay_kind")
        if stored_kind:
            scene["semantic_overlay_kind"] = normalize_semantic_overlay_kind(stored_kind)
        scenes.append(scene)
    normalized["scenes"] = scenes
    return normalized


def persist_context_manifest(
    context: ProductionContext,
    *,
    current_step: str,
    status: str,
    error: str | None = None,
) -> ProjectManifest:
    path = context.artifact_dir / MANIFEST_FILENAME
    previous = load_project_manifest(path) if path.exists() else None
    manifest = build_project_manifest(
        context,
        current_step=current_step,
        status=status,
        error=error,
        created_at=previous.created_at if previous else None,
    )
    return save_project_manifest(path, manifest)


def migrate_legacy_artifacts(
    artifact_dir: Path,
    *,
    job_id: str,
    account_id: str,
    topic_id: str,
    topic_title: str,
) -> ProjectManifest:
    artifacts: dict[str, Any] = {}
    files = {
        "operating_standards": "operating_standards.json",
        "faceless_playbook": "faceless_playbook.json",
        "script": "script.json",
        "voice": "audio_timestamps.json",
        "visuals": "visual_plan.json",
        "video": "video_manifest.json",
        "assembly": "assembly_manifest.json",
        "thumbnail": "thumbnail_manifest.json",
        "qa": "qa_report.json",
        "upload_package": "youtube_private_upload_package.json",
    }
    for key, filename in files.items():
        source = artifact_dir / filename
        if source.is_file():
            value = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                artifacts[key] = value
    context = ProductionContext(
        job_id=job_id,
        account_id=account_id,
        topic=ApprovedTopic(
            id=topic_id,
            account_id=account_id,
            title=topic_title,
            description="",
            total_score=0,
        ),
        artifact_dir=artifact_dir,
        artifacts=artifacts,
    )
    return save_project_manifest(
        artifact_dir / MANIFEST_FILENAME,
        build_project_manifest(context, current_step="migrated", status="legacy_migrated"),
    )


def build_project_manifest(
    context: ProductionContext,
    *,
    current_step: str,
    status: str,
    error: str | None = None,
    created_at: str | None = None,
) -> ProjectManifest:
    now = datetime.now(UTC).isoformat()
    script = _dict(context.artifacts.get("script"))
    voice = _dict(context.artifacts.get("voice"))
    visuals = _dict(context.artifacts.get("visuals"))
    video = _dict(context.artifacts.get("video"))
    assembly = _dict(context.artifacts.get("assembly"))
    qa = _dict(context.artifacts.get("qa"))
    profile = _dict(script.get("channel_profile"))
    brand = _brand_spec(profile)
    versions = _versions(context, script, video, voice, assembly, brand)
    captions = _dict(context.artifacts.get("captions"))
    scenes = _build_scenes(script, voice, visuals, video, versions, captions=captions)
    return ProjectManifest(
        manifest_id=f"manifest-{context.job_id}",
        job_id=context.job_id,
        project_id=context.account_id,
        account_id=context.account_id,
        topic_id=context.topic.id,
        topic_title=context.topic.title,
        channel=_channel_key(profile, context.account_id),
        language_code=str(script.get("language_code") or profile.get("language_code") or "en"),
        brand_profile_version=versions.brand,
        renderer_version=versions.renderer,
        workflow_version=versions.workflow,
        voice_version=versions.voice,
        brand=brand,
        status=status,
        current_step=current_step,
        scenes=scenes,
        artifacts=_global_artifacts(context, scenes),
        qa=_overall_qa(qa),
        error=error,
        created_at=created_at or now,
        updated_at=now,
    )


def _build_scenes(
    script: dict[str, Any],
    voice: dict[str, Any],
    visuals: dict[str, Any],
    video: dict[str, Any],
    versions: ProductionVersions,
    captions: dict[str, Any] | None = None,
) -> list[SceneManifest]:
    from atnos.production.caption_timing import flatten_caption_words, subtitle_text_from_words, words_for_scene

    segments = [item for item in script.get("segments", []) if isinstance(item, dict)]
    timestamps = {
        str(item.get("segment_id")): item
        for item in voice.get("timestamps", [])
        if isinstance(item, dict) and item.get("segment_id") is not None
    }
    visual_scenes = [item for item in visuals.get("scenes", []) if isinstance(item, dict)]
    clips = [item for item in video.get("clips", []) if isinstance(item, dict)]
    timed_words = flatten_caption_words(captions=captions, voice=voice)
    script_source_text = " ".join(
        str(segment.get("text") or "").strip()
        for segment in segments
        if str(segment.get("text") or "").strip()
    )
    script_source_values = [value for _, value in _numeric_values(script_source_text)]
    output: list[SceneManifest] = []
    cursor = 0.0
    for index, segment in enumerate(segments):
        scene_id = str(segment.get("id") or f"s{index + 1:02d}")
        timestamp = _dict(timestamps.get(scene_id))
        visual = visual_scenes[index] if index < len(visual_scenes) else {}
        clip = clips[index] if index < len(clips) else {}
        start = _float(timestamp.get("start_sec"), cursor)
        duration = max(
            0.001,
            _float(
                timestamp.get("duration_sec"),
                _float(visual.get("duration_s"), _float(segment.get("duration_target_sec"), 1.0)),
            ),
        )
        end = _float(timestamp.get("end_sec"), start + duration)
        if end <= start:
            end = start + duration
        cursor = end
        narration = str(segment.get("text") or "").strip()
        visual_prompt = str(visual.get("visual_prompt") or clip.get("prompt") or "").strip()
        scene_type = infer_scene_type(segment, visual, require_source_backed_data=True)
        semantic_overlay_kind = (
            normalize_semantic_overlay_kind(visual.get("semantic_overlay_kind"))
            or infer_semantic_overlay_kind(
                narration,
                visual.get("headline"),
                visual.get("overlay_text"),
            )
            or normalize_semantic_overlay_kind(clip.get("semantic_overlay_kind"))
        )
        source_text = narration or str(visual.get("headline") or visual.get("overlay_text") or "")
        numeric_values = _numeric_values(source_text)
        source_chart_values = [value for _, value in numeric_values]
        raw_chart_values = visual.get("chart_values")
        chart_values = _source_backed_chart_values(raw_chart_values, source_chart_values)
        planner_chart_values = (
            [float(value) for value in raw_chart_values]
            if isinstance(raw_chart_values, list)
            else []
        )
        chart_source_text = source_text
        if scene_type is SceneType.CHART and semantic_overlay_kind == "compound_reinvestment":
            script_backed_values = _source_backed_chart_values(raw_chart_values, script_source_values)
            if planner_chart_values == script_backed_values:
                chart_values = script_backed_values
                chart_source_text = script_source_text
        chart_labels = (
            _source_backed_chart_labels(visual.get("chart_labels"), chart_values, chart_source_text)
            if planner_chart_values == chart_values
            else []
        )
        comparison_left = _source_backed_chart_label(visual.get("comparison_left"), source_text)
        comparison_right = _source_backed_chart_label(visual.get("comparison_right"), source_text)
        if not (comparison_left and comparison_right):
            comparison_left = ""
            comparison_right = ""
        scene_words = words_for_scene(
            timed_words,
            scene_id=scene_id,
            start_sec=start,
            end_sec=end,
        )
        caption = subtitle_text_from_words(scene_words) if scene_words else narration
        scene_input = {
            "segment": segment,
            "visual": visual,
            "semantic_overlay_kind": semantic_overlay_kind,
            "timing": {"start_sec": start, "end_sec": end},
            "caption_words": [word.model_dump(mode="json") for word in scene_words],
            "versions": versions.model_dump(mode="json"),
        }
        output.append(
            SceneManifest(
                scene_id=scene_id,
                scene_type=scene_type,
                timing=TimeRange(start_sec=round(start, 3), end_sec=round(end, 3)),
                narration=narration,
                caption=caption,
                headline=str(visual.get("headline") or "").strip(),
                overlay_text=str(visual.get("overlay_text") or "").strip(),
                caption_words=scene_words,
                number_value=_source_number_value(narration, numeric_values)
                if scene_type is SceneType.NUMBER
                else None,
                chart_values=chart_values if scene_type is SceneType.CHART else [],
                chart_labels=chart_labels if scene_type is SceneType.CHART else [],
                comparison_left=comparison_left,
                comparison_right=comparison_right,
                semantic_overlay_kind=semantic_overlay_kind or None,
                visual_prompt=visual_prompt,
                input_hash=deterministic_hash(scene_input),
                assets=_scene_assets(scene_id, clip, visual_prompt, scene_input),
                qa=_scene_qa(clip),
            )
        )
    return output


def infer_semantic_overlay_kind(*values: Any) -> SemanticOverlayKind | None:
    """Map source-backed finance narration to a deterministic visual explanation."""
    context = " ".join(str(value or "") for value in values).casefold()
    if "after you earn" in context:
        return "income_split"
    if "paycheck lands" in context:
        return "payday_arrival"
    if "convenience gets" in context:
        return "convenience_chain"
    if "feels too small" in context or "small to matter" in context:
        return "cumulative_spending"
    if "assigned without a plan" in context or "by friday" in context:
        return "allocation_sorter"
    if "autopilot" in context:
        return "auto_invest"
    if "boring pause" in context or "pause before" in context or "wait before" in context:
        return "purchase_pause"
    # Concrete production explain beats (must not fall through to decorative geometry).
    if any(
        token in context
        for token in (
            "pay raise",
            "pay rise",
            "higher income",
            "absorb the raise",
            "absorb the increase",
            "new recurring expenses",
            "lifestyle creep",
            "income enters a budget",
            "part of the increase",
            "next raise",
            "extra money to save",
        )
    ):
        return "raise_absorption"
    if any(
        token in context
        for token in (
            "weekly transfer",
            "weekly transfers",
            "automatic weekly",
            "automatic transfer",
            "auto transfer",
            "turn on automatic",
        )
    ):
        return "weekly_transfer"
    if any(
        token in context
        for token in (
            "savings account",
            "separate savings",
            "choose a separate",
            "high-yield savings",
            "high yield savings",
        )
    ):
        return "savings_account"
    if any(
        token in context
        for token in (
            "notebook",
            "budgeting app",
            "budget app",
            "track your transfer",
            "track your transfers",
            "write down",
        )
    ):
        return "notebook_log"
    if any(
        token in context
        for token in (
            "this habit",
            "habit builds",
            "fixed amount each week",
            "financial cushion",
            "each week",
            "every week",
        )
    ):
        return "habit_repeat"
    if "starting money" in context and "prior interest" in context:
        return "compound_layers"
    if "next calculation" in context and "larger base" in context:
        return "compound_reinvestment"
    if "time matters" in context and "one-day" in context:
        return "compound_time"
    if "starting earlier" in context and "more periods" in context:
        return "compound_early_start"
    if "long-term contribution scenario" in context:
        return "compound_scenario"
    return None


def normalize_semantic_overlay_kind(value: Any) -> SemanticOverlayKind | None:
    """Normalize persisted overlay identifiers without accepting unknown values."""
    normalized = str(value or "").strip()
    if not normalized:
        return None
    legacy = LEGACY_SEMANTIC_OVERLAY_ALIASES.get(normalized)
    if legacy is not None:
        return legacy
    if normalized in SEMANTIC_OVERLAY_KINDS:
        return normalized  # type: ignore[return-value]
    return None


def _scene_assets(
    scene_id: str,
    clip: dict[str, Any],
    visual_prompt: str,
    scene_input: dict[str, Any],
) -> list[SceneAsset]:
    paths: list[tuple[str, str]] = []
    selected_image_path = str(clip.get("selected_image_path") or "").strip()
    candidate_records = {
        str(item.get("path") or "").strip(): item
        for item in (clip.get("image_candidates") or [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    for index, value in enumerate(clip.get("generated_image_paths") or []):
        if str(value).strip():
            paths.append((f"{scene_id}-candidate-{index + 1}", str(value)))
    if str(clip.get("file") or "").strip():
        paths.append((f"{scene_id}-video", str(clip["file"])))
    provider = str(clip.get("backend") or "unassigned")
    model = str(clip.get("model") or clip.get("checkpoint") or "")
    workflow = str(clip.get("workflow") or clip.get("target_backend") or "")
    seed = clip.get("seed") if isinstance(clip.get("seed"), int) else None
    result: list[SceneAsset] = []
    for asset_id, value in paths:
        path = Path(value)
        if asset_id.endswith("-video"):
            role = "scene_video"
        elif selected_image_path and _same_path(value, selected_image_path):
            role = "selected_image"
        else:
            role = "image_candidate"
        candidate_record = candidate_records.get(value, {})
        candidate_evidence = _dict(candidate_record.get("generation_evidence"))
        workflow_payload = _dict(candidate_evidence.get("workflow"))
        workflow_hash = str(candidate_evidence.get("workflow_sha256") or "").strip() or (
            deterministic_hash(workflow_payload) if workflow_payload else None
        )
        candidate_models = candidate_evidence.get("models")
        candidate_model = (
            ",".join(str(item) for item in candidate_models if str(item).strip())
            if isinstance(candidate_models, list)
            else ""
        )
        is_image_candidate = role in {"selected_image", "image_candidate"}
        asset_prompt = (
            str(candidate_record.get("prompt") or visual_prompt)
            if is_image_candidate
            else visual_prompt
        )
        asset_seed = (
            candidate_record.get("seed")
            if is_image_candidate and isinstance(candidate_record.get("seed"), int)
            else seed
        )
        asset_model = candidate_model if is_image_candidate and candidate_model else model
        asset_workflow = (
            str(candidate_evidence.get("backend") or workflow)
            if is_image_candidate
            else workflow
        )
        generation_parameters = (
            dict(_dict(candidate_evidence.get("generation_parameters")))
            if is_image_candidate
            else dict(_dict(clip.get("generation_parameters")))
        )
        if is_image_candidate:
            generation_parameters["selection_evidence"] = _candidate_selection_evidence(
                candidate_record,
                selected=role == "selected_image",
            )
        asset_input_hash = deterministic_hash(scene_input)
        if is_image_candidate:
            asset_input_hash = deterministic_hash(
                {
                    "scene_input_hash": asset_input_hash,
                    "asset_role": role,
                    "provider": "comfyui" if candidate_evidence else provider,
                    "model": asset_model,
                    "workflow": asset_workflow,
                    "workflow_hash": workflow_hash,
                    "prompt": asset_prompt,
                    "seed": asset_seed,
                    "generation_parameters": generation_parameters,
                }
            )
        result.append(
            SceneAsset(
                asset_id=asset_id,
                role=role,
                path=str(path),
                exists=path.is_file(),
                provenance=AssetProvenance(
                    provider="comfyui" if is_image_candidate and candidate_evidence else provider,
                    model=asset_model,
                    workflow=asset_workflow,
                    workflow_hash=workflow_hash if is_image_candidate else None,
                    workflow_payload=workflow_payload if is_image_candidate else {},
                    prompt=asset_prompt,
                    seed=asset_seed,
                    generation_parameters=generation_parameters,
                    input_hash=asset_input_hash,
                    artifact_hash=file_sha256(path),
                ),
                qa=_candidate_qa(candidate_record) if role in {"selected_image", "image_candidate"} else _scene_qa(clip),
            )
        )
    return result


def _global_artifacts(context: ProductionContext, scenes: list[SceneManifest]) -> list[GlobalArtifact]:
    voice = _dict(context.artifacts.get("voice"))
    video = _dict(context.artifacts.get("video"))
    assembly = _dict(context.artifacts.get("assembly"))
    thumbnail = _dict(context.artifacts.get("thumbnail"))
    upload = _dict(context.artifacts.get("upload_package"))
    scene_artifact_hashes = {
        asset.asset_id: asset.provenance.artifact_hash
        for scene in scenes
        for asset in scene.assets
        if asset.role == "scene_video" and asset.exists and asset.provenance.artifact_hash
    }
    scene_asset_ids = list(scene_artifact_hashes)
    candidates = [
        (
            "narration",
            "narration",
            voice.get("output_file") or voice.get("audio_path"),
            str(voice.get("primary") or "unassigned"),
            "voice_synthesis",
            [],
        ),
        (
            "subtitles",
            "subtitles",
            voice.get("subtitle_path") or assembly.get("subtitle_file"),
            str(voice.get("subtitle_status") or "subtitle_generator"),
            "subtitle_alignment",
            ["narration"],
        ),
        (
            "raw-video",
            "raw_video",
            assembly.get("remotion_render_path") or video.get("video_raw_path"),
            "remotion" if assembly.get("remotion_render_path") else str(_dict(video.get("backend")).get("mode") or "comfyui"),
            str(assembly.get("renderer_version") or video.get("workflow_template") or "video_generation"),
            scene_asset_ids,
        ),
        (
            "final-video",
            "final_video",
            assembly.get("output_file") or assembly.get("video_path"),
            "ffmpeg",
            "assembly",
            (["raw-video", "narration"] if assembly.get("remotion_render_path") else ["raw-video", "narration", "subtitles"]),
        ),
        (
            "thumbnail",
            "thumbnail",
            thumbnail.get("thumbnail_path"),
            str(thumbnail.get("backend") or "thumbnail_generator"),
            "thumbnail_generation",
            [],
        ),
        (
            "upload-package",
            "upload_package",
            upload.get("package_path") or (context.artifact_dir / "youtube_private_upload_package.json" if upload else None),
            "atnos",
            "private_upload_package",
            ["final-video", "thumbnail"],
        ),
    ]
    output: list[GlobalArtifact] = []
    artifact_hashes: dict[str, str | None] = {}
    for artifact_id, role, value, provider, workflow, source_asset_ids in candidates:
        if not str(value or "").strip():
            continue
        path = Path(str(value))
        artifact_hash = file_sha256(path)
        provenance_provider = provider
        provenance_model = ""
        provenance_workflow = workflow
        provenance_prompt = ""
        provenance_seed: int | None = None
        provenance_parameters: dict[str, Any] = {}
        provenance_workflow_payload: dict[str, Any] = {}

        if artifact_id == "narration":
            identity = _dict(voice.get("voice_identity"))
            provenance_provider = str(identity.get("provider_family") or voice.get("primary") or provider)
            provenance_model = str(identity.get("provider_family") or "")
            provenance_workflow = str(voice.get("primary") or workflow)
            provenance_workflow_payload = {
                "voice_identity": identity,
                "engine_attempts": _voice_attempt_summary(voice.get("engine_attempts")),
            }
            provenance_parameters = {
                "quality_tier": voice.get("quality_tier"),
                "voice_backend_grade": voice.get("voice_backend_grade"),
                "production_ready": voice.get("production_ready"),
                "normalization": voice.get("normalization"),
                "final_normalization": voice.get("final_normalization"),
                "single_pass_narration": voice.get("single_pass_narration"),
                "narration_cache_fingerprint": voice.get("narration_cache_fingerprint"),
            }
        elif artifact_id == "subtitles":
            subtitle_status = _dict(voice.get("subtitle_status"))
            provenance_provider = str(subtitle_status.get("engine") or "subtitle_generator")
            provenance_workflow = "subtitle_alignment"
            provenance_parameters = {
                key: subtitle_status.get(key)
                for key in ("status", "source", "quality_checked", "script_word_overlap", "expected_script_words")
                if key in subtitle_status
            }
        elif artifact_id == "raw-video":
            renderer = _dict(assembly.get("renderer"))
            renderer_marker = _dict(renderer.get("renderer"))
            provenance_provider = "remotion" if assembly.get("remotion_render_path") else provider
            provenance_workflow = str(assembly.get("renderer_version") or workflow)
            provenance_workflow_payload = {
                "renderer_version": assembly.get("renderer_version"),
                "manifest_content_hash": renderer.get("manifest_content_hash"),
                "renderer_source_fingerprint": renderer_marker.get("renderer_source_fingerprint"),
                "renderer_source_hashes": renderer_marker.get("renderer_source_hashes") or {},
                "font_assets": renderer_marker.get("font_assets") or {},
            }
            provenance_parameters = {
                "width": renderer_marker.get("width"),
                "height": renderer_marker.get("height"),
                "fps": renderer_marker.get("fps"),
                "duration_in_frames": renderer_marker.get("duration_in_frames"),
            }
        elif artifact_id == "final-video":
            render = _dict(assembly.get("render"))
            provenance_workflow_payload = {
                "ffmpeg_plan": _dict(assembly.get("ffmpeg_plan")),
                "command": render.get("command") or [],
                "video_filter": render.get("video_filter"),
                "audio_filter": render.get("audio_filter"),
            }
            provenance_parameters = {
                "strategy": _dict(assembly.get("assembly_provenance")).get("strategy"),
                "video_mode": render.get("video_mode"),
                "probe": _dict(render.get("probe")),
                "video_stream_copy_verified": render.get("video_stream_copy_verified"),
                "assembly_chain_hash": _dict(assembly.get("assembly_provenance")).get("chain_hash"),
            }
        elif artifact_id == "thumbnail":
            model = _dict(thumbnail.get("thumbnail_model"))
            prompt_inputs = _dict(thumbnail.get("prompt_inputs"))
            provenance_provider = str(thumbnail.get("thumbnail_generation_backend") or provider)
            provenance_model = str(model.get("checkpoint") or model.get("model_id") or "")
            provenance_workflow = str(thumbnail.get("workflow_template") or workflow)
            provenance_prompt = str(prompt_inputs.get("positive_prompt") or "")
            seed_value = thumbnail.get("seed") or prompt_inputs.get("seed")
            provenance_seed = int(seed_value) if isinstance(seed_value, int) else None
            provenance_workflow_payload = {
                "workflow_template": thumbnail.get("workflow_template"),
                "prompt_inputs": prompt_inputs,
            }
            provenance_parameters = {
                "comfyui_generated": thumbnail.get("comfyui_generated"),
                "fallback_used": thumbnail.get("fallback_used"),
                "local_generated": thumbnail.get("local_generated"),
                "quality": _dict(thumbnail.get("thumbnail_quality")),
            }

        provenance_material = {
            "provider": provenance_provider,
            "model": provenance_model,
            "workflow": provenance_workflow,
            "workflow_payload": provenance_workflow_payload,
            "prompt": provenance_prompt,
            "seed": provenance_seed,
            "generation_parameters": provenance_parameters,
        }
        input_material = {
            "artifact_id": artifact_id,
            "role": role,
            "source_asset_ids": source_asset_ids,
            "source_artifact_hashes": {
                source_id: artifact_hashes.get(source_id) or scene_artifact_hashes.get(source_id)
                for source_id in source_asset_ids
            },
            "provenance": provenance_material,
        }
        output.append(
            GlobalArtifact(
                artifact_id=artifact_id,
                role=role,
                path=str(path),
                exists=path.is_file(),
                artifact_hash=artifact_hash,
                source_asset_ids=source_asset_ids,
                provenance=AssetProvenance(
                    provider=provenance_provider,
                    model=provenance_model,
                    workflow=provenance_workflow,
                    workflow_hash=(
                        deterministic_hash(provenance_workflow_payload)
                        if provenance_workflow_payload
                        else None
                    ),
                    workflow_payload=provenance_workflow_payload,
                    prompt=provenance_prompt,
                    seed=provenance_seed,
                    generation_parameters=provenance_parameters,
                    input_hash=deterministic_hash(input_material),
                    artifact_hash=artifact_hash,
                ),
            )
        )
        artifact_hashes[artifact_id] = artifact_hash
    return output


def _voice_attempt_summary(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        summaries.append(
            {
                "segment_id": item.get("segment_id"),
                "engine": item.get("engine"),
                "status": item.get("status"),
                "single_pass": item.get("single_pass"),
                "attempts": [
                    {
                        key: attempt.get(key)
                        for key in ("engine", "mode", "api_name", "ok", "stream_attempts")
                        if key in attempt
                    }
                    for attempt in attempts
                    if isinstance(attempt, dict)
                ],
            }
        )
    return summaries


def _versions(
    context: ProductionContext,
    script: dict[str, Any],
    video: dict[str, Any],
    voice: dict[str, Any],
    assembly: dict[str, Any],
    brand: BrandSpec,
) -> ProductionVersions:
    standards = _dict(context.artifacts.get("operating_standards"))
    workflow_source = Path(str(video.get("workflow_template") or "unassigned")).expanduser()
    workflow = workflow_source.name
    voice_profile = _dict(voice.get("voice_profile"))
    brand_material = {
        "channel_profile": _dict(script.get("channel_profile")),
        "style_guide": _dict(script.get("style_guide")),
        "standards": {
            "status": standards.get("status"),
            "sections": standards.get("sections") or [],
            "forbidden": standards.get("forbidden") or [],
        },
    }
    explicit_brand_version = str(standards.get("version") or standards.get("schema_version") or "").strip()
    brand_hash = deterministic_hash(
        {"renderer_brand": brand.model_dump(mode="json"), "production_material": brand_material}
    )[:12]
    workflow_material_hash = (
        file_sha256(workflow_source)
        if workflow_source.is_file()
        else deterministic_hash({"unresolved_workflow": str(video.get("workflow_template") or "unassigned")})
    )
    return ProductionVersions(
        brand=(f"{explicit_brand_version}-{brand_hash}" if explicit_brand_version else f"brand-v1-{brand_hash}"),
        renderer=str(assembly.get("renderer_version") or video.get("renderer_version") or "legacy-ffmpeg-v1"),
        workflow=f"{workflow or 'unassigned'}@{workflow_material_hash[:12]}",
        voice=str(voice_profile.get("version") or voice_profile.get("id") or voice.get("primary") or "unassigned"),
    )


def _brand_spec(profile: dict[str, Any]) -> BrandSpec:
    """Return the explicit renderer inputs represented by brand_profile_version."""
    if str(profile.get("key") or "").strip().casefold() == "wealth_blueprint":
        return BrandSpec()
    return BrandSpec(wordmark=str(profile.get("key") or "ATNOS").replace("_", " ").upper())


def infer_scene_type(
    segment: dict[str, Any],
    visual: dict[str, Any],
    *,
    require_source_backed_data: bool = False,
) -> SceneType:
    """Infer a V1 scene from editorial content, never from technical render tokens."""
    explicit_scene_type = str(visual.get("renderer_scene_type") or "").strip()
    if explicit_scene_type:
        if explicit_scene_type == "ComparisonScene":
            return SceneType.CHART
        try:
            return SceneType(explicit_scene_type)
        except ValueError:
            pass
    segment_type = str(segment.get("type") or "").lower()
    segment_text = str(segment.get("text") or "").strip()
    headline_text = str(visual.get("headline") or "").strip()
    overlay_text = str(visual.get("overlay_text") or "").strip()
    primary_narrative = (segment_text or headline_text or overlay_text).lower()
    narrative_text = " ".join(
        str(value or "")
        for value in (
            segment_text,
            headline_text,
            overlay_text,
        )
    ).lower()
    chart_text = f"{narrative_text} {visual.get('visual_prompt') or ''}".lower()
    has_source_backed_chart_values = len(_numeric_values(primary_narrative)) >= 2
    if segment_type == "hook":
        return SceneType.HOOK
    if segment_type in {"cta", "outro"}:
        return SceneType.CTA
    if (has_source_backed_chart_values or not require_source_backed_data) and re.search(
        r"\b(?:charts?|graphs?|axes?|wykres(?:y|u|ie|em)?)\b",
        chart_text,
    ):
        return SceneType.CHART
    if (has_source_backed_chart_values or not require_source_backed_data) and re.search(
        r"\bby (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        chart_text,
    ) and re.search(
        r"\b(?:money|budget|paycheck|spending|savings?)\b", chart_text
    ):
        return SceneType.CHART
    if (has_source_backed_chart_values or not require_source_backed_data) and "pause" in chart_text and re.search(
        r"\b(?:money|budget|paycheck|purchase|spending|savings?)\b",
        chart_text,
    ):
        return SceneType.CHART
    ordinal_words = re.findall(
        r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b",
        primary_narrative,
    )
    if len(set(ordinal_words)) >= 2:
        return SceneType.NUMBER
    if len(re.findall(r"\bone\b", primary_narrative)) >= 2:
        # Enumerated beats such as "One delivery. One subscription. One upgrade."
        # are clearer and safer as deterministic kinetic typography than as an
        # AI still expected to contain several unrelated products at once.
        return SceneType.NUMBER
    if any(char.isdigit() for char in narrative_text):
        return SceneType.NUMBER
    return SceneType.IMAGE


def _channel_key(profile: dict[str, Any], account_id: str) -> str:
    explicit = str(profile.get("key") or "").strip().casefold()
    if explicit:
        return explicit
    normalized = re.sub(r"[^a-z0-9]+", "_", str(account_id or "").casefold()).strip("_")
    if "wealth_blueprint" in normalized or "wealthblueprint" in normalized:
        return "wealth_blueprint"
    if "dark_chronicles" in normalized or "darkchronicles" in normalized:
        return "dark_chronicles"
    if "sigma_squad" in normalized or "sigmasquad" in normalized:
        return "sigma_squad"
    return normalized or "unassigned"


def _same_path(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return left == right


def _candidate_qa(candidate: dict[str, Any]) -> QAResult:
    if not candidate:
        return QAResult(status="pending", action=QAAction.WARN, reasons=["candidate has no recorded QA result"])
    passed = bool(candidate.get("passed"))
    score_value = candidate.get("score")
    score = None
    if isinstance(score_value, (int, float)):
        score = min(1.0, max(0.0, float(score_value)))
    reasons = [str(item) for item in (candidate.get("reasons") or []) if str(item).strip()]
    return QAResult(
        status="passed" if passed else "failed",
        action=QAAction.PASS if passed else QAAction.REGENERATE_SCENE,
        score=score,
        reasons=reasons,
    )


def _candidate_selection_evidence(candidate: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    """Persist why an image candidate was selected or rejected without local paths."""
    comparison = _dict(candidate.get("comparative_vision"))
    comparison_evidence = {
        key: comparison.get(key)
        for key in (
            "status",
            "model",
            "ranked_candidate_indexes",
            "rejected_candidate_indexes",
            "selection_confidence",
            "summary",
            "defects_by_candidate",
        )
        if key in comparison
    }
    diversity = _dict(candidate.get("cross_scene_diversity"))
    diversity_evidence = {
        key: diversity.get(key)
        for key in (
            "minimum_normalized_difference",
            "threshold",
            "compared_scene_count",
            "expected_scene_count",
            "passed",
        )
        if key in diversity
    }
    if selected and comparison_evidence.get("status") == "pass":
        decision = "selected_by_comparative_vision"
    elif selected:
        decision = "selected_by_quality_score"
    elif "comparative_visual_integrity_rejected" in (candidate.get("reasons") or []):
        decision = "rejected_by_comparative_vision"
    elif not bool(candidate.get("passed")):
        decision = "rejected_by_quality_gate"
    else:
        decision = "passing_not_selected"
    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_index": candidate.get("candidate_index"),
        "generation_attempt": candidate.get("generation_attempt"),
        "selected": selected,
        "decision": decision,
        "quality_score": candidate.get("score"),
        "comparison_position": candidate.get("comparison_position"),
        "comparison": comparison_evidence,
        "cross_scene_diversity": diversity_evidence,
        "reasons": [str(item) for item in (candidate.get("reasons") or []) if str(item).strip()],
    }


def _numeric_values(text: str) -> list[tuple[str, float]]:
    values: list[tuple[str, float]] = []
    for match in re.finditer(r"(?<![\w.])(?:[$€£])?(-?\d+(?:[.,]\d+)?)(?:\s?[%xX])?", text):
        raw = match.group(0).strip()
        normalized = match.group(1).replace(",", ".")
        try:
            value = float(normalized)
        except ValueError:
            continue
        values.append((raw, value))
    return values


def _source_backed_chart_values(raw_values: Any, source_values: list[float]) -> list[float]:
    """Accept planner values only when they preserve narration order and multiplicity."""
    if not isinstance(raw_values, list):
        return source_values
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in raw_values
    ):
        raise ValueError("chart_values must contain only finite numeric values")
    explicit = [float(value) for value in raw_values]
    if len(explicit) < 2:
        return source_values
    source_cursor = 0
    for value in explicit:
        expected = round(value, 9)
        match = next(
            (
                index
                for index in range(source_cursor, len(source_values))
                if round(source_values[index], 9) == expected
            ),
            None,
        )
        if match is None:
            return source_values
        source_cursor = match + 1
    return explicit


def _source_backed_chart_labels(
    raw_labels: Any,
    chart_values: list[float],
    source_text: str,
) -> list[str]:
    if not isinstance(raw_labels, list) or len(raw_labels) != len(chart_values):
        return []
    labels = [" ".join(str(label).split())[:24] for label in raw_labels]
    cursor = 0
    for label in labels:
        position = _source_backed_chart_label_position(label, source_text, start_at=cursor)
        if position is None:
            return []
        cursor = position + 1
    return labels


def _source_backed_chart_label(raw_label: Any, source_text: str) -> str:
    """Accept a chart label only when its meaning is explicit in narration."""
    label = " ".join(str(raw_label or "").split())[:24]
    return label if _source_backed_chart_label_position(label, source_text) is not None else ""


def _source_backed_chart_label_position(
    raw_label: Any,
    source_text: str,
    *,
    start_at: int = 0,
) -> int | None:
    """Locate a source-backed label without allowing chart order to rewrite meaning."""
    label = " ".join(str(raw_label or "").split())[:24]
    if not label:
        return None
    normalized_label = _normalize_chart_label_source(label)
    normalized_source = _normalize_chart_label_source(source_text)
    if not normalized_label or not normalized_source:
        return None
    if normalized_label == "start":
        match = re.search(r"\bstart(?:ing)?\b", normalized_source[start_at:])
    else:
        match = re.search(rf"\b{re.escape(normalized_label)}\b", normalized_source[start_at:])
    return start_at + match.start() if match else None


def _normalize_chart_label_source(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    normalized = re.sub(r"\byear\s+one\b", "year 1", normalized)
    normalized = re.sub(r"\byear\s+two\b", "year 2", normalized)
    return normalized


def _source_number_value(
    narration: str,
    numeric_values: list[tuple[str, float]],
) -> str | None:
    """Return a display value that is explicitly present in the narration."""
    if numeric_values:
        return numeric_values[0][0]
    normalized = str(narration or "").lower()
    if len(re.findall(r"\bone\b", normalized)) >= 2:
        return "ONE"
    ordinals = re.findall(
        r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b",
        normalized,
    )
    unique_ordinals = list(dict.fromkeys(ordinals))
    if len(unique_ordinals) >= 2:
        return " / ".join(item.upper() for item in unique_ordinals[:2])
    return unique_ordinals[0].upper() if unique_ordinals else None


def _scene_qa(clip: dict[str, Any]) -> QAResult:
    audits = [
        _dict(clip.get("image_text_audit")),
        _dict(clip.get("image_visual_audit")),
        _dict(clip.get("semantic_visual_audit")),
        _dict(clip.get("cutaway_image_text_audit")),
        _dict(clip.get("cutaway_image_visual_audit")),
    ]
    failures: list[str] = []
    scores: list[float] = []
    for audit in audits:
        if not audit:
            continue
        status = str(audit.get("status") or "").strip().casefold()
        if audit.get("passed") is False or status in {
            "fail",
            "failed",
            "risk",
            "unavailable",
            "invalid_response",
            "missing",
        }:
            reasons = audit.get("blockers") or audit.get("failures") or []
            if isinstance(reasons, list) and reasons:
                failures.extend(str(reason) for reason in reasons if str(reason).strip())
            else:
                failures.append(
                    str(audit.get("reason") or audit.get("message") or f"visual audit status: {status or 'failed'}")
                )
        score = audit.get("score")
        if isinstance(score, (int, float)):
            scores.append(float(score))
    if failures:
        return QAResult(
            status="failed",
            action=QAAction.REGENERATE_SCENE,
            score=min(scores) if scores else None,
            reasons=list(dict.fromkeys(failures)),
        )
    if str(clip.get("status") or "") in {"placeholder", "cached_fallback"}:
        return QAResult(status="failed", action=QAAction.REGENERATE_SCENE, reasons=["scene did not use a generated target asset"])
    if clip:
        return QAResult(status="passed", action=QAAction.PASS, score=min(scores) if scores else None)
    return QAResult()


def _overall_qa(qa: dict[str, Any]) -> QAResult:
    if not qa:
        return QAResult()
    if bool(qa.get("auto_uploadable")):
        return QAResult(status="passed", action=QAAction.PASS)
    failures = [str(item) for item in qa.get("blocking_failures", []) if str(item).strip()]
    owner_gate_names = {"production voice approval", "visual regression baseline"}
    technical_failures = [failure for failure in failures if failure.strip().casefold() not in owner_gate_names]
    lowered = " ".join(technical_failures).lower()
    if failures and not technical_failures:
        return QAResult(status="failed", action=QAAction.BLOCK_FINAL, reasons=failures)
    if any(token in lowered for token in ("audio", "voice", "lufs", "narration")):
        action = QAAction.REGENERATE_AUDIO
    elif any(token in lowered for token in ("visual", "image", "scene")):
        action = QAAction.REGENERATE_SCENE
    else:
        action = QAAction.BLOCK_FINAL
    return QAResult(status="failed", action=action, reasons=failures or ["final QA has not approved the render"])


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
