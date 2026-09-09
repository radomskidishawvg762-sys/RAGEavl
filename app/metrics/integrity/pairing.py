"""Deterministic multi-candidate pairing for numerical_consistency (T-10).

WHY: extract_numerical returns ALL numeric candidates in text order, but a
record-level comparison needs to know WHICH reference value answers WHICH
answer value. "First ok vs first ok" silently mis-binds when either side
states several numbers (reference "净利润4500万元，营收2.3亿元" vs answer
"营收2.3亿元，净利润4500万元" would compare 4500万 against 2.3亿 and report a
false value_mismatch).

DESIGN (minimal, deterministic, evidence-first — no LLM, no new metric,
no registry change):

  affinity(ref_i, ans_j) — all signals deterministic text facts:
    +2.0  identical unit token (both non-None, e.g. 万元 == 万元)
    +1.0  identical unit class (ratio / currency / unitless)
    +1.5  shared temporal anchor — the same normalized interval occurs in the
          sentences containing both candidates
    +1.5  shared entity anchor — the same canonical form (alias-table driven)
          occurs in both sentences
    +0.5·max(0, 1 − 4·|rel_ref − rel_ans|)  relative text-position closeness
  Pairs with affinity <= 0 are NOT eligible (cross unit-class never pairs;
  compare_numerical could not judge them anyway).

  Pairing is STRICT and all-or-nothing per record:
    - every answer candidate must take its unique strictly-best reference
      (affinity ties are allowed ONLY between content-identical reference
      candidates — compare_numerical calls the choice outcome-invariant;
      lowest index wins for determinism);
    - no two answer candidates may claim the same reference (injective);
    - every answer claim must be paired; reference-only values (omission)
      are incompleteness, NOT numerical inconsistency, and stay out of scope
      for this metric (P1 completeness signal, not silently forgiven).
  When pairing succeeds, each couple is compared by compare_numerical with
  the Profile tolerances. Aggregation: the first (lowest reference index)
  couple with score 0.0 decides (value_mismatch / scale_mismatch, score 0.0);
  otherwise every couple matches -> match (1.0). Couples within one unit
  class are always judgeable, so no mixed null-score aggregation exists.

  When strict pairing is impossible:
    - exact multiset equality of (unit_class, base value) on both sides
      -> match ("multiset-equality-v1"): the answer states exactly the
      reference numbers and no label binding is knowable — exact float
      equality only, no tolerance semantics;
    - otherwise -> ambiguous with a recorded reason (tie / no signal /
      duplicate claim). Ambiguous is NEVER converted into a score.

  Exactly one ok candidate per side bypasses pairing entirely: the legacy
  single-pair comparison shape (comparison_basis.diff) is unchanged.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.metrics.integrity.base import normalize_text_key
from app.metrics.integrity.comparison import (
    ComparisonTypeStr,
    _unit_class,
    compare_numerical,
)
from app.metrics.integrity.entity import extract_entities
from app.metrics.integrity.numerical import NormalizedNumerical
from app.metrics.integrity.temporal import extract_temporal

PAIRING_STRATEGY = "strict-unique-affinity-v1"
MULTISET_STRATEGY = "multiset-equality-v1"

# Affinity weights (implementation constants — signal RELIABILITY ordering,
# not quality thresholds; no platform default thresholds are involved).
_W_UNIT_TOKEN = 2.0
_W_UNIT_CLASS = 1.0
_W_TEMPORAL_ANCHOR = 1.5
_W_ENTITY_ANCHOR = 1.5
_W_POSITION = 0.5
_POSITION_DECAY = 4.0  # 0 contribution when relative positions differ by >= 0.25

# Sentence segmentation. '.' is deliberately NOT a separator: decimal points
# ("23.70") must never split a segment.
_SEGMENT_SEP = re.compile(r"[。！？!?；;\n\r]+")


class NumericalPairingOutcome(BaseModel):
    """Record-level outcome after (optional) pairing — same contract as
    ComparisonOutcome so IntegrityEngine can consume either unchanged."""

    comparison_type: ComparisonTypeStr
    score: float | None
    reason: str | None = None
    detail: dict = Field(default_factory=dict)


def _segments(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SEGMENT_SEP.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def _segment_of(spans: list[tuple[int, int]], pos: int) -> int:
    for i, (a, b) in enumerate(spans):
        if a <= pos < b:
            return i
    return len(spans) - 1


def _candidate_offsets(items: list[NormalizedNumerical], text: str) -> list[int]:
    """Char offset of each candidate, scanning sequentially (candidates are
    text-ordered; raws may repeat). Unfindable raws clamp to 0 (deterministic)."""
    offsets: list[int] = []
    cursor = 0
    for item in items:
        pos = text.find(item.raw, cursor)
        if pos < 0:
            pos = text.find(item.raw)
        offsets.append(pos if pos >= 0 else 0)
        cursor = pos + len(item.raw)
    return offsets


class _SideAnchors:
    """Per-segment temporal/entity anchor sets, computed lazily and cached."""

    def __init__(self, text: str, alias_table: dict[str, str]) -> None:
        self._text = text
        self._spans = _segments(text)
        self._alias_table = alias_table or {}
        self._cache: dict[int, tuple[frozenset, frozenset]] = {}

    def segment_of(self, pos: int) -> int:
        return _segment_of(self._spans, pos)

    def anchors(self, segment: int) -> tuple[frozenset, frozenset]:
        if segment not in self._cache:
            a, b = self._spans[segment]
            chunk = self._text[a:b]
            temporal = frozenset(
                (t.interval_start, t.interval_end)
                for t in extract_temporal(chunk)
                if t.parse_status == "ok" and t.interval_start and t.interval_end
            )
            entities = frozenset(
                normalize_text_key(e.canonical)
                for e in extract_entities(chunk, alias_table=self._alias_table)
                if e.parse_status == "ok" and e.canonical
            )
            self._cache[segment] = (temporal, entities)
        return self._cache[segment]


def _affinity(
    ref: NormalizedNumerical,
    ans: NormalizedNumerical,
    *,
    rel_ref: float,
    rel_ans: float,
    ref_anchors: tuple[frozenset, frozenset],
    ans_anchors: tuple[frozenset, frozenset],
) -> float:
    if _unit_class(ref.unit) != _unit_class(ans.unit):
        # Cross unit-class never pairs: compare_numerical could not judge the
        # couple (unit_mismatch), so binding it would only relabel ambiguity.
        return 0.0
    score = 0.0
    if ref.unit is not None and ans.unit is not None and ref.unit == ans.unit:
        score += _W_UNIT_TOKEN
    score += _W_UNIT_CLASS
    if ref_anchors[0] & ans_anchors[0]:
        score += _W_TEMPORAL_ANCHOR
    if ref_anchors[1] & ans_anchors[1]:
        score += _W_ENTITY_ANCHOR
    score += _W_POSITION * max(0.0, 1.0 - _POSITION_DECAY * abs(rel_ref - rel_ans))
    return score


def _multiset(items: list[NormalizedNumerical]) -> list[tuple[str, float]]:
    return sorted(
        (_unit_class(i.unit), i.value * i.scale_to_base if i.scale_to_base is not None else i.value)
        for i in items
        if i.value is not None
    )


def _ambiguous(reason: str, detail: dict) -> NumericalPairingOutcome:
    return NumericalPairingOutcome(
        comparison_type="ambiguous", score=None, reason=reason, detail=detail
    )


def _couple_summary(
    ref: NormalizedNumerical, ans: NormalizedNumerical, outcome
) -> dict:
    return {
        "reference_raw": ref.raw,
        "answer_raw": ans.raw,
        "comparison_type": outcome.comparison_type,
        "score": outcome.score,
    }


def pair_and_compare_numerical(
    ref_oks: list[NormalizedNumerical],
    ans_oks: list[NormalizedNumerical],
    *,
    ref_text: str,
    ans_text: str,
    alias_table: dict[str, str],
    absolute_tolerance: float | None,
    relative_tolerance: float | None,
) -> NumericalPairingOutcome:
    """Strict unique pairing over ok candidates, then per-couple comparison.
    All-or-nothing per record: any ambiguity -> ambiguous (never a guess)."""
    detail: dict = {"pairing": {"strategy": PAIRING_STRATEGY}}
    if not ref_oks or not ans_oks:
        # Defensive: the engine routes empty sides to the missing/ambiguous
        # branches and never reaches pairing; kept safe for direct callers.
        return _ambiguous(
            "no ok numerical candidate on "
            f"{'reference' if not ref_oks else 'answer'} side", detail
        )
    ref_anchors = _SideAnchors(ref_text, alias_table)
    ans_anchors = _SideAnchors(ans_text, alias_table)
    ref_offsets = _candidate_offsets(ref_oks, ref_text)
    ans_offsets = _candidate_offsets(ans_oks, ans_text)

    affinity: list[list[float]] = []
    for i, r in enumerate(ref_oks):
        r_seg = ref_anchors.segment_of(ref_offsets[i])
        r_anchor = ref_anchors.anchors(r_seg)
        row: list[float] = []
        for j, a in enumerate(ans_oks):
            a_seg = ans_anchors.segment_of(ans_offsets[j])
            a_anchor = ans_anchors.anchors(a_seg)
            row.append(
                _affinity(
                    r, a,
                    rel_ref=ref_offsets[i] / max(1, len(ref_text)),
                    rel_ans=ans_offsets[j] / max(1, len(ans_text)),
                    ref_anchors=r_anchor,
                    ans_anchors=a_anchor,
                )
            )
        affinity.append(row)

    detail["pairing"]["affinity"] = {
        r.raw: {a.raw: affinity[i][j] for j, a in enumerate(ans_oks)}
        for i, r in enumerate(ref_oks)
    }

    def _best_ref_for_answer(j: int) -> tuple[float, list[int]]:
        """Max affinity and its winner indices among reference candidates
        for answer candidate j."""
        best = max(affinity[i][j] for i in range(len(ref_oks)))
        winners = sorted({i for i in range(len(ref_oks)) if affinity[i][j] == best})
        return best, winners

    def _content_identical(left, right) -> bool:
        return compare_numerical(
            left, right,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
        ).score == 1.0

    def _resolve_tie(candidates: list, winners: list[int]) -> int | None:
        """Tie between content-identical candidates is outcome-invariant:
        deterministically keep the lowest index. A real tie -> None."""
        if len(winners) == 1:
            return winners[0]
        first = candidates[winners[0]]
        if all(_content_identical(first, candidates[w]) for w in winners[1:]):
            return winners[0]
        return None

    # answer candidate -> chosen reference index
    mapping: dict[int, int] = {}
    for j in range(len(ans_oks)):
        best, ref_winners = _best_ref_for_answer(j)
        if best <= 0.0:
            return _ambiguous(
                f"answer value {ans_oks[j].raw!r} has no positively-matching "
                "reference candidate (no unit/anchor/position signal)",
                detail,
            )
        chosen = _resolve_tie(ref_oks, ref_winners)
        if chosen is None:
            return _ambiguous(
                "multiple pairings plausible: answer value "
                f"{ans_oks[j].raw!r} ties across reference candidates",
                detail,
            )
        mapping[j] = chosen

    if len(set(mapping.values())) != len(mapping):
        return _ambiguous(
            "multiple pairings plausible: answer values claim the same "
            "reference candidate",
            detail,
        )

    couples: list[tuple[int, int]] = [(mapping[j], j) for j in sorted(mapping)]
    outcomes = [
        (
            compare_numerical(
                ref_oks[i], ans_oks[j],
                absolute_tolerance=absolute_tolerance,
                relative_tolerance=relative_tolerance,
            ),
            ref_oks[i], ans_oks[j],
        )
        for i, j in couples
    ]
    detail["pairing"]["couples"] = [
        _couple_summary(r, a, o) for o, r, a in outcomes
    ]
    detail["pairing"]["unpaired_reference_count"] = len(ref_oks) - len(couples)

    mismatched = [(o, r, a) for o, r, a in outcomes if o.score == 0.0]
    if mismatched:
        decisive = mismatched[0][0]  # lowest reference index decides
        detail["decisive"] = decisive.detail
        return NumericalPairingOutcome(
            comparison_type=decisive.comparison_type,
            score=0.0,
            reason=decisive.reason,
            detail=detail,
        )
    return NumericalPairingOutcome(
        comparison_type="match", score=1.0,
        detail={**detail, "representation_differs": any(
            r.unit != a.unit for o, r, a in outcomes if o.comparison_type == "match"
        )},
    )


def pair_or_fallback(
    ref_oks: list[NormalizedNumerical],
    ans_oks: list[NormalizedNumerical],
    *,
    ref_text: str,
    ans_text: str,
    alias_table: dict[str, str],
    absolute_tolerance: float | None,
    relative_tolerance: float | None,
) -> NumericalPairingOutcome:
    """Pairing + comparison with the deterministic multiset-equality fallback.
    Single code path used by IntegrityEngine."""
    outcome = pair_and_compare_numerical(
        ref_oks, ans_oks,
        ref_text=ref_text, ans_text=ans_text, alias_table=alias_table,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
    )
    if not (outcome.comparison_type == "ambiguous" and outcome.score is None):
        return outcome
    ref_ms, ans_ms = _multiset(ref_oks), _multiset(ans_oks)
    if ref_ms and ref_ms == ans_ms:
        return NumericalPairingOutcome(
            comparison_type="match", score=1.0,
            detail={
                "pairing": {
                    "strategy": MULTISET_STRATEGY,
                    "reason": "answer states exactly the reference numbers "
                              "(no unique label binding is knowable)",
                    "reference_multiset": ref_ms,
                    "answer_multiset": ans_ms,
                },
            },
        )
    return outcome
