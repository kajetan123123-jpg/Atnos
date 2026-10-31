from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.providers.registry import ProviderRegistry
from atnos.services.browser_access_diagnostics_service import BrowserAccessDiagnosticsService
from atnos.services.desktop_browser_observer_service import (
    BrowserPageLearningView,
    DesktopBrowserObserverService,
)
from atnos.services.local_host_control_service import LocalHostControlError, LocalHostControlService
from atnos.services.web_site_resolve_service import WebSiteResolveService
from atnos.services.chat_tool_focus import memory_opt_out


BrowserChatAction = Literal["list_tabs", "learn_page", "learn_active_page", "open_and_learn"]


@dataclass(frozen=True, slots=True)
class BrowserChatOperationResult:
    activated: bool
    action: BrowserChatAction | None = None
    assistant_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class BrowserChatOperatorService:
    _URL_PATTERN = re.compile(r"https?://[^\s)>\]]+")
    _TAB_LIST_PATTERNS = (
        "jakie karty",
        "jakie taby",
        "jakie strony sa otwarte",
        "co jest otwarte w chrome",
        "co mam otwarte w chrome",
        "powiedz jakie karty",
        "list tabs",
        "open tabs",
    )
    _BROWSER_WORDS = ("chrome", "przegladar", "browser", "karta", "tab", "strona", "strone", "www")
    _LEARNING_WORDS = (
        "przeanalizuj",
        "sprawdz",
        "wyciagnij wnioski",
        "zapisz do obsydian",
        "zapisz do obsidian",
        "zapamietaj",
        "naucz sie z",
        "przejrzyj",
        "znajdz",
        "wyszukaj",
        "wydobadz",
        "wypisz",
        "podaj",
        "wyciagnij",
    )
    _ACTIVE_PAGE_WORDS = (
        "otwarta karta",
        "aktywna karta",
        "obecna karta",
        "to co widzisz",
        "to co jest otwarte",
    )
    _OPEN_SITE_RE = re.compile(
        r"(?:otworz|odpal|uruchom|wejdz na|odwiedz)\s+(?:mi\s+|nam\s+|prosze\s+)?"
        r"(?:strone|strona|portal|www|link)?\s*(.+)$",
        re.I,
    )
    _FOCUS_SPLIT_RE = re.compile(
        r"\s+(?:i|oraz|,)\s+(?:znajdz|sprawdz|podaj|wypisz|wydobadz|wyciagnij|wyszukaj)\s+",
        re.I,
    )

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.site_resolver = WebSiteResolveService(settings=settings)

    def maybe_handle(self, *, content: str, memory_enabled: bool = True) -> BrowserChatOperationResult:
        memory_enabled = memory_enabled and not memory_opt_out(content)
        normalized = self._normalize(content)
        if not normalized:
            return BrowserChatOperationResult(activated=False)

        if self._wants_tab_list(normalized):
            try:
                diagnostics = BrowserAccessDiagnosticsService(
                    self.session,
                    settings=self.settings,
                ).diagnose_chrome(tab_limit=10)
            except Exception as exc:
                return BrowserChatOperationResult(
                    activated=True,
                    action="list_tabs",
                    assistant_content=self._render_operator_error_message(
                        action_label="sprawdzenia kart Chrome",
                        exc=exc,
                    ),
                    metadata={
                        "browser_operator": {
                            "action": "list_tabs",
                            "error": str(exc),
                        }
                    },
                )
            return BrowserChatOperationResult(
                activated=True,
                action="list_tabs",
                assistant_content=self._render_tab_list_message(diagnostics),
                metadata={
                    "browser_operator": {
                        "action": "list_tabs",
                        "mode": diagnostics.mode,
                        "tab_count": len(diagnostics.tabs),
                        "detected_apps": list(diagnostics.detected_apps),
                    }
                },
            )

        open_and_learn = self._extract_open_and_learn(normalized, original=content)
        if open_and_learn is not None:
            return self._handle_open_and_learn(
                site_query=open_and_learn[0],
                research_focus=open_and_learn[1],
                original_content=content,
                memory_enabled=memory_enabled,
            )

        target_url = self._extract_first_url(content)
        if self._wants_learning(normalized, target_url=target_url):
            observer = DesktopBrowserObserverService(
                self.session,
                settings=self.settings,
                provider_registry=self.provider_registry,
            )
            try:
                if target_url is not None:
                    learning = observer.learn_from_webpage(
                        target_url=target_url,
                        research_focus=content.strip(),
                        note_title=self._note_title_for_url(target_url),
                        allowed_domains=[urlparse(target_url).hostname or ""],
                        headless=True,
                        persist_memory=memory_enabled,
                    )
                    action: BrowserChatAction = "learn_page"
                else:
                    learning = observer.learn_from_active_browser_page(
                        research_focus=content.strip(),
                        note_title="Active browser learning from chat",
                        title_hint=self._extract_browser_hint(normalized),
                        persist_memory=memory_enabled,
                    )
                    action = "learn_active_page"
            except Exception as exc:
                return BrowserChatOperationResult(
                    activated=True,
                    action="learn_page" if target_url is not None else "learn_active_page",
                    assistant_content=self._render_operator_error_message(
                        action_label="analizy strony w przegladarce",
                        exc=exc,
                    ),
                    metadata={
                        "browser_operator": {
                            "action": "learn_page" if target_url is not None else "learn_active_page",
                            "target_url": target_url,
                            "error": str(exc),
                        }
                    },
                )
            return BrowserChatOperationResult(
                activated=True,
                action=action,
                assistant_content=self._render_learning_message(learning),
                metadata={
                    "browser_operator": {
                        "action": action,
                        "target_url": learning.target_url,
                        "final_url": learning.final_url,
                        "vault_note_path": learning.vault_note_path,
                        "curated_path": learning.curated_path,
                        "knowledge_path": learning.knowledge_path,
                        "provider_key": learning.provider_key,
                        "model_key": learning.model_key,
                        "runtime_fallback": learning.runtime_fallback,
                        "instruction_context": learning.skill_instruction_context,
                        "page_title": learning.page_title,
                        "h1_headings": learning.h1_headings,
                        "literal_source_fields": learning.literal_source_fields,
                        "status": self._learning_status(learning),
                    }
                },
            )

        return BrowserChatOperationResult(activated=False)

    def _handle_open_and_learn(
        self,
        *,
        site_query: str,
        research_focus: str,
        original_content: str,
        memory_enabled: bool = True,
    ) -> BrowserChatOperationResult:
        resolved = self.site_resolver.resolve(site_query)
        if not resolved.url:
            return BrowserChatOperationResult(
                activated=True,
                action="open_and_learn",
                assistant_content=(
                    f"Nie rozwiazalem nazwy strony `{site_query}` do URL.\n\n"
                    f"{resolved.summary}\n"
                    "Podaj pelny link (https://...), wtedy od razu wyciagne dane."
                ),
                metadata={
                    "browser_operator": {
                        "action": "open_and_learn",
                        "status": "not_found",
                        "query": site_query,
                        "method": resolved.method,
                    }
                },
            )

        focus = research_focus.strip() or original_content.strip()
        target_url = self._prefer_contact_url(resolved.url, research_focus=focus)

        open_error: str | None = None
        try:
            LocalHostControlService(self.session, settings=self.settings).open_browser_url(target_url)
        except (LocalHostControlError, Exception) as exc:
            open_error = str(exc)

        observer = DesktopBrowserObserverService(
            self.session,
            settings=self.settings,
            provider_registry=self.provider_registry,
        )
        try:
            learning = observer.learn_from_webpage(
                target_url=target_url,
                research_focus=focus,
                note_title=self._note_title_for_url(target_url),
                allowed_domains=[urlparse(target_url).hostname or ""],
                headless=True,
                persist_memory=memory_enabled,
            )
        except Exception as exc:
            opened_note = f"Otworzylem `{target_url}`.\n" if open_error is None else ""
            return BrowserChatOperationResult(
                activated=True,
                action="open_and_learn",
                assistant_content=(
                    f"{opened_note}"
                    f"Nie udalo sie wyciagnac danych ze strony `{target_url}`.\n\n"
                    f"Powod: {exc}"
                ),
                metadata={
                    "browser_operator": {
                        "action": "open_and_learn",
                        "status": "learn_failed",
                        "url": target_url,
                        "query": site_query,
                        "open_error": open_error,
                        "error": str(exc),
                    }
                },
            )

        analysis = (learning.analysis or "").strip()
        # Deterministic contact facts so LLM fluff can't drop NIP/phone.
        facts = self._extract_contact_facts_from_learning(learning)
        if memory_enabled and resolved.method in {"duckduckgo", "domain_guess"} and resolved.url:
            try:
                from atnos.services.chat_session_memory import persist_site_alias_memory

                persist_site_alias_memory(
                    settings=self.settings,
                    query=site_query,
                    url=resolved.url,
                    enabled=True,
                    session=self.session,
                )
            except Exception:
                pass

        lines: list[str] = []
        if facts:
            lines.append("Znalezione dane:")
            lines.extend(f"- {line}" for line in facts)
            lines.append("")
            lines.append(f"Źródło: `{target_url}`")
            if analysis:
                short = analysis[:420].strip()
                if short:
                    lines.extend(["", short])
        else:
            lines.append(f"Otworzylem `{target_url}` ({resolved.method}).")
            if open_error:
                lines.append(f"Uwaga: otwarcie w systemie zglosilo problem: {open_error}")
            lines.extend(["", f"Cel: {focus}", ""])
            if analysis:
                lines.append(analysis[:1200].strip())
            else:
                lines.append("Strone otworzylem, ale analiza nie zwrocila czytelnych faktow.")
        if learning.vault_note_path:
            lines.extend(["", f"Notatka: `{learning.vault_note_path}`"])
        if open_error and facts:
            lines.extend(["", f"Uwaga otwarcia UI: {open_error}"])

        return BrowserChatOperationResult(
            activated=True,
            action="open_and_learn",
            assistant_content="\n".join(lines).strip(),
            metadata={
                "browser_operator": {
                    "action": "open_and_learn",
                    "status": self._learning_status(learning),
                    "instruction_context": learning.skill_instruction_context,
                    "query": site_query,
                    "url": target_url,
                    "resolved_home": resolved.url,
                    "final_url": learning.final_url,
                    "method": resolved.method,
                    "research_focus": focus,
                    "facts": facts,
                    "vault_note_path": learning.vault_note_path,
                    "open_error": open_error,
                }
            },
        )

    @staticmethod
    def _prefer_contact_url(url: str, *, research_focus: str) -> str:
        focus = (research_focus or "").casefold()
        if not any(token in focus for token in ("nip", "telefon", "kontakt", "adres", "email", "mail", "phone")):
            return url
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return url
        path = (parsed.path or "/").rstrip("/")
        if "kontakt" in path.casefold():
            return url
        return f"{parsed.scheme}://{parsed.netloc}/kontakt.html"

    @classmethod
    def _extract_contact_facts_from_learning(cls, learning: BrowserPageLearningView) -> list[str]:
        blobs: list[str] = []
        for value in (
            getattr(learning, "analysis", None),
            getattr(learning, "extracted_text", None),
            getattr(learning, "extracted_text_excerpt", None),
            getattr(learning, "page_text", None),
        ):
            if isinstance(value, str) and value.strip():
                blobs.append(value)
        # Some views keep raw text only in browser_artifacts.
        artifacts = getattr(learning, "browser_artifacts", None)
        if isinstance(artifacts, dict):
            for key in ("extracted_text", "page_text", "text"):
                value = artifacts.get(key)
                if isinstance(value, str) and value.strip():
                    blobs.append(value)
        text = "\n".join(blobs)
        if not text.strip():
            # Last resort: re-fetch contact page content for fact scrape.
            try:
                from atnos.services.desktop_browser_observer_service import DesktopBrowserObserverService

                url = str(getattr(learning, "final_url", "") or getattr(learning, "target_url", "") or "")
                if url.startswith("http"):
                    text, _ = DesktopBrowserObserverService._fetch_webpage_text_http(url)
            except Exception:
                text = ""
        facts: list[str] = []
        nip = re.search(r"\bNIP\s*[:\-]?\s*([0-9]{10})\b", text, re.I)
        if nip:
            facts.append(f"NIP: {nip.group(1)}")
        phones = re.findall(
            r"(?:\+48\s*)?(?:\d{3}[\s\-]?\d{3}[\s\-]?\d{3}|\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})",
            text,
        )
        cleaned_phones: list[str] = []
        for phone in phones:
            digits = re.sub(r"\D", "", phone)
            if len(digits) < 9:
                continue
            if nip and (
                digits == nip.group(1)
                or nip.group(1).startswith(digits)
                or digits.startswith(nip.group(1)[:9])
            ):
                continue
            # Prefer real PL phones (+48XXXXXXXXX or 9-digit mobile), skip 10-digit NIP-like.
            if len(digits) == 10 and not str(phone).strip().startswith("+"):
                continue
            if len(digits) == 11 and digits.startswith("48"):
                pass
            elif len(digits) not in {9, 11}:
                continue
            normalized = phone.strip()
            if normalized not in cleaned_phones:
                cleaned_phones.append(normalized)
        for phone in cleaned_phones[:3]:
            facts.append(f"Telefon: {phone}")
        emails = re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text, re.I)
        for email in emails[:3]:
            facts.append(f"Email: {email}")
        return facts

    def _extract_open_and_learn(self, normalized: str, *, original: str) -> tuple[str, str] | None:
        has_open = any(token in normalized for token in ("otworz", "odpal", "uruchom", "wejdz", "odwiedz"))
        has_site = any(
            token in normalized for token in ("strone", "strona", "portal", "www.", "http://", "https://")
        )
        if not (has_open and has_site):
            return None

        url = self._extract_first_url(original)
        if url:
            focus = original
            for part in re.split(r"https?://[^\s)>\]]+", original, maxsplit=1):
                cleaned = part.strip(" ,.-")
                if cleaned:
                    focus = cleaned
                    break
            return url, focus.strip() or original.strip()

        match = self._OPEN_SITE_RE.search(normalized)
        if not match:
            return None
        tail = match.group(1).strip()
        if not tail:
            return None
        parts = self._FOCUS_SPLIT_RE.split(tail, maxsplit=1)
        site_query = parts[0].strip(" .,!")
        site_query = re.sub(r"^(strone|strona|portal|www)\s+", "", site_query).strip()
        research = parts[1].strip() if len(parts) > 1 else tail
        if not site_query:
            return None
        return site_query, research or original.strip()

    def _wants_tab_list(self, normalized: str) -> bool:
        return any(pattern in normalized for pattern in self._TAB_LIST_PATTERNS)

    def _wants_learning(self, normalized: str, *, target_url: str | None) -> bool:
        if target_url is not None and any(word in normalized for word in self._LEARNING_WORDS):
            return True
        if any(word in normalized for word in self._ACTIVE_PAGE_WORDS) and any(
            word in normalized for word in self._LEARNING_WORDS
        ):
            return True
        if any(word in normalized for word in ("obsydian", "obsidian")) and any(
            browser_word in normalized for browser_word in self._BROWSER_WORDS
        ):
            return True
        return False

    @classmethod
    def _extract_first_url(cls, content: str) -> str | None:
        match = cls._URL_PATTERN.search(content)
        if match is None:
            return None
        return match.group(0).rstrip(".,)")

    @staticmethod
    def _extract_browser_hint(normalized: str) -> str | None:
        if "chrome" in normalized:
            return "chrome"
        if "opera" in normalized:
            return "opera"
        if "edge" in normalized:
            return "edge"
        return None

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
        return " ".join(ascii_only.split())

    @staticmethod
    def _note_title_for_url(target_url: str) -> str:
        host = (urlparse(target_url).hostname or "webpage").strip().lower()
        return f"Browser learning from chat - {host}"

    @staticmethod
    def _render_tab_list_message(diagnostics) -> str:
        if not diagnostics.tabs:
            reasons = "\n".join(f"- {reason}" for reason in diagnostics.reasons)
            return (
                "Nie mam teraz czytelnej listy kart Chrome.\n\n"
                f"Tryb dostepu: `{diagnostics.mode}`.\n"
                "Powod:\n"
                f"{reasons}"
            )
        lines = [
            f"Chrome access: `{diagnostics.mode}`.",
            f"Widze {len(diagnostics.tabs)} kart.",
        ]
        if diagnostics.detected_apps:
            lines.append(f"Wykryte web-appki: {', '.join(diagnostics.detected_apps)}.")
        lines.append("")
        for index, tab in enumerate(diagnostics.tabs[:8], start=1):
            title = str(tab.get("title") or "(bez tytulu)")
            url = str(tab.get("url") or "")
            lines.append(f"{index}. {title}\n   {url}")
        return "\n".join(lines).strip()

    @staticmethod
    def _render_operator_error_message(*, action_label: str, exc: Exception) -> str:
        detail = " ".join(str(exc).split()).strip() or "Nieznany blad lokalnego operatora."
        return (
            f"Nie dowiozlem teraz {action_label}.\n\n"
            f"Dokladny powod: {detail}"
        )

    @staticmethod
    def _learning_status(learning: BrowserPageLearningView) -> str:
        return "incomplete" if (
            not learning.ok
            or "analysis_incomplete" in str(learning.runtime_fallback or "")
            or str(learning.analysis or "").casefold().startswith("analiza niekompletna")
        ) else "ok"

    @staticmethod
    def _render_learning_message(learning: BrowserPageLearningView) -> str:
        summary = (learning.analysis or "").strip()
        if learning.literal_source_fields:
            introduction = ('Odczyt pól strony:' if BrowserChatOperatorService._learning_status(learning) == 'ok'
                            else 'Nie potwierdzono wszystkich żądanych pól strony:')
            return introduction + '\n\n' + summary
        concise_summary = summary if learning.literal_source_fields else summary[:1400].strip()
        artifacts = ", ".join(
            f"{key}={value}"
            for key, value in learning.browser_artifacts.items()
        ) or "brak"
        incomplete = BrowserChatOperatorService._learning_status(learning) == "incomplete"
        introduction = (
            f"Nie ukończyłem analizy źródła `{learning.final_url}`."
            if incomplete else f"Przeanalizowałem źródło `{learning.final_url}`."
        )
        return (
            f"{introduction}\n\n"
            f"Notatka Obsidian: `{learning.vault_note_path or 'brak'}`\n"
            f"Curated note: `{learning.curated_path or 'brak'}`\n"
            f"Model analizy: `{learning.provider_key or 'unknown'}` / `{learning.model_key or 'unknown'}`\n"
            f"Artefakty browsera: {artifacts}\n\n"
            f"{concise_summary}"
        ).strip()
