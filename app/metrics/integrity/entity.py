"""Entity normalization (T-09).

No knowledge graph (Spec A.6). Candidates come from (a) alias-dictionary
occurrences in text and (b) quoted/bracketed spans. Canonical resolution is
alias-table driven with the Spec A.6.2 precedence: explicit canonical in
metadata > alias dictionary > string-key equality. Anything else -> ambiguous.

Alias table shape: {alias: canonical}. Caller-supplied (domain data, not a
platform default); tests pass their own.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.metrics.integrity.base import NormalizedBase, normalize_text_key

_QUOTE_PATTERN = re.compile(r"[「『《\"“']([^」』》\"”']{1,32})[」』》\"”']")


class NormalizedEntity(NormalizedBase):
    kind: str = "entity"
    canonical: str | None = None
    matched_form: str | None = None  # the alias/canonical form actually matched


def _fold_alias_table(
    alias_table: dict[str, str],
) -> tuple[dict[str, set[str]], set[str]]:
    """alias -> set(canonical) folded by normalize_text_key; conflicting aliases
    (same key, different canonicals) are kept as multi-canonical sets so the
    normalizer can report ambiguity instead of guessing."""
    by_alias: dict[str, set[str]] = defaultdict(set)
    for alias, canonical in alias_table.items():
        by_alias[normalize_text_key(alias)].add(canonical)
    canonicals = {normalize_text_key(c) for c in alias_table.values()}
    return dict(by_alias), canonicals


def _resolve(canonical_key: str, canonical_display: dict[str, str]) -> str | None:
    """Return a display form for the folded canonical key when available."""
    return canonical_display.get(canonical_key)


def normalize_entity(
    text: str, *, alias_table: dict[str, str] | None = None
) -> NormalizedEntity:
    """Canonicalize ONE entity mention. Unknown/uncertain -> ambiguous."""
    table = alias_table or {}
    by_alias, canonical_keys = _fold_alias_table(table)
    display = {normalize_text_key(c): c for c in table.values()}

    key = normalize_text_key(text)
    if not key:
        return NormalizedEntity(
            raw=text, parse_status="ambiguous", ambiguity_reason="empty entity mention"
        )
    if key in by_alias:
        hits = by_alias[key]
        if len(hits) > 1:
            return NormalizedEntity(
                raw=text,
                parse_status="ambiguous",
                ambiguity_reason=f"alias maps to multiple canonicals: {sorted(hits)}",
                matched_form=text,
            )
        canonical_key = next(iter(hits))
        return NormalizedEntity(
            raw=text,
            parse_status="ok",
            canonical=_resolve(canonical_key, display) or canonical_key,
            matched_form=text,
        )
    if key in canonical_keys:
        return NormalizedEntity(
            raw=text, parse_status="ok", canonical=_resolve(key, display) or key, matched_form=text
        )
    return NormalizedEntity(
        raw=text,
        parse_status="ambiguous",
        ambiguity_reason="entity not in alias table (no knowledge graph in MVP)",
        matched_form=text,
    )


def extract_entities(
    text: str, *, alias_table: dict[str, str] | None = None
) -> list[NormalizedEntity]:
    """Extract candidate entity mentions from free text and normalize each.

    Candidates: known alias/canonical occurrences (longest first) + quoted or
    bracketed spans. Unclassifiable raw runs are NOT emitted as candidates
    (no NER in MVP) — callers compare what they can see.
    """
    table = alias_table or {}
    by_alias, canonical_keys = _fold_alias_table(table)
    folded_text = normalize_text_key(text)

    spans: list[tuple[int, int, str]] = []
    # 1) dictionary occurrences — match against folded text; longest alias first
    for key in sorted(set(by_alias) | canonical_keys, key=len, reverse=True):
        if not key:
            continue
        start = 0
        while True:
            idx = folded_text.find(key, start)
            if idx < 0:
                break
            spans.append((idx, idx + len(key), key))
            start = idx + len(key)
    # 2) quoted/bracketed spans
    for m in _QUOTE_PATTERN.finditer(text):
        spans.append((m.start(1), m.end(1), m.group(1)))

    if not spans:
        return []

    # de-duplicate overlaps, longest span wins, keep text order
    spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    chosen: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    for s, e, form in spans:
        if any(not (e <= a or s >= b) for a, b in taken):
            continue
        taken.append((s, e))
        chosen.append((s, e, form))
    chosen.sort(key=lambda s: s[0])

    results: list[NormalizedEntity] = []
    for _s, _e, form in chosen:
        results.append(normalize_entity(form, alias_table=table))
    return results
