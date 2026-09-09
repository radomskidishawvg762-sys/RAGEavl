"""Generation diagnosis checks (Post-MVP Phase 6) — deterministic, evidence-first.

Metric ≠ Root Cause: a threshold failure on a generation metric (faithfulness /
answer_relevancy) only TRIGGERS the check. Whether a generation root cause can
be attributed is decided SOLELY by deterministic comparison of persisted
evidence on the EvaluationRecord:

  answer claims        <- numerical claims extracted from record.answer via the
                          T-09 integrity normalizer (pure, deterministic)
  grounding evidence   <- record.contexts (retrieved evidence)
  reference facts      <- numerical claims extracted from reference evidence
                          (reference_answer, fallback reference_contexts)

Rules implemented (approval 2026-09-04):
  generation.unsupported_claim — ALL reliably-parsed answer claims are absent
                                 (by numeric base-value equivalence) from the
                                 retrieved contexts
  generation.partial_answer   — answer restates SOME but not ALL distinct
                                 reference numeric facts (0 < matched < N, N>=2)

Rules explicitly BLOCKED (no deterministic evidence source — never fabricated):
  generation.irrelevant_answer — answer-question relevance is semantic; a
                                 lexical/probabilistic proxy is forbidden this
                                 phase (no LLM / embedding / probabilistic
                                 classifier). Zero-overlap heuristics would be
                                 exactly that, so the rule stays undetermined.

Comparison is numeric base-value equivalence over the T-09 normalizer
(元/万元/亿, %, ‰, Chinese numerals all normalize to one base space) —
reproducible, no LLM/Judge/embedding. Any unreliable or ambiguous situation
yields undetermined, never a guess.

Grounding note: a claim is grounded only by the RETRIEVED contexts (never by
the reference) — "true per reference but absent from retrieval" is still
unsupported by the RAG evidence and belongs to this diagnosis.

Boundaries: pure functions over the EvaluationRecord; may import the T-09
extraction library (app.metrics.integrity — pure normalization only); no
ORM/Session/Repository/Engine/Judge/HTTP imports (source-guard tested).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.schemas import EvaluationRecord, Evidence, EvidenceBundle
from app.metrics.integrity import NormalizedNumerical, extract_numerical

CODE_UNSUPPORTED = "generation.unsupported_claim"
CODE_PARTIAL = "generation.partial_answer"

GENERATION_TRIGGER_METRICS = {"faithfulness", "answer_relevancy"}

_CONTRACT_SUFFIX = ".v1"
# float-noise guard for base-value equivalence (implementation constant, not a
# business threshold — same rationale as the T-10 comparison guards).
_BASE_REL_TOL = 1e-6


class GenerationCheckOutcome(BaseModel):
    """Engine-facing result of the deterministic generation evidence check."""

    status: Literal["diagnosed", "undetermined"]
    code: str | None = None
    contract: str = ""
    bundle: EvidenceBundle | None = None
    root_cause: str | None = None
    reason: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    candidate: str | None = None


class _Summary(BaseModel):
    """Deterministic comparison facts carried on the execution_evidence item."""

    rule_name: str
    answer_claim_count: int  # reliably-parsed, distinct answer claims
    grounded_claim_count: int
    reference_claim_source: str = ""  # reference_answer | reference_contexts
    required_reference_claims: int = 0
    matched_reference_claims: int = 0
    comparison: str = "numeric_base_equivalence_v1"
    also_supported: list[str] = Field(default_factory=list)


def _undetermined(
    reason: str, missing: list[str], *, candidate: str | None = None
) -> GenerationCheckOutcome:
    return GenerationCheckOutcome(
        status="undetermined", reason=reason, missing_evidence=missing, candidate=candidate
    )


def _base(n: NormalizedNumerical) -> float | None:
    if n.value is None:
        return None
    return n.value * n.scale_to_base if n.scale_to_base is not None else n.value


def _distinct_bases(items: list[NormalizedNumerical]) -> list[float]:
    """Tolerance-grouped distinct base values (same number restated = one claim)."""
    bases: list[float] = []
    for item in items:
        b = _base(item)
        if b is None:
            continue
        if not any(abs(b - x) <= _BASE_REL_TOL * max(1.0, abs(b), abs(x)) for x in bases):
            bases.append(b)
    return bases


def _claims_of(text: str) -> list[float]:
    parsed = [n for n in extract_numerical(text) if n.parse_status == "ok" and n.value is not None]
    return _distinct_bases(parsed)


def _same_base(a: float, b: float) -> bool:
    return abs(a - b) <= _BASE_REL_TOL * max(1.0, abs(a), abs(b))


def _reference_facts(record: EvaluationRecord) -> tuple[list[float], str]:
    """Distinct numeric facts the answer is expected to restate (traced source)."""
    if record.reference_answer and record.reference_answer.strip():
        return _claims_of(record.reference_answer), "reference_answer"
    if record.reference_contexts and any(c and c.strip() for c in record.reference_contexts):
        merged = " ".join(c for c in record.reference_contexts if c and c.strip())
        return _claims_of(merged), "reference_contexts"
    return [], ""


def _trace_items(
    record: EvaluationRecord,
    answer_claims: list[float],
    grounded: int,
    ref_facts: list[float],
    ref_source: str,
    matched: int,
    summary: _Summary,
) -> list[Evidence]:
    """Full traceability (approval §13 of Phase 5, mirrored): question, answer
    claims, retrieved contexts, reference facts, rule + comparison result."""
    items: list[Evidence] = [
        Evidence(type="query_evidence", source="question", locator="record.question",
                 content=record.question),
        Evidence(type="answer_claim", source="answer", locator="numeric_claims",
                 content=[{"base": b} for b in answer_claims],
                 metadata={"grounded_count": grounded}),
    ]
    items.extend(
        Evidence(type="retrieved_evidence", source="contexts", locator=f"contexts[{i}]",
                 content=item)
        for i, item in enumerate(record.contexts or [])
    )
    if ref_facts:
        items.append(
            Evidence(type="reference_evidence", source=ref_source,
                     locator=f"numeric_claims@{ref_source}",
                     content=[{"base": b} for b in ref_facts],
                     metadata={"matched_count": matched})
        )
    items.append(
        Evidence(type="execution_evidence", source="diagnosis_rule",
                 locator="deterministic_comparison", content=summary.model_dump())
    )
    return items


def evaluate_generation(record: EvaluationRecord) -> GenerationCheckOutcome:
    """Deterministic generation root-cause check over persisted record evidence.

    Priority (approval §9 analogue): unsupported_claim (grounding) wins over
    partial_answer (completeness) when BOTH are conclusively supported; the
    losing conclusive candidate is preserved in also_supported — never
    silently dropped.
    """
    contexts = [c for c in (record.contexts or []) if isinstance(c, str) and c.strip()]
    if not contexts:
        return _undetermined(
            "no retrieved evidence (contexts) on record — grounding cannot be checked",
            ["retrieved_evidence"],
        )

    answer_claims = _claims_of(record.answer or "")
    context_claims = _claims_of(" ".join(contexts))

    if not answer_claims:
        return _undetermined(
            "no reliably extractable numeric claims in answer; grounding/relevance "
            "cannot be checked deterministically (semantic matching unavailable this phase)",
            ["answer_claim.checkable"],
        )

    grounded = sum(1 for c in answer_claims if any(_same_base(c, x) for x in context_claims))
    unsupported_conclusive = grounded == 0  # ALL answer claims ungrounded

    ref_facts, ref_source = _reference_facts(record)
    matched = sum(1 for f in ref_facts if any(_same_base(f, c) for c in answer_claims))
    partial_conclusive = len(ref_facts) >= 2 and 0 < matched < len(ref_facts)

    def _summary(rule_name: str, also: list[str]) -> _Summary:
        return _Summary(
            rule_name=rule_name,
            answer_claim_count=len(answer_claims),
            grounded_claim_count=grounded,
            reference_claim_source=ref_source,
            required_reference_claims=len(ref_facts),
            matched_reference_claims=matched,
            also_supported=also,
        )

    if unsupported_conclusive:
        code = CODE_UNSUPPORTED
        summary = _summary(
            "unsupported_claim",
            [CODE_PARTIAL] if partial_conclusive else [],
        )
        bundle = EvidenceBundle(
            contract=code + _CONTRACT_SUFFIX,
            items=_trace_items(record, answer_claims, grounded, ref_facts, ref_source, matched,
                               summary),
        )
        return GenerationCheckOutcome(
            status="diagnosed",
            code=code,
            contract=bundle.contract,
            bundle=bundle,
            root_cause=(
                f"Unsupported Claim ({len(answer_claims)}/{len(answer_claims)} checkable "
                f"numeric claims not grounded in {len(contexts)} retrieved contexts)"
            ),
        )

    if partial_conclusive:
        code = CODE_PARTIAL
        summary = _summary("partial_answer", [])
        bundle = EvidenceBundle(
            contract=code + _CONTRACT_SUFFIX,
            items=_trace_items(record, answer_claims, grounded, ref_facts, ref_source, matched,
                               summary),
        )
        return GenerationCheckOutcome(
            status="diagnosed",
            code=code,
            contract=bundle.contract,
            bundle=bundle,
            root_cause=(
                f"Partial Answer ({matched}/{len(ref_facts)} reference numeric claims "
                f"restated in answer)"
            ),
        )

    # Neither rule conclusive — never guess between grounding/completeness/
    # relevance causes without semantic evidence (BLOCKED irrelevance stays out).
    if grounded == len(answer_claims) and (not ref_facts or matched == len(ref_facts)):
        reason = (
            "evidence shows answer claims grounded in retrieved contexts and reference "
            "facts restated; failure not attributable to a generation root cause "
            "without semantic evidence"
        )
    elif len(ref_facts) < 2 and matched == 0:
        reason = (
            "answer restates none of the reference numeric facts; cause ambiguous "
            "(irrelevance / incompleteness / grounding) — semantic matching unavailable"
        )
    else:
        reason = (
            f"answer claims partially grounded ({grounded}/{len(answer_claims)}) and "
            f"reference facts partially restated ({matched}/{len(ref_facts)}); cause "
            "ambiguous without semantic evidence"
        )
    return _undetermined(reason, ["semantic_evidence"], candidate=None)
