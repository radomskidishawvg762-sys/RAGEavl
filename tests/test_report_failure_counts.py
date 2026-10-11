"""Failure counts follow the classifier, not only rows that already have a diagnosis."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.report_service import ReportService


def _metric(name: str, **overrides):
    row = {
        "name": name,
        "category": "integrity",
        "score": 0.5,
        "valid_count": 2,
        "invalid_count": 0,
    }
    row.update(overrides)
    return row


def test_diagnosis_coverage_drops_when_a_classifier_failure_was_not_diagnosed() -> None:
    metrics = [_metric("numerical_consistency")]
    diagnoses = [
        SimpleNamespace(
            status="diagnosed",
            related_metric="numerical_consistency",
            evidence=[{"type": "reference_evidence"}],
        )
    ]
    metric_rows = [
        {
            "metric_name": "numerical_consistency",
            "category": "integrity",
            "score": 0.0,
            "threshold": None,
            "comparison_type": "value_mismatch",
            "error": None,
            "metric_version": "v",
            "record_id": "r1",
        },
        {
            "metric_name": "numerical_consistency",
            "category": "integrity",
            "score": 0.2,
            "threshold": 0.5,
            "comparison_type": None,
            "error": None,
            "metric_version": "v",
            "record_id": "r2",
        },
    ]
    dimensions = ReportService._quality_dimensions(
        metrics, diagnoses, failures=[], undetermined=[], metric_rows=metric_rows,
    )
    correctness = next(d for d in dimensions if d["dimension"] == "correctness")
    assert correctness["failure_count"] == 2
    assert correctness["diagnosed_count"] == 1
    assert correctness["diagnosis_coverage"] == 0.5
