from __future__ import annotations

from urllib.parse import urlparse

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _port_of(url: str) -> int | None:
    """Extract port from a SQLAlchemy URL (postgresql+psycopg://user:pass@host:port/db)."""
    return urlparse(url).port


class Settings(BaseSettings):
    """Application settings. Secrets are SecretStr and never logged.

    Connection-mode rules (Supabase Cloud architecture, constraints 5/6):
      - DATABASE_URL (app ORM): Direct connection preferred (IPv6 when available);
        otherwise Session Pooler (Supavisor, port 5432 on pooler host).
        Transaction Pooler (port 6543) is NOT permitted as primary ORM connection.
      - ALEMBIC_DATABASE_URL: must be Direct connection; falls back to DATABASE_URL.
      - TEST_DATABASE_URL: must differ from DATABASE_URL (enforced in conftest.py).
    """

    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env.test"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database (Supabase Cloud hosted PostgreSQL 17) ---
    database_url: SecretStr | None = None
    alembic_database_url: SecretStr | None = None
    test_database_url: SecretStr | None = None

    # --- Environment ---
    rageval_env: str = "dev"  # dev | test | demo

    # --- Judge LLM (M2 placeholder; M1 does not call) ---
    judge_provider: str | None = None
    judge_model: str | None = None
    judge_model_version: str | None = None
    judge_base_url: str | None = None
    judge_api_key: SecretStr | None = None

    # --- Runtime tunables (engineering defaults, constraint 8) ---
    rageval_judge_concurrency: int = 8
    rageval_db_pool_size: int = 5
    rageval_db_max_overflow: int = 5

    @field_validator("database_url")
    @classmethod
    def _no_transaction_pooler_for_orm(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return v
        url = v.get_secret_value()
        if _port_of(url) == 6543:
            raise ValueError(
                "DATABASE_URL targets port 6543 (Supabase Transaction Pooler). "
                "Transaction Pooler must NOT be the primary SQLAlchemy ORM connection. "
                "Use Direct (port 5432 on db.<project>.supabase.co, IPv6 preferred) "
                "or Session Pooler (port 5432 on the pooler host)."
            )
        return v

    def db_url(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url else None

    def alembic_url(self) -> str | None:
        """Direct connection for migrations. Falls back to DATABASE_URL."""
        if self.alembic_database_url is not None:
            return self.alembic_database_url.get_secret_value()
        return self.db_url()


settings = Settings()  # type: ignore[call-arg]
