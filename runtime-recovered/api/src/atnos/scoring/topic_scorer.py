from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import re
from typing import Any
from uuid import uuid4

import httpx

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads
from atnos.services.ollama_model_roles_service import OllamaModelRolesService


RECOMMENDATION_THRESHOLD = 65.0

TOPIC_SCORING_CONTRACT: dict[str, Any] = {
    "signal_source": "SQLite intelligence.db",
    "generation_requires_real_signals": True,
    "weights": {
        "viral_potential": 25,
        "search_demand": 20,
        "competition_gap": 20,
        "trend_velocity": 15,
        "niche_fit": 10,
        "freshness": 10,
    },
    "required_signal_tables": ["yt_signals", "gtrends_signals", "competitor_videos", "topic_queue"],
    "llm_scope": "local_ollama_only",
    "performance_feedback": True,
}


@dataclass(frozen=True, slots=True)
class TopicScore:
    id: str | None
    topic: str
    description: str
    niche: str
    total_score: float
    recommended: bool
    dimensions: dict[str, float]
    flags: dict[str, bool]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "title": self.topic,
            "description": self.description,
            "niche": self.niche,
            "total_score": self.total_score,
            "recommended": self.recommended,
            "dimensions": self.dimensions,
            "flags": self.flags,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class NicheFitJudgement:
    score: float
    source: str
    model: str
    llm_unavailable: bool


class TopicScorer:
    """Scores candidate topics using collected intelligence signals from SQLite."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def score_topic(
        self,
        *,
        topic: str,
        niche: str,
        topic_id: str | None = None,
        description: str = "",
        account_id: str | None = None,
        geo: str = "PL",
        use_llm: bool = True,
        derived_from_signals: bool = False,
    ) -> TopicScore:
        normalized_topic = _normalize_topic(topic)
        normalized_niche = _normalize_topic(niche)
        if not normalized_topic:
            raise ValueError("topic cannot be empty")
        if not normalized_niche:
            raise ValueError("niche cannot be empty")

        signals = self._read_signals(
            topic=normalized_topic,
            niche=normalized_niche,
            account_id=account_id,
            geo=geo,
        )

        if use_llm:
            niche_fit = await self._niche_fit_judgement(topic=topic, niche=niche)
            flags = await self._judge_topic_flags(topic=topic, description=description, niche=niche)
        else:
            niche_fit = NicheFitJudgement(
                score=_heuristic_niche_fit(topic=topic, niche=niche),
                source="heuristic_fast_path",
                model="",
                llm_unavailable=True,
            )
            flags = _heuristic_flags(topic=topic, description=description)
            flags["llm_unavailable"] = True
        dimensions = {
            "viral_potential": self._score_viral_potential(signals["yt_signals"]),
            "search_demand": self._score_search_demand(signals["gtrends_signals"]),
            "competition_gap": self._score_competition_gap(signals["competitor_videos"]),
            "trend_velocity": self._score_trend_velocity(signals["gtrends_signals"]),
            "niche_fit": niche_fit.score,
            "freshness": self._score_freshness(signals["yt_signals"]),
        }
        flags["llm_unavailable"] = bool(flags.get("llm_unavailable") or niche_fit.llm_unavailable)
        performance_adjustment = self._score_performance_feedback(signals["performance_feedback"])
        total = round(_clamp(sum(dimensions.values()) + performance_adjustment, 0.0, 100.0), 2)
        evidence = self._build_evidence(
            signals,
            performance_adjustment=performance_adjustment,
            niche_fit=niche_fit,
            flags=flags,
        )
        if derived_from_signals and not evidence["evidence_backed"]:
            evidence["evidence_backed"] = bool(signals["yt_signals"] or signals["gtrends_signals"])
            evidence["derived_from_account_signals"] = evidence["evidence_backed"]
        return TopicScore(
            id=topic_id,
            topic=topic.strip(),
            description=description.strip(),
            niche=niche.strip(),
            total_score=total,
            recommended=total >= RECOMMENDATION_THRESHOLD and bool(evidence["evidence_backed"]),
            dimensions={key: round(value, 2) for key, value in dimensions.items()},
            flags=flags,
            evidence=evidence,
        )

    async def score_topics(
        self,
        *,
        topics: list[str | dict[str, Any]],
        niche: str,
        account_id: str | None = None,
        geo: str = "PL",
        use_llm: bool = True,
        derived_from_signals: bool = False,
    ) -> list[TopicScore]:
        scores = [
            await self.score_topic(
                topic=candidate["title"],
                topic_id=candidate.get("id"),
                description=candidate.get("description", ""),
                niche=niche,
                account_id=account_id,
                geo=geo,
                use_llm=use_llm,
                derived_from_signals=derived_from_signals,
            )
            for candidate in (_normalize_candidate(item) for item in topics)
            if candidate["title"].strip()
        ]
        return sorted(scores, key=lambda item: item.total_score, reverse=True)

    async def generate_topics(self, *, account_id: str, count: int = 20, geo: str = "PL") -> list[dict[str, Any]]:
        signals = self._read_generation_signals(account_id=account_id, limit=max(count * 3, 30))
        if not signals["yt_signals"] or not signals["gtrends"]:
            return []
        niches = signals["niches"] or [item["keyword"] for item in signals["gtrends"][:5]]
        if not niches:
            return []

        if count == 1:
            candidates = await self._generate_candidates_with_ollama(
                account_id=account_id,
                count=count,
                niches=niches,
                yt_signals=signals["yt_signals"],
                gtrends=signals["gtrends"],
                performance_feedback=signals["performance_feedback"],
            )
            if not candidates:
                candidates = _fallback_candidates_from_signals(
                    count=count,
                    yt_signals=signals["yt_signals"],
                    gtrends=signals["gtrends"],
                    polish_templates=True,
                )
        else:
            candidates = _fallback_candidates_from_signals(
                count=count,
                yt_signals=signals["yt_signals"],
                gtrends=signals["gtrends"],
                polish_templates=True,
            )

        dominant_niche = niches[0]
        scored = await self.score_topics(
            topics=candidates[:count],
            niche=dominant_niche,
            account_id=account_id,
            geo=geo,
            use_llm=False,
            derived_from_signals=True,
        )
        scored = [item for item in scored if item.evidence.get("evidence_backed")]
        if not scored:
            llm_candidates = await self._generate_candidates_with_ollama(
                account_id=account_id,
                count=count,
                niches=niches,
                yt_signals=signals["yt_signals"],
                gtrends=signals["gtrends"],
                performance_feedback=signals["performance_feedback"],
            )
            scored = await self.score_topics(
                topics=llm_candidates,
                niche=dominant_niche,
                account_id=account_id,
                geo=geo,
                use_llm=False,
                derived_from_signals=False,
            )
            scored = [item for item in scored if item.evidence.get("evidence_backed")]
        return [item.to_dict() for item in scored]

    def _read_signals(
        self,
        *,
        topic: str,
        niche: str,
        account_id: str | None,
        geo: str,
    ) -> dict[str, list[dict[str, Any]]]:
        topic_terms = _search_terms(topic)
        niche_terms = _search_terms(niche)
        terms = tuple(dict.fromkeys((*topic_terms, *niche_terms)))

        with connect(self.settings) as db:
            yt_params: list[Any] = []
            yt_where = ["1=1"]
            if account_id:
                yt_where.append("account_id = ?")
                yt_params.append(account_id)
            if geo:
                yt_where.append("(region = ? OR region IS NULL)")
                yt_params.append(geo.upper())
            yt_rows = db.execute(
                f"""
                SELECT *
                FROM yt_signals
                WHERE {" AND ".join(yt_where)}
                ORDER BY collected_at DESC, view_count DESC
                LIMIT 500
                """,
                yt_params,
            ).fetchall()

            gtrend_rows = db.execute(
                """
                SELECT *
                FROM gtrends_signals
                WHERE geo = ? OR geo IS NULL
                ORDER BY collected_at DESC, interest_score DESC
                LIMIT 500
                """,
                (geo.upper(),),
            ).fetchall()

            competitor_params: list[Any] = []
            competitor_where = ["1=1"]
            if account_id:
                competitor_where.append("account_id = ?")
                competitor_params.append(account_id)
            competitor_rows = db.execute(
                f"""
                SELECT *
                FROM competitor_videos
                WHERE {" AND ".join(competitor_where)}
                ORDER BY published_at DESC, views DESC
                LIMIT 500
                """,
                competitor_params,
            ).fetchall()
            performance_params: list[Any] = []
            performance_where = ["COALESCE(feedback_adjusted_score, performance_score) IS NOT NULL"]
            if account_id:
                performance_where.append("account_id = ?")
                performance_params.append(account_id)
            performance_rows = db.execute(
                f"""
                SELECT id, account_id, title, description, performance_score,
                       feedback_adjusted_score, last_performance_recommendation, analytics_history_json,
                       last_youtube_video_id, analytics_updated_at
                FROM topic_queue
                WHERE {" AND ".join(performance_where)}
                ORDER BY analytics_updated_at DESC, COALESCE(feedback_adjusted_score, performance_score) DESC
                LIMIT 200
                """,
                performance_params,
            ).fetchall()

        yt = [dict(row) for row in yt_rows if _row_matches(row, terms, fields=("title", "niche"))]
        topic_yt = [dict(row) for row in yt_rows if _row_matches(row, tuple(topic_terms), fields=("title",))]
        gtrends = [
            _parse_gtrends_row(dict(row))
            for row in gtrend_rows
            if _row_matches(row, terms, fields=("keyword", "related_rising", "related_topics"))
        ]
        topic_gtrends = [
            _parse_gtrends_row(dict(row))
            for row in gtrend_rows
            if _row_matches(row, tuple(topic_terms), fields=("keyword", "related_rising", "related_topics"))
        ]
        competitor = [
            dict(row)
            for row in competitor_rows
            if _row_matches(row, terms, fields=("title",))
        ]
        performance_feedback = [
            _parse_performance_row(dict(row))
            for row in performance_rows
            if _row_matches(row, terms, fields=("title", "description", "last_performance_recommendation"))
        ]
        return {
            "yt_signals": yt,
            "gtrends_signals": gtrends,
            "competitor_videos": competitor,
            "topic_yt_signals": topic_yt,
            "topic_gtrends_signals": topic_gtrends,
            "performance_feedback": performance_feedback,
        }

    @staticmethod
    def _score_viral_potential(rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        avg_views = sum(_int(row.get("view_count")) for row in rows) / len(rows)
        if avg_views <= 0:
            return 0.0
        return _clamp((math.log10(avg_views) / 7.0) * 25.0, 0.0, 25.0)

    @staticmethod
    def _score_search_demand(rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        interest = max(_int(row.get("interest_score")) for row in rows)
        return _clamp((interest / 100.0) * 20.0, 0.0, 20.0)

    @staticmethod
    def _score_competition_gap(rows: list[dict[str, Any]]) -> float:
        if not rows:
            # Unknown competitor coverage is not a real "gap" signal.
            # Awarding max points here makes the scorer guess instead of using collected data.
            return 0.0
        now = datetime.now(UTC)
        recent_rows = [
            row
            for row in rows
            if (published := _parse_datetime(row.get("published_at"))) is not None
            and (now - published).days <= 14
        ]
        if not recent_rows:
            return 18.0
        avg_views = sum(_int(row.get("views")) for row in recent_rows) / len(recent_rows)
        count_pressure = min(len(recent_rows) / 10.0, 1.0) * 0.55
        view_pressure = min(math.log10(max(avg_views, 1.0)) / 7.0, 1.0) * 0.45
        return _clamp((1.0 - (count_pressure + view_pressure)) * 20.0, 0.0, 20.0)

    @staticmethod
    def _score_trend_velocity(rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        rising_count = max(len(row.get("related_rising") or []) for row in rows)
        return _clamp((rising_count / 10.0) * 15.0, 0.0, 15.0)

    @staticmethod
    def _score_performance_feedback(rows: list[dict[str, Any]]) -> float:
        """Small closed-loop correction from prior YouTube performance of similar topics."""
        if not rows:
            return 0.0
        scores = [
            _float_or_zero(
                row.get("feedback_adjusted_score")
                if row.get("feedback_adjusted_score") is not None
                else row.get("performance_score")
            )
            for row in rows
        ]
        average = sum(scores) / len(scores)
        if average >= 75:
            return 6.0
        if average >= 60:
            return 3.0
        if average <= 30:
            return -8.0
        if average <= 45:
            return -4.0
        return 0.0

    async def _score_niche_fit(self, *, topic: str, niche: str) -> float:
        return (await self._score_niche_fit_with_ollama(topic=topic, niche=niche)).score

    async def _niche_fit_judgement(self, *, topic: str, niche: str) -> NicheFitJudgement:
        if type(self)._score_niche_fit is not _DEFAULT_SCORE_NICHE_FIT:
            score = await self._score_niche_fit(topic=topic, niche=niche)
            return NicheFitJudgement(
                score=float(score),
                source="test_override",
                model="",
                llm_unavailable=False,
            )
        return await self._score_niche_fit_with_ollama(topic=topic, niche=niche)

    async def _score_niche_fit_with_ollama(self, *, topic: str, niche: str) -> NicheFitJudgement:
        model = (
            self.settings.ollama_fast_model
            or self.settings.ollama_main_model
            or self.settings.ollama_chat_model
            or OllamaModelRolesService.DEFAULT_FAST_TEXT_MODEL
        )
        prompt = (
            "Ocen dopasowanie tematu do niszy kanalu.\n"
            "Zasady: zwroc tylko jedna liczbe: 0, 5 albo 10.\n"
            "0 = poza nisza, 5 = czesciowo pasuje, 10 = mocno pasuje.\n"
            f"Nisza: {niche}\n"
            f"Temat: {topic}\n"
            "Odpowiedz:"
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0},
        }
        try:
            async with httpx.AsyncClient(timeout=min(float(self.settings.ollama_timeout_s), 12.0)) as client:
                response = await client.post(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
            message = data.get("message") if isinstance(data, dict) else None
            content = message.get("content") if isinstance(message, dict) else ""
        except Exception:
            return NicheFitJudgement(
                score=5.0,
                source="neutral_fallback",
                model=model,
                llm_unavailable=True,
            )
        return NicheFitJudgement(
            score=float(_extract_allowed_niche_score(str(content))),
            source="local_ollama",
            model=model,
            llm_unavailable=False,
        )

    async def _judge_topic_flags(self, *, topic: str, description: str, niche: str) -> dict[str, bool]:
        model = (
            self.settings.ollama_fast_model
            or self.settings.ollama_main_model
            or self.settings.ollama_chat_model
            or OllamaModelRolesService.DEFAULT_FAST_TEXT_MODEL
        )
        prompt = (
            "Ocen temat shorta YouTube. Zwroc tylko JSON bez markdown.\n"
            "Pola boolean: clickbait_risk, evergreen, shorts_potential.\n"
            "clickbait_risk=true tylko gdy tytul obiecuje cos, czego opis nie pokrywa.\n"
            "evergreen=true gdy temat bedzie aktualny ponad 6 miesiecy.\n"
            "shorts_potential=true gdy da sie go sensownie wyjasnic w mniej niz 60 sekund.\n"
            f"Nisza: {niche}\n"
            f"Tytul: {topic}\n"
            f"Opis: {description or topic}\n"
            'JSON: {"clickbait_risk": false, "evergreen": true, "shorts_potential": true}'
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0},
        }
        fallback = _heuristic_flags(topic=topic, description=description)
        fallback["llm_unavailable"] = True
        try:
            async with httpx.AsyncClient(timeout=min(float(self.settings.ollama_timeout_s), 12.0)) as client:
                response = await client.post(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
            message = data.get("message") if isinstance(data, dict) else None
            content = message.get("content") if isinstance(message, dict) else ""
            parsed = _extract_json_object(str(content))
        except Exception:
            return fallback
        return {
            "clickbait_risk": _bool(parsed.get("clickbait_risk"), fallback["clickbait_risk"]),
            "evergreen": _bool(parsed.get("evergreen"), fallback["evergreen"]),
            "shorts_potential": _bool(parsed.get("shorts_potential"), fallback["shorts_potential"]),
            "llm_unavailable": False,
        }

    def _read_generation_signals(self, *, account_id: str, limit: int) -> dict[str, Any]:
        with connect(self.settings) as db:
            yt_rows = db.execute(
                """
                SELECT *
                FROM yt_signals
                WHERE account_id = ?
                ORDER BY view_count DESC, collected_at DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
            niches = [
                row["niche"]
                for row in db.execute(
                    """
                    SELECT niche, MAX(view_count) AS max_views
                    FROM yt_signals
                    WHERE account_id = ?
                    GROUP BY niche
                    ORDER BY max_views DESC
                    LIMIT 10
                    """,
                    (account_id,),
                ).fetchall()
                if row["niche"]
            ]
            config_row = db.execute(
                "SELECT niches FROM intelligence_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            configured_niches = [
                str(item)
                for item in json_loads(config_row["niches"], [])
                if str(item).strip()
            ] if config_row else []
            gtrend_rows = db.execute(
                """
                SELECT *
                FROM gtrends_signals
                ORDER BY interest_score DESC, collected_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            performance_rows = db.execute(
                """
                SELECT id, title, description, performance_score, last_performance_recommendation,
                       feedback_adjusted_score, analytics_history_json, last_youtube_video_id, analytics_updated_at
                FROM topic_queue
                WHERE account_id = ? AND COALESCE(feedback_adjusted_score, performance_score) IS NOT NULL
                ORDER BY analytics_updated_at DESC, COALESCE(feedback_adjusted_score, performance_score) DESC
                LIMIT 25
                """,
                (account_id,),
            ).fetchall()
        term_sources = configured_niches or [
            *niches,
            *(str(row["title"] or "") for row in yt_rows),
            *(str(row["niche"] or "") for row in yt_rows),
        ]
        account_terms = tuple(
            dict.fromkeys(
                term
                for source in term_sources
                for term in _search_terms(source)
            )
        )
        parsed_gtrends = [_parse_gtrends_row(dict(item)) for item in gtrend_rows]
        if configured_niches:
            gtrends = [
                row
                for row in parsed_gtrends
                if _gtrend_matches_configured_niches(row, configured_niches)
            ]
        else:
            gtrends = [
                row
                for row in parsed_gtrends
                if _row_matches(row, account_terms, fields=("keyword", "related_rising", "related_topics"))
            ]
        return {
            "niches": list(dict.fromkeys(niches)),
            "yt_signals": [dict(row) for row in yt_rows],
            "gtrends": gtrends,
            "performance_feedback": [_parse_performance_row(dict(row)) for row in performance_rows],
        }

    async def _generate_candidates_with_ollama(
        self,
        *,
        account_id: str,
        count: int,
        niches: list[str],
        yt_signals: list[dict[str, Any]],
        gtrends: list[dict[str, Any]],
        performance_feedback: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        performance_feedback = performance_feedback or []
        model = (
            self.settings.ollama_main_model
            or self.settings.ollama_chat_model
            or self.settings.ollama_fast_model
            or OllamaModelRolesService.DEFAULT_MAIN_TEXT_MODEL
        )
        compact_signals = {
            "niches": niches[:8],
            "top_youtube": [
                {
                    "title": row.get("title"),
                    "views": row.get("view_count"),
                    "niche": row.get("niche"),
                    "channel": row.get("channel_title"),
                }
                for row in yt_signals[:20]
            ],
            "google_trends": [
                {
                    "keyword": row.get("keyword"),
                    "interest_score": row.get("interest_score"),
                    "related_rising": (row.get("related_rising") or [])[:6],
                }
                for row in gtrends[:20]
            ],
            "repeat_patterns": [
                {
                    "title": row.get("title"),
                    "performance_score": row.get("performance_score"),
                    "feedback_adjusted_score": row.get("feedback_adjusted_score"),
                    "recommendation": row.get("last_performance_recommendation"),
                }
                for row in performance_feedback
                if _feedback_score_value(row) >= 60
            ][:8],
            "avoid_patterns": [
                {
                    "title": row.get("title"),
                    "performance_score": row.get("performance_score"),
                    "feedback_adjusted_score": row.get("feedback_adjusted_score"),
                    "recommendation": row.get("last_performance_recommendation"),
                }
                for row in performance_feedback
                if _feedback_score_value(row) <= 45
            ][:8],
        }
        prompt = (
            "Generujesz propozycje tematow dla faceless YouTube Shorts.\n"
            "Nie wymyslaj trendow. Uzywaj tylko sygnalow w JSON ponizej.\n"
            "Kazdy temat ma wynikac z realnego sygnalu: YouTube views albo Google Trends.\n"
            "Uwzglednij repeat_patterns jako sprawdzone kierunki i avoid_patterns jako kierunki do unikania.\n"
            "Zasady: konkretne, do wyjasnienia w <60s, bez pustego clickbaitu.\n"
            f"Account ID: {account_id}\n"
            f"Liczba tematow: {count}\n"
            f"Sygnaly: {compact_signals}\n"
            "Zwroc tylko JSON array: "
            '[{"id":"string","title":"string","description":"string"}]'
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.35},
        }
        try:
            async with httpx.AsyncClient(timeout=min(float(self.settings.ollama_timeout_s), 15.0)) as client:
                response = await client.post(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
            message = data.get("message") if isinstance(data, dict) else None
            content = message.get("content") if isinstance(message, dict) else ""
            parsed = _extract_json_array(str(content))
        except Exception:
            return []
        candidates = [
            item
            for item in (_normalize_candidate(item) for item in parsed if isinstance(item, dict))
            if not _candidate_title_is_noise(str(item.get("title") or ""))
        ]
        return [item for item in candidates if item["title"]][:count]

    @staticmethod
    def _score_freshness(rows: list[dict[str, Any]]) -> float:
        published_dates = [
            parsed
            for row in rows
            if (parsed := _parse_datetime(row.get("published_at"))) is not None
        ]
        if not published_dates:
            return 5.0
        newest = max(published_dates)
        age_days = max((datetime.now(UTC) - newest).total_seconds() / 86400.0, 0.0)
        return _clamp(((14.0 - age_days) / 14.0) * 10.0, 0.0, 10.0)

    @staticmethod
    def _build_evidence(
        signals: dict[str, list[dict[str, Any]]],
        *,
        performance_adjustment: float = 0.0,
        niche_fit: NicheFitJudgement | None = None,
        flags: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        yt = signals["yt_signals"]
        gtrends = signals["gtrends_signals"]
        competitors = signals["competitor_videos"]
        topic_yt = signals.get("topic_yt_signals", [])
        topic_gtrends = signals.get("topic_gtrends_signals", [])
        performance_feedback = signals.get("performance_feedback", [])
        flags = flags or {}
        return {
            "yt_signal_count": len(yt),
            "topic_yt_signal_count": len(topic_yt),
            "avg_yt_views": round(sum(_int(row.get("view_count")) for row in yt) / len(yt), 2) if yt else 0,
            "max_google_interest": max((_int(row.get("interest_score")) for row in gtrends), default=0),
            "topic_google_signal_count": len(topic_gtrends),
            "rising_query_count": max((len(row.get("related_rising") or []) for row in gtrends), default=0),
            "competitor_match_count": len(competitors),
            "recent_competitor_match_count": sum(
                1
                for row in competitors
                if (published := _parse_datetime(row.get("published_at"))) is not None
                and (datetime.now(UTC) - published).days <= 14
            ),
            "evidence_backed": bool(topic_yt or topic_gtrends),
            "llm_unavailable": bool(flags.get("llm_unavailable")),
            "niche_fit_source": niche_fit.source if niche_fit else "unknown",
            "niche_fit_model": niche_fit.model if niche_fit else "",
            "performance_feedback_count": len(performance_feedback),
            "performance_adjustment": round(performance_adjustment, 2),
            "performance_examples": [
                {
                    "topic_id": row.get("id"),
                    "title": row.get("title"),
                    "performance_score": row.get("performance_score"),
                    "feedback_adjusted_score": row.get("feedback_adjusted_score"),
                    "recommendation": row.get("last_performance_recommendation"),
                    "youtube_video_id": row.get("last_youtube_video_id"),
                }
                for row in sorted(performance_feedback, key=_feedback_score_value, reverse=True)[:3]
            ],
            "top_youtube_examples": [
                {
                    "video_id": row.get("video_id"),
                    "title": row.get("title"),
                    "view_count": row.get("view_count"),
                    "channel_title": row.get("channel_title"),
                }
                for row in sorted(yt, key=lambda item: _int(item.get("view_count")), reverse=True)[:3]
            ],
        }


def _parse_gtrends_row(row: dict[str, Any]) -> dict[str, Any]:
    row["related_rising"] = json_loads(row.get("related_rising"), [])
    row["related_topics"] = json_loads(row.get("related_topics"), [])
    return row


def _parse_performance_row(row: dict[str, Any]) -> dict[str, Any]:
    row["analytics_history"] = json_loads(row.get("analytics_history_json"), [])
    row["performance_score"] = _float_or_zero(row.get("performance_score"))
    row["feedback_adjusted_score"] = (
        _float_or_zero(row.get("feedback_adjusted_score"))
        if row.get("feedback_adjusted_score") is not None
        else row["performance_score"]
    )
    return row


def _feedback_score_value(row: dict[str, Any]) -> float:
    return _float_or_zero(
        row.get("feedback_adjusted_score")
        if row.get("feedback_adjusted_score") is not None
        else row.get("performance_score")
    )


def _normalize_candidate(item: str | dict[str, Any]) -> dict[str, str | None]:
    if isinstance(item, str):
        return {"id": None, "title": item.strip(), "description": ""}
    return {
        "id": str(item.get("id")).strip() if item.get("id") is not None else None,
        "title": str(item.get("title") or item.get("topic") or "").strip(),
        "description": str(item.get("description") or item.get("rationale") or "").strip(),
    }


def _fallback_candidates_from_signals(
    *,
    count: int,
    yt_signals: list[dict[str, Any]],
    gtrends: list[dict[str, Any]],
    polish_templates: bool = False,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in yt_signals:
        title = str(row.get("title") or "").strip()
        niche = str(row.get("niche") or "").strip()
        topic_title = _polish_topic_from_signal(title=title, niche=niche) if polish_templates else _short_topic_from_title(title)
        if topic_title:
            candidates.append(
                {
                    "id": f"topic-{uuid4().hex[:12]}",
                    "title": topic_title,
                    "description": f"Temat wyprowadzony z realnego sygnału YouTube: {title}",
                }
            )
    for row in gtrends:
        keyword = str(row.get("keyword") or "").strip()
        keyword_topic = _polish_topic_from_signal(title=keyword, niche=keyword) if polish_templates else keyword
        if keyword_topic:
            candidates.append(
                {
                    "id": f"topic-{uuid4().hex[:12]}",
                    "title": keyword_topic,
                    "description": f"Temat wyprowadzony z Google Trends: {keyword}, interest score {row.get('interest_score', 0)}.",
                }
            )
        for query in (row.get("related_rising") or [])[:3]:
            query_text = _related_query_text(query)
            query_topic = _polish_topic_from_signal(title=query_text, niche=keyword) if polish_templates else query_text
            if query_topic:
                candidates.append(
                    {
                        "id": f"topic-{uuid4().hex[:12]}",
                        "title": query_topic,
                        "description": f"Temat wyprowadzony z rosnącego zapytania Google powiązanego z: {keyword}.",
                    }
                )
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        key = _normalize_topic(item["title"])
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:count]


def _dedupe_candidates(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        title = str(item.get("title") or "").strip()
        key = _normalize_topic(title)
        if key and key not in seen and not _candidate_title_is_noise(title):
            seen.add(key)
            deduped.append(item)
    return deduped


def _gtrend_matches_configured_niches(row: dict[str, Any], configured_niches: list[str]) -> bool:
    haystack = " ".join(
        (
            str(row.get("keyword") or ""),
            " ".join(str(item) for item in row.get("related_rising") or []),
            " ".join(str(item) for item in row.get("related_topics") or []),
        )
    ).casefold()
    for niche in configured_niches:
        normalized = _normalize_topic(niche)
        if normalized and (normalized in haystack or haystack in normalized):
            return True
    return False


def _short_topic_from_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    words = cleaned.split()
    return " ".join(words[:10]) if len(words) > 10 else cleaned


def _polish_topic_from_signal(*, title: str, niche: str) -> str:
    if _candidate_title_is_noise(title):
        return ""
    normalized_niche = niche.casefold()
    if any(term in normalized_niche for term in ("finans", "pieniad", "oszcz", "inwest", "budzet", "bogact")):
        if "inwest" in normalized_niche:
            return "Jak zacząć inwestować bez najczęstszych błędów początkujących"
        if "oszcz" in normalized_niche:
            return "Dlaczego samo oszczędzanie nie wystarczy, jeśli robisz ten błąd"
        if "budzet" in normalized_niche:
            return "Prosty budżet domowy, który pokazuje gdzie uciekają pieniądze"
        return "5 błędów finansowych, które po cichu zabierają ci pieniądze"
    if any(term in normalized_niche for term in ("histori", "history", "starozyt", "średniowie", "sredniowie", "wojn", "wynalaz")):
        if "średniowie" in normalized_niche or "sredniowie" in normalized_niche:
            return "Dlaczego średniowiecze było dużo dziwniejsze niż pokazują filmy"
        if "wojn" in normalized_niche:
            return "Jedna decyzja z wojny, która zmieniła bieg historii"
        if "polski" in normalized_niche or "polska" in normalized_niche:
            return "Moment z historii Polski, o którym prawie nikt nie pamięta"
        return "Ciekawostka historyczna, która brzmi jak mit, ale wydarzyła się naprawdę"
    if any(term in normalized_niche for term in ("brainrot", "glupot", "głupot", "memy", "surreal", "funny ai", "dziwne ai", " ai")):
        if "brainrot" in normalized_niche:
            return "Najdziwniejszy AI brainrot, który wygląda jak sen po za dużej kawie"
        if "memy" in normalized_niche:
            return "AI zrobiło mema, którego nikt normalny by nie wymyślił"
        return "Absurdalna scena AI, która nie powinna mieć sensu, ale ma"
    return _short_topic_from_title(title)


def _candidate_title_is_noise(title: str) -> bool:
    cleaned = " ".join(str(title or "").split())
    if len(cleaned) < 8:
        return True
    if len(re.findall(r"#[\w-]+", cleaned)) >= 4:
        return True
    if re.search(r"[\u0400-\u04FF]", cleaned):
        return True
    letters = re.findall(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]", cleaned)
    return len(letters) < 5


def _heuristic_flags(*, topic: str, description: str) -> dict[str, bool]:
    text = f"{topic} {description}".casefold()
    clickbait_terms = ("szok", "nigdy", "sekret", "ukrywaja", "ukrywają", "zniszczy", "natychmiast")
    time_sensitive_terms = ("2026", "dzis", "dziś", "dzisiaj", "teraz", "aktualnie", "wybory", "kurs")
    words = re.findall(r"\w+", text)
    return {
        "clickbait_risk": any(term in text for term in clickbait_terms) and len(description.strip()) < 30,
        "evergreen": not any(term in text for term in time_sensitive_terms),
        "shorts_potential": len(words) <= 80,
        "llm_unavailable": False,
    }


def _heuristic_niche_fit(*, topic: str, niche: str) -> float:
    topic_terms = set(_search_terms(topic))
    niche_terms = set(_search_terms(niche))
    if not topic_terms or not niche_terms:
        return 5.0
    overlap = topic_terms & niche_terms
    if overlap:
        return 10.0
    topic_text = topic.casefold()
    niche_text = niche.casefold()
    if any(term in topic_text or term in niche_text for term in ("finans", "histori", "brainrot", "ai")):
        return 5.0
    return 5.0


def _extract_json_object(content: str) -> dict[str, Any]:
    stripped = _strip_markdown_json(content)
    try:
        parsed = json.loads(stripped)
    except Exception:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_json_array(content: str) -> list[Any]:
    stripped = _strip_markdown_json(content)
    try:
        parsed = json.loads(stripped)
    except Exception:
        match = re.search(r"\[.*\]", stripped, flags=re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return []
    return parsed if isinstance(parsed, list) else []


def _strip_markdown_json(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    return stripped


def _bool(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "tak", "yes", "1"}:
            return True
        if normalized in {"false", "nie", "no", "0"}:
            return False
    return fallback


def _normalize_topic(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _search_terms(value: str) -> list[str]:
    stop_words = {
        "jak",
        "dla",
        "oraz",
        "czy",
        "the",
        "and",
        "with",
        "about",
        "temat",
        "kanal",
        "kanał",
    }
    words = [word for word in re.findall(r"\w+", value.casefold()) if len(word) >= 3]
    terms = [word for word in words if word not in stop_words]
    if value and value not in terms:
        terms.insert(0, value)
    return terms[:8]


def _row_matches(row: Any, terms: tuple[str, ...], *, fields: tuple[str, ...]) -> bool:
    if not terms:
        return True
    haystack_parts: list[str] = []
    for field in fields:
        value = row[field] if hasattr(row, "keys") and field in row.keys() else None
        field_text = _field_text(value)
        if field_text:
            haystack_parts.append(field_text.casefold())
    haystack = " ".join(haystack_parts)
    return any(term and term in haystack for term in terms)


def _field_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [_field_text(item) for item in value.values()]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, list):
        parts = [_field_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    if value is None:
        return ""
    return str(value)


def _related_query_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("query", "topic", "title", "topic_title"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return ""
    return str(value or "").strip()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _extract_allowed_niche_score(content: str) -> int:
    numbers = [int(match) for match in re.findall(r"\b(?:0|5|10)\b", content)]
    return numbers[0] if numbers else 5


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


_DEFAULT_SCORE_NICHE_FIT = TopicScorer._score_niche_fit
