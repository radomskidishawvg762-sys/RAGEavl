"""FailureClassifier (T-11) — the ONLY place that decides "is this a Failure?".

Boundary: MetricResult + ComparisonBasis + Profile(threshold) -> Failure.
DiagnosisEngine never self-judges failure-ness (user decision 2026-08-29).

Failure semantics:
  A. threshold failure:       score != null AND threshold != null AND score < threshold
  B. deterministic mismatch:  comparison_type in DETERMINISTIC_MISMATCH_TYPES
     (value_mismatch / unit_mismatch / scale_mismatch / temporal_mismatch /
      entity_mismatch) — a direct Integrity Failure regardless of threshold.

NOT failures (UNDETERMINED / INSUFFICIENT EVIDENCE — never diagnosed):
  ambiguous, missing_reference (unparsed sides normalize to these), and
  metric-level errors. ambiguity + anything is never converted into a
  mismatch, and never guessed into a root cause.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.domain.schemas import Failure, MetricResult

DETERMINISTIC_MISMATCH_TYPES = {
    "value_mismatch",
    "unit_mismatch",
    "scale_mismatch",
    "temporal_mismatch",
    "entity_mismatch",
}

# comparison outcomes that mean "cannot judge" — UNDETERMINED, never Failure
UNDETERMINED_TYPES = {"ambiguous", "missing_reference"}

ClassificationStatus = Literal["failure", "not_failed", "undetermined"]


class Classification(BaseModel):
    status: ClassificationStatus
    failure: Failure | None = None
    reason: str | None = None


class FailureClassifier:
    """Pure function of (MetricResult, Profile threshold already baked into
    result.threshold). No ORM, no LLM, no mutation of the input result."""

    def classify(self, result: MetricResult) -> Classification:
        # 0) evaluator-level error WITHOUT a comparison basis (missing input /
        #    exception) means nothing was judged -> undetermined. Note: judged
        #    null-score outcomes (ambiguous / unit_mismatch / missing_reference)
        #    also carry error={"reason"} but ALWAYS carry a comparison_basis —
        #    those are handled by the comparison_type branches below, never as
        #    evaluator errors.
        if result.error is not None and result.comparison_basis is None:
            code = result.error.get("code", "unknown")
            return Classification(
                status="undetermined",
                reason=f"metric error ({code}): insufficient basis to judge",
            )

        basis = result.comparison_basis
        ctype = basis.comparison_type if basis is not None else None

        # 1) cannot-judge outcomes are UNDETERMINED — never Failure, never match
        if ctype in UNDETERMINED_TYPES:
            return Classification(
                status="undetermined",
                reason=f"comparison_type={ctype}: cannot reliably judge (no forced diagnosis)",
            )

        # 2) B — deterministic integrity mismatch is a Failure outright
        if ctype in DETERMINISTIC_MISMATCH_TYPES:
            return Classification(
                status="failure",
                failure=Failure(
                    result_id=result.record_id,
                    metric_name=result.metric_name,
                    source="deterministic_mismatch",
                    comparison_type=ctype,
                    detail={
                        "score": result.score,
                        "threshold": result.threshold,
                        "method": basis.method if basis else None,
                        "diff": basis.diff if basis else None,
                        "threshold_failure_also": self._threshold_failure(result),
                    },
                ),
            )

        # 3) A — pure threshold failure (e.g. RAGAS score below threshold)
        if self._threshold_failure(result):
            return Classification(
                status="failure",
                failure=Failure(
                    result_id=result.record_id,
                    metric_name=result.metric_name,
                    source="threshold",
                    comparison_type=ctype,
                    detail={"score": result.score, "threshold": result.threshold},
                ),
            )

        return Classification(status="not_failed")

    @staticmethod
    def _threshold_failure(result: MetricResult) -> bool:
        return (
            result.score is not None
            and result.threshold is not None
            and result.score < result.threshold
        )
