"""Resolve a natural site/query name into http(s) URLs without inventing domains."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class WebSiteResolveResult:
    query: str
    url: str | None
    candidates: tuple[str, ...]
    method: str
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "url": self.url,
            "candidates": list(self.candidates),
            "method": self.method,
            "summary": self.summary,
        }


def site_aliases_path(settings: Settings | None = None) -> Path:
    if settings is not None and getattr(settings, "data_dir", None) is not None:
        return Path(settings.data_dir).resolve() / "site-aliases.json"
    return Path("E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data") / "site-aliases.json"


def load_persisted_site_aliases(settings: Settings | None = None) -> dict[str, str]:
    path = site_aliases_path(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    aliases: dict[str, str] = {}
    for key, value in raw.items():
        name = " ".join(str(key or "").casefold().split()).strip()
        url = str(value or "").strip()
        if name and url.startswith(("http://", "https://")):
            aliases[name] = url
    return aliases


def persist_site_alias_file(
    *,
    query: str,
    url: str,
    settings: Settings | None = None,
) -> Path | None:
    cleaned_query = " ".join(str(query or "").split()).strip()
    cleaned_url = str(url or "").strip()
    if not cleaned_query or not cleaned_url.startswith(("http://", "https://")):
        return None
    path = site_aliases_path(settings)
    aliases = load_persisted_site_aliases(settings)
    key = cleaned_query.casefold()
    if aliases.get(key) == cleaned_url:
        return path if path.exists() else None
    aliases[key] = cleaned_url
    compact = re.sub(r"\s+", " ", key)
    aliases[compact] = cleaned_url
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(sorted(aliases.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path
    except OSError:
        return None


class WebSiteResolveService:
    """Resolve 'Forteca IT' / 'strona X' into concrete https URLs."""

    KNOWN_SITE_ALIASES: dict[str, str] = {
        "forteca it": "https://fortecait.pl/",
        "forteca": "https://fortecait.pl/",
        "fortecait": "https://fortecait.pl/",
        "fortecait.pl": "https://fortecait.pl/",
        "youtube": "https://www.youtube.com/",
        "github": "https://github.com/",
        "google": "https://www.google.com/",
        "linkedin": "https://www.linkedin.com/",
    }

    _URL_RE = re.compile(r"^https?://", re.I)
    _DOMAIN_RE = re.compile(
        r"^(?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+/?$",
        re.I,
    )
    _RESULT_HREF_RE = re.compile(
        r'uddg=([^&"]+)|href="(https?://[^"]+)"',
        re.I,
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def resolve(self, query: str, *, limit: int = 5) -> WebSiteResolveResult:
        cleaned = " ".join(str(query or "").strip().split())
        if not cleaned:
            return WebSiteResolveResult(
                query="",
                url=None,
                candidates=(),
                method="empty",
                summary="Puste zapytanie o stronę.",
            )

        if self._URL_RE.match(cleaned):
            return WebSiteResolveResult(
                query=cleaned,
                url=cleaned,
                candidates=(cleaned,),
                method="literal_url",
                summary=f"Użyto podanego URL: {cleaned}",
            )

        normalized = cleaned.casefold().strip().rstrip("/")
        alias = self.KNOWN_SITE_ALIASES.get(normalized)
        if alias is None:
            alias = load_persisted_site_aliases(self.settings).get(normalized)
        if alias:
            return WebSiteResolveResult(
                query=cleaned,
                url=alias,
                candidates=(alias,),
                method="alias",
                summary=f"Dopasowano alias '{cleaned}' -> {alias}",
            )

        domain_candidate = normalized.replace(" ", "")
        if self._DOMAIN_RE.match(domain_candidate):
            url = f"https://{domain_candidate.rstrip('/')}/"
            return WebSiteResolveResult(
                query=cleaned,
                url=url,
                candidates=(url,),
                method="domain_guess",
                summary=f"Traktuję „{cleaned}” jako domenę: {url}",
            )

        candidates = self._duckduckgo_candidates(cleaned, limit=limit)
        if candidates:
            return WebSiteResolveResult(
                query=cleaned,
                url=candidates[0],
                candidates=tuple(candidates),
                method="duckduckgo",
                summary=f"Znaleziono {len(candidates)} kandydat(ów) dla „{cleaned}”; wybieram {candidates[0]}",
            )

        return WebSiteResolveResult(
            query=cleaned,
            url=None,
            candidates=(),
            method="unresolved",
            summary=(
                f"Nie udało się jednoznacznie rozwiązać „{cleaned}” do URL. "
                "Podaj dokładniejszą nazwę lub pełny link."
            ),
        )

    def _duckduckgo_candidates(self, query: str, *, limit: int) -> list[str]:
        urls: list[str] = []
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                response = client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        )
                    },
                )
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        html = unescape(response.text or "")
        for match in self._RESULT_HREF_RE.finditer(html):
            raw = match.group(1) or match.group(2) or ""
            candidate = unquote(raw).strip()
            if "uddg=" in candidate:
                parsed = urlparse(candidate)
                qs = parse_qs(parsed.query)
                uddg = qs.get("uddg", [None])[0]
                if uddg:
                    candidate = unquote(uddg)
            if not candidate.startswith(("http://", "https://")):
                continue
            host = (urlparse(candidate).hostname or "").lower()
            if not host or host.endswith("duckduckgo.com"):
                continue
            if candidate in urls:
                continue
            urls.append(candidate)
            if len(urls) >= max(1, limit):
                break
        return urls
