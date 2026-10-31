from __future__ import annotations

import unittest
from unittest.mock import patch

from atnos.db.models.enums import PersonaProfile, ProviderPosture
from atnos.services.chat_orchestration_service import ChatOrchestrationService
from atnos.services.conversation_service import ExecutionPreferences
from atnos.services.conversation_settings_resolver_service import ConversationSettingsResolution


class ChatOrchestrationAccessRealityTest(unittest.TestCase):
    def test_request_strict_local_overrides_global_provider_and_disables_internet(self) -> None:
        resolved = ConversationSettingsResolution(
            conversation_id=1,
            active_work_mode="auto",
            active_work_mode_source="default",
            response_style_override=None,
            memory_enabled_override=None,
            file_enabled_override=None,
            internet_enabled_override=None,
            persona_profile_override=None,
            provider_posture_override=None,
            resolved_response_style=None,
            resolved_response_style_source="default",
            resolved_memory_enabled=True,
            resolved_memory_enabled_source="default",
            resolved_file_enabled=True,
            resolved_file_enabled_source="default",
            resolved_internet_enabled=True,
            resolved_internet_enabled_source="default",
            resolved_persona_profile=PersonaProfile.STRATEGIST,
            resolved_persona_profile_source="global",
            resolved_provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
            resolved_provider_posture_source="global",
        )

        updated = ChatOrchestrationService._apply_request_execution_overrides(
            resolved,
            request_execution_preferences=ExecutionPreferences(
                provider_posture="strict-local",
                internet_enabled=True,
            ),
        )

        self.assertEqual(updated.resolved_provider_posture, ProviderPosture.STRICT_LOCAL)
        self.assertEqual(updated.resolved_provider_posture_source, "request")
        self.assertFalse(updated.resolved_internet_enabled)
        self.assertEqual(updated.resolved_internet_enabled_source, "request")

    def test_access_block_distinguishes_profile_email_from_mailbox_access(self) -> None:
        service = ChatOrchestrationService.__new__(ChatOrchestrationService)
        service.session = object()
        service.settings = object()
        service.provider_registry = object()

        with patch("atnos.services.chat_orchestration_service.AccessRealityService") as access_cls:
            access_cls.return_value.build_chat_block.return_value = (
                "Access reality snapshot:\n"
                "- owner_email_access: identity_only_or_no_live_mail\n"
                "- identity.owner_email: level=identity_only; type=identity; summary=Atnos knows the owner email as identity only.; account=none; connector=none; actions=none; reasons=Profile email does not grant inbox access.\n"
                "- connector.gmail: level=configured; type=connector; summary=Gmail connector exists but has no active OAuth session.; account=none; connector=gmail; actions=none; reasons=No active OAuth session is connected.\n"
                "- platform.youtube: level=live_write; type=platform; summary=YouTube is ready for supervised write or publish.; account=YouTube Studio glowny; connector=youtube-studio; actions=publish_supervised, fetch_analytics; reasons=none\n"
                "- platform.bootstrap-mailbox: level=configured; type=platform; summary=Bootstrap Mailbox is ready for draft workflows but not final write.; account=Bootstrap mailbox; connector=bootstrap-mailbox; actions=signup_recovery_anchor; reasons=Browser control is disabled in runtime.\n"
                "- The owner email from Settings is identity only unless a matching live mail source is explicitly live_read or live_write.\n"
                "- If only identity.owner_email exists for mail, say exactly: 'Znam Twoj adres z ustawien, ale nie mam dostepu do skrzynki.'"
            )

            block = ChatOrchestrationService._build_access_reality_block(
                service,
                user_message="Masz dostep do mojego maila?",
            )

        self.assertIsNotNone(block)
        assert block is not None
        self.assertIn("identity.owner_email: level=identity_only", block)
        self.assertIn("connector.gmail: level=configured", block)
        self.assertIn("platform.youtube: level=live_write", block)
        self.assertIn("Znam Twoj adres z ustawien, ale nie mam dostepu do skrzynki.", block)

    def test_access_block_can_be_skipped_when_irrelevant_and_empty(self) -> None:
        service = ChatOrchestrationService.__new__(ChatOrchestrationService)
        service.session = object()
        service.settings = object()
        service.provider_registry = object()

        with patch("atnos.services.chat_orchestration_service.AccessRealityService") as access_cls:
            access_cls.return_value.build_chat_block.return_value = None

            block = ChatOrchestrationService._build_access_reality_block(
                service,
                user_message="Napisz plan dzialania dla nowego projektu.",
            )

        self.assertIsNone(block)


if __name__ == "__main__":
    unittest.main()
