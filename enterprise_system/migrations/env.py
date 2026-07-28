"""Alembic environment for the independent simulated enterprise database."""

from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from enterprise_system.app import models as enterprise_models  # noqa: F401
from enterprise_system.app.persistence import EnterpriseBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = EnterpriseBase.metadata


def _database_url() -> str:
    url = (
        os.getenv("ENTERPRISE_DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if not url or not url.strip():
        raise RuntimeError("ENTERPRISE_DATABASE_URL must be configured for migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
