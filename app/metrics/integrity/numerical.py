"""Numerical normalization (T-09).

Extraction + normalization ONLY — comparison (match/mismatch, tolerance
application) is T-10. Output carries value / unit / scale-to-base /
currency / precision so T-10 can compare in a common space.

MVP unit scope (Spec): 元, 万元, 百万元, 亿元, 万亿元, %, ‰.
Anything outside the scope (吨, 件, …) or a non-CNY currency marker (美元/USD/
…) -> ambiguous (Spec: cross-currency enters ambiguous; never guess an FX
rate). Numbers without a unit are ok with unit=None (T-10 compares only
like units).

Tolerance note: absolute_tolerance / relative_tolerance are COMPARISON
mechanism parameters (T-10, supplied from the Evaluation Profile). The
constants below are implementation-level float-noise guards — mathematical
defaults, NOT financial industry thresholds (platform ships zero default
thresholds, PRD Q3).
"""

from __future__ import annotations

import re

from app.metrics.integrity.base import NormalizedBase
from app.metrics.integrity.chinese_number import parse_chinese_number

# Implementation-level float guards ONLY (math defaults, not business thresholds).
DEFAULT_ABSOLUTE_TOLERANCE = 1e-9
DEFAULT_RELATIVE_TOLERANCE = 1e-6

# unit token -> (normalized unit, scale factor to base yuan, currency)
# English magnitude words are normalized into the SAME base-yuan space as the
# CJK tokens (million == 百万元 == 1e6). case-insensitive matching is handled
# by lowering the candidate token before lookup.
_UNIT_TABLE: dict[str, tuple[str, float, str | None]] = {
    "元": ("元", 1.0, "CNY"),
    "万元": ("万元", 1e4, "CNY"),
    "百万元": ("百万元", 1e6, "CNY"),
    "亿元": ("亿元", 1e8, "CNY"),
    "万亿元": ("万亿元", 1e12, "CNY"),
    "%": ("percent", 1e-2, None),
    "％": ("percent", 1e-2, None),
    "‰": ("permille", 1e-3, None),
    "thousand": ("thousand", 1e3, "CNY"),
    "million": ("million", 1e6, "CNY"),
    "billion": ("billion", 1e9, "CNY"),
    "trillion": ("trillion", 1e12, "CNY"),
    "percent": ("percent", 1e-2, None),
    "per cent": ("percent", 1e-2, None),
}

# Comparison class per NORMALIZED unit token, derived from _UNIT_TABLE instead of
# re-listed, so the two cannot drift apart again. They did drift: the comparison
# layer hardcoded only the CJK currency tokens, so "thousand"/"million"/
# "billion"/"trillion" — which _UNIT_TABLE normalizes into the SAME base-yuan
# space as 百万元/亿元 — were classified "unitless". Any English-vs-CJK amount
# ("5 million" vs "500万元", both 5_000_000) therefore hit the unit-class gate and
# came back a CONFIRMED unit_mismatch failure on an answer that was correct.
_RATIO_UNITS = frozenset({"percent", "permille"})
UNIT_CLASS_BY_UNIT: dict[str, str] = {
    normalized: ("ratio" if normalized in _RATIO_UNITS else "currency" if currency else "unitless")
    for normalized, _scale, currency in _UNIT_TABLE.values()
}

_UNIT_ALTERNATION = "|".join(sorted(_UNIT_TABLE, key=len, reverse=True))

_FOREIGN_CURRENCY = re.compile(r"(美元|欧元|日元|港币|USD|EUR|JPY|HKD)", re.IGNORECASE)

_ARABIC = re.compile(
    rf"(?P<num>-?\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_ALTERNATION})?",
    re.IGNORECASE,
)
_CN_NUM_FULL = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二三四五六七八九]+)?")
_CN_PERCENT = re.compile(r"(?P<word>百分之|千分之)(?P<num>[零〇一二两三四五六七八九十点]+)")


class NormalizedNumerical(NormalizedBase):
    kind: str = "numerical"
    value: float | None = None  # the number as written (before scaling)
    unit: str | None = None  # normalized unit token ("元"/"万元"/"percent"/"permille"/None)
    scale_to_base: float | None = None  # multiply value by this to reach base yuan / ratio
    currency: str | None = None  # "CNY" for yuan-family; None otherwise
    precision: int | None = None  # decimal places as written (informational, T-10 hint)


def _precision_of(num_text: str) -> int:
    _, dot, decimals = num_text.partition(".")
    return len(decimals) if dot else 0


def _clean_number(num_text: str) -> float:
    return float(num_text.replace(",", ""))


def normalize_numerical(text: str) -> NormalizedNumerical:
    """Normalize the FIRST numeric expression found. No reliable parse ->
    ambiguous/unparsed; never a guess."""
    raw = text.strip()
    if not raw:
        return NormalizedNumerical(raw=text, parse_status="ambiguous", ambiguity_reason="empty input")

    m = _CN_PERCENT.search(raw)
    if m:
        value = parse_chinese_number(m.group("num"))
        if value is not None:
            unit = "percent" if m.group("word") == "百分之" else "permille"
            scale = _UNIT_TABLE["%" if unit == "percent" else "‰"][1]
            return NormalizedNumerical(
                raw=raw, parse_status="ok", value=value, unit=unit,
                scale_to_base=scale, currency=None,
                precision=0,
            )

    foreign = _FOREIGN_CURRENCY.search(raw)
    if foreign:
        return NormalizedNumerical(
            raw=raw,
            parse_status="ambiguous",
            ambiguity_reason=f"cross-currency not supported in MVP: {foreign.group(1)} (Spec)",
            metadata={"currency_detected": foreign.group(1).upper()},
        )

    m = _ARABIC.search(raw)
    if m:
        unit_token = m.group("unit")
        # A CJK/letter char right after the number that is not a known unit.
        # CJK unit (吨/件/...) -> ambiguous (Spec: unit outside scope, never guess).
        # ASCII letter -> NOT a unit: in English text the char after a bare
        # number is usually the first letter of the next word ("23.70 per hour"
        # -> "p"). Treating it as an unknown unit marks otherwise-valid figures
        # ambiguous. A bare number + ASCII letter is a valid unitless value.
        after = raw[m.end() : m.end() + 1]
        if not unit_token and after and re.match(r"[一-鿿]", after):
            return NormalizedNumerical(
                raw=raw,
                parse_status="ambiguous",
                ambiguity_reason=f"unit outside MVP scope: {after}",
            )
        if unit_token:
            unit, scale, currency = _UNIT_TABLE[unit_token.lower()]
            value = _clean_number(m.group("num"))
            return NormalizedNumerical(
                raw=raw, parse_status="ok", value=value, unit=unit,
                scale_to_base=scale, currency=currency,
                precision=_precision_of(m.group("num")),
            )
        value = _clean_number(m.group("num"))
        return NormalizedNumerical(
            raw=raw, parse_status="ok", value=value, unit=None, scale_to_base=None,
            currency=None, precision=_precision_of(m.group("num")),
        )

    # Chinese number + known unit: locate the unit token in text; the part
    # before it must be a complete Chinese numeral (avoids greedy class
    # swallowing 亿/万 that belong to the UNIT, e.g. 一亿元).
    for token in sorted((t for t in _UNIT_TABLE if t not in ("%", "％", "‰")), key=len, reverse=True):
        idx = raw.find(token)
        if idx <= 0:
            continue
        num_part = raw[:idx].strip()
        if _CN_NUM_FULL.fullmatch(num_part):
            value = parse_chinese_number(num_part)
            if value is None:
                return NormalizedNumerical(
                    raw=raw, parse_status="ambiguous",
                    ambiguity_reason="unparseable Chinese numeral expression",
                )
            unit, scale, currency = _UNIT_TABLE[token]
            return NormalizedNumerical(
                raw=raw, parse_status="ok", value=value, unit=unit,
                scale_to_base=scale, currency=currency, precision=0,
            )

    # bare Chinese number (no known unit)
    m = _CN_NUM_FULL.search(raw)
    if m:
        after = raw[m.end() : m.end() + 1]
        if after and re.match(r"[一-鿿A-Za-z]", after):
            return NormalizedNumerical(
                raw=raw,
                parse_status="ambiguous",
                ambiguity_reason=f"unit outside MVP scope: {after}",
            )
        value = parse_chinese_number(m.group(0))
        if value is None:
            return NormalizedNumerical(
                raw=raw, parse_status="ambiguous",
                ambiguity_reason="unparseable Chinese numeral expression",
            )
        return NormalizedNumerical(
            raw=raw, parse_status="ok", value=value, unit=None, scale_to_base=None,
            currency=None, precision=0,
        )

    return NormalizedNumerical(raw=text, parse_status="unparsed", ambiguity_reason="no numeric expression")


def extract_numerical(text: str) -> list[NormalizedNumerical]:
    """All numeric candidates, in text order (arabic + Chinese + CN percent).

    Trailing-context rule: a BARE number (no unit) immediately followed by
    年/月/日 is a date/year COMPONENT — temporal-domain content (T-09 temporal
    handles it), never a numerical claim, and is NOT emitted here. A bare
    number followed by any other CJK/letter char keeps that char as trailing
    context so the unit-scope check still fires (100吨 -> ambiguous), instead
    of silently degrading to a unitless ok value because the span cut the
    unit off.
    """
    results: list[NormalizedNumerical] = []
    spans: list[tuple[int, int]] = []
    _DATE_COMPONENT_SUFFIX = ("年", "月", "日")

    def _overlaps(m: re.Match[str]) -> bool:
        return any(not (m.end() <= a or m.start() >= b) for a, b in spans)

    def _with_context(candidate: str, trailing: str) -> str:
        if trailing and re.match(r"[一-鿿A-Za-z]", trailing):
            return candidate + trailing  # keep unit-scope signal attached
        return candidate

    # 1) Chinese percent (collected first so 百分之X wins its span)
    for m in _CN_PERCENT.finditer(text):
        if _overlaps(m):
            continue
        spans.append((m.start(), m.end()))
        results.append(normalize_numerical(m.group(0)))
    # 2) arabic — unit-aware single pass
    for m in _ARABIC.finditer(text):
        if _overlaps(m):
            continue
        has_unit = m.group("unit") is not None
        trailing = "" if has_unit else text[m.end() : m.end() + 1]
        if not has_unit and trailing in _DATE_COMPONENT_SUFFIX:
            continue  # bare year/date component -> temporal domain
        spans.append((m.start(), m.end()))
        results.append(normalize_numerical(_with_context(m.group(0), trailing)))
    # 3) Chinese numerals
    for m in _CN_NUM_FULL.finditer(text):
        if _overlaps(m):
            continue
        trailing = text[m.end() : m.end() + 1]
        if trailing in _DATE_COMPONENT_SUFFIX:
            continue  # bare year/date component (二〇二四年 / 三月) -> temporal
        spans.append((m.start(), m.end()))
        results.append(normalize_numerical(_with_context(m.group(0), trailing)))
    return sorted(results, key=lambda r: text.find(r.raw) if r.raw in text else len(text))
