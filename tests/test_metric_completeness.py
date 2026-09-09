"""Cross-engine metric completeness checks for the evaluation contract."""

from __future__ import annotations

import asyncio

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.engines.judge import JudgeConfig, PlaceholderJudge
from app.engines.ragas import RAGAS_METRIC_DEFS, RagasEngine


def test_declared_profile_metric_set_is_seven_metrics() -> None:
    assert {d.name for d in RAGAS_METRIC_DEFS} == {
        "faithfulness", "answer_relevancy", "context_precision", "context_recall",
    }
    assert {d.name for d in IntegrityEngine().specs()} == {
        "entity_consistency", "temporal_consistency", "numerical_consistency",
    }


def test_unconfigured_ragas_emits_one_structured_result_per_metric() -> None:
    record = EvaluationRecord(
        id="r1", question="q", contexts=["context"], answer="answer",
        reference_answer="reference", reference_contexts=["reference context"],
    )
    metrics = [d.name for d in RAGAS_METRIC_DEFS]
    results = asyncio.run(
        RagasEngine(judge=PlaceholderJudge(JudgeConfig())).evaluate(record, metrics, EvalParams())
    )
    assert len(results) == len(metrics)
    assert {r.metric_name for r in results} == set(metrics)
    assert all(r.score is None and r.passed is None for r in results)
    assert all(r.error and r.error["code"] == "BIZ_JUDGE_NOT_CONFIGURED" for r in results)


def test_integrity_emits_one_result_per_requested_metric() -> None:
    record = EvaluationRecord(
        id="r1", question="q", contexts=["context"], answer="2024年营收100元",
        reference_answer="2024年营收100元",
    )
    metrics = [d.name for d in IntegrityEngine().specs()]
    results = asyncio.run(IntegrityEngine().evaluate(record, metrics, EvalParams()))
    assert len(results) == len(metrics)
    assert {r.metric_name for r in results} == set(metrics)
