from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unicodedata

from atnos.db.models.enums import AttachmentKind, ModelTask
from atnos.services.conversation_service import AttachmentDraft
from atnos.services.document_action_service import DocumentActionResult, DocumentActionService


class ChatIntent(str, Enum):
    GENERAL_CHAT = "general_chat"
    CODING = "coding"
    SEARCH_RESEARCH = "search_research"
    ATTACHMENT_ANALYSIS = "attachment_analysis"
    DOCUMENT_WORKFLOW = "document_workflow"
    TRAVEL_PLANNING = "travel_planning"
    CREATIVE_ASSET = "creative_asset"
    CREATIVE_VIDEO = "creative_video"
    PROMPT_OPTIMIZATION = "prompt_optimization"
    EXTERNAL_LLM_OPERATOR = "external_llm_operator"
    BROWSER_OPERATOR = "browser_operator"
    DESKTOP_OPERATOR = "desktop_operator"


@dataclass(slots=True, frozen=True)
class IntentDetectionResult:
    intent: ChatIntent
    model_task: ModelTask
    reason: str
    document_action: DocumentActionResult | None = None


class IntentDetectionService:
    DOCUMENT_KEYWORDS = (
        "document",
        "dokumen",
        "streszcz",
        "podsumuj",
        "przeanalizuj plik",
        "opracuj plik",
        "prezentacja",
        "prezentacje",
        "slajd",
        "tabela",
        "arkusz",
        "excel",
        "porownanie",
        "raport",
        "dokument",
        "word",
        "notatka",
        "pdf",
    )
    # Strong travel signals — weather alone is not enough.
    TRAVEL_KEYWORDS = (
        "trasa",
        "wyjazd",
        "dojazd",
        "przejazd",
        "jak dojechac",
        "jak dojade",
        "podroz",
        "podrozy",
        "wycieczk",
        "lot",
        "lotnisko",
        "pociag",
        "pkp",
        "przesiadk",
        "autobus",
        "bus",
        "hotel",
        "nocleg",
        "maps",
        "google maps",
        "nawigac",
        "korki",
        "traffic",
        "route",
        "trip",
        "travel",
        "drive",
        "train",
        "flight",
        "plan wyjazdu",
        "plan podrozy",
    )
    TRAVEL_WEATHER_KEYWORDS = (
        "weather",
        "pogoda",
    )
    BROWSER_APP_KEYWORDS = (
        "chrome",
        "przegladar",
        "browser",
        "edge",
        "firefox",
    )
    BROWSER_SURFACE_KEYWORDS = (
        "karta",
        "tab",
        "strona",
        "url",
        "link",
        "http://",
        "https://",
        "www.",
    )
    BROWSER_TAB_KEYWORDS = (
        "jakie karty",
        "jakie taby",
        "jakie strony sa otwarte",
        "co jest otwarte w chrome",
        "co mam otwarte w chrome",
        "open tabs",
        "list tabs",
        "lista kart",
        "pokaz karty",
    )
    BROWSER_LEARNING_KEYWORDS = (
        "przeanalizuj",
        "sprawdz",
        "wyciagnij wnioski",
        "zapisz do obsydian",
        "zapisz do obsidian",
        "zapamietaj",
        "naucz sie z",
        "otwarta karta",
        "aktywna karta",
        "obecna karta",
        "to co widzisz",
        "to co jest otwarte",
    )
    BROWSER_LAUNCH_PHRASES = (
        "otworz chrome",
        "otworz przegladarke",
        "uruchom chrome",
        "uruchom przegladarke",
        "odpal chrome",
        "open chrome",
        "open browser",
        "launch chrome",
        "otworz strone",
        "otworz mi strone",
        "otworz nam strone",
        "otworz www",
        "otworz portal",
        "wejdz na strone",
        "wejdz na www",
        "odwiedz strone",
        "otworz link",
    )
    BROWSER_RESEARCH_VERBS = (
        "znajdz",
        "znajdź",
        "wyszukaj",
        "wydobadz",
        "wyciagnij",
        "sprawdz",
        "podaj",
        "wypisz",
        "zbierz",
        "research",
        "find",
        "extract",
    )
    DESKTOP_LAUNCH_VERBS = (
        "otworz",
        "wlacz",
        "uruchom",
        "odpal",
        "zainstaluj",
        "start",
        "open",
        "launch",
        "install",
        "focus",
        "przelacz na",
        "przelacz do",
    )
    DESKTOP_APP_HINTS = (
        "obsydian",
        "obsidian",
        "kimi",
        "comfy",
        "comfort",
        "capcut",
        "cursor",
        "spotify",
        "discord",
        "steam",
        "wallpaper",
        "notepad",
        "notatnik",
        "explorer",
        "kalkulator",
        "calculator",
        "aplikac",
        "program",
        "softwar",
        "desktop",
        "pulpit",
        ".exe",
        ".lnk",
    )
    DESKTOP_DIRECT_PHRASES = (
        "zapisz do obsydiana",
        "zapisz do obsydian",
        "zapisz do obsidian",
        "zapytaj kimi",
        "wyslij do kimi",
        "uzyj kimi",
        "skorzystaj z kimi",
        "focus obsidian",
        "focus kimi",
    )
    EXTERNAL_LLM_KEYWORDS = (
        "claude",
        "anthropic",
        "chatgpt",
        "chat gpt",
        "openai",
        "gemini",
        "grok",
        "browserowe ai",
        "przegladarkowe ai",
        "browser ai",
        "ai w przegladarce",
        "best external llm",
        "best available external llm",
        "najlepszy zewnetrzny llm",
        "najlepszy zewnętrzny llm",
        "inny llm",
        "other llm",
        "another llm",
    )
    EXTERNAL_LLM_VERBS = (
        "zapytaj",
        "ask",
        "uzyj",
        "użyj",
        "use",
        "wyslij",
        "wyślij",
        "send",
        "skonsultuj",
        "consult",
        "skorzystaj",
        "korzystaj",
    )
    CREATIVE_ACTION_KEYWORDS = (
        "zrob",
        "stworz",
        "stwoz",
        "wygeneruj",
        "przygotuj",
        "zaprojektuj",
        "narysuj",
        "wyrenderuj",
        "make",
        "create",
        "generate",
        "design",
    )
    CREATIVE_GRAPHIC_KEYWORDS = (
        "grafi",
        "grafik",
        "obraz",
        "ilustrac",
        "miniatur",
        "thumbnail",
        "plakat",
        "banner",
        "cover",
        "okladk",
        "poster",
        "wizual",
        "hero image",
        "logo",
    )
    CREATIVE_VIDEO_KEYWORDS = (
        "wideo",
        "video",
        "filmik",
        "film",
        "short",
        "shorta",
        "shorts",
        "rolka",
        "rolke",
        "reel",
        "reels",
        "klip",
        "clip",
        "youtube short",
    )
    CODING_KEYWORDS = (
        "kod",
        "code",
        "repo",
        "bug",
        "debug",
        "testy",
        "tests",
        "pytest",
        "typescript",
        "python",
        "refactor",
        "pull request",
        "commit",
    )
    SEARCH_RESEARCH_KEYWORDS = (
        "wyszukaj",
        "poszukaj",
        "w internecie",
        "internet",
        "research",
        "latest",
    )

    def __init__(self) -> None:
        self.document_actions = DocumentActionService()

    def detect(
        self,
        *,
        content: str,
        attachments: tuple[AttachmentDraft, ...],
    ) -> IntentDetectionResult:
        normalized = self._normalize_text(content)
        attachment_kinds = {attachment.kind for attachment in attachments}
        document_action = self.document_actions.detect(content=content, attachments=attachments)

        if AttachmentKind.IMAGE in attachment_kinds:
            return IntentDetectionResult(
                intent=ChatIntent.ATTACHMENT_ANALYSIS,
                model_task=ModelTask.VISION,
                reason="Detected image attachment, routing to attachment analysis intent.",
            )

        if attachment_kinds.intersection({AttachmentKind.DOCUMENT, AttachmentKind.TEXT}):
            return IntentDetectionResult(
                intent=ChatIntent.DOCUMENT_WORKFLOW,
                model_task=ModelTask.SYNTHESIS,
                reason="Detected document-style attachment, routing to document workflow intent.",
                document_action=document_action,
            )

        if any(keyword in normalized for keyword in self.DOCUMENT_KEYWORDS):
            return IntentDetectionResult(
                intent=ChatIntent.DOCUMENT_WORKFLOW,
                model_task=ModelTask.SYNTHESIS,
                reason="Detected document workflow keywords in user request.",
                document_action=document_action,
            )

        if self._looks_like_travel(normalized):
            return IntentDetectionResult(
                intent=ChatIntent.TRAVEL_PLANNING,
                model_task=ModelTask.ORCHESTRATION,
                reason="Detected travel planning keywords in user request.",
            )

        if any(keyword in normalized for keyword in self.CODING_KEYWORDS):
            return IntentDetectionResult(
                intent=ChatIntent.CODING,
                model_task=ModelTask.CODING,
                reason="Detected coding or repository keywords in user request.",
            )

        if any(keyword in normalized for keyword in self.SEARCH_RESEARCH_KEYWORDS):
            return IntentDetectionResult(
                intent=ChatIntent.SEARCH_RESEARCH,
                model_task=ModelTask.SEARCH_RESEARCH,
                reason="Detected live search or research keywords in user request.",
            )

        if any(keyword in normalized for keyword in self.CREATIVE_VIDEO_KEYWORDS) and (
            any(keyword in normalized for keyword in self.CREATIVE_ACTION_KEYWORDS)
            or any(
                phrase in normalized
                for phrase in (
                    "chce wideo",
                    "chce filmik",
                    "chce shorta",
                    "potrzebuje wideo",
                    "potrzebuje shorta",
                    "poprosze shorta",
                    "poprosze wideo",
                )
            )
        ):
            return IntentDetectionResult(
                intent=ChatIntent.CREATIVE_VIDEO,
                model_task=ModelTask.ORCHESTRATION,
                reason="Detected short-form video generation request in user message.",
            )

        if any(keyword in normalized for keyword in self.CREATIVE_GRAPHIC_KEYWORDS) and (
            any(keyword in normalized for keyword in self.CREATIVE_ACTION_KEYWORDS)
            or any(
                phrase in normalized
                for phrase in (
                    "chce grafike",
                    "chce obraz",
                    "potrzebuje grafiki",
                    "potrzebuje obrazu",
                    "poprosze grafike",
                    "poprosze obraz",
                )
            )
        ):
            return IntentDetectionResult(
                intent=ChatIntent.CREATIVE_ASSET,
                model_task=ModelTask.IMAGE_GENERATION,
                reason="Detected image or graphic generation request in user message.",
            )

        if any(keyword in normalized for keyword in self.EXTERNAL_LLM_KEYWORDS) and (
            any(keyword in normalized for keyword in self.EXTERNAL_LLM_VERBS)
            or any(
                keyword in normalized
                for keyword in (
                    "best external llm",
                    "najlepszy zewnetrzny llm",
                    "najlepszy zewnętrzny llm",
                    "other llm",
                    "another llm",
                )
            )
        ):
            return IntentDetectionResult(
                intent=ChatIntent.EXTERNAL_LLM_OPERATOR,
                model_task=ModelTask.ORCHESTRATION,
                reason="Detected explicit external LLM operator request in user message.",
            )

        # Browser before desktop so "otwórz Chrome" / karty nie lecą do desktop operatora.
        if self._looks_like_browser(normalized):
            return IntentDetectionResult(
                intent=ChatIntent.BROWSER_OPERATOR,
                model_task=ModelTask.ORCHESTRATION,
                reason="Detected browser operator request in user message.",
            )

        if self._looks_like_desktop(normalized):
            return IntentDetectionResult(
                intent=ChatIntent.DESKTOP_OPERATOR,
                model_task=ModelTask.ORCHESTRATION,
                reason="Detected desktop operator request in user message.",
            )

        return IntentDetectionResult(
            intent=ChatIntent.GENERAL_CHAT,
            model_task=ModelTask.GENERAL_CHAT,
            reason="Fell back to general chat intent.",
        )

    @classmethod
    def _looks_like_travel(cls, normalized: str) -> bool:
        has_travel = any(keyword in normalized for keyword in cls.TRAVEL_KEYWORDS)
        if has_travel:
            return True
        has_weather = any(keyword in normalized for keyword in cls.TRAVEL_WEATHER_KEYWORDS)
        if not has_weather:
            return False
        # Weather alone is not travel; require a weak travel co-signal.
        return any(
            token in normalized
            for token in (
                "wyjazd",
                "podroz",
                "trasa",
                "lot",
                "hotel",
                "nocleg",
                "trip",
                "travel",
                "route",
            )
        )

    @classmethod
    def _looks_like_browser(cls, normalized: str) -> bool:
        if any(phrase in normalized for phrase in cls.BROWSER_LAUNCH_PHRASES):
            return True
        if any(keyword in normalized for keyword in cls.BROWSER_TAB_KEYWORDS):
            return True
        has_app = any(keyword in normalized for keyword in cls.BROWSER_APP_KEYWORDS)
        has_surface = any(keyword in normalized for keyword in cls.BROWSER_SURFACE_KEYWORDS)
        has_learning = any(keyword in normalized for keyword in cls.BROWSER_LEARNING_KEYWORDS)
        has_research = any(keyword in normalized for keyword in cls.BROWSER_RESEARCH_VERBS)
        if has_app and (has_surface or has_learning or has_research):
            return True
        # „otwórz stronę X i znajdź Y” — surface + research, bez nazwy Chrome.
        if has_surface and (has_learning or has_research):
            return True
        if has_surface and any(verb in normalized for verb in ("otworz", "odpal", "uruchom", "wejdz", "open")):
            return True
        # „otwórz mi stronę X …” — verb + strone even if words are split by mi/nam/prosze.
        if "strone" in normalized and any(
            verb in normalized for verb in ("otworz", "odpal", "uruchom", "wejdz", "open", "pokaz")
        ):
            return True
        if ("http://" in normalized or "https://" in normalized) and any(
            verb in normalized for verb in ("otworz", "open", "wejdz", "sprawdz", "analiz", "znajdz")
        ):
            return True
        return False

    @classmethod
    def _looks_like_desktop(cls, normalized: str) -> bool:
        # Explicit web wording wins over desktop.
        if any(
            token in normalized
            for token in (" strone ", " strona ", "www.", "http://", "https://", " portal ", " link ")
        ) or normalized.startswith("strone ") or "otworz strone" in normalized:
            return False
        if any(phrase in normalized for phrase in cls.DESKTOP_DIRECT_PHRASES):
            return True
        has_verb = any(verb in normalized for verb in cls.DESKTOP_LAUNCH_VERBS)
        has_app = any(hint in normalized for hint in cls.DESKTOP_APP_HINTS)
        if has_verb and has_app:
            return True
        return False

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
        return " ".join(ascii_only.split())
