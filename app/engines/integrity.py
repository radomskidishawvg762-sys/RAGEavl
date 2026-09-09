"""IntegrityEngine (T-10 / FR-16) — entity/temporal/numerical consistency.

Pipeline per metric (Spec A.6 Deterministic-first Hybrid):
  T-09 normalization -> T-10 deterministic comparison -> ComparisonBasis ->
  MetricResult.

Boundary rules: Metric ≠ Diagnosis (P-1) — no Diagnosis/Recommendation here;
no Repository/ORM; no Judge LLM in MVP (LLM fallback hook deliberately
unwired); single-record errors become structured MetricResult.error and never
break the batch (FR-22).

Metric version pins the normalization+comparison semantics:
  "integrity-normalization-v2" (v2: deterministic multi-candidate pairing for
  numerical_consistency — strict unique affinity pairing replaces first-ok
  selection whenever either side states several ok values).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.domain.schemas import (
    ComparisonBasis,
    EvaluationRecord,
    MetricResult,
)
from app.engines.base import EvalParams
from app.metrics.base import MetricSpec
from app.metrics.integrity import (
    extract_entities,
    extract_numerical,
    extract_temporal,
)
from app.metrics.integrity.base import NormalizedBase
from app.metrics.integrity.comparison import (
    ComparisonOutcome,
    compare_entity,
    compare_numerical,
    compare_temporal,
)
from app.metrics.integrity.pairing import pair_or_fallback
from app.metrics.registry import MetricRegistry

INTEGRITY_METRIC_VERSION = "integrity-normalization-v2"


class IntegrityMetricDef(BaseModel):
    name: str
    kind: str  # entity | temporal | numerical
    input_requirements: list[str]


INTEGRITY_METRIC_DEFS = [
    IntegrityMetricDef(
        name="entity_consistency", kind="entity",
        input_requirements=["answer", "reference_answer"],
    ),
    IntegrityMetricDef(
        name="temporal_consistency", kind="temporal",
        input_requirements=["answer", "reference_answer"],
    ),
    IntegrityMetricDef(
        name="numerical_consistency", kind="numerical",
        input_requirements=["answer", "reference_answer"],
    ),
]


class IntegrityEngine:
    name = "integrity"

    def __init__(self, alias_table: dict[str, str] | None = None) -> None:
        # alias table is caller/domain-supplied data (Spec A.6.2) — no built-ins
        self._alias_table = alias_table or {}

    # ---- EvaluationEngine protocol ----

    def metric_names(self) -> list[str]:
        return [d.name for d in INTEGRITY_METRIC_DEFS]

    def specs(self) -> list[MetricSpec]:
        return [
            MetricSpec(
                name=d.name,
                category="integrity",
                engine=self.name,
                description=f"{d.kind} consistency (deterministic-first, Spec A.6)",
                input_requirements=d.input_requirements,
                version=INTEGRITY_METRIC_VERSION,
                direction="higher_is_better",
            )
            for d in INTEGRITY_METRIC_DEFS
        ]

    def register_into(self, registry: MetricRegistry) -> None:
        for spec in self.specs():
            registry.register(spec, self._make_evaluator(spec.name))

    async def evaluate(
        self,
        record: EvaluationRecord,
        metrics: list[str],
        params: EvalParams,
    ) -> list[MetricResult]:
        owned = {d.name: d for d in INTEGRITY_METRIC_DEFS}
        tolerance = (params.extra or {}).get("tolerance") or {}
        alias_table = (params.extra or {}).get("alias_table") or self._alias_table
        results: list[MetricResult] = []
        for metric_name in metrics:
            d = owned.get(metric_name)
            if d is None:
                continue
            missing = [
                f for f in d.input_requirements
                if getattr(record, f, None) is None
                or (isinstance(getattr(record, f, None), (str, list)) and len(getattr(record, f)) == 0)
            ]
            if missing:
                results.append(self._error_result(record.id, metric_name,
                                                  code="BIZ_METRIC_INPUT_MISSING",
                                                  message="record lacks required fields",
                                                  missing=missing))
                continue
            try:
                results.append(
                    self._evaluate_one(record, d, tolerance=tolerance, alias_table=alias_table,
                                       threshold=self._threshold_for(metric_name, params))
                )
            except Exception as e:  # noqa: BLE001 — never break the batch
                results.append(self._error_result(record.id, metric_name,
                                                  code="SYS_METRIC_ERROR",
                                                  message=f"{type(e).__name__}: {e}"[:500]))
        return results

    # ---- internals ----

    def _extract(self, kind: str, text: str, alias_table: dict[str, str]) -> list[NormalizedBase]:
        if kind == "entity":
            return list(extract_entities(text, alias_table=alias_table))
        if kind == "temporal":
            return list(extract_temporal(text))
        return list(extract_numerical(text))

    @staticmethod
    def _primary(
        items: list[NormalizedBase]
    ) -> tuple[NormalizedBase | None, str | None, list[NormalizedBase]]:
        """First ok candidate as primary; else first ambiguity reason; all
        candidates are preserved for the ComparisonBasis record."""
        oks = [i for i in items if i.parse_status == "ok"]
        if oks:
            return oks[0], None, items
        if items:
            return None, (items[0].ambiguity_reason or f"primary {items[0].kind} ambiguous"), items
        return None, None, items

    def _evaluate_one(
        self,
        record: EvaluationRecord,
        d: IntegrityMetricDef,
        *,
        tolerance: dict,
        alias_table: dict[str, str],
        threshold: float | None,
    ) -> MetricResult:
        ref_text = record.reference_answer or ""
        ans_text = record.answer or ""
        ref_items = self._extract(d.kind, ref_text, alias_table)
        ans_items = self._extract(d.kind, ans_text, alias_table)
        ref, _ref_reason, _ = self._primary(ref_items)
        ans, _ans_reason, _ = self._primary(ans_items)
        ref_oks = [i for i in ref_items if i.parse_status == "ok"]
        ans_oks = [i for i in ans_items if i.parse_status == "ok"]

        if ref is not None and ans is not None:
            if (
                d.kind == "numerical"
                and (len(ref_oks) > 1 or len(ans_oks) > 1)
            ):
                # Multi-value records: strict deterministic pairing replaces
                # first-ok selection (never compares arbitrary first values).
                outcome = pair_or_fallback(
                    ref_oks, ans_oks,
                    ref_text=ref_text, ans_text=ans_text,
                    alias_table=alias_table,
                    absolute_tolerance=tolerance.get("absolute"),
                    relative_tolerance=tolerance.get("relative"),
                )
            else:
                outcome = self._compare(d.kind, ref, ans, tolerance)
        else:
            ref_empty, ans_empty = not ref_items, not ans_items
            if ref_empty and ans_empty:
                outcome = ComparisonOutcome(
                    comparison_type="match", score=1.0,
                    reason=f"no {d.kind} expression on either side (vacuously consistent)",
                    detail={"vacuous": True},
                )
            elif ref_empty:
                if ans is not None:
                    outcome = ComparisonOutcome(
                        comparison_type="missing_reference", score=None,
                        reason=f"reference contains no {d.kind} expression",
                        detail={},
                    )
                else:
                    outcome = ComparisonOutcome(
                        comparison_type="ambiguous", score=None,
                        reason=(f"reference expresses no {d.kind}; answer {d.kind} ambiguous"),
                        detail={},
                    )
            elif ans_empty:
                outcome = ComparisonOutcome(
                    comparison_type="ambiguous", score=None,
                    reason=f"answer contains no {d.kind} expression to verify",
                    detail={},
                )
            else:
                # both sides have candidates but at least one is not reliably
                # parsed — ambiguous + anything is NEVER a match (decision).
                reasons = [
                    i.ambiguity_reason
                    for i in (*ref_items, *ans_items)
                    if i.parse_status != "ok" and i.ambiguity_reason
                ]
                outcome = ComparisonOutcome(
                    comparison_type="ambiguous", score=None,
                    reason="; ".join(reasons)
                    or f"{d.kind} not reliably normalized on at least one side",
                    detail={},
                )

        def _side(items: list[NormalizedBase], primary: NormalizedBase | None) -> tuple[dict, str]:
            if primary is not None:
                status = "ok"
            elif items:
                status = "ambiguous"
            else:
                status = "empty"
            return (
                {
                    "primary": primary.model_dump() if primary else None,
                    "candidates": [i.model_dump() for i in items],
                },
                status,
            )

        ref_payload, ref_status = _side(ref_items, ref)
        ans_payload, ans_status = _side(ans_items, ans)
        basis = ComparisonBasis(
            reference={**ref_payload, "side_status": ref_status},
            answer={**ans_payload, "side_status": ans_status},
            comparison_type=outcome.comparison_type,
            method="deterministic",
            tolerance_applied=tolerance or None,
            diff={k: v for k, v in outcome.detail.items() if k != "tolerance"} or None,
        )
        return MetricResult(
            record_id=record.id,
            metric_name=d.name,
            category="integrity",
            score=outcome.score,
            threshold=threshold,
            passed=(outcome.score >= threshold) if (outcome.score is not None and threshold is not None) else None,
            comparison_basis=basis,
            metric_version=INTEGRITY_METRIC_VERSION,
            error=({"reason": outcome.reason} if outcome.reason and outcome.score is None else None),
        )

    @staticmethod
    def _compare(
        kind: str, ref: NormalizedBase, ans: NormalizedBase, tolerance: dict
    ) -> ComparisonOutcome:
        if kind == "entity":
            return compare_entity(ref, ans)  # type: ignore[arg-type]
        if kind == "temporal":
            return compare_temporal(ref, ans)  # type: ignore[arg-type]
        return compare_numerical(
            ref, ans,  # type: ignore[arg-type]
            absolute_tolerance=tolerance.get("absolute"),
            relative_tolerance=tolerance.get("relative"),
        )

    @staticmethod
    def _threshold_for(metric_name: str, params: EvalParams) -> float | None:
        per_metric = params.extra.get(metric_name) if params.extra else None
        if isinstance(per_metric, dict) and "threshold" in per_metric:
            return per_metric["threshold"]
        return params.threshold

    @staticmethod
    def _error_result(
        record_id: str, metric_name: str, *, code: str, message: str,
        missing: list[str] | None = None,
    ) -> MetricResult:
        error: dict[str, Any] = {"code": code, "message": message}
        if missing:
            error["missing"] = missing
        return MetricResult(
            record_id=record_id, metric_name=metric_name, category="integrity",
            score=None, threshold=None, passed=None, comparison_basis=None,
            metric_version=INTEGRITY_METRIC_VERSION, error=error,
        )

    def _make_evaluator(self, metric_name: str):
        async def evaluator(record: EvaluationRecord, params: dict) -> MetricResult:
            eval_params = params if isinstance(params, EvalParams) else EvalParams.model_validate(params)
            results = await self.evaluate(record, [metric_name], eval_params)
            return results[0]

        return evaluator
