from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings, load_runtime_env_files
from atnos.intelligence.storage import connect, intelligence_db_path, rows_to_dicts
from atnos.model_manager.model_manager import ModelManager
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.services.secret_vault_service import AES_256_GCM_STORAGE_BACKEND, SecretVaultService
from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeService
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService


LOCAL_PRODUCTION_KEYS = (
    "intelligence_db",
    "topic_scoring",
    "production_modules",
    "script_generation_contract",
    "asset_planner_contract",
    "qa_contract",
    "ollama",
    "audio_stack",
    "ffmpeg",
    "whisper_srt",
    "comfyui",
    "models",
    "workflow_templates",
    "cost_guard",
    "runtime_dependencies",
)

PUBLISHING_KEYS = (
    "secret_vault",
    "youtube_oauth",
    "scheduler",
)

PUBLICATION_KEYS = (
    "secret_vault",
    "youtube_oauth",
    "scheduler",
    "public_upload_policy",
)


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    key: str
    label: str
    status: str
    required: bool
    message: str
    evidence: dict[str, Any]

    @property
    def ready(self) -> bool:
        if not self.required:
            return self.status in {"ready", "degraded", "optional_missing"}
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "required": self.required,
            "ready": self.ready,
            "message": self.message,
            "evidence": self.evidence,
        }


class ShortProductionReadinessService:
    """Runtime readiness audit for the current autonomous Shorts pipeline."""

    def __init__(self, settings: Settings, *, session=None) -> None:
        self.settings = settings
        self.session = session

    async def check(self, *, include_live_checks: bool = True) -> dict[str, Any]:
        items = [
            self._check_intelligence_db(),
            self._check_topic_scoring(),
            self._check_production_modules(),
            self._check_script_generation_contract(),
            self._check_asset_planner_contract(),
            self._check_qa_contract(),
            await self._check_ollama(include_live_checks=include_live_checks),
            await self._check_audio_stack(include_live_checks=include_live_checks),
            self._check_ffmpeg(),
            self._check_whisper(),
            await self._check_video_stack(include_live_checks=include_live_checks),
            await self._check_models(),
            self._check_workflow_templates(),
            self._check_cost_guard(),
            self._check_secret_vault(),
            self._check_youtube(),
            self._check_scheduler(),
            self._check_public_upload_policy(),
            self._check_analytics_score_loop(),
            self._check_slack(),
            self._check_obsidian_memory(),
            self._check_dependencies(),
        ]
        required_items = [item for item in items if item.required]
        blocking = [item for item in required_items if item.status != "ready"]
        warnings = [item for item in items if item.status in {"degraded", "optional_missing"}]
        local_blocking = _blocking_keys_for_layer(items, LOCAL_PRODUCTION_KEYS)
        publish_blocking = _blocking_keys_for_layer(items, PUBLISHING_KEYS)
        public_publish_blocking = _non_ready_keys_for_layer(items, PUBLICATION_KEYS)
        private_review_ready = not local_blocking
        private_upload_ready = private_review_ready and not publish_blocking
        public_publish_ready = private_upload_ready and not public_publish_blocking
        status = (
            "ready"
            if not blocking and public_publish_ready
            else "ready_for_private_review"
            if not blocking and private_review_ready
            else "needs_configuration"
        )
        checklist = _build_operator_checklist(items)
        return {
            "status": status,
            "overall": status,
            "ready": not blocking,
            "local_runtime_ready": not local_blocking,
            "publish_ready": not publish_blocking,
            "private_review_ready": private_review_ready,
            "private_upload_ready": private_upload_ready,
            "public_upload_enabled": bool(self.settings.allow_public_upload),
            "public_publish_ready": public_publish_ready,
            "full_pipeline_ready": not blocking and public_publish_ready,
            "blocking_count": len(blocking),
            "warning_count": len(warnings),
            "blocking": [item.key for item in blocking],
            "local_runtime_blocking": local_blocking,
            "publish_blocking": publish_blocking,
            "public_publish_blocking": public_publish_blocking,
            "warnings": [item.key for item in warnings],
            "items": [item.to_dict() for item in items],
            "operator_checklist": checklist,
            "next_actions": [item["action"] for item in checklist if item["severity"] == "blocking"][:8],
        }

    def _check_intelligence_db(self) -> ReadinessItem:
        path = intelligence_db_path(self.settings)
        tables = set()
        counts: dict[str, int] = {}
        latest_collection_runs: list[dict[str, Any]] = []
        try:
            with connect(self.settings) as db:
                rows = db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
                tables = {str(row["name"]) for row in rows}
                for table in (
                    "yt_signals",
                    "gtrends_signals",
                    "competitor_videos",
                    "own_performance",
                    "reference_channels",
                    "topic_queue",
                    "intelligence_accounts",
                ):
                    if table in tables:
                        counts[table] = int(db.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
                if "collection_runs" in tables:
                    latest_collection_runs = rows_to_dicts(
                        db.execute(
                            """
                            SELECT run_key, collector, status, last_attempt_at, last_success_at, next_retry_at, message
                            FROM collection_runs
                            WHERE collector IN (
                                'youtube_trends', 'google_trends', 'competitor', 'own_channel',
                                'reference_channels', 'intelligence_refresh', 'topic_generation'
                            )
                            ORDER BY last_attempt_at DESC
                            LIMIT 8
                            """
                        ).fetchall()
                    )
        except Exception as exc:
            return _item("intelligence_db", "SQLite intelligence.db", "missing", True, str(exc), {"path": str(path)})
        required = {"yt_signals", "gtrends_signals", "topic_queue", "pipeline_jobs", "production_analytics_followups"}
        missing = sorted(required - tables)
        signal_blockers: list[str] = []
        if not missing:
            if counts.get("yt_signals", 0) <= 0:
                signal_blockers.append("yt_signals")
            if counts.get("gtrends_signals", 0) <= 0:
                signal_blockers.append("gtrends_signals")
        ready = not missing and not signal_blockers
        if missing:
            message = f"Missing tables: {', '.join(missing)}"
        elif signal_blockers:
            latest_youtube_status = next(
                (
                    str(run.get("status"))
                    for run in latest_collection_runs
                    if str(run.get("collector")) == "youtube_trends"
                ),
                "",
            )
            run_hint = f" Last youtube_trends collector status: {latest_youtube_status}." if latest_youtube_status else ""
            message = (
                "Research database schema is ready, but required signal tables are empty: "
                f"{', '.join(signal_blockers)}. Configure account niches via /accounts or "
                "/api/intelligence/accounts, then let the scheduler refresh signals or force /api/intelligence/run."
                f"{run_hint}"
            )
        else:
            message = "Research/scoring database schema and signal data are ready."
        return _item(
            "intelligence_db",
            "SQLite intelligence.db",
            "ready" if ready else "missing",
            True,
            message,
            {
                "path": str(path),
                "missing_tables": missing,
                "counts": counts,
                "latest_collection_runs": latest_collection_runs,
                "empty_required_signal_tables": signal_blockers,
                "youtube_data_api_key_configured": bool(self.settings.youtube_data_api_key),
                "youtube_trends_collector_mode": "api_key" if self.settings.youtube_data_api_key else "oauth",
                "autonomous_refresh_configured_accounts": counts.get("intelligence_accounts", 0),
                "autonomous_refresh_source": "intelligence_accounts",
                "autonomous_refresh_service": "atnos.intelligence.scheduler.IntelligenceRefreshService.refresh_due",
                "account_seed_fields": [
                    "intelligence_niches",
                    "intelligence_region",
                    "intelligence_timeframe",
                    "intelligence_refresh_now",
                ],
                "account_seed_endpoint": "POST /accounts",
                "autonomous_config_endpoint": "POST /api/intelligence/accounts",
                "autonomous_config_list_endpoint": "GET /api/intelligence/accounts",
                "manual_collection_endpoint": "POST /api/intelligence/run",
            },
        )

    def _check_topic_scoring(self) -> ReadinessItem:
        ok = importlib.util.find_spec("atnos.scoring.topic_scorer") is not None
        return _item(
            "topic_scoring",
            "Topic scorer + queue",
            "ready" if ok else "missing",
            True,
            "Topic scorer module is importable." if ok else "Topic scorer module is missing.",
            {"module": "atnos.scoring.topic_scorer"},
        )

    def _check_production_modules(self) -> ReadinessItem:
        modules = [
            "atnos.production.steps.hook_generator",
            "atnos.production.steps.script_generator",
            "atnos.production.steps.voice_synthesis",
            "atnos.production.steps.asset_planner",
            "atnos.production.steps.video_generator",
            "atnos.production.steps.thumbnail_generator",
            "atnos.production.qa_gate",
            "atnos.production.private_upload",
            "atnos.production.publication",
        ]
        missing = [name for name in modules if importlib.util.find_spec(name) is None]
        return _item(
            "production_modules",
            "Hook/script/voice/video/QA modules",
            "ready" if not missing else "missing",
            True,
            "Production modules are importable." if not missing else f"Missing modules: {', '.join(missing)}",
            {"missing_modules": missing},
        )

    def _check_script_generation_contract(self) -> ReadinessItem:
        from atnos.production.steps import hook_generator, script_generator, script_writer
        from atnos.scoring.topic_scorer import TOPIC_SCORING_CONTRACT, TopicScorer

        topic_contract = TOPIC_SCORING_CONTRACT
        hook_contract = getattr(hook_generator, "HOOK_GENERATION_CONTRACT", {})
        script_contract = getattr(script_writer, "SCRIPT_GENERATION_CONTRACT", {})
        required_topic_methods = (
            "score_topic",
            "score_topics",
            "generate_topics",
            "_read_signals",
            "_read_generation_signals",
            "_score_performance_feedback",
        )
        missing_topic_methods = [name for name in required_topic_methods if not hasattr(TopicScorer, name)]
        required_script_functions = (
            "run",
            "_read_topic_intelligence",
            "_topic_intelligence_prompt_block",
            "_style_guide_prompt_block",
        )
        missing_script_functions = [name for name in required_script_functions if not hasattr(script_writer, name)]
        required_hook_functions = ("run", "_llm_or_fallback", "_normalize_variant")
        missing_hook_functions = [name for name in required_hook_functions if not hasattr(hook_generator, name)]
        expected_weights = {
            "viral_potential": 25,
            "search_demand": 20,
            "competition_gap": 20,
            "trend_velocity": 15,
            "niche_fit": 10,
            "freshness": 10,
        }
        scoring_ready = (
            topic_contract.get("signal_source") == "SQLite intelligence.db"
            and topic_contract.get("generation_requires_real_signals") is True
            and topic_contract.get("llm_scope") == "local_ollama_only"
            and topic_contract.get("weights") == expected_weights
            and not missing_topic_methods
        )
        hook_ready = (
            hook_contract.get("variant_count") == 3
            and hook_contract.get("requires_tension_score") is True
            and hook_contract.get("selection_mode") == "auto_highest_tension"
            and not missing_hook_functions
        )
        script_generator_exports_writer = getattr(script_generator, "run", None) is getattr(script_writer, "run", None)
        script_ready = (
            script_contract.get("topic_intelligence_source") == "SQLite intelligence.db"
            and script_contract.get("requires_hook_artifact") is True
            and script_contract.get("style_guide_reader") == "atnos.memory.obsidian_style_guide.read_channel_style_guide"
            and script_contract.get("faceless_playbook_source") == "atnos.production.faceless_playbook.build_faceless_playbook"
            and "topic_intelligence" in set(script_contract.get("records_usage_metadata") or [])
            and "style_guide" in set(script_contract.get("records_usage_metadata") or [])
            and "faceless_playbook" in set(script_contract.get("records_usage_metadata") or [])
            and script_generator_exports_writer
            and not missing_script_functions
        )
        ready = scoring_ready and hook_ready and script_ready
        blockers: list[str] = []
        if not scoring_ready:
            blockers.append("topic_scoring_contract")
        if not hook_ready:
            blockers.append("hook_generation_contract")
        if not script_ready:
            blockers.append("script_generation_contract")
        return _item(
            "script_generation_contract",
            "Topic scorer -> hook -> script contract",
            "ready" if ready else "missing",
            True,
            "Topic scoring, hook generation, and script generation use real signals, local Ollama, and Obsidian style guidance."
            if ready
            else f"Script generation contract is incomplete: {', '.join(blockers)}.",
            {
                "blockers": blockers,
                "topic_scoring_contract": topic_contract,
                "hook_generation_contract": hook_contract,
                "script_generation_contract": script_contract,
                "missing_topic_methods": missing_topic_methods,
                "missing_hook_functions": missing_hook_functions,
                "missing_script_functions": missing_script_functions,
                "script_generator_exports_script_writer_run": script_generator_exports_writer,
                "pipeline_order": ["TopicScorer.generate_topics", "hook_generator.run", "script_generator.run"],
            },
        )

    def _check_qa_contract(self) -> ReadinessItem:
        from atnos.production.qa_gate import ADVISORY_QA_CHECK_NAMES, REQUIRED_QA_CHECK_NAMES

        required = list(REQUIRED_QA_CHECK_NAMES)
        advisory = list(ADVISORY_QA_CHECK_NAMES)
        required_contract_checks = {
            "Hook 3-second test",
            "Final MP4 present",
            "Final MP4 audio stream",
            "Visual variety",
            "Visual topic relevance",
            "Generated image text risk",
            "Generated image topic match",
            "Channel visual floor",
            "Operating standards evidence",
            "Ollama QA review",
        }
        missing_contract_checks = sorted(required_contract_checks - set(required))
        ready = (
            len(required) >= len(required_contract_checks)
            and not missing_contract_checks
        )
        return _item(
            "qa_contract",
            f"{len(required)}-check QA gate contract",
            "ready" if ready else "missing",
            True,
            (
                "QA gate has required checks for hook timing, FFprobe media checks, visual variety, "
                "visual topic relevance, channel visual floor, operating standards evidence and local Ollama review."
            )
            if ready
            else "QA gate contract is incomplete.",
            {
                "required_check_count": len(required),
                "required_checks": required,
                "required_contract_checks": sorted(required_contract_checks),
                "missing_contract_checks": missing_contract_checks,
                "advisory_check_count": len(advisory),
                "advisory_checks": advisory,
                "uses_ffprobe": True,
                "uses_ollama": "Ollama QA review" in required,
                "uses_visual_topic_relevance": "Visual topic relevance" in required,
                "uses_generated_image_text_audit": "Generated image text risk" in required,
                "uses_generated_image_visual_audit": "Generated image topic match" in required,
                "uses_operating_standards_evidence": "Operating standards evidence" in required,
            },
        )

    def _check_asset_planner_contract(self) -> ReadinessItem:
        from atnos.production.steps import asset_planner, visual_planner

        required_functions = ("run", "_llm_or_fallback", "_style_guide_prompt_block", "_normalize_scene")
        missing_functions = [name for name in required_functions if not hasattr(visual_planner, name)]
        asset_planner_exports_visual_planner = getattr(asset_planner, "run", None) is getattr(visual_planner, "run", None)
        ready = bool(asset_planner_exports_visual_planner and not missing_functions)
        return _item(
            "asset_planner_contract",
            "Asset Planner visual prompts",
            "ready" if ready else "missing",
            True,
            "Asset Planner generates scene-level ComfyUI prompts with Obsidian style guidance and max-3-word headlines."
            if ready
            else "Asset Planner contract is incomplete.",
            {
                "module": "atnos.production.steps.visual_planner",
                "asset_planner_exports_visual_planner_run": asset_planner_exports_visual_planner,
                "missing_functions": missing_functions,
                "style_guide_reader": "atnos.memory.obsidian_style_guide.read_channel_style_guide",
                "records_style_guide_usage": True,
                "prompt_target": "ComfyUI scene prompts for LTX/Wan video generation",
                "headline_max_words": 3,
                "forbids_text_inside_generated_images": True,
                "forbids_competitor_copying": True,
                "scene_fields": [
                    "scene_id",
                    "script_lines",
                    "duration_s",
                    "headline",
                    "visual_prompt",
                    "overlay_text",
                    "risk_notes",
                ],
            },
        )

    async def _check_ollama(self, *, include_live_checks: bool) -> ReadinessItem:
        from atnos.services.ollama_model_roles_service import OllamaModelRolesService

        endpoint = self.settings.ollama_base_url.rstrip("/")
        roles = OllamaModelRolesService(self.settings)
        base_evidence = {
            "endpoint": endpoint,
            "main_text_model": roles.main_text_model,
            "fast_text_model": roles.fast_text_model,
            "vision_model": roles.vision_model,
            "required_roles": {
                "script_hook_qa_asset_planning": roles.main_text_model,
                "fast_fallback": roles.fast_text_model,
                "vision_review": roles.vision_model,
            },
            "pull_commands": [
                f"ollama pull {roles.main_text_model}",
                f"ollama pull {roles.fast_text_model}",
                f"ollama pull {roles.vision_model}",
            ],
            "roles_service": "atnos.services.ollama_model_roles_service.OllamaModelRolesService.probe",
        }
        if not include_live_checks:
            return _item(
                "ollama",
                "Ollama local LLM",
                "degraded",
                True,
                "Live Ollama check skipped. Required model roles and pull commands are reported in evidence.",
                base_evidence,
            )
        try:
            snapshot = await asyncio.to_thread(roles.probe)
        except Exception as exc:
            return _item("ollama", "Ollama local LLM", "missing", True, str(exc), base_evidence)
        evidence = {
            **base_evidence,
            "role_snapshot": snapshot.to_dict(),
        }
        return _item(
            "ollama",
            "Ollama local LLM",
            "ready" if snapshot.ready else "missing",
            True,
            snapshot.reason,
            evidence,
        )

    async def _check_audio_stack(self, *, include_live_checks: bool = False) -> ReadinessItem:
        from atnos.production.steps import assembler
        from atnos.production.steps import voice_synthesis

        pyttsx3_ready = importlib.util.find_spec("pyttsx3") is not None
        coqui_importable = importlib.util.find_spec("TTS") is not None
        kokoro_importable = KokoroLocalRuntimeService(self.settings).available_python_module() is not None
        kokoro_assets = KokoroLocalRuntimeService(self.settings).resolve_assets()
        kokoro_assets_ready = kokoro_assets is not None
        coqui_declared = bool(self.settings.coqui_tts_model)
        f5_endpoint_configured = bool(self.settings.f5_tts_base_url)
        elevenlabs_voice_ids = voice_synthesis._elevenlabs_voice_ids_by_profile(self.settings)
        elevenlabs_profile_voice_configured = {key: bool(value) for key, value in elevenlabs_voice_ids.items()}
        elevenlabs_any_voice_configured = bool(self.settings.elevenlabs_voice_id or any(elevenlabs_voice_ids.values()))
        elevenlabs_configured = bool(
            self.settings.shorts_allow_cloud_tts
            and self.settings.elevenlabs_api_key
            and elevenlabs_any_voice_configured
        )
        openai_api_key_configured = bool(self.settings.openai_api_key)
        openai_tts_configured = bool(self.settings.shorts_allow_cloud_tts and self.settings.openai_api_key)
        f5_live = await self._probe_f5_tts() if include_live_checks and f5_endpoint_configured else {
            "checked": False,
            "ready": False,
            "status_code": None,
            "error": "live check skipped" if not include_live_checks else "F5-TTS endpoint is not configured.",
        }
        elevenlabs_live = await self._probe_elevenlabs_tts() if include_live_checks and elevenlabs_configured else {
            "checked": False,
            "ready": False,
            "status_code": None,
            "error": "live check skipped" if not include_live_checks else "ElevenLabs is not fully configured.",
        }
        openai_tts_live = await self._probe_openai_tts() if include_live_checks and openai_tts_configured else {
            "checked": False,
            "ready": False,
            "status_code": None,
            "error": "live check skipped" if not include_live_checks else "OpenAI TTS is not configured.",
        }
        kokoro_local_live = await self._probe_kokoro_local_tts() if include_live_checks else {
            "checked": False,
            "ready": False,
            "status_code": None,
            "error": "live check skipped" if not include_live_checks else "Kokoro local live probe skipped.",
        }
        required_functions = (
            "_try_elevenlabs_tts",
            "_try_openai_tts",
            "_try_f5_tts",
            "_try_kokoro_local_tts",
            "_try_coqui_tts",
            "_try_edge_tts",
            "_try_pyttsx3",
            "_generate_subtitles",
            "_normalize_audio",
        )
        missing_functions = [name for name in required_functions if not hasattr(voice_synthesis, name)]
        target_audio_ready = (
            getattr(voice_synthesis, "TARGET_SAMPLE_RATE", None) == 44100
            and getattr(voice_synthesis, "TARGET_CHANNELS", None) == 2
        )
        pyttsx3_auto_install_package = getattr(voice_synthesis, "PYTTSX3_PACKAGE", "pyttsx3")
        pyttsx3_auto_install_available = hasattr(voice_synthesis, "_auto_install") and bool(pyttsx3_auto_install_package)
        ready = bool(
            f5_endpoint_configured
            and coqui_declared
            and (pyttsx3_ready or pyttsx3_auto_install_available)
            and not missing_functions
            and target_audio_ready
        )
        if ready:
            if pyttsx3_ready:
                message = "Voice stack has F5-TTS primary, Coqui/pyttsx3 fallbacks, subtitles, and 44100 Hz stereo normalization."
            else:
                message = "Voice stack is configured; pyttsx3 fallback will be auto-installed on first use if needed."
        elif not f5_endpoint_configured:
            message = "F5-TTS endpoint is not configured."
        elif missing_functions:
            message = f"Voice synthesis implementation is missing functions: {', '.join(missing_functions)}."
        elif not target_audio_ready:
            message = "Voice synthesis normalization target is not 44100 Hz stereo."
        elif not pyttsx3_auto_install_available:
            message = "pyttsx3 fallback package is missing and auto-install is unavailable."
        else:
            message = "Coqui TTS model is not configured."
        return _item(
            "audio_stack",
            "F5-TTS + Coqui + pyttsx3 fallback",
            "ready" if ready else "missing",
            True,
            message,
            {
                "env_keys": ["F5_TTS_BASE_URL", "COQUI_TTS_MODEL"],
                "local_public_voice_env_keys": ["KOKORO_MODEL_PATH", "KOKORO_VOICES_PATH", "KOKORO_VOICE_NAME"],
                "premium_env_keys": [
                    "SHORTS_ALLOW_CLOUD_TTS",
                    "ELEVENLABS_API_KEY",
                    "ELEVENLABS_VOICE_ID",
                    "ELEVENLABS_VOICE_ID_WEALTH_BLUEPRINT",
                    "ELEVENLABS_VOICE_ID_DARK_CHRONICLES",
                    "ELEVENLABS_VOICE_ID_SIGMA_SQUAD",
                    "OPENAI_API_KEY",
                    "OPENAI_TTS_MODEL",
                    "OPENAI_TTS_VOICE_WEALTH_BLUEPRINT",
                    "OPENAI_TTS_VOICE_DARK_CHRONICLES",
                    "OPENAI_TTS_VOICE_SIGMA_SQUAD",
                ],
                "f5_tts_base_url": self.settings.f5_tts_base_url,
                "f5_tts_configured": f5_endpoint_configured,
                "f5_tts_live_checked": bool(f5_live["checked"]),
                "f5_tts_live_ready": bool(f5_live["ready"]),
                "f5_tts_live_status_code": f5_live["status_code"],
                "f5_tts_live_error": f5_live["error"],
                "premium_voice": "atnos.production.steps.voice_synthesis._try_elevenlabs_tts",
                "premium_voice_publication_gate": bool(self.settings.shorts_require_premium_voice_before_publication),
                "elevenlabs_configured": elevenlabs_configured,
                "elevenlabs_allow_cloud_tts": bool(self.settings.shorts_allow_cloud_tts),
                "elevenlabs_api_key_configured": bool(self.settings.elevenlabs_api_key),
                "elevenlabs_global_voice_configured": bool(self.settings.elevenlabs_voice_id),
                "elevenlabs_profile_voice_configured": elevenlabs_profile_voice_configured,
                "elevenlabs_tts_live_checked": bool(elevenlabs_live["checked"]),
                "elevenlabs_tts_live_ready": bool(elevenlabs_live["ready"]),
                "elevenlabs_tts_live_status_code": elevenlabs_live["status_code"],
                "elevenlabs_tts_live_error": elevenlabs_live["error"],
                "openai_tts_configured": openai_tts_configured,
                "openai_tts_allow_cloud_tts": bool(self.settings.shorts_allow_cloud_tts),
                "openai_api_key_configured": openai_api_key_configured,
                "openai_tts_model": self.settings.openai_tts_model,
                "openai_tts_live_probe_enabled": bool(self.settings.shorts_tts_live_generation_probe),
                "openai_tts_live_checked": bool(openai_tts_live["checked"]),
                "openai_tts_live_ready": bool(openai_tts_live["ready"]),
                "openai_tts_live_status_code": openai_tts_live["status_code"],
                "openai_tts_live_error": openai_tts_live["error"],
                "openai_tts_profile_voices": voice_synthesis._openai_tts_voices_by_profile(self.settings),
                "kokoro_local_importable": kokoro_importable,
                "kokoro_local_assets_ready": kokoro_assets_ready,
                "kokoro_local_model_path": str(kokoro_assets.model_path) if kokoro_assets is not None else str(self.settings.kokoro_model_path),
                "kokoro_local_voices_path": str(kokoro_assets.voices_path) if kokoro_assets is not None else str(self.settings.kokoro_voices_path),
                "kokoro_local_voice_name": self.settings.kokoro_voice_name,
                "kokoro_local_live_checked": bool(kokoro_local_live["checked"]),
                "kokoro_local_live_ready": bool(kokoro_local_live["ready"]),
                "kokoro_local_live_status_code": kokoro_local_live["status_code"],
                "kokoro_local_live_error": kokoro_local_live["error"],
                "premium_publication_voice_ready": bool(
                    openai_tts_live["ready"]
                    or elevenlabs_live["ready"]
                    or f5_live["ready"]
                ),
                "local_fallback_publication_voice_ready": bool(kokoro_local_live["ready"]),
                "public_publication_voice_ready": bool(
                    openai_tts_live["ready"]
                    or elevenlabs_live["ready"]
                    or f5_live["ready"]
                    or (
                        not bool(self.settings.shorts_require_premium_voice_before_publication)
                        and kokoro_local_live["ready"]
                    )
                ),
                "f5_primary": "atnos.production.steps.voice_synthesis._try_f5_tts",
                "local_public_voice": "atnos.production.steps.voice_synthesis._try_kokoro_local_tts",
                "coqui_tts_model": self.settings.coqui_tts_model,
                "coqui_auto_install_package": getattr(voice_synthesis, "COQUI_TTS_PACKAGE", "TTS"),
                "kokoro_auto_install_packages": [
                    getattr(voice_synthesis, "KOKORO_ONNX_PACKAGE", "kokoro-onnx"),
                    getattr(voice_synthesis, "ESPEAKNG_LOADER_PACKAGE", "espeakng-loader"),
                ],
                "coqui_importable": coqui_importable,
                "pyttsx3_auto_install_package": pyttsx3_auto_install_package,
                "pyttsx3_auto_install_available": pyttsx3_auto_install_available,
                "pyttsx3_importable": pyttsx3_ready,
                "fallback_order": [
                    "elevenlabs",
                    "openai_tts",
                    "f5_tts_http",
                    "kokoro_local",
                    "coqui_tts",
                    "edge_tts",
                    "pyttsx3",
                    "silent_wav",
                ],
                "missing_functions": missing_functions,
                "target_sample_rate_hz": getattr(voice_synthesis, "TARGET_SAMPLE_RATE", None),
                "target_channels": getattr(voice_synthesis, "TARGET_CHANNELS", None),
                "normalization": "loudnorm I=-14 TP=-1.5 LRA=11",
                "final_audio_mix_filter": getattr(assembler, "AUDIO_MIX_FILTER_TEMPLATE", ""),
                "subtitle_generation": "whisper_cpp primary, script_aligned_fallback",
                "burned_subtitle_renderer": "ASS UTF-8 Montserrat ExtraBold",
                "burned_subtitle_font_size": getattr(assembler, "ASS_SUBTITLE_FONT_SIZE", None),
                "burned_subtitle_max_words_per_cue": getattr(assembler, "ASS_SUBTITLE_MAX_WORDS", None),
                "burned_subtitle_max_duration_s": getattr(assembler, "ASS_SUBTITLE_MAX_DURATION_S", None),
            },
        )

    async def _probe_kokoro_local_tts(self) -> dict[str, Any]:
        from atnos.production.steps import voice_synthesis

        output_path = self.settings.data_dir / "tmp" / "voice_provider_kokoro_probe.wav"
        try:
            result = await voice_synthesis._try_kokoro_local_tts(
                self.settings,
                text="Atnos readiness test.",
                output_path=output_path,
                language_hint="en",
                voice_profile={
                    "channel_profile_key": "wealth_blueprint",
                    "language_code": "en",
                    "kokoro_voice": self.settings.kokoro_voice_name,
                    "kokoro_speed": 1.0,
                },
            )
            ready = bool(result.get("ok")) and output_path.exists() and output_path.stat().st_size > 1024
            return {
                "checked": True,
                "ready": ready,
                "status_code": None,
                "error": "" if ready else str(result.get("error") or "Kokoro local probe did not produce audio.")[:240],
            }
        except Exception as exc:
            return {"checked": True, "ready": False, "status_code": None, "error": str(exc)[:240]}

    async def _probe_f5_tts(self) -> dict[str, Any]:
        from atnos.production.steps import voice_synthesis

        endpoint = str(self.settings.f5_tts_base_url or "").strip()
        if not endpoint:
            return {"checked": False, "ready": False, "status_code": None, "error": "F5-TTS endpoint is not configured."}
        base_url = voice_synthesis._f5_gradio_base_url(endpoint)
        api_name = voice_synthesis._f5_gradio_api_name(self.settings)
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                response = await client.get(f"{base_url}/config")
            has_api = api_name in response.text
            ready = response.status_code == 200 and has_api
            return {
                "checked": True,
                "ready": ready,
                "status_code": response.status_code,
                "error": "" if ready else f"F5-TTS Gradio API '{api_name}' unavailable: HTTP {response.status_code}",
            }
        except Exception as exc:
            return {"checked": True, "ready": False, "status_code": None, "error": str(exc)[:240]}

    async def _probe_elevenlabs_tts(self) -> dict[str, Any]:
        from atnos.production.steps import voice_synthesis

        voice_id = voice_synthesis._preferred_elevenlabs_voice_id(
            self.settings,
            voice_profile={"channel_profile_key": "wealth_blueprint", "language_code": "en"},
        )
        if not self.settings.elevenlabs_api_key or not voice_id:
            return {"checked": False, "ready": False, "status_code": None, "error": "ElevenLabs API key or voice ID is missing."}
        endpoint = f"{self.settings.elevenlabs_base_url.rstrip('/')}/v1/text-to-speech/{voice_id}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(
                    endpoint,
                    params={"output_format": self.settings.elevenlabs_output_format},
                    headers={
                        "xi-api-key": self.settings.elevenlabs_api_key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    json={
                        "text": "Atnos readiness test.",
                        "model_id": self.settings.elevenlabs_model_id,
                        "language_code": "en",
                        "voice_settings": {
                            "stability": self.settings.elevenlabs_stability,
                            "similarity_boost": self.settings.elevenlabs_similarity_boost,
                            "style": self.settings.elevenlabs_style,
                            "speed": self.settings.elevenlabs_speed,
                            "use_speaker_boost": self.settings.elevenlabs_use_speaker_boost,
                        },
                    },
                )
            error = ""
            if response.status_code != 200:
                error = _safe_http_error_message(response)
            return {
                "checked": True,
                "ready": response.status_code == 200 and len(response.content or b"") > 1024,
                "status_code": response.status_code,
                "error": error,
            }
        except Exception as exc:
            return {"checked": True, "ready": False, "status_code": None, "error": str(exc)[:240]}

    async def _probe_openai_tts(self) -> dict[str, Any]:
        if not self.settings.shorts_tts_live_generation_probe:
            return {
                "checked": False,
                "ready": False,
                "status_code": None,
                "error": "live generation probe disabled; set SHORTS_TTS_LIVE_GENERATION_PROBE=true to verify.",
            }
        if not self.settings.openai_api_key:
            return {"checked": False, "ready": False, "status_code": None, "error": "OPENAI_API_KEY is missing."}
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {self.settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.openai_tts_model,
                        "voice": self.settings.openai_tts_voice_wealth_blueprint or self.settings.openai_tts_voice,
                        "input": "Atnos readiness test.",
                        "instructions": "Speak clearly and naturally.",
                        "response_format": self.settings.openai_tts_response_format,
                    },
                )
            error = ""
            if response.status_code != 200:
                error = _safe_http_error_message(response)
            return {
                "checked": True,
                "ready": response.status_code == 200 and len(response.content or b"") > 1024,
                "status_code": response.status_code,
                "error": error,
            }
        except Exception as exc:
            return {"checked": True, "ready": False, "status_code": None, "error": str(exc)[:240]}

    def _check_ffmpeg(self) -> ReadinessItem:
        ffmpeg_path = resolve_ffmpeg()
        ffprobe_path = resolve_ffprobe(ffmpeg_path)
        ready = bool(ffmpeg_path and ffprobe_path)
        if ready:
            message = "FFmpeg and FFprobe are available for media assembly and QA."
        elif ffmpeg_path:
            message = "FFmpeg is available, but FFprobe is missing. QA media probing is degraded."
        else:
            message = "FFmpeg/FFprobe media tooling is missing."
        return _item(
            "ffmpeg",
            "FFmpeg/FFprobe media tooling",
            "ready" if ready else "missing",
            True,
            message,
            {"ffmpeg": ffmpeg_path, "ffprobe": ffprobe_path},
        )

    def _check_whisper(self) -> ReadinessItem:
        runtime = WhisperCppSubtitleService(self.settings).runtime_availability()
        return _item(
            "whisper_srt",
            "Whisper SRT subtitles",
            "ready" if runtime.available else "degraded",
            True,
            runtime.reason,
            {
                "env_keys": ["WHISPER_CPP_EXE", "WHISPER_MODEL_PATH", "WHISPER_LANGUAGE"],
                "executable": runtime.executable_path,
                "model": runtime.model_path,
                "language": runtime.language,
                "fallback_when_unavailable": "script_aligned_fallback",
                "quality_gate": "voice_synthesis validates Whisper SRT overlap before accepting it",
            },
        )

    async def _check_video_stack(self, *, include_live_checks: bool) -> ReadinessItem:
        from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

        selected_backend = str(getattr(self.settings, "shorts_video_backend", "motion_graphic") or "motion_graphic")
        effective_backend = _effective_shorts_video_backend(self.settings)
        runtime = ComfyUiRuntimeService(self.settings)
        install_state = runtime.describe_install()
        install_evidence = {
            "selected_backend": selected_backend,
            "effective_backend": effective_backend,
            "openai_image_motion_ready": _openai_image_motion_ready(self.settings),
            "endpoint": self.settings.comfyui_base_url,
            "install_dir": str(install_state.install_dir) if install_state.install_dir else None,
            "install_exists": install_state.exists,
            "run_script": str(install_state.run_script) if install_state.run_script else None,
            "fallback_script": str(install_state.fallback_script) if install_state.fallback_script else None,
            "python_executable": str(install_state.python_executable) if install_state.python_executable else None,
            "main_script": str(install_state.main_script) if install_state.main_script else None,
            "checkpoint_dir": str(install_state.checkpoint_dir) if install_state.checkpoint_dir else None,
            "auto_launch_supported": bool(
                install_state.exists
                and install_state.install_dir
                and (
                    (install_state.python_executable and install_state.main_script)
                    or install_state.run_script
                    or install_state.fallback_script
                )
            ),
            "startup_service": "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.ensure_startup_ready",
        }
        if effective_backend in {"motion_graphic", "openai_image_motion"}:
            is_openai_image_motion = effective_backend == "openai_image_motion"
            return _item(
                "comfyui",
                "ComfyUI LTX/Wan video backend",
                "ready",
                True,
                "OpenAI image motion backend is selected; ComfyUI LTX/Wan is optional for this production mode."
                if is_openai_image_motion
                else "Stable local motion-graphics backend is selected; ComfyUI LTX/Wan is optional for this production mode.",
                {
                    **install_evidence,
                    "comfyui_required": False,
                    "local_video_backend": (
                        "atnos.production.steps.video_generator._try_generate_openai_image_motion_clip"
                        if is_openai_image_motion
                        else "atnos.production.steps.video_generator._write_motion_graphic_clip"
                    ),
                    "quality_gate": "requires fresh generated motion-graphics clips and blocks placeholders",
                },
            )
        if not include_live_checks:
            return _item(
                "comfyui",
                "ComfyUI LTX/Wan video backend",
                "degraded",
                True,
                "Live ComfyUI check skipped. Install/auto-launch capability is reported in evidence.",
                install_evidence,
            )
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.settings.comfyui_base_url.rstrip('/')}/system_stats")
            ready = response.status_code == 200
        except Exception as exc:
            return _item(
                "comfyui",
                "ComfyUI LTX/Wan video backend",
                "degraded",
                True,
                str(exc),
                install_evidence,
            )
        return _item(
            "comfyui",
            "ComfyUI LTX/Wan video backend",
            "ready" if ready else "degraded",
            True,
            "ComfyUI is reachable." if ready else f"ComfyUI returned HTTP {response.status_code}.",
            install_evidence,
        )

    async def _check_models(self) -> ReadinessItem:
        selected_backend = str(getattr(self.settings, "shorts_video_backend", "motion_graphic") or "motion_graphic")
        effective_backend = _effective_shorts_video_backend(self.settings)
        if effective_backend == "comfy_image_motion":
            from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService

            checkpoint = await asyncio.to_thread(
                ComfyUiImageGenerationService(self.settings).resolve_checkpoint_name
            )
            return _item(
                "models",
                "ComfyUI image checkpoint",
                "ready" if checkpoint else "degraded",
                True,
                f"Active ComfyUI image checkpoint: {checkpoint}."
                if checkpoint
                else "Active ComfyUI does not expose an image checkpoint.",
                {
                    "selected_backend": selected_backend,
                    "effective_backend": effective_backend,
                    "active_checkpoint": checkpoint,
                    "source": "live_comfyui_object_info",
                    "required_model_ids": [],
                },
            )
        if effective_backend in {"motion_graphic", "openai_image_motion"}:
            is_openai_image_motion = effective_backend == "openai_image_motion"
            return _item(
                "models",
                "HuggingFace model manager",
                "ready",
                True,
                "OpenAI image motion backend does not require LTX/Wan/SDXL model downloads for QA-passed renders."
                if is_openai_image_motion
                else "Motion-graphics Shorts backend does not require LTX/Wan/SDXL model downloads for local QA-passed renders.",
                {
                    "selected_backend": selected_backend,
                    "effective_backend": effective_backend,
                    "openai_image_motion_ready": _openai_image_motion_ready(self.settings),
                    "required_model_ids": [],
                    "optional_upgrade_model_ids": ["ltx_video", "ltx_vae", "t5_encoder", "wan_video_1_3b", "sdxl_base_vae"],
                    "auto_download_endpoint": "/api/models/ensure-pipeline/shorts_full",
                },
            )
        status = await ModelManager.get_instance(self.settings).get_status()
        models = status.get("models", {}) if isinstance(status, dict) else {}
        important = ["ltx_video", "ltx_vae", "t5_encoder", "wan_video_1_3b", "sdxl_base_vae"]
        missing = [
            model_id
            for model_id in important
            if not isinstance(models.get(model_id), dict) or models[model_id].get("status") != "ok"
        ]
        missing_details = [
            {
                "model_id": model_id,
                "name": str(models.get(model_id, {}).get("name", model_id)) if isinstance(models.get(model_id), dict) else model_id,
                "size_gb": float(models.get(model_id, {}).get("size_gb", 0.0)) if isinstance(models.get(model_id), dict) else 0.0,
                "status": str(models.get(model_id, {}).get("status", "missing")) if isinstance(models.get(model_id), dict) else "missing",
                "local_path": str(models.get(model_id, {}).get("local_path", "")) if isinstance(models.get(model_id), dict) else "",
            }
            for model_id in missing
        ]
        missing_total_gb = round(sum(float(item["size_gb"]) for item in missing_details), 2)
        missing_names = ", ".join(str(item["name"]) for item in missing_details)
        return _item(
            "models",
            "HuggingFace model manager",
            "ready" if not missing else "degraded",
            True,
            "Shorts video/image models are downloaded."
            if not missing
            else f"Brakuje modeli: {missing_names}. Szacowany download: {missing_total_gb:.2f} GB.",
            {
                "missing": missing,
                "missing_details": missing_details,
                "missing_total_gb": missing_total_gb,
                "auto_download_endpoint": "/api/models/ensure-pipeline/shorts_full",
                "pipelines": status.get("pipelines", {}) if isinstance(status, dict) else {},
            },
        )

    def _check_workflow_templates(self) -> ReadinessItem:
        from atnos.production.steps import thumbnail_generator, video_generator

        selected_backend = str(getattr(self.settings, "shorts_video_backend", "motion_graphic") or "motion_graphic")
        effective_backend = _effective_shorts_video_backend(self.settings)
        if effective_backend == "comfy_image_motion":
            return _item(
                "workflow_templates",
                "ComfyUI image-motion workflow",
                "ready",
                True,
                "Local image-motion builds a current ComfyUI workflow from the live image checkpoint.",
                {
                    "selected_backend": selected_backend,
                    "effective_backend": effective_backend,
                    "required_templates": [],
                    "local_video_backend": (
                        "atnos.production.steps.video_generator._try_generate_comfy_image_motion_clip"
                    ),
                    "missing_or_invalid": [],
                },
            )
        if effective_backend in {"motion_graphic", "openai_image_motion"}:
            is_openai_image_motion = effective_backend == "openai_image_motion"
            return _item(
                "workflow_templates",
                "ComfyUI workflow templates",
                "ready",
                True,
                "OpenAI image motion backend does not require ComfyUI LTX/Wan workflow templates."
                if is_openai_image_motion
                else "Motion-graphics Shorts backend does not require ComfyUI LTX/Wan workflow templates.",
                {
                    "selected_backend": selected_backend,
                    "effective_backend": effective_backend,
                    "openai_image_motion_ready": _openai_image_motion_ready(self.settings),
                    "required_templates": [],
                    "optional_templates": ["ltx_video", "wan_video", "thumbnail"],
                    "local_video_backend": (
                        "atnos.production.steps.video_generator._try_generate_openai_image_motion_clip"
                        if is_openai_image_motion
                        else "atnos.production.steps.video_generator._write_motion_graphic_clip"
                    ),
                    "local_thumbnail_backend": "atnos.production.steps.thumbnail_generator._generate_placeholder_png",
                    "missing_or_invalid": [],
                },
            )
        expected = {
            "ltx_video": video_generator._workflow_path(self.settings, "ltx"),
            "wan_video": video_generator._workflow_path(self.settings, "wan"),
            "thumbnail": thumbnail_generator._workflow_path(self.settings),
        }
        templates: dict[str, dict[str, Any]] = {}
        missing_or_invalid: list[str] = []
        for key, path in expected.items():
            try:
                if path.exists():
                    workflow = json.loads(path.read_text(encoding="utf-8"))
                elif key == "thumbnail":
                    workflow = thumbnail_generator._load_or_create_workflow(path)
                else:
                    mode = "wan" if key == "wan_video" else "ltx"
                    workflow = video_generator._load_or_create_workflow(path, mode)
                class_types = sorted(
                    {
                        str(node.get("class_type"))
                        for node in workflow.values()
                        if isinstance(node, dict) and node.get("class_type")
                    }
                )
                if "VHS_VideoCombine" not in class_types and ({"CreateVideo", "SaveVideo"} & set(class_types)):
                    class_types = sorted([*class_types, "VHS_VideoCombine"])
                validation = _validate_workflow_template(key, workflow, class_types)
                valid = validation["valid"]
                templates[key] = {
                    "path": str(path),
                    "exists": path.exists(),
                    "size_bytes": path.stat().st_size if path.exists() else 0,
                    "class_types": class_types,
                    "missing_class_types": validation["missing_class_types"],
                    "missing_placeholders": validation["missing_placeholders"],
                    "valid": valid,
                }
                if not valid:
                    missing_or_invalid.append(key)
            except Exception as exc:
                templates[key] = {
                    "path": str(path),
                    "exists": path.exists(),
                    "size_bytes": path.stat().st_size if path.exists() else 0,
                    "class_types": [],
                    "valid": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                missing_or_invalid.append(key)

        return _item(
            "workflow_templates",
            "ComfyUI workflow templates",
            "ready" if not missing_or_invalid else "missing",
            True,
            "LTX, Wan and thumbnail workflow templates are present and parseable."
            if not missing_or_invalid
            else f"Missing or invalid workflow templates: {', '.join(missing_or_invalid)}",
            {
                "selected_backend": selected_backend,
                "effective_backend": effective_backend,
                "openai_image_motion_ready": _openai_image_motion_ready(self.settings),
                "required_templates": list(expected.keys()),
                "templates": templates,
                "missing_or_invalid": missing_or_invalid,
            },
        )

    def _check_cost_guard(self) -> ReadinessItem:
        from atnos.production.cost_guard import CostGuard

        guard = CostGuard(self.settings)
        snapshot = guard.snapshot()
        estimate = guard.estimate_production_job()
        decision = guard.check_can_spend(
            estimate.estimated_cost_pln,
            operation="readiness_next_production_job",
        )
        if not bool(snapshot.get("enabled")):
            status = "degraded"
            message = "Cost guard is disabled; autonomous production can spend API budget without local enforcement."
        elif bool(decision.get("allowed")):
            status = "ready"
            message = "Cost guard is active and the next estimated production job fits within budget."
        else:
            status = "missing"
            message = "Cost guard is active and blocks the next estimated production job because budget is exhausted."
        return _item(
            "cost_guard",
            "API spend guard",
            status,
            True,
            message,
            {
                **snapshot,
                "next_job_estimated_cost_pln": estimate.estimated_cost_pln,
                "next_job_estimate_notes": list(estimate.notes),
                "next_job_decision": decision,
                "settings": {
                    "enabled_env": "ATNOS_COST_GUARD_ENABLED",
                    "monthly_limit_env": "ATNOS_MONTHLY_SPEND_LIMIT_PLN",
                    "daily_limit_env": "ATNOS_DAILY_SPEND_LIMIT_PLN",
                    "estimate_env": "ATNOS_ESTIMATED_COST_PER_PREMIUM_SHORT_PLN",
                },
            },
        )

    def _check_secret_vault(self) -> ReadinessItem:
        status = SecretVaultService(self.settings).get_storage_status()
        ready = status.storage_backend == AES_256_GCM_STORAGE_BACKEND and status.is_secret_storage_enabled
        return _item(
            "secret_vault",
            "AES-256 token store",
            "ready" if ready else "missing",
            True,
            status.summary,
            {"backend": status.storage_backend, "warnings": list(status.warnings)},
        )

    def _check_youtube(self) -> ReadinessItem:
        from atnos.services.oauth_provider_registry import OAuthProviderRegistry
        from atnos.services.runtime_settings_service import RuntimeSettingsService
        from atnos.services.google_oauth_client_secret_importer import diagnose_google_oauth_client_secret_file
        from atnos.services.youtube_connector_service import YouTubeConnectorService

        effective_settings = (
            RuntimeSettingsService(self.session, self.settings).build_effective_settings()
            if self.session is not None
            else self.settings
        )
        diagnostics_client_secret_file = effective_settings.youtube_client_secret_file
        if self.settings.app_env == "test" and self.settings.app_root:
            app_root = self.settings.app_root.resolve()
            try:
                diagnostics_client_secret_file.resolve().relative_to(app_root)
            except ValueError:
                diagnostics_client_secret_file = app_root / "client_secret.json"
        descriptor = OAuthProviderRegistry(effective_settings).get_connector("youtube-studio")
        client_secret_diagnostics = diagnose_google_oauth_client_secret_file(
            diagnostics_client_secret_file,
            redirect_base_url=effective_settings.oauth_redirect_base_url,
        )
        setup_evidence = {
            "client_secret_status_endpoint": "GET /oauth/youtube-studio/client-secret-status",
            "connect_endpoint": "POST /oauth/youtube-studio/connect",
            "import_client_secret_endpoint": "POST /oauth/youtube-studio/import-client-secret",
            "runtime_config_endpoint": "POST /oauth/youtube-studio/runtime-config",
            "oauth_start_endpoint": "POST /oauth/youtube-studio/start",
            "oauth_callback_path": "/oauth/youtube-studio/callback",
            "redirect_uri": descriptor.redirect_uri,
            "client_secret_file": str(diagnostics_client_secret_file),
            "client_secret_diagnostics": client_secret_diagnostics.to_dict(),
            "required_scopes": list(descriptor.scopes),
            "configured": (descriptor.is_configured or descriptor.mode == "fake_provider")
            and client_secret_diagnostics.import_ready,
            "enabled": descriptor.enabled,
            "setup_warnings": list(descriptor.setup_warnings),
            "token_store": {
                "service": "atnos.services.oauth_token_store.OAuthTokenStore",
                "vault_service": "atnos.services.secret_vault_service.SecretVaultService",
                "required_backend": AES_256_GCM_STORAGE_BACKEND,
            },
            "upload_contract": {
                "service": "atnos.services.youtube_connector_service.YouTubeConnectorService.upload_short",
                "privacy_status": "private",
                "resumable": True,
                "chunk_size_bytes": YouTubeConnectorService.VIDEO_UPLOAD_CHUNK_SIZE_BYTES,
                "retry_attempts": 3,
                "captions_supported": True,
                "thumbnail_supported": True,
            },
        }
        if self.session is None:
            return _item(
                "youtube_oauth",
                "YouTube OAuth/upload",
                "degraded",
                True,
                "YouTube live session check skipped.",
                setup_evidence,
            )
        status = YouTubeConnectorService(self.session, self.settings).get_status()
        granted = set(status.granted_scopes)
        required = set(descriptor.scopes)
        evidence = {
            **setup_evidence,
            **status.to_dict(),
            "missing_scopes": sorted(required - granted) if status.connected else sorted(required),
        }
        return _item(
            "youtube_oauth",
            "YouTube OAuth/upload",
            "ready" if status.ready else "missing",
            True,
            "YouTube connector is ready." if status.ready else "Connect YouTube Studio OAuth before private upload.",
            evidence,
        )

    def _check_scheduler(self) -> ReadinessItem:
        from atnos.services.scheduler_runtime_service import (
            INTELLIGENCE_REFRESH_INTERVAL_MINUTES,
            INTELLIGENCE_REFRESH_JOB_ID,
            PRODUCTION_AUTOPILOT_JOB_ID,
            PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID,
            PRODUCTION_ANALYTICS_FOLLOWUP_LIMIT,
            SCHEDULER_TICK_SECONDS,
            get_scheduler_runtime_status,
        )

        runtime = get_scheduler_runtime_status()
        internal_loop_running = bool(runtime.get("internal_loop_running"))
        apscheduler_running = bool(runtime.get("apscheduler_running"))
        ready = bool(internal_loop_running or self.settings.scheduled_tasks_enabled)
        if internal_loop_running:
            message = "Scheduler runtime loop is running; production analytics follow-ups and intelligence refresh are automatic."
        elif self.settings.scheduled_tasks_enabled:
            message = "Scheduled tasks are enabled; runtime should start APScheduler jobs during API lifespan."
        else:
            message = "Scheduler runtime is not active and scheduled tasks are disabled; analytics follow-ups will not run automatically."
        return _item(
            "scheduler",
            "APScheduler jobs",
            "ready" if ready else "degraded",
            True,
            message,
            {
                "scheduled_tasks_enabled": self.settings.scheduled_tasks_enabled,
                "active_runtime": runtime.get("active_runtime"),
                "internal_loop_running": internal_loop_running,
                "apscheduler_running": apscheduler_running,
                "system_job_ids": runtime.get("system_job_ids"),
                "runtime_loop_tick_seconds": SCHEDULER_TICK_SECONDS,
                "production_autopilot_enabled": self.settings.shorts_autopilot_enabled,
                "production_autopilot_interval_minutes": self.settings.shorts_autopilot_interval_minutes,
                "production_autopilot_max_jobs_per_tick": self.settings.shorts_autopilot_max_jobs_per_tick,
                "production_autopilot_min_topic_buffer": self.settings.shorts_autopilot_min_topic_buffer,
                "production_autopilot_generate_count": self.settings.shorts_autopilot_generate_count,
                "production_autopilot_auto_approve_min_score": self.settings.shorts_autopilot_auto_approve_min_score,
                "production_autopilot_best_effort_approval_enabled": self.settings.shorts_autopilot_best_effort_approval_enabled,
                "production_autopilot_best_effort_min_score": self.settings.shorts_autopilot_best_effort_min_score,
                "production_autopilot_auto_reject_below_score": self.settings.shorts_autopilot_auto_reject_below_score,
                "production_autopilot_require_shorts_potential": self.settings.shorts_autopilot_require_shorts_potential,
                "production_autopilot_block_clickbait_risk": self.settings.shorts_autopilot_block_clickbait_risk,
                "post_qa_review_dispatcher": True,
                "human_approval_before_private_upload": self.settings.shorts_require_human_approval_before_upload,
                "review_outbox": "data/production/{account_id}/{job_id}/review_notification.json",
                "private_upload_endpoint": "POST /api/production/jobs/{job_id}/upload-private",
                "public_upload_automatic": False,
                "public_promotion_endpoint": "POST /api/production/jobs/{job_id}/schedule-public",
                "public_promotion_requires_request_approval": True,
                "allow_public_upload_enabled": self.settings.allow_public_upload,
                "public_promotion_source_status": "uploaded_private",
                "production_autopilot_job_id": PRODUCTION_AUTOPILOT_JOB_ID,
                "production_autopilot_endpoint": "POST /api/production/content-loop/run-once",
                "production_analytics_followup_poll_seconds": SCHEDULER_TICK_SECONDS,
                "production_analytics_followup_batch_limit": PRODUCTION_ANALYTICS_FOLLOWUP_LIMIT,
                "production_analytics_followup_job_id": PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID,
                "production_analytics_windows_hours": [24, 48, 72],
                "intelligence_refresh_interval_minutes": INTELLIGENCE_REFRESH_INTERVAL_MINUTES,
                "intelligence_refresh_job_id": INTELLIGENCE_REFRESH_JOB_ID,
                "pollers": runtime.get("pollers"),
                "production_autopilot_automatic": runtime.get("production_autopilot_automatic"),
                "production_analytics_followups_automatic": runtime.get("production_analytics_followups_automatic"),
                "intelligence_refresh_automatic": runtime.get("intelligence_refresh_automatic"),
                "scheduled_task_cron_jobs_automatic": runtime.get("scheduled_task_cron_jobs_automatic"),
            },
        )

    def _check_public_upload_policy(self) -> ReadinessItem:
        enabled = bool(self.settings.allow_public_upload)
        approval_required = bool(self.settings.shorts_require_human_approval_before_upload)
        return _item(
            "public_upload_policy",
            "Public upload policy",
            "ready" if enabled else "degraded",
            False,
            (
                "Public promotion is enabled; public upload still requires the configured review/approval gate."
                if enabled
                else "Public promotion is disabled by ALLOW_PUBLIC_UPLOAD. Private review uploads can still work."
            ),
            {
                "allow_public_upload_enabled": enabled,
                "human_approval_before_upload": approval_required,
                "public_promotion_endpoint": "POST /api/production/jobs/{job_id}/schedule-public",
                "public_promotion_requires_request_approval": True,
                "private_review_flow_allowed": True,
                "env_key": "ALLOW_PUBLIC_UPLOAD",
            },
        )

    def _check_analytics_score_loop(self) -> ReadinessItem:
        from atnos.production.private_upload import FOLLOWUP_RETRY_DELAY_HOURS, FOLLOWUP_WINDOWS_HOURS
        from atnos.scoring.topic_scorer import TopicScorer
        from atnos.services.scheduler_runtime_service import PRODUCTION_ANALYTICS_FOLLOWUP_LIMIT

        path = intelligence_db_path(self.settings)
        required_topic_columns = {
            "last_youtube_video_id",
            "last_analytics_json",
            "analytics_history_json",
            "performance_score",
            "feedback_adjusted_score",
            "last_performance_recommendation",
            "analytics_updated_at",
        }
        required_followup_columns = {
            "job_id",
            "account_id",
            "topic_id",
            "youtube_video_id",
            "due_at",
            "window_hours",
            "status",
            "analytics_json",
            "score_update_json",
        }
        try:
            with connect(self.settings) as db:
                tables = {str(row["name"]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                topic_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(topic_queue)").fetchall()}
                followup_columns = {
                    str(row["name"])
                    for row in db.execute("PRAGMA table_info(production_analytics_followups)").fetchall()
                }
                latest_own_channel_runs = (
                    rows_to_dicts(
                        db.execute(
                            """
                            SELECT run_key, status, last_attempt_at, last_success_at, next_retry_at, message
                            FROM collection_runs
                            WHERE collector = 'own_channel'
                            ORDER BY last_attempt_at DESC
                            LIMIT 5
                            """
                        ).fetchall()
                    )
                    if "collection_runs" in tables
                    else []
                )
        except Exception as exc:
            return _item(
                "analytics_score_loop",
                "Analytics score updater loop",
                "missing",
                True,
                str(exc),
                {"path": str(path)},
            )

        missing_tables = sorted({"topic_queue", "production_analytics_followups"} - tables)
        missing_topic_columns = sorted(required_topic_columns - topic_columns)
        missing_followup_columns = sorted(required_followup_columns - followup_columns)
        windows_ready = tuple(FOLLOWUP_WINDOWS_HOURS) == (24, 48, 72)
        scorer_reads_feedback = hasattr(TopicScorer, "_score_performance_feedback")
        own_channel_access_errors = [
            run
            for run in latest_own_channel_runs
            if str(run.get("status") or "").casefold() == "error"
            and self._looks_like_youtube_analytics_access_error(str(run.get("message") or ""))
        ]
        analytics_project_id = next(
            (
                self._extract_google_project_id(str(run.get("message") or ""))
                for run in own_channel_access_errors
                if self._extract_google_project_id(str(run.get("message") or ""))
            ),
            "",
        )
        analytics_enable_url = "https://console.developers.google.com/apis/api/youtubeanalytics.googleapis.com/overview"
        if analytics_project_id:
            analytics_enable_url = f"{analytics_enable_url}?project={analytics_project_id}"
        analytics_api_access_configured = not own_channel_access_errors
        contract_ready = (
            not missing_tables
            and not missing_topic_columns
            and not missing_followup_columns
            and windows_ready
            and scorer_reads_feedback
        )
        ready = contract_ready and analytics_api_access_configured
        degraded = contract_ready and not analytics_api_access_configured
        if not contract_ready:
            message = "Analytics follow-up to topic scoring loop is incomplete."
        elif degraded:
            message = (
                "Analytics feedback loop can use YouTube Data API fallback for views/likes/comments, "
                "but YouTube Analytics API is disabled so retention/watch-time learning is unavailable."
            )
        else:
            message = "Private upload analytics follow-ups feed performance_score back into topic scoring."
        return _item(
            "analytics_score_loop",
            "Analytics score updater loop",
            "ready" if ready else "degraded" if degraded else "missing",
            True,
            message,
            {
                "path": str(path),
                "missing_tables": missing_tables,
                "missing_topic_queue_columns": missing_topic_columns,
                "missing_followup_columns": missing_followup_columns,
                "latest_own_channel_runs": latest_own_channel_runs,
                "own_channel_access_errors": own_channel_access_errors,
                "youtube_analytics_api_access_configured": analytics_api_access_configured,
                "data_api_fallback_available": contract_ready and not analytics_api_access_configured,
                "degraded_metrics_missing": [
                    "average_view_percentage",
                    "average_view_duration",
                    "estimated_minutes_watched",
                    "subscribers_gained",
                    "shares",
                ] if degraded else [],
                "youtube_analytics_api_project_id": analytics_project_id,
                "youtube_analytics_api_enable_url": analytics_enable_url,
                "followup_windows_hours": list(FOLLOWUP_WINDOWS_HOURS),
                "followup_batch_limit": PRODUCTION_ANALYTICS_FOLLOWUP_LIMIT,
                "followup_retry_delay_hours": FOLLOWUP_RETRY_DELAY_HOURS,
                "not_ready_behavior": "reschedule_scheduled_followup_instead_of_dead_pending_status",
                "due_row_claim_status": "running",
                "run_due_followups": "atnos.production.private_upload.run_due_analytics_followups",
                "topic_performance_columns": sorted(required_topic_columns),
                "scorer_uses_performance_feedback": scorer_reads_feedback,
            },
        )


    def _looks_like_youtube_analytics_access_error(self, message: str) -> bool:
        lowered = message.casefold()
        return (
            "youtube analytics api" in lowered
            and (
                "accessnotconfigured" in lowered
                or "disabled" in lowered
                or "has not been used" in lowered
                or "enable it" in lowered
            )
        )

    def _extract_google_project_id(self, message: str) -> str:
        match = re.search(r"(?:[?&]project=|\bproject\s+)(\d+)", message, flags=re.IGNORECASE)
        return match.group(1) if match else ""

    def _check_slack(self) -> ReadinessItem:
        configured = bool(self.settings.slack_webhook_url)
        return _item(
            "slack_webhooks",
            "Slack webhooks",
            "ready" if configured else "optional_missing",
            False,
            "Slack webhook is configured." if configured else "Slack webhook is optional and not configured.",
            {
                "enabled": self.settings.slack_notifications_enabled,
                "configured": configured,
                "env_keys": ["SLACK_NOTIFICATIONS_ENABLED", "SLACK_WEBHOOK_URL"],
                "notifier": "atnos.services.slack_webhook_notifier.SlackWebhookNotifier",
                "notification_scope": ["pipeline_errors", "qa_result", "private_upload", "analytics"],
                "event_sources": [
                    "atnos.production.orchestrator.ReelsProductionOrchestrator._emit",
                    "atnos.production.private_upload.upload_private_from_package",
                    "atnos.production.private_upload.run_analytics_followup",
                    "atnos.production.private_upload.run_due_analytics_followups",
                ],
                "webhook_url_redacted": "[configured]" if configured else None,
            },
        )

    def _check_obsidian_memory(self) -> ReadinessItem:
        vault = self.settings.obsidian_vault_path
        vault_path = Path(vault).resolve() if vault else None
        vault_exists = bool(vault_path and vault_path.exists() and vault_path.is_dir())
        shorts_dir = None
        style_guides: list[str] = []
        learning_notes: list[str] = []
        writer_enabled = bool(self.settings.obsidian_export_enabled and self.settings.atnos_write_obsidian_logs)
        shorts_dir_inside_vault = False
        if vault_path and vault_exists:
            shorts_dir = (vault_path / self.settings.atnos_obsidian_shorts_dir.strip()).resolve()
            shorts_dir_inside_vault = _path_is_inside(shorts_dir, vault_path)
            if shorts_dir_inside_vault and shorts_dir.exists():
                style_guides = [
                    str(path)
                    for path in sorted(shorts_dir.glob("*/style_guide.md"))
                    if path.is_file()
                ][:20]
                learning_notes = [
                    str(path)
                    for path in sorted(shorts_dir.glob("channel-learning-*.md"))
                    if path.is_file()
                ][:20]
        memory_sources_ready = bool(style_guides or learning_notes)
        ready = bool(vault_exists and shorts_dir_inside_vault and writer_enabled and memory_sources_ready)
        status = "ready" if ready else "degraded"
        if ready:
            message = "Obsidian vault is configured for style-guide reads and production log writes."
        elif not vault_exists:
            message = "Obsidian vault missing; local channel_ops fallback remains available."
        elif not writer_enabled:
            message = "Obsidian vault exists, but export/log writing is disabled."
        elif not shorts_dir_inside_vault:
            message = "Configured Obsidian shorts directory escapes the vault."
        else:
            message = "Obsidian vault exists, but no channel style guides or learning notes were found yet."
        return _item(
            "obsidian_memory",
            "Obsidian writer + style guide reader",
            status,
            False,
            message,
            {
                "vault_path": str(vault_path) if vault_path else None,
                "vault_exists": vault_exists,
                "export_enabled": bool(self.settings.obsidian_export_enabled),
                "write_logs_enabled": bool(self.settings.atnos_write_obsidian_logs),
                "shorts_dir": str(shorts_dir) if shorts_dir else None,
                "shorts_dir_inside_vault": shorts_dir_inside_vault,
                "style_guide_count": len(style_guides),
                "style_guides": style_guides,
                "learning_note_count": len(learning_notes),
                "learning_notes": learning_notes,
                "production_log_writer": "atnos.memory.obsidian_writer.write_production_log",
                "analytics_learning_writer": "atnos.memory.obsidian_writer.write_analytics_learning_note",
                "style_guide_reader": "atnos.memory.obsidian_style_guide.read_channel_style_guide",
                "style_guide_bootstrap": "atnos.memory.obsidian_style_guide.ensure_channel_style_guide",
                "reader_fallback_order": ["obsidian_style_guide", "latest_channel_learning_note", "local_channel_ops"],
            },
        )

    def _check_dependencies(self) -> ReadinessItem:
        modules = {
            "apscheduler": "apscheduler",
            "cryptography": "cryptography",
            "google_api_python_client": "googleapiclient",
            "google_auth": "google.auth",
            "google_auth_oauthlib": "google_auth_oauthlib",
            "huggingface_hub": "huggingface_hub",
            "imageio_ffmpeg": "imageio_ffmpeg",
            "pytrends": "pytrends",
            "pyttsx3": "pyttsx3",
            "dotenv": "dotenv",
        }
        missing = [name for name, module in modules.items() if importlib.util.find_spec(module) is None]
        loaded_env_files = [str(path) for path in load_runtime_env_files()]
        env_record = os.environ.get("ATNOS_LOADED_ENV_FILES", "")
        return _item(
            "runtime_dependencies",
            "Runtime Python dependencies",
            "ready" if not missing else "missing",
            True,
            "Required runtime dependencies are importable." if not missing else f"Missing packages: {', '.join(missing)}",
            {
                "missing": missing,
                "modules": modules,
                "dotenv_loader": "atnos.core.config.load_runtime_env_files",
                "loaded_env_files": loaded_env_files,
                "loaded_env_files_env": [item for item in env_record.split(os.pathsep) if item],
                "settings_env_file": ".env",
                "youtube_client_secret_file": str(self.settings.youtube_client_secret_file),
            },
        )


def _item(
    key: str,
    label: str,
    status: str,
    required: bool,
    message: str,
    evidence: dict[str, Any],
) -> ReadinessItem:
    return ReadinessItem(key=key, label=label, status=status, required=required, message=message, evidence=evidence)


def _validate_workflow_template(
    key: str,
    workflow: dict[str, Any],
    class_types: list[str],
) -> dict[str, Any]:
    required_classes = {
        "ltx_video": {
            "CheckpointLoaderSimple",
            "CLIPLoader",
            "CLIPTextEncode",
            "LTXVConditioning",
            "EmptyLTXVLatentVideo",
            "ModelSamplingLTXV",
            "KSampler",
            "VAEDecode",
            "CreateVideo",
            "SaveVideo",
        },
        "wan_video": {
            "CheckpointLoaderSimple",
            "CLIPTextEncode",
            "EmptyLatentVideo",
            "KSampler",
            "VAEDecode",
            "CreateVideo",
            "SaveVideo",
        },
        "thumbnail": {
            "CheckpointLoaderSimple",
            "CLIPTextEncode",
            "EmptyLatentImage",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        },
    }
    required_placeholders = {
        "ltx_video": {"{positive_prompt}", "{negative_prompt}", "{frame_count}", "{seed}"},
        "wan_video": {"{positive_prompt}", "{negative_prompt}", "{frame_count}", "{seed}"},
        "thumbnail": {"{checkpoint}", "{positive_prompt}", "{negative_prompt}", "{seed}"},
    }
    classes = set(class_types)
    serialized = json_dumps_for_readiness(workflow)
    missing_classes = sorted(required_classes.get(key, set()) - classes)
    missing_placeholders = sorted(
        placeholder for placeholder in required_placeholders.get(key, set()) if placeholder not in serialized
    )
    return {
        "valid": not missing_classes and not missing_placeholders,
        "missing_class_types": missing_classes,
        "missing_placeholders": missing_placeholders,
    }


def _openai_image_motion_ready(settings: Settings) -> bool:
    return bool(
        getattr(settings, "shorts_allow_cloud_visuals", False)
        and str(getattr(settings, "openai_api_key", "") or "").strip()
    )


def _effective_shorts_video_backend(settings: Settings) -> str:
    selected = str(getattr(settings, "shorts_video_backend", "motion_graphic") or "motion_graphic").strip().lower()
    if selected == "auto":
        return "auto"
    return selected


def json_dumps_for_readiness(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _build_operator_checklist(items: list[ReadinessItem]) -> list[dict[str, Any]]:
    by_key = {item.key: item for item in items}
    layers = [
        (
            "research",
            "Research signals",
            ("intelligence_db",),
            "Ustaw YOUTUBE_DATA_API_KEY albo polacz YouTube OAuth, zapisz nisze przez /accounts "
            "(intelligence_niches) albo POST /api/intelligence/accounts, poczekaj na scheduler albo wymus "
            "POST /api/intelligence/run, potem /api/topics/generate.",
        ),
        (
            "script",
            "Topic scoring + hook/script generation",
            ("topic_scoring", "production_modules", "script_generation_contract", "ollama"),
            "Uruchom Ollama i sprawdz modele tekstowe; bez tego fallback dziala, ale jakosc spada.",
        ),
        (
            "voice",
            "Voice + subtitles",
            ("audio_stack", "whisper_srt", "ffmpeg"),
            "Uruchom F5-TTS na skonfigurowanym endpointcie albo zostaw pyttsx3 jako awaryjny fallback.",
        ),
        (
            "video",
            "Video + thumbnails",
            ("asset_planner_contract", "comfyui", "models", "workflow_templates", "ffmpeg"),
            "Dla stabilnego trybu motion_graphic wystarczy FFmpeg i Pillow. Dla trybu AI-video uruchom one-shot prepare: "
            "POST /api/production/runtime/prepare z {\"start_comfyui\": true, \"ensure_models\": true, "
            "\"ensure_ollama_models\": true}; upewnij sie, ze workflow LTX/Wan/thumbnail sa gotowe.",
        ),
        (
            "qa",
            "Quality gate",
            ("production_modules", "qa_contract", "ffmpeg", "ollama"),
            "Upewnij sie, ze FFprobe/FFmpeg i Ollama dzialaja; QA wymaga probe mediow i lokalnej oceny.",
        ),
        (
            "cost",
            "API spend guard",
            ("cost_guard",),
            "Zwieksz ATNOS_MONTHLY_SPEND_LIMIT_PLN, zmniejsz koszt renderu albo poczekaj do kolejnego okresu budzetowego.",
        ),
        (
            "upload",
            "Private YouTube upload",
            ("secret_vault", "youtube_oauth", "scheduler"),
            "Wrzuć Google client_secret.json i uruchom POST /oauth/youtube-studio/connect, potem otwórz zwrócony authorization_url.",
        ),
        (
            "analytics",
            "24/48/72h analytics loop",
            ("scheduler", "youtube_oauth", "intelligence_db", "analytics_score_loop"),
            "Wlacz YouTube Analytics API w projekcie Google OAuth, odczekaj kilka minut na propagacje, "
            "potem uruchom /api/production/readiness?live=true i collector own_channel ponownie.",
        ),
        (
            "publication",
            "Manual public promotion",
            ("youtube_oauth", "scheduler"),
            "Po review prywatnego filmu wywolaj POST /api/production/jobs/{job_id}/schedule-public z approved=true; "
            "publiczny upload wymaga ALLOW_PUBLIC_UPLOAD=True.",
        ),
        (
            "memory",
            "Obsidian memory",
            ("obsidian_memory",),
            "Skonfiguruj vault Obsidian, jezeli chcesz style guide i production logs poza lokalnym fallbackiem.",
        ),
        (
            "infra",
            "Infra dependencies",
            ("runtime_dependencies", "slack_webhooks"),
            "Doinstaluj brakujace paczki; Slack webhook jest opcjonalny, ale pomaga w operacyjnym logowaniu.",
        ),
    ]
    checklist: list[dict[str, Any]] = []
    for key, label, item_keys, action in layers:
        layer_items = [by_key[item_key] for item_key in item_keys if item_key in by_key]
        blocking = [item for item in layer_items if item.required and item.status != "ready"]
        degraded = [item for item in layer_items if item.status in {"degraded", "optional_missing"} and item not in blocking]
        if blocking:
            severity = "blocking"
            ready = False
            detail = "; ".join(f"{item.label}: {item.message}" for item in blocking[:3])
        elif degraded:
            severity = "warning"
            ready = True
            detail = "; ".join(f"{item.label}: {item.message}" for item in degraded[:3])
        else:
            severity = "ready"
            ready = True
            detail = "Layer ready."
        checklist.append(
            {
                "key": key,
                "label": label,
                "ready": ready,
                "severity": severity,
                "items": [item.key for item in layer_items],
                "detail": detail,
                "action": action if severity != "ready" else "",
            }
        )
    return checklist


def _blocking_keys_for_layer(items: list[ReadinessItem], keys: tuple[str, ...]) -> list[str]:
    by_key = {item.key: item for item in items}
    blocking: list[str] = []
    for key in keys:
        item = by_key.get(key)
        if item and item.required and item.status != "ready":
            blocking.append(key)
    return blocking


def _non_ready_keys_for_layer(items: list[ReadinessItem], keys: tuple[str, ...]) -> list[str]:
    by_key = {item.key: item for item in items}
    blocking: list[str] = []
    for key in keys:
        item = by_key.get(key)
        if item and item.status != "ready":
            blocking.append(key)
    return blocking


def _safe_http_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:180]}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    raw_error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(raw_error, dict):
        code = str(raw_error.get("code") or raw_error.get("status") or "").strip()
        message = str(raw_error.get("message") or "").strip()
        if code and message:
            return f"{code}: {message[:220]}"
        if message:
            return message[:220]
        if code:
            return code
    if isinstance(detail, dict):
        status = str(detail.get("status") or "").strip()
        message = str(detail.get("message") or "").strip()
        if status and message:
            return f"{status}: {message[:220]}"
        if status:
            return status
    if isinstance(detail, str):
        return detail[:220]
    return f"HTTP {response.status_code}"
