from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.skills import explain_route, route_task


class SkillsRouterTest(unittest.TestCase):
    def test_inflected_obsidian_request_loads_memory_instructions(self) -> None:
        from atnos.skills.skill_prompt import load_skill_prompt_context

        for vault_name in ("Obsidianie", "Obsydianie"):
            task = f"Przypomnij decyzję WB i zapisz nową preferencję w {vault_name}"
            with self.subTest(vault_name=vault_name):
                decision = route_task(task)
                self.assertIn("atnos:obsidian-memory", decision.selected_skills)
                loaded = load_skill_prompt_context(task, decision).metadata["loaded_instructions"]
                self.assertIn("atnos:obsidian-memory", [item["skill_id"] for item in loaded])

    def test_open_local_application_request_loads_desktop_instructions(self) -> None:
        from atnos.skills.skill_prompt import load_skill_prompt_context

        task = "Otwórz lokalną aplikację Notatnik na komputerze"
        decision = route_task(task)
        self.assertIn("atnos:desktop-operator", decision.selected_skills)
        loaded = load_skill_prompt_context(task, decision).metadata["loaded_instructions"]
        self.assertIn("atnos:desktop-operator", [item["skill_id"] for item in loaded])

    def test_inflected_obsidian_opt_out_does_not_load_memory(self) -> None:
        decision = route_task(
            "Odczytaj stronę https://example.org. Nie zapisuj nic w Obsidianie."
        )
        self.assertNotIn("atnos:obsidian-memory", decision.selected_skills)

    def test_describing_local_application_does_not_select_desktop_operator(self) -> None:
        decision = route_task("Wyjaśnij jednym zdaniem, czym jest lokalna aplikacja.")
        self.assertNotIn("atnos:desktop-operator", decision.selected_skills)

    def test_file_path_folder_names_do_not_route_to_browser(self) -> None:
        decision = route_task(r'agentic: use fs_read to read E:\Atnos\App\reports\native-browser\quote.txt. Quote its first line.')
        self.assertIn("atnos:file-analysis", decision.selected_skills)
        self.assertNotIn("atnos:browser-operator", decision.selected_skills)

    def assertRoutesTo(self, task: str, *expected: str) -> None:
        decision = route_task(task)
        for skill_id in expected:
            self.assertIn(skill_id, decision.selected_skills, msg=f"{task!r} should route to {skill_id}.")

    def test_pdf_routes_to_pdf_and_file_analysis(self) -> None:
        self.assertRoutesTo("Przeanalizuj ten PDF", "anthropic-skills:pdf", "atnos:file-analysis")

    def test_repo_task_routes_to_codex_repo_work(self) -> None:
        self.assertRoutesTo("Napraw bug w repozytorium i uruchom testy", "atnos:codex-repo-work")

    def test_accessibility_task_routes_to_design_accessibility(self) -> None:
        self.assertRoutesTo("Zrob audyt dostepnosci tego designu", "design:accessibility-review")

    def test_graphic_task_routes_to_local_image_generation(self) -> None:
        self.assertRoutesTo("Zrob grafike promocyjna dla Atnosa", "atnos:image-generation")

    def test_skill_creator_task_routes_to_skill_creator(self) -> None:
        self.assertRoutesTo("Stworz nowy skill do analizy CSV", "anthropic-skills:skill-creator")

    def test_youtube_task_routes_to_youtube_automation(self) -> None:
        self.assertRoutesTo("Wgraj film na YouTube i pobierz analytics", "atnos:youtube-automation")

    def test_short_video_task_routes_to_content_pipeline(self) -> None:
        self.assertRoutesTo(
            "Zrob shorta o Atnosie do YouTube Shorts z narracja i miniatura",
            "atnos:content-pipeline",
        )

    def test_short_answer_request_stays_on_local_llm(self) -> None:
        decision = route_task("What is 7 times 8? Reply in one short sentence.")
        self.assertIn("atnos:local-llm", decision.selected_skills)
        self.assertNotIn("atnos:content-pipeline", decision.selected_skills)

    def test_summarize_in_short_sentence_does_not_route_to_video_pipeline(self) -> None:
        decision = route_task("Summarize this explanation in one short sentence.")
        self.assertIn("atnos:local-llm", decision.selected_skills)
        self.assertNotIn("atnos:content-pipeline", decision.selected_skills)

    def test_browser_request_that_explicitly_disables_memory_does_not_load_memory_skill(self) -> None:
        decision = route_task(
            "agentic: call browser_open with https://www.nasa.gov and browser_learn one fact. "
            "Do not save anything to memory."
        )
        self.assertIn("atnos:browser-operator", decision.selected_skills)
        self.assertNotIn("atnos:obsidian-memory", decision.selected_skills)

    def test_polish_website_inflection_routes_to_browser(self) -> None:
        self.assertRoutesTo("Przeanalizuj stronę https://www.nasa.gov/ i podaj jeden cel NASA", "atnos:browser-operator")

    def test_ubuntu_task_routes_to_ssh_skill_and_marks_risk(self) -> None:
        decision = route_task("Skonfiguruj laptop Ubuntu przez SSH")
        self.assertIn("atnos:ubuntu-full-control", decision.selected_skills)
        self.assertTrue(any("high-risk" in risk for risk in decision.risks))
        self.assertTrue(any("host" in item for item in decision.required_inputs))

    def test_deep_research_task_routes_to_research(self) -> None:
        self.assertRoutesTo(
            "Zrob research z linkami o najlepszych TTS po polsku",
            "atnos:deep-research",
        )

    def test_portable_text_task_routes_to_sanity_conversion(self) -> None:
        self.assertRoutesTo(
            "Przerob markdown na Portable Text w Sanity",
            "sanity:portable-text-conversion",
        )

    def test_brand_voice_task_routes_to_enforce_voice(self) -> None:
        self.assertRoutesTo(
            "Napisz maila zgodnie z glosem marki",
            "brand-voice:enforce-voice",
        )

    def test_onboarding_task_routes_to_hr_onboarding(self) -> None:
        self.assertRoutesTo(
            "Przygotuj onboarding nowego pracownika",
            "human-resources:onboarding",
        )

    def test_explain_route_returns_polish_summary(self) -> None:
        explanation = explain_route("Przeanalizuj ten PDF")
        self.assertIn("Wybrane skille", explanation)
        self.assertIn("anthropic-skills:pdf", explanation)


if __name__ == "__main__":
    unittest.main()
