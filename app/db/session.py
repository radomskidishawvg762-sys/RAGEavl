from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Lazily constructed — app boots without DATABASE_URL (constraint: no DB needed to start).
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_size=settings.rageval_db_pool_size,  # 5 (constraint 8)
        max_overflow=settings.rageval_db_max_overflow,  # 5
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def get_engine() -> Engine | None:
    """Return the application ORM engine, or None if DATABASE_URL is unset."""
    global _engine, _SessionLocal
    if _engine is None:
        url = settings.db_url()
        if url:
            _engine = _build_engine(url)
            _SessionLocal = sessionmaker(
                bind=_engine, autoflush=False, expire_on_commit=False, future=True
            )
    return _engine


def reset_engine() -> None:
    """Test helper: drop the cached engine so a new URL takes effect."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


def get_session() -> Iterator[Session]:
    eng = get_engine()
    if eng is None or _SessionLocal is None:
        raise RuntimeError("DATABASE_URL not configured")
    with _SessionLocal() as session:
        yield session


@contextmanager
def open_session() -> Iterator[Session]:
    """Session for BACKGROUND work (T-13 async submission): owns its own
    session/transaction and closes it on exit — never tied to a request scope.
    Raises RuntimeError when DATABASE_URL is unset (tests override the launcher
    with fake repositories instead)."""
    eng = get_engine()
    if eng is None or _SessionLocal is None:
        raise RuntimeError("DATABASE_URL not configured")
    with _SessionLocal() as session:
        yield session
