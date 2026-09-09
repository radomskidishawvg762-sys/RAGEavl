"""Temporal normalization (T-09).

Raw strings are NEVER compared (no string equality on dates, Spec red line).
Every resolvable expression maps to a normalized ISO-date interval
[inclusive start, inclusive end]; granularity records what the expression
denotes (year / fiscal_year / quarter / reporting_period / date).

Supported: year (2024年 / 2024年度), fiscal year (FY2024 / FY24 / 2024财年 —
mapped to the calendar year, fiscal offsets are company-specific config and
NOT guessed), quarter (Q1 2024 / 2024Q1 / 2024年第一季度), reporting periods
(上半年/下半年/全年 with explicit year), concrete dates (2024年3月15日 /
2024-03-15 / 2024/3/15), as-of dates (截至/截止 …).

Relative expressions without an anchor (去年/今年/上一报告期/报告期内) and
anything unparseable -> ambiguous. Never guess.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from app.metrics.integrity.base import NormalizedBase

Granularity = Literal["year", "fiscal_year", "quarter", "reporting_period", "date"]

_CN_Q = {"一": 1, "二": 2, "三": 3, "四": 4}
_RELATIVE_TERMS = ("去年", "今年", "明年", "上一年度", "上一报告期", "报告期内", "最近", "最新")

_MONTH_DAYS = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        return 29
    return _MONTH_DAYS[month]


class NormalizedTemporal(NormalizedBase):
    kind: str = "temporal"
    interval_start: str | None = None  # ISO date, inclusive
    interval_end: str | None = None  # ISO date, inclusive
    granularity: Granularity | None = None


def _make(
    year: int,
    start: tuple[int, int],
    end: tuple[int, int],
    granularity: Granularity,
    raw: str,
    metadata: dict | None = None,
) -> NormalizedTemporal:
    return NormalizedTemporal(
        raw=raw,
        parse_status="ok",
        interval_start=date(year, *start).isoformat(),
        interval_end=date(year, *end).isoformat(),
        granularity=granularity,
        metadata=metadata or {},
    )


_PATTERNS: list[tuple[re.Pattern[str], Granularity]] = [
    # concrete date: 截至YYYY年M月D日 / YYYY-M-D / YYYY/M/D / YYYY年M月D日
    (
        re.compile(
            r"(?:截至|截止(?:至)?)?\s*(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?"
        ),
        "date",
    ),
    # quarter: 2024Q1 / Q1 2024 / 2024年第一季度 / 2024年第1季度 / 2024年Q1
    (
        re.compile(
            r"(?:Q([1-4])\s*(\d{4})|(\d{4})\s*年?\s*(?:第?([一二三四1-4])季度|Q([1-4])))",
            re.IGNORECASE,
        ),
        "quarter",
    ),
    # fiscal year: FY2024 / FY24 / 2024财年
    (re.compile(r"(?:FY\s?(\d{2,4})|(\d{4})\s*财年)", re.IGNORECASE), "fiscal_year"),
    # reporting half/year: 2024年上半年 / 2024年下半年 / 2024年全年 / 2024年 / 2024年度
    (
        re.compile(
            r"(\d{4})\s*年(?:度)?\s*(上半年|下半年|全年)?"
        ),
        "year",
    ),
]

_RELATIVE_ONLY = re.compile(
    "(去年|今年|明年|上一年度|上一报告期|报告期内|最近|最新)"
)


def _two_digit_year(y: int) -> int:
    """FY24 -> 2024 (decided rule: two-digit FY maps into 2000-2099)."""
    return 2000 + y if y < 100 else y


def normalize_temporal(text: str) -> NormalizedTemporal:
    raw = text.strip()
    if not raw:
        return NormalizedTemporal(
            raw=text, parse_status="ambiguous", ambiguity_reason="empty temporal expression"
        )
    for pattern, granularity in _PATTERNS:
        m = pattern.search(raw)
        if not m:
            continue
        groups = m.groups()
        if granularity == "date":
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            if not (1 <= month <= 12 and 1 <= day <= _days_in_month(year, month)):
                break  # fall through to later patterns rather than guess
            as_of = "截至" in m.group(0) or "截止" in m.group(0)
            return _make(year, (month, day), (month, day), "date", raw,
                         metadata={"as_of": as_of})
        if granularity == "quarter":
            if groups[0] is not None:  # Q1 2024 form
                q, year = int(groups[0]), _two_digit_year(int(groups[1]))
            else:  # 2024年Q1 / 2024年第一季度
                year = int(groups[2])
                q_token = groups[3] or groups[4]
                q = int(q_token) if str(q_token).isdigit() else _CN_Q[str(q_token)]
            start_month = (q - 1) * 3 + 1
            return _make(year, (start_month, 1), (start_month + 2, _days_in_month(year, start_month + 2)),
                         "quarter", raw)
        if granularity == "fiscal_year":
            y = int(groups[0]) if groups[0] is not None else int(groups[1])
            year = _two_digit_year(y)
            return _make(year, (1, 1), (12, 31), "fiscal_year", raw,
                         metadata={"fiscal": True,
                                   "note": "mapped to calendar year; fiscal offsets are company-specific config"})
        if granularity == "year":
            year = int(groups[0])
            suffix = groups[1]
            if suffix == "上半年":
                return _make(year, (1, 1), (6, 30), "reporting_period", raw)
            if suffix == "下半年":
                return _make(year, (7, 1), (12, 31), "reporting_period", raw)
            meta = {"full_year": True} if suffix == "全年" else {}
            return _make(year, (1, 1), (12, 31), "year", raw, metadata=meta)

    rel = _RELATIVE_ONLY.search(raw)
    if rel:
        return NormalizedTemporal(
            raw=text,
            parse_status="ambiguous",
            ambiguity_reason=f"relative time without anchor: {rel.group(1)}",
        )
    return NormalizedTemporal(
        raw=text, parse_status="unparsed", ambiguity_reason="no recognizable temporal expression"
    )


def extract_temporal(text: str) -> list[NormalizedTemporal]:
    """All distinct temporal candidates in text (one entry per pattern hit)."""
    results: list[NormalizedTemporal] = []
    seen_spans: list[tuple[int, int]] = []
    for pattern, _ in _PATTERNS:
        for m in pattern.finditer(text):
            if any(not (m.end() <= a or m.start() >= b) for a, b in seen_spans):
                continue
            seen_spans.append((m.start(), m.end()))
            results.append(normalize_temporal(m.group(0)))
    if not results and _RELATIVE_ONLY.search(text):
        results.append(normalize_temporal(text))
    return results
