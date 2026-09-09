"""DiagnosisEngine (T-11 / FR-23..25) — Integrity + Retrieval Diagnosis.

Flow (Spec A.5.5 — score alone NEVER implies a root cause):
  FailureClassifier -> Failure -> RuleEngine -> EvidenceCollector ->
  Evidence Contract validation -> Diagnosis (+ rule Recommendations)

Integrity path: deterministic mismatch -> COMPARISON_TO_RULE -> integrity rule.
Retrieval path (Post-MVP Phase 5): a threshold failure on a retrieval metric
only TRIGGERS the check — retrieval.retrieval evidence comparison (pure,
deterministic, in app/diagnosis/retrieval.py) decides whether
retrieval.missing_evidence / retrieval.top_k_issue can be attributed;
metadata_filter_issue / knowledge_coverage stay BLOCKED (no evidence source).

Responsibilities here: find the rule for a Failure, request evidence,
validate the Evidence Contract, emit root_cause / severity / confidence.
It does NOT decide failure-ness (FailureClassifier does), does NOT mutate
MetricResult or EvaluationRecord, does NOT touch Repository/ORM, and calls
NO LLM (LLM-assisted diagnosis is out of MVP scope).

Undetermined handling (hard rule): ambiguous / unparsed / missing evidence /
no MVP rule -> status="undetermined", root_cause=None, NO attribution.
Sample-level (result_id != null) is implemented; Run-level keeps only the
extension point `diagnose_run` (aggregation reasoning deferred).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.diagnosis.classifier import FailureClassifier
from app.diagnosis.evidence import EvidenceCollector
from app.diagnosis.generation import (
    GENERATION_TRIGGER_METRICS,
    evaluate_generation,
)
from app.diagnosis.recommendations import build_recommendations
from app.diagnosis.retrieval import (
    RETRIEVAL_TRIGGER_METRICS,
    evaluate_retrieval,
)
from app.diagnosis.rules import RuleEngine
from app.diagnosis.run_aggregator import DiagnosisLike, RunDiagnosisSummary, aggregate_run_diagnoses
from app.diagnosis.taxonomy import (
    DEFAULT_SEVERITY_HINTS,
    GENERATION_RULES,
    RETRIEVAL_RULES,
    DiagnosisRule,
)
from app.domain.schemas import (
    Diagnosis,
    DiagnosisResult,
    EvaluationRecord,
    MetricResult,
    Severity,
)


class DiagnosisEngine:
    name = "rule_based_integrity"

    def __init__(
        self,
        severity_mapping: dict[str, str] | None = None,
        *,
        classifier: FailureClassifier | None = None,
        rules: RuleEngine | None = None,
        collector: EvidenceCollector | None = None,
    ) -> None:
        # severity_mapping: Profile overrides ("numerical_mismatch": "CRITICAL" ...)
        # keys accept either the full taxonomy code or the short suffix.
        self._severity_mapping = severity_mapping or {}
        self._classifier = classifier or FailureClassifier()
        self._rules = rules or RuleEngine()
        self._collector = collector or EvidenceCollector()

    def diagnose(self, record: EvaluationRecord, result: MetricResult) -> DiagnosisResult:
        classification = self._classifier.classify(result)
        if classification.status != "failure" or classification.failure is None:
            return DiagnosisResult(
                status="undetermined" if classification.status == "undetermined" else "not_failed",
                reason=classification.reason,
            )

        failure = classification.failure
        # Phase 5/6 evidence-first dispatch: the score only TRIGGERS;
        # attribution is decided exclusively by the deterministic evidence
        # comparison (retrieval: containment; generation: numeric equivalence).
        if failure.source == "threshold" and failure.metric_name in RETRIEVAL_TRIGGER_METRICS:
            return self._diagnose_retrieval(record, failure)
        if failure.source == "threshold" and failure.metric_name in GENERATION_TRIGGER_METRICS:
            return self._diagnose_generation(record, failure)
        rule = self._rules.resolve(failure)
        if rule is None:
            # Threshold-source failures and RAGAS-side failures: no MVP rule —
            # report undetermined, never fabricate a root cause from a score.
            return DiagnosisResult(
                status="undetermined",
                reason=(
                    f"no MVP diagnosis rule for failure source={failure.source} "
                    f"metric={failure.metric_name} (integrity-only this round)"
                ),
            )

        bundle = self._collector.collect_integrity(record, result, rule.evidence_contract)
        missing = self._collector.validate_integrity(bundle, result.comparison_basis)
        if missing:
            return DiagnosisResult(
                status="undetermined",
                missing_evidence=missing,
                reason=f"evidence contract {rule.evidence_contract} unmet; attribution forbidden",
                candidate_failure_type=rule.code,  # real code, NOT attributed
            )

        diagnosis = Diagnosis(
            failure_type=rule.code,
            related_metric=result.metric_name,
            root_cause=self._root_cause(rule, result),
            severity=self._severity_for(rule),
            evidence=bundle.items,
            evidence_contract=rule.evidence_contract,
            confidence="high",  # deterministic comparison + contract satisfied
            result_id=record.id,  # Sample-level diagnosis
        )
        return DiagnosisResult(
            status="diagnosed",
            diagnosis=diagnosis,
            recommendations=build_recommendations(rule),
        )

    def diagnose_run(self, results: list[MetricResult]) -> list[DiagnosisResult]:
        """Run-level extension point (result_id == null semantics). MVP keeps
        the interface only — cross-sample aggregation reasoning is deferred;
        callers get an empty list rather than a fabricated aggregate."""
        return []

    def diagnose_run_aggregate(
        self,
        diagnoses: Iterable[DiagnosisLike],
        record_id_by_result: Mapping[str, str | None],
    ) -> RunDiagnosisSummary:
        """Run-level diagnosis = aggregation of PERSISTED sample-level
        diagnoses (Failure -> Evidence -> Diagnosis already happened per
        sample; this only counts and groups). Consumption-only: no Engine or
        Judge re-execution, no score-only inference (Spec A.5.5) — samples
        without a satisfied Evidence Contract stay in undetermined buckets."""
        return aggregate_run_diagnoses(diagnoses, record_id_by_result)

    # ---- internals ----

    def _diagnose_retrieval(self, record: EvaluationRecord, failure) -> DiagnosisResult:
        """Retrieval evidence check -> Diagnosis (Phase 5)."""
        return self._diagnose_from_check(evaluate_retrieval(record), record, failure, RETRIEVAL_RULES)

    def _diagnose_generation(self, record: EvaluationRecord, failure) -> DiagnosisResult:
        """Generation evidence check -> Diagnosis (Phase 6)."""
        return self._diagnose_from_check(evaluate_generation(record), record, failure, GENERATION_RULES)

    def _diagnose_from_check(
        self, outcome, record: EvaluationRecord, failure, rules: dict[str, DiagnosisRule]
    ) -> DiagnosisResult:
        """Shared evidence-check -> Diagnosis path (existing contract).

        diagnosed    -> real taxonomy code + EvidenceBundle + recommendations
                        (rule lookup goes through taxonomy so severity_mapping
                        and ACTION_TEMPLATES stay authoritative)
        undetermined -> NO attribution; reason/missing_evidence/candidate
                        come from the deterministic check.
        """
        if outcome.status == "diagnosed" and outcome.code and outcome.bundle:
            rule = rules[outcome.code]
            diagnosis = Diagnosis(
                failure_type=rule.code,
                related_metric=failure.metric_name,
                root_cause=outcome.root_cause,
                severity=self._severity_for(rule),
                evidence=list(outcome.bundle.items),
                evidence_contract=rule.evidence_contract,
                confidence="high",  # deterministic comparison + contract satisfied
                result_id=record.id,
            )
            return DiagnosisResult(
                status="diagnosed",
                diagnosis=diagnosis,
                recommendations=build_recommendations(rule),
            )
        return DiagnosisResult(
            status="undetermined",
            reason=outcome.reason,
            missing_evidence=outcome.missing_evidence,
            candidate_failure_type=outcome.candidate,
        )

    @staticmethod
    def _root_cause(rule: DiagnosisRule, result: MetricResult) -> str:
        """Human-readable root cause built ONLY from deterministic basis facts."""
        basis = result.comparison_basis
        title = rule.code.split(".", 1)[1].replace("_", " ").title()  # "Numerical Mismatch"
        facts: list[str] = []
        if basis is not None and basis.diff:
            base = basis.diff.get("base")
            if isinstance(base, dict) and base.get("abs_diff") is not None:
                facts.append(f"abs_diff={base['abs_diff']}")
            if basis.diff.get("relation"):
                facts.append(f"relation={basis.diff['relation']}")
        side = (basis.reference or {}).get("primary") if basis else None
        if isinstance(side, dict) and side.get("raw"):
            facts.append(f"reference={side['raw']!r}")
        return f"{title} ({', '.join(facts)})" if facts else title

    def _severity_for(self, rule: DiagnosisRule) -> Severity:
        for key in (rule.code, rule.code.split(".", 1)[1]):
            if key in self._severity_mapping:
                return self._severity_mapping[key]  # type: ignore[return-value]
        return DEFAULT_SEVERITY_HINTS.get(rule.code, rule.severity_hint)
