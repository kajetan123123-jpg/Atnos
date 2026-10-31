from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, utc_now_iso
from atnos.production.orchestrator import ProductionOrchestrator
from test_support import create_test_app


def _seed_topic(settings) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'approved', ?, ?, ?)
            """,
            (
                "topic-cancel",
                "acct-cancel",
                "Why Compound Interest Rewards Time",
                "A source-backed compound interest explainer.",
                88,
                now,
                now,
            ),
        )
        db.commit()


def test_cancelled_production_restores_topic_and_records_job_state(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings)

    async def cancel_job(_self, _context):
        raise asyncio.CancelledError("simulated operator cancellation")

    monkeypatch.setattr(ProductionOrchestrator, "run_job", cancel_job)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ProductionOrchestrator(settings).start(
                account_id="acct-cancel",
                topic_id="topic-cancel",
            )
        )

    with connect(settings) as db:
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-cancel'").fetchone()
        job = db.execute("SELECT status, current_step, error FROM pipeline_jobs WHERE topic_id = 'topic-cancel'").fetchone()

    assert topic["status"] == "approved"
    assert job["status"] == "cancelled"
    assert job["current_step"] == "cancelled"
    assert job["error"] == "production job cancelled before completion"
