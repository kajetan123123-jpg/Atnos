from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config

    from atnos.core.config import Settings


def resolve_api_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_alembic_config(settings: "Settings") -> "Config":
    from alembic.config import Config

    api_root = resolve_api_root()
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url)
    return config


def upgrade_database(settings: "Settings", revision: str = "head") -> None:
    if revision == "head" and _sqlite_database_at_head(settings):
        return

    from alembic import command

    command.upgrade(build_alembic_config(settings), revision)


def _sqlite_database_at_head(settings: "Settings") -> bool:
    if not settings.resolved_database_url.startswith("sqlite:///"):
        return False

    sqlite_file = settings.sqlite_file
    if not sqlite_file.exists():
        return False

    head_revision = _resolve_single_head_revision(resolve_api_root() / "alembic" / "versions")
    if head_revision is None:
        return False

    connection = None
    try:
        connection = sqlite3.connect(sqlite_file)
        row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()

    return bool(row and row[0] == head_revision)


@lru_cache(maxsize=8)
def _resolve_single_head_revision(versions_dir: Path) -> str | None:
    revisions: set[str] = set()
    down_revisions: set[str] = set()

    for path in versions_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision_match = re.search(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", text, flags=re.MULTILINE)
        if revision_match is None:
            continue
        revision = revision_match.group(1)
        revisions.add(revision)

        down_match = re.search(r"^down_revision\s*=\s*(.+)$", text, flags=re.MULTILINE)
        if down_match is None:
            continue
        raw_down = down_match.group(1).strip()
        if raw_down in {"None", "null"}:
            continue
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", raw_down)
        down_revisions.update(quoted)

    heads = revisions.difference(down_revisions)
    if len(heads) != 1:
        return None
    return next(iter(heads))
