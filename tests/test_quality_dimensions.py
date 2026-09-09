"""Phase 9 Quality Dimension Model tests."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.report_service import ReportService


def test_quality_dimensions_cover_four_domains_and_dual_home_faithfulness() -> None:
    metrics = [
        {"name": "context_precision", "category": "retrieval", "score": 0.9, "valid_count": 3, "invalid_count": 0},
        {"name": "context_recall", "category": "retrieval", "score": 0.8, "valid_count": 3, "invalid_count": 0},
        {"name": "faithfulness", "category": "generation", "score": 0.7, "valid_count": 2, "invalid_count": 1},
        {"name": "entity_consistency", "category": "integrity", "score": 1.0, "valid_count": 3, "invalid_count": 0},
        {"name": "temporal_consistency", "category": "integrity", "score": None, "valid_count": 0, "invalid_count": 3},
        {"name": "numerical_consistency", "category": "integrity", "score": 0.5, "valid_count": 2, "invalid_count": 1},
    ]
    diagnoses = [
        SimpleNamespace(
            status="diagnosed",
            related_metric="numerical_consistency",
            evidence=[{"type": "reference_evidence"}],
        )
    ]
    failures = [{"related_metric": "numerical_consistency"}]
    undetermined = [
        {"related_metric": "faithfulness"},
        {"related_metric": "temporal_consistency"},
    ]

    dimensions = ReportService._quality_dimensions(metrics, diagnoses, failures, undetermined)
    by_name = {d["dimension"]: d for d in dimensions}

    assert set(by_name) == {"retrieval", "generation", "groundedness", "correctness"}
    assert by_name["retrieval"]["metrics"] == ["context_precision", "context_recall"]
    assert by_name["retrieval"]["score"] == 0.85
    assert by_name["generation"]["score"] == 0.7
    assert by_name["groundedness"]["score"] == 0.7
    assert by_name["generation"]["undetermined_count"] == 1
    assert by_name["correctness"]["failure_count"] == 1
    assert by_name["correctness"]["diagnosed_count"] == 1
    assert by_name["correctness"]["undetermined_count"] == 1
    assert by_name["correctness"]["diagnosis_coverage"] == 1.0
    assert by_name["correctness"]["evidence_coverage"] == 1.0
    assert by_name["correctness"]["is_weakest"] is False
    assert by_name["generation"]["is_weakest"] is True
    assert by_name["groundedness"]["is_weakest"] is True
    assert by_name["retrieval"]["is_weakest"] is False


def test_quality_dimensions_without_metrics_keep_null_score() -> None:
    dimensions = ReportService._quality_dimensions([], [], [], [])
    assert len(dimensions) == 4
    assert all(d["score"] is None for d in dimensions)
    assert all(d["metric_count"] == 0 for d in dimensions)
    assert all(d["is_weakest"] is False for d in dimensions)
