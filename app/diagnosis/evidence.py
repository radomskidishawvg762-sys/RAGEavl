"""Evidence collection + Evidence Contract validation (Spec A.5.3/A.5.4).

Three evidence layers (user decision 2026-08-29):
  Layer 1 — EvaluationRecord: question/contexts/answer/reference_answer/
            reference_contexts/metadata
  Layer 2 — MetricResult / ComparisonBasis: comparison_type/method/tolerance/
            diff/normalized sides
  Layer 3 — Execution / Configuration context (top_k, rewrite, ...) — NOT
            consumed by integrity contracts in MVP.

Integrity contract (minimal sufficient set, Spec A.5.3):
  reference_evidence + answer_claim  (Layer 1 items)
  + ComparisonBasis with deterministic comparison_type and diff (Layer 2)

Output before a Diagnosis MUST satisfy the contract; otherwise the engine
marks undetermined with the missing items listed — attribution is forbidden.
Hard rules: contract string carries a version suffix; items carry
type/source/locator/content; flat string arrays are forbidden (ADR-07).
"""

from __future__ import annotations

from app.diagnosis.classifier import DETERMINISTIC_MISMATCH_TYPES
from app.domain.schemas import (
    ComparisonBasis,
    EvaluationRecord,
    Evidence,
    EvidenceBundle,
    MetricResult,
)

INTEGRITY_CONTRACT_TYPES: tuple[str, ...] = ("reference_evidence", "answer_claim")


class InsufficientEvidenceError(Exception):
    """Raised when evidence does not satisfy the contract (Spec A.5.5)."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"evidence contract unmet, missing: {missing}")


def _answer_locator(answer: str, claim: str, index: int) -> str:
    """`answer_claim` 的定位：片段在答案原文中的字符区间。

    只在片段**唯一出现**时给出区间。出现多次时无法确定被比较的是哪一个，
    此时猜一个「看起来权威但可能指错地方」的定位，比老实说「第 N 条声明」更坏
    —— 证据的意义就在于能被核对。片段找不到（来自归一化改写而非原文）同样退回序号。
    """
    if claim and answer:
        start = answer.find(claim)
        if start != -1 and answer.find(claim, start + 1) == -1:
            return f"answer[{start}:{start + len(claim)}]"
    return f"claim[{index}]"


class EvidenceCollector:
    """Collects Layer-1 evidence for integrity diagnoses from the record and
    Layer-2 facts from the ComparisonBasis. Pure — no mutation, no ORM."""

    def collect_integrity(
        self, record: EvaluationRecord, result: MetricResult, contract: str
    ) -> EvidenceBundle:
        """`contract` is the rule's evidence_contract code (e.g.
        "integrity.numerical_mismatch.v1") — set by the engine, never guessed."""
        basis = result.comparison_basis
        items: list[Evidence] = []

        ref_content, ref_source, ref_locator = self._reference_side(record)
        if ref_content:
            items.append(
                Evidence(
                    type="reference_evidence",
                    source=ref_source,
                    locator=ref_locator,
                    content=ref_content,
                    metadata={"normalized": (basis.reference or {}).get("primary")}
                    if basis
                    else None,
                )
            )

        claim_content, claim_meta = self._answer_claim(record, basis)
        if claim_content:
            items.append(
                Evidence(
                    type="answer_claim",
                    source="answer",
                    locator=_answer_locator(record.answer, claim_content, 0),
                    content=claim_content,
                    metadata=claim_meta,
                )
            )

        return EvidenceBundle(contract=contract, items=items)

    def validate_integrity(
        self, bundle: EvidenceBundle, basis: ComparisonBasis | None
    ) -> list[str]:
        """Returns the list of unmet contract requirements (empty = satisfied)."""
        missing: list[str] = []
        if basis is None:
            missing.append("comparison_basis")
        else:
            if basis.comparison_type not in DETERMINISTIC_MISMATCH_TYPES:
                missing.append("comparison_basis.deterministic_comparison_type")
            if not basis.diff:
                missing.append("comparison_basis.diff")

        present = {i.type for i in bundle.items if (i.content or "").strip()}
        for required in INTEGRITY_CONTRACT_TYPES:
            if required not in present:
                missing.append(required)
        return missing

    # ---- internals ----

    @staticmethod
    def _reference_side(record: EvaluationRecord) -> tuple[str, str, str]:
        if record.reference_contexts:
            first = record.reference_contexts[0]
            if first and first.strip():
                return first, "reference_contexts", "record.reference_contexts[0]"
        if record.reference_answer and record.reference_answer.strip():
            return record.reference_answer, "reference_answer", "record.reference_answer"
        return "", "", ""

    @staticmethod
    def _answer_claim(
        record: EvaluationRecord, basis: ComparisonBasis | None
    ) -> tuple[str, dict | None]:
        normalized = (basis.answer or {}).get("primary") if basis else None
        claim_raw = (normalized or {}).get("raw") if isinstance(normalized, dict) else None
        content = claim_raw or record.answer
        metadata = {"normalized": normalized} if normalized else None
        return (content or "").strip(), metadata
