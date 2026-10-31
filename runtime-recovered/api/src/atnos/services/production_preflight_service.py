from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads
from atnos.production.public_quality_gate import build_public_quality_gate_status
from atnos.services.external_action_preflight_service import ExternalActionPreflightService
from atnos.services.shorts_onboarding_service import ShortsOnboardingService


TERMINAL_JOB_STATUSES = {"qa_passed", "qa_failed", "error", "uploaded_private", "uploaded", "cancelled"}


@dataclass(frozen=True, slots=True)
class ProductionTopicPreflight:
    exists: bool
    account_id: str
    topic_id: str
    title: str | None
    status: str | None
    total_score: float | None
    flags: dict[str, bool]


@dataclass(frozen=True, slots=True)
class ProductionPreflightReport:
    account_id: str
    topic_id: str
    ready_to_start_local_job: bool
    ready_for_private_upload: bool
    ready_for_revenue_ops: bool
    ready_for_public_quality: bool
    blocking: tuple[str, ...]
    warnings: tuple[str, ...]
    topic: ProductionTopicPreflight
    active_job: dict[str, Any] | None
    onboarding: dict[str, Any]
    external_actions: dict[str, Any]
    youtube_oauth: dict[str, Any]
    public_quality_gate: dict[str, Any]
    voice_provider: dict[str, Any]
    visual_backend: dict[str, Any]
    style_guide: dict[str, Any]
    runtime: dict[str, Any]
    research: dict[str, Any]
    signal_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "topic_id": self.topic_id,
            "ready_to_start_local_job": self.ready_to_start_local_job,
            "ready_for_private_upload": self.ready_for_private_upload,
            "ready_for_revenue_ops": self.ready_for_revenue_ops,
            "ready_for_public_quality": self.ready_for_public_quality,
            "blocking": list(self.blocking),
            "warnings": list(self.warnings),
            "topic": asdict(self.topic),
            "active_job": self.active_job,
            "onboarding": self.onboarding,
            "external_actions": self.external_actions,
            "youtube_oauth": self.youtube_oauth,
            "public_quality_gate": self.public_quality_gate,
            "voice_provider": self.voice_provider,
            "visual_backend": self.visual_backend,
            "style_guide": self.style_guide,
            "runtime": self.runtime,
            "research": self.research,
            "signal_counts": self.signal_counts,
        }


class ProductionPreflightService:
    """Pre-start audit for approved topics before creating a production job."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def check(self, *, account_id: str, topic_id: str) -> ProductionPreflightReport:
        onboarding_checklist = await ShortsOnboardingService(self.session, self.settings).build_checklist()
        onboarding = onboarding_checklist.to_dict()
        external_actions = ExternalActionPreflightService(
            self.session,
            self.settings,
        ).check_shorts_real_actions(account_id=account_id).to_dict()
        youtube_oauth = _youtube_oauth_preflight_from_external_actions(external_actions)
        public_quality_gate = build_public_quality_gate_status(self.settings, account_id=account_id)
        voice_provider = public_quality_gate["voice_provider"]
        visual_backend = public_quality_gate["visual_backend"]
        style_guide = public_quality_gate["style_guide"]
        ready_for_public_quality = bool(public_quality_gate.get("ready"))
        runtime = _runtime_preflight_from_onboarding(onboarding)
        topic = self._load_topic(account_id=account_id, topic_id=topic_id)
        active_job = self._active_job(account_id=account_id, topic_id=topic_id)
        signal_counts = self._signal_counts(account_id=account_id)
        research = _research_preflight_from_signal_counts(signal_counts, account_id=account_id)

        blocking: list[str] = []
        warnings: list[str] = []
        if not topic.exists:
            blocking.append("topic_missing")
        elif topic.status != "approved":
            blocking.append("topic_not_approved")
        if active_job is not None:
            blocking.append("active_job_exists")
        if not onboarding_checklist.ready_for_local_production:
            blocking.append("local_production_not_ready")
        if signal_counts.get("yt_signals", 0) <= 0 or signal_counts.get("gtrends_signals", 0) <= 0:
            blocking.append("real_signals_missing")
        if signal_counts.get("competitor_videos", 0) <= 0 and signal_counts.get("reference_channels", 0) <= 0:
            warnings.append("benchmark_sources_missing")
        if topic.total_score is not None and topic.total_score < 40:
            warnings.append("topic_score_low")
        if not onboarding_checklist.ready_for_private_upload:
            warnings.append("private_upload_not_ready")
        if not onboarding_checklist.ready_for_revenue_ops:
            warnings.append("revenue_ops_not_ready")
        if not ready_for_public_quality:
            warnings.extend(public_quality_gate.get("blockers") or [])

        return ProductionPreflightReport(
            account_id=account_id,
            topic_id=topic_id,
            ready_to_start_local_job=not blocking,
            ready_for_private_upload=onboarding_checklist.ready_for_private_upload,
            ready_for_revenue_ops=onboarding_checklist.ready_for_revenue_ops,
            ready_for_public_quality=ready_for_public_quality,
            blocking=tuple(blocking),
            warnings=tuple(warnings),
            topic=topic,
            active_job=active_job,
            onboarding={
                "blocking_keys": onboarding["blocking_keys"],
                "minimal_user_inputs": onboarding["minimal_user_inputs"],
                "ready_for_local_production": onboarding["ready_for_local_production"],
                "ready_for_private_upload": onboarding["ready_for_private_upload"],
                "ready_for_revenue_ops": onboarding["ready_for_revenue_ops"],
            },
            external_actions={
                "ready_for_requested_real_test": external_actions["ready_for_requested_real_test"],
                "items": [
                    item
                    for item in external_actions["items"]
                    if item["action"] in {"youtube_private_upload", "gmail_send", "int_mail_smtp_send"}
                ],
            },
            youtube_oauth=youtube_oauth,
            public_quality_gate=public_quality_gate,
            voice_provider=voice_provider,
            visual_backend=visual_backend,
            style_guide=style_guide,
            runtime=runtime,
            research=research,
            signal_counts=signal_counts,
        )

    def _load_topic(self, *, account_id: str, topic_id: str) -> ProductionTopicPreflight:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT
                    id, account_id, title, status, total_score,
                    clickbait_risk, evergreen, shorts_potential
                FROM topic_queue
                WHERE id = ? AND account_id = ?
                """,
                (topic_id, account_id),
            ).fetchone()
        if row is None:
            return ProductionTopicPreflight(
                exists=False,
                account_id=account_id,
                topic_id=topic_id,
                title=None,
                status=None,
                total_score=None,
                flags={},
            )
        return ProductionTopicPreflight(
            exists=True,
            account_id=str(row["account_id"]),
            topic_id=str(row["id"]),
            title=str(row["title"] or ""),
            status=str(row["status"] or ""),
            total_score=float(row["total_score"]) if row["total_score"] is not None else None,
            flags={
                "clickbait_risk": bool(row["clickbait_risk"]),
                "evergreen": bool(row["evergreen"]),
                "shorts_potential": bool(row["shorts_potential"]),
            },
        )

    def _active_job(self, *, account_id: str, topic_id: str) -> dict[str, Any] | None:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT id, status, current_step, created_at, updated_at
                FROM pipeline_jobs
                WHERE account_id = ? AND topic_id = ? AND status NOT IN ({})
                ORDER BY created_at DESC
                LIMIT 1
                """.format(",".join("?" for _ in TERMINAL_JOB_STATUSES)),
                (account_id, topic_id, *sorted(TERMINAL_JOB_STATUSES)),
            ).fetchone()
        return dict(row) if row is not None else None

    def _signal_counts(self, *, account_id: str) -> dict[str, int]:
        queries = {
            "yt_signals": "SELECT COUNT(*) AS count FROM yt_signals WHERE account_id = ?",
            "competitor_channels": "SELECT COUNT(*) AS count FROM competitor_channels WHERE account_id = ?",
            "competitor_videos": "SELECT COUNT(*) AS count FROM competitor_videos WHERE account_id = ?",
            "approved_topics": "SELECT COUNT(*) AS count FROM topic_queue WHERE account_id = ? AND status = 'approved'",
            "pending_topics": "SELECT COUNT(*) AS count FROM topic_queue WHERE account_id = ? AND status = 'pending'",
        }
        counts: dict[str, int] = {}
        with connect(self.settings) as db:
            for key, query in queries.items():
                counts[key] = int(db.execute(query, (account_id,)).fetchone()["count"])
            account_terms = self._account_research_terms(db, account_id=account_id)
            counts["gtrends_signals"] = self._matching_gtrends_count(db, account_terms)
            counts["reference_channels"] = int(db.execute("SELECT COUNT(*) AS count FROM reference_channels").fetchone()["count"])
            counts["account_research_terms"] = len(account_terms)
        return counts

    def _account_research_terms(self, db: Any, *, account_id: str) -> list[str]:
        terms: list[str] = []
        row = db.execute(
            "SELECT niches FROM intelligence_accounts WHERE account_id = ? AND enabled = 1",
            (account_id,),
        ).fetchone()
        if row is not None:
            terms.extend(str(item) for item in json_loads(row["niches"], []) if str(item).strip())
        rows = db.execute(
            "SELECT DISTINCT niche, title FROM yt_signals WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        terms.extend(str(row["niche"]) for row in rows if str(row["niche"] or "").strip())
        terms.extend(str(row["title"]) for row in rows if str(row["title"] or "").strip())
        topic_rows = db.execute(
            "SELECT title, description FROM topic_queue WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        for row in topic_rows:
            terms.append(str(row["title"] or ""))
            terms.append(str(row["description"] or ""))
        normalized: list[str] = []
        for term in terms:
            for cleaned in _research_match_terms(term):
                if cleaned and cleaned not in normalized:
                    normalized.append(cleaned)
        return normalized

    def _matching_gtrends_count(self, db: Any, terms: list[str]) -> int:
        if not terms:
            return 0
        rows = db.execute(
            "SELECT keyword, related_rising, related_topics FROM gtrends_signals WHERE status = 'ready'"
        ).fetchall()
        count = 0
        for row in rows:
            haystack = " ".join(
                str(value or "").casefold()
                for value in (row["keyword"], row["related_rising"], row["related_topics"])
            )
            if any(term in haystack or haystack in term for term in terms):
                count += 1
        return count


def _research_match_terms(value: object) -> list[str]:
    text = " ".join(str(value or "").casefold().split())
    if not text:
        return []
    terms = [text]
    for token in text.replace(",", " ").replace(".", " ").replace(":", " ").split():
        cleaned = "".join(char for char in token if char.isalnum() or char in {"-", "_"})
        if len(cleaned) >= 4 and cleaned not in terms:
            terms.append(cleaned)
    return terms[:12]


def _youtube_oauth_preflight_from_external_actions(external_actions: dict[str, Any]) -> dict[str, Any]:
    items = external_actions.get("items") if isinstance(external_actions, dict) else []
    youtube = next(
        (item for item in items if isinstance(item, dict) and item.get("action") == "youtube_private_upload"),
        None,
    )
    if youtube is None:
        return {
            "ready": False,
            "blockers": ["youtube_private_upload preflight item is missing."],
            "warnings": [],
            "evidence": {},
            "next_steps": [],
        }
    evidence = youtube.get("evidence") if isinstance(youtube.get("evidence"), dict) else {}
    return {
        "ready": bool(youtube.get("ready")),
        "mode": youtube.get("mode"),
        "blockers": list(youtube.get("blockers") or []),
        "warnings": list(youtube.get("warnings") or []),
        "evidence": {
            "oauth_status": evidence.get("oauth_status"),
            "provider_configured": evidence.get("provider_configured"),
            "connected": evidence.get("connected"),
            "ready": evidence.get("ready"),
            "content_account_id": evidence.get("content_account_id"),
            "account_id": evidence.get("account_id"),
            "channel_title": evidence.get("channel_title"),
            "client_secret_file": evidence.get("client_secret_file"),
            "client_secret_diagnostics": evidence.get("client_secret_diagnostics"),
            "client_secret_status_endpoint": evidence.get("client_secret_status_endpoint"),
            "oauth_readiness_endpoint": evidence.get("oauth_readiness_endpoint"),
            "connect_endpoint": evidence.get("connect_endpoint"),
            "oauth_start_endpoint": evidence.get("oauth_start_endpoint"),
            "required_scopes": evidence.get("required_scopes") or [],
            "granted_scopes": evidence.get("granted_scopes") or [],
            "missing_scopes": evidence.get("missing_scopes") or [],
        },
        "next_steps": list(youtube.get("next_steps") or []),
    }


def _runtime_preflight_from_onboarding(onboarding: dict[str, Any]) -> dict[str, Any]:
    items = onboarding.get("items") if isinstance(onboarding, dict) else []
    runtime = next(
        (item for item in items if isinstance(item, dict) and item.get("key") == "runtime_stack"),
        None,
    )
    if runtime is None:
        return {
            "ready": bool(onboarding.get("ready_for_local_production")) if isinstance(onboarding, dict) else False,
            "status": "ready" if bool(onboarding.get("ready_for_local_production")) else "unknown",
            "blockers": [],
            "evidence": {},
            "next_steps": list(onboarding.get("minimal_user_inputs") or []) if isinstance(onboarding, dict) else [],
        }
    evidence = runtime.get("evidence") if isinstance(runtime.get("evidence"), dict) else {}
    blockers = sorted(key for key, value in evidence.items() if value != "ready")
    next_steps = list(runtime.get("next_steps") or [])
    if not bool(runtime.get("ready")):
        next_steps.insert(
            0,
            "Uruchom POST /api/production/runtime/prepare, zeby jednym krokiem przygotowac Ollama, ComfyUI i modele.",
        )
    return {
        "ready": bool(runtime.get("ready")),
        "status": runtime.get("status"),
        "blockers": blockers,
        "evidence": evidence,
        "next_steps": list(dict.fromkeys(str(step) for step in next_steps if str(step).strip())),
    }


def _research_preflight_from_signal_counts(signal_counts: dict[str, int], *, account_id: str) -> dict[str, Any]:
    missing: list[str] = []
    if signal_counts.get("yt_signals", 0) <= 0:
        missing.append("yt_signals")
    if signal_counts.get("gtrends_signals", 0) <= 0:
        missing.append("gtrends_signals")
    if signal_counts.get("account_research_terms", 0) <= 0:
        missing.append("account_research_terms")
    ready = not missing
    next_steps: list[str] = []
    if missing:
        next_steps.extend(
            [
                "Sprawdz pelny setup YouTube przez GET /oauth/youtube-studio/setup-guide.",
                "Zapisz realny YouTube Data API key przez POST /api/intelligence/youtube-data-api-key albo polacz YouTube OAuth.",
                f"Zapisz nisze konta przez POST /api/intelligence/accounts dla account_id={account_id}.",
                f"Uruchom zbieranie sygnalow przez POST /api/intelligence/run dla account_id={account_id}.",
                f"Po zebraniu sygnalow uruchom POST /api/topics/generate dla account_id={account_id}.",
            ]
        )
    return {
        "ready": ready,
        "missing": missing,
        "counts": dict(signal_counts),
        "required_signal_tables": ["yt_signals", "gtrends_signals"],
        "endpoints": {
            "youtube_setup_guide": "GET /oauth/youtube-studio/setup-guide",
            "configure_youtube_data_api_key": "POST /api/intelligence/youtube-data-api-key",
            "configure_account_intelligence": "POST /api/intelligence/accounts",
            "run_collectors": "POST /api/intelligence/run",
            "generate_topics": "POST /api/topics/generate",
            "queue": f"GET /api/topics/queue?account_id={account_id}&status=pending",
        },
        "credential_purpose": {
            "youtube_data_api_key": "Publiczne trendy YouTube i yt_signals bez dostepu do prywatnego kanalu.",
            "youtube_oauth": "Prywatny upload, wlasna analityka kanalu i follow-upy 24/48/72h.",
        },
        "next_steps": next_steps,
    }
