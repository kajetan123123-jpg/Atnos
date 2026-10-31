from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir


class TrendWatchApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_run_endpoint_returns_refreshed_trend_watch_payload(self) -> None:
        fake_payload = {
            "profile": {"user_id": 1, "display_name": "Owner", "handle": "@owner", "email": None, "bio": None, "locale": "pl-PL", "timezone": "Europe/Warsaw"},
            "style": {"response_length": "medium", "tone": "direct", "structure": "mixed", "criticality_level": 4, "supportiveness_level": 3, "professional_default": True},
            "policy": {
                "automation_level": "assisted",
                "verification_mode": "smart",
                "allow_local_file_read": True,
                "allow_local_file_write": True,
                "allow_web_read": True,
                "allow_external_fallback": True,
                "allow_external_attachment_metadata": True,
                "allow_external_attachment_content": False,
                "prefer_local_llm": True,
                "default_provider_posture": "prefer-local",
                "default_persona_profile": "strategist",
                "jarvis_address_as": "none",
                "max_external_attachment_text_chars": 4000,
                "max_daily_cost": 25,
                "max_monthly_cost": 250,
            },
            "runtime": {
                "browser_control_enabled": False,
                "external_actions_enabled": False,
                "scheduler_enabled": False,
                "oauth_enabled": False,
                "autonomy_mode": "blocked",
                "emergency_stop_enabled": False,
                "pause_all_jobs": False,
                "login_automation_enabled": False,
                "two_factor_automation_enabled": False,
                "publish_automation_enabled": False,
                "default_retry_budget": 2,
                "default_vram_floor_gb": 4,
                "voice_stt_engine": "browser-speech",
                "voice_stt_model_path": None,
                "voice_stt_device": "cpu",
                "ambient_listening_enabled": False,
                "ambient_wake_word": "Atnos",
                "ambient_mic_device": None,
                "ambient_muted": False,
                "ambient_context_enabled": False,
                "trend_watch_enabled": True,
                "trend_watch_query": "short video hooks",
                "trend_watch_region": "PL",
                "trend_watch_interval_minutes": 180,
                "trend_watch_last_run_at": "2026-05-10T15:00:00Z",
                "voice_replies_enabled": False,
                "voice_reply_volume": 0.8,
                "voice_reply_voice_id": None,
                "voice_replies_available": False,
                "voice_replies_reason": "not available",
                "last_user_interaction_at": None,
                "last_briefing_at": None,
                "execution_enabled": False,
                "pc_operator_mode": "planning_only",
                "action_broker_mode": "preview_approval_only",
                "playwright_adapter_available": True,
            },
            "capabilities": {
                "browser_control_enabled": False,
                "external_actions_enabled": False,
                "scheduler_enabled": False,
                "playwright_adapter_available": True,
                "oauth_enabled": False,
                "obsidian_export_enabled": False,
            },
            "obsidian": {"vault_path": None, "export_enabled": False, "path_exists": False, "source": "env", "warnings": [], "vault_name": "obsydian", "readable": False, "note_count": 0},
            "voices": [],
            "roles": [],
            "plugins": [],
            "provider_options": [],
            "provider_cards": [],
            "oauth_connectors": [],
            "runtime_summary": {
                "providers_ready": 0,
                "enabled_models": 0,
                "tasks_ready": 0,
                "has_local_chat": True,
                "has_external_fallback": False,
                "voice_stt": {"configured_engine": "browser-speech", "effective_engine": "browser-speech", "status": "ready", "status_reason": "configured"},
            },
            "trusted_auto": {"runtime_controls": {}, "accounts": [], "trust_grants": [], "scheduler_jobs": [], "execution_runs": [], "media_pipeline_presets": [], "hardware_snapshot": None, "browser_runtime": None, "youtube_connector": None},
            "omega_protocol": {
                "profile_key": "omega_expanded",
                "mode": "expanded",
                "activated": False,
                "ready_for_browser_workflows": False,
                "ready_for_account_login": False,
                "ready_for_two_factor": False,
                "ready_for_publish": False,
                "web_advisor_fallback_enabled": False,
                "trusted_accounts_count": 0,
                "trust_grants_count": 0,
                "runtime_controls": {},
                "browser_runtime": {"profile_dir": "", "downloads_dir": "", "trace_dir": "", "browsers_path": "", "browser_executable": None, "desktop_shell": None, "ready": False, "status_reason": ""},
                "trusted_filesystem_roots": [],
                "runtime_probes": [],
                "allowed_capabilities": [],
                "blocked_capabilities": [],
                "recommended_next_steps": [],
                "persona_protocol": [],
                "note_path": None,
                "note_written_at": None,
            },
            "skills_dashboard": {"summary": {"total_skills": 0, "ready": 0, "partial": 0, "blueprint": 0, "future": 0}, "categories": [], "items": []},
            "decision_log": [],
            "remote_workers": [],
            "trend_watch": {
                "enabled": True,
                "query": "short video hooks",
                "region": "PL",
                "interval_minutes": 180,
                "last_run_at": "2026-05-10T15:00:00Z",
                "latest_snapshot": {
                    "snapshot_id": 7,
                    "project_id": None,
                    "topic_query": "short video hooks",
                    "region_code": "PL",
                    "status": "ready",
                    "source_mix": [],
                    "top_signals": [],
                    "summary": {"query": "short video hooks", "region_code": "PL", "top_keywords": ["story"], "top_hashtags": ["#latenight"], "recommended_reel_types": ["micro_story"], "dominant_sources": ["google_trends_rss"], "signals_count": 1, "interval_minutes": 180, "collected_at": "2026-05-10T15:00:00Z"},
                    "evaluation_notes": ["note"],
                    "note_path": "Atnos/Inbox/Trends.md",
                    "failure_reason": None,
                    "collected_at": "2026-05-10T15:00:00Z",
                    "expires_at": "2026-05-10T18:00:00Z",
                },
            },
        }

        with patch(
            "atnos.services.settings_control_center_service.SettingsControlCenterService.run_trend_watch",
            return_value=fake_payload,
        ):
            with TestClient(self.app) as client:
                response = client.post("/settings/control-center/trend-watch/run")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("trend_watch", payload)
        self.assertEqual(payload["trend_watch"]["latest_snapshot"]["summary"]["top_keywords"], ["story"])


if __name__ == "__main__":
    unittest.main()
