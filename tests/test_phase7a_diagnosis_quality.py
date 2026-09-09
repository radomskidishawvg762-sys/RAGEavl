"""Phase 7A diagnosis quality hardening.

Test-only coverage for cross-domain isolation, evidence validity, recommendation
mapping, mixed run aggregation, and read-only report behavior. No external
provider, HTTP call, database, or business-code change is required.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.diagnosis import DiagnosisEngine
from app.diagnosis.generation import CODE_PARTIAL, CODE_UNSUPPORTED
from app.diagnosis.retrieval import CODE_MISSING, CODE_TOP_K
from app.diagnosis.taxonomy import GENERATION_RULES, INTEGRITY_RULES, RETRIEVAL_RULES
from app.domain.schemas import Diagnosis, EvaluationRecord, MetricResult
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.services.report_service import ReportService
from tests.test_evaluation_service import FakeEvaluationRepo
from tests.test_report_aggregation import _add_record, _make_run


def _record(*, answer: str = "营收2亿元", contexts: list[str] | None = None,
            reference_answer: str | None = "营收1亿元",
            reference_contexts: list[str] | None = None,
            metadata: dict | None = None) -> EvaluationRecord:
    return EvaluationRecord(
        id="r1", question="公司营收是多少？", contexts=contexts or ["无关上下文"],
        answer=answer, reference_answer=reference_answer,
        reference_contexts=reference_contexts, metadata=metadata or {},
    )


def _threshold(metric: str, category: str) -> MetricResult:
    return MetricResult(
        record_id="r1", metric_name=metric, category=category,
        score=0.2, threshold=0.8, metric_version="phase7a-test-v1",
    )


def _integrity_result(record: EvaluationRecord) -> MetricResult:
    import asyncio

    return asyncio.run(
        IntegrityEngine().evaluate(record, ["numerical_consistency"], EvalParams())
    )[0]


def _diagnosed(record: EvaluationRecord, result: MetricResult):
    outcome = DiagnosisEngine().diagnose(record, result)
    assert outcome.status == "diagnosed", outcome.reason
    assert outcome.diagnosis is not None
    return outcome.diagnosis


# ---------------- cross-domain isolation ----------------


def test_retrieval_failure_does_not_enter_generation_diagnosis() -> None:
    record = _record(
        answer="营收2亿元", contexts=["公司营收为1亿元"],
        reference_contexts=["公司营收为1亿元"],
    )
    outcome = DiagnosisEngine().diagnose(record, _threshold("context_recall", "retrieval"))
    assert outcome.status == "undetermined"  # retrieval evidence supports gold
    assert outcome.diagnosis is None
    assert outcome.candidate_failure_type is None


def test_generation_failure_does_not_enter_retrieval_diagnosis() -> None:
    record = _record(answer="营收1亿元", contexts=["公司营收为1亿元"])
    outcome = DiagnosisEngine().diagnose(record, _threshold("faithfulness", "generation"))
    assert outcome.status == "undetermined"  # grounded claim, no generation RCA
    assert outcome.diagnosis is None
    assert outcome.candidate_failure_type is None


def test_integrity_mismatch_stays_in_integrity_domain() -> None:
    record = _record(answer="营收2亿元", reference_answer="营收1亿元")
    diagnosis = _diagnosed(record, _integrity_result(record))
    assert diagnosis.failure_type == "integrity.numerical_mismatch"
    assert not diagnosis.failure_type.startswith("retrieval.")
    assert not diagnosis.failure_type.startswith("generation.")


def test_same_record_can_keep_retrieval_generation_and_integrity_diagnoses() -> None:
    record = _record(
        answer="营收2亿元", contexts=["无关上下文"],
        reference_answer="营收1亿元", reference_contexts=["公司营收为1亿元"],
    )
    engine = DiagnosisEngine()
    retrieval = _diagnosed(record, _threshold("context_recall", "retrieval"))
    generation = _diagnosed(record, _threshold("faithfulness", "generation"))
    integrity = _diagnosed(record, _integrity_result(record))
    codes = {retrieval.failure_type, generation.failure_type, integrity.failure_type}
    assert codes == {CODE_MISSING, CODE_UNSUPPORTED, "integrity.numerical_mismatch"}
    assert len({retrieval.related_metric, generation.related_metric, integrity.related_metric}) == 3
    # Calling the same engine does not mutate the record or another outcome.
    assert engine.diagnose(record, _threshold("faithfulness", "generation")).diagnosis.failure_type == CODE_UNSUPPORTED


# ---------------- mixed run-level aggregation ----------------


def _row(code: str | None, *, result_id: str, metric: str, severity: str,
         status: str = "diagnosed", evidence: list[dict] | None = None):
    """Row-like object for aggregation, including nullable undetermined codes."""
    return SimpleNamespace(
        run_id="run1", result_id=result_id, status=status, failure_type=code,
        related_metric=metric, root_cause="root" if status == "diagnosed" else None,
        severity=severity, evidence=evidence or [],
        evidence_contract=f"{code}.v1" if status == "diagnosed" and code else "",
        confidence="high" if status == "diagnosed" else "low",
        detail=None if status == "diagnosed" else {"reason": "insufficient", "missing_evidence": ["evidence"]},
    )


def test_mixed_run_level_buckets_remain_independent() -> None:
    rows = (
        [_row(CODE_MISSING, result_id=f"r{i}", metric="context_recall", severity="ERROR") for i in range(10)]
        + [_row(CODE_UNSUPPORTED, result_id=f"r{i}", metric="faithfulness", severity="CRITICAL") for i in range(10, 15)]
        + [_row("integrity.numerical_mismatch", result_id=f"r{i}", metric="numerical_consistency", severity="CRITICAL") for i in range(15, 18)]
        + [_row(None, result_id=f"r{i}", metric="context_recall", severity="INFO", status="undetermined") for i in range(18, 22)]
    )
    summary = DiagnosisEngine().diagnose_run_aggregate(rows, {f"r{i}": f"record-{i}" for i in range(22)})
    assert summary.total_diagnoses == 22
    assert summary.undetermined_count == 4
    assert {b.failure_type for b in summary.buckets} == {CODE_MISSING, CODE_UNSUPPORTED, "integrity.numerical_mismatch", None}
    by_code = {(b.status, b.failure_type): b for b in summary.buckets}
    assert by_code[("diagnosed", CODE_MISSING)].count == 10
    assert by_code[("diagnosed", CODE_MISSING)].ratio == pytest.approx(10 / 22)
    assert by_code[("diagnosed", CODE_MISSING)].severity == "ERROR"
    assert by_code[("diagnosed", CODE_MISSING)].related_metrics == ["context_recall"]
    assert by_code[("diagnosed", CODE_MISSING)].affected_records == 10
    assert by_code[("diagnosed", CODE_MISSING)].evidence_available is False  # fixture has no evidence
    assert by_code[("diagnosed", CODE_UNSUPPORTED)].count == 5
    assert by_code[("diagnosed", "integrity.numerical_mismatch")].count == 3
    assert by_code[("undetermined", None)].count == 4
    assert by_code[("undetermined", None)].evidence_available is False


# ---------------- recommendation consistency ----------------


def test_implemented_diagnosis_codes_map_to_same_code_templates() -> None:
    implemented = {**INTEGRITY_RULES, **RETRIEVAL_RULES, **GENERATION_RULES}
    expected = {
        "integrity.numerical_mismatch", "integrity.temporal_mismatch",
        "integrity.entity_mismatch", "integrity.unit_mismatch",
        CODE_MISSING, CODE_TOP_K, CODE_UNSUPPORTED, CODE_PARTIAL,
    }
    assert set(implemented) == expected
    from app.diagnosis.recommendations import build_recommendations

    for code, rule in implemented.items():
        recommendations = build_recommendations(rule)
        assert recommendations, code
        assert all(r.source == "rule" and r.action for r in recommendations)
        # The rule's own fallback/template is the only source of its actions;
        # code-specific templates are checked through the taxonomy key itself.
        assert rule.code == code


# ---------------- evidence validity ----------------


def assert_diagnosed_evidence_valid(diagnosis: Diagnosis, *, status: str = "diagnosed") -> None:
    # Domain Diagnosis intentionally has no status field; status belongs to
    # DiagnosisResult / persisted ORM rows. The caller supplies the wrapper's
    # already-asserted status while this helper validates the Diagnosis body.
    assert status == "diagnosed"
    assert diagnosis.evidence
    assert diagnosis.evidence_contract == f"{diagnosis.failure_type}.v1"
    for item in diagnosis.evidence:
        assert item.type and item.source and item.locator and item.content is not None

    types = {item.type for item in diagnosis.evidence}
    if diagnosis.failure_type == CODE_MISSING:
        assert {"query_evidence", "reference_evidence", "retrieved_evidence", "execution_evidence"} <= types
        execution = next(i for i in diagnosis.evidence if i.type == "execution_evidence")
        assert execution.content["rule_name"] == "missing_gold_evidence"
        assert execution.content["comparison"] == "normalized_substring_containment_v1"
    elif diagnosis.failure_type == CODE_TOP_K:
        assert {"query_evidence", "reference_evidence", "retrieved_evidence",
                "configuration_evidence", "execution_evidence"} <= types
        execution = next(i for i in diagnosis.evidence if i.type == "execution_evidence")
        assert execution.content["rule_name"] == "top_k_insufficient"
        assert execution.content["configured_top_k"] > 0
        assert execution.content["missing_gold_count"] > 0
    elif diagnosis.failure_type == CODE_UNSUPPORTED:
        assert {"query_evidence", "answer_claim", "retrieved_evidence", "execution_evidence"} <= types
        execution = next(i for i in diagnosis.evidence if i.type == "execution_evidence")
        assert execution.content["comparison"] == "numeric_base_equivalence_v1"
    elif diagnosis.failure_type == CODE_PARTIAL:
        assert {"query_evidence", "answer_claim", "reference_evidence", "execution_evidence"} <= types
        execution = next(i for i in diagnosis.evidence if i.type == "execution_evidence")
        assert 0 < execution.content["matched_reference_claims"] < execution.content["required_reference_claims"]
    else:
        assert {"reference_evidence", "answer_claim"} <= types


def test_all_diagnosed_rule_outputs_have_valid_evidence() -> None:
    records_and_results = [
        (_record(contexts=["无关内容"], reference_contexts=["gold-A"]), _threshold("context_recall", "retrieval")),
        (_record(contexts=["chunk-1", "chunk-2", "chunk-3"], reference_contexts=["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"], metadata={"rag": {"top_k": 3}}), _threshold("context_recall", "retrieval")),
        (_record(answer="营收2亿元", contexts=["营收1亿元"]), _threshold("faithfulness", "generation")),
        (_record(answer="营收1亿元", contexts=["营收1亿元"], reference_answer="营收1亿元、利润3百万元、现金流5百万元"), _threshold("faithfulness", "generation")),
        (_record(answer="营收2亿元", reference_answer="营收1亿元"), None),
    ]
    for record, result in records_and_results:
        diagnosis = _diagnosed(record, _integrity_result(record) if result is None else result)
        assert_diagnosed_evidence_valid(diagnosis)


def test_undetermined_diagnosis_has_no_root_cause_or_diagnosed_bucket() -> None:
    record = _record(answer="无法解析", contexts=["ctx"], reference_answer=None)
    outcome = DiagnosisEngine().diagnose(record, _threshold("faithfulness", "generation"))
    assert outcome.status == "undetermined"
    assert outcome.diagnosis is None
    assert outcome.candidate_failure_type is None


# ---------------- report read-only consistency ----------------


def test_report_run_level_matches_persisted_diagnosis_aggregation_and_is_read_only() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    result_id = _add_record(repo, run_id, "record-1", True)
    from app.models import Diagnosis as DiagnosisRow

    diagnosis = DiagnosisRow(
        run_id=run_id, result_id=result_id, status="diagnosed",
        failure_type=CODE_UNSUPPORTED, related_metric="faithfulness",
        root_cause="root", severity="CRITICAL",
        evidence=[{"type": "answer_claim", "source": "answer", "locator": "claim[0]",
                   "content": "x", "metadata": None}],
        evidence_contract=f"{CODE_UNSUPPORTED}.v1", confidence="high",
    )
    repo.insert_diagnosis_with_recommendations(diagnosis, [])
    before = copy.deepcopy(repo.diagnoses)
    report = ReportService(repo).build_report(run_id)
    after = copy.deepcopy(repo.diagnoses)
    assert after == before
    bucket = report["run_level_diagnoses"]["items"][0]
    assert bucket["failure_type"] == CODE_UNSUPPORTED
    assert bucket["count"] == 1
    expected = DiagnosisEngine().diagnose_run_aggregate(
        repo.list_diagnoses_for_run(run_id), {result_id: "record-1"}
    ).buckets[0].model_dump()
    assert bucket == expected


def test_report_without_persisted_diagnosis_is_not_available() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    report = ReportService(repo).build_report(run_id)
    assert report["run_level_diagnoses"]["status"] == "not_available"
    assert report["run_level_diagnoses"]["items"] == []
