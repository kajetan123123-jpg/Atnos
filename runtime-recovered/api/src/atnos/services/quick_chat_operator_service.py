from __future__ import annotations

import ast
from dataclasses import dataclass, field
import math
import operator
import re
from typing import Literal


QuickChatAction = Literal[
    "greeting",
    "presence",
    "identity",
    "capabilities",
    "gratitude",
    "ack",
    "echo_word",
    "calculation",
]


CAPABILITY_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Chat i modele",
        (
            "Ollama lokalnie jako domyslny tor roboczy.",
            "OpenAI, Claude/Anthropic i Gemini przez provider registry, jesli klucze sa skonfigurowane.",
            "Task-aware routing: chat, kod, research, synteza, vision oraz strict_local bez cloud calli.",
        ),
    ),
    (
        "Pamiec i projekty",
        (
            "Historia rozmow, projekty, decision log, commitments i briefing.",
            "Obsidian read/write jako jawna pamiec projektu, jesli vault jest skonfigurowany.",
            "Agent task logs, notatki produkcyjne i raporty QA.",
        ),
    ),
    (
        "Pliki i artefakty",
        (
            "Upload i analiza plikow, dokumentow, PDF, obrazow oraz lokalnych artefaktow.",
            "Generowanie raportow, markdown, produkcyjnych paczek artefaktow i attachmentow w chacie.",
        ),
    ),
    (
        "Content i media",
        (
            "Generowanie grafik, miniatur i prostych kompozycji wizualnych.",
            "Lokalny pipeline shortow: script -> Piper TTS -> subtitles -> thumbnail -> final.mp4 -> QA.",
            "YouTube private upload i analytics sa dostepne po OAuth i zawsze z approval przed publikacja.",
        ),
    ),
    (
        "Narzedzia lokalne",
        (
            "Browser operator: Google Chrome, Brave i Chromium — otwieranie stron, czytanie tekstu, screenshoty oraz paste-only do AI web.",
            "Ubuntu SSH sandbox z pelnym sudo w sandboxie, logowaniem komend i emergency stop.",
            "Screen capture / visual QA do oceny UI, screenshotow i nagran ekranu.",
            "Desktop operator do fokusowania i uruchamiania lokalnych aplikacji.",
        ),
    ),
    (
        "Integracje",
        (
            "GitHub, Linear, Gmail, Google Calendar i YouTube istnieja jako connectory, ale wymagaja OAuth/API.",
            "Konta przez Trusted Accounts maja tryby: read, draft, supervised write i approval gates.",
        ),
    ),
    (
        "Bezpieczenstwo",
        (
            "Publikacje, platnosci, sekrety, kasowanie danych i high-risk na glownym PC wymagaja zgody.",
            "Nie omijam CAPTCHA, 2FA, paywalli ani logowania; wtedy zwracam HUMAN_REQUIRED.",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class QuickChatOperationResult:
    activated: bool
    action: QuickChatAction | None = None
    assistant_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class QuickChatOperatorService:
    _MATH_REQUEST_RE = re.compile(r"^(?:(?:ile jest|oblicz|policz|calculate|what is)\s+)?([\d\s()+\-*/%^,.]+)$")
    _MATH_OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }
    _GREETING_EXACT = {
        "hej",
        "hej atnos",
        "hej atnosie",
        "czesc",
        "czesc atnos",
        "czesc atnosie",
        "siema",
        "witam",
        "halo",
        "halo atnos",
    }
    _PRESENCE_EXACT = {
        "jestes",
        "atnos jestes",
        "dzialasz",
        "atnos dzialasz",
        "slyszysz mnie",
        "halo jestes",
        "jestes tam",
    }
    _IDENTITY_PATTERNS = (
        "kim jestes",
        "kto ty",
        "co to atnos",
        "atnos kim jestes",
    )
    _CAPABILITY_PATTERNS = (
        "co umiesz",
        "co potrafisz",
        "co mozesz",
        "jak mozesz pomoc",
        "z czym mozesz pomoc",
        "do czego masz dostep",
    )
    _THANKS_EXACT = {
        "dzieki",
        "dzieki atnos",
        "dziekuje",
        "dziekuje atnos",
        "thx",
        "thanks",
    }
    _ACK_EXACT = {
        "ok",
        "okej",
        "okay",
        "jasne",
        "super",
        "git",
        "rozumiem",
        "dobrze",
        "spoko",
        "tak",
        "nie",
    }
    _ECHO_WORD_RE = re.compile(
        r"^(?:odpowiedz|napisz|powiedz)\s+(?:jednym\s+)?(?:slowem|wyrazem)\s*[:\-]?\s*(.+)$"
    )
    _ACTIONABLE_HINTS = (
        "otworz",
        "uruchom",
        "odpal",
        "wlacz",
        "sprawdz",
        "przeanalizuj",
        "zapisz",
        "zapytaj",
        "wyslij",
        "stworz",
        "napisz",
        "zrob",
        "wyciagnij",
        "powiedz jakie karty",
    )

    def maybe_handle(self, *, content: str) -> QuickChatOperationResult:
        normalized = self._normalize(content)
        if not normalized:
            return QuickChatOperationResult(activated=False)

        calculation = self._try_calculation(normalized)
        if calculation is not None:
            expression, value = calculation
            formatted = self._format_number(value)
            return QuickChatOperationResult(
                activated=True,
                action="calculation",
                assistant_content=f"{expression} = {formatted}",
                metadata={
                    "quick_chat": {
                        "action": "calculation",
                        "expression": expression,
                        "result": formatted,
                        "deterministic": True,
                    }
                },
            )

        if self._is_simple_greeting(normalized):
            return QuickChatOperationResult(
                activated=True,
                action="greeting",
                assistant_content="Jestem. Powiedz mi konkretny cel, a od razu przejde do dzialania.",
                metadata={"quick_chat": {"action": "greeting"}},
            )

        if self._is_presence_check(normalized):
            return QuickChatOperationResult(
                activated=True,
                action="presence",
                assistant_content=(
                    "Tak, dzialam. Moge pracowac na plikach, przegladarce, Obsidianie i aplikacjach desktopowych."
                ),
                metadata={"quick_chat": {"action": "presence"}},
            )

        if self._matches_any(normalized, self._IDENTITY_PATTERNS, max_words=8):
            return QuickChatOperationResult(
                activated=True,
                action="identity",
                assistant_content=(
                    "Jestem Atnos. Dzialam jako Twoj lokalny agent AI: chat, pliki, Obsidian, przegladarka, Ubuntu sandbox, shorty i routing modeli. Mowie wprost, co mam realnie dostepne, a co wymaga konfiguracji."
                ),
                metadata={"quick_chat": {"action": "identity"}},
            )

        if self._matches_any(normalized, self._CAPABILITY_PATTERNS, max_words=10):
            return QuickChatOperationResult(
                activated=True,
                action="capabilities",
                assistant_content=self._build_capability_answer(),
                metadata={
                    "quick_chat": {
                        "action": "capabilities",
                        "capability_sections": [
                            {"label": label, "items": list(items)}
                            for label, items in CAPABILITY_SECTIONS
                        ],
                    }
                },
            )

        if normalized in self._THANKS_EXACT:
            return QuickChatOperationResult(
                activated=True,
                action="gratitude",
                assistant_content="Jasne. Lecimy dalej.",
                metadata={"quick_chat": {"action": "gratitude"}},
            )

        echo_match = self._ECHO_WORD_RE.match(normalized)
        if echo_match:
            word = " ".join(echo_match.group(1).strip().split())[:40]
            if word and not self._looks_actionable(word):
                return QuickChatOperationResult(
                    activated=True,
                    action="echo_word",
                    assistant_content=word,
                    metadata={"quick_chat": {"action": "echo_word"}},
                )

        if normalized in self._ACK_EXACT:
            return QuickChatOperationResult(
                activated=True,
                action="ack",
                assistant_content="OK.",
                metadata={"quick_chat": {"action": "ack"}},
            )

        return QuickChatOperationResult(activated=False)

    def _is_simple_greeting(self, normalized: str) -> bool:
        if normalized in self._GREETING_EXACT:
            return True
        return (
            normalized.startswith("hej ")
            and len(normalized.split()) <= 4
            and not self._looks_actionable(normalized)
        )

    def _is_presence_check(self, normalized: str) -> bool:
        if normalized in self._PRESENCE_EXACT:
            return True
        if "dzialasz" in normalized and len(normalized.split()) <= 4:
            return True
        return False

    def _matches_any(self, normalized: str, patterns: tuple[str, ...], *, max_words: int) -> bool:
        if len(normalized.split()) > max_words:
            return False
        if self._looks_actionable(normalized):
            return False
        return any(pattern in normalized for pattern in patterns)

    def _looks_actionable(self, normalized: str) -> bool:
        return any(token in normalized for token in self._ACTIONABLE_HINTS)

    @staticmethod
    def _build_capability_answer() -> str:
        lines: list[str] = [
            "Realnie moge pracowac w tych obszarach. Jesli cos wymaga konfiguracji, oznaczam to wprost:",
            "",
        ]
        for label, items in CAPABILITY_SECTIONS:
            lines.append(f"**{label}:**")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
        lines.append(
            "Najlepiej dawaj mi cel typu: `sprawdz laptop Ubuntu`, `zrob shorta o X`, "
            "`przeanalizuj plik`, `zapisz w Obsidianie`, `otworz strone i stresc`, "
            "`przygotuj prywatny upload YouTube`."
        )
        lines.append(
            "Dla dluzszych zadan powinienem pokazywac etap pracy i dowod: artefakt, log, sciezke pliku albo konkretny blad."
        )
        return "\n".join(lines).strip()

    @staticmethod
    def _normalize(content: str) -> str:
        normalized = " ".join((content or "").strip().lower().split())
        normalized = normalized.replace("ą", "a").replace("ć", "c").replace("ę", "e")
        normalized = normalized.replace("ł", "l").replace("ń", "n").replace("ó", "o")
        normalized = normalized.replace("ś", "s").replace("ź", "z").replace("ż", "z")
        return re.sub(r"\s+", " ", normalized).strip(" .!?")

    @classmethod
    def _try_calculation(cls, normalized: str) -> tuple[str, int | float] | None:
        match = cls._MATH_REQUEST_RE.fullmatch(normalized)
        if match is None:
            return None
        expression = match.group(1).strip().replace("^", "**")
        if not expression or len(expression) > 100:
            return None
        if "," in expression:
            if "." in expression or expression.count(",") > 1:
                return None
            expression = re.sub(r"(?<=\d),(?=\d)", ".", expression)
        try:
            tree = ast.parse(expression, mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 32:
                return None
            value = cls._evaluate_math_node(tree.body)
        except (ArithmeticError, SyntaxError, TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(float(value)) or abs(value) > 1_000_000_000_000_000:
            return None
        return expression, value

    @classmethod
    def _evaluate_math_node(cls, node: ast.AST) -> int | float:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 1_000_000_000_000:
                raise ValueError("numeric literal out of range")
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in cls._MATH_OPERATORS:
            return cls._MATH_OPERATORS[type(node.op)](cls._evaluate_math_node(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in cls._MATH_OPERATORS:
            left = cls._evaluate_math_node(node.left)
            right = cls._evaluate_math_node(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("exponent out of range")
            result = cls._MATH_OPERATORS[type(node.op)](left, right)
            if not math.isfinite(float(result)) or abs(result) > 1_000_000_000_000_000:
                raise ValueError("result out of range")
            return result
        raise ValueError("unsupported expression")

    @staticmethod
    def _format_number(value: int | float) -> str:
        if isinstance(value, int) or float(value).is_integer():
            return str(int(value))
        return f"{value:.8f}".rstrip("0").rstrip(".")
