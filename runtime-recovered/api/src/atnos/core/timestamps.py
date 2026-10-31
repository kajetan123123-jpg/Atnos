"""UTC boundaries for timestamps whose timezone SQLite does not preserve."""
from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Database-naive values use SQLite's UTC clock; aware values keep their instant."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
