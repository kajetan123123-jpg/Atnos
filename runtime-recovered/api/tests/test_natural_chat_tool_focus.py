import unittest
from unittest.mock import patch
from atnos.core.config import Settings
from atnos.services.background_subagent_service import BackgroundSubagentService

from atnos.services.chat_tool_focus import natural_tool_focus, format_background_status, direct_read_request, format_direct_read, memory_opt_out
from atnos.services.chat_orchestration_service import ChatOrchestrationService
from atnos.tools.chat_agent_tools import build_chat_agent_system_prompt


class NaturalChatToolFocusTest(unittest.TestCase):
    def test_no_save_phrase_inside_a_note_title_is_data(self):
        for title in ('„Bez zapisu pamięci”', '"Bez zapisu pamięci"'):
            with self.subTest(title=title):
                request = f'Zapisz notatkę {title}: Fikcyjny przykład do testu.'
                self.assertFalse(memory_opt_out(request))
                self.assertEqual(natural_tool_focus(request).purpose, 'memory_write')

    def test_file_inspection_is_not_stolen_by_a_memory_write_prohibition(self):
        request = (r'Sprawdź w repo plik E:\Atnos\App\runtime-recovered\api\src\atnos\production\channels\wealth_blueprint\planning_desk_contract.py. '
                   'Zacytuj dosłownie pierwszą linię i podaj nazwę funkcji wybierającej akcję sceny. '
                   'Tylko odczyt, bez modyfikacji, uruchamiania kodu, delegacji i zapisu pamięci.')
        focus = natural_tool_focus(request)
        self.assertEqual(focus.purpose, 'file_read')
        self.assertEqual(focus.required, ('fs_read',))
        self.assertTrue(memory_opt_out(request))

    def test_no_write_clause_keeps_a_real_memory_read_and_does_not_require_memory_for_a_file(self):
        request = 'Przeczytaj E:\\Atnos\\App\\AGENTS.md. Bez zapisu pamięci.'
        self.assertEqual(natural_tool_focus(request).purpose, 'file_read')
        self.assertTrue(memory_opt_out(request))
        memory_request = 'Przeczytaj notatkę w Obsidianie. Bez zapisu pamięci.'
        self.assertEqual(natural_tool_focus(memory_request).purpose, 'memory_read')
        self.assertNotIn('obsidian_write', natural_tool_focus(memory_request).required)

    def test_read_only_does_not_mean_discard_all_but_the_first_line(self):
        request = ('Przeczytaj E:\\Atnos\\App\\example.py i zacytuj pierwszą linię '
                   'oraz podaj nazwę funkcji. Tylko odczyt, bez zapisu pamięci.')
        self.assertIsNone(direct_read_request(request, natural_tool_focus(request)))

    def test_natural_requests_route_without_technical_names(self):
        cases = {
            "Sprawdź jakie zadania w tle teraz działają. Niczego nie uruchamiaj.": "background_status",
            "Pokaż status workerów": "background_status",
            "Jakie zadania aktualnie działają?": "background_status",
            "Czy możesz sprawdzić status workerów?": "background_status",
            "Jaki jest stan zadań w tle?": "background_status",
            "jesteś w stanie zrobić zadanie w pętli? odpalić kilku workerów?": "background_capabilities",
            "Przeczytaj E:\\Atnos\\App\\AGENTS.md i zacytuj pierwszy wiersz": "file_read",
            "Pokaż zawartość folderu E:\\Atnos\\App": "file_list",
            "Przeczytaj services/example.py i przygotuj propozycję poprawki": "coding_proposal",
        }
        for text, purpose in cases.items():
            with self.subTest(text=text):
                self.assertEqual(natural_tool_focus(text).purpose, purpose)
                self.assertTrue(ChatOrchestrationService._looks_like_agentic_tool_request(text))

    def test_does_not_infer_execution_from_general_or_negated_text(self):
        for text in ["Wyjaśnij czym jest worker", "Nie sprawdzaj zadań w tle", "Co oznacza uruchomienie zadania w tle?", "Napisz bajkę", "```Sprawdź zadania w tle```", '"Sprawdź zadania w tle" to przykład polecenia']:
            with self.subTest(text=text):
                self.assertIsNone(natural_tool_focus(text))

    def test_capability_question_cannot_start_jobs(self):
        focus = natural_tool_focus("Czy możesz uruchamiać workerów w tle?")
        self.assertEqual(focus.required, ())
        self.assertNotIn("ai_delegate_background", focus.tools)

    def test_status_preserves_review_and_scope(self):
        result = format_background_status({"tasks": [{"id": 118, "task_type": "coding", "status": "completed", "verification_status": "awaiting_human_review"}]})
        self.assertIn("1 ostatnich", result)
        self.assertIn("wymaga przeglądu", result)
        self.assertIn("nie kontrola wszystkich procesów", result)
        self.assertNotIn("zatwierdz", result)
        self.assertIn("nie mogę potwierdzić", format_background_status({}))

    def test_focused_prompt_is_compact_and_truthful(self):
        prompt = build_chat_agent_system_prompt(files_enabled=True, memory_enabled=False, internet_enabled=False, work_mode="quick", available_tools=["ai_delegate_status"], focus_purpose="background_status")
        self.assertLess(len(prompt), 1600)
        self.assertIn("Nie uruchamiaj nowych zadań", prompt)
        self.assertNotIn("shell_powershell", prompt)

    def test_status_does_not_dump_code_and_prompts_into_chat(self):
        task = {"id": 1, "status": "completed", "payload_json": {"context": {"job_token": "abc"}}, "result_json": {"diff": "unnecessary-code"}, "redacted_prompt": "unnecessary-full-prompt"}
        with patch("atnos.services.background_subagent_service.DatabaseManager"), patch("atnos.services.background_subagent_service.AiDelegationRouter") as router:
            router.return_value.list_tasks.return_value = [task]
            result = BackgroundSubagentService(Settings()).list_recent()
        self.assertNotIn("unnecessary", str(result))
        self.assertEqual(result["tasks"][0]["job_token"], "abc")

    def test_direct_quote_uses_one_explicit_path_without_guessing_limits(self):
        text = 'Przeczytaj "E:\\Atnos\\My Project\\AGENTS.md" i zacytuj wyłącznie jego pierwszy wiersz.'
        self.assertEqual(direct_read_request(text, natural_tool_focus(text)), ("fs_read", {"path": "E:\\Atnos\\My Project\\AGENTS.md"}))
        self.assertEqual(format_direct_read("fs_read", {"content": "# Dokładny wiersz\nReszta", "truncated": False}), "# Dokładny wiersz")
        self.assertIn("uciął", format_direct_read("fs_read", {"content": "partial", "truncated": True}))
        multiple = 'Przeczytaj E:\\one.md oraz E:\\two.md i podaj tylko pierwszy wiersz.'
        self.assertIsNone(direct_read_request(multiple, natural_tool_focus(multiple)))

    def test_memory_opt_out_and_compound_requests(self):
        self.assertTrue(memory_opt_out("Sprawdź zadania. Nie zapisuj pamięci."))
        self.assertFalse(memory_opt_out("Zapisz tę decyzję w pamięci."))
        self.assertIsNone(natural_tool_focus("Przeczytaj brief.json i wygeneruj film."))


if __name__ == "__main__":
    unittest.main()
