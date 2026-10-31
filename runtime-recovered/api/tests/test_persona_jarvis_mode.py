from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import (
    ExecutionAutomationLevel,
    ExecutionVerificationMode,
    JarvisAddressAs,
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


class JarvisModePersonaTest(unittest.TestCase):
    def test_context_builder_uses_jarvis_prompt_and_address_guidance(self) -> None:
        service = ContextBuilderService()
        context = service.build(
            conversation=Conversation(id=1, title="Jarvis", active_work_mode="auto"),
            user_message=Message(
                conversation_id=1,
                sequence_number=1,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="Przygotuj mi poranny briefing.",
                attachment_only=False,
            ),
            attachments=[],
            execution_policy=ExecutionPolicy(
                user_id=1,
                automation_level=ExecutionAutomationLevel.ASSISTED,
                verification_mode=ExecutionVerificationMode.SMART,
                allow_local_file_read=True,
                allow_local_file_write=True,
                allow_web_read=True,
                allow_external_fallback=True,
                allow_external_attachment_metadata=True,
                allow_external_attachment_content=False,
                prefer_local_llm=True,
                default_provider_posture=ProviderPosture.PREFER_LOCAL,
                default_persona_profile=PersonaProfile.JARVIS_MODE,
                jarvis_address_as=JarvisAddressAs.SIR_MADAM,
                max_external_attachment_text_chars=None,
                max_daily_cost=None,
                max_monthly_cost=None,
            ),
            intent=IntentDetectionResult(
                intent=ChatIntent.GENERAL_CHAT,
                model_task=ModelTask.GENERAL_CHAT,
                reason="test",
            ),
            resolved_persona_profile=PersonaProfile.JARVIS_MODE,
            provider=Provider(
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
            ),
            capability_snapshot=None,
        )

        self.assertIn("Operate in jarvis-mode.", context.provider_prompt)
        self.assertIn("you are Atnos, the user's own local AI operator", context.provider_prompt)
        self.assertIn("Use Sir or Madam sparingly", context.provider_prompt)
