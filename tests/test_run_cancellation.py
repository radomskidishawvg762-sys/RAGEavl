"""Cancellation must be observable from the executor's own session.

Regression: ``is_run_cancelled()`` read the run through ``get_run()``, a
``Session.get()`` served from the identity map of a session built with
``expire_on_commit=False``. A cancel committed by the request's session was
therefore invisible to the executor, which evaluated every remaining record and
then persisted full coverage onto a row that already said "cancelled".

The existing cancellation tests inject an ``is_cancelled`` callable directly, so
they bypass ``EvaluationService.is_run_cancelled`` entirely and never exercised
this path.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.services.evaluation_service import EvaluationService


def test_identity_map_hides_another_sessions_commit(tmp_path) -> None:
    """Pins the mechanism the fix relies on: under expire_on_commit=False a
    Session.get() keeps serving the loaded status, while a column query issued on
    the same session sees the other session's committed write.

    The database is a FILE, not ``sqlite://``: an in-memory SQLite engine pools to
    SingletonThreadPool, so both sessions would share one connection and the
    staleness would not reproduce. A file gives each session its own connection,
    which is what the real PostgreSQL pool does.
    """

    class _Base(DeclarativeBase):
        pass

    class _Row(_Base):
        __tablename__ = "identity_map_probe"
        id: Mapped[str] = mapped_column(primary_key=True)
        status: Mapped[str] = mapped_column()

    engine = create_engine(f"sqlite:///{tmp_path}/identity_map_probe.db")
    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    with factory() as seed:
        seed.add(_Row(id="r1", status="running"))
        seed.commit()

    with factory() as executor, factory() as canceller:
        # Hold a strong reference: the identity map keeps only weak ones, so a
        # discarded object is collected and the next get() would re-query and look
        # fresh. execute_run() holds the run in a live local, which is exactly the
        # situation that makes the stale read happen in the app.
        cached = executor.get(_Row, "r1")
        assert cached.status == "running"

        canceller.execute(text("UPDATE identity_map_probe SET status='cancelled' WHERE id='r1'"))
        canceller.commit()

        assert executor.get(_Row, "r1").status == "running"  # stale: identity map
        assert cached.status == "running"  # the held object never refreshes
        assert (  # fresh: column read
            executor.execute(select(_Row.status).where(_Row.id == "r1")).scalar_one()
            == "cancelled"
        )


class _DivergentRepo:
    """Mimics the real repository's semantics across the two read paths:
    ``get_run()`` serves the cached copy, ``get_run_status()`` the committed truth."""

    def __init__(self, *, cached_status: str, persisted_status: str) -> None:
        self._cached = cached_status
        self._persisted = persisted_status
        self.status_reads = 0

    def get_run(self, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(status=self._cached)

    def get_run_status(self, run_id: str) -> str:
        self.status_reads += 1
        return self._persisted


def test_is_run_cancelled_reads_the_fresh_status_not_the_cached_run() -> None:
    """Fails if the check is reverted to the cached ``get_run()`` path."""
    repo = _DivergentRepo(cached_status="running", persisted_status="cancelled")
    svc = EvaluationService(repo)  # type: ignore[arg-type]

    assert svc.is_run_cancelled("run-1") is True
    assert repo.status_reads == 1


def test_is_run_cancelled_is_false_while_the_run_is_not_cancelled() -> None:
    repo = _DivergentRepo(cached_status="running", persisted_status="running")
    svc = EvaluationService(repo)  # type: ignore[arg-type]

    assert svc.is_run_cancelled("run-1") is False
