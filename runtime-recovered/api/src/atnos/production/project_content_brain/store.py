"""Trwała pamięć Project Content Brain w intelligence.db (bez zewnętrznych API)."""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from atnos.intelligence.storage import json_dumps, utc_now_iso
from atnos.production.project_content_brain.types import (
    CAPABILITY_FLAGS,
    PROJECT_KEYS,
    CampaignRecord,
    CommentClusterRecord,
    CompetitorKnowledgeRecord,
    ContentPlanRecord,
    ExperimentRecord,
    FormatFatigueRecord,
    HostCharacterProfile,
    HistoryDecision,
    ProjectBrainSnapshot,
    PublishResultRecord,
    SeriesRecord,
    TrendDecisionRecord,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS project_content_brain (
    project TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_series (
    project TEXT NOT NULL,
    series_key TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, series_key)
);

CREATE TABLE IF NOT EXISTS project_content_campaigns (
    campaign_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_plans (
    plan_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    record_json TEXT NOT NULL,
    stage TEXT NOT NULL,
    job_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_content_plans_project_stage
    ON project_content_plans (project, stage);

CREATE TABLE IF NOT EXISTS project_content_history_decisions (
    job_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_trend_decisions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_comment_clusters (
    cluster_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_competitor_knowledge (
    entry_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    record_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_experiments (
    experiment_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_publish_results (
    job_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    record_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_content_format_fatigue (
    project TEXT NOT NULL,
    format_key TEXT NOT NULL,
    record_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (project, format_key)
);

CREATE TABLE IF NOT EXISTS project_content_characters (
    project TEXT NOT NULL,
    character_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, character_id)
);

CREATE TABLE IF NOT EXISTS project_content_picker_log (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    plan_id TEXT,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def ensure_schema(db) -> None:
    db.executescript(SCHEMA_SQL)
    from atnos.production.project_content_brain.content_intelligence import ensure_intelligence_schema
    ensure_intelligence_schema(db)


def _require_project(project: str) -> str:
    key = str(project).strip().casefold().replace(" ", "_")
    if key not in PROJECT_KEYS:
        raise ValueError("Unsupported production project")
    return key


def default_snapshot(project: str, *, profile: dict | None = None) -> ProjectBrainSnapshot:
    key = _require_project(project)
    production = (profile or {}).get("production") or {}
    now = utc_now_iso()
    return ProjectBrainSnapshot(
        project=key,
        brand={
            "channel_name": (profile or {}).get("channel_name"),
            "account_id": (profile or {}).get("account_id"),
            "allowed_topics": (profile or {}).get("allowed_topics") or [],
            "forbidden_topics": (profile or {}).get("forbidden_topics") or [],
        },
        audience={"language": (profile or {}).get("language") or production.get("language") or "en"},
        voice=production.get("voice") or {},
        visual_rules=list(production.get("visual_rules") or []),
        characters=[],
        production_rules=list(production.get("narrative_rules") or []),
        publication_history=[],
        errors=[],
        capabilities=dict(CAPABILITY_FLAGS),
        updated_at=now,
    )


class ProjectContentBrainStore:
    """Odczyt/zapis pamięci kanału; fail-closed przy braku danych zewnętrznych."""

    def __init__(self, db):
        ensure_schema(db)
        self.db = db

    def load_or_create_snapshot(self, project: str, *, profile: dict | None = None) -> dict:
        key = _require_project(project)
        row = self.db.execute(
            "SELECT snapshot_json FROM project_content_brain WHERE project=?", (key,)
        ).fetchone()
        if row:
            data = json.loads(row["snapshot_json"])
            data.setdefault("capabilities", dict(CAPABILITY_FLAGS))
            return data
        snap = default_snapshot(key, profile=profile).model_dump(mode="json")
        self.db.execute(
            "INSERT INTO project_content_brain(project,snapshot_json,updated_at) VALUES(?,?,?)",
            (key, json_dumps(snap), snap["updated_at"]),
        )
        return snap

    def save_snapshot(self, snapshot: ProjectBrainSnapshot | dict) -> dict:
        data = snapshot.model_dump(mode="json") if isinstance(snapshot, ProjectBrainSnapshot) else dict(snapshot)
        key = _require_project(str(data["project"]))
        data["project"] = key
        data["updated_at"] = utc_now_iso()
        data.setdefault("capabilities", dict(CAPABILITY_FLAGS))
        self.db.execute(
            "INSERT INTO project_content_brain(project,snapshot_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(project) DO UPDATE SET snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at",
            (key, json_dumps(data), data["updated_at"]),
        )
        return data

    def upsert_series(self, record: SeriesRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        data["updated_at"] = utc_now_iso()
        self.db.execute(
            "INSERT INTO project_content_series(project,series_key,record_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(project,series_key) DO UPDATE SET record_json=excluded.record_json,updated_at=excluded.updated_at",
            (data["project"], data["series_key"], json_dumps(data), data["updated_at"]),
        )
        return data

    def get_series(self, project: str, series_key: str) -> dict | None:
        row = self.db.execute(
            "SELECT record_json FROM project_content_series WHERE project=? AND series_key=?",
            (_require_project(project), series_key),
        ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def list_series(self, project: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT record_json FROM project_content_series WHERE project=? ORDER BY updated_at DESC",
            (_require_project(project),),
        ).fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    def upsert_campaign(self, record: CampaignRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        data["updated_at"] = utc_now_iso()
        self.db.execute(
            "INSERT INTO project_content_campaigns(campaign_id,project,record_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(campaign_id) DO UPDATE SET record_json=excluded.record_json,updated_at=excluded.updated_at,project=excluded.project",
            (data["campaign_id"], data["project"], json_dumps(data), data["updated_at"]),
        )
        return data

    def get_campaign(self, campaign_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT record_json FROM project_content_campaigns WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def upsert_plan(self, record: ContentPlanRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        data["updated_at"] = utc_now_iso()
        self.db.execute(
            "INSERT INTO project_content_plans(plan_id,project,record_json,stage,job_id,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(plan_id) DO UPDATE SET record_json=excluded.record_json,stage=excluded.stage,"
            "job_id=excluded.job_id,updated_at=excluded.updated_at,project=excluded.project",
            (data["plan_id"], data["project"], json_dumps(data), data["stage"], data.get("job_id"), data["updated_at"]),
        )
        return data

    def list_plans(self, project: str, *, stages: list[str] | None = None) -> list[dict]:
        key = _require_project(project)
        if stages:
            placeholders = ",".join("?" for _ in stages)
            rows = self.db.execute(
                f"SELECT record_json FROM project_content_plans WHERE project=? AND stage IN ({placeholders}) ORDER BY updated_at DESC",
                (key, *stages),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT record_json FROM project_content_plans WHERE project=? ORDER BY updated_at DESC",
                (key,),
            ).fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    def save_history_decision(self, job_id: str, project: str, decision: HistoryDecision | dict) -> dict:
        data = decision.model_dump(mode="json") if isinstance(decision, HistoryDecision) else dict(decision)
        now = utc_now_iso()
        self.db.execute(
            "INSERT INTO project_content_history_decisions(job_id,project,decision_json,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET decision_json=excluded.decision_json,project=excluded.project",
            (job_id, _require_project(project), json_dumps(data), now),
        )
        return data

    def get_history_decision(self, job_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT decision_json FROM project_content_history_decisions WHERE job_id=?", (job_id,)
        ).fetchone()
        return json.loads(row["decision_json"]) if row else None

    def save_trend_decision(self, record: TrendDecisionRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        entry_id = uuid4().hex
        self.db.execute(
            "INSERT INTO project_content_trend_decisions(id,project,record_json,created_at) VALUES(?,?,?,?)",
            (entry_id, data["project"], json_dumps(data), data["decided_at"]),
        )
        return {**data, "id": entry_id}

    def save_comment_cluster(self, record: CommentClusterRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        if data["comment_count"] <= 0 and not data["sample_refs"]:
            data["metrics_status"] = "UNKNOWN"
            data["status"] = "observe"
            data["confidence"] = 0.0
        self.db.execute(
            "INSERT INTO project_content_comment_clusters(cluster_id,project,record_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(cluster_id) DO UPDATE SET record_json=excluded.record_json,updated_at=excluded.updated_at",
            (data["cluster_id"], data["project"], json_dumps(data), utc_now_iso()),
        )
        return data

    def save_competitor_knowledge(self, record: CompetitorKnowledgeRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        self.db.execute(
            "INSERT INTO project_content_competitor_knowledge(entry_id,project,record_json,observed_at) VALUES(?,?,?,?) "
            "ON CONFLICT(entry_id) DO UPDATE SET record_json=excluded.record_json,observed_at=excluded.observed_at",
            (data["entry_id"], data["project"], json_dumps(data), data["observed_at"]),
        )
        return data

    def save_experiment(self, record: ExperimentRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        if data["metrics_status"] == "UNKNOWN":
            data["winner"] = None
            data["status"] = "observe" if data["status"] != "planned" else "planned"
            data["conclusion"] = data.get("conclusion") or "Brak metryk — nie ogłaszaj zwycięzcy A/B."
        data["updated_at"] = utc_now_iso()
        self.db.execute(
            "INSERT INTO project_content_experiments(experiment_id,project,record_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(experiment_id) DO UPDATE SET record_json=excluded.record_json,updated_at=excluded.updated_at",
            (data["experiment_id"], data["project"], json_dumps(data), data["updated_at"]),
        )
        return data

    def save_publish_result(self, record: PublishResultRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        if data["metrics_status"] == "UNKNOWN":
            data["views"] = None
            data["ctr"] = None
            data["retention"] = None
            data["avg_view_duration"] = None
            data["confidence"] = 0.0
            data["reason"] = data.get("reason") or "Brak metryk publikacji — status UNKNOWN."
        self.db.execute(
            "INSERT INTO project_content_publish_results(job_id,project,record_json,observed_at) VALUES(?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET record_json=excluded.record_json,observed_at=excluded.observed_at",
            (data["job_id"], data["project"], json_dumps(data), data["observed_at"]),
        )
        return data

    def save_format_fatigue(self, record: FormatFatigueRecord) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        if data["metrics_status"] == "UNKNOWN" or data["sample_size"] < 3:
            data["verdict"] = "insufficient_data"
            data["confidence"] = min(float(data.get("confidence") or 0), 0.2)
            data["reason"] = data.get("reason") or "Za mało prawdziwych wyników — nie deklaruj zmęczenia formatem."
        self.db.execute(
            "INSERT INTO project_content_format_fatigue(project,format_key,record_json,observed_at) VALUES(?,?,?,?) "
            "ON CONFLICT(project,format_key) DO UPDATE SET record_json=excluded.record_json,observed_at=excluded.observed_at",
            (data["project"], data["format_key"], json_dumps(data), data["observed_at"]),
        )
        return data

    def upsert_character(self, record: HostCharacterProfile) -> dict:
        data = record.model_dump(mode="json")
        data["project"] = _require_project(data["project"])
        if data["is_real_person"]:
            consents = (data.get("likeness_consent"), data.get("voice_consent"), data.get("reference_consent"))
            if any(c is not True for c in consents):
                raise ValueError("Profil prawdziwej osoby wymaga zgody na wizerunek, głos i referencje")
        data["updated_at"] = utc_now_iso()
        self.db.execute(
            "INSERT INTO project_content_characters(project,character_id,record_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(project,character_id) DO UPDATE SET record_json=excluded.record_json,updated_at=excluded.updated_at",
            (data["project"], data["character_id"], json_dumps(data), data["updated_at"]),
        )
        return data

    def list_characters(self, project: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT record_json FROM project_content_characters WHERE project=?",
            (_require_project(project),),
        ).fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    def log_picker(self, *, project: str, plan_id: str | None, reason: str, confidence: float, source: str, payload: dict) -> dict:
        entry = {
            "id": uuid4().hex,
            "project": _require_project(project),
            "plan_id": plan_id,
            "reason": reason,
            "confidence": confidence,
            "source": source,
            "payload": payload,
            "created_at": utc_now_iso(),
        }
        self.db.execute(
            "INSERT INTO project_content_picker_log(id,project,plan_id,reason,confidence,source,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (entry["id"], entry["project"], plan_id, reason, confidence, source, json_dumps(payload), entry["created_at"]),
        )
        return entry

    def collect_history_corpus(self, project: str) -> list[dict[str, Any]]:
        """Tematy/hooki/CTA z własnych zleceń i planów — bez wymyślonych wyników YouTube."""
        key = _require_project(project)
        corpus: list[dict[str, Any]] = []
        for row in self.db.execute(
            "SELECT o.job_id,o.request_json,j.status,j.script,j.hooks,j.selected_hook_id "
            "FROM pipeline_job_orders o JOIN pipeline_jobs j ON j.id=o.job_id "
            "WHERE json_extract(o.request_json,'$.project')=? ORDER BY o.created_at DESC LIMIT 200",
            (key,),
        ).fetchall():
            request = json.loads(row["request_json"] or "{}")
            hook_text = ""
            cta_text = ""
            script_raw = row["script"]
            if script_raw:
                try:
                    script = json.loads(script_raw)
                    segments = script.get("segments") if isinstance(script, dict) else None
                    if isinstance(segments, list) and segments:
                        first = segments[0] if isinstance(segments[0], dict) else {}
                        last = segments[-1] if isinstance(segments[-1], dict) else {}
                        if first.get("type") == "hook":
                            hook_text = str(first.get("text") or "")
                        if last.get("type") == "cta":
                            cta_text = str(last.get("text") or "")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            if not hook_text and row["hooks"]:
                try:
                    hooks = json.loads(row["hooks"])
                    if isinstance(hooks, list) and hooks:
                        selected = row["selected_hook_id"]
                        chosen = next((h for h in hooks if isinstance(h, dict) and h.get("id") == selected), hooks[0])
                        if isinstance(chosen, dict):
                            hook_text = str(chosen.get("text") or chosen.get("hook") or "")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            corpus.append(
                {
                    "job_id": row["job_id"],
                    "status": row["status"],
                    "topic": str(request.get("topic") or ""),
                    "direction": str(request.get("direction") or ""),
                    "series_key": request.get("series_key"),
                    "hook": hook_text,
                    "cta": cta_text,
                    "kind": "order",
                }
            )
        for plan in self.list_plans(key):
            corpus.append(
                {
                    "job_id": plan.get("job_id"),
                    "plan_id": plan.get("plan_id"),
                    "status": plan.get("stage"),
                    "topic": str(plan.get("idea") or ""),
                    "direction": "",
                    "series_key": plan.get("series_key"),
                    "hook": "",
                    "cta": "",
                    "kind": "plan",
                }
            )
        return corpus
