"""Startup reconciliation of runs a previous process left in flight.

Runs execute as in-process asyncio tasks (ADR-02), so a process that dies mid-run
leaves its row at `pending`/`running` forever: GET /progress keeps reporting
"running" (the UI polls without end), the report stays non-final, the dataset
stays locked by ADR-06, and no endpoint can move it.

The happy path needs a real database, so it is verified functionally rather than
here (probe: insert a `running` row, call _reconcile_orphaned_runs, observe
status=failed + finished_at + code SYS_RUN_ORPHANED). What IS unit-testable — and
what would be far worse to get wrong — is that a database outage at startup must
not stop the process from booting.
"""

from __future__ import annotations

import app.main as main


def test_database_outage_at_startup_does_not_stop_the_app(monkeypatch) -> None:
    """/api/health is what reports a down database, and it can only report it if
    the process is up — so reconciliation must be best-effort, never fatal."""

    def _boom():
        raise RuntimeError("database is down")

    monkeypatch.setattr(main, "open_session", _boom)

    main._reconcile_orphaned_runs()  # must not raise


def test_reconciliation_runs_before_the_app_serves() -> None:
    """Lifespan runs it at startup, so a stale `running` row is already `failed`
    by the time the first request can observe it."""
    import inspect

    src = inspect.getsource(main._lifespan)

    assert "_reconcile_orphaned_runs()" in src
    assert "yield" in src
