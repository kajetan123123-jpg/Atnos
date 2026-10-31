from __future__ import annotations

import json
from typing import Literal
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atnos.core.logging import redact_secrets
from atnos.intelligence.storage import connect, json_dumps, utc_now_iso


class ProductionFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(min_length=5, max_length=1000)
    # once = jednorazowo (tylko źródłowe zlecenie); film/production = film;
    # series / project / global = szerszy zakres. production zachowane dla kompatybilności.
    scope: Literal["once", "film", "production", "series", "project", "global"] = "production"
    category: Literal["auto", "narration", "voice", "visuals", "captions", "thumbnail"] = "auto"

    @field_validator("note")
    @classmethod
    def exclude_secrets(cls, value):
        if redact_secrets(value) != value:
            raise ValueError("Feedback must not contain credentials")
        return value


def classify_feedback(note: str) -> str:
    normalized = unicodedata.normalize("NFKD", note.casefold()).encode("ascii", "ignore").decode()
    for category, terms in (
        ("thumbnail", ("miniatur", "thumbnail", "cover")),
        ("captions", ("napis", "caption", "subtitle")),
        ("voice", ("gos", "voice", "wymow", "pronounc", "breath")),
        ("visuals", ("scen", "grafik", "visual", "footage", "montaz", "transition")),
    ):
        if any(term in normalized for term in terms):
            return category
    return "narration"


def feedback_records(db, project: str) -> list[dict]:
    rows = db.execute(
        "SELECT e.event,e.detail_json FROM pipeline_job_events e "
        "JOIN pipeline_job_orders o ON o.job_id=e.job_id "
        "WHERE json_extract(o.request_json,'$.project')=? "
        "AND e.event IN ('feedback_added','feedback_disabled') ORDER BY e.id", (project,)).fetchall()
    records = {}
    for row in rows:
        detail = json.loads(row["detail_json"])
        if row["event"] == "feedback_added":
            records[detail["id"]] = {**detail, "active": True}
        elif detail["id"] in records:
            records[detail["id"]]["active"] = False
    return list(records.values())


def snapshot_feedback(db, project: str, series_key: str | None) -> list[dict]:
    """Zasady na przyszłe zlecenia: film/once nie wchodzą; global obejmuje wszystkie projekty."""
    rules = []
    for row in feedback_records(db, project):
        if not row["active"]:
            continue
        scope = row["scope"]
        if scope in {"once", "film", "production"}:
            continue
        if scope == "project" or scope == "global":
            rules.append(row)
        elif scope == "series" and series_key and row.get("series_key") == series_key:
            rules.append(row)
    # Globalne reguły z innych projektów (ten sam event log / wszystkie zamówienia).
    for row in _global_feedback_records(db):
        if row["active"] and row["scope"] == "global" and row["id"] not in {r["id"] for r in rules}:
            rules.append(row)
    if len(rules) > 40:
        raise ValueError("More than forty active project rules; consolidate or disable older feedback first")
    return rules


def _global_feedback_records(db) -> list[dict]:
    rows = db.execute(
        "SELECT e.event,e.detail_json FROM pipeline_job_events e "
        "WHERE e.event IN ('feedback_added','feedback_disabled') ORDER BY e.id"
    ).fetchall()
    records = {}
    for row in rows:
        detail = json.loads(row["detail_json"])
        if detail.get("scope") != "global":
            continue
        if row["event"] == "feedback_added":
            records[detail["id"]] = {**detail, "active": True}
        elif detail["id"] in records:
            records[detail["id"]]["active"] = False
    return list(records.values())


class ProductionFeedbackStore:
    """Append scoped feedback to the production event log; keep old snapshots intact."""

    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def _order(db, job_id):
        row = db.execute("SELECT request_json FROM pipeline_job_orders WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Production order not found")
        return json.loads(row["request_json"])

    def list(self, job_id: str) -> list[dict]:
        with connect(self.settings) as db:
            order = self._order(db, job_id)
            by_id = {row["id"]: row for row in feedback_records(db, order["project"])}
            for row in _global_feedback_records(db):
                by_id.setdefault(row["id"], row)
            return [
                row for row in by_id.values()
                if row["source_job_id"] == job_id
                or row["scope"] in {"project", "global"}
                or (row["scope"] == "series" and row.get("series_key") == order.get("series_key"))
            ]

    def add(self, job_id: str, feedback: ProductionFeedback) -> dict:
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            order = self._order(db, job_id)
            scope = "film" if feedback.scope == "production" else feedback.scope
            if scope == "series" and not order.get("series_key"):
                raise ValueError("This production has no series; choose film, project or global scope")
            record = {"id": uuid4().hex, "source_job_id": job_id, "project": order["project"],
                      "series_key": order.get("series_key") if scope == "series" else None,
                      "scope": scope,
                      "category": feedback.category if feedback.category != "auto" else classify_feedback(feedback.note),
                      "note": feedback.note, "created_at": utc_now_iso()}
            db.execute("INSERT INTO pipeline_job_events(job_id,stage,event,detail_json,created_at) VALUES(?,NULL,'feedback_added',?,?)",
                       (job_id, json_dumps(record), record["created_at"]))
            db.commit()
        return {**record, "active": True}

    def disable(self, job_id: str, feedback_id: str) -> None:
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            order = self._order(db, job_id)
            records = feedback_records(db, order["project"])
            rule = next((row for row in records if row["id"] == feedback_id and row["source_job_id"] == job_id), None)
            if rule is None:
                raise KeyError("Feedback not found for this production")
            if rule["active"]:
                db.execute("INSERT INTO pipeline_job_events(job_id,stage,event,detail_json,created_at) VALUES(?,NULL,'feedback_disabled',?,?)",
                           (job_id, json_dumps({"id": feedback_id}), utc_now_iso()))
            db.commit()
