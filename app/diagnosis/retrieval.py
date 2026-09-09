"""Retrieval diagnosis checks (Post-MVP Phase 5) — deterministic, evidence-first.

Metric ≠ Root Cause: the triggering Failure (threshold breach on a retrieval
metric) only marks the sample for diagnosis. WHETHER a retrieval root cause
can be attributed is decided SOLELY by deterministic comparison of persisted
evidence on the EvaluationRecord:

  gold evidence      <- record.reference_contexts (fallback: reference_answer)
  retrieved evidence <- record.contexts
  top_k datum        <- record.metadata["rag"]["top_k"] (optional passthrough)

Rules implemented (approval 2026-09-04):
  retrieval.missing_evidence  — gold evidence entirely absent from retrieved
  retrieval.top_k_issue       — top_k datum valid AND cap saturated
                                (retrieved_count == top_k) AND required gold
                                count > top_k AND gold evidence missing
Rules explicitly BLOCKED (no evidence source exists — never fabricated):
  retrieval.metadata_filter_issue / retrieval.knowledge_coverage

Comparison is normalized substring containment (lowercase, whitespace and
punctuation normalization) — reproducible, no embeddings/LLM/Judge. Any
unreliable or ambiguous situation yields undetermined, never a guess.

Boundaries: pure functions over the EvaluationRecord; no ORM/Session/
Repository/Engine/Judge/HTTP imports (source-guard tested).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.schemas import EvaluationRecord, Evidence, EvidenceBundle

CODE_MISSING = "retrieval.missing_evidence"
CODE_TOP_K = "retrieval.top_k_issue"

RETRIEVAL_TRIGGER_METRICS = {"context_recall", "context_precision"}

_CONTRACT_SUFFIX = ".v1"

# Evidence item types come from the EXISTING domain EvidenceType enum —
# no second evidence contract is introduced.
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Deterministic normalization: lowercase, punctuation -> space, collapse
    whitespace. Empty result means the item is not reliably comparable."""
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


class RetrievalCheckOutcome(BaseModel):
    """Engine-facing result of the deterministic retrieval evidence check."""

    status: Literal["diagnosed", "undetermined"]
    code: str | None = None  # conclusive taxonomy code (diagnosed)
    contract: str = ""  # "<code>.v1"
    bundle: EvidenceBundle | None = None
    root_cause: str | None = None  # human-readable, built ONLY from evidence facts
    reason: str | None = None  # undetermined
    missing_evidence: list[str] = Field(default_factory=list)
    candidate: str | None = None  # undetermined candidate code when identifiable


class _Summary(BaseModel):
    """Deterministic comparison facts carried on the execution_evidence item
    (field names per approval §8)."""

    rule_name: str
    gold_source: str
    required_evidence_count: int
    retrieved_evidence_count: int
    matched_gold_count: int
    missing_gold_count: int
    configured_top_k: int | None = None
    comparison: str = "normalized_substring_containment_v1"
    also_supported: list[str] = Field(default_factory=list)


def _undetermined(
    reason: str,
    missing: list[str],
    *,
    candidate: str | None = None,
) -> RetrievalCheckOutcome:
    return RetrievalCheckOutcome(
        status="undetermined", reason=reason, missing_evidence=missing, candidate=candidate
    )


def _gold_items(record: EvaluationRecord) -> tuple[list[str], str]:
    """Gold evidence items + their traceable source (never mixed, approval §4).

    Items are returned AS-IS (blank items included) so the reliability gate
    can flag non-comparable gold evidence instead of silently dropping it.
    """
    if record.reference_contexts and any(c and c.strip() for c in record.reference_contexts):
        return list(record.reference_contexts), "reference_contexts"
    if record.reference_answer and record.reference_answer.strip():
        return [record.reference_answer.strip()], "reference_answer"
    return [], ""


def _retrieved_items(record: EvaluationRecord) -> list[str]:
    return [c for c in (record.contexts or []) if isinstance(c, str) and c.strip()]


def _top_k_datum(record: EvaluationRecord) -> tuple[int | None, bool]:
    """(raw datum or None, is_valid_positive_int). rag metadata passthrough only."""
    rag_meta = record.metadata.get("rag") if isinstance(record.metadata, dict) else None
    raw = rag_meta.get("top_k") if isinstance(rag_meta, dict) else None
    valid = isinstance(raw, int) and not isinstance(raw, bool) and raw > 0
    return raw, valid


def _is_present(gold_norm: str, retrieved_norm: list[str]) -> bool:
    return any(gold_norm in r for r in retrieved_norm)


def _trace_items(
    record: EvaluationRecord,
    gold: list[str],
    gold_source: str,
    retrieved: list[str],
    top_k_datum: int | None,
    summary: _Summary,
) -> list[Evidence]:
    """Full traceability bundle items (approval §13): question/record, gold,
    retrieved, top_k (when present), rule + deterministic comparison result."""
    items: list[Evidence] = [
        Evidence(type="query_evidence", source="question", locator="record.question",
                 content=record.question),
    ]
    items.extend(
        Evidence(type="reference_evidence", source=gold_source,
                 locator=f"{gold_source}[{i}]", content=item)
        for i, item in enumerate(gold)
    )
    items.extend(
        Evidence(type="retrieved_evidence", source="contexts", locator=f"contexts[{i}]",
                 content=item)
        for i, item in enumerate(retrieved)
    )
    if top_k_datum is not None:
        items.append(
            Evidence(type="configuration_evidence", source="metadata.rag",
                     locator="metadata.rag.top_k", content=top_k_datum)
        )
    items.append(
        Evidence(type="execution_evidence", source="diagnosis_rule",
                 locator="deterministic_comparison", content=summary.model_dump())
    )
    return items


def evaluate_retrieval(record: EvaluationRecord) -> RetrievalCheckOutcome:
    """Deterministic retrieval root-cause check over persisted record evidence.

    Priority (approval §9): top_k_issue (carries the top_k datum) wins over
    missing_evidence when BOTH are conclusively supported; the losing
    conclusive candidate is preserved in the summary's also_supported —
    never silently dropped.
    """
    gold, gold_source = _gold_items(record)
    retrieved = _retrieved_items(record)

    if not gold:
        return _undetermined(
            "no reference evidence (gold) on record — attribution forbidden",
            ["reference_evidence"],
        )
    if not retrieved:
        return _undetermined(
            "no retrieved evidence (contexts) on record — attribution forbidden",
            ["retrieved_evidence"],
        )
    if not all(normalize_text(g) for g in gold):
        return _undetermined(
            "gold evidence not reliably comparable after deterministic normalization",
            ["reference_evidence.comparable"],
        )

    retrieved_norm = [normalize_text(c) for c in retrieved]
    missing = [g for g in gold if not _is_present(normalize_text(g), retrieved_norm)]
    top_k_datum, top_k_valid = _top_k_datum(record)

    top_k_conclusive = (
        top_k_valid
        and len(retrieved) == top_k_datum  # cap saturated (approval §7B)
        and len(gold) > top_k_datum  # capacity mathematically below requirement (§7D)
        and bool(missing)
    )
    missing_conclusive = len(missing) == len(gold)  # ALL gold absent (approval §6)

    def _summary(rule_name: str, also: list[str]) -> _Summary:
        return _Summary(
            rule_name=rule_name,
            gold_source=gold_source,
            required_evidence_count=len(gold),
            retrieved_evidence_count=len(retrieved),
            matched_gold_count=len(gold) - len(missing),
            missing_gold_count=len(missing),
            configured_top_k=top_k_datum if top_k_datum is not None else None,
            also_supported=also,
        )

    if top_k_conclusive:
        code = CODE_TOP_K
        summary = _summary(
            "top_k_insufficient",
            [CODE_MISSING] if missing_conclusive else [],
        )
        bundle = EvidenceBundle(
            contract=code + _CONTRACT_SUFFIX,
            items=_trace_items(record, gold, gold_source, retrieved, top_k_datum, summary),
        )
        return RetrievalCheckOutcome(
            status="diagnosed",
            code=code,
            contract=bundle.contract,
            bundle=bundle,
            root_cause=(
                f"Top-K Issue (configured_top_k={top_k_datum} < required gold evidence "
                f"{len(gold)}; retrieved {len(retrieved)} at cap; missing {len(missing)})"
            ),
        )

    if missing_conclusive:
        code = CODE_MISSING
        summary = _summary("missing_gold_evidence", [])
        bundle = EvidenceBundle(
            contract=code + _CONTRACT_SUFFIX,
            items=_trace_items(record, gold, gold_source, retrieved, top_k_datum, summary),
        )
        return RetrievalCheckOutcome(
            status="diagnosed",
            code=code,
            contract=bundle.contract,
            bundle=bundle,
            root_cause=(
                f"Missing Evidence (0/{len(gold)} gold evidence items present in "
                f"{len(retrieved)} retrieved contexts)"
            ),
        )

    # Neither rule conclusive — ambiguous among ranking/filter/coverage/
    # chunking causes; never pick one without further evidence (approval §12).
    if len(missing) == 0:
        reason = (
            f"all {len(gold)} gold evidence items present in retrieved contexts; "
            "low score not attributable to a retrieval root cause without retrieval metadata"
        )
    else:
        reason = (
            f"gold evidence partially retrieved ({len(gold) - len(missing)}/{len(gold)}); "
            "cause ambiguous (ranking / filter / coverage / top_k) without retrieval metadata"
        )
    if top_k_datum is not None and not top_k_valid:
        reason += f"; top_k datum present but invalid: {top_k_datum!r}"
    return _undetermined(reason, ["retrieval_metadata"], candidate=None)
