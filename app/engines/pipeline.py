from __future__ import annotations

from collections.abc import Iterable

from app.domain.schemas import EvaluationRecord, MetricResult
from app.engines.base import EvalParams, EvaluationEngine


async def run_record(
    engines: list[EvaluationEngine],
    record: EvaluationRecord,
    enabled_metrics: Iterable[str],
    params: EvalParams,
) -> list[MetricResult]:
    """Run one record through all engines; merge results.

    Each engine is asked only for the metrics it owns that are enabled in the Profile.
    No engine-identity conditionals anywhere (ADR-03) — dispatch is structural.
    """
    enabled = set(enabled_metrics)
    results: list[MetricResult] = []
    for engine in engines:
        metrics = [m for m in engine.metric_names() if m in enabled]
        if not metrics:
            continue
        results.extend(await engine.evaluate(record, metrics, params))
    return results
