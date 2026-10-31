from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from atnos.core.paths import atnos_state_dir


def default_cache_path() -> Path:
    configured = os.getenv("ATNOS_RESEARCH_CACHE_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    return atnos_state_dir() / "research_cache.db"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _init_db_sync(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        db.commit()


async def init_cache_db(db_path: Path | None = None) -> None:
    await asyncio.to_thread(_init_db_sync, db_path or default_cache_path())


def _get_cached_sync(key: str, db_path: Path) -> dict[str, Any] | None:
    _init_db_sync(db_path)
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if datetime.fromisoformat(expires_at) < _utc_now():
            db.execute("DELETE FROM cache WHERE key = ?", (key,))
            db.commit()
            return None
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}


async def get_cached(key: str, db_path: Path | None = None) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_cached_sync, key, db_path or default_cache_path())


def _set_cached_sync(key: str, value: dict[str, Any], ttl_hours: int, db_path: Path) -> None:
    _init_db_sync(db_path)
    now = _utc_now()
    expires_at = now + timedelta(hours=ttl_hours)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO cache (key, value, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                key,
                json.dumps(value, ensure_ascii=False),
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        db.commit()


async def set_cached(
    key: str,
    value: dict[str, Any],
    *,
    ttl_hours: int = 6,
    db_path: Path | None = None,
) -> None:
    await asyncio.to_thread(_set_cached_sync, key, value, ttl_hours, db_path or default_cache_path())


def _clear_expired_sync(db_path: Path) -> int:
    _init_db_sync(db_path)
    with sqlite3.connect(db_path) as db:
        cursor = db.execute("DELETE FROM cache WHERE expires_at < ?", (_utc_now().isoformat(),))
        db.commit()
        return int(cursor.rowcount or 0)


async def clear_expired(db_path: Path | None = None) -> int:
    return await asyncio.to_thread(_clear_expired_sync, db_path or default_cache_path())


def _stats_sync(db_path: Path) -> dict[str, Any]:
    _init_db_sync(db_path)
    with sqlite3.connect(db_path) as db:
        count = db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    return {
        "path": str(db_path),
        "entries": int(count),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
    }


async def cache_stats(db_path: Path | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(_stats_sync, db_path or default_cache_path())


def _clear_all_sync(db_path: Path) -> int:
    _init_db_sync(db_path)
    with sqlite3.connect(db_path) as db:
        cursor = db.execute("DELETE FROM cache")
        db.commit()
        return int(cursor.rowcount or 0)


async def clear_all(db_path: Path | None = None) -> int:
    return await asyncio.to_thread(_clear_all_sync, db_path or default_cache_path())
