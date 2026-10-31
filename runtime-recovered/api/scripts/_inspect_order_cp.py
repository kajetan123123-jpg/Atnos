import sqlite3, json
db = r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\intelligence.db"
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row
job_id = "prod-dc76431dac84"
job = c.execute("SELECT id,status,current_step,error,updated_at FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone()
print("JOB:", dict(job) if job else None)
print("--- checkpoints ---")
for row in c.execute("SELECT stage,status,attempts,substr(input_hash,1,12) AS h,substr(coalesce(error_json,''),1,180) AS err FROM pipeline_stage_checkpoints WHERE job_id=? ORDER BY rowid", (job_id,)):
    print(dict(row))
