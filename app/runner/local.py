from __future__ import annotations

import asyncio

from app.core.config import settings
from app.domain.schemas import EvaluationRecord, RunStatus
from app.engines import pipeline
from app.engines.base import EvalParams, EvaluationEngine
from app.runner.base import CancelCheck, ProgressCallback, RecordDoneCallback, RunSummary


class LocalAsyncRunner:
    """MVP in-process runner (ADR-02 / ADR-09). asyncio + Semaphore.

    Per-record error isolation (FR-22): a single record's failure is caught and
    delivered to `on_record_done` as an exception — the batch continues, no
    global failure-rate threshold (PRD Q6). Results are delivered per record to
    `on_record_done` so the EvaluationService persistence boundary never
    re-implements the execution loop (Pre-T13 C1).
    """

    def __init__(self, concurrency: int | None = None) -> None:
        self._sem = asyncio.Semaphore(
            concurrency or settings.rageval_judge_concurrency
        )

    async def run(
        self,
        records: list[EvaluationRecord],
        engines: list[EvaluationEngine],
        enabled_metrics: list[str],
        params: EvalParams,
        on_progress: ProgressCallback | None = None,
        on_record_done: RecordDoneCallback | None = None,
        is_cancelled: CancelCheck | None = None,
        resolve_record=None,
    ) -> RunSummary:
        """`resolve_record` (T-14B): optional per-record hook —
        `async (record_id) -> EvaluationRecord | Exception`, called INSIDE the
        semaphore so RAG-input fetching obeys the SAME concurrency bound as
        evaluation (no second concurrency mechanism). An exception here is
        delivered to on_record_done as a record error (FR-22 isolation); the
        record never enters metric evaluation with fabricated content."""
        total = len(records)
        evaluated = 0
        errors = 0
        cancelled = False

        async def _one(record: EvaluationRecord) -> None:
            nonlocal evaluated, errors, cancelled
            async with self._sem:
                # cancellation is checked AFTER acquiring the semaphore so
                # queued tasks observe the flag when they actually start
                if is_cancelled is not None and is_cancelled():
                    cancelled = True
                    return  # not evaluated, not errored — run ends as cancelled
                try:
                    if resolve_record is not None:
                        record = await resolve_record(record.id)
                        if isinstance(record, Exception):
                            raise record
                    results = await pipeline.run_record(
                        engines, record, enabled_metrics, params
                    )
                    evaluated += 1
                    done = results
                except Exception as e:  # noqa: BLE001 — FR-22 isolation
                    errors += 1
                    done = e
            if on_record_done is not None:
                cb = on_record_done(record.id, done)
                if asyncio.iscoroutine(cb):
                    await cb
            if on_progress is not None:
                cb = on_progress(evaluated, errors, total)
                if asyncio.iscoroutine(cb):
                    await cb

        await asyncio.gather(*(_one(r) for r in records))

        status = self._determine_status(total, evaluated, errors)
        coverage = (evaluated / total) if total else 0.0
        return RunSummary(
            total=total,
            evaluated=evaluated,
            errors=errors,
            coverage=coverage,
            status=status,
            cancelled=cancelled,
        )

    @staticmethod
    def _determine_status(total: int, evaluated: int, errors: int) -> RunStatus:
        # No percentage thresholds (PRD Q6).
        if total == 0:
            return "completed"
        if evaluated == 0:
            return "failed"
        if errors == 0 and evaluated == total:
            return "completed"
        return "completed_with_errors"
