"""Kontrola historii i duplikacji przed nowym materiałem — bez wymyślonych metryk."""
from __future__ import annotations

import re
import unicodedata
from typing import Any
from uuid import uuid4

from atnos.intelligence.storage import utc_now_iso
from atnos.production.project_content_brain.store import ProjectContentBrainStore
from atnos.production.project_content_brain.types import (
    ContentPlanRecord,
    HistoryDecision,
    SeriesRecord,
)


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "your", "you", "with",
        "this", "that", "is", "are", "be", "how", "why", "what", "when", "from", "into",
    }
)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.casefold()).strip()


def tokens(value: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(normalize_text(value)) if t not in _STOP and len(t) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(left: str, right: str) -> float:
    return jaccard(tokens(left), tokens(right))


def evaluate_history(
    store: ProjectContentBrainStore,
    *,
    project: str,
    topic: str,
    direction: str = "",
    series_key: str | None = None,
    hook: str = "",
    cta: str = "",
) -> HistoryDecision:
    """Porównaj propozycję z własną historią zamówień/planów/serii."""
    corpus = store.collect_history_corpus(project)
    topic_n = normalize_text(topic)
    direction_n = normalize_text(direction)
    hook_n = normalize_text(hook)
    cta_n = normalize_text(cta)
    best = 0.0
    best_row: dict[str, Any] | None = None
    matched_jobs: list[str] = []
    matched_topics: list[str] = []
    matched_hooks: list[str] = []
    matched_ctas: list[str] = []

    for row in corpus:
        score = similarity(topic_n, str(row.get("topic") or ""))
        if direction_n and row.get("direction"):
            score = max(score, 0.85 * similarity(direction_n, str(row["direction"])) + 0.15 * score)
        if hook_n and row.get("hook"):
            hook_score = similarity(hook_n, str(row["hook"]))
            if hook_score >= 0.72:
                matched_hooks.append(str(row["hook"])[:160])
                score = max(score, hook_score)
        if cta_n and row.get("cta"):
            cta_score = similarity(cta_n, str(row["cta"]))
            if cta_score >= 0.8:
                matched_ctas.append(str(row["cta"])[:160])
                score = max(score, 0.9 * cta_score)
        if score > best:
            best = score
            best_row = row
        if score >= 0.55 and row.get("job_id"):
            matched_jobs.append(str(row["job_id"]))
        if score >= 0.55 and row.get("topic"):
            matched_topics.append(str(row["topic"])[:200])

    episode_number = None
    if series_key:
        series = store.get_series(project, series_key)
        if series:
            episode_number = int(series.get("next_episode_number") or 1)
            for prior in series.get("prior_topics") or []:
                prior_score = similarity(topic_n, str(prior))
                if prior_score > best:
                    best = prior_score
                    best_row = {"topic": prior, "kind": "series_prior", "series_key": series_key}
                if prior_score >= 0.55:
                    matched_topics.append(str(prior)[:200])

    matched_jobs = list(dict.fromkeys(matched_jobs))[:20]
    matched_topics = list(dict.fromkeys(matched_topics))[:20]
    matched_hooks = list(dict.fromkeys(matched_hooks))[:20]
    matched_ctas = list(dict.fromkeys(matched_ctas))[:10]
    now = utc_now_iso()

    if best >= 0.92:
        return HistoryDecision(
            decision="already_used",
            reason=f"Temat/hook niemal identyczny z wcześniejszym materiałem ({(best_row or {}).get('topic', '')}).",
            confidence=min(0.99, 0.7 + best * 0.3),
            matched_job_ids=matched_jobs,
            matched_topics=matched_topics,
            matched_hooks=matched_hooks,
            matched_ctas=matched_ctas,
            series_key=series_key,
            episode_number=episode_number,
            duplication_warning=True,
            decided_at=now,
        )
    if best >= 0.72:
        return HistoryDecision(
            decision="needs_rephrase",
            reason=f"Wysokie podobieństwo do wcześniejszego kąta — wymagana przeróbka hooka/CTA ({best:.2f}).",
            confidence=round(0.55 + best * 0.35, 3),
            matched_job_ids=matched_jobs,
            matched_topics=matched_topics,
            matched_hooks=matched_hooks,
            matched_ctas=matched_ctas,
            series_key=series_key,
            episode_number=episode_number,
            duplication_warning=True,
            decided_at=now,
        )
    if best >= 0.45:
        return HistoryDecision(
            decision="similar_angle",
            reason=f"Podobny kąt jak wcześniejszy materiał ({(best_row or {}).get('topic', '')}); można kontynuować z wyraźną różnicą.",
            confidence=round(0.4 + best * 0.4, 3),
            matched_job_ids=matched_jobs,
            matched_topics=matched_topics,
            matched_hooks=matched_hooks,
            matched_ctas=matched_ctas,
            series_key=series_key,
            episode_number=episode_number,
            duplication_warning=True,
            decided_at=now,
        )
    return HistoryDecision(
        decision="new",
        reason="Brak bliskiego dopasowania w historii zleceń, planów i serii tego projektu.",
        confidence=0.75 if corpus else 0.55,
        matched_job_ids=[],
        matched_topics=[],
        matched_hooks=[],
        matched_ctas=[],
        series_key=series_key,
        episode_number=episode_number,
        duplication_warning=False,
        decided_at=now,
    )


def ensure_series_for_order(
    store: ProjectContentBrainStore,
    *,
    project: str,
    series_key: str | None,
    topic: str,
) -> dict | None:
    if not series_key:
        return None
    existing = store.get_series(project, series_key)
    if existing:
        return existing
    now = utc_now_iso()
    return store.upsert_series(
        SeriesRecord(
            series_key=series_key,
            project=project,
            name=series_key.replace("_", " ").replace("-", " ").title(),
            premise="",
            format="youtube_short",
            next_episode_number=1,
            prior_topics=[],
            created_at=now,
            updated_at=now,
        )
    )


def advance_series_topic(store: ProjectContentBrainStore, *, project: str, series_key: str | None, topic: str) -> dict | None:
    if not series_key:
        return None
    series = ensure_series_for_order(store, project=project, series_key=series_key, topic=topic)
    if not series:
        return None
    priors = list(series.get("prior_topics") or [])
    if topic and topic not in priors:
        priors.append(topic)
    series["prior_topics"] = priors[-200:]
    series["next_episode_number"] = int(series.get("next_episode_number") or 1) + 1
    return store.upsert_series(SeriesRecord.model_validate(series))


def plan_from_order(
    *,
    project: str,
    topic: str,
    series_key: str | None,
    job_id: str,
    channel: str,
    history: HistoryDecision,
    campaign_role: str | None = None,
) -> ContentPlanRecord:
    now = utc_now_iso()
    stage = "in_production"
    pub = "IN_PRODUCTION"
    return ContentPlanRecord(
        plan_id=uuid4().hex[:16],
        idea=topic,
        priority=50,
        project=project,
        series_key=series_key,
        stage=stage,  # type: ignore[arg-type]
        planned_date=None,
        channel=channel,
        campaign_role=campaign_role,  # type: ignore[arg-type]
        job_id=job_id,
        duplication_warning=history.duplication_warning,
        duplication_reason=history.reason if history.duplication_warning else "",
        picker_reason="",
        publication_status=pub,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
    )


def pick_next_plan(
    store: ProjectContentBrainStore,
    *,
    project: str,
) -> dict:
    """Wybierz następny plan ze zapisanym powodem; bez wymyślonych CTR."""
    candidates = store.list_plans(project, stages=["idea", "planned"])
    if not candidates:
        reason = "Brak planów w etapach idea/planned dla tego projektu."
        entry = store.log_picker(
            project=project,
            plan_id=None,
            reason=reason,
            confidence=1.0,
            source="project_content_brain.picker",
            payload={"candidates": 0},
        )
        return {"status": "empty", "plan": None, "reason": reason, "confidence": 1.0, "log_id": entry["id"]}

    ranked = sorted(candidates, key=lambda p: (-int(p.get("priority") or 0), str(p.get("planned_date") or "9999"), p.get("created_at") or ""))
    chosen = ranked[0]
    # Preferuj pozycje bez ostrzeżenia o duplikacji.
    clean = [p for p in ranked if not p.get("duplication_warning")]
    if clean:
        chosen = clean[0]
        reason = (
            f"Wybrano plan o najwyższym priorytecie bez ostrzeżenia o duplikacji "
            f"(priority={chosen.get('priority')}, stage={chosen.get('stage')})."
        )
        confidence = 0.8
    else:
        reason = (
            f"Wszystkie kandydatury mają ostrzeżenie o duplikacji; wybrano najwyższy priorytet "
            f"z jawnym ostrzeżeniem (plan_id={chosen.get('plan_id')})."
        )
        confidence = 0.45
    chosen = dict(chosen)
    chosen["picker_reason"] = reason
    store.upsert_plan(ContentPlanRecord.model_validate(chosen))
    entry = store.log_picker(
        project=project,
        plan_id=chosen.get("plan_id"),
        reason=reason,
        confidence=confidence,
        source="project_content_brain.picker",
        payload={"plan_id": chosen.get("plan_id"), "priority": chosen.get("priority"), "duplication_warning": chosen.get("duplication_warning")},
    )
    return {"status": "picked", "plan": chosen, "reason": reason, "confidence": confidence, "log_id": entry["id"]}


def warn_near_duplicate_plans(store: ProjectContentBrainStore, *, project: str, idea: str) -> dict:
    """Ostrzeż, gdy podobny pomysł jest już w produkcji — bez blokady hard (warning stored)."""
    active = store.list_plans(project, stages=["planned", "in_production", "qa", "needs_review", "approved", "ready_to_publish"])
    idea_n = normalize_text(idea)
    matches = []
    for plan in active:
        score = similarity(idea_n, str(plan.get("idea") or ""))
        if score >= 0.72:
            matches.append({"plan_id": plan.get("plan_id"), "idea": plan.get("idea"), "stage": plan.get("stage"), "score": round(score, 3)})
    return {
        "duplication_warning": bool(matches),
        "matches": matches[:10],
        "reason": (
            "Wykryto bliski duplikat względem planu już w produkcji/kolejce."
            if matches
            else "Brak bliskiego duplikatu w aktywnych planach."
        ),
    }
