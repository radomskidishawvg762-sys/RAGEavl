"""T-11 Diagnosis Layer MVP (Integrity only) tests.

Covers the 15 mandated points: Failure semantics (threshold + deterministic
mismatch), integrity diagnosis rules, evidence contract, undetermined
behavior (ambiguous is NEVER diagnosed), sample result_id, run-level
extension point, and non-mutation of MetricResult.
"""

from __future__ import annotations

import asyncio
import copy

from app.diagnosis import (
    FAILURE_TAXONOMY,
    INTEGRITY_CODES,
    DiagnosisEngine,
    FailureClassifier,
)
from app.diagnosis.classifier import DETERMINISTIC_MISMATCH_TYPES
from app.diagnosis.taxonomy import INTEGRITY_CODES as DIAGNOSIS_ENGINE_INTEGRITY_CODES
from app.domain.schemas import (
    ComparisonBasis,
    Diagnosis,
    EvaluationRecord,
    MetricResult,
)
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine

# ---------------- fixtures ----------------


def _record(
    answer: str,
    reference_answer: str | None = None,
    reference_contexts: list[str] | None = None,
    record_id: str = "r1",
) -> EvaluationRecord:
    return EvaluationRecord(
        id=record_id,
        question="q",
        contexts=["召回上下文"],
        answer=answer,
        reference_answer=reference_answer,
        reference_contexts=reference_contexts,
    )


def _run_engine(record: EvaluationRecord, metric: str, threshold: float | None = None) -> MetricResult:
    results = asyncio.run(
        IntegrityEngine().evaluate(record, [metric], EvalParams(threshold=threshold))
    )
    return results[0]


def _classify(result: MetricResult):
    return FailureClassifier().classify(result)


def _diagnose(record: EvaluationRecord, result: MetricResult, severity_mapping=None):
    return DiagnosisEngine(severity_mapping=severity_mapping).diagnose(record, result)


# ---------------- A/B: Failure semantics (points 1-3) ----------------

def _fake_ragas_result(score: float | None, threshold: float | None) -> MetricResult:
    return MetricResult(
        record_id="r1", metric_name="faithfulness", category="generation",
        score=score, threshold=threshold, metric_version="ragas-test-v1",
    )


def test_1_score_below_threshold_is_failure() -> None:
    c = _classify(_fake_ragas_result(score=0.42, threshold=0.8))
    assert c.status == "failure"
    assert c.failure.source == "threshold"
    assert c.failure.result_id == "r1"


def test_2_score_at_or_above_threshold_is_not_failure() -> None:
    assert _classify(_fake_ragas_result(score=0.8, threshold=0.8)).status == "not_failed"
    assert _classify(_fake_ragas_result(score=0.9, threshold=0.8)).status == "not_failed"


def test_3_threshold_null_never_fails_on_score() -> None:
    assert _classify(_fake_ragas_result(score=0.1, threshold=None)).status == "not_failed"


# ---------------- deterministic integrity failures (points 4-7) ----------------

def test_4_numerical_value_mismatch_failure_and_diagnosis() -> None:
    rec = _record("中国平安2024年营收2亿元", reference_answer="中国平安2024年营收1亿元")
    result = _run_engine(rec, "numerical_consistency", threshold=0.99)
    c = _classify(result)
    assert c.status == "failure" and c.failure.source == "deterministic_mismatch"
    assert c.failure.comparison_type == "value_mismatch"

    out = _diagnose(rec, result)
    assert out.status == "diagnosed"
    d = out.diagnosis
    assert d.failure_type == "integrity.numerical_mismatch"
    assert d.related_metric == "numerical_consistency"
    assert d.root_cause and "Numerical Mismatch" in d.root_cause
    assert d.severity == "CRITICAL"  # spec appendix B default hint
    assert d.confidence == "high"
    assert d.evidence_contract == "integrity.numerical_mismatch.v1"
    types = {e.type for e in d.evidence}
    assert {"reference_evidence", "answer_claim"} <= types
    assert out.recommendations and all(r.source == "rule" for r in out.recommendations)


def test_4b_scale_mismatch_maps_to_numerical_mismatch() -> None:
    rec = _record("1万元", reference_answer="1亿元")
    result = _run_engine(rec, "numerical_consistency")
    out = _diagnose(rec, result)
    assert out.diagnosis.failure_type == "integrity.numerical_mismatch"


def test_5_unit_mismatch_failure_and_diagnosis() -> None:
    rec = _record("增长50%", reference_answer="增长50元")
    result = _run_engine(rec, "numerical_consistency")
    c = _classify(result)
    assert c.status == "failure" and c.failure.comparison_type == "unit_mismatch"
    out = _diagnose(rec, result)
    assert out.status == "diagnosed"
    assert out.diagnosis.failure_type == "integrity.unit_mismatch"
    assert out.diagnosis.severity == "ERROR"


def test_6_temporal_mismatch_failure_and_diagnosis() -> None:
    rec = _record("2023年年报显示", reference_answer="2024年年报显示")
    result = _run_engine(rec, "temporal_consistency")
    assert _classify(result).failure.comparison_type == "temporal_mismatch"
    out = _diagnose(rec, result)
    assert out.status == "diagnosed"
    assert out.diagnosis.failure_type == "integrity.temporal_mismatch"


def test_7_entity_mismatch_failure_and_diagnosis() -> None:
    rec = _record(
        "招商银行2024年营收1亿元", reference_answer="中国平安2024年营收1亿元",
        reference_contexts=["中国平安2024年营业收入为1亿元"],
    )
    result = asyncio.run(
        IntegrityEngine(alias_table={"平安": "中国平安", "中国平安": "中国平安", "招商银行": "招商银行"}).evaluate(
            rec, ["entity_consistency"], EvalParams()
        )
    )[0]
    assert _classify(result).failure.comparison_type == "entity_mismatch"
    out = _diagnose(rec, result)
    assert out.status == "diagnosed"
    assert out.diagnosis.failure_type == "integrity.entity_mismatch"


# ---------------- ambiguous / undetermined (points 8-10) ----------------

def test_8_ambiguous_is_undetermined_never_diagnosed() -> None:
    rec = _record("去年营收大幅增长", reference_answer="2024年营收1亿元")
    result = _run_engine(rec, "temporal_consistency")
    c = _classify(result)
    assert c.status == "undetermined" and c.failure is None
    out = _diagnose(rec, result)
    assert out.status == "undetermined"
    assert out.diagnosis is None and out.reason


def test_9_both_ambiguous_is_undetermined() -> None:
    rec = _record("去年营收增长", reference_answer="今年营收增长")
    result = _run_engine(rec, "temporal_consistency")
    assert _classify(result).status == "undetermined"
    out = _diagnose(rec, result)
    assert out.status == "undetermined" and out.diagnosis is None


def test_10_unparsed_is_undetermined() -> None:
    # answer side unparseable, reference ok -> comparison_type=ambiguous
    rec = _record("无法解析的时间", reference_answer="2024年")
    result = _run_engine(rec, "temporal_consistency")
    assert result.comparison_basis.comparison_type == "ambiguous"
    assert _classify(result).status == "undetermined"
    out = _diagnose(rec, result)
    assert out.status == "undetermined"
    # mirrored: reference side has no expression -> missing_reference
    rec2 = _record("2024年", reference_answer="无法解析的时间")
    result2 = _run_engine(rec2, "temporal_consistency")
    assert result2.comparison_basis.comparison_type == "missing_reference"
    out2 = _diagnose(rec2, result2)
    assert out2.status == "undetermined" and out2.diagnosis is None


def test_10b_metric_error_is_undetermined() -> None:
    rec = _record("答案", reference_answer=None)  # missing reference -> engine error result
    result = _run_engine(rec, "numerical_consistency")
    assert result.error is not None
    out = _diagnose(rec, result)
    assert out.status == "undetermined"


# ---------------- evidence contract (points 11-12) ----------------

def test_11_missing_evidence_is_undetermined_with_missing_list() -> None:
    """value_mismatch basis but record carries no reference evidence at all."""
    rec = _record("中国平安2024年营收2亿元", reference_answer=None, reference_contexts=None)
    result = MetricResult(
        record_id=rec.id, metric_name="numerical_consistency", category="integrity",
        score=0.0, threshold=None,
        comparison_basis=ComparisonBasis(
            reference={"primary": {"raw": "1亿元"}, "side_status": "ok"},
            answer={"primary": {"raw": "2亿元"}, "side_status": "ok"},
            comparison_type="value_mismatch", method="deterministic",
            diff={"base": {"reference": 1e8, "answer": 2e8, "abs_diff": 1e8}},
        ),
        metric_version="integrity-normalization-v1",
    )
    assert _classify(result).status == "failure"
    out = _diagnose(rec, result)
    assert out.status == "undetermined"
    assert "reference_evidence" in out.missing_evidence
    assert out.diagnosis is None


def test_12_contract_satisfied_produces_diagnosis_with_bundle() -> None:
    rec = _record(
        "2024年营收2亿元", reference_answer="2024年营收1亿元",
        reference_contexts=["2024年公司营业收入为1亿元。"],
    )
    result = _run_engine(rec, "numerical_consistency")
    out = _diagnose(rec, result)
    assert out.status == "diagnosed"
    d = out.diagnosis
    assert d.evidence_contract.endswith(".v1")
    for e in d.evidence:
        assert e.type and e.source and e.locator and e.content  # ADR-07 traceability
    ref = next(e for e in d.evidence if e.type == "reference_evidence")
    assert ref.locator == "record.reference_contexts[0]"
    assert ref.metadata["normalized"]["raw"] == "1亿元"  # Layer-2 normalized side attached


# ---------------- sample / run level (points 13-14) ----------------

def test_13_sample_diagnosis_carries_result_id() -> None:
    rec = _record("2亿元", reference_answer="1亿元", record_id="rec-42")
    result = _run_engine(rec, "numerical_consistency")
    out = _diagnose(rec, result)
    assert out.diagnosis.result_id == "rec-42"
    assert out.diagnosis.result_id == result.record_id


def test_14_run_level_extension_point_does_not_break_models() -> None:
    engine = DiagnosisEngine()
    assert engine.diagnose_run([]) == []  # interface kept, aggregation deferred
    d = Diagnosis(  # result_id=None is structurally valid (Run-level)
        failure_type="integrity.numerical_mismatch", related_metric="numerical_consistency",
        root_cause="Numerical Mismatch", severity="ERROR", evidence=[],
        evidence_contract="integrity.numerical_mismatch.v1", confidence="high",
        result_id=None,
    )
    assert d.result_id is None


# ---------------- invariants (point 15 + taxonomy integrity) ----------------

def test_15_diagnosis_does_not_mutate_metric_result() -> None:
    rec = _record("2亿元", reference_answer="1亿元", reference_contexts=["ref ctx"])
    result = _run_engine(rec, "numerical_consistency", threshold=0.99)
    before = copy.deepcopy(result.model_dump())
    record_before = copy.deepcopy(rec.model_dump())
    _diagnose(rec, result, severity_mapping={"numerical_mismatch": "WARNING"})
    assert result.model_dump() == before
    assert rec.model_dump() == record_before


def test_severity_mapping_overrides_platform_hint() -> None:
    rec = _record("2亿元", reference_answer="1亿元")
    result = _run_engine(rec, "numerical_consistency")
    out = _diagnose(rec, result, severity_mapping={"numerical_mismatch": "WARNING"})
    assert out.diagnosis.severity == "WARNING"
    out_default = _diagnose(rec, result)  # no mapping -> spec hint
    assert out_default.diagnosis.severity == "CRITICAL"


def test_threshold_only_failure_on_integrity_metric_has_no_rule() -> None:
    """match score below threshold -> threshold failure, but no deterministic
    mismatch -> no integrity rule -> undetermined (never score-based attribution)."""
    rec = _record("2024年", reference_answer="2024年度")
    result = _run_engine(rec, "temporal_consistency", threshold=1.1)
    c = _classify(result)
    assert c.status == "failure" and c.failure.source == "threshold"
    out = _diagnose(rec, result)
    assert out.status == "undetermined"
    assert "no MVP diagnosis rule" in out.reason


def test_taxonomy_codes_complete_and_integrity_only_rules() -> None:
    assert len(FAILURE_TAXONOMY) == 14
    assert set(INTEGRITY_CODES) == set(DIAGNOSIS_ENGINE_INTEGRITY_CODES)
    assert {"value_mismatch", "unit_mismatch", "scale_mismatch",
                                            "temporal_mismatch", "entity_mismatch"} >= DETERMINISTIC_MISMATCH_TYPES
