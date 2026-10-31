from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.model_config_repository import ModelConfigRepository
from atnos.repositories.plugin_repository import PluginRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.runtime_settings_service import RuntimeSettingsService


class BootstrapLeastPrivilegeDefaultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_bootstrap_enables_supervised_least_privilege_profile_by_default(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            pass

        with app.state.database.session_scope() as session:
            owner = UserRepository(session).get_owner()
            self.assertIsNotNone(owner)
            assert owner is not None

            policy = ExecutionPolicyRepository(session).get_for_user(owner.id)
            self.assertIsNotNone(policy)
            assert policy is not None
            self.assertTrue(policy.allow_local_file_read)
            self.assertTrue(policy.allow_local_file_write)
            self.assertTrue(policy.allow_web_read)
            self.assertTrue(policy.allow_external_fallback)
            self.assertTrue(policy.allow_external_attachment_metadata)
            self.assertFalse(policy.allow_external_attachment_content)
            self.assertTrue(policy.prefer_local_llm)
            self.assertEqual(policy.automation_level.value, "assisted")
            self.assertEqual(policy.verification_mode.value, "strict")
            self.assertEqual(policy.default_provider_posture.value, "ask-before-external")
            self.assertEqual(policy.default_persona_profile.value, "jarvis-mode")

            runtime = RuntimeSettingsService(session, app.state.settings).snapshot()
            self.assertEqual(runtime.autonomy_mode, "supervised")
            self.assertTrue(runtime.browser_control_enabled)
            self.assertFalse(runtime.browser_allow_any_url)
            self.assertTrue(runtime.external_actions_enabled)
            self.assertTrue(runtime.scheduler_enabled)
            self.assertTrue(runtime.oauth_enabled)
            self.assertFalse(runtime.login_automation_enabled)
            self.assertFalse(runtime.two_factor_automation_enabled)
            self.assertFalse(runtime.publish_automation_enabled)
            self.assertFalse(runtime.public_upload_enabled)
            self.assertGreaterEqual(runtime.default_retry_budget, 3)
            self.assertGreaterEqual(runtime.default_vram_floor_gb, 6)

            plugins = PluginRepository(session).list_all()
            self.assertGreaterEqual(len(plugins), 2)
            self.assertTrue(all(plugin.is_enabled for plugin in plugins))
            self.assertTrue(all(plugin.permission_policy.value == "ask" for plugin in plugins))
            self.assertTrue(all(plugin.requires_confirmation is True for plugin in plugins))

            models = ModelConfigRepository(session)
            local_chat = models.get_by_key("ollama-local/chat-default")
            local_orchestration = models.get_by_key("ollama-local/orchestration-default")
            local_vision = models.get_by_key("ollama-local/vision-default")
            self.assertIsNotNone(local_chat)
            self.assertIsNotNone(local_orchestration)
            self.assertIsNotNone(local_vision)
            assert local_chat is not None
            assert local_orchestration is not None
            assert local_vision is not None
            self.assertEqual(local_chat.runtime_model_name, "qwen3.5:9b")
            self.assertEqual(local_orchestration.runtime_model_name, "qwen3:14b")
            self.assertEqual(local_vision.runtime_model_name, "qwen3-vl:8b")
            self.assertEqual(local_chat.context_window, 40960)
            self.assertTrue(local_chat.supports_tools)
            self.assertTrue(local_orchestration.supports_tools)
            self.assertFalse(local_vision.supports_tools)


if __name__ == "__main__":
    unittest.main()
