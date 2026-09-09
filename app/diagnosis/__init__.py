"""Diagnosis layer (T-11 MVP): Integrity Diagnosis only.

Public surface:
  FailureClassifier  — Failure semantics (threshold / deterministic / undetermined)
  DiagnosisEngine    — rule match -> evidence contract -> Diagnosis
  EvidenceCollector  — Layer-1/2 evidence collection + contract validation
  RuleEngine         — Failure -> DiagnosisRule (integrity rules only)
  build_recommendations — minimal rule templates (no LLM)
  taxonomy           — 14 taxonomy codes + integrity rules + severity hints

Boundaries: no Repository/ORM, no LLM, no mutation of MetricResult /
EvaluationRecord. Faithfulness / Context Recall / Answer Relevancy / LLM
diagnosis are explicitly NOT implemented this round.
"""

from app.diagnosis.classifier import (
    DETERMINISTIC_MISMATCH_TYPES,
    UNDETERMINED_TYPES,
    Classification,
    FailureClassifier,
)
from app.diagnosis.engine import DiagnosisEngine
from app.diagnosis.evidence import (
    INTEGRITY_CONTRACT_TYPES,
    EvidenceCollector,
    InsufficientEvidenceError,
)
from app.diagnosis.recommendations import build_recommendations
from app.diagnosis.rules import RuleEngine
from app.diagnosis.run_aggregator import (
    RunDiagnosisSummary,
    RunFailureBucket,
    aggregate_run_diagnoses,
)
from app.diagnosis.taxonomy import (
    FAILURE_TAXONOMY,
    INTEGRITY_CODES,
    INTEGRITY_RULES,
    DiagnosisRule,
)

__all__ = [
    "DETERMINISTIC_MISMATCH_TYPES",
    "UNDETERMINED_TYPES",
    "Classification",
    "FailureClassifier",
    "DiagnosisEngine",
    "INTEGRITY_CONTRACT_TYPES",
    "EvidenceCollector",
    "InsufficientEvidenceError",
    "build_recommendations",
    "RuleEngine",
    "RunDiagnosisSummary",
    "RunFailureBucket",
    "aggregate_run_diagnoses",
    "FAILURE_TAXONOMY",
    "INTEGRITY_CODES",
    "INTEGRITY_RULES",
    "DiagnosisRule",
]
