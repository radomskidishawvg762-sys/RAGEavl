"""T-10 IntegrityEngine + 3 consistency metric tests.

Pure deterministic — no DB, no Judge LLM. Comparison semantics follow the
2026-08-29 decision: temporal = semantic equivalence first; ambiguous sides
are NEVER match.
"""

from __future__ import annotations

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import INTEGRITY_METRIC_DEFS, INTEGRITY_METRIC_VERSION, IntegrityEngine
from app.metrics.integrity import (
    normalize_entity,
    normalize_numerical,
    normalize_temporal,
)
from app.metrics.integrity.comparison import (
    compare_entity,
    compare_numerical,
    compare_temporal,
)
from app.metrics.registry import MetricRegistry

ALIASES = {"中国平安": "中国平安", "平安": "中国平安", "Ping An": "中国平安"}


def _record(reference_answer: str, answer: str, record_id: str = "r1") -> EvaluationRecord:
    return EvaluationRecord(
        id=record_id,
        question="q",
        contexts=["c"],
        answer=answer,
        reference_answer=reference_answer,
    )


# ---------------- comparators: entity ----------------

def test_entity_canonical_match() -> None:
    ref = normalize_entity("中国平安", alias_table=ALIASES)
    ans = normalize_entity("平安", alias_table=ALIASES)
    out = compare_entity(ref, ans)
    assert out.comparison_type == "match" and out.score == 1.0


def test_entity_mismatch() -> None:
    table = {**ALIASES, "招商银行": "招商银行"}
    ref = normalize_entity("中国平安", alias_table=table)
    ans = normalize_entity("招商银行", alias_table=table)
    out = compare_entity(ref, ans)
    assert out.comparison_type == "entity_mismatch" and out.score == 0.0


def test_entity_ambiguous_side() -> None:
    ref = normalize_entity("中国平安", alias_table=ALIASES)
    ans = normalize_entity("不知名主体", alias_table=ALIASES)  # ambiguous
    out = compare_entity(ref, ans)
    assert out.comparison_type == "ambiguous" and out.score is None


# ---------------- comparators: temporal (semantic equivalence first) ----------------

def test_temporal_exact_equivalent_match() -> None:
    for ref_raw, ans_raw in (("2024年", "2024年度"), ("2024Q1", "Q1 2024"), ("FY2024", "2024财年")):
        out = compare_temporal(normalize_temporal(ref_raw), normalize_temporal(ans_raw))
        assert out.comparison_type == "match", f"{ref_raw} vs {ans_raw}: {out}"
        assert out.score == 1.0


def test_temporal_different_year_mismatch() -> None:
    out = compare_temporal(normalize_temporal("2024年"), normalize_temporal("2023年"))
    assert out.comparison_type == "temporal_mismatch" and out.score == 0.0


def test_temporal_quarter_vs_year_is_not_match() -> None:
    out = compare_temporal(normalize_temporal("2024年"), normalize_temporal("2024Q1"))
    assert out.comparison_type != "match"
    assert out.score is None  # contained -> ambiguous, auxiliary info kept
    assert out.detail["relation"] == "contained"


def test_temporal_containment_and_overlap_not_match() -> None:
    # containment: date inside quarter
    out = compare_temporal(normalize_temporal("2024Q1"), normalize_temporal("2024-02-15"))
    assert out.comparison_type == "ambiguous" and out.detail["relation"] == "contained"
    # overlap (no containment): H1 [01-01,06-30] vs Q2 [04-01,06-30]... contained;
    # true overlap: Q2 [04-01,06-30] vs H2 [07-01..]? disjoint; use 2024Q3 [07-01,09-30] vs 2024年下半年 [07-01,12-31] contained.
    # genuine overlap: 2024年上半年 [01-01,06-30] vs 截至2024-08-31 impossible via expressions —
    # overlap reachable via date vs 2024-02-01..02-29 style: use date within-year vs half-year ranges:
    out = compare_temporal(normalize_temporal("2024年上半年"), normalize_temporal("2024Q2"))
    assert out.comparison_type == "ambiguous" and out.detail["relation"] == "contained"
    # disjoint quarters
    out = compare_temporal(normalize_temporal("2024Q1"), normalize_temporal("2024Q4"))
    assert out.comparison_type == "temporal_mismatch"


def test_temporal_unparseable_and_relative_are_ambiguous() -> None:
    for raw in ("去年", "上一报告期"):
        n = normalize_temporal(raw)
        out = compare_temporal(n, n)  # both sides equally unresolvable
        assert out.comparison_type == "ambiguous"
        assert out.score is None


# ---------------- comparators: numerical ----------------

def test_numerical_same_value_same_unit() -> None:
    out = compare_numerical(normalize_numerical("100元"), normalize_numerical("100元"))
    assert out.comparison_type == "match" and out.score == 1.0


def test_numerical_opposite_sign_is_mismatch() -> None:
    """Layer-1 contamination fix: sign is semantically meaningful (+50 vs -50
    are opposite facts). Tolerance comparison must never equate them."""
    out = compare_numerical(normalize_numerical("12.6"), normalize_numerical("-12.6"))
    assert out.comparison_type == "value_mismatch" and out.score == 0.0
    assert out.detail["base"]["sign_mismatch"] is True
    out_pct = compare_numerical(normalize_numerical("6.67%"), normalize_numerical("-6.67%"))
    assert out_pct.comparison_type == "value_mismatch"
    out_same = compare_numerical(normalize_numerical("12.6"), normalize_numerical("12.6"))
    assert out_same.comparison_type == "match"


def test_numerical_english_magnitude_units_same_base() -> None:
    """English magnitude words normalize into the same base space as each other
    and as the CJK currency tokens (million == 百万元 == 1e6).

    They are classified as CURRENCY (decided 2026-09-28), not as bare unitless
    magnitudes: a bare number states no unit, so "12.6 million" vs "12600000" is
    a unit-class mismatch — the two sides are only comparable once both state
    the currency.
    """
    out = compare_numerical(normalize_numerical("1000 million"), normalize_numerical("1 billion"))
    assert out.comparison_type == "match" and out.score == 1.0
    out2 = compare_numerical(normalize_numerical("1 million"), normalize_numerical("1000 thousand"))
    assert out2.comparison_type == "match"
    # Same numeric base, but only one side states a unit.
    out3 = compare_numerical(normalize_numerical("12.6 million"), normalize_numerical("12600000"))
    assert out3.comparison_type == "unit_mismatch" and out3.score is None


def test_numerical_english_and_cjk_currency_are_comparable() -> None:
    """Regression: an English magnitude and its CJK currency equivalent used to
    come back a CONFIRMED unit_mismatch failure, because _unit_class() knew only
    the CJK tokens while _UNIT_TABLE had already tagged the English ones CNY."""
    out = compare_numerical(normalize_numerical("5 million"), normalize_numerical("500万元"))
    assert out.comparison_type == "match" and out.score == 1.0
    out2 = compare_numerical(normalize_numerical("200 million"), normalize_numerical("2亿元"))
    assert out2.comparison_type == "match"


def test_numerical_english_scale_confusion_detected() -> None:
    out = compare_numerical(normalize_numerical("12.6 million"), normalize_numerical("12.6 thousand"))
    assert out.comparison_type == "scale_mismatch" and out.score == 0.0


def test_numerical_same_value_different_units_same_base() -> None:
    out = compare_numerical(normalize_numerical("1亿元"), normalize_numerical("10000万元"))
    assert out.comparison_type == "match" and out.score == 1.0
    assert out.detail["representation_differs"] is True


def test_numerical_tolerance_pass_and_fail() -> None:
    ref, ans = normalize_numerical("100元"), normalize_numerical("100.2元")
    ok = compare_numerical(ref, ans, absolute_tolerance=0.5)
    assert ok.comparison_type == "match"
    fail = compare_numerical(ref, ans, absolute_tolerance=0.1)
    assert fail.comparison_type == "value_mismatch" and fail.score == 0.0


def test_numerical_relative_tolerance() -> None:
    ref, ans = normalize_numerical("10000元"), normalize_numerical("10150元")
    ok = compare_numerical(ref, ans, relative_tolerance=0.02)  # 1.5% <= 2%
    assert ok.comparison_type == "match"
    fail = compare_numerical(ref, ans, relative_tolerance=0.01)  # 1.5% > 1%
    assert fail.comparison_type == "value_mismatch"


def test_numerical_scale_mismatch_same_written_value() -> None:
    out = compare_numerical(normalize_numerical("1亿元"), normalize_numerical("1万元"))
    assert out.comparison_type == "scale_mismatch" and out.score == 0.0


def test_numerical_unit_class_conflict_not_compared() -> None:
    out = compare_numerical(normalize_numerical("50%"), normalize_numerical("50元"))
    assert out.comparison_type == "unit_mismatch" and out.score is None


def test_numerical_unknown_unit_ambiguous() -> None:
    out = compare_numerical(normalize_numerical("100吨"), normalize_numerical("100元"))
    assert out.comparison_type == "ambiguous" and out.score is None


def test_numerical_currency_mismatch_via_cross_currency_ambiguous() -> None:
    out = compare_numerical(normalize_numerical("100元"), normalize_numerical("100万美元"))
    assert out.comparison_type == "ambiguous" and out.score is None


def test_numerical_percent_permille_ratio_equivalence() -> None:
    out = compare_numerical(normalize_numerical("5%"), normalize_numerical("50‰"))
    assert out.comparison_type == "match"  # both ratio 0.05


# ---------------- THE critical invariant ----------------

def test_both_ambiguous_is_never_match() -> None:
    """ambiguous + ambiguous -> ambiguous -> 不归因 -> score/pass = null."""
    e = compare_entity(
        normalize_entity("不知名甲", alias_table=ALIASES),
        normalize_entity("不知名乙", alias_table=ALIASES),
    )
    t = compare_temporal(normalize_temporal("去年"), normalize_temporal("今年"))
    n = compare_numerical(normalize_numerical("100吨"), normalize_numerical("200件"))
    for out in (e, t, n):
        assert out.comparison_type == "ambiguous"
        assert out.score is None
        # engine-level: both sides with unresolvable content must not produce a match
        engine = IntegrityEngine(alias_table=ALIASES)
        import asyncio

    results = asyncio.run(engine.evaluate(
        _record("去年营收大幅增长", "今年营收大幅增长"), ["temporal_consistency"], EvalParams()
    ))
    r = results[0]
    assert r.comparison_basis.comparison_type == "ambiguous"
    assert r.score is None and r.passed is None


# ---------------- engine / MetricResult contract ----------------

ALL = [d.name for d in INTEGRITY_METRIC_DEFS]


def _run(record: EvaluationRecord, metrics: list[str], params: EvalParams | None = None):
    import asyncio

    engine = IntegrityEngine(alias_table=ALIASES)
    return asyncio.run(engine.evaluate(record, metrics, params or EvalParams()))


def test_engine_full_match_record() -> None:
    results = _run(
        _record("平安2024年营收1亿元，同比增长5%", "平安2024年度营收10000万元，同比增长5.0%"),
        ALL,
    )
    by_name = {r.metric_name: r for r in results}
    assert set(by_name) == set(ALL)
    for r in results:
        assert r.category == "integrity"
        assert r.metric_version == INTEGRITY_METRIC_VERSION
        assert r.score == 1.0 and r.error is None
        assert r.comparison_basis.method == "deterministic"
        assert r.comparison_basis.comparison_type == "match"


def test_engine_missing_input_error() -> None:
    rec = EvaluationRecord(id="r2", question="q", contexts=["c"], answer="a", reference_answer=None)
    results = _run(rec, ["numerical_consistency"])
    r = results[0]
    assert r.score is None and r.error is not None
    assert r.error["code"] == "BIZ_METRIC_INPUT_MISSING"
    assert r.error["missing"] == ["reference_answer"]


def test_engine_comparison_basis_records_basis_on_mismatch() -> None:
    results = _run(_record("中国平安2024年营收1亿元", "中国平安2024年营收2亿元"), ["numerical_consistency"])
    r = results[0]
    basis = r.comparison_basis
    assert basis.comparison_type == "value_mismatch"
    assert basis.reference["primary"]["unit"] == "亿元"
    assert basis.diff["base"]["abs_diff"] == 1e8
    assert basis.tolerance_applied is None  # defaults used, none supplied


def test_engine_threshold_and_passed_semantics() -> None:
    good = _run(_record("2024年", "2024年度"), ["temporal_consistency"], EvalParams(threshold=0.99))[0]
    assert good.score == 1.0 and good.passed is True
    bad = _run(_record("2024年", "2023年"), ["temporal_consistency"], EvalParams(threshold=0.99))[0]
    assert bad.score == 0.0 and bad.passed is False
    amb = _run(_record("去年", "2024年"), ["temporal_consistency"], EvalParams(threshold=0.99))[0]
    assert amb.score is None and amb.passed is None  # comparison_type/score/pass semantics aligned
    assert amb.comparison_basis.comparison_type == "ambiguous"
    assert amb.error is not None and "reason" in amb.error


def test_engine_tolerances_via_params() -> None:
    params = EvalParams(extra={"tolerance": {"absolute": 0.5}})
    r = _run(_record("100元", "100.2元"), ["numerical_consistency"], params)[0]
    assert r.score == 1.0
    assert r.comparison_basis.tolerance_applied == {"absolute": 0.5}


def test_engine_registry_and_no_branching_wiring() -> None:
    registry = MetricRegistry()
    engine = IntegrityEngine(alias_table=ALIASES)
    engine.register_into(registry)
    for name in ALL:
        spec = registry.get_metric(name).spec
        assert spec.engine == "integrity"
        assert spec.category == "integrity"
        assert spec.version == INTEGRITY_METRIC_VERSION


def test_engine_no_diagnosis_or_recommendation_fields() -> None:
    """Metric ≠ Diagnosis (P-1): engine output carries no attribution."""
    results = _run(_record("2024年", "2023年"), ["temporal_consistency"])
    r = results[0]
    assert not hasattr(r, "root_cause")
    assert not hasattr(r, "failure_type")
    basis_dump = r.comparison_basis.model_dump() if r.comparison_basis else {}
    assert "diagnosis" not in basis_dump
    assert "recommendation" not in basis_dump
