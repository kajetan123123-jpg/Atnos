from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from atnos.intelligence.storage import connect, utc_now_iso
from atnos.scoring.topic_scorer import TopicScorer

router = APIRouter(prefix="/api/topics", tags=["topics"])


class TopicCandidateRequest(BaseModel):
    id: str
    title: str = Field(min_length=1)
    description: str = ""


class TopicScoreRequest(BaseModel):
    account_id: str = Field(min_length=1)
    topics: list[TopicCandidateRequest] = Field(min_length=1)
    niche: str | None = None
    geo: str = "PL"

    @field_validator("geo")
    @classmethod
    def normalize_geo(cls, value: str) -> str:
        return (value or "PL").strip().upper() or "PL"


class TopicGenerateRequest(BaseModel):
    account_id: str = Field(min_length=1)
    count: int = Field(default=20, ge=1, le=100)
    geo: str = "PL"

    @field_validator("geo")
    @classmethod
    def normalize_geo(cls, value: str) -> str:
        return (value or "PL").strip().upper() or "PL"


class TopicStatusPatchRequest(BaseModel):
    status: str = Field(pattern="^(pending|approved|rejected|in_production|done)$")


def _settings(request: Request):
    return request.app.state.settings


@router.post("/score")
async def score_topics(request: Request, payload: TopicScoreRequest) -> list[dict[str, Any]]:
    try:
        scorer = TopicScorer(_settings(request))
        niche = payload.niche or _infer_niche_for_account(request, payload.account_id)
        scores = await scorer.score_topics(
            topics=[topic.model_dump() for topic in payload.topics],
            niche=niche,
            account_id=payload.account_id,
            geo=payload.geo,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [score.to_dict() for score in scores]


@router.post("/generate")
async def generate_topics(request: Request, payload: TopicGenerateRequest) -> list[dict[str, Any]]:
    research_status = _topic_generation_research_status(request, account_id=payload.account_id, geo=payload.geo)
    if not research_status["ready"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=research_status)
    topics = await TopicScorer(_settings(request)).generate_topics(
        account_id=payload.account_id,
        count=payload.count,
        geo=payload.geo,
    )
    stored = _store_scored_topics(request, account_id=payload.account_id, topics=topics)
    _push_topic_ws_event(
        request,
        {
            "type": "topics_generated",
            "account_id": payload.account_id,
            "count": len(stored),
            "top_topic": stored[0]["title"] if stored else "",
        },
    )
    return stored


def _topic_generation_research_status(request: Request, *, account_id: str, geo: str) -> dict[str, Any]:
    normalized_geo = (geo or "PL").strip().upper() or "PL"
    settings = _settings(request)
    with connect(settings) as db:
        yt_count = db.execute(
            """
            SELECT COUNT(*) AS count
            FROM yt_signals
            WHERE account_id = ? AND region = ?
            """,
            (account_id, normalized_geo),
        ).fetchone()["count"]
        yt_rows = db.execute(
            """
            SELECT title, niche
            FROM yt_signals
            WHERE account_id = ? AND region = ?
            ORDER BY view_count DESC, collected_at DESC
            LIMIT 100
            """,
            (account_id, normalized_geo),
        ).fetchall()
        gtrend_rows = db.execute(
            """
            SELECT keyword, related_rising, related_topics
            FROM gtrends_signals
            WHERE geo = ?
            ORDER BY interest_score DESC, collected_at DESC
            LIMIT 300
            """,
            (normalized_geo,),
        ).fetchall()
        gtrends_count = _matching_gtrends_count(yt_rows=yt_rows, gtrend_rows=gtrend_rows)
    blockers: list[str] = []
    if int(yt_count or 0) <= 0:
        blockers.append("yt_signals_empty")
    if int(gtrends_count or 0) <= 0:
        blockers.append("gtrends_signals_empty")
    return {
        "ready": not blockers,
        "account_id": account_id,
        "geo": normalized_geo,
        "blockers": blockers,
        "counts": {
            "yt_signals": int(yt_count or 0),
            "gtrends_signals": int(gtrends_count or 0),
        },
        "message": (
            "Topic generation requires real YouTube and Google Trends signals in intelligence.db."
            if blockers
            else "Research signals are ready."
        ),
        "next_actions": _topic_generation_next_actions(blockers, account_id=account_id, geo=normalized_geo),
    }


def _topic_generation_next_actions(blockers: list[str], *, account_id: str, geo: str) -> list[str]:
    actions: list[str] = []
    if "yt_signals_empty" in blockers:
        actions.append(
            f"Collect YouTube trends first: POST /api/intelligence/run with account_id={account_id}, niches, region={geo}."
        )
    if "gtrends_signals_empty" in blockers:
        actions.append("Collect Google Trends first: GET /api/intelligence/gtrends?keywords=<niches>&geo=<region>.")
    return actions


def _matching_gtrends_count(*, yt_rows: list[Any], gtrend_rows: list[Any]) -> int:
    terms = tuple(
        dict.fromkeys(
            term
            for row in yt_rows
            for value in (row["title"], row["niche"])
            for term in _search_terms(str(value or ""))
        )
    )
    if not terms:
        return len(gtrend_rows)
    count = 0
    for row in gtrend_rows:
        haystack = " ".join(
            (
                str(row["keyword"] or ""),
                " ".join(str(item) for item in _json_list(row["related_rising"])),
                " ".join(str(item) for item in _json_list(row["related_topics"])),
            )
        ).casefold()
        if any(term and term in haystack for term in terms):
            count += 1
    return count


def _search_terms(value: str) -> list[str]:
    stop_words = {"jak", "dla", "oraz", "czy", "the", "and", "with", "about", "temat", "kanal", "kanał"}
    return [word for word in re.findall(r"\w+", value.casefold()) if len(word) >= 3 and word not in stop_words][:12]


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


@router.get("/queue")
async def topic_queue(
    request: Request,
    account_id: str = Query(min_length=1),
    status_filter: str | None = Query(
        default="pending",
        alias="status",
        pattern="^(pending|approved|rejected|in_production|done|all)$",
    ),
) -> list[dict[str, Any]]:
    return _read_topic_queue(request, account_id=account_id, status=status_filter)


@router.patch("/{topic_id}")
async def update_topic_status(
    topic_id: str,
    request: Request,
    payload: TopicStatusPatchRequest,
) -> dict[str, Any]:
    settings = _settings(request)
    with connect(settings) as db:
        row = db.execute("SELECT * FROM topic_queue WHERE id = ?", (topic_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Topic {topic_id} was not found.")
        db.execute(
            "UPDATE topic_queue SET status = ?, updated_at = ? WHERE id = ?",
            (payload.status, utc_now_iso(), topic_id),
        )
        db.commit()
    updated = _read_topic_by_id(request, topic_id)
    _push_topic_ws_event(
        request,
        {
            "type": "topic_status_updated",
            "account_id": updated["account_id"],
            "topic_id": topic_id,
            "status": payload.status,
        },
    )
    return updated


@router.delete("/{topic_id}")
async def delete_topic(topic_id: str, request: Request) -> dict[str, Any]:
    settings = _settings(request)
    with connect(settings) as db:
        row = db.execute("SELECT account_id FROM topic_queue WHERE id = ?", (topic_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Topic {topic_id} was not found.")
        account_id = str(row["account_id"])
        db.execute("DELETE FROM topic_queue WHERE id = ?", (topic_id,))
        db.commit()
    _push_topic_ws_event(
        request,
        {"type": "topic_removed", "account_id": account_id, "topic_id": topic_id},
    )
    return {"deleted": True, "topic_id": topic_id}


@router.get("/score")
async def score_topic(
    request: Request,
    topic: str = Query(min_length=1),
    niche: str = Query(min_length=1),
    account_id: str | None = None,
    geo: str = "PL",
) -> dict[str, Any]:
    try:
        score = await TopicScorer(_settings(request)).score_topic(
            topic=topic,
            niche=niche,
            account_id=account_id,
            geo=geo,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return score.to_dict()


def _infer_niche_for_account(request: Request, account_id: str) -> str:
    settings = _settings(request)
    with connect(settings) as db:
        row = db.execute(
            """
            SELECT niche
            FROM yt_signals
            WHERE account_id = ?
            GROUP BY niche
            ORDER BY MAX(view_count) DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if row and row["niche"]:
            return str(row["niche"])
        trend = db.execute(
            """
            SELECT keyword
            FROM gtrends_signals
            ORDER BY interest_score DESC, collected_at DESC
            LIMIT 1
            """
        ).fetchone()
        if trend and trend["keyword"]:
            return str(trend["keyword"])
    return "general"


def _store_scored_topics(request: Request, *, account_id: str, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = utc_now_iso()
    settings = _settings(request)
    with connect(settings) as db:
        for topic in topics:
            dimensions = topic.get("dimensions") if isinstance(topic.get("dimensions"), dict) else {}
            flags = topic.get("flags") if isinstance(topic.get("flags"), dict) else {}
            topic_id = str(topic.get("id") or f"topic-{uuid4().hex[:12]}")
            total_score = _float(topic.get("total_score"))
            topic_status = "rejected" if total_score < 30 else "pending"
            db.execute(
                """
                INSERT INTO topic_queue (
                    id, account_id, title, description, inspired_by, status, total_score,
                    viral_potential, search_demand, competition_gap, trend_velocity,
                    niche_fit, freshness, clickbait_risk, evergreen, shorts_potential,
                    llm_unavailable, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    inspired_by=excluded.inspired_by,
                    status=excluded.status,
                    total_score=excluded.total_score,
                    viral_potential=excluded.viral_potential,
                    search_demand=excluded.search_demand,
                    competition_gap=excluded.competition_gap,
                    trend_velocity=excluded.trend_velocity,
                    niche_fit=excluded.niche_fit,
                    freshness=excluded.freshness,
                    clickbait_risk=excluded.clickbait_risk,
                    evergreen=excluded.evergreen,
                    shorts_potential=excluded.shorts_potential,
                    llm_unavailable=excluded.llm_unavailable,
                    updated_at=excluded.updated_at
                """,
                (
                    topic_id,
                    account_id,
                    str(topic.get("title") or topic.get("topic") or "").strip(),
                    str(topic.get("description") or "").strip(),
                    _inspired_by(topic),
                    topic_status,
                    total_score,
                    _float(dimensions.get("viral_potential")),
                    _float(dimensions.get("search_demand")),
                    _float(dimensions.get("competition_gap")),
                    _float(dimensions.get("trend_velocity")),
                    _float(dimensions.get("niche_fit")),
                    _float(dimensions.get("freshness")),
                    int(bool(flags.get("clickbait_risk"))),
                    int(bool(flags.get("evergreen"))),
                    int(bool(flags.get("shorts_potential"))),
                    int(bool(flags.get("llm_unavailable"))),
                    now,
                    now,
                ),
            )
        db.commit()
    return _read_topic_queue(request, account_id=account_id, status="all")


def _read_topic_queue(request: Request, *, account_id: str, status: str | None = "pending") -> list[dict[str, Any]]:
    where = ["account_id = ?"]
    params: list[Any] = [account_id]
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    with connect(_settings(request)) as db:
        rows = db.execute(
            f"""
            SELECT *
            FROM topic_queue
            WHERE {" AND ".join(where)}
            ORDER BY total_score DESC, updated_at DESC
            """,
            params,
        ).fetchall()
    return [_topic_row_to_payload(row) for row in rows]


def _read_topic_by_id(request: Request, topic_id: str) -> dict[str, Any]:
    with connect(_settings(request)) as db:
        row = db.execute("SELECT * FROM topic_queue WHERE id = ?", (topic_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Topic {topic_id} was not found.")
    return _topic_row_to_payload(row)


def _topic_row_to_payload(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "title": row["title"],
        "topic": row["title"],
        "description": row["description"] or "",
        "inspired_by": row["inspired_by"] or "",
        "status": row["status"],
        "total_score": _float(row["total_score"]),
        "dimensions": {
            "viral_potential": _float(row["viral_potential"]),
            "search_demand": _float(row["search_demand"]),
            "competition_gap": _float(row["competition_gap"]),
            "trend_velocity": _float(row["trend_velocity"]),
            "niche_fit": _float(row["niche_fit"]),
            "freshness": _float(row["freshness"]),
        },
        "flags": {
            "clickbait_risk": bool(row["clickbait_risk"]),
            "evergreen": bool(row["evergreen"]),
            "shorts_potential": bool(row["shorts_potential"]),
            "llm_unavailable": bool(row["llm_unavailable"]),
        },
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _inspired_by(topic: dict[str, Any]) -> str:
    evidence = topic.get("evidence") if isinstance(topic.get("evidence"), dict) else {}
    examples = evidence.get("top_youtube_examples")
    if isinstance(examples, list) and examples:
        first = examples[0]
        if isinstance(first, dict):
            return str(first.get("title") or first.get("video_id") or "youtube_signal")
    if evidence.get("max_google_interest"):
        return f"google_trends:{evidence['max_google_interest']}"
    return "intelligence_signals"


def _float(value: object) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _push_topic_ws_event(request: Request, event: dict[str, Any]) -> None:
    events = getattr(request.app.state, "topic_ws_events", None)
    if not isinstance(events, list):
        events = []
        request.app.state.topic_ws_events = events
    events.append(event)
    del events[:-50]
