"""Integrity diagnosis evidence quotes the text that was actually compared."""

from __future__ import annotations

import asyncio

from app.diagnosis import DiagnosisEngine
from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine


def _diagnose(answer: str, reference_answer: str, reference_contexts: list[str] | None = None):
    record = EvaluationRecord(
        id="r1",
        question="q",
        contexts=["召回上下文"],
        answer=answer,
        reference_answer=reference_answer,
        reference_contexts=reference_contexts,
    )
    result = asyncio.run(
        IntegrityEngine().evaluate(record, ["numerical_consistency"], EvalParams())
    )[0]
    return DiagnosisEngine().diagnose(record, result)


def test_evidence_uses_compared_raw_text_and_reference_answer_source() -> None:
    out = _diagnose(
        "2024年营收2亿元",
        "2024年营收1亿元",
        reference_contexts=["一整段无关的长上下文，不要用它当证据。"],
    )
    assert out.status == "diagnosed"
    assert out.diagnosis is not None
    ref = next(item for item in out.diagnosis.evidence if item.type == "reference_evidence")
    assert ref.source == "reference_answer"
    assert ref.content == "1亿元"
    assert "无关" not in ref.content


def test_multi_value_evidence_uses_the_pair_that_decided_the_result() -> None:
    out = _diagnose(
        "净利润4600万元，营收2.3亿元",
        "净利润4500万元，营收2.3亿元",
    )
    assert out.status == "diagnosed"
    assert out.diagnosis is not None
    ref = next(item for item in out.diagnosis.evidence if item.type == "reference_evidence")
    claim = next(item for item in out.diagnosis.evidence if item.type == "answer_claim")
    assert ref.source == "reference_answer"
    assert ref.content == "4500万元"
    assert claim.content == "4600万元"
