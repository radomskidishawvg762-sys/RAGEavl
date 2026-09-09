"""Deterministic multi-candidate pairing for numerical_consistency (T-10 v2).

Locks the strict-unique-affinity pairing semantics:
  - reorder / multi-value references no longer produce false value_mismatch
    (first-ok selection bug);
  - only UNIQUE reliable pairings are compared — ties stay ambiguous;
  - cross unit-class never pairs; no-signal stays ambiguous;
  - multiset-equality fallback fires only when pairing is impossible AND the
    answer states exactly the reference numbers;
  - single ok-candidate records keep the legacy fast-path comparison shape.
"""

from __future__ import annotations

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import INTEGRITY_METRIC_VERSION, IntegrityEngine
from app.metrics.integrity.numerical import NormalizedNumerical, extract_numerical
from app.metrics.integrity.pairing import (
    MULTISET_STRATEGY,
    PAIRING_STRATEGY,
    pair_or_fallback,
)

ALIASES = {"平安银行": "平安银行"}


def _record(reference_answer: str, answer: str, record_id: str = "r1") -> EvaluationRecord:
    return EvaluationRecord(
        id=record_id, question="q", contexts=["c"], answer=answer,
        reference_answer=reference_answer,
    )


def _evaluate(reference_answer: str, answer: str):
    import asyncio

    engine = IntegrityEngine(alias_table=ALIASES)
    results = asyncio.run(
        engine.evaluate(_record(reference_answer, answer), ["numerical_consistency"], EvalParams())
    )
    return results[0]


# ---------------- engine-level regression: reorder / mis-binding ----------------

def test_reorder_no_longer_false_mismatch() -> None:
    """Legacy first-ok selection compared 净利润4500万元 against 营收2.3亿元 and
    reported a false value_mismatch; strict pairing binds by unit token."""
    result = _evaluate("净利润4500万元，营收2.3亿元", "营收2.3亿元，净利润4500万元")
    assert result.score == 1.0
    assert result.comparison_basis.comparison_type == "match"
    pairing = result.comparison_basis.diff["pairing"]
    assert pairing["strategy"] == PAIRING_STRATEGY
    assert len(pairing["couples"]) == 2
    assert all(c["score"] == 1.0 for c in pairing["couples"])


def test_reference_multi_value_answer_subset_unique_pairing() -> None:
    """Legacy: ref[0]=2.3亿元 vs ans[0]=4500万元 -> false value_mismatch.
    Now: 4500万元 pairs by unit token + position -> match (omission of 营收
    value is incompleteness, not numerical inconsistency)."""
    result = _evaluate("营收2.3亿元，净利润4500万元", "净利润4500万元")
    assert result.score == 1.0
    pairing = result.comparison_basis.diff["pairing"]
    assert pairing["unpaired_reference_count"] == 1


def test_pairing_mismatch_is_detected_with_decisive_couple() -> None:
    result = _evaluate("净利润4500万元，营收2.3亿元", "净利润4600万元")
    assert result.score == 0.0
    basis = result.comparison_basis
    assert basis.comparison_type == "value_mismatch"
    assert basis.diff["decisive"]["base"]["abs_diff"] == 1000000.0


def test_temporal_anchor_binds_same_period_value() -> None:
    result = _evaluate("2024年营收2.3亿元，2023年营收1.8亿元", "2023年营收1.8亿元")
    assert result.score == 1.0


# ---------------- strict uniqueness: ambiguity is preserved ----------------

def test_equidistant_answer_value_is_ambiguous_not_guessed() -> None:
    """Answer value exactly between two reference values: position signal
    cancels out (contribution 0 both ways), ties are NOT broken -> ambiguous
    (never a guess). Module level — the geometry must be exact."""
    ref = [_num("100", 100.0), _num("200", 200.0)]
    ans = [_num("200", 200.0)]
    out = pair_or_fallback(
        ref, ans,
        ref_text="100xxxxxx200",      # rel positions 0.0 and 0.75
        ans_text="xxxxx200xxxxx",     # rel position 0.385 — 0 contribution both
        alias_table={},
        absolute_tolerance=None,
        relative_tolerance=None,
    )
    assert out.comparison_type == "ambiguous" and out.score is None
    assert "ties across reference candidates" in out.reason


def test_duplicate_answer_claims_stay_ambiguous() -> None:
    """Two answer values claiming the same reference candidate: injectivity
    violated -> ambiguous (multiset differs)."""
    result = _evaluate("数值100", "100，100")
    assert result.score is None
    assert result.comparison_basis.comparison_type == "ambiguous"


def test_cross_unit_class_answer_value_is_ambiguous() -> None:
    """A percent answer value against currency-only reference: no positively
    matching candidate -> ambiguous, never unit-guessed."""
    result = _evaluate("营收100元，利润200元", "增长50%")
    assert result.score is None
    assert result.comparison_basis.comparison_type == "ambiguous"


def test_tie_between_identical_reference_candidates_resolves() -> None:
    """Affinity tie across content-identical reference candidates is
    outcome-invariant -> deterministic lowest-index pairing -> match."""
    result = _evaluate("100万元与100万元", "100万元")
    assert result.score == 1.0
    assert result.comparison_basis.comparison_type == "match"


# ---------------- multiset-equality fallback ----------------

def _num(raw: str, value: float) -> NormalizedNumerical:
    return NormalizedNumerical(raw=raw, parse_status="ok", value=value)


def test_multiset_equality_fallback_when_pairing_impossible() -> None:
    """All position signals beyond decay range -> affinity ties -> pairing
    impossible, but the answer states exactly the reference numbers -> match
    via multiset equality (exact base values, unit-class aware)."""
    ref = [_num("100", 100.0), _num("200", 200.0)]
    ans = [_num("200", 200.0), _num("100", 100.0)]
    out = pair_or_fallback(
        ref, ans,
        ref_text="100xxxxxx200",       # rel positions 0.0 and 0.75
        ans_text="xxxxx200100xxxxx",   # rel positions 0.3125 and 0.5
        alias_table={},
        absolute_tolerance=None,
        relative_tolerance=None,
    )
    assert out.comparison_type == "match" and out.score == 1.0
    assert out.detail["pairing"]["strategy"] == MULTISET_STRATEGY


def test_multiset_fallback_does_not_fire_on_different_values() -> None:
    ref = [_num("100", 100.0), _num("200", 200.0)]
    ans = [_num("300", 300.0), _num("100", 100.0)]
    out = pair_or_fallback(
        ref, ans,
        ref_text="100xxxxxx200",
        ans_text="xxxxx300100xxxxx",
        alias_table={},
        absolute_tolerance=None,
        relative_tolerance=None,
    )
    assert out.comparison_type == "ambiguous" and out.score is None


# ---------------- fast path unchanged ----------------

def test_single_ok_candidates_keep_legacy_fast_path() -> None:
    """Exactly one ok candidate per side: legacy comparison shape, no
    pairing block in the basis diff."""
    result = _evaluate("营收1亿元", "营收1亿元")
    assert result.score == 1.0
    assert "pairing" not in (result.comparison_basis.diff or {})


def test_metric_version_is_v2() -> None:
    assert INTEGRITY_METRIC_VERSION == "integrity-normalization-v2"


# ---------------- pairing module-level: extract -> pair pipeline ----------------

def test_pairing_uses_only_ok_candidates() -> None:
    """Ambiguous candidates (e.g. 100吨 outside unit scope) never enter the
    pairing pool and never silently degrade the outcome."""
    ref_items = extract_numerical("产量100吨，产值200万元")
    ans_items = extract_numerical("产值200万元")
    ref_oks = [i for i in ref_items if i.parse_status == "ok"]
    ans_oks = [i for i in ans_items if i.parse_status == "ok"]
    assert len(ref_oks) == 1 and len(ans_oks) == 1
    out = pair_or_fallback(
        ref_oks, ans_oks, ref_text="产量100吨，产值200万元", ans_text="产值200万元",
        alias_table={}, absolute_tolerance=None, relative_tolerance=None,
    )
    assert out.comparison_type == "match" and out.score == 1.0
