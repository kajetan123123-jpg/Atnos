from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(slots=True, frozen=True)
class ChatPromptImprovementResult:
    original_content: str
    improved_content: str
    strategy_note: str
    changed: bool


class ChatPromptImprovementService:
    _LEADING_VERB_PATTERN = re.compile(
        r"^(powiedz(?: mi)?|napisz(?: mi)?|podaj|pokaz|pokaż|wyjasnij|wyjaśnij|wytlumacz|wytłumacz|sprawdz|sprawdź|zobacz)\s+",
        flags=re.IGNORECASE,
    )
    _CODING_HINTS = ("kod", "repo", "frontend", "backend", "api", "bug", "fix", "patch", "refactor")
    _BROWSER_HINTS = ("przegladar", "przeglądar", "chrome", "firefox", "edge", "karty", "taby", "tabs")
    _FILE_HINTS = ("plik", "pdf", "docx", "pptx", "excel", "arkusz", "dokument")
    _RESEARCH_HINTS = ("porownaj", "porównaj", "research", "zbadaj", "sprawdz informacje", "sprawdź informacje")

    def improve(self, content: str) -> ChatPromptImprovementResult:
        original = self._normalize(content)
        if not original:
            raise ValueError("Prompt do poprawy nie moze byc pusty.")

        category = self._detect_category(original)
        improved = getattr(self, f"_rewrite_{category}")(original)
        improved = self._normalize(improved)
        return ChatPromptImprovementResult(
            original_content=original,
            improved_content=improved,
            strategy_note=self._strategy_note_for_category(category),
            changed=improved != original,
        )

    def _detect_category(self, content: str) -> str:
        lowered = content.lower()
        if any(hint in lowered for hint in self._BROWSER_HINTS):
            return "browser"
        if any(hint in lowered for hint in self._CODING_HINTS):
            return "coding"
        if any(hint in lowered for hint in self._FILE_HINTS):
            return "file"
        if any(hint in lowered for hint in self._RESEARCH_HINTS):
            return "research"
        return "general"

    def _rewrite_browser(self, content: str) -> str:
        cleaned = self._strip_leading_verb(content).rstrip("?. ")
        lowered = cleaned.lower()
        if "karty" in lowered and "chrome" in lowered:
            return (
                "Sprawdz, jakie karty sa teraz otwarte w Google Chrome na tym komputerze. "
                "Zwroc krotka liste: tytul karty i URL. "
                "Jesli nie masz dostepu do aktywnej sesji Chrome, napisz dokladnie, co blokuje odczyt."
            )
        if lowered.startswith(("jakie", "ktore", "które", "czy")):
            return (
                f"Sprawdz, {cleaned[0].lower() + cleaned[1:]}. "
                "Zwroc tylko konkretny wynik bez wstepu. "
                "Jesli nie mozesz wykonac tego lokalnie, napisz dokladnie, co blokuje zadanie."
            )
        return (
            f"Sprawdz: {cleaned}. "
            "Zwroc tylko konkretny wynik bez wstepu. "
            "Jesli nie mozesz wykonac tego lokalnie, napisz dokladnie, co blokuje zadanie."
        )

    def _rewrite_coding(self, content: str) -> str:
        cleaned = self._strip_leading_verb(content).rstrip("?. ")
        return (
            f"Wykonaj to zadanie w kodzie: {cleaned}. "
            "Najpierw ustal root cause, potem zrob minimalna zmiane. "
            "Na koncu podaj, co zmieniles i jak to zweryfikowales. "
            "Jesli czegos nie mozesz domknac lokalnie, napisz dokladnie, co blokuje wynik."
        )

    def _rewrite_file(self, content: str) -> str:
        cleaned = self._strip_leading_verb(content).rstrip("?. ")
        return (
            f"Pracuj na tym materiale: {cleaned}. "
            "Zwroc konkretny wynik albo plik, bez ogolnikow. "
            "Jesli brakuje dostepu do pliku albo narzedzia, napisz dokladnie, czego brakuje."
        )

    def _rewrite_research(self, content: str) -> str:
        cleaned = self._strip_leading_verb(content).rstrip("?. ")
        return (
            f"Zbadaj to: {cleaned}. "
            "Zwroc zwięzly wynik z najwazniejszymi wnioskami i zrodlem, jesli bylo potrzebne. "
            "Jesli pytanie jest za szerokie, zawez je do najbardziej uzytecznej odpowiedzi zamiast uciekac w ogolniki."
        )

    def _rewrite_general(self, content: str) -> str:
        cleaned = self._strip_leading_verb(content).rstrip("?. ")
        if cleaned.lower().startswith(("jakie", "jaki", "jaka", "ktore", "które", "czy")):
            return (
                f"Sprawdz, {cleaned[0].lower() + cleaned[1:]}. "
                "Zwroc konkretna odpowiedz bez lania wody. "
                "Jesli nie mozesz tego zrobic, napisz dokladnie, co blokuje wykonanie."
            )
        return (
            f"Zrob to zadanie: {cleaned}. "
            "Zwroc konkretny wynik bez wstepu. "
            "Jesli potrzebujesz czegos, czego nie masz lokalnie, napisz dokladnie czego."
        )

    @staticmethod
    def _normalize(content: str) -> str:
        return " ".join(content.strip().split())

    def _strip_leading_verb(self, content: str) -> str:
        stripped = self._LEADING_VERB_PATTERN.sub("", content.strip(), count=1)
        return self._normalize(stripped) or self._normalize(content)

    @staticmethod
    def _strategy_note_for_category(category: str) -> str:
        mapping = {
            "browser": "Doprecyzowano akcje w przegladarce, format wyniku i jawny warunek bledu.",
            "coding": "Doprecyzowano root cause, minimalna zmiane i oczekiwana walidacje.",
            "file": "Doprecyzowano oczekiwany artefakt i warunek brakujacego dostepu.",
            "research": "Doprecyzowano zakres, wynik i oczekiwanie na konkret zamiast ogolnikow.",
            "general": "Doprecyzowano cel, format odpowiedzi i jawny fallback bledu.",
        }
        return mapping[category]
