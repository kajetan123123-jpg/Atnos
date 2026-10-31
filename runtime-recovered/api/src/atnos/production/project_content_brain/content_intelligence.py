"""Real feedback loop: published video → analytics/comments → brain → decision.

Missing metrics stay UNKNOWN. Model text never counts as YouTube evidence.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from atnos.intelligence.storage import json_dumps, utc_now_iso

INTELLIGENCE_SQL = """
CREATE TABLE IF NOT EXISTS project_content_metric_snapshots (
    project TEXT NOT NULL,
    video_id TEXT NOT NULL,
    job_id TEXT,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (project, video_id)
);

CREATE TABLE IF NOT EXISTS project_content_comments (
    comment_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    video_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_insights (
    insight_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_decisions (
    decision_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_sync_log (
    project TEXT PRIMARY KEY,
    last_attempt TEXT,
    last_success TEXT,
    status TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_competitors (
    project TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, channel_id)
);
"""

SOURCE_YOUTUBE = "YOUTUBE_ANALYTICS"
SOURCE_COMMENTS = "COMMENT_SIGNAL"
SOURCE_COMPETITOR = "COMPETITOR_RESEARCH"
SOURCE_MODEL = "MODEL_INFERENCE"
SOURCE_PRODUCTION = "PRODUCTION"

_QUESTION = re.compile(r"\?|^(how|why|what|when|can|should)\b", re.I)
_SEQUEL = re.compile(r"\b(part 2|next part|sequel|another video)\b", re.I)
_OBJECTION = re.compile(r"\b(won't|does not work|doesn't work|too hard|scam)\b", re.I)
_REQUEST = re.compile(r"\b(please|can you|could you|show me)\b", re.I)
_CONFUSION = re.compile(r"\b(confused|don't understand|do not understand|unclear)\b", re.I)


def ensure_intelligence_schema(db) -> None:
    db.executescript(INTELLIGENCE_SQL)


def metric_slot(value: Any, *, available: bool) -> dict[str, Any]:
    if not available or value is None:
        return {"value": None, "status": "UNKNOWN"}
    return {"value": value, "status": "observed"}


def normalize_analytics(payload: dict[str, Any], *, fetched_at: str, window_days: int | None = None) -> dict[str, Any]:
    """Map connector output. Zero is real only when the field was actually returned."""
    status = str(payload.get("status") or "")
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if window_days is None:
        if start_date and end_date:
            try:
                from datetime import date as date_cls
                window_days = max(1, (date_cls.fromisoformat(str(end_date)) - date_cls.fromisoformat(str(start_date))).days)
            except ValueError:
                window_days = None
        elif payload.get("window_days") is not None:
            window_days = int(payload["window_days"])
    period_days = window_days  # may stay None for lifetime totals
    if status in {"unavailable", ""} or payload.get("integration") == "REQUIRES_SETUP":
        fields = (
            "views", "impressions", "thumbnail_ctr", "annotation_click_through_rate",
            "watch_time_minutes", "average_view_duration",
            "average_view_percentage", "subscribers_gained", "likes", "comments",
            "lifetime_views", "lifetime_likes", "lifetime_comments",
        )
        return {
            "video_id": payload.get("video_id"),
            "integration": "REQUIRES_SETUP" if "Connect YouTube" in str(payload.get("message") or "") or payload.get("integration") == "REQUIRES_SETUP" else "UNAVAILABLE",
            "source": SOURCE_YOUTUBE,
            "fetched_at": fetched_at,
            "window_days": period_days,
            "start_date": start_date,
            "end_date": end_date,
            "metrics": {name: metric_slot(None, available=False) for name in fields},
            "reason": str(payload.get("message") or "YouTube nie jest podłączone albo analityka jest niedostępna."),
        }
    if status == "pending":
        fields = (
            "views", "impressions", "thumbnail_ctr", "annotation_click_through_rate",
            "watch_time_minutes", "average_view_duration",
            "average_view_percentage", "subscribers_gained", "likes", "comments",
            "lifetime_views", "lifetime_likes", "lifetime_comments",
        )
        return {
            "video_id": payload.get("video_id"),
            "integration": "PENDING",
            "source": SOURCE_YOUTUBE,
            "fetched_at": fetched_at,
            "window_days": period_days,
            "start_date": start_date,
            "end_date": end_date,
            "metrics": {name: metric_slot(None, available=False) for name in fields},
            "reason": str(payload.get("message") or "YouTube Analytics has no data yet."),
        }
    degraded = status == "ready_degraded"
    available = set(payload.get("metrics_available") or [])
    def present(name: str, key: str) -> bool:
        if degraded:
            return name in available or key in available
        return payload.get(key) is not None or name in (payload.get("metrics") or {})

    lifetime = bool(payload.get("lifetime_totals")) or degraded
    metrics = {
        "views": metric_slot(payload.get("views"), available=present("views", "views") and not lifetime),
        "impressions": metric_slot(None, available=False),
        # Thumbnail CTR is not provided by the current Analytics query — stay UNKNOWN.
        "thumbnail_ctr": metric_slot(None, available=False),
        "annotation_click_through_rate": metric_slot(
            payload.get("annotation_click_through_rate"),
            available=payload.get("annotation_click_through_rate") is not None and not degraded,
        ),
        "watch_time_minutes": metric_slot(payload.get("estimated_minutes_watched"), available=payload.get("estimated_minutes_watched") is not None),
        "average_view_duration": metric_slot(payload.get("average_view_duration"), available=payload.get("average_view_duration") is not None),
        "average_view_percentage": metric_slot(payload.get("average_view_percentage"), available=payload.get("average_view_percentage") is not None),
        "subscribers_gained": metric_slot(payload.get("subscribers_gained"), available=payload.get("subscribers_gained") is not None),
        "likes": metric_slot(payload.get("likes"), available=present("likes", "likes") and not lifetime),
        "comments": metric_slot(payload.get("comments"), available=present("comments", "comments") and not lifetime),
        "lifetime_views": metric_slot(payload.get("views") if lifetime else None, available=lifetime and present("views", "views")),
        "lifetime_likes": metric_slot(payload.get("likes") if lifetime else None, available=lifetime and present("likes", "likes")),
        "lifetime_comments": metric_slot(payload.get("comments") if lifetime else None, available=lifetime and present("comments", "comments")),
    }
    return {
        "video_id": payload.get("video_id"),
        "integration": "LIVE" if not degraded else "DEGRADED",
        "source": SOURCE_YOUTUBE if not degraded else "YOUTUBE_DATA_API",
        "fetched_at": fetched_at,
        "window_days": None if lifetime else period_days,
        "start_date": None if lifetime else start_date,
        "end_date": None if lifetime else end_date,
        "metrics": metrics,
        "reason": str(payload.get("message") or "Pobrane z YouTube."),
    }


def cluster_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for comment in comments:
        text = str(comment.get("text") or "").strip()
        if not text:
            continue
        if _SEQUEL.search(text):
            kind = "request"
            theme = "prośba o kolejną część"
        elif _QUESTION.search(text) or _CONFUSION.search(text):
            kind = "question"
            theme = "pytanie widzów"
        elif _OBJECTION.search(text):
            kind = "objection"
            theme = "obiekcja"
        elif _REQUEST.search(text):
            kind = "request"
            theme = "prośba widzów"
        else:
            kind = "other"
            theme = "inne sygnały"
        bucket = groups.setdefault(theme, {"theme": theme, "kind": kind, "comment_ids": [], "video_ids": set(), "count": 0})
        bucket["count"] += 1
        bucket["comment_ids"].append(comment["comment_id"])
        if comment.get("video_id"):
            bucket["video_ids"].add(comment["video_id"])
    signals = []
    for theme, bucket in groups.items():
        count = bucket["count"]
        confidence = "LOW" if count < 3 else "MEDIUM" if count < 8 else "HIGH"
        signals.append({
            "kind": "AUDIENCE_SIGNAL",
            "theme": theme,
            "signal_kind": bucket["kind"],
            "frequency": count,
            "comment_ids": bucket["comment_ids"][:40],
            "video_ids": sorted(bucket["video_ids"]),
            "source": SOURCE_COMMENTS,
            "confidence": confidence,
            "confidence_reason": f"{count} komentarzy w jednej grupie.",
            "freshness": "ACTIVE",
        })
    return signals


def performance_insights(snapshots: list[dict[str, Any]], *, baseline: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """No strong claim from one film. Needs at least 3 observed percentages."""
    observed = []
    for snap in snapshots:
        slot = (snap.get("metrics") or {}).get("average_view_percentage") or {}
        if slot.get("status") == "observed" and isinstance(slot.get("value"), (int, float)):
            observed.append((snap.get("video_id"), float(slot["value"]), snap.get("hook") or ""))
    if len(observed) < 3:
        return [{
            "kind": "PERFORMANCE_INSIGHT",
            "statement": "Za mało filmów z prawdziwym watch percentage, żeby wyciągać wniosek.",
            "sample_size": len(observed),
            "baseline": "channel_median" if baseline else "none",
            "confidence": "LOW",
            "confidence_reason": "Próba mniejsza niż 3.",
            "source": SOURCE_YOUTUBE,
            "evidence_video_ids": [item[0] for item in observed],
            "freshness": "ACTIVE",
            "status": "insufficient_data",
        }]
    values = [item[1] for item in observed]
    median = sorted(values)[len(values) // 2]
    above = [item for item in observed if item[1] >= median]
    return [{
        "kind": "PERFORMANCE_INSIGHT",
        "statement": f"{len(above)} z {len(observed)} filmów jest na poziomie mediany kanału albo wyżej ({median:.1f}%).",
        "sample_size": len(observed),
        "baseline": median,
        "compared_baseline": "channel_median_of_this_sample",
        "confidence": "MEDIUM" if len(observed) < 8 else "HIGH",
        "confidence_reason": f"Mediana z {len(observed)} obserwacji watch percentage.",
        "source": SOURCE_YOUTUBE,
        "evidence_video_ids": [item[0] for item in observed],
        "freshness": "ACTIVE",
        "status": "observed",
    }]


def competitor_insights(videos: list[dict[str, Any]], *, own_topics: list[str]) -> list[dict[str, Any]]:
    tokens: Counter[str] = Counter()
    for video in videos:
        for word in re.findall(r"[a-z0-9']{4,}", str(video.get("title") or "").casefold()):
            tokens[word] += 1
    own = " ".join(own_topics).casefold()
    insights = []
    for word, count in tokens.most_common(8):
        if count < 2:
            continue
        saturated = count >= 3
        covered = word in own
        insights.append({
            "kind": "SATURATION_WARNING" if saturated else "OPPORTUNITY",
            "topic": word,
            "evidence_count": count,
            "source": SOURCE_COMPETITOR,
            "confidence": "LOW" if count < 3 else "MEDIUM",
            "confidence_reason": f"Słowo w {count} publicznych tytułach. To nie jest kopiowanie.",
            "already_covered_by_us": covered,
            "freshness": "ACTIVE",
        })
    if not insights:
        insights.append({
            "kind": "OPPORTUNITY",
            "topic": "",
            "statement": "Za mało publicznych tytułów, żeby wskazać lukę.",
            "source": SOURCE_COMPETITOR,
            "confidence": "LOW",
            "confidence_reason": "Brak powtórzeń w tytułach.",
            "freshness": "ACTIVE",
            "status": "insufficient_data",
        })
    return insights


def assert_provenance(insight: dict[str, Any], *, known_video_ids: set[str] | None = None) -> None:
    """A model sentence cannot be its own evidence; IDs alone are not proof."""
    source = insight.get("source")
    evidence_ids = [str(item) for item in (insight.get("evidence_video_ids") or []) if str(item).strip()]
    comment_ids = [str(item) for item in (insight.get("comment_ids") or []) if str(item).strip()]
    if source == SOURCE_MODEL:
        if not evidence_ids and not comment_ids:
            raise ValueError("MODEL_INFERENCE bez pierwotnych danych nie może wejść do Project Brain")
        if known_video_ids is not None:
            missing = [item for item in evidence_ids if item not in known_video_ids]
            if missing:
                raise ValueError(f"MODEL_INFERENCE wskazuje nieistniejące evidence_video_ids: {missing}")
        elif evidence_ids and not insight.get("evidence_verified"):
            raise ValueError("MODEL_INFERENCE wymaga zweryfikowanych evidence, nie samych niepustych ID")
    if evidence_ids and known_video_ids is not None:
        missing = [item for item in evidence_ids if item not in known_video_ids]
        if missing and source in {SOURCE_MODEL, SOURCE_YOUTUBE, "EXPERIMENT"}:
            raise ValueError(f"evidence_video_ids nie istnieją w obserwacjach projektu: {missing}")


def content_decision(
    *,
    candidates: list[dict[str, Any]],
    duplicate_topics: list[str],
) -> dict[str, Any]:
    viable = []
    rejected = []
    folded = {item.casefold() for item in duplicate_topics}
    for candidate in candidates:
        topic = str(candidate.get("topic") or "").strip()
        if not topic or topic.casefold() in folded:
            rejected.append({"topic": topic, "reason": "temat już użyty albo pusty"})
            continue
        if candidate.get("source") == SOURCE_MODEL and not candidate.get("evidence_video_ids") and not candidate.get("comment_ids"):
            rejected.append({"topic": topic, "reason": "sama inferencja modelu, bez evidence"})
            continue
        viable.append(candidate)
    if not viable:
        return {
            "decision": None,
            "alternatives": rejected,
            "reason": "Brak kandydata z evidence. Nie wybieram tematu.",
            "confidence": "LOW",
            "source": SOURCE_PRODUCTION,
        }
    ranked = sorted(viable, key=lambda item: {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(str(item.get("confidence")), 0), reverse=True)
    chosen = ranked[0]
    return {
        "decision": chosen.get("topic"),
        "alternatives": [item.get("topic") for item in ranked[1:]] + [item["topic"] for item in rejected],
        "rejected": rejected,
        "evidence": {
            "comment_ids": chosen.get("comment_ids") or [],
            "video_ids": chosen.get("evidence_video_ids") or [],
            "source": chosen.get("source"),
        },
        "reason": chosen.get("reason") or chosen.get("statement") or "Wybrane na podstawie zapisanego sygnału.",
        "confidence": chosen.get("confidence") or "LOW",
        "source": chosen.get("source") or SOURCE_PRODUCTION,
    }


def retrieve_for_task(insights: list[dict[str, Any]], *, topic: str, limit: int = 5) -> list[dict[str, Any]]:
    words = set(re.findall(r"[a-z0-9']{4,}", topic.casefold()))
    scored = []
    for insight in insights:
        blob = " ".join(str(insight.get(key) or "") for key in ("topic", "theme", "statement", "reason")).casefold()
        overlap = len(words & set(re.findall(r"[a-z0-9']{4,}", blob)))
        if overlap or not words:
            scored.append((overlap, insight))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:limit]]


class ContentIntelligenceStore:
    def __init__(self, db):
        ensure_intelligence_schema(db)
        self.db = db

    def save_metrics(self, project: str, job_id: str | None, snapshot: dict[str, Any]) -> None:
        video_id = str(snapshot.get("video_id") or "")
        if not video_id:
            raise ValueError("metric snapshot needs video_id")
        payload = {**snapshot, "job_id": job_id}
        self.db.execute(
            """INSERT INTO project_content_metric_snapshots(project,video_id,job_id,payload_json,fetched_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(project, video_id) DO UPDATE SET
                 job_id=excluded.job_id, payload_json=excluded.payload_json, fetched_at=excluded.fetched_at""",
            (project, video_id, job_id, json_dumps(payload), snapshot.get("fetched_at") or utc_now_iso()),
        )

    def save_comments(self, project: str, comments: list[dict[str, Any]]) -> int:
        inserted = 0
        now = utc_now_iso()
        for comment in comments:
            comment_id = str(comment.get("comment_id") or "")
            if not comment_id:
                continue
            cursor = self.db.execute(
                """INSERT INTO project_content_comments(comment_id,project,video_id,payload_json,fetched_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(comment_id) DO UPDATE SET payload_json=excluded.payload_json, fetched_at=excluded.fetched_at""",
                (comment_id, project, comment.get("video_id"), json_dumps(comment), now),
            )
            if cursor.rowcount:
                inserted += 1
        return inserted

    def list_comments(self, project: str, video_id: str | None = None) -> list[dict[str, Any]]:
        if video_id:
            rows = self.db.execute(
                "SELECT payload_json FROM project_content_comments WHERE project=? AND video_id=?",
                (project, video_id),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT payload_json FROM project_content_comments WHERE project=?",
                (project,),
            ).fetchall()
        import json
        return [json.loads(row["payload_json"]) for row in rows]

    def list_metrics(self, project: str) -> list[dict[str, Any]]:
        import json
        rows = self.db.execute(
            "SELECT payload_json FROM project_content_metric_snapshots WHERE project=?",
            (project,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_insight(self, project: str, insight: dict[str, Any]) -> str:
        known = {str(item.get("video_id")) for item in self.list_metrics(project) if item.get("video_id")}
        assert_provenance(insight, known_video_ids=known)
        import json
        raw = json.dumps({"project": project, **insight}, sort_keys=True)
        insight_id = hashlib.sha256(raw.encode()).hexdigest()[:24]
        payload = {**insight, "insight_id": insight_id, "project": project, "created_at": insight.get("created_at") or utc_now_iso()}
        self.db.execute(
            """INSERT INTO project_content_insights(insight_id,project,kind,payload_json,created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(insight_id) DO NOTHING""",
            (insight_id, project, insight.get("kind") or "INSIGHT", json_dumps(payload), payload["created_at"]),
        )
        return insight_id

    def list_insights(self, project: str) -> list[dict[str, Any]]:
        import json
        rows = self.db.execute(
            "SELECT payload_json FROM project_content_insights WHERE project=? ORDER BY created_at",
            (project,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_decision(self, project: str, decision: dict[str, Any]) -> str:
        decision_id = hashlib.sha256(json_dumps({"project": project, **decision}).encode()).hexdigest()[:24]
        payload = {**decision, "decision_id": decision_id, "project": project, "created_at": utc_now_iso()}
        self.db.execute(
            """INSERT INTO project_content_decisions(decision_id,project,payload_json,created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(decision_id) DO UPDATE SET
                 payload_json=excluded.payload_json,
                 created_at=excluded.created_at""",
            (decision_id, project, json_dumps(payload), payload["created_at"]),
        )
        return decision_id

    def list_decisions(self, project: str) -> list[dict[str, Any]]:
        import json

        rows = self.db.execute(
            "SELECT payload_json FROM project_content_decisions WHERE project=? ORDER BY created_at",
            (project,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def record_sync(self, project: str, *, status: str, detail: str, success: bool) -> dict[str, Any]:
        now = utc_now_iso()
        row = self.db.execute("SELECT last_success FROM project_content_sync_log WHERE project=?", (project,)).fetchone()
        last_success = row["last_success"] if row else None
        if success:
            last_success = now
        self.db.execute(
            """INSERT INTO project_content_sync_log(project,last_attempt,last_success,status,detail)
               VALUES(?,?,?,?,?)
               ON CONFLICT(project) DO UPDATE SET
                 last_attempt=excluded.last_attempt,
                 last_success=excluded.last_success,
                 status=excluded.status,
                 detail=excluded.detail""",
            (project, now, last_success, status, detail),
        )
        return {"last_attempt": now, "last_success": last_success, "status": status, "detail": detail}

    def sync_state(self, project: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM project_content_sync_log WHERE project=?", (project,)).fetchone()
        return dict(row) if row else None

    def comment_count(self, project: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM project_content_comments WHERE project=?",
            (project,),
        ).fetchone()
        return int(row["n"])


def classify_oauth_record(record: dict[str, Any]) -> str:
    """Diagnose a stored connection without reading token material."""
    if not record.get("configured"):
        return "NOT_CONFIGURED"
    status = str(record.get("status") or "")
    failure = str(record.get("refresh_failure_code") or "")
    if status == "pending":
        return "AWAITING_USER_AUTHORIZATION"
    if record.get("cancelled"):
        return "CANCELLED"
    last_error = str(record.get("last_error") or "").casefold()
    if failure == "invalid_grant" or record.get("reconnect_required") or "uniewa" in last_error:
        return "TOKEN_REVOKED"
    if record.get("missing_scopes"):
        return "INSUFFICIENT_SCOPE"
    if record.get("refresh_available") and record.get("access_expired") and not failure:
        return "TOKEN_EXPIRED_REFRESHABLE"
    if status == "connected" and record.get("validated"):
        return "CONNECTED"
    if status == "connected":
        return "ERROR"
    if record.get("configured"):
        return "NOT_CONNECTED"
    return "ERROR"


def present_youtube_integration(oauth: str, *, analytics: str = "unknown", comments: str = "unknown", last_success: str | None = None, last_attempt: str | None = None, syncing: bool = False) -> dict[str, Any]:
    """UI status. LIVE only after a completed real sync, never from contract tests."""
    if syncing:
        return {
            "status": "RUNNING",
            "detail": "Synchronizacja YouTube jest w toku.",
            "analytics": analytics,
            "comments": comments,
            "last_success": last_success,
            "last_attempt": last_attempt,
        }
    if oauth in {"MISSING", "EXPIRED", "INSUFFICIENT_SCOPE", "NOT_CONFIGURED", "NOT_CONNECTED", "TOKEN_REVOKED", "TOKEN_EXPIRED_REFRESHABLE", "AWAITING_USER_AUTHORIZATION"}:
        detail = {
            "MISSING": "YouTube is not connected. Analytics and comments are unavailable until OAuth setup is completed.",
            "NOT_CONFIGURED": "YouTube is not connected. Analytics and comments are unavailable until OAuth setup is completed.",
            "NOT_CONNECTED": "YouTube is not connected. Analytics and comments are unavailable until OAuth setup is completed.",
            "EXPIRED": "YouTube connection expired. Reconnect to restore analytics and comments.",
            "TOKEN_REVOKED": "YouTube connection expired. Reconnect to restore analytics and comments.",
            "TOKEN_EXPIRED_REFRESHABLE": "Access token wygasł. Atnos spróbuje odświeżyć sesję bez ponownego logowania.",
            "AWAITING_USER_AUTHORIZATION": "Waiting for YouTube authorization…",
            "INSUFFICIENT_SCOPE": "Connected, but Analytics scope is missing.",
        }[oauth]
        setup = oauth not in {"TOKEN_EXPIRED_REFRESHABLE", "AWAITING_USER_AUTHORIZATION", "INSUFFICIENT_SCOPE"}
        return {
            "status": "REQUIRES_SETUP" if setup else ("DEGRADED" if oauth == "INSUFFICIENT_SCOPE" else "RUNNING"),
            "detail": detail,
            "analytics": "unavailable",
            "comments": "unavailable",
            "last_success": last_success,
            "last_attempt": last_attempt,
        }
    if oauth == "ERROR":
        return {
            "status": "ERROR",
            "detail": "Połączenie YouTube jest zapisane, ale odczyt kanału się nie udał. Spróbuj synchronizacji ponownie.",
            "analytics": analytics,
            "comments": comments,
            "last_success": last_success,
            "last_attempt": last_attempt,
        }
    if analytics == "available" and comments == "available":
        return {"status": "LIVE", "detail": "YouTube — LIVE", "analytics": "available", "comments": "available", "last_success": last_success, "last_attempt": last_attempt}
    if analytics == "available" or comments == "available":
        missing = "Analytics API" if analytics != "available" else "komentarze"
        return {
            "status": "DEGRADED",
            "detail": f"Dane filmu są dostępne, ale {missing} nie działa.",
            "analytics": analytics,
            "comments": comments,
            "last_success": last_success,
            "last_attempt": last_attempt,
        }
    return {
        "status": "DEGRADED",
        "detail": "Kanał jest połączony, ale analityka i komentarze nie zostały jeszcze potwierdzone.",
        "analytics": analytics,
        "comments": comments,
        "last_success": last_success,
        "last_attempt": last_attempt,
    }


def classify_comparison(*, same_concept: bool, single_variable: bool, same_window: bool) -> str:
    if same_concept and single_variable and same_window:
        return "CONTROLLED_EXPERIMENT"
    return "OBSERVATIONAL_COMPARISON"


def assert_youtube_read(state: str, *, capability: str | None = None, capabilities: dict[str, bool] | None = None) -> None:
    """Real YouTube reads go through this guard. Anything except CONNECTED is skipped."""
    if state != "CONNECTED":
        raise PermissionError(f"SKIPPED — YouTube not connected ({state})")
    if capability and capabilities is not None and not capabilities.get(capability, False):
        raise PermissionError(f"SKIPPED — {capability} unavailable")


def _observed_value(variant: dict[str, Any], metric_name: str) -> float | None:
    metric = (variant.get("metrics") or {}).get(metric_name)
    if not isinstance(metric, dict) or metric.get("status") != "observed":
        return None
    value = metric.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def evaluate_experiment(experiment: dict[str, Any]) -> dict[str, Any]:
    """Winner exists only for a controlled test with observed metrics on every required variant."""
    kind = experiment.get("kind") or classify_comparison(
        same_concept=bool(experiment.get("same_concept")),
        single_variable=bool(experiment.get("single_variable")),
        same_window=bool(experiment.get("same_window")),
    )
    metric_name = str(experiment.get("primary_metric") or "average_view_percentage")
    variants = list(experiment.get("variants") or [])
    publication_ids = [str(item) for item in (experiment.get("publication_ids") or []) if str(item).strip()]
    base = {**experiment, "kind": kind, "winner": None, "result": "INCONCLUSIVE"}
    if kind != "CONTROLLED_EXPERIMENT":
        return {**base, "status": "INCONCLUSIVE", "limitations": "To nie jest kontrolowany test. Warunki albo zmienne się różnią."}
    if not experiment.get("measurement_window") or not publication_ids:
        return {**base, "status": "COLLECTING_DATA", "limitations": "Brak okna pomiaru albo powiązania publikacji. Nie ogłaszam zwycięzcy."}
    if len(variants) != len(publication_ids):
        return {
            **base,
            "status": "COLLECTING_DATA",
            "limitations": "Liczba wariantów nie zgadza się z publication_ids. Nie ogłaszam zwycięzcy.",
        }
    observed = []
    for variant in variants:
        value = _observed_value(variant, metric_name)
        if value is None:
            return {
                **base,
                "status": "COLLECTING_DATA",
                "limitations": "Brak observed metric dla każdego wymaganego wariantu. Nie ogłaszam zwycięzcy.",
            }
        observed.append((variant, value))
    values = [item[1] for item in observed]
    if len(set(values)) == 1:
        return {
            **base,
            "status": "COMPLETED",
            "winner": None,
            "result": "TIE",
            "limitations": "Remis na metryce głównej. Brak zwycięzcy i brak twierdzenia o wyższości.",
        }
    ranked = sorted(observed, key=lambda item: item[1], reverse=True)
    winner_variant, winner_value = ranked[0]
    runner_up = ranked[1][1]
    return {
        **base,
        "status": "COMPLETED",
        "winner": winner_variant.get("id"),
        "result": "SUPPORTS_HYPOTHESIS" if winner_value > runner_up else "DOES_NOT_SUPPORT",
        "limitations": "Wynik dotyczy tylko tego eksperymentu i tego okna pomiaru. To wyższa zaobserwowana metryka, nie potwierdzony efekt przyczynowy.",
    }


def experiment_learning(experiment: dict[str, Any]) -> dict[str, Any] | None:
    evaluated = evaluate_experiment(experiment)
    if evaluated.get("winner") is None or evaluated.get("result") == "TIE":
        return None
    if evaluated.get("status") != "COMPLETED":
        return None
    if not experiment.get("experiment_id") or not experiment.get("publication_ids") or not experiment.get("measurement_window"):
        return None
    return {
        "source": "EXPERIMENT",
        "experiment_id": experiment.get("experiment_id"),
        "publication_ids": experiment.get("publication_ids"),
        "measurement_window": experiment.get("measurement_window"),
        "statement": (
            f"In experiment {experiment.get('experiment_id')}, variant {evaluated.get('winner')} "
            "had the higher observed primary metric in this window only. This is not a confirmed causal effect."
        ),
        "evidence_video_ids": list(experiment.get("publication_ids") or []),
        "kind": "EXPERIMENT_LEARNING",
        "hypothesis_vs_finding": "observed_metric_ranking",
    }


def ingest_status_from_connector_error(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    if "Connect YouTube" in message or "OAuth" in message:
        return {"integration": "REQUIRES_SETUP", "message": message, "status": "unavailable"}
    return {"integration": "UNAVAILABLE", "message": message, "status": "unavailable"}
