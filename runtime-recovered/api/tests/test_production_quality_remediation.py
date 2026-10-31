from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, utc_now_iso
from atnos.production.quality_remediation import ProductionQualityRemediationService, remediation_reason
from atnos.production.quality_scheduler import QUALITY_REVIEW_BLOCKED
from test_support import create_test_app


def test_remediation_reason_maps_semantic_visual_failures() -> None:
    assert (
        remediation_reason({"blockers": ["semantic_visual:semantic_childish"]})
        == "quality_auto_remediation:thumbnail_semantic_failed"
    )
    assert (
        remediation_reason({"blockers": ["semantic_first_frame:off_topic"]})
        == "quality_auto_remediation:first_frame_semantic_failed"
    )
    assert (
        remediation_reason({"blockers": ["thumbnail_quality:thumbnail_blurry_or_flat"]})
        == "quality_auto_remediation:thumbnail_quality_failed"
    )


def test_quality_remediation_queues_regeneration_without_upload(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_quality_auto_remediation_run_jobs = False
    asyncio.run(init_intelligence_db(settings))
    _seed_quality_blocked_job(settings)

    result = asyncio.run(ProductionQualityRemediationService(settings).run_due(limit=1))

    assert result["status"] == "completed"
    assert result["planned_count"] == 1
    assert result["started_count"] == 1
    recovery = result["results"][0]
    assert recovery["status"] == "queued"
    assert recovery["source_job_id"] == "prod-quality-source"
    assert recovery["reason"] == "quality_auto_remediation:thumbnail_semantic_failed"
    assert recovery["remediation"]["run_jobs"] is False

    with connect(settings) as db:
        source = db.execute(
            "SELECT review_status FROM pipeline_jobs WHERE id = 'prod-quality-source'",
        ).fetchone()
        fresh = db.execute(
            """
            SELECT id, account_id, topic_id, status, youtube_url, upload_result
            FROM pipeline_jobs
            WHERE id = ?
            """,
            (recovery["job_id"],),
        ).fetchone()
    assert str(source["review_status"]).startswith(f"regeneration_queued:{recovery['job_id']}:")
    assert fresh is not None
    assert fresh["status"] == "queued"
    assert fresh["youtube_url"] is None
    assert fresh["upload_result"] is None


def _seed_quality_blocked_job(settings) -> None:
    now = utc_now_iso()
    quality_review_result = {
        "status": "blocked",
        "blockers": ["semantic_visual:semantic_childish", "semantic_visual:topic_relevance_score_below_floor"],
        "next_action": "regenerate_thumbnail_and_first_frame_with_topic_specific_visuals",
    }
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO intelligence_accounts (
                account_id, niches, region, timeframe, last_requested_at, enabled, content_profile
            )
            VALUES ('acct-quality', '["finance"]', 'PL', 'now 7-d', ?, 1, 'wealth finance')
            """,
            (now,),
        )
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-quality', 'acct-quality', 'Why Money Feels Fake', 'finance', 'done', 85, ?, ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, review_status,
                quality_review_status, quality_review_result, quality_review_checked_at,
                created_at, updated_at
            )
            VALUES (
                'prod-quality-source', 'acct-quality', 'topic-quality', 'qa_passed', 'qa',
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                QUALITY_REVIEW_BLOCKED,
                QUALITY_REVIEW_BLOCKED,
                json.dumps(quality_review_result),
                now,
                now,
                now,
            ),
        )
        db.commit()
