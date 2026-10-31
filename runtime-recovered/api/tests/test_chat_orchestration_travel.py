from __future__ import annotations

from types import SimpleNamespace
import unittest

from atnos.services.chat_orchestration_service import ChatOrchestrationService
from atnos.services.intent_service import ChatIntent


class ChatOrchestrationTravelGuidanceTest(unittest.TestCase):
    def test_travel_planning_block_encourages_live_data_instead_of_generic_disclaimer(self) -> None:
        block = ChatOrchestrationService._build_travel_planning_block(
            intent=SimpleNamespace(intent=ChatIntent.TRAVEL_PLANNING),
            user_message="Znajdz trase z Lutomierska do Konstantynowa Lodzkiego i sprawdz korki.",
            execution_policy=SimpleNamespace(allow_web_read=True),
            conversation_settings=SimpleNamespace(resolved_internet_enabled=True),
            runtime_controls=SimpleNamespace(browser_control_enabled=True),
        )

        self.assertIsNotNone(block)
        assert block is not None
        self.assertIn("live_web_allowed: True", block)
        self.assertIn("browser_research_available: True", block)
        self.assertIn("Do not default to a generic disclaimer", block)
        self.assertIn("Do not claim that you already checked live traffic", block)
        self.assertIn("never write phrases such as 'sprawdzam aktualny ruch'", block)
        self.assertIn("Nie mam potwierdzonego live odczytu w tej turze", block)
        self.assertIn("prefer high-level route wording", block)
        self.assertIn("For local Polish travel", block)


if __name__ == "__main__":
    unittest.main()
