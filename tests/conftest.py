from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# pydantic-settings env_file=(".env.local", ".env.test") — the LAST file wins,
# so resolution precedence is: os.environ > .env.test > .env.local.
_ENV_FILE_PRECEDENCE = (".env.test", ".env.local")

# The 10 Alembic-managed business tables (Spec §5.2 / README).
_TEN_BUSINESS_TABLES = {
    "projects", "datasets", "dataset_records", "evaluation_configs",
    "evaluation_runs", "evaluation_results", "metric_results",
    "diagnoses", "recommendations", "metric_definitions",
}

# Static SQL (no user input): single statement + CASCADE so FK order is
# irrelevant. Never touches alembic_version or Supabase platform schemas.
_TRUNCATE_SQL = (
    "TRUNCATE TABLE projects, datasets, dataset_records, evaluation_configs, "
    "evaluation_runs, evaluation_results, metric_results, diagnoses, "
    "recommendations, metric_definitions CASCADE"
)


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ")
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _resolve_env(name: str) -> str | None:
    """os.environ wins; then .env.test; then .env.local. Empty means unset."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    for file_name in _ENV_FILE_PRECEDENCE:
        value = _load_env_file(_PROJECT_ROOT / file_name).get(name, "").strip()
        if value:
            return value
    return None


def pytest_configure(config: pytest.Config) -> None:
    """Hard isolation: TEST_DATABASE_URL must differ from DATABASE_URL.

    Both values resolve from os.environ, then .env.test, then .env.local —
    matching Settings precedence. When both are set and equal the run aborts:
    destructive integration tests must never target the production database.
    """
    db = _resolve_env("DATABASE_URL")
    test_db = _resolve_env("TEST_DATABASE_URL")
    if db and test_db and db == test_db:
        raise pytest.UsageError(
            "TEST_DATABASE_URL must differ from DATABASE_URL "
            "(hard isolation requirement, NFR Level A)."
        )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Dedicated test PostgreSQL URL.

    Missing -> explicit skip (integration tests are NEVER counted as passed).
    Equal to DATABASE_URL -> hard UsageError (belt & braces on top of
    pytest_configure, which already aborts the run for that case).
    """
    url = _resolve_env("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not configured — PostgreSQL integration tests "
            "skipped. Set it in .env.test (dedicated test database, must "
            "differ from DATABASE_URL); see README '集成测试'."
        )
    db_url = _resolve_env("DATABASE_URL")
    if db_url and url == db_url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL must differ from DATABASE_URL "
            "(hard isolation requirement, NFR Level A)."
        )
    return url


@pytest.fixture(scope="session")
def pg_engine(test_database_url):
    """Session-wide engine over the DEDICATED test database.

    Unreachable -> honest skip. Missing tables -> one 'alembic upgrade head'
    attempt against TEST_DATABASE_URL (Alembic is the ONLY migration system —
    never create_all); still missing -> loud failure, not a skip.
    """
    from sqlalchemy import create_engine, inspect

    engine = create_engine(
        test_database_url, connect_args={"connect_timeout": 10}, pool_pre_ping=True
    )
    try:
        present = set(inspect(engine).get_table_names())
    except Exception as exc:  # unreachable test database
        engine.dispose()
        pytest.skip(f"TEST_DATABASE_URL unreachable: {type(exc).__name__}")

    if not _TEN_BUSINESS_TABLES.issubset(present):
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_PROJECT_ROOT,
            env={**os.environ, "ALEMBIC_DATABASE_URL": test_database_url},
            capture_output=True,
            text=True,
            timeout=120,
        )
        present = set(inspect(engine).get_table_names())
        if not _TEN_BUSINESS_TABLES.issubset(present):
            engine.dispose()
            pytest.fail(
                "test database not migrated and 'alembic upgrade head' failed\n"
                f"exit={proc.returncode}\nstdout: {proc.stdout[-2000:]}\n"
                f"stderr: {proc.stderr[-2000:]}"
            )
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_db(pg_engine):
    """Per-test session factory with full TRUNCATE isolation (before + after).

    Yields a sessionmaker; repositories take their own Session from it, so
    tests exercise the real commit/rollback semantics of the data layer.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(
        bind=pg_engine, autoflush=False, expire_on_commit=False, future=True
    )
    with pg_engine.begin() as conn:
        conn.execute(text(_TRUNCATE_SQL))
    yield factory
    with pg_engine.begin() as conn:
        conn.execute(text(_TRUNCATE_SQL))


def _skip_causes(reports) -> str:
    """Distinct skip causes, deduped, taken from the reports themselves.

    The two causes are NOT interchangeable: a test database that is configured
    but down must never read as an unconfigured one. Reporting the reports'
    own wording keeps that distinction visible instead of collapsing both into
    a fixed two-way guess.
    """
    causes: list[str] = []
    for report in reports:
        longrepr = getattr(report, "longrepr", None)
        text = str(longrepr[2]) if isinstance(longrepr, tuple) and len(longrepr) >= 3 else str(longrepr or "")
        line = text.strip().splitlines()[0].strip() if text.strip() else ""
        if not line:
            continue
        # Both messages are "TEST_DATABASE_URL <cause> — <remediation prose>";
        # keep the identifying head, drop the shared tail.
        head = line.split(" — ")[0].strip()
        if head.startswith("Skipped: "):  # pytest's own prefix, redundant here
            head = head[len("Skipped: ") :].strip()
        if head and head not in causes:
            causes.append(head)
    return "; ".join(causes)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Mandated integration-test visibility: configured / executed / passed.

    Skipped integration tests are reported as skipped — never as passed.
    """
    stats = terminalreporter.stats
    passed = [r for r in stats.get("passed", []) if "postgres" in r.keywords]
    skipped = [r for r in stats.get("skipped", []) if "postgres" in r.keywords]
    errored = [r for r in stats.get("error", []) if "postgres" in r.keywords]
    if not passed and not skipped and not errored:
        return
    terminalreporter.section("PostgreSQL integration tests", sep="=", bold=True)
    if _resolve_env("TEST_DATABASE_URL"):
        terminalreporter.write_line("TEST_DATABASE_URL configured")
    if errored:
        terminalreporter.write_line(f"Integration tests ERRORED: {len(errored)}")
    if skipped:
        causes = _skip_causes(skipped)
        detail = f" ({causes})" if causes else ""
        terminalreporter.write_line(
            f"Integration tests skipped: {len(skipped)}{detail} — NOT counted as passed"
        )
    if passed:
        terminalreporter.write_line(f"Integration tests executed: {len(passed)}")
        terminalreporter.write_line(f"Integration tests passed: {len(passed)}")
