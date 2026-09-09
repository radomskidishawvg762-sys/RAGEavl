"""Shared foundations for integrity normalization (T-09)."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel

ParseStatus = Literal["ok", "ambiguous", "unparsed"]


def normalize_text_key(text: str) -> str:
    """Base normalization: NFKC + trim + collapse inner whitespace + casefold.
    Deterministic; used by entity matching and canonical keys."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).strip()).casefold()


class NormalizedBase(BaseModel):
    """Common contract consumed by T-10 Comparison (one side of a pair)."""

    kind: str
    raw: str
    parse_status: ParseStatus
    ambiguity_reason: str | None = None
    metadata: dict = {}


def ambiguity(kind: type, raw: str, reason: str, **extra) -> BaseModel:
    """Build an ambiguous result — the ONLY outcome when unsure (never guess)."""
    return kind(raw=raw, parse_status="ambiguous", ambiguity_reason=reason, **extra)
