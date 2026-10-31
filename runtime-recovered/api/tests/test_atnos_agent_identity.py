from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.tools.chat_agent_tools import (
    build_chat_agent_system_prompt,
    load_atnos_agent_identity,
)


class AtnosAgentIdentityTest(unittest.TestCase):
    def test_identity_file_defines_atnos_for_localuseran(self) -> None:
        identity = load_atnos_agent_identity()
        self.assertIn("Atnos", identity)
        self.assertIn("operator", identity)
        self.assertIn("Jarvis", identity)
        self.assertIn("pierwszej osobie", identity)

    def test_system_prompt_includes_identity_contract_and_jarvis(self) -> None:
        prompt = build_chat_agent_system_prompt(
            files_enabled=True,
            memory_enabled=True,
            internet_enabled=True,
            work_mode="quick",
            available_tools=["desktop_app_open", "self_state"],
            ready_desktop_apps=["CapCut"],
            persona_profile="jarvis-mode",
        )
        self.assertIn("Jesteś Atnos", prompt)
        self.assertIn("ATNOS_INTERACTION_CONTRACT", prompt)
        self.assertIn("Operate in jarvis-mode", prompt)
        self.assertIn("desktop_app_open", prompt)
        self.assertIn("CapCut", prompt)
        self.assertIn("Quick ON", prompt)


if __name__ == "__main__":
    unittest.main()
