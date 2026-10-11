"""Serve the app on :8001 against the dedicated migrated test database only.

Used by frontend/scripts/workflow-e2e.mjs. Never targets the development DB,
resets data or calls migrations. Do not run concurrently with PostgreSQL tests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
values = {**dotenv_values(ROOT / ".env.local"), **dotenv_values(ROOT / ".env.test"), **os.environ}
test_url = values.get("TEST_DATABASE_URL")
dev_url = values.get("DATABASE_URL")
if not test_url or not dev_url:
    raise SystemExit("DATABASE_URL and a dedicated TEST_DATABASE_URL are required")
target, source = make_url(test_url), make_url(dev_url)
if (target.host, target.port, target.database) == (source.host, source.port, source.database):
    raise SystemExit("Refusing to use the development database")
if "test" not in (target.database or "").lower():
    raise SystemExit("Review server requires an explicitly named test database")
os.environ.update(DATABASE_URL=test_url, RAGEVAL_ENV="test")
if "--with-judge" in sys.argv:
    # Explicit opt-in for the small live-model audit. Never used by routine UI tests.
    os.environ["RAGEVAL_JUDGE_CONCURRENCY"] = "2"
else:
    os.environ.update(JUDGE_API_KEY="", JUDGE_PROVIDER="", JUDGE_MODEL="", JUDGE_BASE_URL="")

from app.main import app  # noqa: E402


@app.middleware("http")
async def mark_isolated_database(request, call_next):
    response = await call_next(request)
    response.headers["X-Rageval-Review"] = "isolated-test-database"
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)
