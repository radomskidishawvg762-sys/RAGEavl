"""English magnitude words are scale, not currency, unless a currency word is adjacent."""

from __future__ import annotations

from app.metrics.integrity.comparison import compare_numerical
from app.metrics.integrity.numerical import extract_numerical, normalize_numerical


def test_bare_million_is_not_cny_and_does_not_match_yuan() -> None:
    bare = normalize_numerical("5 million")
    assert bare.parse_status == "ok"
    assert bare.currency is None
    assert bare.scale_to_base == 1e6
    out = compare_numerical(bare, normalize_numerical("500万元"))
    assert out.comparison_type == "unit_mismatch"
    assert out.score is None


def test_million_adjacent_to_cny_is_money() -> None:
    stated = normalize_numerical("5 million 元")
    assert stated.currency == "CNY"
    out = compare_numerical(stated, normalize_numerical("500万元"))
    assert out.comparison_type == "match"
    assert out.score == 1.0
    prefixed = normalize_numerical("CNY 5 million")
    assert prefixed.currency == "CNY"


def test_extract_keeps_adjacent_currency_and_ignores_a_distant_one() -> None:
    adjacent = extract_numerical("营收 5 million 元")
    assert len(adjacent) == 1
    assert adjacent[0].currency == "CNY"
    distant = extract_numerical("5 million，后来提到元")
    assert len(distant) == 1
    assert distant[0].currency is None


def test_million_next_to_foreign_currency_stays_ambiguous() -> None:
    parsed = normalize_numerical("5 million USD")
    assert parsed.parse_status == "ambiguous"
    assert "cross-currency" in (parsed.ambiguity_reason or "")
