from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.intelligence.storage import connect, init_intelligence_db
from test_support import create_test_app


def test_pipeline_jobs_alias_reads_production_jobs(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))
    with connect(app.state.settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES (
                'topic-1', 'yt-finanse-pl', 'Testowy temat', 'Opis', 'approved', 88,
                '2026-06-04T00:00:00+00:00', '2026-06-04T00:00:00+00:00'
            )
            """
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, created_at, updated_at
            )
            VALUES (
                'job-1', 'yt-finanse-pl', 'topic-1', 'qa_passed', 'qa',
                '2026-06-04T00:00:00+00:00', '2026-06-04T00:00:00+00:00'
            )
            """
        )
        db.commit()

    client = TestClient(app)
    response = client.get("/api/pipeline/jobs", params={"account_id": "yt-finanse-pl"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs"][0]["id"] == "job-1"
    assert payload["jobs"][0]["topic_title"] == "Testowy temat"


def test_pipeline_alias_blocks_legacy_direct_rendering_that_bypasses_quality_gates(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/pipeline/start",
        json={
            "account_id": "youtube:wealth-blueprint",
            "topic": {"title": "Why compounding matters", "description": "Legacy payload"},
        },
    )

    assert response.status_code == 409
    payload = response.json()["detail"]
    assert payload["required_route"] == "POST /api/production/start"
    assert "semantic visual audit" in payload["message"]
