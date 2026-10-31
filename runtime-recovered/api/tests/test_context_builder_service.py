from __future__ import annotations

import unittest

from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import (
    ExecutionAutomationLevel,
    ExecutionVerificationMode,
    MessageRole,
    MessageStatus,
    ModelTask,
    PersonaProfile,
    ProviderAuthMode,
    ProviderKind,
    ProviderPosture,
    ProviderTransport,
)
from atnos.db.models.execution_policy import ExecutionPolicy
from atnos.db.models.message import Message
from atnos.db.models.provider import Provider
from atnos.services.context_builder_service import ContextBuilderService
from atnos.services.intent_service import ChatIntent, IntentDetectionResult
from atnos.services.runtime_context_preferences import RuntimeContextPreferences


class ContextBuilderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ContextBuilderService()
        self.conversation = Conversation(
            id=1,
            title="Test rozmowy",
            active_work_mode="auto",
        )
        self.user_message = Message(
            conversation_id=1,
            sequence_number=1,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Czy mozesz automatyzowac konta i logowac sie do stron w moim imieniu?",
            attachment_only=False,
        )
        self.execution_policy = ExecutionPolicy(
            user_id=1,
            automation_level=ExecutionAutomationLevel.AUTO,
            verification_mode=ExecutionVerificationMode.SMART,
            allow_local_file_read=True,
            allow_local_file_write=True,
            allow_web_read=True,
            allow_external_fallback=True,
            allow_external_attachment_metadata=True,
            allow_external_attachment_content=False,
            prefer_local_llm=True,
            default_provider_posture=ProviderPosture.PREFER_LOCAL,
            default_persona_profile=PersonaProfile.STRATEGIST,
            max_external_attachment_text_chars=None,
            max_daily_cost=None,
            max_monthly_cost=None,
        )
        self.provider = Provider(
            key="ollama-local",
            label="Ollama",
            vendor="Ollama",
            kind=ProviderKind.LOCAL_LLM,
            transport=ProviderTransport.LOCAL_RUNTIME,
            auth_mode=ProviderAuthMode.LOCAL,
            endpoint_url="http://127.0.0.1:11434",
            is_enabled=True,
            is_builtin=True,
            local_first=True,
            supports_chat=True,
            supports_vision=False,
            supports_audio_in=False,
            supports_audio_out=False,
            supports_image_generation=False,
        )
        self.intent = IntentDetectionResult(
            intent=ChatIntent.GENERAL_CHAT,
            model_task=ModelTask.GENERAL_CHAT,
            reason="test",
        )

    def test_capability_prompt_allows_vault_backed_account_automation(self) -> None:
        context = self.service.build(
            conversation=self.conversation,
            user_message=self.user_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
            capability_snapshot={
                "files_enabled": True,
                "active_document_available": False,
                "obsidian_vault_name": "obsydian",
                "obsidian_configured": True,
                "obsidian_read_available": True,
                "project_memory_available": True,
                "autonomy_mode": "trusted_auto",
                "pc_operator_mode": "trusted_auto",
                "automation_control_available": True,
                "action_broker_mode": "trusted_auto",
                "execution_enabled": True,
                "browser_control_enabled": True,
                "external_actions_enabled": True,
                "scheduler_enabled": True,
                "login_automation_enabled": True,
                "two_factor_automation_enabled": True,
                "publish_automation_enabled": True,
            },
        )

        self.assertIn("Trusted Accounts + Vault", context.provider_prompt)
        self.assertIn("Do not tell the user that account usage is categorically forbidden", context.provider_prompt)
        self.assertIn("login_automation_enabled: True", context.provider_prompt)
        self.assertIn("Vault-backed account automation is enabled", context.provider_prompt)
        self.assertIn("Settings -> Trusted Accounts", context.provider_prompt)
        self.assertIn("encrypted local vault outside the repository", context.provider_prompt)

    def test_provider_prompt_includes_atnos_interaction_contract(self) -> None:
        context = self.service.build(
            conversation=self.conversation,
            user_message=self.user_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
        )

        self.assertIn("ATNOS_INTERACTION_CONTRACT:", context.provider_prompt)
        self.assertIn("Distinguish real runtime capability from planned capability", context.provider_prompt)
        self.assertIn("For tool work, report evidence", context.provider_prompt)
        self.assertIn("Do not use cheerleading", context.provider_prompt)

    def test_provider_prompt_rewrites_retired_atnos_paths_from_memory(self) -> None:
        self.conversation.summary = (
            "Use F:/Apps/Atnos and F:\\AtnosSystem\\AppData\\Roaming\\Atnos for this project."
        )

        context = self.service.build(
            conversation=self.conversation,
            user_message=self.user_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
        )

        self.assertNotIn("F:/Apps/Atnos", context.provider_prompt)
        self.assertNotIn("F:\\AtnosSystem\\AppData\\Roaming\\Atnos", context.provider_prompt)
        self.assertIn("E:/Atnos/App", context.provider_prompt)
        self.assertIn(
            "E:\\Atnos\\Profiles\\AtnosSystem\\AppData\\Roaming\\Atnos",
            context.provider_prompt,
        )

    def test_capability_prompt_blocks_plaintext_secrets_in_obsidian(self) -> None:
        context = self.service.build(
            conversation=self.conversation,
            user_message=self.user_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
            capability_snapshot={
                "files_enabled": True,
                "active_document_available": False,
                "obsidian_vault_name": "obsydian",
                "obsidian_configured": True,
                "obsidian_read_available": True,
                "project_memory_available": True,
                "autonomy_mode": "assisted",
                "pc_operator_mode": "planning_only",
                "automation_control_available": True,
                "action_broker_mode": "preview_approval_only",
                "execution_enabled": False,
                "browser_control_enabled": False,
                "external_actions_enabled": False,
                "scheduler_enabled": True,
                "login_automation_enabled": False,
                "two_factor_automation_enabled": False,
                "publish_automation_enabled": False,
            },
        )

        self.assertIn("Never suggest storing plaintext passwords", context.provider_prompt)
        self.assertIn("not Obsidian notes", context.provider_prompt)
        self.assertIn("Settings -> Trusted Accounts", context.provider_prompt)

    def test_direct_atnos_address_is_treated_as_self_reference(self) -> None:
        direct_message = Message(
            conversation_id=1,
            sequence_number=2,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Atnos, ogarnij mi plan na dzisiaj.",
            attachment_only=False,
        )

        context = self.service.build(
            conversation=self.conversation,
            user_message=direct_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
        )

        self.assertIn("the user is addressing you by name as Atnos", context.provider_prompt)
        self.assertIn("Treat 'Atnos' as your own name", context.provider_prompt)

    def test_product_reference_to_atnos_does_not_trigger_self_reference(self) -> None:
        product_message = Message(
            conversation_id=1,
            sequence_number=3,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Dodaj przycisk do frontendu Atnosa.",
            attachment_only=False,
        )

        context = self.service.build(
            conversation=self.conversation,
            user_message=product_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
        )

        self.assertNotIn("the user is addressing you by name as Atnos", context.provider_prompt)

    def test_capability_prompt_mentions_live_tools_and_external_paths(self) -> None:
        context = self.service.build(
            conversation=self.conversation,
            user_message=self.user_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
            capability_snapshot={
                "files_enabled": True,
                "active_document_available": False,
                "obsidian_vault_name": "obsydian",
                "obsidian_configured": True,
                "obsidian_read_available": True,
                "project_memory_available": True,
                "autonomy_mode": "trusted_auto",
                "pc_operator_mode": "trusted_auto",
                "automation_control_available": True,
                "action_broker_mode": "trusted_auto",
                "execution_enabled": True,
                "browser_control_enabled": True,
                "external_actions_enabled": True,
                "scheduler_enabled": True,
                "login_automation_enabled": True,
                "two_factor_automation_enabled": True,
                "publish_automation_enabled": True,
                "skill_router_available": True,
                "prompt_optimizer_available": True,
                "browser_operator_available": True,
                "desktop_operator_available": True,
                "external_llm_operator_available": True,
                "external_api_llms_ready": ["Claude", "OpenAI"],
                "chrome_mode": "devtools_live",
                "chrome_visible_llm_tabs": ["ChatGPT"],
                "chrome_detected_apps": ["ChatGPT", "GitHub"],
                "ready_desktop_apps": ["Obsidian", "Google Chrome", "CapCut"],
                "powershell_available": True,
                "desktop_input_available": True,
                "browser_recipe_available": True,
                "playwright_adapter_available": True,
            },
        )

        self.assertIn("Operational awareness:", context.provider_prompt)
        self.assertIn("external_api_llms_ready: Claude, OpenAI", context.provider_prompt)
        self.assertIn("chrome_mode: devtools_live", context.provider_prompt)
        self.assertIn("ready_desktop_apps: Obsidian, Google Chrome, CapCut", context.provider_prompt)
        self.assertIn("internal_operators: prompt optimizer, quick chat, browser operator, desktop operator, external LLM operator", context.provider_prompt)

    def test_non_capability_prompt_still_gets_compact_tool_awareness(self) -> None:
        non_capability_message = Message(
            conversation_id=1,
            sequence_number=4,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Napisz mi krótki plan działania na dziś.",
            attachment_only=False,
        )

        context = self.service.build(
            conversation=self.conversation,
            user_message=non_capability_message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
            capability_snapshot={
                "files_enabled": True,
                "active_document_available": False,
                "obsidian_vault_name": "obsydian",
                "obsidian_configured": True,
                "obsidian_read_available": True,
                "project_memory_available": True,
                "autonomy_mode": "trusted_auto",
                "pc_operator_mode": "trusted_auto",
                "automation_control_available": True,
                "action_broker_mode": "trusted_auto",
                "execution_enabled": True,
                "browser_control_enabled": True,
                "external_actions_enabled": True,
                "scheduler_enabled": True,
                "login_automation_enabled": True,
                "two_factor_automation_enabled": True,
                "publish_automation_enabled": True,
                "prompt_optimizer_available": True,
                "external_api_llms_ready": ["Claude"],
                "chrome_mode": "devtools_live",
                "chrome_detected_apps": ["ChatGPT"],
            },
        )

        self.assertIn("Capability summary:", context.provider_prompt)
        self.assertIn("external_api=Claude", context.provider_prompt)
        self.assertNotIn("Operational awareness:", context.provider_prompt)
        self.assertLess(context.provider_prompt_chars, 2_000)

    def test_fast_general_chat_uses_small_prompt_without_runtime_dump(self) -> None:
        message = Message(
            conversation_id=1,
            sequence_number=5,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Odpowiedz jednym slowem: gotowe.",
            attachment_only=False,
        )
        preferences = RuntimeContextPreferences(
            conversation_id=1,
            project_id=None,
            response_style=None,
            response_style_source="default",
            persona_profile=PersonaProfile.JARVIS_MODE,
            persona_profile_source="global",
            memory_enabled=False,
            memory_enabled_source="conversation",
            work_mode="quick",
            work_mode_source="conversation",
            quality_profile="fast-turnaround",
            quality_profile_source="conversation",
        )

        context = self.service.build(
            conversation=self.conversation,
            user_message=message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
            runtime_preferences=preferences,
            capability_snapshot={"external_api_llms_ready": ["Claude"]},
        )

        self.assertLess(context.provider_prompt_chars, 2_000)
        self.assertIn("You are ATNOS", context.provider_prompt)
        self.assertIn("grammatically correct Polish", context.provider_prompt)
        self.assertIn("one clear, general sentence", context.provider_prompt)
        self.assertIn("calm, direct, lightly formal voice", context.provider_prompt)
        self.assertNotIn("ATNOS_INTERACTION_CONTRACT", context.provider_prompt)
        self.assertNotIn("JARVIS MODE", context.provider_prompt)
        self.assertIn("prompt_mode=compact", context.summary)

    def test_fast_general_chat_includes_bounded_current_conversation_history_when_memory_is_off(self) -> None:
        message = Message(
            conversation_id=1,
            sequence_number=3,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Jaki kolor podałem?",
            attachment_only=False,
        )
        preferences = RuntimeContextPreferences(
            conversation_id=1,
            project_id=None,
            response_style=None,
            response_style_source="default",
            persona_profile=PersonaProfile.JARVIS_MODE,
            persona_profile_source="default",
            memory_enabled=False,
            memory_enabled_source="conversation",
            work_mode="quick",
            work_mode_source="conversation",
            quality_profile="fast-turnaround",
            quality_profile_source="conversation",
        )

        context = self.service.build(
            conversation=self.conversation,
            user_message=message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=self.intent,
            provider=self.provider,
            runtime_preferences=preferences,
            conversation_history=(
                {"role": "user", "content": "Kontrolny kolor to błękitny."},
                {"role": "assistant", "content": "Zapamiętałem w tej rozmowie: błękitny."},
            ),
        )

        self.assertIn("CURRENT CONVERSATION HISTORY", context.provider_prompt)
        self.assertIn("Prior user message: Kontrolny kolor to błękitny.", context.provider_prompt)
        self.assertIn("Prior assistant reply: Zapamiętałem w tej rozmowie: błękitny.", context.provider_prompt)
        self.assertIn("Current user message: Jaki kolor podałem?", context.provider_prompt)
        self.assertLess(context.provider_prompt_chars, 2_500)
        self.assertIn("conversation_history_messages=2", context.summary)

    def test_provider_prompt_includes_vision_grounding_for_vision_task(self) -> None:
        intent = IntentDetectionResult(
            intent=ChatIntent.GENERAL_CHAT,
            model_task=ModelTask.VISION,
            reason="test-vision",
        )
        message = Message(
            conversation_id=1,
            sequence_number=1,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="co tu się znajduje",
            attachment_only=False,
        )
        preferences = RuntimeContextPreferences(
            conversation_id=1,
            project_id=None,
            response_style=None,
            response_style_source="default",
            persona_profile=PersonaProfile.JARVIS_MODE,
            persona_profile_source="default",
            memory_enabled=False,
            memory_enabled_source="default",
            work_mode="auto",
            work_mode_source="default",
            quality_profile="standard",
            quality_profile_source="default",
        )
        context = self.service.build(
            conversation=self.conversation,
            user_message=message,
            attachments=[],
            execution_policy=self.execution_policy,
            intent=intent,
            provider=self.provider,
            runtime_preferences=preferences,
        )
        self.assertIn("ATNOS_VISION_GROUNDING", context.provider_prompt)
        self.assertIn("antyhalucynacja", context.provider_prompt.lower())
        self.assertIn("Prefer 'nie wiem'", context.provider_prompt)


if __name__ == "__main__":
    unittest.main()
