"""Both sides with nothing to compare are not a match.

A question that states no entity, number, or time used to score 1.0
("vacuously consistent") and enter the mean. It is not applicable.
"""

from __future__ import annotations

import asyncio

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.services.report_service import ReportService, compute_overall_score
from app.services.run_planner import build_reproducibility_meta


def _record(reference: str, answer: str) -> EvaluationRecord:
    return EvaluationRecord(
        id="r1", question="q", contexts=["c"], answer=answer, reference_answer=reference,
    )


def _run(reference: str, answer: str, metric: str):
    engine = IntegrityEngine()
    return asyncio.run(engine.evaluate(_record(reference, answer), [metric], EvalParams()))[0]


def test_both_sides_without_entities_are_not_applicable_and_excluded_from_mean() -> None:
    result = _run("这是一段没有专名的说明", "另一段也没有专名的说明", "entity_consistency")
    assert result.comparison_basis is not None
    assert result.comparison_basis.comparison_type == "not_applicable"
    assert result.score is None
    assert result.passed is None
    assert result.error is None
    score, valid, _total = compute_overall_score(
        [
            {"metric_name": "entity_consistency", "score": result.score},
            {"metric_name": "entity_consistency", "score": 0.5},
        ],
        {},
    )
    assert valid == 1
    assert score == 0.5


def test_numerical_and_temporal_on_non_applicable_text_are_not_applicable() -> None:
    numerical = _run("没有数字", "也没有数字", "numerical_consistency")
    temporal = _run("没有日期", "也没有日期", "temporal_consistency")
    for result in (numerical, temporal):
        assert result.comparison_basis is not None
        assert result.comparison_basis.comparison_type == "not_applicable"
        assert result.score is None
        assert result.error is None


def test_one_sided_empty_stays_missing_reference() -> None:
    result = _run("没有数字", "营收1亿元", "numerical_consistency")
    assert result.comparison_basis is not None
    assert result.comparison_basis.comparison_type == "missing_reference"
    assert result.score is None


def test_report_marks_entity_metric_inactive_without_alias_table() -> None:
    meta = build_reproducibility_meta(
        config_version="cfg",
        dataset_version="v1",
        merged={"metrics": {"entity_consistency": {"enabled": True}}, "alias_table": {}},
        enabled_metrics=["entity_consistency"],
    )
    assert meta["alias_table_present"] is False
    aggregated = ReportService._aggregate_metrics(
        [{
            "metric_name": "entity_consistency",
            "category": "integrity",
            "score": None,
            "threshold": None,
            "passed": None,
            "comparison_type": "not_applicable",
            "metric_version": "v",
        }],
        {},
        reproducibility_meta=meta,
    )
    entity = aggregated[0]
    assert entity["inactive_reason"] == "该指标未生效"
    assert entity["status"] == "not_applicable"


def test_historical_run_without_alias_flag_is_not_marked_inactive() -> None:
    aggregated = ReportService._aggregate_metrics(
        [{
            "metric_name": "entity_consistency",
            "category": "integrity",
            "score": 1.0,
            "threshold": None,
            "passed": None,
            "comparison_type": "match",
            "metric_version": "v",
        }],
        {},
        reproducibility_meta={},
    )
    assert "inactive_reason" not in aggregated[0]
