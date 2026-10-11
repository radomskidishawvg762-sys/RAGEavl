"""Profile tolerance must reach the integrity engine and the stored basis."""

from __future__ import annotations

import asyncio

from app.domain.schemas import EvaluationRecord
from app.engines.integrity import IntegrityEngine
from app.metrics.bootstrap import bootstrap_metrics
from app.metrics.integrity.numerical import (
    DEFAULT_ABSOLUTE_TOLERANCE,
    DEFAULT_RELATIVE_TOLERANCE,
)
from app.services.run_planner import resolve_profile

bootstrap_metrics()


def test_resolve_profile_passes_tolerance_into_eval_params() -> None:
    merged = {
        "metrics": {"numerical_consistency": {"enabled": True}},
        "tolerance": {"absolute": 0.01, "relative": 0.05},
    }
    plan = resolve_profile(merged)
    assert plan.params.extra["tolerance"] == {"absolute": 0.01, "relative": 0.05}


def test_tolerance_applied_records_profile_values_not_null() -> None:
    merged = {
        "metrics": {"numerical_consistency": {"enabled": True}},
        "tolerance": {"absolute": 0.01, "relative": 0.05},
    }
    plan = resolve_profile(merged)
    record = EvaluationRecord(
        id="r1", question="q", contexts=["c"],
        answer="营收1.00亿元", reference_answer="营收1.00亿元",
    )
    result = asyncio.run(
        IntegrityEngine().evaluate(record, ["numerical_consistency"], plan.params)
    )[0]
    assert result.comparison_basis is not None
    assert result.comparison_basis.tolerance_applied == {"absolute": 0.01, "relative": 0.05}


def test_tolerance_applied_records_math_defaults_when_profile_omits_tolerance() -> None:
    plan = resolve_profile({"metrics": {"numerical_consistency": {"enabled": True}}})
    record = EvaluationRecord(
        id="r1", question="q", contexts=["c"],
        answer="营收1亿元", reference_answer="营收1亿元",
    )
    result = asyncio.run(
        IntegrityEngine().evaluate(record, ["numerical_consistency"], plan.params)
    )[0]
    assert result.comparison_basis is not None
    assert result.comparison_basis.tolerance_applied == {
        "absolute": DEFAULT_ABSOLUTE_TOLERANCE,
        "relative": DEFAULT_RELATIVE_TOLERANCE,
    }
