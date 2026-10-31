from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import unicodedata
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.trend_watch_snapshot_repository import TrendWatchSnapshotRepository
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.runtime_settings_service import RuntimeSettingsService

GOOGLE_TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo={region}"
TIKTOK_CREATIVE_CENTER_URL = "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en"
DEFAULT_TREND_QUERY = "short video hooks"
DEFAULT_TREND_REGION = "PL"
DEFAULT_INTERVAL_MINUTES = 180
GOOGLE_NAMESPACE = {"ht": "https://trends.google.com/trending/rss"}
TIKTOK_NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class TrendWatchService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = TrendWatchSnapshotRepository(session)
        self.runtime_settings = RuntimeSettingsService(session, settings)
        self.integration_config = IntegrationConfigService(session, settings)

    def latest_payload(self, *, project_id: int | None = None) -> dict[str, object] | None:
        item = self.repository.latest_project_or_global(project_id)
        if item is None:
            return None
        return self._serialize_snapshot(item)

    def run_due_watch_if_needed(self) -> dict[str, object] | None:
        runtime = self.runtime_settings.snapshot()
        if not runtime.trend_watch_enabled:
            return None
        interval_minutes = max(30, int(runtime.trend_watch_interval_minutes))
        now = datetime.now(UTC)
        if (
            runtime.trend_watch_last_run_at is not None
            and now - runtime.trend_watch_last_run_at < timedelta(minutes=interval_minutes)
        ):
            return None
        return self.run_watch(force=True)

    def run_watch(self, *, force: bool = False, project_id: int | None = None) -> dict[str, object]:
        runtime = self.runtime_settings.snapshot()
        if not force and not runtime.trend_watch_enabled:
            raise RuntimeError("Trend watch is disabled in runtime controls.")

        query = (runtime.trend_watch_query or DEFAULT_TREND_QUERY).strip() or DEFAULT_TREND_QUERY
        region = (runtime.trend_watch_region or DEFAULT_TREND_REGION).strip().upper() or DEFAULT_TREND_REGION
        interval_minutes = max(30, int(runtime.trend_watch_interval_minutes))
        collected_at = datetime.now(UTC)

        google_signals = self._fetch_google_trends(region=region)
        tiktok_signals = self._fetch_tiktok_hashtags()
        if not google_signals and not tiktok_signals:
            raise RuntimeError("Trend watch could not collect any public signals from the internet.")

        top_signals = [*google_signals[:8], *tiktok_signals[:8]]
        summary = self._build_summary(
            topic_query=query,
            region_code=region,
            top_signals=top_signals,
            interval_minutes=interval_minutes,
            collected_at=collected_at,
        )
        evaluation_notes = self._build_evaluation_notes(summary=summary, top_signals=top_signals)
        note_path = self._write_obsidian_note(
            topic_query=query,
            region_code=region,
            top_signals=top_signals,
            summary=summary,
            evaluation_notes=evaluation_notes,
        )
        snapshot = self.repository.create(
            project_id=project_id,
            topic_query=query,
            region_code=region,
            status="ready",
            source_mix_json=[
                {
                    "source": "google_trends_rss",
                    "url": GOOGLE_TRENDS_RSS_URL.format(region=region),
                    "status": "ready" if google_signals else "empty",
                    "signals_count": len(google_signals),
                },
                {
                    "source": "tiktok_creative_center",
                    "url": TIKTOK_CREATIVE_CENTER_URL,
                    "status": "ready" if tiktok_signals else "empty",
                    "signals_count": len(tiktok_signals),
                },
            ],
            top_signals_json=top_signals,
            summary_json=summary,
            evaluation_notes_json=evaluation_notes,
            note_path=note_path,
            failure_reason=None,
            collected_at=collected_at,
            expires_at=collected_at + timedelta(minutes=interval_minutes),
        )
        self.runtime_settings.update_owner_controls(trend_watch_last_run_at=collected_at)
        return self._serialize_snapshot(snapshot)

    def _fetch_google_trends(self, *, region: str) -> list[dict[str, object]]:
        text = self._fetch_text(GOOGLE_TRENDS_RSS_URL.format(region=region))
        root = ElementTree.fromstring(text)
        items = root.findall("./channel/item")
        results: list[dict[str, object]] = []
        for item in items[:12]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            link = (item.findtext("link") or "").strip() or None
            approx_traffic = (item.findtext("ht:approx_traffic", namespaces=GOOGLE_NAMESPACE) or "").strip() or None
            news_items = [node.text.strip() for node in item.findall("ht:news_item/ht:news_item_title", GOOGLE_NAMESPACE) if node.text and node.text.strip()]
            results.append(
                {
                    "source": "google_trends_rss",
                    "label": title,
                    "url": link,
                    "velocity": approx_traffic,
                    "tags": self._extract_tags(title),
                    "excerpt": "; ".join(news_items[:2]) if news_items else None,
                }
            )
        return results

    def _fetch_tiktok_hashtags(self) -> list[dict[str, object]]:
        text = self._fetch_text(TIKTOK_CREATIVE_CENTER_URL)
        match = TIKTOK_NEXT_DATA_PATTERN.search(text)
        if match is None:
            return []
        payload = json.loads(match.group(1))
        queries = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("dehydratedState", {})
            .get("queries", [])
        )
        if not isinstance(queries, list) or len(queries) < 2:
            return []
        list_query = queries[1]
        pages = (
            list_query.get("state", {})
            .get("data", {})
            .get("pages", [])
        )
        if not isinstance(pages, list) or not pages:
            return []
        raw_items = pages[0].get("list", [])
        if not isinstance(raw_items, list):
            return []
        results: list[dict[str, object]] = []
        for item in raw_items[:12]:
            if not isinstance(item, dict):
                continue
            hashtag = str(item.get("hashtagName") or "").strip()
            if not hashtag:
                continue
            industry = (
                item.get("industryInfo", {}).get("value")
                if isinstance(item.get("industryInfo"), dict)
                else None
            )
            publish_count = item.get("publishCnt")
            video_views = item.get("videoViews")
            rank = item.get("rank")
            results.append(
                {
                    "source": "tiktok_creative_center",
                    "label": f"#{hashtag}",
                    "url": TIKTOK_CREATIVE_CENTER_URL,
                    "velocity": f"views={video_views}" if video_views is not None else None,
                    "tags": [tag for tag in (industry, "tiktok") if isinstance(tag, str) and tag.strip()],
                    "excerpt": f"rank {rank}, publishCnt={publish_count}, videoViews={video_views}",
                }
            )
        return results

    def _build_summary(
        self,
        *,
        topic_query: str,
        region_code: str,
        top_signals: list[dict[str, object]],
        interval_minutes: int,
        collected_at: datetime,
    ) -> dict[str, object]:
        keyword_counts: dict[str, int] = {}
        hashtags: list[str] = []
        sources: dict[str, int] = {}
        for item in top_signals:
            source = str(item.get("source") or "").strip() or "unknown"
            sources[source] = sources.get(source, 0) + 1
            label = str(item.get("label") or "").strip()
            if label.startswith("#"):
                hashtags.append(label)
            for token in self._extract_tags(label):
                keyword_counts[token] = keyword_counts.get(token, 0) + 1
            excerpt = str(item.get("excerpt") or "").strip()
            for token in self._extract_tags(excerpt):
                keyword_counts[token] = keyword_counts.get(token, 0) + 1

        top_keywords = [item for item, _ in sorted(keyword_counts.items(), key=lambda entry: (-entry[1], entry[0]))[:8]]
        top_hashtags = hashtags[:6]
        recommended_reel_types = self._recommended_reel_types(top_keywords=top_keywords, top_hashtags=top_hashtags)
        return {
            "query": topic_query,
            "region_code": region_code,
            "top_keywords": top_keywords,
            "top_hashtags": top_hashtags,
            "recommended_reel_types": recommended_reel_types,
            "dominant_sources": [item for item, _ in sorted(sources.items(), key=lambda entry: (-entry[1], entry[0]))],
            "signals_count": len(top_signals),
            "interval_minutes": interval_minutes,
            "collected_at": collected_at.isoformat(),
        }

    @staticmethod
    def _build_evaluation_notes(
        *,
        summary: dict[str, object],
        top_signals: list[dict[str, object]],
    ) -> list[str]:
        keywords = list(summary.get("top_keywords") or [])
        hashtags = list(summary.get("top_hashtags") or [])
        notes = [
            "Najpierw porownuj hook, tytul i payoff shorta z aktualnymi keywordami, zamiast oceniac go w prozni.",
            "Sygnaly z TikToka traktuj jako tempo i vibe dystrybucji, a sygnaly z Google jako szerszy kontekst zainteresowania.",
        ]
        if keywords:
            notes.append(f"Najmocniejsze slowa obiegu teraz: {', '.join(str(item) for item in keywords[:5])}.")
        if hashtags:
            notes.append(f"Najmocniejsze hashtagi obiegu teraz: {', '.join(str(item) for item in hashtags[:4])}.")
        if top_signals:
            notes.append(f"Snapshot zbudowano z {len(top_signals)} publicznych sygnalow internetowych.")
        return notes[:5]

    def _write_obsidian_note(
        self,
        *,
        topic_query: str,
        region_code: str,
        top_signals: list[dict[str, object]],
        summary: dict[str, object],
        evaluation_notes: list[str],
    ) -> str | None:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists or not config.export_enabled:
            return None
        relative = Path("Atnos") / "Inbox" / "Trends.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError as exc:
            raise RuntimeError("Trend note path escaped the Obsidian vault.") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Trends",
            "",
            f"- updated_at: {datetime.now(UTC).isoformat()}",
            f"- query: {topic_query}",
            f"- region: {region_code}",
            f"- sources: {', '.join(str(item) for item in list(summary.get('dominant_sources') or [])) or 'brak'}",
            "",
            "## Top keywords",
            "",
            *[f"- {item}" for item in list(summary.get("top_keywords") or [])],
            "",
            "## Top hashtags",
            "",
            *[f"- {item}" for item in list(summary.get("top_hashtags") or [])],
            "",
            "## Recommended reel types",
            "",
            *[f"- {item}" for item in list(summary.get("recommended_reel_types") or [])],
            "",
            "## Signals",
            "",
        ]
        for item in top_signals[:10]:
            lines.append(
                f"- [{item.get('source')}] {item.get('label')} | velocity={item.get('velocity') or 'n/a'}"
            )
        lines.extend(["", "## Evaluation notes", "", *[f"- {item}" for item in evaluation_notes], ""])
        target.write_text("\n".join(lines), encoding="utf-8")
        return relative.as_posix()

    @staticmethod
    def _serialize_snapshot(item) -> dict[str, object]:
        return {
            "snapshot_id": item.id,
            "project_id": item.project_id,
            "topic_query": item.topic_query,
            "region_code": item.region_code,
            "status": item.status,
            "source_mix": list(item.source_mix_json or []),
            "top_signals": list(item.top_signals_json or []),
            "summary": dict(item.summary_json or {}),
            "evaluation_notes": list(item.evaluation_notes_json or []),
            "note_path": item.note_path,
            "failure_reason": item.failure_reason,
            "collected_at": item.collected_at.isoformat(),
            "expires_at": item.expires_at.isoformat() if item.expires_at is not None else None,
        }

    @staticmethod
    def _recommended_reel_types(*, top_keywords: list[str], top_hashtags: list[str]) -> list[str]:
        recommendations = ["talking_hook", "cta_teaser"]
        entertainment_tokens = {"film", "tv", "contest", "show", "music", "viral", "challenge"}
        story_tokens = {"story", "case", "day", "night", "live", "behind"}
        keyword_set = set(top_keywords) | {item.lstrip("#") for item in top_hashtags}
        if keyword_set & story_tokens:
            recommendations.append("micro_story")
        if keyword_set & entertainment_tokens:
            recommendations.append("behind_the_scene")
        deduped: list[str] = []
        for item in recommendations:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _fetch_text(self, url: str) -> str:
        response = httpx.get(
            url,
            timeout=20,
            headers={"User-Agent": f"Atnos/{self.settings.app_version}"},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    @classmethod
    def _extract_tags(cls, value: str) -> list[str]:
        normalized = unicodedata.normalize("NFKD", value.lower())
        flattened = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return [
            token
            for token in re.split(r"[^a-z0-9#]+", flattened)
            if len(token) >= 3 and token not in cls._stopwords()
        ]

    @staticmethod
    def _stopwords() -> set[str]:
        return {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "your",
            "jest",
            "oraz",
            "dla",
            "sie",
            "czy",
            "ale",
            "pod",
            "jak",
            "tak",
            "nie",
            "and",
            "all",
            "rank",
            "views",
        }
