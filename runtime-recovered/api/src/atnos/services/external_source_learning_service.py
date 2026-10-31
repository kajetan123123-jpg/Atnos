from __future__ import annotations

"""Import zewnetrznych zrodel do workspace wiedzy Atnosa.

Modul celowo nie wymusza ciezkich zaleznosci. Jezeli `crawl4ai` jest dostepny,
Atnos uzywa go jako glownego crawlera. Jezeli nie, zapisuje jawny status
fallbacku i korzysta z prostego pobrania HTML przez httpx.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import html
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from atnos.services.knowledge_workspace_service import KnowledgeWorkspaceService

if TYPE_CHECKING:
    from atnos.core.config import Settings


MAX_CRAWL_TEXT_CHARS = 28_000
ALLOWED_SCHEMES = {"http", "https"}


@dataclass(frozen=True, slots=True)
class ExternalApiSource:
    key: str
    name: str
    category: str
    url: str
    source: str
    auth: str
    use_cases: tuple[str, ...]
    notes: str
    priority: int = 50

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["use_cases"] = list(self.use_cases)
        return payload


@dataclass(frozen=True, slots=True)
class CrawledSource:
    url: str
    final_url: str
    title: str
    text: str
    crawler: str
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PUBLIC_API_CATALOG: tuple[ExternalApiSource, ...] = (
    ExternalApiSource(
        key="public_apis_index",
        name="public-apis/public-apis",
        category="source_catalog",
        url="https://github.com/public-apis/public-apis",
        source="github",
        auth="mixed",
        use_cases=(
            "wyszukiwanie nowych darmowych zrodel danych",
            "uzupelnianie researchu bez zgadywania LLM",
            "lista kandydatow API do recznej walidacji przed integracja",
        ),
        notes="Katalog zrodel, nie runtime dependency. API z listy trzeba walidowac osobno.",
        priority=100,
    ),
    ExternalApiSource(
        key="sec_companyfacts",
        name="SEC Companyfacts",
        category="finance",
        url="https://www.sec.gov/edgar/sec-api-documentation",
        source="official",
        auth="none",
        use_cases=("fakty finansowe spolek", "material do kanalow finance education", "benchmarki fundamentalne"),
        notes="Wymaga respektowania limitow SEC i poprawnego User-Agent.",
        priority=90,
    ),
    ExternalApiSource(
        key="fred",
        name="FRED Economic Data",
        category="macro",
        url="https://fred.stlouisfed.org/docs/api/fred/",
        source="official",
        auth="api_key",
        use_cases=("makroekonomia", "inflacja i stopy procentowe", "research pod tematy finansowe"),
        notes="Dobre zrodlo danych do narracji makro, po podlaczeniu klucza.",
        priority=85,
    ),
    ExternalApiSource(
        key="wikipedia_rest",
        name="Wikipedia REST API",
        category="knowledge",
        url="https://www.mediawiki.org/wiki/API:REST_API",
        source="official",
        auth="none",
        use_cases=("fakty do skryptow", "weryfikacja nazw i dat", "kontekst historyczny"),
        notes="Juz uzywane w module research; trzymane w katalogu jako pewne zrodlo wiedzy.",
        priority=82,
    ),
    ExternalApiSource(
        key="youtube_data_api",
        name="YouTube Data API v3",
        category="content_intelligence",
        url="https://developers.google.com/youtube/v3",
        source="official",
        auth="oauth_or_api_key",
        use_cases=("trendy wideo", "upload prywatny", "metadata kanalow", "analytics po OAuth"),
        notes="Uzywac minimalnych scope i limitow zgodnych z compliance review.",
        priority=95,
    ),
    ExternalApiSource(
        key="brave_search",
        name="Brave Search API",
        category="search",
        url="https://api.search.brave.com/app/documentation/web-search/get-started",
        source="official",
        auth="api_key",
        use_cases=("fallback web research", "news dla tematow", "sygnaly popytu wyszukiwania"),
        notes="Juz obslugiwane jako fallback w research_client.",
        priority=78,
    ),
)


class ExternalSourceLearningService:
    """Laczy crawl4ai, katalog publicznych API i lokalny RAG Atnosa."""

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.workspace = KnowledgeWorkspaceService(settings)

    def public_api_catalog(self, *, category: str | None = None) -> list[dict[str, Any]]:
        normalized_category = (category or "").strip().casefold()
        entries = [
            entry
            for entry in PUBLIC_API_CATALOG
            if not normalized_category or entry.category.casefold() == normalized_category
        ]
        return [entry.to_dict() for entry in sorted(entries, key=lambda item: (-item.priority, item.key))]

    def ingest_public_api_catalog(self) -> dict[str, Any]:
        entries = self.public_api_catalog()
        body = [
            "## Rola katalogu",
            "Ten wpis jest lokalnym katalogiem kandydatow API dla Atnosa. Nie oznacza automatycznego uzycia kazdego API.",
            "",
            "## Zrodla",
        ]
        for item in entries:
            body.extend(
                [
                    f"### {item['name']}",
                    f"- key: `{item['key']}`",
                    f"- category: `{item['category']}`",
                    f"- url: {item['url']}",
                    f"- auth: `{item['auth']}`",
                    f"- use_cases: {', '.join(item['use_cases'])}",
                    f"- notes: {item['notes']}",
                    "",
                ]
            )
        note = self.workspace.save_curated_note(
            bucket="general",
            title="Public API source catalog for Atnos",
            summary="Curated source catalog based on public-apis plus official APIs already relevant to Atnos.",
            body_markdown="\n".join(body),
            keywords=["public APIs", "research sources", "data catalog", "RAG", "source discovery"],
            tags=["public-apis", "source-catalog", "research", "rag"],
            source_label="Atnos external source catalog",
            source_url="https://github.com/public-apis/public-apis",
            license_label="Curated references; verify each source license before use",
        )
        return {"status": "ingested", "entry_count": len(entries), **note}

    async def crawl_and_ingest(
        self,
        *,
        url: str,
        bucket: str = "general",
        title: str | None = None,
        research_focus: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_url = self._validate_url(url)
        crawled = await self.crawl_url(normalized_url)
        note_title = title or crawled.title or f"External source - {urlparse(crawled.final_url).netloc}"
        focus = (research_focus or "").strip()
        body = [
            f"- crawler: `{crawled.crawler}`",
            f"- status: `{crawled.status}`",
            f"- url: {normalized_url}",
            f"- final_url: {crawled.final_url}",
        ]
        if focus:
            body.append(f"- research_focus: {focus}")
        if crawled.error:
            body.append(f"- error: {crawled.error}")
        body.extend(["", "## Extracted content", "", crawled.text.strip()])
        note = self.workspace.save_curated_note(
            bucket=bucket,
            title=note_title,
            summary=self._summary(crawled.text, fallback=f"External source imported from {normalized_url}."),
            body_markdown="\n".join(body),
            keywords=self._keywords(note_title, focus, crawled.text),
            tags=["external-source", "crawl4ai", "rag", *(tags or [])],
            source_label=f"External web source via {crawled.crawler}",
            source_url=crawled.final_url,
            license_label="External source excerpt; respect original source terms",
        )
        return {"status": "ingested", "crawl": crawled.to_dict(), **note}

    async def crawl_url(self, url: str) -> CrawledSource:
        normalized_url = self._validate_url(url)
        crawl4ai_result = await self._crawl_with_crawl4ai(normalized_url)
        if crawl4ai_result is not None:
            return crawl4ai_result
        return await self._fetch_basic_html(normalized_url)

    async def _crawl_with_crawl4ai(self, url: str) -> CrawledSource | None:
        try:
            from crawl4ai import AsyncWebCrawler  # type: ignore
        except Exception:
            return None

        try:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url)
        except Exception as exc:
            return CrawledSource(
                url=url,
                final_url=url,
                title="",
                text="",
                crawler="crawl4ai",
                status="error",
                error=str(exc),
            )

        text = str(
            getattr(result, "markdown", None)
            or getattr(result, "extracted_content", None)
            or getattr(result, "cleaned_html", None)
            or getattr(result, "html", None)
            or ""
        )
        final_url = str(getattr(result, "url", None) or url)
        title = str(getattr(result, "metadata", {}).get("title") if isinstance(getattr(result, "metadata", None), dict) else "")
        return CrawledSource(
            url=url,
            final_url=final_url,
            title=title or self._title_from_text(text),
            text=self._clean_text(text),
            crawler="crawl4ai",
            status="ok" if text.strip() else "empty",
            error=None if text.strip() else "crawl4ai returned empty content",
        )

    async def _fetch_basic_html(self, url: str) -> CrawledSource:
        try:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                headers={"user-agent": "AtnosExternalSourceLearning/1.0"},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return CrawledSource(
                url=url,
                final_url=url,
                title="",
                text="",
                crawler="basic_httpx",
                status="error",
                error=str(exc),
            )
        text = response.text
        return CrawledSource(
            url=url,
            final_url=str(response.url),
            title=self._html_title(text),
            text=self._html_to_text(text),
            crawler="basic_httpx",
            status="fallback_basic_html",
            error="crawl4ai is not installed; used basic HTML extraction",
        )

    @staticmethod
    def _validate_url(url: str) -> str:
        normalized = (url or "").strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
            raise ValueError("Only absolute http(s) URLs can be imported.")
        return normalized

    @staticmethod
    def _html_title(value: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", value, flags=re.IGNORECASE | re.DOTALL)
        return ExternalSourceLearningService._clean_text(match.group(1))[:160] if match else ""

    @staticmethod
    def _html_to_text(value: str) -> str:
        cleaned = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        return ExternalSourceLearningService._clean_text(cleaned)

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(html.unescape(str(value or "")).split())[:MAX_CRAWL_TEXT_CHARS]

    @staticmethod
    def _title_from_text(value: str) -> str:
        first_line = next((line.strip("# ").strip() for line in str(value).splitlines() if line.strip()), "")
        return " ".join(first_line.split())[:160]

    @staticmethod
    def _summary(text: str, *, fallback: str) -> str:
        cleaned = ExternalSourceLearningService._clean_text(text)
        if not cleaned:
            return fallback
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        return " ".join(sentences[:2])[:320] or cleaned[:320]

    @staticmethod
    def _keywords(*values: str) -> list[str]:
        text = " ".join(values).casefold()
        tokens = re.findall(r"[a-z0-9][a-z0-9_-]{3,}", text)
        seen: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.append(token)
            if len(seen) >= 12:
                break
        return seen or ["external-source", "research", "rag"]


def external_source_learning_status() -> dict[str, Any]:
    try:
        import crawl4ai  # type: ignore  # noqa: F401

        crawl4ai_available = True
    except Exception:
        crawl4ai_available = False
    return {
        "crawl4ai": {
            "available": crawl4ai_available,
            "role": "primary crawler for education, research and RAG ingestion",
            "fallback": "basic_httpx_html_extraction",
            "install_command": "pip install crawl4ai",
        },
        "public_apis": {
            "available": True,
            "role": "local catalog of API/data-source candidates",
            "entry_count": len(PUBLIC_API_CATALOG),
            "source_url": "https://github.com/public-apis/public-apis",
        },
        "turbovec": {
            "available": False,
            "role": "future PoC only if SQLite semantic index becomes too slow",
            "decision": "not a production dependency in this iteration",
        },
        "easyslice_ai": {
            "available": False,
            "role": "UX and automation benchmark for clipping workflows",
            "decision": "not a dependency; use as product pattern",
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }
