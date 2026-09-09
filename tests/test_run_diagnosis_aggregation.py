"""Run-level diagnosis aggregation tests (Post-MVP Phase 4).

Chain invariant under test: run-level buckets can only exist because
persisted sample-level diagnoses exist (Failure -> Evidence -> Diagnosis ->
aggregate). The aggregator is consumption-only — no Engine/Judge re-run,
no score-only inference; evidence-insufficient samples stay undetermined.
"""

from __future__ import annotations

import pytest

from app.diagnosis import DiagnosisEngine, aggregate_run_diagnoses
from app.models import Diagnosis
from tests.test_evaluation_service import FakeEvaluationRepo
from tests.test_report_aggregation import _add_record, _make_run, _report

MAPPING = {"res1": "rec1", "res2": "rec2", "res3": "rec3"}

EVIDENCE = [
    {"type": "reference_evidence", "source": "reference_answer",
     "locator": "record.reference_answer", "content": "1亿元", "metadata": None},
]


def _d(
    *,
    run_id: str = "run1",
    status: str = "diagnosed",
    failure_type: str | None = "integrity.numerical_mismatch",
    metric: str = "numerical_consistency",
    severity: str = "CRITICAL",
    evidence: list | None = None,
    result_id: str = "res1",
) -> Diagnosis:
    return Diagnosis(
        run_id=run_id, result_id=result_id, status=status,
        failure_type=failure_type, related_metric=metric,
        root_cause="rc" if status == "diagnosed" else None,
        severity=severity,
        evidence=evidence if evidence is not None else [],
        evidence_contract=f"{failure_type}.v1" if (status == "diagnosed" and failure_type) else "",
        confidence="high" if status == "diagnosed" else "low",
    )


# ---------------- pure aggregation ----------------


def test_buckets_sorted_by_count_desc_with_ratio() -> None:
    rows = [
        _d(result_id="res1"),
        _d(result_id="res2"),
        _d(result_id="res1"),
        _d(failure_type="integrity.temporal_mismatch", metric="temporal_consistency",
           severity="ERROR", result_id="res3"),
        _d(status="undetermined", failure_type=None, metric="temporal_consistency",
           severity="INFO", result_id="res3"),
    ]
    s = aggregate_run_diagnoses(rows, MAPPING)
    assert s.available is True
    assert s.total_diagnoses == 5
    assert s.total_failure_records == 3
    assert s.undetermined_count == 1

    top = s.buckets[0]
    assert top.failure_type == "integrity.numerical_mismatch"
    assert top.status == "diagnosed"
    assert top.count == 3
    assert top.ratio == pytest.approx(3 / 5)
    assert top.related_metrics == ["numerical_consistency"]
    # count desc, then diagnosed before undetermined, then failure_type None last
    assert [(b.count, b.status, b.failure_type) for b in s.buckets] == [
        (3, "diagnosed", "integrity.numerical_mismatch"),
        (1, "diagnosed", "integrity.temporal_mismatch"),
        (1, "undetermined", None),
    ]


def test_severity_is_bucket_max() -> None:
    rows = [
        _d(severity="WARNING", result_id="res1"),
        _d(severity="ERROR", result_id="res2"),
        _d(severity="CRITICAL", result_id="res3"),
    ]
    (bucket,) = aggregate_run_diagnoses(rows, MAPPING).buckets
    assert bucket.severity == "CRITICAL"


def test_affected_records_counts_distinct_samples() -> None:
    rows = [
        _d(result_id="res1"),
        _d(result_id="res1"),                       # same sample, second metric row
        _d(result_id="res2"),
    ]
    mapping = {"res1": "rec1", "res2": "rec1"}      # two results -> one record
    (bucket,) = aggregate_run_diagnoses(rows, mapping).buckets
    assert bucket.count == 3
    assert bucket.affected_records == 1


def test_evidence_available_requires_every_row_to_carry_evidence() -> None:
    all_with = [_d(evidence=EVIDENCE, result_id="res1"), _d(evidence=EVIDENCE, result_id="res2")]
    (bucket,) = aggregate_run_diagnoses(all_with, MAPPING).buckets
    assert bucket.evidence_available is True

    mixed = [_d(evidence=EVIDENCE, result_id="res1"), _d(result_id="res2")]
    (bucket,) = aggregate_run_diagnoses(mixed, MAPPING).buckets
    assert bucket.evidence_available is False


def test_undetermined_bucket_keeps_candidate_never_attributed() -> None:
    rows = [
        _d(status="undetermined", failure_type="integrity.numerical_mismatch",
           severity="INFO", result_id="res1", evidence=EVIDENCE),  # evidence ignored
        _d(status="undetermined", failure_type=None, metric="temporal_consistency",
           severity="INFO", result_id="res2"),
    ]
    s = aggregate_run_diagnoses(rows, MAPPING)
    assert s.undetermined_count == 2
    candidate, plain = s.buckets  # candidate-code bucket sorts before the None one
    assert candidate.status == "undetermined"
    assert candidate.failure_type == "integrity.numerical_mismatch"
    assert candidate.evidence_available is False  # never attributed, contract unmet
    assert plain.failure_type is None


def test_empty_diagnoses_report_not_available() -> None:
    s = aggregate_run_diagnoses([], MAPPING)
    assert s.available is False
    assert s.total_diagnoses == 0 and s.buckets == []


def test_engine_delegate_matches_pure_aggregator() -> None:
    rows = [_d(result_id="res1"), _d(result_id="res2")]
    assert DiagnosisEngine().diagnose_run_aggregate(rows, MAPPING) == \
        aggregate_run_diagnoses(rows, MAPPING)


def test_aggregator_imports_no_engines_or_orm() -> None:
    """Consumption-only guard: the aggregation module must not reach engines,
    ORM models, or the Judge boundary — persisted rows are its only input."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/diagnosis/run_aggregator.py").read_text(
        encoding="utf-8")
    for forbidden in ("app.engines", "app.models", "JudgeClient", "Session"):
        assert forbidden not in src, forbidden


# ---------------- report integration ----------------


def test_report_surfaces_run_level_buckets() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    result_id = _add_record(repo, run_id, "r1", True)
    repo.insert_diagnosis_with_recommendations(
        _d(run_id=run_id, result_id=result_id, evidence=EVIDENCE), [])
    repo.insert_diagnosis_with_recommendations(
        _d(run_id=run_id, status="undetermined", failure_type=None,
           metric="temporal_consistency", severity="INFO", result_id=result_id), [])

    rep = _report(repo, run_id)
    rld = rep["run_level_diagnoses"]
    assert rld["status"] == "available"
    assert rld["total_diagnoses"] == 2
    assert rld["total_failure_records"] == 1
    assert rld["undetermined_count"] == 1
    (top, und) = rld["items"]
    assert top["failure_type"] == "integrity.numerical_mismatch" and top["count"] == 1
    assert top["evidence_available"] is True
    assert und["status"] == "undetermined" and und["failure_type"] is None


def test_report_without_diagnoses_stays_not_available() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    rep = _report(repo, run_id)
    assert rep["run_level_diagnoses"]["status"] == "not_available"
    assert rep["run_level_diagnoses"]["total_diagnoses"] == 0
