"""A metric-level error finishes the run as completed_with_errors."""

from __future__ import annotations

import asyncio

from app.diagnosis import DiagnosisEngine
from app.domain.schemas import MetricResult
from app.engines.base import EvalParams
from app.runner.local import LocalAsyncRunner
from app.services.evaluation_service import EvaluationService
from tests.test_evaluation_service import (
    _make_service,
    _record_row,
)


class _MixedMetricEngine:
    name = "mixed"

    def metric_names(self):
        return ["faithfulness", "context_precision"]

    async def evaluate(self, record, metrics, params):
        return [
            MetricResult(
                record_id=record.id, metric_name="faithfulness", category="generation",
                score=1.0, threshold=None, metric_version="t",
            ),
            MetricResult(
                record_id=record.id, metric_name="context_precision", category="retrieval",
                score=None, threshold=None, metric_version="t",
                error={"code": "SYS_METRIC_ERROR", "message": "boom"},
            ),
        ]


def test_runner_counts_metric_errors_and_marks_completed_with_errors() -> None:
    record = __import__("app.domain.schemas", fromlist=["EvaluationRecord"]).EvaluationRecord(
        id="r1", question="q", contexts=["c"], answer="a", reference_answer="ref",
    )
    summary = asyncio.run(LocalAsyncRunner().run(
        [record], [_MixedMetricEngine()], ["faithfulness", "context_precision"], EvalParams(),
    ))
    assert summary.metric_errors == 1
    assert summary.status == "completed_with_errors"


def test_service_persists_metric_error_count_and_completed_with_errors() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "a", "ref")])
    run = svc.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1",
        reproducibility_meta={"config_version": "x"},
    )
    summary = asyncio.run(svc.execute_run(
        run.id,
        engines=[_MixedMetricEngine()],
        enabled_metrics=["faithfulness", "context_precision"],
        params=EvalParams(),
        diagnosis_engine=DiagnosisEngine(),
    ))
    assert summary["status"] == "completed_with_errors"
    assert repo.runs[run.id]["status"] == "completed_with_errors"
    assert repo.runs[run.id]["error_summary"]["metric_errors"] == 1
    assert isinstance(svc, EvaluationService)
