"""RuleEngine (T-11 MVP) — resolves a Failure to a DiagnosisRule.

MVP scope: integrity rules only (comparison_type -> taxonomy code via
taxonomy.COMPARISON_TO_RULE). Threshold-source failures and RAGAS-side
failures have NO rule in this round: the engine reports them undetermined
("no rule") instead of guessing a root cause (Spec A.5.5 hard constraint).
Faithfulness / Context Recall / Answer Relevancy / LLM-assisted diagnosis are
explicitly out of scope.
"""

from __future__ import annotations

from app.diagnosis.taxonomy import COMPARISON_TO_RULE, INTEGRITY_RULES, DiagnosisRule
from app.domain.schemas import Failure


class RuleEngine:
    def resolve(self, failure: Failure) -> DiagnosisRule | None:
        """Deterministic-mismatch failures resolve via comparison_type; the
        resolved rule's trigger_metrics must still cover the failing metric."""
        if failure.source != "deterministic_mismatch" or failure.comparison_type is None:
            return None
        code = COMPARISON_TO_RULE.get(failure.comparison_type)
        if code is None:
            return None
        rule = INTEGRITY_RULES.get(code)
        if rule is None:
            return None
        if failure.metric_name not in rule.trigger_metrics:
            return None
        return rule
