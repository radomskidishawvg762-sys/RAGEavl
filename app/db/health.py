from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.engine import Engine


def check_db(engine: Engine | None) -> tuple[bool, int | None]:
    """SELECT 1 with latency. Returns (healthy, latency_ms). Never raises.

    No secrets or connection strings are emitted; only a boolean and a latency number.
    """
    if engine is None:
        return False, None
    try:
        start = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, int((time.perf_counter() - start) * 1000)
    except Exception:
        return False, None
