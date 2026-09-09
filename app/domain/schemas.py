from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["retrieval", "generation", "integrity"]
# T-10 addition (sanctioned 2026-08-29): entity_mismatch / temporal_mismatch —
# integrity comparators need finer-grained mismatch types than the original 6.
ComparisonType = Literal[
    "match", "value_mismatch", "unit_mismatch", "scale_mismatch",
    "missing_reference", "ambiguous", "entity_mismatch", "temporal_mismatch",
]
Method = Literal["deterministic", "llm_fallback"]
Severity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
Confidence = Literal["high", "medium", "low"]
EvidenceType = Literal[
    "reference_evidence", "retrieved_evidence", "answer_claim", "query_evidence",
    "metadata_evidence", "ranking_evidence", "configuration_evidence",
    "execution_evidence",
]
RunStatus = Literal[
    "pending", "running", "completed", "completed_with_errors", "failed", "cancelled",
]


class EvaluationRecord(BaseModel):
    """Core abstraction, framework-agnostic (FR-07)."""

    id: str
    question: str
    contexts: list[str]
    answer: str
    reference_answer: str | None = None
    reference_contexts: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ComparisonBasis(BaseModel):
    """Integrity metrics MUST populate this (FR-18). Never fabricate later."""

    reference: dict | None = None
    answer: dict | None = None
    comparison_type: ComparisonType
    method: Method
    tolerance_applied: dict | None = None
    diff: dict | None = None


class MetricResult(BaseModel):
    record_id: str
    metric_name: str
    category: Category
    score: float | None = None  # NULL when the evaluator errored
    threshold: float | None = None  # NULL = no PASS/FAIL judgment
    passed: bool | None = None  # NULL when threshold is NULL
    comparison_basis: ComparisonBasis | None = None
    metric_version: str
    error: dict | None = None


class Evidence(BaseModel):
    type: EvidenceType
    source: str
    locator: str | None = None
    content: Any
    metadata: dict | None = None


class EvidenceBundle(BaseModel):
    """Persistence form. Flat string arrays forbidden (ADR-07)."""

    contract: str  # "<taxonomy_code>.v1"
    items: list[Evidence]


class Diagnosis(BaseModel):
    failure_type: str  # one of 14 taxonomy codes
    related_metric: str | None = None
    root_cause: str | None = None  # None when undetermined
    severity: Severity
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_contract: str  # "<code>.v1"
    confidence: Confidence
    result_id: str | None = None  # None = Run-level; non-None = Sample-level


# ---------------- T-11: Failure / Diagnosis layer ----------------

FailureSource = Literal["threshold", "deterministic_mismatch"]


class Failure(BaseModel):
    """Domain signal produced by FailureClassifier (Spec A.5.5: score alone
    NEVER implies a root cause — a Failure only marks the sample for
    diagnosis). Two sources:
      A. threshold:       score != null AND threshold != null AND score < threshold
      B. deterministic:   comparison_type is an explicit deterministic mismatch
    ambiguous / unparsed / missing_reference are NOT failures — they are
    UNDETERMINED (insufficient evidence); forcing a diagnosis on them is
    forbidden."""

    result_id: str | None = None  # None = Run-level; non-None = Sample-level
    metric_name: str
    source: FailureSource
    comparison_type: ComparisonType | None = None
    detail: dict = Field(default_factory=dict)


class Recommendation(BaseModel):
    """Advisory only — must never write to any user system (Spec A.5)."""

    action: str
    priority: int = 1  # 1 = highest
    source: Literal["rule", "llm"] = "rule"


class DiagnosisResult(BaseModel):
    """DiagnosisEngine output wrapper. `status` makes the undetermined branch
    explicit instead of overloading Diagnosis with null fields."""

    status: Literal["diagnosed", "undetermined", "not_failed"]
    diagnosis: Diagnosis | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)  # undetermined: unmet contract items
    reason: str | None = None
    # T-12 persistence aid: for undetermined-with-rule outcomes, the REAL
    # taxonomy code that would have applied (never a sentinel). NULL when no
    # code is applicable (ambiguous / no matching rule).
    candidate_failure_type: str | None = None


class DatasetRecordData(BaseModel):
    """Golden-set record (import/persistence layer).

    Distinct from EvaluationRecord by design: the golden dataset carries
    question + references only; answer/contexts belong to runtime evaluation
    (EvaluationRecord / evaluation_results snapshot, Spec §5.2). No id — the
    dataset_record UUID becomes EvaluationRecord.id at Run time. row_index is
    assigned from list position at import (Spec §5.2: source-file locator).
    """

    question: str
    reference_answer: str | None = None
    reference_contexts: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    row_index: int
    field: str
    detail: str = ""


class ValidationCheck(BaseModel):
    name: str  # schema | duplicate | missing_field | reference | domain_metadata
    passed: bool
    count: int | None = None  # present on passing checks (Spec A.4)
    issues: list[ValidationIssue] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """Spec A.4 contract: {valid, checks:[{name, passed, count|issues}]}."""

    valid: bool
    checks: list[ValidationCheck] = Field(default_factory=list)
