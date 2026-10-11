"""Temporal consistency: year+month granularity, text-order candidates, and
multi-value records that cannot be paired deterministically."""

from __future__ import annotations

import asyncio

import pytest

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.metrics.integrity import extract_temporal
from app.metrics.integrity.comparison import compare_temporal
from app.metrics.integrity.temporal import normalize_temporal


def _temporal(reference: str, answer: str):
    record = EvaluationRecord(
        id="r1", question="q", contexts=["c"], answer=answer, reference_answer=reference,
    )
    return asyncio.run(
        IntegrityEngine().evaluate(record, ["temporal_consistency"], EvalParams())
    )[0]


@pytest.mark.parametrize(("raw", "start", "end"), [
    ("2024年3月", "2024-03-01", "2024-03-31"),
    ("2024年02月", "2024-02-01", "2024-02-29"),
    ("2023-02", "2023-02-01", "2023-02-28"),
    ("2024-12", "2024-12-01", "2024-12-31"),
])
def test_year_month_normalizes_to_month_interval(raw: str, start: str, end: str) -> None:
    n = normalize_temporal(raw)
    assert n.parse_status == "ok"
    assert n.granularity == "month"
    assert (n.interval_start, n.interval_end) == (start, end)


def test_different_months_of_same_year_are_a_mismatch() -> None:
    out = compare_temporal(normalize_temporal("2024年3月"), normalize_temporal("2024年5月"))
    assert out.comparison_type == "temporal_mismatch"
    assert out.score == 0.0


def test_engine_flags_different_months_as_inconsistent() -> None:
    r = _temporal("2024年3月营收1亿元", "2024年5月营收1亿元")
    assert r.comparison_basis.comparison_type == "temporal_mismatch"
    assert r.score == 0.0


def test_same_month_in_both_notations_matches() -> None:
    r = _temporal("2024年3月营收1亿元", "2024-03营收1亿元")
    assert r.comparison_basis.comparison_type == "match"
    assert r.score == 1.0


def test_full_dates_and_year_ranges_are_not_read_as_months() -> None:
    assert normalize_temporal("2024-03-15").granularity == "date"
    assert normalize_temporal("2024年3月15日").granularity == "date"
    assert [t.granularity for t in extract_temporal("2023-2024年")] == ["year"]
    assert normalize_temporal("2024-13").granularity != "month"


def test_candidates_are_returned_in_text_order() -> None:
    items = extract_temporal("2024年全年营收增长，其中2024Q1表现最好，截至2024年3月31日")
    assert [i.granularity for i in items] == ["year", "quarter", "date"]


def test_multi_value_record_that_cannot_be_paired_is_ambiguous() -> None:
    # reference states two years; answer states one: first-vs-first would
    # compare 2023 against 2024 and report a false temporal_mismatch.
    r = _temporal("2023年和2024年营收均为1亿元", "2024年营收为1亿元")
    assert r.comparison_basis.comparison_type == "ambiguous"
    assert r.score is None and r.passed is None


def test_multi_value_with_identical_interval_sets_matches() -> None:
    r = _temporal("2023年营收1亿元，2024年营收2亿元", "2024年营收2亿元，2023年营收1亿元")
    assert r.comparison_basis.comparison_type == "match"
    assert r.score == 1.0


def test_repeated_same_interval_still_compares_as_single_value() -> None:
    r = _temporal("2024年营收1亿元，2024年净利润1000万元", "2023年营收1亿元")
    assert r.comparison_basis.comparison_type == "temporal_mismatch"
    assert r.score == 0.0
