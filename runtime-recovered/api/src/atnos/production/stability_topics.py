from __future__ import annotations

import json
import hashlib
import hmac
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from atnos.core.config import Settings
from atnos.intelligence.storage import connect


STABILITY_SOURCE_CONTRACT = "wealth_blueprint_v1_stability_source_v1"
ALLOWED_SOURCE_HOSTS = {"investor.gov", "www.investor.gov", "consumerfinance.gov", "www.consumerfinance.gov", "finra.org", "www.finra.org"}
ALLOWED_SCENE_TYPES = {"HookScene", "ImageScene", "NumberScene", "ChartScene", "CTAScene"}
DEFAULT_STABILITY_SUITE_PATH = Path(__file__).resolve().parents[3] / "config" / "wealth_blueprint_v1_stability_topics.json"


class StabilityTopicError(RuntimeError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StabilityProductionBrief(_StrictModel):
    hook_strategy: str = Field(min_length=12, max_length=180)
    image_subject: str = Field(min_length=20, max_length=300)
    cta_action: str = Field(min_length=8, max_length=140)
    expected_scene_evidence: list[str] = Field(min_length=3, max_length=8)


class StabilityTopic(_StrictModel):
    id: str = Field(pattern=r"^wealth-v1-stability-[a-z0-9-]+$")
    title: str = Field(min_length=12, max_length=100)
    description: str = Field(min_length=80, max_length=900)
    source_name: str = Field(min_length=5)
    source_url: str
    source_facts: list[str] = Field(min_length=1, max_length=5)
    forbidden_claims: list[str] = Field(min_length=1, max_length=6)
    visual_test_focus: list[str] = Field(min_length=3, max_length=5)
    production_brief: StabilityProductionBrief

    @model_validator(mode="after")
    def validate_source_and_scope(self) -> "StabilityTopic":
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SOURCE_HOSTS:
            raise ValueError(f"source_url must use an approved primary-source host: {self.source_url}")
        invalid_scenes = set(self.visual_test_focus) - ALLOWED_SCENE_TYPES
        if invalid_scenes:
            raise ValueError(f"visual_test_focus contains unsupported scenes: {sorted(invalid_scenes)}")
        if "HookScene" not in self.visual_test_focus or "CTAScene" not in self.visual_test_focus:
            raise ValueError("every benchmark topic must exercise HookScene and CTAScene")
        return self


class StabilityTopicSuite(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    suite_id: Literal["WEALTH_BLUEPRINT_V1_STABILITY_TOPICS_001"]
    channel: Literal["wealth_blueprint"]
    language_code: Literal["en"]
    source_checked_at: str
    topics: list[StabilityTopic] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def validate_uniqueness(self) -> "StabilityTopicSuite":
        ids = [topic.id for topic in self.topics]
        titles = [" ".join(topic.title.casefold().split()) for topic in self.topics]
        if len(set(ids)) != 10 or len(set(titles)) != 10:
            raise ValueError("stability suite requires ten distinct IDs and titles")
        return self


def load_stability_topic_suite(path: Path) -> StabilityTopicSuite:
    return StabilityTopicSuite.model_validate_json(path.read_text(encoding="utf-8"))


def seed_stability_topics(
    settings: Settings,
    *,
    account_id: str,
    suite_path: Path,
) -> dict[str, object]:
    """Idempotently seed the existing topic queue with source-backed benchmark topics."""
    suite = load_stability_topic_suite(suite_path)
    now = datetime.now(UTC).isoformat()
    inserted = 0
    reused = 0
    with connect(settings) as db:
        for topic in suite.topics:
            evidence = _topic_evidence(suite, topic)
            existing = db.execute(
                "SELECT account_id, title, description, inspired_by, status FROM topic_queue WHERE id = ?",
                (topic.id,),
            ).fetchone()
            if existing is not None:
                expected = (account_id, topic.title, topic.description, evidence, "approved")
                actual = tuple(existing[key] for key in ("account_id", "title", "description", "inspired_by", "status"))
                if actual != expected:
                    raise StabilityTopicError(f"Refusing to overwrite a changed benchmark topic: {topic.id}")
                reused += 1
                continue
            db.execute(
                """
                INSERT INTO topic_queue (
                    id, account_id, title, description, inspired_by, status, total_score,
                    clickbait_risk, evergreen, shorts_potential, llm_unavailable, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'approved', NULL, 0, 1, 1, 0, ?, ?)
                """,
                (topic.id, account_id, topic.title, topic.description, evidence, now, now),
            )
            inserted += 1
        db.commit()
    return {
        "status": "verified",
        "suite_id": suite.suite_id,
        "account_id": account_id,
        "topic_ids": [topic.id for topic in suite.topics],
        "inserted": inserted,
        "reused": reused,
    }


def upgrade_unmodified_stability_topics(
    settings: Settings,
    *,
    account_id: str,
    suite_path: Path,
    backup_path: Path,
) -> dict[str, object]:
    """Upgrade only untouched benchmark rows that have never entered production."""
    suite = load_stability_topic_suite(suite_path)
    expected_ids = {topic.id for topic in suite.topics}
    topics_by_id = {topic.id: topic for topic in suite.topics}
    with connect(settings) as db:
        rows = db.execute(
            "SELECT * FROM topic_queue WHERE id LIKE 'wealth-v1-stability-%' ORDER BY id"
        ).fetchall()
        actual_ids = {str(row["id"]) for row in rows}
        if actual_ids != expected_ids:
            raise StabilityTopicError(
                f"Canonical upgrade requires the exact benchmark ID set; missing={sorted(expected_ids - actual_ids)}, "
                f"unexpected={sorted(actual_ids - expected_ids)}"
            )
        for row in rows:
            raw_record = _raw_stability_source_record(row["inspired_by"])
            record = parse_stability_source_record(row["inspired_by"])
            untouched = str(row["created_at"] or "") == str(row["updated_at"] or "")
            legacy_unsigned_canonical = raw_record == _topic_evidence_payload(suite, topics_by_id[str(row["id"])])
            if (
                str(row["account_id"] or "") != account_id
                or str(row["status"] or "") != "approved"
                or not (untouched or legacy_unsigned_canonical)
                or not (record or legacy_unsigned_canonical)
                or (raw_record or {}).get("suite_id") != suite.suite_id
            ):
                raise StabilityTopicError(f"Refusing to upgrade a modified benchmark topic: {row['id']}")
        placeholders = ",".join("?" for _ in expected_ids)
        used = db.execute(
            f"SELECT topic_id, COUNT(*) AS count FROM production_jobs WHERE topic_id IN ({placeholders}) GROUP BY topic_id",
            list(sorted(expected_ids)),
        ).fetchall()
        if used:
            raise StabilityTopicError(
                f"Refusing to upgrade benchmark topics already used in production: "
                f"{[(str(row['topic_id']), int(row['count'])) for row in used]}"
            )
        backup = {
            "schema_version": "1.0",
            "suite_id": suite.suite_id,
            "target_suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
            "backed_up_at": datetime.now(UTC).isoformat(),
            "account_id": account_id,
            "rows": [dict(row) for row in rows],
        }
        _atomic_json_write(backup_path, backup)
        now = datetime.now(UTC).isoformat()
        for topic in suite.topics:
            db.execute(
                """
                UPDATE topic_queue
                SET title = ?, description = ?, inspired_by = ?, updated_at = ?
                WHERE id = ? AND account_id = ?
                """,
                (topic.title, topic.description, _topic_evidence(suite, topic), now, topic.id, account_id),
            )
        db.commit()
    return {
        "status": "verified",
        "suite_id": suite.suite_id,
        "account_id": account_id,
        "updated": len(suite.topics),
        "backup_path": str(backup_path),
    }


def _topic_evidence(suite: StabilityTopicSuite, topic: StabilityTopic) -> str:
    payload = _topic_evidence_payload(suite, topic)
    payload["record_hash"] = _source_record_hash(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _topic_evidence_payload(suite: StabilityTopicSuite, topic: StabilityTopic) -> dict[str, object]:
    return {
        "contract": STABILITY_SOURCE_CONTRACT,
        "suite_id": suite.suite_id,
        "source_checked_at": suite.source_checked_at,
        "source_name": topic.source_name,
        "source_url": topic.source_url,
        "source_facts": topic.source_facts,
        "forbidden_claims": topic.forbidden_claims,
        "visual_test_focus": topic.visual_test_focus,
        "production_brief": topic.production_brief.model_dump(mode="json"),
    }


def canonical_stability_contracts(
    suite_path: Path = DEFAULT_STABILITY_SUITE_PATH,
) -> dict[str, dict[str, object]]:
    suite = load_stability_topic_suite(suite_path)
    contracts: dict[str, dict[str, object]] = {}
    for topic in suite.topics:
        payload = _topic_evidence_payload(suite, topic)
        payload["record_hash"] = _source_record_hash(payload)
        contracts[topic.id] = payload
    return contracts


def parse_stability_source_record(value: object) -> dict[str, object] | None:
    payload = _raw_stability_source_record(value)
    if not payload:
        return None
    stored_hash = str(payload.get("record_hash") or "")
    if not stored_hash or not hmac.compare_digest(stored_hash, _source_record_hash(payload)):
        return None
    return payload


def is_stability_source_record(value: object) -> bool:
    return parse_stability_source_record(value) is not None


def _raw_stability_source_record(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        payload = dict(value)
    else:
        try:
            payload = json.loads(str(value or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(payload, dict) or payload.get("contract") != STABILITY_SOURCE_CONTRACT:
        return None
    return payload


def _source_record_hash(payload: dict[str, object]) -> str:
    material = dict(payload)
    material.pop("record_hash", None)
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def verify_stability_topic_sources(
    suite_path: Path,
    *,
    output_path: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Verify live primary-source availability and persist body hashes, not page contents."""
    suite = load_stability_topic_suite(suite_path)
    checked_at = datetime.now(UTC).isoformat()
    topics_by_url: dict[str, list[str]] = {}
    for topic in suite.topics:
        topics_by_url.setdefault(topic.source_url, []).append(topic.id)
    records: list[dict[str, object]] = []
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        transport=transport,
        headers={"User-Agent": "Atnos-Wealth-Blueprint-Source-Verification/1.0"},
    ) as client:
        for url, topic_ids in topics_by_url.items():
            record: dict[str, object] = {
                "source_url": url,
                "topic_ids": topic_ids,
                "status": "failed",
                "checked_at": checked_at,
            }
            try:
                response = await client.get(url)
                final_url = str(response.url)
                final_host = urlparse(final_url).hostname
                content_type = str(response.headers.get("content-type") or "").casefold()
                body = response.content
                valid_type = any(token in content_type for token in ("text/html", "application/pdf", "text/plain"))
                passed = (
                    response.status_code == 200
                    and final_host in ALLOWED_SOURCE_HOSTS
                    and valid_type
                    and len(body) >= 512
                )
                record.update(
                    {
                        "status": "verified" if passed else "failed",
                        "status_code": response.status_code,
                        "final_url": final_url,
                        "content_type": content_type,
                        "bytes": len(body),
                        "content_sha256": hashlib.sha256(body).hexdigest(),
                        "etag": str(response.headers.get("etag") or ""),
                        "last_modified": str(response.headers.get("last-modified") or ""),
                    }
                )
            except (httpx.HTTPError, OSError) as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
            records.append(record)
    passed = all(record["status"] == "verified" for record in records)
    report: dict[str, object] = {
        "schema_version": "1.0",
        "suite_id": suite.suite_id,
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "status": "verified" if passed else "failed",
        "passed": passed,
        "checked_at": checked_at,
        "sources": records,
    }
    if output_path is not None:
        _atomic_json_write(output_path, report)
        report["report_path"] = str(output_path)
    return report


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
