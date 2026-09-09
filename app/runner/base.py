from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.domain.schemas import EvaluationRecord, MetricResult, RunStatus
from app.engines.base import EvalParams, EvaluationEngine

ProgressCallback = Callable[[int, int, int], "Awaitable[None] | None"]
# args: (evaluated, errors, total) — runner-internal counts (exception-level)

RecordDoneCallback = Callable[
    ["str", "list[MetricResult] | Exception"], "Awaitable[None] | None"
]
# args: (record_id, results | exception) — per-record delivery for the
# EvaluationService persistence boundary (Pre-T13 C1). A raised exception means
# the whole record failed to evaluate (FR-22 isolation already applied).

CancelCheck = Callable[[], bool]


class RunSummary(BaseModel):
    total: int
    evaluated: int
    errors: int
    coverage: float
    status: RunStatus
    cancelled: bool = False  # Pre-T13 C1: set when is_cancelled() fired mid-run


@runtime_checkable
class EvaluationRunner(Protocol):
    async def run(
        self,
        records: list[EvaluationRecord],
        engines: list[EvaluationEngine],
        enabled_metrics: list[str],
        params: EvalParams,
        on_progress: ProgressCallback | None = None,
        on_record_done: RecordDoneCallback | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> RunSummary: ...
