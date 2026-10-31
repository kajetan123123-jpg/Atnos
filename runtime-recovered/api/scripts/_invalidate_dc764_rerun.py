import json
import sqlite3
from datetime import datetime, timezone

db = r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\intelligence.db"
now = datetime.now(timezone.utc).isoformat()
job_id = "prod-dc76431dac84"
stages = (
    "remotion_render",
    "remotion_mux",
    "covers",
    "media_qa",
    "semantic/s01",
    "semantic/s02",
    "semantic/s03",
    "semantic/s04",
    "semantic/s05",
    "semantic/s06",
    "semantic/cover_portrait",
    "semantic/cover_landscape",
    "semantic_qa",
)
err = json.dumps(
    {
        "category": "forced_rerun",
        "message": "invalidate after s06 planner+QA contract fix",
    }
)
with sqlite3.connect(db) as conn:
    conn.execute(
        "UPDATE pipeline_jobs SET status=?, current_step=?, error=NULL, updated_at=? WHERE id=?",
        ("running", "rendering", now, job_id),
    )
    for stage in stages:
        conn.execute(
            "UPDATE pipeline_stage_checkpoints SET status=?, error_json=?, updated_at=? WHERE job_id=? AND stage=?",
            ("failed", err, now, job_id, stage),
        )
    conn.commit()
    print(conn.execute("SELECT status, current_step, error FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone())
    for row in conn.execute(
        "SELECT stage, status FROM pipeline_stage_checkpoints WHERE job_id=? AND stage IN ("
        "'remotion_render','remotion_mux','semantic/s06','semantic_qa')",
        (job_id,),
    ):
        print(row)
