from __future__ import annotations

from atnos.core.config import Settings
from atnos.intelligence import storage


def test_connect_initializes_schema_once_per_process(tmp_path, monkeypatch) -> None:
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    db_path = storage.intelligence_db_path(settings)
    storage._INITIALIZED_DB_PATHS.discard(db_path)

    calls = 0
    original = storage._init_intelligence_db_uncached

    def wrapped(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(storage, "_init_intelligence_db_uncached", wrapped)

    with storage.connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] > 0
    with storage.connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] > 0

    assert calls == 1


def test_connect_sets_sqlite_busy_timeout(tmp_path) -> None:
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    db_path = storage.intelligence_db_path(settings)
    storage._INITIALIZED_DB_PATHS.discard(db_path)

    with storage.connect(settings) as db:
        busy_timeout_ms = db.execute("PRAGMA busy_timeout").fetchone()[0]

    assert busy_timeout_ms >= int(storage.SQLITE_BUSY_TIMEOUT_S * 1000)
