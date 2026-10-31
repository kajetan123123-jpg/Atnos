from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from atnos.core.config import Settings
from atnos.db.base import Base
from atnos.db import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = Settings()
settings.ensure_directories()

configured_url = config.get_main_option("sqlalchemy.url")
if not configured_url or configured_url == "sqlite:///./atnos.db":
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url)
else:
    config.set_main_option("sqlalchemy.url", configured_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    effective_url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=effective_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
