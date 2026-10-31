"""Wealth Blueprint full video pipeline job — resumable stages, no upload."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from PIL import Image

from atnos.core.config import Settings
from atnos.production.audio_pipeline import F5NarrationSynthesizer, WealthBlueprintAudioPipeline
from atnos.production.channels.wealth_blueprint.health import run_wealth_blueprint_video_engine_health
from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
from atnos.production.channels.wealth_blueprint.scene_plan_models import (
    NarrationSegment,
    ScenePlannerInput,
)
from atnos.production.channels.wealth_blueprint.video_job_models import (
    STAGE_ORDER,
    JobState,
    JobStatus,
    StageRecord,
)
from atnos.production.channels.wealth_blueprint.video_qc import run_wealth_blueprint_qc
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.project_manifest import (
    file_sha256,
    save_project_manifest,
)
from atnos.production.remotion_renderer import render_project_manifest
from atnos.production.steps.assembler import _mux_remotion_video


DEFAULT_TOPIC = "Keep more of your raise"
DEFAULT_SCRIPT = (
    "Your paycheck feels bigger this year. "
    "But wealth is what you keep, not what you earn. "
    "Track income against expenses every month so the gap is visible. "
    "Save first, then spend what remains without guilt. "
    "Invest consistently. Compound growth rewards time, not perfection. "
    "A small automatic transfer beats waiting for the perfect moment. "
    "Start today, keep the system simple, and let months stack in your favor."
)


class WealthBlueprintVideoJobError(RuntimeError):
    pass


class BlockedDependencyError(WealthBlueprintVideoJobError):
    """A required production dependency is unavailable — do not substitute silently."""

    def __init__(self, dependency: str, detail: str) -> None:
        self.dependency = dependency
        self.detail = detail
        super().__init__(f"BLOCKED_DEPENDENCY:{dependency}: {detail}")


class WealthBlueprintVideoJob:
    """TOPIC→…→FINAL_RENDER orchestration. Never uploads."""

    def __init__(self, settings: Settings, *, job_id: str | None = None, artifact_root: Path | None = None) -> None:
        self.settings = settings
        self.job_id = job_id or f"wb-video-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        root = artifact_root or (Path(settings.data_dir) / "production" / "wealth_blueprint" / self.job_id)
        self.artifact_dir = root
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.artifact_dir / "job.json"
        self.state = self._load_or_create()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _load_or_create(self) -> JobState:
        if self.state_path.is_file():
            return JobState.model_validate(json.loads(self.state_path.read_text(encoding="utf-8")))
        state = JobState(
            job_id=self.job_id,
            status="PENDING",
            created_at=self._now(),
            updated_at=self._now(),
            artifact_dir=str(self.artifact_dir),
            stages={
                name: StageRecord(name=name)
                for name in STAGE_ORDER
                if name not in {"DONE", "FAILED", "BLOCKED_DEPENDENCY"}
            },
        )
        self._save(state)
        return state

    def _save(self, state: JobState | None = None) -> None:
        if state is not None:
            self.state = state
        self.state.updated_at = self._now()
        self.state_path.write_text(self.state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _hash(self, payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _stage_dir(self, name: str) -> Path:
        path = self.artifact_dir / "stages" / name.lower()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_stage(self, name: JobStatus, input_payload: dict[str, Any], fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Resume-aware stage runner: skip when completed with matching input hash."""
        record = self.state.stages.get(name) or StageRecord(name=name)
        input_hash = self._hash(input_payload)
        marker = self._stage_dir(name) / "output.json"
        if (
            record.status == "completed"
            and record.input_hash == input_hash
            and marker.is_file()
        ):
            return json.loads(marker.read_text(encoding="utf-8"))

        self.state.status = name
        record.status = "running"
        record.started_at = self._now()
        record.input_hash = input_hash
        record.error = None
        self.state.stages[name] = record
        self._save()
        started = time.perf_counter()
        try:
            output = fn()
            marker.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record.status = "completed"
            record.finished_at = self._now()
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            record.output_paths = [str(marker), *[str(p) for p in output.get("artifacts", [])]]
            self.state.stages[name] = record
            self._save()
            return output
        except BlockedDependencyError as exc:
            record.status = "blocked"
            record.finished_at = self._now()
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            record.error = str(exc)[:2000]
            record.blocked_dependency = exc.dependency
            self.state.stages[name] = record
            self.state.status = "BLOCKED_DEPENDENCY"
            if exc.dependency not in self.state.blocked_dependencies:
                self.state.blocked_dependencies.append(exc.dependency)
            self._save()
            raise
        except Exception as exc:
            record.status = "failed"
            record.finished_at = self._now()
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            record.error = str(exc)[:2000]
            self.state.stages[name] = record
            self.state.status = "FAILED"
            self._save()
            raise WealthBlueprintVideoJobError(f"{name} failed: {exc}") from exc

    def preflight_dependencies(self) -> dict[str, Any]:
        """Fail closed when required deps are down — never substitute OfflineTone/fixtures."""
        # Comfy is checked at ASSETS stage only when required assets are missing.
        report = run_wealth_blueprint_video_engine_health(
            self.settings,
            require_comfy=False,
            require_whisper=bool(self.settings.wealth_blueprint_require_whisper_alignment),
        )
        (self.artifact_dir / "health_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if report["status"] == "BLOCKED_DEPENDENCY":
            blocked = report.get("blocked_dependencies") or []
            self.state.status = "BLOCKED_DEPENDENCY"
            self.state.blocked_dependencies = list(blocked)
            self._save()
            raise BlockedDependencyError(
                blocked[0] if blocked else "unknown",
                f"preflight blocked: {', '.join(blocked)}",
            )
        return report

    def run(self, *, topic: str = DEFAULT_TOPIC, script: str | None = None, force: bool = False) -> JobState:
        if force:
            for name, stage in self.state.stages.items():
                stage.status = "pending"
                stage.input_hash = None
                stage.error = None
                stage.blocked_dependency = None
            self.state.status = "PENDING"
            self.state.auto_corrections = []
            self.state.blocked_dependencies = []
            self._save()

        self.state.topic = topic
        self.state.upload = False
        self._save()

        # Apply configured tool paths into process env for child renderers
        self._apply_runtime_env()
        self.preflight_dependencies()

        script_text = script or DEFAULT_SCRIPT

        script_out = self._run_stage(
            "SCRIPT",
            {"topic": topic, "script": script_text},
            lambda: self._stage_script(topic, script_text),
        )
        audio_out = self._run_stage(
            "AUDIO",
            {"script_hash": script_out["script_hash"]},
            lambda: asyncio.run(self._stage_audio(script_out)),
        )
        plan_out = self._run_stage(
            "PLANNING",
            {"script_hash": script_out["script_hash"], "audio_hash": audio_out["audio_hash"]},
            lambda: self._stage_planning(script_out, audio_out),
        )
        assets_out = self._run_stage(
            "ASSETS",
            {"plan_hash": plan_out["plan_hash"]},
            lambda: self._stage_assets(plan_out),
        )
        preview_out = self._run_stage(
            "RENDER_PREVIEW",
            {
                "plan_hash": plan_out["plan_hash"],
                "audio_hash": audio_out["audio_hash"],
                "assets_hash": assets_out["assets_hash"],
            },
            lambda: self._stage_render_preview(script_out, audio_out, plan_out, assets_out),
        )
        qc_out = self._run_stage(
            "QC",
            {"preview_hash": preview_out["preview_hash"]},
            lambda: self._stage_qc(audio_out, plan_out, preview_out),
        )
        corrections_out = self._run_stage(
            "CORRECTIONS",
            {"qc_ok": qc_out["ok"], "preview_hash": preview_out["preview_hash"]},
            lambda: self._stage_corrections(qc_out, preview_out, audio_out, plan_out, assets_out, script_out),
        )
        final_out = self._run_stage(
            "FINAL_RENDER",
            {"corrections_hash": corrections_out.get("output_hash"), "qc_ok": corrections_out.get("qc_ok", qc_out["ok"])},
            lambda: self._stage_final(corrections_out, preview_out),
        )

        self.state.status = "DONE"
        self.state.final_mp4 = final_out.get("final_mp4")
        report = self._write_report(script_out, audio_out, plan_out, assets_out, preview_out, qc_out, corrections_out, final_out)
        self.state.report_path = str(report)
        self._save()
        return self.state

    def _apply_runtime_env(self) -> None:
        import os

        if self.settings.ffmpeg_exe:
            os.environ["FFMPEG_EXE"] = str(self.settings.ffmpeg_exe)
            os.environ["ATNOS_FFMPEG"] = str(self.settings.ffmpeg_exe)
        if self.settings.atnos_node_exe:
            os.environ["ATNOS_NODE"] = str(self.settings.atnos_node_exe)
        if self.settings.remotion_browser_executable:
            os.environ["ATNOS_REMOTION_BROWSER"] = str(self.settings.remotion_browser_executable)
        if self.settings.atnos_tools_dir:
            os.environ["ATNOS_TOOLS_DIR"] = str(self.settings.atnos_tools_dir)

    def _stage_script(self, topic: str, script: str) -> dict[str, Any]:
        out = self._stage_dir("SCRIPT")
        path = out / "script.json"
        payload = {
            "topic": topic,
            "script": script,
            "language": "en",
            "channel": "wealth_blueprint",
            "word_count": len(script.split()),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return {"artifacts": [str(path)], "script": script, "topic": topic, "script_hash": self._hash(payload), "path": str(path)}

    async def _stage_audio(self, script_out: dict[str, Any]) -> dict[str, Any]:
        out = self._stage_dir("AUDIO")
        work = out / "work"
        work.mkdir(exist_ok=True)
        prefer_whisper = bool(self.settings.wealth_blueprint_require_whisper_alignment)
        synthesizer = F5NarrationSynthesizer(self.settings)
        pipeline = WealthBlueprintAudioPipeline(
            self.settings,
            synthesizer=synthesizer,
            cache_root=out / "cache",
            prefer_whisper=prefer_whisper,
            require_whisper=prefer_whisper,
        )
        try:
            result = await pipeline.run(script_out["script"], work_dir=work)
        except Exception as exc:
            message = str(exc)
            dependency = "tts"
            if "Whisper" in message or "whisper" in message:
                dependency = "whisper"
            raise BlockedDependencyError(dependency, f"F5 TTS / audio pipeline failed: {exc}") from exc

        if str(result.engine or "").startswith("offline") or result.engine == "offline_tone":
            raise BlockedDependencyError(
                "tts",
                f"production job refused offline/tone engine ({result.engine}); configure F5_TTS_BASE_URL",
            )

        payload = result.model_dump(mode="json")
        (out / "audio_result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return {
            "artifacts": [result.audio_file, result.word_timestamps_path],
            "audio_file": result.audio_file,
            "word_timestamps_path": result.word_timestamps_path,
            "duration_sec": result.duration_seconds,
            "segments": [s.model_dump(mode="json") for s in result.segments],
            "words": [w.model_dump(mode="json") for w in result.words],
            "engine": result.engine,
            "audio_hash": self._hash({"file": result.audio_file, "duration": result.duration_seconds}),
        }

    def _stage_planning(self, script_out: dict[str, Any], audio_out: dict[str, Any]) -> dict[str, Any]:
        out = self._stage_dir("PLANNING")
        segments = [
            NarrationSegment.model_validate(
                {
                    "id": item["id"],
                    "text": item["text"],
                    "start": item["start"],
                    "end": item["end"],
                }
            )
            for item in audio_out.get("segments") or []
        ]
        plan = WealthBlueprintScenePlanner(self.settings).plan(
            ScenePlannerInput(
                script=script_out["script"],
                segments=segments,
                profile={"channel": "wealth_blueprint", "topic": script_out["topic"]},
                design_system={"id": "wealth_blueprint_style_v1"},
                available_asset_types=["character", "icon", "charts", "background"],
                mode="deterministic",
            )
        )
        path = out / "scene_plan.json"
        path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return {
            "artifacts": [str(path)],
            "scene_plan_path": str(path),
            "plan": plan.model_dump(mode="json"),
            "plan_hash": self._hash(plan.model_dump(mode="json")),
        }

    def _stage_assets(self, plan_out: dict[str, Any]) -> dict[str, Any]:
        out = self._stage_dir("ASSETS")
        from atnos.production.order_comfy_routing import (
            OrderAssetRequest,
            OrderComfyAssetFulfiller,
            OrderComfyRoutingError,
            OrderRoutingMetrics,
        )
        from atnos.production.channels.wealth_blueprint.health import check_comfyui

        metrics = OrderRoutingMetrics()
        fulfiller = OrderComfyAssetFulfiller(self.settings, metrics=metrics)
        resolved: list[dict[str, Any]] = []
        generation_needs: list[dict[str, Any]] = []
        awaiting_approval: list[dict[str, Any]] = []
        plan = plan_out["plan"]
        for scene in plan.get("scenes") or []:
            for need in scene.get("asset_needs") or []:
                subject = str(need.get("character") or "")
                asset_type = need.get("type") if need.get("type") in {
                    "character", "background", "icon", "money", "banking", "investing",
                    "business", "houses", "cars", "technology", "charts", "decorations", "transitions",
                } else "icon"
                req = OrderAssetRequest(
                    channel_id="wealth_blueprint",
                    asset_type=str(asset_type),
                    subject=subject,
                    pose=str(need.get("pose") or ""),
                    tags=list(need.get("tags") or []),
                    text=str(need.get("text") or ""),
                    purpose="film_final",
                    allow_generate=False,
                )
                try:
                    result = fulfiller.fulfill(req)
                    if result.status in {"reused_library", "reused_cache"} and result.usable_in_final_film:
                        resolved.append(
                            {
                                "scene_id": scene["scene_id"],
                                "asset_id": result.asset_brain_id,
                                "status": result.status,
                                "profile_id": result.profile_id,
                                "comfy_called": False,
                            }
                        )
                    else:
                        generation_needs.append(
                            {
                                "scene_id": scene["scene_id"],
                                "need": need,
                                "reason": result.reason or "missing_approved_asset",
                            }
                        )
                except OrderComfyRoutingError as exc:
                    generation_needs.append(
                        {"scene_id": scene["scene_id"], "need": need, "reason": str(exc)[:200]}
                    )
                except Exception as exc:  # noqa: BLE001
                    generation_needs.append(
                        {"scene_id": scene["scene_id"], "need": need, "reason": str(exc)[:200]}
                    )

        required_missing = [
            item for item in generation_needs if bool((item.get("need") or {}).get("required", True))
        ]
        if required_missing and self.settings.wealth_blueprint_require_comfy_for_missing_assets:
            comfy = check_comfyui(self.settings)
            if comfy["status"] != "ok":
                raise BlockedDependencyError(
                    "comfyui",
                    f"{len(required_missing)} required assets missing and ComfyUI unavailable: {comfy['detail']}",
                )
            for item in list(required_missing):
                need = item.get("need") or {}
                subject = str(need.get("character") or "")
                asset_type = need.get("type") if need.get("type") in {
                    "character", "background", "icon", "money", "banking", "investing",
                    "business", "houses", "cars", "technology", "charts", "decorations", "transitions",
                } else "icon"
                req = OrderAssetRequest(
                    channel_id="wealth_blueprint",
                    asset_type=str(asset_type),
                    subject=subject,
                    pose=str(need.get("pose") or ""),
                    tags=list(need.get("tags") or []),
                    text=str(need.get("text") or ""),
                    purpose="film_final",
                    allow_generate=True,
                )
                try:
                    result = fulfiller.fulfill(req)
                    if result.status in {"reused_library", "reused_cache"} and result.usable_in_final_film:
                        resolved.append(
                            {
                                "scene_id": item.get("scene_id"),
                                "asset_id": result.asset_brain_id,
                                "status": result.status,
                                "profile_id": result.profile_id,
                                "comfy_called": result.comfy_called,
                            }
                        )
                        generation_needs.remove(item)
                    elif result.status == "awaiting_approval" or not result.usable_in_final_film:
                        # Alex / newly generated stills never auto-enter final film.
                        awaiting_approval.append(
                            {
                                "scene_id": item.get("scene_id"),
                                "asset_id": result.asset_brain_id,
                                "status": result.status,
                                "reason": result.reason,
                                "output_path": result.output_path,
                            }
                        )
                        generation_needs.remove(item)
                    else:
                        raise BlockedDependencyError(
                            "comfyui",
                            f"ComfyUI did not provision asset for scene {item.get('scene_id')}: {result.status}",
                        )
                except BlockedDependencyError:
                    raise
                except OrderComfyRoutingError as exc:
                    raise BlockedDependencyError("comfyui", f"asset generation failed: {exc}") from exc
                except Exception as exc:
                    raise BlockedDependencyError("comfyui", f"asset generation failed: {exc}") from exc

        selected_source = self._resolve_selected_still()
        fixture = out / "selected_image.png"
        from atnos.production.channels.wealth_blueprint.scene_production_assets import stage_production_still

        stage_production_still(
            selected_source,
            fixture,
            preserve_alpha=True,
            fit="contain",
        )

        needs_path = out / "generation_needs.json"
        needs_path.write_text(json.dumps(generation_needs, indent=2) + "\n", encoding="utf-8")
        resolved_path = out / "resolved_assets.json"
        resolved_path.write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
        awaiting_path = out / "awaiting_approval.json"
        awaiting_path.write_text(json.dumps(awaiting_approval, indent=2) + "\n", encoding="utf-8")
        metrics_path = out / "comfy_order_routing_metrics.json"
        metrics_path.write_text(json.dumps(metrics.as_dict(), indent=2) + "\n", encoding="utf-8")
        return {
            "artifacts": [str(fixture), str(needs_path), str(resolved_path), str(awaiting_path), str(metrics_path)],
            "selected_image": str(fixture),
            "selected_source": str(selected_source),
            "resolved": resolved,
            "generation_needs": generation_needs,
            "awaiting_approval": awaiting_approval,
            "comfy_routing_metrics": metrics.as_dict(),
            "assets_hash": self._hash(
                {
                    "image": file_sha256(fixture),
                    "resolved": resolved,
                    "needs": generation_needs,
                    "awaiting": awaiting_approval,
                }
            ),
        }

    def _resolve_selected_still(self) -> Path:
        """Prefer approved production stills — never channel reference sheets."""
        from atnos.production.asset_brain.models import AssetQuery
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain

        brain = WealthBlueprintAssetBrain(self.settings)
        for asset_type, character, role in (
            ("character", "alex", "character_cutout"),
            ("icon", None, "icon"),
            ("background", None, "background"),
        ):
            result = brain.select_best(
                AssetQuery(
                    type=asset_type,  # type: ignore[arg-type]
                    character=character,
                    production_role=role,  # type: ignore[arg-type]
                    require_approved=True,
                    exclude_reference_sheets=True,
                )
            )
            if result.status == "found" and result.match is not None:
                asset = result.match.asset
                if not asset.is_production_selectable():
                    continue
                path = brain.resolve_file(asset)
                if path.is_file():
                    return path
        raise BlockedDependencyError(
            "assets",
            "WAITING_FOR_ASSETS: no approved production still "
            "(character_cutout / icon / background); reference sheets are forbidden",
        )

    def _stage_render_preview(
        self,
        script_out: dict[str, Any],
        audio_out: dict[str, Any],
        plan_out: dict[str, Any],
        assets_out: dict[str, Any],
    ) -> dict[str, Any]:
        out = self._stage_dir("RENDER_PREVIEW")
        manifest = scene_plan_to_project_manifest(
            job_id=self.job_id,
            topic=script_out["topic"],
            script=script_out["script"],
            plan=plan_out["plan"],
            selected_image=Path(assets_out["selected_image"]),
            duration_hint=float(audio_out["duration_sec"]),
        )
        manifest_path = out / "project_manifest.json"
        save_project_manifest(manifest_path, manifest)
        silent = out / "renderer_silent.mp4"
        try:
            remotion = render_project_manifest(manifest_path, silent)
        except Exception as exc:
            raise BlockedDependencyError("remotion", f"Remotion render failed: {exc}") from exc
        (out / "remotion_result.json").write_text(json.dumps(remotion, indent=2, default=str) + "\n", encoding="utf-8")
        if not remotion.get("ok"):
            raise BlockedDependencyError(
                "remotion",
                f"Remotion preview failed: {remotion.get('reason') or remotion}",
            )

        voice_path = Path(str(audio_out["audio_file"]))
        duration = float(audio_out.get("duration_sec") or 0.0)
        audio_for_mux = voice_path
        sound_notes: dict[str, Any] = {"wired": False}
        try:
            from atnos.production.sound_design import (
                MusicBedRequest,
                SoundEventRequest,
                VoiceTrackSpec,
                WealthBlueprintSoundDesignEngine,
                render_mix_plan,
            )

            events: list[SoundEventRequest] = []
            for scene in (plan_out.get("plan") or {}).get("scenes") or []:
                for cue in scene.get("sound_events") or []:
                    try:
                        events.append(
                            SoundEventRequest(
                                event=str(cue.get("event") or "soft_whoosh"),  # type: ignore[arg-type]
                                at_sec=float(cue.get("at_sec") or 0.0),
                                priority=int(cue.get("priority") or 0),
                            )
                        )
                    except Exception:
                        continue
            if len(events) < 3:
                events = [
                    SoundEventRequest(event="soft_whoosh", at_sec=0.1, priority=2),
                    SoundEventRequest(event="pop", at_sec=max(1.0, duration * 0.35), priority=3),
                    SoundEventRequest(event="rise", at_sec=max(2.0, duration * 0.7), priority=3),
                ]
            engine = WealthBlueprintSoundDesignEngine()
            plan_mix = engine.plan(
                voice=VoiceTrackSpec(file_path=str(voice_path), duration_sec=max(duration, 0.5)),
                events=events[:8],
                music=MusicBedRequest(event="bed_editorial", gain_db=-18.0),
                seed=self.job_id,
            )
            sound_plan_path = out / "sound_mix_plan.json"
            sound_plan_path.write_text(plan_mix.model_dump_json(indent=2) + "\n", encoding="utf-8")
            sound_notes = {
                "wired": True,
                "plan": str(sound_plan_path),
                "events": len(events),
                "clips": len(plan_mix.clips),
                "dropped": plan_mix.dropped_events,
            }
            if plan_mix.clips:
                mixed = out / "sound_mix.aac"
                rendered = render_mix_plan(plan_mix, mixed)
                audio_for_mux = mixed
                sound_notes["executed"] = True
                sound_notes["mix"] = rendered
            else:
                sound_notes["executed"] = False
                sound_notes["reason"] = "no_resolved_clips"
        except Exception as exc:  # noqa: BLE001
            sound_notes = {"wired": True, "executed": False, "error": str(exc)[:400]}
        (out / "sound_mix_notes.json").write_text(
            json.dumps(sound_notes, indent=2, default=str) + "\n", encoding="utf-8"
        )

        preview = out / "preview.mp4"
        mux = _mux_remotion_video(
            raw_video_path=silent,
            audio_path=audio_for_mux,
            output_path=preview,
            target_lufs=-14.0,
        )
        (out / "mux_result.json").write_text(json.dumps(mux, indent=2, default=str) + "\n", encoding="utf-8")
        if not mux.get("ok"):
            raise BlockedDependencyError("ffmpeg", f"Audio mux failed: {mux.get('reason')}")

        return {
            "artifacts": [str(manifest_path), str(silent), str(preview)],
            "manifest_path": str(manifest_path),
            "silent_mp4": str(silent),
            "preview_mp4": str(preview),
            "preview_hash": file_sha256(preview),
            "remotion": remotion,
            "mux": mux,
            "sound": sound_notes,
        }

    def _stage_qc(self, audio_out: dict[str, Any], plan_out: dict[str, Any], preview_out: dict[str, Any]) -> dict[str, Any]:
        out = self._stage_dir("QC")
        report = run_wealth_blueprint_qc(
            artifact_dir=self.artifact_dir,
            video_path=Path(preview_out["preview_mp4"]),
            audio_path=Path(audio_out["audio_file"]),
            manifest_path=Path(preview_out["manifest_path"]),
            scene_plan_path=Path(plan_out["scene_plan_path"]),
        )
        path = out / "qc_report.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return {"artifacts": [str(path)], "ok": report["ok"], "report": report, "path": str(path)}

    def _stage_corrections(
        self,
        qc_out: dict[str, Any],
        preview_out: dict[str, Any],
        audio_out: dict[str, Any],
        plan_out: dict[str, Any],
        assets_out: dict[str, Any],
        script_out: dict[str, Any],
    ) -> dict[str, Any]:
        out = self._stage_dir("CORRECTIONS")
        if qc_out.get("ok"):
            note = "QC passed — no corrections required"
            (out / "corrections.json").write_text(json.dumps({"applied": [], "note": note}, indent=2) + "\n", encoding="utf-8")
            return {
                "artifacts": [str(out / "corrections.json")],
                "applied": [],
                "preview_mp4": preview_out["preview_mp4"],
                "manifest_path": preview_out["manifest_path"],
                "qc_ok": True,
                "output_hash": preview_out["preview_hash"],
            }

        failed = [c["name"] for c in qc_out.get("report", {}).get("checks", []) if c.get("status") == "fail"]
        applied: list[str] = []
        # Auto-fix: if duration mismatch mild, re-mux with -shortest already used; try again
        if "video_matches_audio_duration" in failed or "final_mp4_valid" in failed:
            preview = Path(preview_out["preview_mp4"])
            silent = Path(preview_out["silent_mp4"])
            mux = _mux_remotion_video(
                raw_video_path=silent,
                audio_path=audio_out["audio_file"],
                output_path=preview,
                target_lufs=-14.0,
            )
            applied.append("re-mux preview after QC duration/mp4 failure")
            self.state.auto_corrections.append("CORRECTIONS: re-muxed preview")
            if not mux.get("ok"):
                raise WealthBlueprintVideoJobError(f"Correction mux failed: {mux.get('reason')}")

        # Re-run QC after corrections
        report = run_wealth_blueprint_qc(
            artifact_dir=self.artifact_dir,
            video_path=Path(preview_out["preview_mp4"]),
            audio_path=Path(audio_out["audio_file"]),
            manifest_path=Path(preview_out["manifest_path"]),
            scene_plan_path=Path(plan_out["scene_plan_path"]),
        )
        (out / "corrections.json").write_text(
            json.dumps({"applied": applied, "failed_before": failed, "qc_after": report}, indent=2) + "\n",
            encoding="utf-8",
        )
        self._save()
        if not report["ok"]:
            failed_after = [c["name"] for c in report.get("checks", []) if c.get("status") == "fail"]
            raise WealthBlueprintVideoJobError(
                f"QC still failing after corrections: {', '.join(failed_after)}"
            )
        return {
            "artifacts": [str(out / "corrections.json")],
            "applied": applied,
            "preview_mp4": preview_out["preview_mp4"],
            "manifest_path": preview_out["manifest_path"],
            "qc_ok": True,
            "output_hash": file_sha256(Path(preview_out["preview_mp4"])),
        }

    def _stage_final(self, corrections_out: dict[str, Any], preview_out: dict[str, Any]) -> dict[str, Any]:
        out = self._stage_dir("FINAL_RENDER")
        src = Path(corrections_out.get("preview_mp4") or preview_out["preview_mp4"])
        final = out / "final.mp4"
        final.write_bytes(src.read_bytes())
        # Also copy to a stable job-level path
        stable = self.artifact_dir / "final_short.mp4"
        stable.write_bytes(src.read_bytes())
        meta = {
            "final_mp4": str(stable),
            "sha256": file_sha256(stable),
            "bytes": stable.stat().st_size,
            "upload": False,
        }
        (out / "final_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return {"artifacts": [str(stable), str(final)], "final_mp4": str(stable), **meta}

    def _write_report(self, *stage_outputs: dict[str, Any]) -> Path:
        timings = {
            name: {
                "status": stage.status,
                "duration_ms": stage.duration_ms,
                "error": stage.error,
            }
            for name, stage in self.state.stages.items()
        }
        report = {
            "job_id": self.job_id,
            "job_type": "wealth_blueprint_video_job",
            "topic": self.state.topic,
            "status": self.state.status,
            "final_mp4": self.state.final_mp4,
            "upload": False,
            "timings": timings,
            "auto_corrections": self.state.auto_corrections,
            "stages_completed": [n for n, s in self.state.stages.items() if s.status == "completed"],
            "works": [],
            "does_not_work": [],
            "errors": [f"{n}: {s.error}" for n, s in self.state.stages.items() if s.error],
        }
        # Fill works / does_not_work after DONE
        for name, stage in self.state.stages.items():
            if stage.status == "completed":
                report["works"].append(f"{name} completed in {stage.duration_ms}ms")
            elif stage.status == "failed":
                report["does_not_work"].append(f"{name}: {stage.error}")
        path = self.artifact_dir / "pipeline_report.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        # Human markdown report
        md = self.artifact_dir / "pipeline_report.md"
        lines = [
            f"# Wealth Blueprint Video Job Report — `{self.job_id}`",
            "",
            f"- Topic: {self.state.topic}",
            f"- Status: {self.state.status}",
            f"- Final MP4: `{self.state.final_mp4}`",
            f"- Upload: no",
            "",
            "## Stage timings",
        ]
        for name, info in timings.items():
            lines.append(f"- **{name}**: {info.get('status')} · {info.get('duration_ms')} ms")
            if info.get("error"):
                lines.append(f"  - error: {info['error']}")
        lines.extend(["", "## What works", *[f"- {item}" for item in report["works"] or ["(none)"]]])
        lines.extend(["", "## What does not work", *[f"- {item}" for item in report["does_not_work"] or ["(none)"]]])
        lines.extend(["", "## Auto corrections", *[f"- {item}" for item in self.state.auto_corrections or ["(none)"]]])
        lines.extend(["", "## Errors", *[f"- {item}" for item in report["errors"] or ["(none)"]]])
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
