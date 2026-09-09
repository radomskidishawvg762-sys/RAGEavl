from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  — register all models on Base.metadata
from alembic import context
from app.core.config import settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target: RAGEval business models only. Supabase platform schemas
# (auth/storage/realtime/supabase_*) are never included in migrations.
target_metadata = Base.metadata


def _resolve_url() -> str:
    url = settings.alembic_url()  # ALEMBIC_DATABASE_URL -> falls back to DATABASE_URL (Direct)
    if not url:
        raise RuntimeError(
            "No Alembic URL: set ALEMBIC_DATABASE_URL or DATABASE_URL (Direct connection)."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {}) or {}
    cfg["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
