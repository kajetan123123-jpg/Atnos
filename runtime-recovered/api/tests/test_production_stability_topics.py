from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest
import httpx
from pydantic import ValidationError

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect
from atnos.production.stability_topics import (
    STABILITY_SOURCE_CONTRACT,
    StabilityTopicError,
    canonical_stability_contracts,
    load_stability_topic_suite,
    parse_stability_source_record,
    seed_stability_topics,
    upgrade_unmodified_stability_topics,
    verify_stability_topic_sources,
)
from test_support import create_test_app


SUITE_PATH = TESTS_DIR.parents[0] / "config" / "wealth_blueprint_v1_stability_topics.json"


def test_canonical_stability_suite_has_ten_distinct_primary_sourced_topics() -> None:
    suite = load_stability_topic_suite(SUITE_PATH)

    assert len(suite.topics) == 10
    assert len({topic.id for topic in suite.topics}) == 10
    assert len({topic.title.casefold() for topic in suite.topics}) == 10
    assert all(topic.source_url.startswith("https://") for topic in suite.topics)
    assert all("HookScene" in topic.visual_test_focus for topic in suite.topics)
    assert all("CTAScene" in topic.visual_test_focus for topic in suite.topics)
    assert {scene for topic in suite.topics for scene in topic.visual_test_focus} == {
        "HookScene",
        "ImageScene",
        "NumberScene",
        "ChartScene",
        "CTAScene",
    }
    assert all(topic.production_brief.expected_scene_evidence for topic in suite.topics)


def test_stability_suite_rejects_non_primary_source(tmp_path: Path) -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["topics"][0]["source_url"] = "https://example.com/unverified"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="approved primary-source host"):
        load_stability_topic_suite(path)


def test_stability_seed_is_idempotent_and_does_not_invent_scores(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    first = seed_stability_topics(
        settings,
        account_id="youtube:account-c5034bf4@example.invalid",
        suite_path=SUITE_PATH,
    )
    second = seed_stability_topics(
        settings,
        account_id="youtube:account-c5034bf4@example.invalid",
        suite_path=SUITE_PATH,
    )

    assert first["inserted"] == 10
    assert second["inserted"] == 0
    assert second["reused"] == 10
    with connect(settings) as db:
        rows = db.execute(
            "SELECT id, status, total_score, inspired_by FROM topic_queue WHERE id LIKE 'wealth-v1-stability-%'"
        ).fetchall()
    assert len(rows) == 10
    assert all(row["status"] == "approved" for row in rows)
    assert all(row["total_score"] is None for row in rows)
    assert all(json.loads(row["inspired_by"])["contract"] == STABILITY_SOURCE_CONTRACT for row in rows)
    assert all(parse_stability_source_record(row["inspired_by"]) for row in rows)


def test_stability_source_record_rejects_tampering() -> None:
    contract = canonical_stability_contracts(SUITE_PATH)["wealth-v1-stability-compound-interest"]
    assert parse_stability_source_record(contract) == contract

    tampered = dict(contract)
    tampered["source_facts"] = ["Invented guaranteed return."]
    assert parse_stability_source_record(tampered) is None


def test_stability_seed_refuses_to_overwrite_changed_topic(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    seed_stability_topics(settings, account_id="youtube:account-c5034bf4@example.invalid", suite_path=SUITE_PATH)
    with connect(settings) as db:
        db.execute(
            "UPDATE topic_queue SET description = 'locally changed' WHERE id = 'wealth-v1-stability-compound-interest'"
        )
        db.commit()

    with pytest.raises(StabilityTopicError, match="Refusing to overwrite"):
        seed_stability_topics(settings, account_id="youtube:account-c5034bf4@example.invalid", suite_path=SUITE_PATH)


def test_canonical_upgrade_backs_up_and_updates_only_untouched_rows(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    account_id = "youtube:account-c5034bf4@example.invalid"
    seed_stability_topics(settings, account_id=account_id, suite_path=SUITE_PATH)
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["topics"][0]["title"] = "A Revised Compound Interest Benchmark Title"
    revised_suite = tmp_path / "revised-suite.json"
    revised_suite.write_text(json.dumps(payload), encoding="utf-8")
    backup = tmp_path / "evidence" / "topics-backup.json"

    result = upgrade_unmodified_stability_topics(
        settings,
        account_id=account_id,
        suite_path=revised_suite,
        backup_path=backup,
    )

    assert result["updated"] == 10
    assert backup.is_file()
    assert len(json.loads(backup.read_text(encoding="utf-8"))["rows"]) == 10
    with connect(settings) as db:
        row = db.execute(
            "SELECT title FROM topic_queue WHERE id = 'wealth-v1-stability-compound-interest'"
        ).fetchone()
    assert row["title"] == "A Revised Compound Interest Benchmark Title"


def test_canonical_upgrade_rejects_row_with_changed_timestamp(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    account_id = "youtube:account-c5034bf4@example.invalid"
    seed_stability_topics(settings, account_id=account_id, suite_path=SUITE_PATH)
    with connect(settings) as db:
        db.execute(
            "UPDATE topic_queue SET updated_at = 'manual-change' WHERE id = 'wealth-v1-stability-compound-interest'"
        )
        db.commit()

    with pytest.raises(StabilityTopicError, match="modified benchmark topic"):
        upgrade_unmodified_stability_topics(
            settings,
            account_id=account_id,
            suite_path=SUITE_PATH,
            backup_path=tmp_path / "must-not-exist.json",
        )
    assert not (tmp_path / "must-not-exist.json").exists()


def test_live_source_verifier_records_hashes_without_page_contents(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8", "etag": '"source-v1"'},
            content=(b"official primary source material " * 80),
            request=request,
        )

    output = tmp_path / "source-evidence.json"
    report = asyncio.run(
        verify_stability_topic_sources(
            SUITE_PATH,
            output_path=output,
            transport=httpx.MockTransport(handler),
        )
    )

    assert report["passed"] is True
    assert report["suite_sha256"]
    assert output.is_file()
    expected_source_count = len({topic.source_url for topic in load_stability_topic_suite(SUITE_PATH).topics})
    assert len(report["sources"]) == expected_source_count
    assert all(record["content_sha256"] for record in report["sources"])
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert all("content" not in record and "body" not in record for record in persisted["sources"])


def test_live_source_verifier_rejects_redirect_outside_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 1024, request=request)
        return httpx.Response(302, headers={"location": "https://example.com/fake"}, request=request)

    report = asyncio.run(
        verify_stability_topic_sources(SUITE_PATH, transport=httpx.MockTransport(handler))
    )

    assert report["passed"] is False
    assert all(record["status"] == "failed" for record in report["sources"])
