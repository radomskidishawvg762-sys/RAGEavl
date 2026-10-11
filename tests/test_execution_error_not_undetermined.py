"""An execution error is not also stored as an undetermined diagnosis."""

from __future__ import annotations

import asyncio

from app.diagnosis import DiagnosisEngine, FailureClassifier
from app.domain.schemas import EvaluationRecord, MetricResult
from app.engines.base import EvalParams
from app.services.evaluation_service import EvaluationService
from tests.test_evaluation_service import _make_service, _record_row


def _error_result(record_id: str) -> MetricResult:
    return MetricResult(
        record_id=record_id,
        metric_name="faithfulness",
        category="generation",
        score=None,
        threshold=None,
        metric_version="t",
        error={"code": "SYS_METRIC_ERROR", "message": "boom"},
    )


class _ErrorOnlyEngine:
    name = "err"

    def metric_names(self):
        return ["faithfulness"]

    async def evaluate(self, record, metrics, params):
        return [_error_result(record.id)]


def test_classifier_and_engine_do_not_call_execution_errors_undetermined() -> None:
    record = EvaluationRecord(
        id="r1", question="q", contexts=["c"], answer="a", reference_answer="ref",
    )
    result = _error_result("r1")
    assert FailureClassifier().classify(result).status != "undetermined"
    outcome = DiagnosisEngine().diagnose(record, result)
    assert outcome.status != "undetermined"
    assert outcome.status == "not_failed"


def test_service_does_not_persist_undetermined_for_execution_error() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "a", "ref")])
    run = svc.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1",
        reproducibility_meta={"config_version": "x"},
    )
    asyncio.run(svc.execute_run(
        run.id,
        engines=[_ErrorOnlyEngine()],
        enabled_metrics=["faithfulness"],
        params=EvalParams(),
        diagnosis_engine=DiagnosisEngine(),
    ))
    assert repo.diagnoses == []
    assert repo.metric_results[0]["error"]["code"] == "SYS_METRIC_ERROR"
    assert isinstance(svc, EvaluationService)
