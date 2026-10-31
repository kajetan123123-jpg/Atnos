import sqlite3
c = sqlite3.connect(r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\intelligence.db")
c.row_factory = sqlite3.Row
job = c.execute(
    "SELECT status, current_step, substr(coalesce(error,''),1,300) AS error FROM pipeline_jobs WHERE id=?",
    ("prod-dc76431dac84",),
).fetchone()
print(dict(job) if job else None)
