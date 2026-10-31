from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.chat_session_memory import (
    load_recent_memory_brief,
    persist_curated_chat_memory,
    persist_desktop_alias_memory,
    persist_chat_decision_memory,
)
from atnos.services.web_site_resolve_service import WebSiteResolveService
from memory.obsidian_note_store import read_notes


class ChatSessionMemoryTest(unittest.TestCase):
    def test_automatic_operation_writeback_respects_shared_readonly_policy(self):
        from atnos.services.chat_session_memory import persist_agentic_session_memory
        for instruction in ['Tylko odczyt internetu.', 'Niczego nie zapisuj do Obsidianie.', 'Read-only, do not persist.']:
            with self.subTest(instruction=instruction):
                self.assertIsNone(persist_agentic_session_memory(settings=self.settings,
                    user_message=instruction, assistant_message='Model prose is not permission.',
                    tools_used=['fs_write']))
        self.assertFalse(list(self.vault.rglob('*.md')))

    def test_explicit_note_is_not_duplicated_as_an_automatic_decision(self):
        from atnos.services.chat_session_memory import persist_agentic_session_memory
        result = persist_agentic_session_memory(settings=self.settings,
            user_message='Zapisz notatkę learning: fikcyjny test, nie stan filmu.',
            assistant_message='Saved.', tools_used=['obsidian_write', 'browser_learn'])
        self.assertIsNone(result)
        self.assertFalse(list(self.vault.rglob('*.md')))

    def test_other_executed_operations_still_retain_their_trace(self):
        from atnos.services.chat_session_memory import persist_agentic_session_memory
        result = persist_agentic_session_memory(settings=self.settings,
            user_message='Uruchom edytor.', assistant_message='Opened.', tools_used=['desktop_app_open'])
        self.assertIsNotNone(result)
        self.assertIn('desktop_app_open', result.read_text(encoding='utf-8'))

    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.vault = self.root / "vault"
        self.vault.mkdir(parents=True, exist_ok=True)
        self.settings = build_test_settings(self.root)
        object.__setattr__(self.settings, "obsidian_vault_path", self.vault)

    def test_persist_desktop_alias_and_load_brief(self) -> None:
        path = persist_desktop_alias_memory(
            settings=self.settings,
            query="Comfort",
            resolved_alias="comfy-desktop",
            app_key="comfy-desktop",
            label="Comfy Desktop",
            enabled=True,
        )
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.exists())
        notes = read_notes(agent="atnos-chat", type="memory", settings=self.settings)
        self.assertTrue(any("comfort" in note.title for note in notes))
        brief = load_recent_memory_brief(settings=self.settings, limit=5)
        self.assertIn("Pamięć między sesjami", brief)
        self.assertIn("desktop-alias-comfort", brief.casefold())
        self.assertIn("lokalna aplikacja", brief.casefold())

    def test_query_returns_relevant_note_content_and_omits_other_topics(self) -> None:
        persist_chat_decision_memory(
            settings=self.settings,
            summary="Wealth Blueprint voice: keep English scripts calm, clear, and practical.",
            source="user_decision",
            tags=["wealth-blueprint", "voice"],
        )
        persist_chat_decision_memory(
            settings=self.settings,
            summary="Wonder Crew stories should stay gentle and suitable for ages four to seven.",
            source="user_decision",
            tags=["wonder-crew", "stories"],
        )

        brief = load_recent_memory_brief(
            settings=self.settings,
            limit=4,
            query="Wealth Blueprint voice",
        )

        self.assertIn("calm, clear, and practical", brief)
        self.assertIn("[[atnos-chat/decision/", brief)
        self.assertNotIn("Wonder Crew", brief)

    def test_retrieved_memory_redacts_secret_values(self) -> None:
        persist_chat_decision_memory(
            settings=self.settings,
            summary="Keep the deployment setting documented; api_key=sk-test12345678901234567890",
            source="user_decision",
        )

        brief = load_recent_memory_brief(
            settings=self.settings,
            query="deployment setting documented",
        )

        self.assertIn("api_key=[REDACTED]", brief)
        self.assertNotIn("sk-test12345678901234567890", brief)

    def test_explicit_durable_preference_is_saved_with_source_and_topic_tags(self) -> None:
        path = persist_curated_chat_memory(
            settings=self.settings,
            user_message="Pamiętaj, że Atnos ma odpowiadać po polsku i konkretnie.",
            conversation_id=42,
            message_id=9,
            project_name="Atnos chat",
            enabled=True,
        )

        self.assertIsNotNone(path)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        self.assertIn("conversation:42#message:9", content)
        self.assertIn("user_preference", content)
        self.assertIn("odpowiadać po polsku i konkretnie", content)

    def test_curated_memory_skips_ephemeral_requests_and_explicit_opt_out(self) -> None:
        ephemeral = persist_curated_chat_memory(
            settings=self.settings,
            user_message="Jakie jest teraz połączenie SSH?",
            conversation_id=42,
            message_id=10,
        )
        opted_out = persist_curated_chat_memory(
            settings=self.settings,
            user_message="Pamiętaj, ale nie zapisuj tej preferencji do pamięci.",
            conversation_id=42,
            message_id=11,
        )

        self.assertIsNone(ephemeral)
        self.assertIsNone(opted_out)

    def test_site_alias_is_not_injected_into_unrelated_memory_brief(self) -> None:
        memory_dir = self.vault / "atnos-chat" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "operating-brief.md").write_text(
            "# Operating brief\n"
            "## Aliasy WWW\n"
            "- Forteca IT → https://fortecait.pl/\n"
            "## Polityka dysków\n"
            "- Kod i dane Atnosa: E:\\\n",
            encoding="utf-8",
        )
        (memory_dir / "site-alias-forteca-it.md").write_text(
            "Użytkownik mówi Forteca IT → https://fortecait.pl/\n",
            encoding="utf-8",
        )

        brief = load_recent_memory_brief(settings=self.settings)

        self.assertIn("Polityka dysków", brief)
        self.assertNotIn("forteca", brief.casefold())
        resolved = WebSiteResolveService(self.settings).resolve("Forteca IT")
        self.assertEqual(resolved.method, "alias")
        self.assertEqual(resolved.url, "https://fortecait.pl/")


if __name__ == "__main__":
    unittest.main()
