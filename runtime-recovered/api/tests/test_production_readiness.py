from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, json_dumps, write_collection_run
from atnos.production.qa_gate import REQUIRED_QA_CHECK_NAMES
from atnos.production.readiness import ShortProductionReadinessService
from atnos.services.whisper_cpp_subtitle_service import WhisperCppRuntimeAvailability
from test_support import create_test_app


def test_production_readiness_endpoint_reports_layer_statuses(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "motion_graphic"
    asyncio.run(init_intelligence_db(app.state.settings))

    with TestClient(app) as client:
        response = client.get("/api/production/readiness?live=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ready", "ready_for_private_review", "needs_configuration"}
    assert payload["overall"] == payload["status"]
    assert payload["local_runtime_ready"] is False
    assert payload["publish_ready"] is False
    assert payload["full_pipeline_ready"] is False
    assert payload["private_review_ready"] is False
    assert payload["private_upload_ready"] is False
    assert payload["public_upload_enabled"] is False
    assert payload["public_publish_ready"] is False
    assert "public_upload_policy" in payload["public_publish_blocking"]
    by_key = {item["key"]: item for item in payload["items"]}

    assert by_key["intelligence_db"]["status"] == "missing"
    assert by_key["intelligence_db"]["evidence"]["empty_required_signal_tables"] == ["yt_signals", "gtrends_signals"]
    assert by_key["intelligence_db"]["evidence"]["latest_collection_runs"] == []
    assert by_key["intelligence_db"]["evidence"]["autonomous_refresh_configured_accounts"] == 0
    assert by_key["intelligence_db"]["evidence"]["autonomous_refresh_source"] == "intelligence_accounts"
    assert by_key["intelligence_db"]["evidence"]["account_seed_endpoint"] == "POST /accounts"
    assert "intelligence_niches" in by_key["intelligence_db"]["evidence"]["account_seed_fields"]
    assert by_key["intelligence_db"]["evidence"]["autonomous_config_endpoint"] == "POST /api/intelligence/accounts"
    assert by_key["intelligence_db"]["evidence"]["autonomous_config_list_endpoint"] == "GET /api/intelligence/accounts"
    assert by_key["intelligence_db"]["evidence"]["manual_collection_endpoint"] == "POST /api/intelligence/run"
    assert by_key["topic_scoring"]["status"] == "ready"
    assert by_key["production_modules"]["status"] == "ready"
    assert by_key["script_generation_contract"]["status"] == "ready"
    script_contract = by_key["script_generation_contract"]["evidence"]
    assert script_contract["topic_scoring_contract"]["signal_source"] == "SQLite intelligence.db"
    assert script_contract["topic_scoring_contract"]["generation_requires_real_signals"] is True
    assert script_contract["topic_scoring_contract"]["weights"]["viral_potential"] == 25
    assert script_contract["hook_generation_contract"]["variant_count"] == 3
    assert script_contract["hook_generation_contract"]["selection_mode"] == "auto_highest_tension"
    assert script_contract["script_generation_contract"]["topic_intelligence_source"] == "SQLite intelligence.db"
    assert script_contract["script_generation_contract"]["style_guide_reader"] == "atnos.memory.obsidian_style_guide.read_channel_style_guide"
    assert "topic_intelligence" in script_contract["script_generation_contract"]["records_usage_metadata"]
    assert "style_guide" in script_contract["script_generation_contract"]["records_usage_metadata"]
    assert "faceless_playbook" in script_contract["script_generation_contract"]["records_usage_metadata"]
    assert script_contract["script_generation_contract"]["faceless_playbook_source"] == "atnos.production.faceless_playbook.build_faceless_playbook"
    assert script_contract["script_generator_exports_script_writer_run"] is True
    assert script_contract["pipeline_order"] == ["TopicScorer.generate_topics", "hook_generator.run", "script_generator.run"]
    assert by_key["asset_planner_contract"]["status"] == "ready"
    asset_contract = by_key["asset_planner_contract"]["evidence"]
    assert asset_contract["asset_planner_exports_visual_planner_run"] is True
    assert asset_contract["style_guide_reader"] == "atnos.memory.obsidian_style_guide.read_channel_style_guide"
    assert asset_contract["prompt_target"] == "ComfyUI scene prompts for LTX/Wan video generation"
    assert asset_contract["headline_max_words"] == 3
    assert asset_contract["forbids_text_inside_generated_images"] is True
    assert asset_contract["forbids_competitor_copying"] is True
    assert by_key["qa_contract"]["status"] == "ready"
    assert by_key["qa_contract"]["evidence"]["required_check_count"] == len(REQUIRED_QA_CHECK_NAMES)
    assert "Ollama QA review" in by_key["qa_contract"]["evidence"]["required_checks"]
    assert "Channel visual floor" in by_key["qa_contract"]["evidence"]["required_checks"]
    assert "Visual topic relevance" in by_key["qa_contract"]["evidence"]["required_checks"]
    assert "Generated image text risk" in by_key["qa_contract"]["evidence"]["required_checks"]
    assert "Generated image topic match" in by_key["qa_contract"]["evidence"]["required_checks"]
    assert "Operating standards evidence" in by_key["qa_contract"]["evidence"]["required_checks"]
    assert by_key["qa_contract"]["evidence"]["uses_ffprobe"] is True
    assert by_key["qa_contract"]["evidence"]["uses_ollama"] is True
    assert by_key["qa_contract"]["evidence"]["uses_visual_topic_relevance"] is True
    assert by_key["qa_contract"]["evidence"]["uses_operating_standards_evidence"] is True
    assert by_key["ollama"]["status"] == "degraded"
    ollama_evidence = by_key["ollama"]["evidence"]
    assert ollama_evidence["endpoint"] == app.state.settings.ollama_base_url
    assert ollama_evidence["main_text_model"]
    assert ollama_evidence["fast_text_model"]
    assert ollama_evidence["vision_model"]
    assert ollama_evidence["required_roles"]["script_hook_qa_asset_planning"] == ollama_evidence["main_text_model"]
    assert f"ollama pull {ollama_evidence['main_text_model']}" in ollama_evidence["pull_commands"]
    assert f"ollama pull {ollama_evidence['vision_model']}" in ollama_evidence["pull_commands"]
    assert ollama_evidence["roles_service"] == "atnos.services.ollama_model_roles_service.OllamaModelRolesService.probe"
    assert by_key["secret_vault"]["status"] == "ready"
    assert by_key["scheduler"]["status"] in {"ready", "degraded"}
    assert by_key["scheduler"]["evidence"]["production_analytics_followup_poll_seconds"] == 10
    assert "active_runtime" in by_key["scheduler"]["evidence"]
    assert "internal_loop_running" in by_key["scheduler"]["evidence"]
    assert "production_autopilot_automatic" in by_key["scheduler"]["evidence"]
    assert "production_analytics_followups_automatic" in by_key["scheduler"]["evidence"]
    assert "intelligence_refresh_automatic" in by_key["scheduler"]["evidence"]
    assert by_key["scheduler"]["evidence"]["runtime_loop_tick_seconds"] == 10
    assert by_key["scheduler"]["evidence"]["production_autopilot_enabled"] is True
    assert by_key["scheduler"]["evidence"]["production_autopilot_interval_minutes"] == 15
    assert by_key["scheduler"]["evidence"]["production_autopilot_max_jobs_per_tick"] == 1
    assert by_key["scheduler"]["evidence"]["production_autopilot_min_topic_buffer"] == 5
    assert by_key["scheduler"]["evidence"]["production_autopilot_generate_count"] == 20
    assert by_key["scheduler"]["evidence"]["production_autopilot_auto_approve_min_score"] == 75.0
    assert by_key["scheduler"]["evidence"]["production_autopilot_best_effort_approval_enabled"] is True
    assert by_key["scheduler"]["evidence"]["production_autopilot_best_effort_min_score"] == 50.0
    assert by_key["scheduler"]["evidence"]["production_autopilot_auto_reject_below_score"] == 30.0
    assert by_key["scheduler"]["evidence"]["production_autopilot_require_shorts_potential"] is True
    assert by_key["scheduler"]["evidence"]["production_autopilot_block_clickbait_risk"] is True
    assert by_key["scheduler"]["evidence"]["production_autopilot_job_id"] == "system:production-autopilot"
    assert by_key["scheduler"]["evidence"]["production_autopilot_endpoint"] == "POST /api/production/content-loop/run-once"
    assert by_key["scheduler"]["evidence"]["production_analytics_followup_batch_limit"] == 5
    assert by_key["scheduler"]["evidence"]["production_analytics_followup_job_id"] == "system:production-analytics-followups"
    assert by_key["scheduler"]["evidence"]["production_analytics_windows_hours"] == [24, 48, 72]
    assert by_key["scheduler"]["evidence"]["intelligence_refresh_interval_minutes"] == 15
    assert by_key["scheduler"]["evidence"]["intelligence_refresh_job_id"] == "system:intelligence-refresh"
    assert "production_analytics_followups" in by_key["scheduler"]["evidence"]["pollers"]
    assert "production_autopilot" in by_key["scheduler"]["evidence"]["pollers"]
    assert "intelligence_refresh" in by_key["scheduler"]["evidence"]["pollers"]
    assert by_key["analytics_score_loop"]["status"] == "ready"
    assert by_key["analytics_score_loop"]["evidence"]["followup_windows_hours"] == [24, 48, 72]
    assert by_key["analytics_score_loop"]["evidence"]["followup_batch_limit"] == 5
    assert by_key["analytics_score_loop"]["evidence"]["followup_retry_delay_hours"] == 1
    assert by_key["analytics_score_loop"]["evidence"]["not_ready_behavior"] == "reschedule_scheduled_followup_instead_of_dead_pending_status"
    assert by_key["analytics_score_loop"]["evidence"]["due_row_claim_status"] == "running"
    assert by_key["analytics_score_loop"]["evidence"]["scorer_uses_performance_feedback"] is True
    assert "performance_score" in by_key["analytics_score_loop"]["evidence"]["topic_performance_columns"]
    assert "feedback_adjusted_score" in by_key["analytics_score_loop"]["evidence"]["topic_performance_columns"]
    assert by_key["audio_stack"]["evidence"]["fallback_order"] == [
        "elevenlabs",
        "openai_tts",
        "f5_tts_http",
        "kokoro_local",
        "coqui_tts",
        "edge_tts",
        "pyttsx3",
        "silent_wav",
    ]
    assert by_key["audio_stack"]["evidence"]["env_keys"] == ["F5_TTS_BASE_URL", "COQUI_TTS_MODEL"]
    assert by_key["audio_stack"]["evidence"]["local_public_voice_env_keys"] == [
        "KOKORO_MODEL_PATH",
        "KOKORO_VOICES_PATH",
        "KOKORO_VOICE_NAME",
    ]
    assert by_key["audio_stack"]["evidence"]["premium_env_keys"] == [
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
    ]
    assert by_key["audio_stack"]["evidence"]["premium_voice_publication_gate"] is True
    assert set(by_key["audio_stack"]["evidence"]["elevenlabs_profile_voice_configured"]) == {
        "wealth_blueprint",
        "dark_chronicles",
        "sigma_squad",
    }
    assert by_key["audio_stack"]["evidence"]["coqui_auto_install_package"] == "TTS>=0.22,<1.0"
    assert by_key["audio_stack"]["evidence"]["pyttsx3_auto_install_package"] == "pyttsx3>=2.99,<3.0"
    assert by_key["audio_stack"]["evidence"]["target_sample_rate_hz"] == 44100
    assert by_key["audio_stack"]["evidence"]["target_channels"] == 2
    assert by_key["audio_stack"]["evidence"]["subtitle_generation"] == "whisper_cpp primary, script_aligned_fallback"
    assert by_key["whisper_srt"]["required"] is True
    assert by_key["whisper_srt"]["evidence"]["env_keys"] == ["WHISPER_CPP_EXE", "WHISPER_MODEL_PATH", "WHISPER_LANGUAGE"]
    assert by_key["whisper_srt"]["evidence"]["fallback_when_unavailable"] == "script_aligned_fallback"
    assert by_key["runtime_dependencies"]["status"] == "ready"
    runtime_dependencies = by_key["runtime_dependencies"]["evidence"]
    assert runtime_dependencies["dotenv_loader"] == "atnos.core.config.load_runtime_env_files"
    assert runtime_dependencies["settings_env_file"] == ".env"
    assert runtime_dependencies["youtube_client_secret_file"] == str(app.state.settings.youtube_client_secret_file)
    assert "dotenv" in runtime_dependencies["modules"]
    assert by_key["workflow_templates"]["status"] == "ready"
    workflow_templates = by_key["workflow_templates"]["evidence"]
    assert workflow_templates["selected_backend"] == "motion_graphic"
    assert workflow_templates["required_templates"] == []
    assert workflow_templates["missing_or_invalid"] == []
    assert by_key["public_upload_policy"]["status"] == "degraded"
    assert by_key["public_upload_policy"]["required"] is False
    assert by_key["public_upload_policy"]["evidence"]["allow_public_upload_enabled"] is False
    assert by_key["public_upload_policy"]["evidence"]["private_review_flow_allowed"] is True
    assert by_key["youtube_oauth"]["status"] in {"missing", "degraded"}
    youtube_evidence = by_key["youtube_oauth"]["evidence"]
    assert youtube_evidence["client_secret_status_endpoint"] == "GET /oauth/youtube-studio/client-secret-status"
    assert youtube_evidence["connect_endpoint"] == "POST /oauth/youtube-studio/connect"
    assert youtube_evidence["import_client_secret_endpoint"] == "POST /oauth/youtube-studio/import-client-secret"
    assert youtube_evidence["runtime_config_endpoint"] == "POST /oauth/youtube-studio/runtime-config"
    assert youtube_evidence["oauth_start_endpoint"] == "POST /oauth/youtube-studio/start"
    assert youtube_evidence["oauth_callback_path"] == "/oauth/youtube-studio/callback"
    assert youtube_evidence["client_secret_file"]
    assert youtube_evidence["client_secret_diagnostics"]["exists"] is False
    assert youtube_evidence["client_secret_diagnostics"]["import_ready"] is False
    assert "https://www.googleapis.com/auth/youtube.upload" in youtube_evidence["required_scopes"]
    assert "https://www.googleapis.com/auth/yt-analytics.readonly" in youtube_evidence["required_scopes"]
    assert "missing_scopes" in youtube_evidence
    assert youtube_evidence["token_store"]["required_backend"] == "aes_256_gcm"
    assert youtube_evidence["upload_contract"]["privacy_status"] == "private"
    assert youtube_evidence["upload_contract"]["resumable"] is True
    assert youtube_evidence["upload_contract"]["chunk_size_bytes"] == 1024 * 1024
    assert youtube_evidence["upload_contract"]["captions_supported"] is True
    assert youtube_evidence["upload_contract"]["thumbnail_supported"] is True
    assert by_key["comfyui"]["status"] == "ready"
    comfy_evidence = by_key["comfyui"]["evidence"]
    assert comfy_evidence["selected_backend"] == "motion_graphic"
    assert comfy_evidence["comfyui_required"] is False
    assert comfy_evidence["endpoint"]
    assert "auto_launch_supported" in comfy_evidence
    assert comfy_evidence["startup_service"] == "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.ensure_startup_ready"
    assert "install_exists" in comfy_evidence
    assert "main_script" in comfy_evidence
    assert "ollama" in payload["blocking"]
    assert "intelligence_db" in payload["blocking"]
    assert "comfyui" not in payload["blocking"]
    assert "models" not in payload["blocking"]
    assert "youtube_oauth" in payload["blocking"]
    assert "youtube_oauth" not in payload["local_runtime_blocking"]
    assert "youtube_oauth" in payload["publish_blocking"]
    assert "intelligence_db" in payload["local_runtime_blocking"]
    assert "models" not in payload["local_runtime_blocking"]
    assert by_key["models"]["status"] == "ready"
    assert by_key["models"]["evidence"]["selected_backend"] == "motion_graphic"
    assert by_key["models"]["evidence"]["required_model_ids"] == []
    assert by_key["models"]["evidence"]["auto_download_endpoint"] == "/api/models/ensure-pipeline/shorts_full"
    checklist = {item["key"]: item for item in payload["operator_checklist"]}
    assert set(checklist) >= {"research", "script", "voice", "video", "qa", "upload", "analytics", "memory", "infra"}
    assert checklist["research"]["severity"] == "blocking"
    assert "YOUTUBE_DATA_API_KEY" in checklist["research"]["action"]
    assert "intelligence_niches" in checklist["research"]["action"]
    assert "POST /api/intelligence/accounts" in checklist["research"]["action"]
    assert "/api/intelligence" in checklist["research"]["action"]
    assert "script_generation_contract" in checklist["script"]["items"]
    assert checklist["video"]["severity"] == "ready"
    assert "asset_planner_contract" in checklist["video"]["items"]
    assert checklist["video"]["action"] == ""
    assert "qa_contract" in checklist["qa"]["items"]
    assert checklist["upload"]["severity"] == "blocking"
    assert "POST /oauth/youtube-studio/connect" in checklist["upload"]["action"]
    assert payload["next_actions"]
    assert all(isinstance(item, str) and item for item in payload["next_actions"])


def test_public_upload_runtime_switch_controls_readiness_endpoint(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "motion_graphic"
    asyncio.run(init_intelligence_db(app.state.settings))

    with TestClient(app) as client:
        before = client.get("/api/production/readiness?live=false")
        update = client.patch("/settings/control-center/runtime", json={"public_upload_enabled": True})
        after = client.get("/api/production/readiness?live=false")

    assert before.status_code == 200
    assert before.json()["public_upload_enabled"] is False
    assert update.status_code == 200
    assert update.json()["runtime"]["public_upload_enabled"] is True
    assert after.status_code == 200
    payload = after.json()
    assert payload["public_upload_enabled"] is True
    by_key = {item["key"]: item for item in payload["items"]}
    assert by_key["public_upload_policy"]["evidence"]["allow_public_upload_enabled"] is True


def test_production_readiness_reports_running_scheduler_runtime_loop(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/production/readiness?live=false")

    assert response.status_code == 200
    payload = response.json()
    scheduler = next(item for item in payload["items"] if item["key"] == "scheduler")
    assert scheduler["status"] == "ready"
    assert scheduler["evidence"]["active_runtime"] is True
    assert scheduler["evidence"]["internal_loop_running"] is True
    assert scheduler["evidence"]["production_autopilot_automatic"] is True
    assert scheduler["evidence"]["production_analytics_followups_automatic"] is True
    assert scheduler["evidence"]["intelligence_refresh_automatic"] is True


def test_production_readiness_accepts_include_live_checks_alias(tmp_path: Path, monkeypatch) -> None:
    calls: list[bool] = []

    async def fake_check(self, *, include_live_checks: bool = True):
        calls.append(include_live_checks)
        return {"status": "ok", "items": [], "blocking": []}

    monkeypatch.setattr(ShortProductionReadinessService, "check", fake_check)
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/production/readiness?live=true&include_live_checks=false")

    assert response.status_code == 200
    assert calls == [False]


def test_production_readiness_surfaces_latest_research_collector_status(tmp_path: Path) -> None:
    import asyncio

    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))
    write_collection_run(
        app.state.settings,
        run_key="youtube_trends:yt-finance:finanse osobiste:PL",
        collector="youtube_trends",
        status="needs_config",
        message="YouTube OAuth provider is not configured.",
    )

    payload = asyncio.run(ShortProductionReadinessService(app.state.settings).check(include_live_checks=False))
    intelligence = next(item for item in payload["items"] if item["key"] == "intelligence_db")

    assert intelligence["status"] == "missing"
    assert "Last youtube_trends collector status: needs_config." in intelligence["message"]
    assert intelligence["evidence"]["latest_collection_runs"][0]["collector"] == "youtube_trends"
    assert intelligence["evidence"]["latest_collection_runs"][0]["status"] == "needs_config"
    assert "not configured" in intelligence["evidence"]["latest_collection_runs"][0]["message"]


def test_production_readiness_surfaces_autonomous_intelligence_refresh_runs(tmp_path: Path) -> None:
    import asyncio

    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))
    write_collection_run(
        app.state.settings,
        run_key="intelligence_refresh:yt-finance",
        collector="intelligence_refresh",
        status="refreshed",
        message="Refreshed YouTube and Google Trends for 2 niches.",
        success=True,
    )

    payload = asyncio.run(ShortProductionReadinessService(app.state.settings).check(include_live_checks=False))
    intelligence = next(item for item in payload["items"] if item["key"] == "intelligence_db")

    assert intelligence["evidence"]["latest_collection_runs"][0]["collector"] == "intelligence_refresh"
    assert intelligence["evidence"]["latest_collection_runs"][0]["status"] == "refreshed"


def test_production_readiness_reports_intelligence_ready_when_signal_tables_have_data(tmp_path: Path) -> None:
    import asyncio

    app = create_test_app(tmp_path)
    app.state.settings.youtube_data_api_key = "youtube-data-key-test"
    asyncio.run(init_intelligence_db(app.state.settings))
    with connect(app.state.settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-ready-1', 'acct-1', 'finanse osobiste', 'PL', 'Inflacja i oszczednosci',
                100000, 1200, 30, '2026-06-01T00:00:00+00:00', 'UC1', 'Finance PL',
                'https://img.test/1.jpg', '2026-06-01T01:00:00+00:00'
            )
            """
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'inflacja oszczednosci', 'now 7-d', 'PL', 80, ?, '[]', 'ready',
                '2026-06-01T01:00:00+00:00'
            )
            """,
            (json_dumps(["lokaty", "konto oszczednosciowe"]),),
        )
        db.commit()

    item = ShortProductionReadinessService(app.state.settings)._check_intelligence_db()

    assert item.status == "ready"
    assert item.ready is True
    assert item.evidence["counts"]["yt_signals"] == 1
    assert item.evidence["counts"]["gtrends_signals"] == 1
    assert item.evidence["empty_required_signal_tables"] == []
    assert item.evidence["youtube_data_api_key_configured"] is True
    assert item.evidence["youtube_trends_collector_mode"] == "api_key"


def test_production_readiness_reports_analytics_score_loop_contract(tmp_path: Path) -> None:
    import asyncio

    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    item = ShortProductionReadinessService(app.state.settings)._check_analytics_score_loop()

    assert item.status == "ready"
    assert item.ready is True
    assert item.evidence["missing_tables"] == []
    assert item.evidence["missing_topic_queue_columns"] == []
    assert item.evidence["missing_followup_columns"] == []
    assert item.evidence["followup_windows_hours"] == [24, 48, 72]
    assert item.evidence["run_due_followups"] == "atnos.production.private_upload.run_due_analytics_followups"
    assert item.evidence["scorer_uses_performance_feedback"] is True
    assert "feedback_adjusted_score" in item.evidence["topic_performance_columns"]


def test_production_readiness_blocks_analytics_when_youtube_analytics_api_disabled(tmp_path: Path) -> None:
    import asyncio

    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))
    write_collection_run(
        app.state.settings,
        run_key="own_channel:yt-finanse-pl",
        collector="own_channel",
        status="error",
        message=(
            "YouTube Analytics API has not been used in project 265469749111 before or it is disabled. "
            "Enable it by visiting https://console.developers.google.com/apis/api/youtubeanalytics.googleapis.com/overview"
        ),
    )

    item = ShortProductionReadinessService(app.state.settings)._check_analytics_score_loop()

    assert item.status == "degraded"
    assert item.ready is False
    assert item.evidence["youtube_analytics_api_access_configured"] is False
    assert item.evidence["data_api_fallback_available"] is True
    assert "average_view_percentage" in item.evidence["degraded_metrics_missing"]
    assert item.evidence["own_channel_access_errors"][0]["run_key"] == "own_channel:yt-finanse-pl"
    assert item.evidence["youtube_analytics_api_project_id"] == "265469749111"
    assert item.evidence["youtube_analytics_api_enable_url"].endswith("?project=265469749111")
    assert "youtubeanalytics.googleapis.com" in item.evidence["youtube_analytics_api_enable_url"]
    assert "YouTube Data API fallback" in item.message


def test_production_readiness_reports_script_generation_contract(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    item = ShortProductionReadinessService(app.state.settings)._check_script_generation_contract()

    assert item.status == "ready"
    assert item.required is True
    assert item.evidence["blockers"] == []
    assert item.evidence["topic_scoring_contract"]["llm_scope"] == "local_ollama_only"
    assert item.evidence["topic_scoring_contract"]["weights"] == {
        "viral_potential": 25,
        "search_demand": 20,
        "competition_gap": 20,
        "trend_velocity": 15,
        "niche_fit": 10,
        "freshness": 10,
    }
    assert item.evidence["hook_generation_contract"]["variant_count"] == 3
    assert item.evidence["hook_generation_contract"]["requires_tension_score"] is True
    assert item.evidence["script_generation_contract"]["requires_hook_artifact"] is True
    assert item.evidence["script_generation_contract"]["forbidden_claim_policy"] == "do not invent market numbers, earnings, or performance claims"
    assert item.evidence["missing_topic_methods"] == []
    assert item.evidence["missing_hook_functions"] == []
    assert item.evidence["missing_script_functions"] == []


def test_production_readiness_reports_asset_planner_contract(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    item = ShortProductionReadinessService(app.state.settings)._check_asset_planner_contract()

    assert item.status == "ready"
    assert item.required is True
    assert item.evidence["asset_planner_exports_visual_planner_run"] is True
    assert item.evidence["missing_functions"] == []
    assert item.evidence["records_style_guide_usage"] is True
    assert item.evidence["headline_max_words"] == 3
    assert item.evidence["scene_fields"] == [
        "scene_id",
        "script_lines",
        "duration_s",
        "headline",
        "visual_prompt",
        "overlay_text",
        "risk_notes",
    ]


def test_production_readiness_reports_voice_contract(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    item = asyncio.run(ShortProductionReadinessService(app.state.settings)._check_audio_stack(include_live_checks=False))

    assert item.status == "ready"
    assert item.required is True
    assert item.evidence["f5_primary"] == "atnos.production.steps.voice_synthesis._try_f5_tts"
    assert item.evidence["premium_voice"] == "atnos.production.steps.voice_synthesis._try_elevenlabs_tts"
    assert item.evidence["openai_tts_model"]
    assert item.evidence["openai_tts_profile_voices"] == {
        "wealth_blueprint": item.evidence["openai_tts_profile_voices"]["wealth_blueprint"],
        "dark_chronicles": item.evidence["openai_tts_profile_voices"]["dark_chronicles"],
        "sigma_squad": item.evidence["openai_tts_profile_voices"]["sigma_squad"],
    }
    assert item.evidence["env_keys"] == ["F5_TTS_BASE_URL", "COQUI_TTS_MODEL"]
    assert item.evidence["f5_tts_configured"] is True
    assert item.evidence["f5_tts_live_checked"] is False
    assert item.evidence["f5_tts_live_ready"] is False
    assert item.evidence["local_public_voice"] == "atnos.production.steps.voice_synthesis._try_kokoro_local_tts"
    assert item.evidence["kokoro_local_live_checked"] is False
    assert item.evidence["kokoro_local_live_ready"] is False
    assert item.evidence["premium_publication_voice_ready"] is False
    assert item.evidence["local_fallback_publication_voice_ready"] is False
    assert item.evidence["public_publication_voice_ready"] is False
    assert item.evidence["fallback_order"] == [
        "elevenlabs",
        "openai_tts",
        "f5_tts_http",
        "kokoro_local",
        "coqui_tts",
        "edge_tts",
        "pyttsx3",
        "silent_wav",
    ]
    assert item.evidence["target_sample_rate_hz"] == 44100
    assert item.evidence["target_channels"] == 2
    assert item.evidence["normalization"] == "loudnorm I=-14 TP=-1.5 LRA=11"
    assert item.evidence["subtitle_generation"] == "whisper_cpp primary, script_aligned_fallback"


def test_production_readiness_allows_pyttsx3_auto_install_fallback(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    def fake_find_spec(name: str):
        if name == "pyttsx3":
            return None
        return object()

    with patch("atnos.production.readiness.importlib.util.find_spec", side_effect=fake_find_spec):
        item = asyncio.run(ShortProductionReadinessService(app.state.settings)._check_audio_stack(include_live_checks=False))

    assert item.status == "ready"
    assert item.evidence["pyttsx3_importable"] is False
    assert item.evidence["pyttsx3_auto_install_available"] is True
    assert item.evidence["pyttsx3_auto_install_package"] == "pyttsx3>=2.99,<3.0"
    assert "auto-installed" in item.message


def test_production_readiness_marks_public_voice_ready_only_when_f5_live(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)

    async def fake_probe(self):
        return {"checked": True, "ready": True, "status_code": 200, "error": ""}

    monkeypatch.setattr(ShortProductionReadinessService, "_probe_f5_tts", fake_probe)

    item = asyncio.run(ShortProductionReadinessService(app.state.settings)._check_audio_stack(include_live_checks=True))

    assert item.evidence["f5_tts_live_checked"] is True
    assert item.evidence["f5_tts_live_ready"] is True
    assert item.evidence["premium_publication_voice_ready"] is True
    assert item.evidence["public_publication_voice_ready"] is True


def test_f5_readiness_probe_uses_gradio_config_not_generation(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        status_code = 200
        text = '{"dependencies":[{"api_name":"basic_tts"}]}'

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str):
            calls.append(("GET", url))
            return FakeResponse()

        async def post(self, url: str, **kwargs):
            calls.append(("POST", url))
            raise AssertionError("F5 readiness must not trigger audio generation")

    monkeypatch.setattr("atnos.production.readiness.httpx.AsyncClient", FakeClient)

    result = asyncio.run(ShortProductionReadinessService(app.state.settings)._probe_f5_tts())

    assert result["checked"] is True
    assert result["ready"] is True
    assert calls == [("GET", "http://localhost:7860/config")]


def test_production_readiness_marks_public_voice_ready_when_openai_tts_live(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_allow_cloud_tts = True
    app.state.settings.openai_api_key = "test-openai-key"
    app.state.settings.shorts_tts_live_generation_probe = True

    async def fake_probe(self):
        return {"checked": True, "ready": True, "status_code": 200, "error": ""}

    monkeypatch.setattr(ShortProductionReadinessService, "_probe_openai_tts", fake_probe)

    item = asyncio.run(ShortProductionReadinessService(app.state.settings)._check_audio_stack(include_live_checks=True))

    assert item.evidence["openai_tts_configured"] is True
    assert item.evidence["openai_tts_live_probe_enabled"] is True
    assert item.evidence["openai_tts_live_checked"] is True
    assert item.evidence["openai_tts_live_ready"] is True
    assert item.evidence["premium_publication_voice_ready"] is True
    assert item.evidence["public_publication_voice_ready"] is True


def test_production_readiness_does_not_treat_kokoro_as_public_voice_when_premium_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)

    async def fake_kokoro_probe(self):
        return {"checked": True, "ready": True, "status_code": None, "error": ""}

    async def fake_f5_probe(self):
        return {"checked": True, "ready": False, "status_code": None, "error": "offline"}

    monkeypatch.setattr(ShortProductionReadinessService, "_probe_f5_tts", fake_f5_probe)
    monkeypatch.setattr(ShortProductionReadinessService, "_probe_kokoro_local_tts", fake_kokoro_probe)

    item = asyncio.run(ShortProductionReadinessService(app.state.settings)._check_audio_stack(include_live_checks=True))

    assert item.evidence["kokoro_local_live_checked"] is True
    assert item.evidence["kokoro_local_live_ready"] is True
    assert item.evidence["premium_publication_voice_ready"] is False
    assert item.evidence["local_fallback_publication_voice_ready"] is True
    assert item.evidence["public_publication_voice_ready"] is False


def test_production_readiness_allows_kokoro_for_public_only_when_premium_gate_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_require_premium_voice_before_publication = False

    async def fake_kokoro_probe(self):
        return {"checked": True, "ready": True, "status_code": None, "error": ""}

    async def fake_f5_probe(self):
        return {"checked": True, "ready": False, "status_code": None, "error": "offline"}

    monkeypatch.setattr(ShortProductionReadinessService, "_probe_f5_tts", fake_f5_probe)
    monkeypatch.setattr(ShortProductionReadinessService, "_probe_kokoro_local_tts", fake_kokoro_probe)

    item = asyncio.run(ShortProductionReadinessService(app.state.settings)._check_audio_stack(include_live_checks=True))

    assert item.evidence["premium_publication_voice_ready"] is False
    assert item.evidence["local_fallback_publication_voice_ready"] is True
    assert item.evidence["public_publication_voice_ready"] is True


def test_production_readiness_requires_whisper_srt_runtime(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with patch(
        "atnos.production.readiness.WhisperCppSubtitleService.runtime_availability",
        return_value=WhisperCppRuntimeAvailability(
            available=False,
            executable_path=None,
            model_path=None,
            language="pl",
            reason="whisper.cpp model file is missing.",
        ),
    ):
        item = ShortProductionReadinessService(app.state.settings)._check_whisper()

    assert item.required is True
    assert item.ready is False
    assert item.status == "degraded"
    assert item.evidence["env_keys"] == ["WHISPER_CPP_EXE", "WHISPER_MODEL_PATH", "WHISPER_LANGUAGE"]
    assert item.evidence["fallback_when_unavailable"] == "script_aligned_fallback"
    assert "model file is missing" in item.message


def test_production_readiness_creates_default_workflow_templates(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"
    workflows_dir = app.state.settings.data_dir / "workflows"
    assert not (workflows_dir / "ltx_video_default.json").exists()
    assert not (workflows_dir / "wan_video_default.json").exists()
    assert not (workflows_dir / "thumbnail_default.json").exists()

    item = ShortProductionReadinessService(app.state.settings)._check_workflow_templates()

    assert item.status == "ready"
    templates = item.evidence["templates"]
    assert Path(templates["ltx_video"]["path"]).exists()
    assert Path(templates["wan_video"]["path"]).exists()
    assert Path(templates["thumbnail"]["path"]).exists()
    assert "EmptyLTXVLatentVideo" in templates["ltx_video"]["class_types"]
    assert "EmptyLatentVideo" in templates["wan_video"]["class_types"]
    assert "SaveImage" in templates["thumbnail"]["class_types"]


def test_production_readiness_rejects_semantically_invalid_workflow_template(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"
    workflow_path = app.state.settings.data_dir / "workflows" / "ltx_video_default.json"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(
        '{"1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"ltx-video.safetensors"}}}',
        encoding="utf-8",
    )

    item = ShortProductionReadinessService(app.state.settings)._check_workflow_templates()

    assert item.status == "missing"
    ltx = item.evidence["templates"]["ltx_video"]
    assert ltx["valid"] is False
    assert "EmptyLTXVLatentVideo" in ltx["missing_class_types"]
    assert "{positive_prompt}" in ltx["missing_placeholders"]
    assert "ltx_video" in item.evidence["missing_or_invalid"]


def test_production_readiness_reports_obsidian_style_guide_and_writer(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"
    style_path = settings.obsidian_vault_path / "Atnos" / "Shorts" / "acct-1" / "style_guide.md"
    style_path.parent.mkdir(parents=True)
    style_path.write_text("# Style\n\nFast hooks. No bank jargon.", encoding="utf-8")

    item = ShortProductionReadinessService(settings)._check_obsidian_memory()

    assert item.status == "ready"
    assert item.evidence["vault_exists"] is True
    assert item.evidence["export_enabled"] is True
    assert item.evidence["write_logs_enabled"] is True
    assert item.evidence["shorts_dir_inside_vault"] is True
    assert item.evidence["style_guide_count"] == 1
    assert item.evidence["style_guides"] == [str(style_path.resolve())]
    assert item.evidence["learning_note_count"] == 0
    assert item.evidence["production_log_writer"] == "atnos.memory.obsidian_writer.write_production_log"
    assert item.evidence["analytics_learning_writer"] == "atnos.memory.obsidian_writer.write_analytics_learning_note"
    assert item.evidence["style_guide_reader"] == "atnos.memory.obsidian_style_guide.read_channel_style_guide"
    assert item.evidence["style_guide_bootstrap"] == "atnos.memory.obsidian_style_guide.ensure_channel_style_guide"
    assert item.evidence["reader_fallback_order"] == ["obsidian_style_guide", "latest_channel_learning_note", "local_channel_ops"]


def test_production_readiness_accepts_obsidian_learning_note_as_memory_source(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"
    learning_path = settings.obsidian_vault_path / "Atnos" / "Shorts" / "channel-learning-acct-1-20260602.md"
    learning_path.parent.mkdir(parents=True)
    learning_path.write_text("# Learning\n\nRepeat fast hooks.", encoding="utf-8")

    item = ShortProductionReadinessService(settings)._check_obsidian_memory()

    assert item.status == "ready"
    assert item.evidence["style_guide_count"] == 0
    assert item.evidence["learning_note_count"] == 1
    assert item.evidence["learning_notes"] == [str(learning_path.resolve())]


def test_production_readiness_degrades_obsidian_when_style_guide_is_missing(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    item = ShortProductionReadinessService(settings)._check_obsidian_memory()

    assert item.status == "degraded"
    assert item.ready is True
    assert item.evidence["vault_exists"] is True
    assert item.evidence["style_guide_count"] == 0
    assert item.evidence["learning_note_count"] == 0
    assert "no channel style guides or learning notes" in item.message


def test_production_readiness_has_no_secret_leak_for_slack(tmp_path: Path) -> None:
    import asyncio

    env_path = tmp_path / ".env"
    env_path.write_text("SLACK_WEBHOOK_URL=https://hooks.slack.test/services/secret-token\n", encoding="utf-8")
    app = create_test_app(tmp_path)
    app.state.settings.slack_webhook_url = "https://hooks.slack.test/services/secret-token"
    asyncio.run(init_intelligence_db(app.state.settings))

    with TestClient(app) as client:
        response = client.get("/api/production/readiness?live=false")

    assert response.status_code == 200
    payload_text = response.text
    assert "secret-token" not in payload_text
    by_key = {item["key"]: item for item in response.json()["items"]}
    assert by_key["slack_webhooks"]["evidence"]["configured"] is True
    assert by_key["slack_webhooks"]["evidence"]["webhook_url_redacted"] == "[configured]"
    assert by_key["slack_webhooks"]["evidence"]["env_keys"] == ["SLACK_NOTIFICATIONS_ENABLED", "SLACK_WEBHOOK_URL"]
    assert by_key["slack_webhooks"]["evidence"]["notifier"] == "atnos.services.slack_webhook_notifier.SlackWebhookNotifier"
    assert "atnos.production.private_upload.upload_private_from_package" in by_key["slack_webhooks"]["evidence"]["event_sources"]
    assert "atnos.production.private_upload.run_due_analytics_followups" in by_key["slack_webhooks"]["evidence"]["event_sources"]
    assert "secret-token" not in str(response.json()["operator_checklist"])
    assert "secret-token" not in str(response.json()["next_actions"])


def test_production_readiness_requires_ffprobe_not_only_ffmpeg(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = ShortProductionReadinessService(app.state.settings)

    with (
        patch("atnos.production.readiness.resolve_ffmpeg", return_value="C:/Tools/ffmpeg/bin/ffmpeg.exe"),
        patch("atnos.production.readiness.resolve_ffprobe", return_value=None),
    ):
        item = service._check_ffmpeg()

    assert item.status == "missing"
    assert item.ready is False
    assert item.evidence["ffmpeg"] == "C:/Tools/ffmpeg/bin/ffmpeg.exe"
    assert item.evidence["ffprobe"] is None
    assert "FFprobe is missing" in item.message
