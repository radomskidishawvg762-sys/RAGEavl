"""Chinese numeral parsing for T-09 numerical normalization.

Supports 零〇一二两三四五六七八九十百千万亿 + 点 (decimal). Positional
algorithm: split by 亿, then 万, accumulate 千百十 within each segment.
Pure function, no dependencies.
"""

from __future__ import annotations

import re

_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUMBER_RE = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二三四五六七八九]+)?")


def _parse_small(segment: str) -> float:
    """Parse a segment < 1万: e.g. 十五 -> 15, 三千零五 -> 3005, 两百 -> 200."""
    total = 0.0
    current = 0.0
    seen_digit = False
    for ch in segment:
        if ch in _DIGITS:
            current = float(_DIGITS[ch])
            seen_digit = True
        elif ch in _SMALL_UNITS:
            unit = _SMALL_UNITS[ch]
            if current == 0 and not seen_digit and unit == 10:
                current = 1.0  # leading 十: 十五 = 15
            total += current * unit
            current = 0.0
        else:
            return None  # type: ignore[return-value]
    return total + current


def parse_chinese_number(text: str) -> float | None:
    """一亿二千万 -> 120_000_000; 两千万 -> 20_000_000; 十五 -> 15; 三点五 -> 3.5."""
    text = text.strip()
    if not text:
        return None
    integer_part, dot, decimal_part = text.partition("点")

    s = integer_part
    if not s:
        return None
    total = 0.0
    if "亿" in s:
        before, s = s.split("亿", 1)
        head = _parse_small(before) if before else 1.0  # bare 亿 = 1亿
        if head is None:
            return None
        total += head * 100_000_000
    if "万" in s:
        before, s = s.split("万", 1)
        head = _parse_small(before) if before else 1.0  # bare 万 = 1万
        if head is None:
            return None
        total += head * 10_000
    if s:
        tail = _parse_small(s)
        if tail is None:
            return None
        total += tail

    if dot:
        if not decimal_part or not all(ch in _DIGITS for ch in decimal_part):
            return None
        fraction = 0.0
        scale = 0.1
        for ch in decimal_part:
            fraction += _DIGITS[ch] * scale
            scale *= 0.1
        total += fraction
    return total
