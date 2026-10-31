"""Inspect canonical credential readability and references; never export secret values."""
import json
import os
from pathlib import Path
import sqlite3
from collections import Counter

os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
from atnos.core.config import get_settings
from atnos.services.secret_vault_service import SecretVaultService
from atnos.services.oauth_token_store import OAuthTokenStore

root = Path('E:/Atnos/App/reports/readiness-20260909/canonical-accounts-20260914')
root.mkdir(exist_ok=True)
settings = get_settings()
db_path = Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/atnos.db')
assert (db_path.parent/'account-secrets/aes-256-gcm.master.key').is_file()
vault = SecretVaultService(settings)
tokens = OAuthTokenStore(settings)
db = sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True)
db.row_factory = sqlite3.Row
tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
references = []
for table in tables:
    quoted = '"'+table.replace('"','""')+'"'
    for row in db.execute('PRAGMA foreign_key_list('+quoted+')'):
        if row['table'] == 'account_credentials':
            references.append((table, row['from']))
accounts = []
permission_targets = {}
def retain_permission_target(secret_ref):
    path = vault._resolve_secret_path(secret_ref)
    stat = path.stat()
    permission_targets[str(path)] = {'path': str(path), 'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns}
for row in db.execute('SELECT * FROM account_credentials ORDER BY id'):
    item = {key: row[key] for key in ('id','provider_key','status','secret_type','storage_backend','linked_integration_key','linked_project_id')}
    item['secret_readable'] = None
    if row['secret_ref'] and row['storage_backend'] != 'metadata_only':
        try:
            secret = vault.resolve_secret(row['secret_ref'])
            item['secret_readable'] = bool(secret)
            del secret
        except Exception as exc:
            item.update(secret_readable=False, error_type=type(exc).__name__, cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
            if isinstance(exc.__cause__, PermissionError):
                retain_permission_target(row['secret_ref'])
    item['references'] = {}
    for table, column in references:
        table_sql = '"'+table.replace('"','""')+'"'
        column_sql = '"'+column.replace('"','""')+'"'
        count = db.execute('SELECT count(*) FROM '+table_sql+' WHERE '+column_sql+'=?', (row['id'],)).fetchone()[0]
        if count:
            item['references'][table] = count
    item['orphan_oauth_metadata'] = (row['account_identifier'].startswith('oauth:') and not item['references']
                                     and not row['secret_ref'] and row['linked_project_id'] is None)
    accounts.append(item)
connections = []
for row in db.execute('SELECT id,connector_key,status,account_id,token_ref FROM oauth_connections ORDER BY id'):
    item = {key: row[key] for key in ('id','connector_key','status','account_id')}
    item['bundle_readable'] = None
    if row['token_ref']:
        try:
            bundle = tokens.load_token_bundle(row['token_ref'])
            item.update(bundle_readable=True, has_access_token=bool(bundle.access_token), has_refresh_token=bool(bundle.refresh_token))
            del bundle
        except Exception as exc:
            item.update(bundle_readable=False, error_type=type(exc).__name__, cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
            if isinstance(exc.__cause__, PermissionError):
                retain_permission_target(row['token_ref'])
    connections.append(item)
db.close()
summary = {'account_count': len(accounts), 'readable_secrets': sum(a['secret_readable'] is True for a in accounts),
           'unreadable_secrets': sum(a['secret_readable'] is False for a in accounts),
           'orphan_oauth_metadata_ids': [a['id'] for a in accounts if a['orphan_oauth_metadata']],
           'oauth_status_counts': dict(Counter(c['status'] for c in connections)),
           'readable_oauth_bundles': sum(c['bundle_readable'] is True for c in connections),
           'unreadable_oauth_bundles': sum(c['bundle_readable'] is False for c in connections),
           'scope': 'Read-only local decryption and relational references; no provider login, no deletion, no secret values exported.'}
(root/'inventory.json').write_text(json.dumps({'summary':summary,'accounts':accounts,'oauth_connections':connections}, indent=2), encoding='utf-8')
if permission_targets:
    (root/'permission-targets.json').write_text(json.dumps({'files':list(permission_targets.values()), 'scope':'Existing referenced encrypted files with confirmed PermissionError only.'}, indent=2), encoding='utf-8')
print(json.dumps(summary))
