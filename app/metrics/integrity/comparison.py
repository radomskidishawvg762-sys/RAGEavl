"""Deterministic comparators (T-10). Pure functions over T-09 outputs.

NO Diagnosis, NO Recommendation, NO Repository/ORM, NO Judge LLM — the LLM
fallback hook stays unwired in MVP (ADR-04: deterministic first).

Temporal semantics (decided 2026-08-29): SEMANTIC EQUIVALENCE FIRST —
  - equal normalized interval -> match
  - disjoint                  -> temporal_mismatch
  - containment / overlap     -> ambiguous (+ relation recorded), NEVER match
  - any non-ok side           -> ambiguous
Two ambiguous sides are ambiguous, never match.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.metrics.integrity.entity import NormalizedEntity
from app.metrics.integrity.numerical import (
    DEFAULT_ABSOLUTE_TOLERANCE,
    DEFAULT_RELATIVE_TOLERANCE,
    UNIT_CLASS_BY_UNIT,
    NormalizedNumerical,
)
from app.metrics.integrity.temporal import NormalizedTemporal

ComparisonTypeStr = Literal[
    "match", "value_mismatch", "unit_mismatch", "scale_mismatch",
    "missing_reference", "ambiguous", "entity_mismatch", "temporal_mismatch",
]

_MATCH = 1.0
_MISMATCH = 0.0


class ComparisonOutcome(BaseModel):
    comparison_type: ComparisonTypeStr
    score: float | None  # 1.0 / 0.0 when judged; None when not judgeable
    reason: str | None = None
    detail: dict = Field(default_factory=dict)


# ---------------- entity ----------------

def compare_entity(ref: NormalizedEntity, ans: NormalizedEntity) -> ComparisonOutcome:
    if ref.parse_status != "ok" or ans.parse_status != "ok":
        reasons = [
            s for s in (ref.ambiguity_reason, ans.ambiguity_reason) if s
        ]
        return ComparisonOutcome(
            comparison_type="ambiguous",
            score=None,
            reason="; ".join(reasons) or "entity not reliably normalized on both sides",
            detail={"reference_status": ref.parse_status, "answer_status": ans.parse_status},
        )
    if ref.canonical == ans.canonical:
        return ComparisonOutcome(
            comparison_type="match", score=_MATCH,
            detail={"canonical": ref.canonical},
        )
    return ComparisonOutcome(
        comparison_type="entity_mismatch",
        score=_MISMATCH,
        detail={"reference_canonical": ref.canonical, "answer_canonical": ans.canonical},
    )


# ---------------- temporal ----------------

def _relation(r: tuple[str, str], a: tuple[str, str]) -> str:
    r_start, r_end = r
    a_start, a_end = a
    if a_end < r_start or r_end < a_start:
        return "disjoint"
    if (r_start <= a_start and a_end <= r_end) or (a_start <= r_start and r_end <= a_end):
        return "contained"
    return "overlap"


def compare_temporal(ref: NormalizedTemporal, ans: NormalizedTemporal) -> ComparisonOutcome:
    detail = {
        "reference_interval": [ref.interval_start, ref.interval_end],
        "answer_interval": [ans.interval_start, ans.interval_end],
        "reference_granularity": ref.granularity,
        "answer_granularity": ans.granularity,
    }
    if ref.parse_status != "ok" or ans.parse_status != "ok":
        reasons = [s for s in (ref.ambiguity_reason, ans.ambiguity_reason) if s]
        return ComparisonOutcome(
            comparison_type="ambiguous", score=None,
            reason="; ".join(reasons) or "temporal not reliably normalized on both sides",
            detail=detail,
        )

    r = (ref.interval_start, ref.interval_end)
    a = (ans.interval_start, ans.interval_end)
    detail["relation"] = _relation(r, a)
    if r == a:
        # semantic equivalence (same normalized interval), e.g.
        # 2024年 ↔ 2024年度, 2024Q1 ↔ Q1 2024
        return ComparisonOutcome(comparison_type="match", score=_MATCH, detail=detail)
    if detail["relation"] == "disjoint":
        return ComparisonOutcome(
            comparison_type="temporal_mismatch", score=_MISMATCH, detail=detail
        )
    # containment / overlap: auxiliary info kept; NOT judged as match (decision)
    return ComparisonOutcome(
        comparison_type="ambiguous",
        score=None,
        reason=f"intervals {detail['relation']} but not semantically equivalent (MVP: not a match)",
        detail=detail,
    )


# ---------------- numerical ----------------

def _unit_class(unit: str | None) -> str:
    """Comparison class for a NORMALIZED unit token.

    Derived from _UNIT_TABLE (numerical.py) rather than re-listed, so the table's
    currency metadata and this classification cannot drift apart — see the note
    on UNIT_CLASS_BY_UNIT for the English/CJK unit_mismatch bug that drift caused.
    """
    return UNIT_CLASS_BY_UNIT.get(unit or "", "unitless")


def _base(n: NormalizedNumerical) -> float | None:
    if n.value is None or n.scale_to_base is None:
        return n.value
    return n.value * n.scale_to_base


def compare_numerical(
    ref: NormalizedNumerical,
    ans: NormalizedNumerical,
    *,
    absolute_tolerance: float | None = None,
    relative_tolerance: float | None = None,
) -> ComparisonOutcome:
    """Order (Spec): normalization -> unit/scale/currency alignment -> tolerance
    -> comparison. Tolerances are comparison-mechanism parameters supplied by
    the caller (Profile) — the constants are float-noise guards only."""
    abs_tol = DEFAULT_ABSOLUTE_TOLERANCE if absolute_tolerance is None else absolute_tolerance
    rel_tol = DEFAULT_RELATIVE_TOLERANCE if relative_tolerance is None else relative_tolerance
    detail: dict = {
        "reference": {"value": ref.value, "unit": ref.unit, "currency": ref.currency, "precision": ref.precision},
        "answer": {"value": ans.value, "unit": ans.unit, "currency": ans.currency, "precision": ans.precision},
        "tolerance": {"absolute": abs_tol, "relative": rel_tol},
    }

    if ref.parse_status != "ok" or ans.parse_status != "ok":
        reasons = [s for s in (ref.ambiguity_reason, ans.ambiguity_reason) if s]
        return ComparisonOutcome(
            comparison_type="ambiguous", score=None,
            reason="; ".join(reasons) or "numerical not reliably normalized on both sides",
            detail=detail,
        )

    r_class, a_class = _unit_class(ref.unit), _unit_class(ans.unit)
    detail["unit_class"] = {"reference": r_class, "answer": a_class}
    if r_class != a_class:
        return ComparisonOutcome(
            comparison_type="unit_mismatch", score=None,
            reason=f"unit class mismatch: {r_class} vs {a_class} (not comparable)",
            detail=detail,
        )

    ref_base, ans_base = _base(ref), _base(ans)
    if ref_base is None or ans_base is None:
        return ComparisonOutcome(
            comparison_type="ambiguous", score=None, reason="missing base value", detail=detail
        )
    # Sign is semantically meaningful: +50 vs -50 are opposite facts (growth vs
    # decline in finance), never "the same magnitude". Different non-zero signs
    # are a mismatch outright — do not fall through to tolerance (which uses
    # absolute difference and would call them equal).
    if ref_base * ans_base < 0:
        detail["base"] = {
            "reference": ref_base, "answer": ans_base,
            "abs_diff": abs(ref_base - ans_base), "sign_mismatch": True,
        }
        return ComparisonOutcome(
            comparison_type="value_mismatch", score=_MISMATCH, detail=detail,
            reason="sign differs: reference and answer have opposite signs",
        )
    diff = abs(ref_base - ans_base)
    detail["base"] = {"reference": ref_base, "answer": ans_base, "abs_diff": diff}

    if diff <= max(abs_tol, rel_tol * abs(ref_base)):
        detail["representation_differs"] = ref.unit != ans.unit
        return ComparisonOutcome(
            comparison_type="match", score=_MATCH, detail=detail
        )
    # same written value but different scale (1亿元 vs 1万元) -> scale_mismatch
    written_equal = ref.value is not None and ans.value is not None and abs(ref.value - ans.value) <= max(abs_tol, rel_tol * abs(ref.value))
    if written_equal and ref.unit != ans.unit:
        return ComparisonOutcome(
            comparison_type="scale_mismatch", score=_MISMATCH, detail=detail
        )
    return ComparisonOutcome(
        comparison_type="value_mismatch", score=_MISMATCH, detail=detail
    )
