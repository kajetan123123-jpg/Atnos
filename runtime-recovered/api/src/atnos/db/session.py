from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from atnos.core.config import Settings


class DatabaseManager:
    SQLITE_BUSY_TIMEOUT_S = 10.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine = None
        self._session_factory = None

    @property
    def engine(self):
        if self._engine is None:
            connect_args = {}
            sqlite_backend = self.settings.resolved_database_url.startswith("sqlite:///")
            if sqlite_backend:
                connect_args["check_same_thread"] = False
                connect_args["timeout"] = self.SQLITE_BUSY_TIMEOUT_S

            self._engine = create_engine(
                self.settings.resolved_database_url,
                future=True,
                pool_pre_ping=True,
                connect_args=connect_args,
            )
            if sqlite_backend:
                busy_timeout_ms = int(self.SQLITE_BUSY_TIMEOUT_S * 1000)

                @event.listens_for(self._engine, "connect")
                def _configure_sqlite_connection(dbapi_connection, _connection_record):
                    cursor = dbapi_connection.cursor()
                    cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
                    cursor.close()
        return self._engine

    @property
    def session_factory(self):
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
            )
        return self._session_factory

    def initialize(self) -> None:
        self.settings.ensure_directories()
        if not self.ping():
            raise RuntimeError(
                "ATNOS database ping failed during startup. "
                f"Expected SQLite file at {self.settings.sqlite_file}."
            )

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @contextmanager
    def session_scope(self):
        session: Session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception as exc:
            session.rollback()
            raise self._normalize_database_error(exc, context="committing a database session")
        finally:
            session.close()

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

    def _normalize_database_error(self, exc: Exception, *, context: str) -> Exception:
        if not self.settings.resolved_database_url.startswith("sqlite:///"):
            return exc
        if isinstance(exc, OperationalError):
            detail = str(exc).lower()
            if "database is locked" in detail or "database schema is locked" in detail:
                return RuntimeError(
                    "SQLite database lock detected while "
                    f"{context}. Database file: {self.settings.sqlite_file}. "
                    "Another Atnos process may still be holding the file lock."
                )
        return exc
