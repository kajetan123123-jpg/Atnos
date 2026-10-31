from __future__ import annotations

import unittest

from atnos.db.models.enums import AttachmentKind, ModelTask
from atnos.services.conversation_service import AttachmentDraft
from atnos.services.intent_service import ChatIntent, IntentDetectionService


class IntentDetectionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = IntentDetectionService()

    def test_detects_travel_planning_keywords(self) -> None:
        result = self.service.detect(
            content="Znajdz mi najlepsza trase z Lutomierska do Konstantynowa Lodzkiego i sprawdz pogode na wyjazd.",
            attachments=tuple(),
        )

        self.assertEqual(result.intent, ChatIntent.TRAVEL_PLANNING)
        self.assertEqual(result.model_task, ModelTask.ORCHESTRATION)

    def test_weather_alone_is_not_travel(self) -> None:
        result = self.service.detect(content="Jaka jest pogoda w Lodzi?", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.GENERAL_CHAT)

    def test_image_attachment_wins_over_keywords(self) -> None:
        result = self.service.detect(
            content="otworz Obsidian i napisz kod",
            attachments=(
                AttachmentDraft(
                    filename="shot.png",
                    storage_path="E:/tmp/shot.png",
                    kind=AttachmentKind.IMAGE,
                    media_type="image/png",
                    size_bytes=12,
                ),
            ),
        )
        self.assertEqual(result.intent, ChatIntent.ATTACHMENT_ANALYSIS)
        self.assertEqual(result.model_task, ModelTask.VISION)

    def test_open_chrome_is_browser_not_desktop(self) -> None:
        result = self.service.detect(content="otwórz Chrome", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.BROWSER_OPERATOR)

    def test_list_tabs_is_browser(self) -> None:
        result = self.service.detect(content="jakie karty mam otwarte w Chrome?", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.BROWSER_OPERATOR)

    def test_open_obsidian_is_desktop(self) -> None:
        result = self.service.detect(content="otwórz Obsidian", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.DESKTOP_OPERATOR)

    def test_bare_open_is_not_desktop(self) -> None:
        result = self.service.detect(content="otwórz", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.GENERAL_CHAT)

    def test_save_to_obsidian_is_desktop(self) -> None:
        result = self.service.detect(content="zapisz do Obsidian: Comfort = Comfy", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.DESKTOP_OPERATOR)

    def test_coding_keywords(self) -> None:
        result = self.service.detect(content="zrefaktoruj ten kod w pythonie", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.CODING)
        self.assertEqual(result.model_task, ModelTask.CODING)

    def test_search_research_keywords(self) -> None:
        result = self.service.detect(content="wyszukaj latest news w internecie", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.SEARCH_RESEARCH)

    def test_open_site_and_find_data_is_browser(self) -> None:
        result = self.service.detect(
            content="otwórz mi stronę Forteca IT i znajdź NIP oraz kontakt",
            attachments=tuple(),
        )
        self.assertEqual(result.intent, ChatIntent.BROWSER_OPERATOR)

    def test_open_site_phrase_is_browser(self) -> None:
        result = self.service.detect(content="otwórz stronę fortecait.pl", attachments=tuple())
        self.assertEqual(result.intent, ChatIntent.BROWSER_OPERATOR)


if __name__ == "__main__":
    unittest.main()
