"""T-09 integrity normalization tests. Pure functions — no DB, no Judge LLM,
no comparison decisions (T-10's job)."""

from __future__ import annotations

from app.metrics.integrity import (
    extract_entities,
    extract_numerical,
    normalize_entity,
    normalize_numerical,
    normalize_temporal,
)
from app.metrics.integrity.base import normalize_text_key
from app.metrics.integrity.numerical import (
    DEFAULT_ABSOLUTE_TOLERANCE,
    DEFAULT_RELATIVE_TOLERANCE,
)

ALIASES = {"中国平安": "中国平安", "平安": "中国平安", "Ping An": "中国平安", "pingan": "中国平安"}


# ---------------- entity ----------------

def test_entity_alias_variants_canonicalize() -> None:
    for mention in ("中国平安", "平安", "Ping An", "ping  an", "PINGAN"):
        r = normalize_entity(mention, alias_table=ALIASES)
        assert r.parse_status == "ok", f"{mention} -> {r}"
        assert r.canonical == "中国平安"


def test_entity_extraction_finds_mentions_in_text() -> None:
    items = extract_entities("平安发布的年报，Ping An 的市值……", alias_table=ALIASES)
    canonicals = [i.canonical for i in items if i.parse_status == "ok"]
    assert canonicals == ["中国平安", "中国平安"]


def test_entity_unknown_is_ambiguous_never_guessed() -> None:
    r = normalize_entity("某某不知名主体", alias_table=ALIASES)
    assert r.parse_status == "ambiguous"
    assert r.canonical is None
    assert "not in alias table" in r.ambiguity_reason


def test_entity_conflicting_alias_is_ambiguous() -> None:
    conflicting = {"Nova": "甲公司", "nova": "乙公司"}
    r = normalize_entity("nova", alias_table=conflicting)
    assert r.parse_status == "ambiguous"
    assert "multiple canonicals" in r.ambiguity_reason


def test_text_key_normalization_basics() -> None:
    assert normalize_text_key("  ＡＢＣ   def ") == "abc def"  # NFKC full-width fold


# ---------------- temporal ----------------

def test_year_and_annual_same_interval() -> None:
    for raw in ("2024年", "2024年度"):
        r = normalize_temporal(raw)
        assert r.parse_status == "ok"
        assert (r.interval_start, r.interval_end) == ("2024-01-01", "2024-12-31")
        assert r.granularity == "year"


def test_fiscal_year_maps_to_calendar_with_flag() -> None:
    for raw in ("FY2024", "fy24", "2024财年"):
        r = normalize_temporal(raw)
        assert r.parse_status == "ok"
        assert r.granularity == "fiscal_year"
        assert (r.interval_start, r.interval_end) == ("2024-01-01", "2024-12-31")
        assert r.metadata.get("fiscal") is True


def test_quarter_intervals() -> None:
    cases = {"Q1 2024": ("2024-01-01", "2024-03-31"), "2024Q1": ("2024-01-01", "2024-03-31"),
             "2024年第一季度": ("2024-01-01", "2024-03-31"), "2024年第四季度": ("2024-10-01", "2024-12-31")}
    for raw, expected in cases.items():
        r = normalize_temporal(raw)
        assert r.parse_status == "ok", raw
        assert (r.interval_start, r.interval_end) == expected
        assert r.granularity == "quarter"


def test_reporting_period_and_as_of_date() -> None:
    r = normalize_temporal("2024年上半年")
    assert (r.interval_start, r.interval_end) == ("2024-01-01", "2024-06-30")
    assert r.granularity == "reporting_period"

    r = normalize_temporal("截至2024年6月30日")
    assert r.parse_status == "ok"
    assert (r.interval_start, r.interval_end) == ("2024-06-30", "2024-06-30")
    assert r.granularity == "date" and r.metadata.get("as_of") is True

    r = normalize_temporal("截止2024-12-31")
    assert (r.interval_start, r.interval_end) == ("2024-12-31", "2024-12-31")

    r = normalize_temporal("2024年3月15日")
    assert (r.interval_start, r.interval_end) == ("2024-03-15", "2024-03-15")


def test_relative_temporal_is_ambiguous() -> None:
    for raw in ("去年", "今年", "上一报告期", "报告期内"):
        r = normalize_temporal(raw)
        assert r.parse_status == "ambiguous", raw
        assert "anchor" in (r.ambiguity_reason or "")


def test_no_temporal_expression_is_unparsed() -> None:
    r = normalize_temporal("营收增长强劲")
    assert r.parse_status == "unparsed"


def test_temporal_output_is_interval_not_raw_string() -> None:
    """Structural guarantee: comparison input is a normalized interval, never
    the raw string (no string equality possible downstream)."""
    r = normalize_temporal("2024年度")
    assert r.interval_start and r.interval_end  # ISO dates
    assert r.raw != r.interval_start


# ---------------- numerical ----------------

def test_unit_cross_representation_same_base_value() -> None:
    bases = []
    for raw in ("1亿元", "10000万元", "100000000元"):
        r = normalize_numerical(raw)
        assert r.parse_status == "ok", raw
        assert r.currency == "CNY"
        bases.append(r.value * r.scale_to_base)
    assert bases == [1e8, 1e8, 1e8]
    assert [r.unit for r in extract_numerical("1亿元")] == ["亿元"]


def test_precision_information_recorded() -> None:
    for raw, precision in (("100.0", 1), ("100", 0), ("100.00", 2)):
        r = normalize_numerical(raw)
        assert r.parse_status == "ok"
        assert r.value == 100.0
        assert r.precision == precision
        assert r.unit is None  # unitless — T-10 compares like units only


def test_percent_and_permille() -> None:
    r = normalize_numerical("5%")
    assert (r.unit, r.value, r.scale_to_base) == ("percent", 5.0, 1e-2)
    r = normalize_numerical("5‰")
    assert (r.unit, r.value, r.scale_to_base) == ("permille", 5.0, 1e-3)
    r = normalize_numerical("百分之五")
    assert (r.unit, r.value) == ("percent", 5.0)
    r = normalize_numerical("千分之三")
    assert (r.unit, r.value) == ("permille", 3.0)


def test_chinese_numerals() -> None:
    cases = {"一亿元": (1.0, "亿元"), "两千万": (20_000_000.0, None), "十五": (15.0, None),
             "一亿二千万": (120_000_000.0, None), "三点五亿元": (3.5, "亿元")}
    for raw, (value, unit) in cases.items():
        r = normalize_numerical(raw)
        assert r.parse_status == "ok", f"{raw} -> {r}"
        assert r.value == value, raw
        assert r.unit == unit, raw
        if r.scale_to_base is not None:
            assert r.value * r.scale_to_base > 0
    r = normalize_numerical("一亿二千万")  # explicit base check
    assert r.value == 120_000_000.0


def test_thousands_separators() -> None:
    r = normalize_numerical("1,234,567元")
    assert r.parse_status == "ok"
    assert r.value == 1_234_567.0
    assert r.value * r.scale_to_base == 1_234_567.0


def test_unknown_unit_is_ambiguous() -> None:
    r = normalize_numerical("100吨")
    assert r.parse_status == "ambiguous"
    assert "outside MVP scope" in r.ambiguity_reason


def test_cross_currency_is_ambiguous_per_spec() -> None:
    for raw in ("100万美元", "5 million USD"):
        r = normalize_numerical(raw)
        assert r.parse_status == "ambiguous", raw
        assert "cross-currency" in r.ambiguity_reason


def test_no_number_is_unparsed() -> None:
    assert normalize_numerical("没有数字").parse_status == "unparsed"


def test_extract_multiple_numerics_in_order() -> None:
    items = extract_numerical("营收1,000万元，同比增长5%，其中港元部分忽略")
    units = [i.unit for i in items]
    assert units == ["万元", "percent"]


# ---------------- T-10 boundary & tolerance positioning ----------------

def test_normalizer_output_feeds_comparison_basis() -> None:
    """T-09 provides standardized sides; T-10 decides match/mismatch."""
    ref = normalize_numerical("1亿元")
    ans = normalize_numerical("10000万元")
    # both sides serialize into ComparisonBasis.reference/answer dicts (M1 schema)
    side = {"items": [ref.model_dump()]}
    assert side["items"][0]["unit"] == "亿元"
    assert ref.value * ref.scale_to_base == ans.value * ans.scale_to_base
    # no match/mismatch field exists on normalizer output
    assert not hasattr(ref, "comparison_type")
    assert not hasattr(ref, "passed")


def test_tolerances_are_math_defaults_not_business_thresholds() -> None:
    """The only tolerance constants in T-09 are implementation-level float
    guards; Profile-supplied tolerances are consumed by T-10, never here."""
    assert DEFAULT_ABSOLUTE_TOLERANCE == 1e-9
    assert DEFAULT_RELATIVE_TOLERANCE == 1e-6


# ---------------- sign preservation (layer-1 contamination fix) ----------------

def test_negative_sign_preserved_in_parse() -> None:
    r = normalize_numerical("-12.6")
    assert r.parse_status == "ok"
    assert r.value == -12.6


def test_positive_sign_preserved_in_parse() -> None:
    r = normalize_numerical("+50")
    assert r.parse_status == "ok"
    assert r.value == 50.0


# ---------------- english magnitude units (layer-1 contamination fix) ----------------

def test_english_million_normalized() -> None:
    r = normalize_numerical("12.6 million")
    assert r.parse_status == "ok"
    assert r.unit == "million"
    assert r.scale_to_base == 1e6


def test_english_million_case_insensitive() -> None:
    r = normalize_numerical("12.6 Million")
    assert r.parse_status == "ok"
    assert r.value == 12.6 and r.scale_to_base == 1e6


def test_english_thousand_and_billion_scales() -> None:
    assert normalize_numerical("1000 thousand").scale_to_base == 1e3
    assert normalize_numerical("1 billion").scale_to_base == 1e9
    assert normalize_numerical("1 trillion").scale_to_base == 1e12


def test_english_percent_word_normalized_like_symbol() -> None:
    r = normalize_numerical("6.67 percent")
    assert r.parse_status == "ok"
    assert r.unit == "percent" and r.scale_to_base == 1e-2
