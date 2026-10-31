from __future__ import annotations

"""Cytowany research źródeł publicznych dla Portfolio Alpha.

Worker tworzy wyłącznie krótki, deterministyczny digest z cytatami. Nie tworzy
sygnałów transakcyjnych, nie uruchamia LLM i nigdy nie aktualizuje strategii
paper-tradingu. Dzięki temu raport zachowuje pochodzenie danych także wtedy,
gdy lokalny model nie jest dostępny.
"""

import html
import json
import re
from typing import Any
from uuid import uuid4

import httpx

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_dumps, json_loads, rows_to_dicts, utc_now_iso
from atnos.investment.education_sources import InvestmentEducationSourceRegistry
from atnos.services.source_envelope_service import (
    MemoryEligibility,
    PermissionMode,
    PrivacyClass,
    RetentionPolicy,
    SourceCitation,
    SourceEnvelope,
    SourceEnvelopeService,
    SourceKind,
)

MAX_SOURCES = 4
MAX_RESPONSE_BYTES = 350_000
MAX_EXCERPT_CHARS = 220


class InvestmentEducationResearchService:
    """Buduje trwały digest tylko z wcześniej sprawdzonych adresów publicznych."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sources = InvestmentEducationSourceRegistry(settings)
        self.envelopes = SourceEnvelopeService()

    def list_reports(self, *, limit: int = 10) -> list[dict[str, Any]]:
        self._ensure_schema()
        safe_limit = max(1, min(int(limit), 50))
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT id, created_at, status, source_count, citation_count,
                       digest_json, citations_json, llm_synthesis_status,
                       error_message, report_markdown
                FROM investment_education_reports
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        reports = rows_to_dicts(rows)
        for report in reports:
            report["digest"] = json_loads(report.pop("digest_json", None), fallback={})
            report["citations"] = json_loads(report.pop("citations_json", None), fallback=[])
        return reports

    async def create_cited_digest(self, *, max_sources: int = MAX_SOURCES) -> dict[str, Any]:
        """Pobierz kontrolowaną liczbę źródeł i zapisz bezpieczne cytaty."""
        self._ensure_schema()
        candidates = [
            item
            for item in self.sources.list_sources()
            if item.get("verification_status") == "available"
            and item.get("access_scope") in {"public_reference", "public_reference_only"}
            and item.get("url")
        ][: max(1, min(int(max_sources), MAX_SOURCES))]
        if not candidates:
            return {
                "status": "blocked",
                "reason": "verified_public_education_sources_required",
                "source_count": 0,
                "citation_count": 0,
                "llm_synthesis_status": "not_requested",
                "safe_for_paper_strategy": False,
                "real_trading_enabled": False,
                "next_action": "Register and verify public education sources first.",
            }

        captured_at = utc_now_iso()
        entries: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        errors: list[str] = []
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "AtnosCitedResearch/1.0"},
        ) as client:
            for source in candidates:
                try:
                    fetched = await self._fetch_source(client, source)
                    envelope, _summary = self.envelopes.preview_envelope(
                        SourceEnvelope(
                            source=SourceKind.UNKNOWN,
                            source_item_id=str(source["source_id"]),
                            origin_uri=str(source["url"]),
                            title=str(fetched["title"]),
                            content_type="text/html",
                            privacy_class=PrivacyClass.PUBLIC,
                            retention_policy=RetentionPolicy.LONG_LIVED,
                            permission_mode=PermissionMode.READ_ONLY,
                            memory_eligibility=MemoryEligibility.PROJECT_MEMORY_CANDIDATE,
                            allowed_actions=["research", "summarization"],
                            metadata={
                                "publisher": source.get("publisher"),
                                "access_scope": source.get("access_scope"),
                                "captured_at": captured_at,
                            },
                            content_preview=str(fetched["excerpt"]),
                            citations=[
                                SourceCitation(
                                    label=str(source.get("title") or fetched["title"]),
                                    locator="public page excerpt",
                                    origin_uri=str(source["url"]),
                                    quote=str(fetched["excerpt"]),
                                )
                            ],
                        )
                    )
                    citation = envelope.citations[0].model_dump(mode="json") if envelope.citations else {}
                    entries.append(
                        {
                            "source_id": str(source["source_id"]),
                            "title": envelope.title or str(source["title"]),
                            "publisher": str(source.get("publisher") or ""),
                            "url": str(source["url"]),
                            "excerpt": envelope.content_preview or "",
                            "access_scope": str(source.get("access_scope") or "public_reference"),
                        }
                    )
                    if citation:
                        citations.append(citation)
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(f"{source.get('source_id')}: {type(exc).__name__}")

        report = self._persist_report(
            created_at=captured_at,
            entries=entries,
            citations=citations,
            errors=errors,
        )
        return {
            **report,
            "safe_for_paper_strategy": False,
            "real_trading_enabled": False,
            "next_action": (
                "Review the cited digest, then request a separate supervised research synthesis. "
                "No paper strategy is changed by this report."
            ),
        }

    async def _fetch_source(self, client: httpx.AsyncClient, source: dict[str, Any]) -> dict[str, str]:
        response = await client.get(str(source["url"]), headers={"Range": f"bytes=0-{MAX_RESPONSE_BYTES - 1}"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        raw_content = response.content[:MAX_RESPONSE_BYTES].decode(response.encoding or "utf-8", errors="replace")
        if "json" in content_type:
            return self._extract_json_snapshot(raw_content, fallback_title=str(source.get("title") or "Public source"))
        if "html" not in content_type and "text" not in content_type:
            raise ValueError("Source did not return a text, HTML or JSON response.")
        title = self._extract_title(raw_content, fallback=str(source.get("title") or "Public source"))
        excerpt = self._extract_excerpt(raw_content)
        if not excerpt:
            raise ValueError("Source returned no usable text excerpt.")
        return {"title": title, "excerpt": excerpt}

    @staticmethod
    def _extract_json_snapshot(raw_content: str, *, fallback_title: str) -> dict[str, str]:
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ValueError("Source returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Source returned a JSON payload without a single public snapshot.")
        allowed_fields = (
            "symbol",
            "lastPrice",
            "price",
            "openPrice",
            "highPrice",
            "lowPrice",
            "priceChangePercent",
            "closeTime",
        )
        fields = [f"{key}={payload[key]}" for key in allowed_fields if key in payload]
        if not fields:
            raise ValueError("Source JSON payload did not contain a usable public market-data field.")
        symbol = str(payload.get("symbol") or "market")
        return {
            "title": f"{fallback_title} ({symbol})"[:180],
            "excerpt": ("Public market-data snapshot. " + ", ".join(fields) + ". No trading signal is inferred.")[:MAX_EXCERPT_CHARS],
        }

    def _persist_report(
        self,
        *,
        created_at: str,
        entries: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        errors: list[str],
    ) -> dict[str, Any]:
        report_id = f"investment_research_{uuid4().hex}"
        status = "completed" if entries and not errors else "degraded" if entries else "failed"
        digest = {
            "kind": "cited_source_digest",
            "entries": entries,
            "errors": errors,
            "generated_from_verified_sources_only": True,
        }
        report_markdown = self._report_markdown(created_at=created_at, entries=entries, errors=errors)
        with connect(self.settings) as db:
            db.execute(
                """
                INSERT INTO investment_education_reports (
                    id, created_at, status, source_count, citation_count, digest_json,
                    citations_json, llm_synthesis_status, error_message, report_markdown
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'not_requested', ?, ?)
                """,
                (
                    report_id,
                    created_at,
                    status,
                    len(entries),
                    len(citations),
                    json_dumps(digest),
                    json_dumps(citations),
                    "; ".join(errors)[:1000] if errors else None,
                    report_markdown,
                ),
            )
            db.commit()
        return {
            "id": report_id,
            "created_at": created_at,
            "status": status,
            "source_count": len(entries),
            "citation_count": len(citations),
            "digest": digest,
            "citations": citations,
            "llm_synthesis_status": "not_requested",
            "error_message": "; ".join(errors) if errors else None,
            "report_markdown": report_markdown,
        }

    def _ensure_schema(self) -> None:
        with connect(self.settings) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS investment_education_reports (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    citation_count INTEGER NOT NULL DEFAULT 0,
                    digest_json TEXT NOT NULL DEFAULT '{}',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    llm_synthesis_status TEXT NOT NULL DEFAULT 'not_requested',
                    error_message TEXT,
                    report_markdown TEXT NOT NULL DEFAULT ''
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_investment_education_reports_created
                ON investment_education_reports (created_at DESC)
                """
            )
            db.commit()

    @staticmethod
    def _extract_title(raw_html: str, *, fallback: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
        return InvestmentEducationResearchService._clean_text(match.group(1))[:180] if match else fallback[:180]

    @staticmethod
    def _extract_excerpt(raw_html: str) -> str:
        without_noise = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", without_noise, flags=re.IGNORECASE | re.DOTALL)
        cleaned = [InvestmentEducationResearchService._clean_text(item) for item in paragraphs]
        usable = next((item for item in cleaned if 80 <= len(item) <= 1_200), "")
        if not usable:
            usable = InvestmentEducationResearchService._clean_text(without_noise)
        return usable[:MAX_EXCERPT_CHARS].rstrip()

    @staticmethod
    def _clean_text(value: str) -> str:
        without_tags = re.sub(r"<[^>]+>", " ", value)
        return " ".join(html.unescape(without_tags).split())

    @staticmethod
    def _report_markdown(*, created_at: str, entries: list[dict[str, Any]], errors: list[str]) -> str:
        lines = ["# Portfolio Alpha - Cited source digest", f"Generated: {created_at}", ""]
        for entry in entries:
            lines.extend(
                [
                    f"## {entry['title']}",
                    f"Source: {entry['url']}",
                    f"Excerpt: {entry['excerpt']}",
                    "",
                ]
            )
        if errors:
            lines.extend(["## Fetch errors", *[f"- {item}" for item in errors]])
        lines.append("This digest is not a trading signal and does not alter paper strategy.")
        return "\n".join(lines)
