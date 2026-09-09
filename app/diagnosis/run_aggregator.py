"""Run-level diagnosis aggregation (Post-MVP Phase 4).

Consumes ONLY persisted sample-level Diagnosis rows — every bucket exists
because the corresponding sample diagnoses already passed the full chain
FailureClassifier -> RuleEngine -> EvidenceContract. No Engine, no Judge,
no re-classification of MetricResults, and never a root cause inferred from
a score alone (Spec A.5.5): evidence-insufficient samples stay in
"undetermined" buckets, never attributed.

Bucketing:
  diagnosed    -> keyed by the REAL taxonomy code (failure_type)
  undetermined -> keyed by the candidate code when present, else None;
                  evidence_available is False by construction (attribution
                  is forbidden without a satisfied Evidence Contract)

ratio denominator = total persisted sample-level diagnosis rows
(diagnosed + undetermined), exposed as total_diagnoses so consumers can
recompute the percentage themselves.

Boundaries: no Repository/ORM import — inputs are duck-typed row-like
objects (ORM rows and domain Diagnosis both satisfy the Protocol); pure
and side-effect free.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

SEVERITY_ORDER: dict[str, int] = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}


@runtime_checkable
class DiagnosisLike(Protocol):
    """Structural view of a persisted Diagnosis row (ORM or domain model)."""

    status: str
    failure_type: str | None
    related_metric: str | None
    severity: str
    evidence: list | None
    result_id: str | None


class RunFailureBucket(BaseModel):
    status: Literal["diagnosed", "undetermined"]
    failure_type: str | None  # real code (diagnosed) or candidate/None (undetermined)
    count: int
    ratio: float  # count / total_diagnoses
    severity: str  # highest severity among the bucket's rows
    related_metrics: list[str] = Field(default_factory=list)
    affected_records: int  # distinct samples (record-level) in this bucket
    evidence_available: bool  # diagnosed: every row carries >=1 evidence item


class RunDiagnosisSummary(BaseModel):
    available: bool  # False when the run has no persisted sample-level diagnoses
    total_diagnoses: int  # denominator of bucket ratios
    total_failure_records: int  # distinct samples with >=1 diagnosis row
    undetermined_count: int
    buckets: list[RunFailureBucket] = Field(default_factory=list)  # count desc


def _severity_max(current: str | None, candidate: str) -> str:
    if current is None or SEVERITY_ORDER.get(candidate, 0) > SEVERITY_ORDER.get(current, 0):
        return candidate
    return current


def _has_evidence(row: DiagnosisLike) -> bool:
    return bool(row.evidence) and len(row.evidence) > 0


def aggregate_run_diagnoses(
    diagnoses: Iterable[DiagnosisLike],
    record_id_by_result: Mapping[str, str | None],
) -> RunDiagnosisSummary:
    """Aggregate persisted Diagnosis rows into run-level failure buckets.

    `record_id_by_result` maps evaluation_results.id -> dataset_records.id so
    affected_records is record-level (a record with two diagnoses of the same
    type counts once there, but twice in `count`).
    """
    rows = list(diagnoses)
    total = len(rows)
    if total == 0:
        return RunDiagnosisSummary(
            available=False, total_diagnoses=0, total_failure_records=0,
            undetermined_count=0, buckets=[],
        )

    class _Acc:
        def __init__(self) -> None:
            self.count = 0
            self.severity: str | None = None
            self.metrics: set[str] = set()
            self.samples: set[str] = set()
            self.with_evidence = 0

    acc_by_key: dict[tuple[str, str | None], _Acc] = {}
    all_samples: set[str] = set()
    undetermined_count = 0

    for row in rows:
        status = "diagnosed" if row.status == "diagnosed" else "undetermined"
        key = (status, row.failure_type if status == "diagnosed" else (row.failure_type or None))
        acc = acc_by_key.setdefault(key, _Acc())
        acc.count += 1
        acc.severity = _severity_max(acc.severity, row.severity or "INFO")
        if row.related_metric:
            acc.metrics.add(row.related_metric)
        if row.result_id:
            sample = record_id_by_result.get(row.result_id) or row.result_id
            acc.samples.add(sample)
            all_samples.add(sample)
        if _has_evidence(row):
            acc.with_evidence += 1
        if status == "undetermined":
            undetermined_count += 1

    buckets = [
        RunFailureBucket(
            status=status,
            failure_type=failure_type,
            count=acc.count,
            ratio=acc.count / total,
            severity=acc.severity or "INFO",
            related_metrics=sorted(acc.metrics),
            affected_records=len(acc.samples),
            # undetermined rows never carry a satisfied contract — never True
            evidence_available=(acc.with_evidence == acc.count) if status == "diagnosed" else False,
        )
        for (status, failure_type), acc in acc_by_key.items()
    ]
    buckets.sort(key=lambda b: (-b.count, b.status != "diagnosed", b.failure_type is None,
                                b.failure_type or ""))
    return RunDiagnosisSummary(
        available=True,
        total_diagnoses=total,
        total_failure_records=len(all_samples),
        undetermined_count=undetermined_count,
        buckets=buckets,
    )
