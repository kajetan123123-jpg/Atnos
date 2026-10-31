"""Create only the local, disconnected account fixture used by the browser audit."""
import sqlite3
from pathlib import Path

root = Path('E:/Atnos/App/reports/readiness-20260909/isolated-runtime').resolve()
database = (root / 'data/intelligence.db').resolve()
if not database.is_relative_to(root) or not database.is_file():
    raise RuntimeError('The isolated readiness database must already exist')
with sqlite3.connect(database) as db:
    db.execute("INSERT OR IGNORE INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES(?,?,?,?)",
               ('yt-wealth-blueprint', '["finance","readiness_fixture"]', 'US', '2026-09-10T00:00:00Z'))
print('Isolated production account fixture ready; no OAuth connection or canonical data changed.')
