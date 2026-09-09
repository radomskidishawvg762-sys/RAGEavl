"""Retrieval diagnosis checks (Phase 5) — pure deterministic evidence tests.

Covers the approval test matrix: diagnosed / undetermined / false-positive
protection / ambiguity per rule, Cases A-F, the conflict strategy, and the
source guard (no Engine/Judge/ORM/HTTP imports in the rule module).
"""

from __future__ import annotations

import asyncio

import pytest

from app.diagnosis import DiagnosisEngine
from app.diagnosis.retrieval import (
    CODE_MISSING,
    CODE_TOP_K,
    evaluate_retrieval,
    normalize_text,
)
from app.domain.schemas import EvaluationRecord, MetricResult
from app.engines.base import EvalParams
from app.services.evaluation_service import EvaluationService
from app.services.report_service import ReportService
from tests.test_evaluation_service import FakeEvaluationRepo


def _record(
    *,
    contexts: list[str],
    reference_contexts: list[str] | None = None,
    reference_answer: str | None = None,
    rag_meta: dict | None = None,
) -> EvaluationRecord:
    metadata = {"rag": rag_meta} if rag_meta is not None else {}
    return EvaluationRecord(
        id="r1", question="q?", contexts=contexts, answer="a",
        reference_answer=reference_answer, reference_contexts=reference_contexts,
        metadata=metadata,
    )


def _summary_item(outcome):
    return next(i for i in outcome.bundle.items if i.type == "execution_evidence")


# ---------------- normalization ----------------


def test_normalization_is_deterministic() -> None:
    assert normalize_text("营收：1亿元！") == normalize_text("营收 1亿元")
    assert normalize_text("  A  B  ") == "a b"
    assert normalize_text("!!!") == ""  # not reliably comparable


# ---------------- Rule 1: retrieval.missing_evidence ----------------


def test_missing_evidence_diagnosed_case_e() -> None:
    rec = _record(
        contexts=["公司利润大幅增长", "其他无关内容"],
        reference_contexts=["营业收入为1亿元", "净利润为3千万"],
    )
    out = evaluate_retrieval(rec)
    assert out.status == "diagnosed" and out.code == CODE_MISSING
    assert out.contract == "retrieval.missing_evidence.v1"
    assert out.root_cause and "0/2" in out.root_cause
    types = {i.type for i in out.bundle.items}
    assert {"query_evidence", "reference_evidence", "retrieved_evidence",
            "execution_evidence"} <= types
    summary = _summary_item(out).content
    assert summary["rule_name"] == "missing_gold_evidence"
    assert summary["gold_source"] == "reference_contexts"
    assert summary["required_evidence_count"] == 2
    assert summary["matched_gold_count"] == 0
    assert summary["configured_top_k"] is None
    assert summary["also_supported"] == []


def test_missing_evidence_undetermined_without_gold() -> None:
    out = evaluate_retrieval(_record(contexts=["ctx"], reference_answer=None))
    assert out.status == "undetermined" and "reference_evidence" in out.missing_evidence


def test_missing_evidence_undetermined_without_retrieved() -> None:
    out = evaluate_retrieval(
        _record(contexts=[], reference_contexts=["gold"]))
    assert out.status == "undetermined" and "retrieved_evidence" in out.missing_evidence


def test_case_a_gold_retrieved_is_not_missing_evidence() -> None:
    """context_recall low, but gold evidence IS retrieved -> NOT missing_evidence."""
    rec = _record(
        contexts=["2024年营业收入为1亿元，同比增长10%"],
        reference_contexts=["2024年营业收入为1亿元。"],
    )
    out = evaluate_retrieval(rec)
    assert out.status == "undetermined"
    assert out.code is None and out.candidate is None


def test_normalization_absorbs_punctuation_and_case() -> None:
    rec = _record(
        contexts=["FY2024 REVENUE: 1亿元。"],
        reference_contexts=["fy2024 revenue 1亿元"],
    )
    out = evaluate_retrieval(rec)
    assert out.status == "undetermined"  # gold present -> never missing


def test_gold_answer_fallback_source_is_traced() -> None:
    rec = _record(contexts=["无关内容"], reference_answer="营业收入为1亿元")
    out = evaluate_retrieval(rec)
    assert out.status == "diagnosed" and out.code == CODE_MISSING
    assert _summary_item(out).content["gold_source"] == "reference_answer"


# ---------------- Rule 2: retrieval.top_k_issue ----------------


def test_top_k_issue_diagnosed_case_f() -> None:
    rec = _record(
        contexts=["chunk-1 (含 gold-A)", "chunk-2", "chunk-3"],
        reference_contexts=["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"],
        rag_meta={"top_k": 3},
    )
    out = evaluate_retrieval(rec)
    assert out.status == "diagnosed" and out.code == CODE_TOP_K
    assert out.contract == "retrieval.top_k_issue.v1"
    assert out.root_cause and "configured_top_k=3" in out.root_cause
    summary = _summary_item(out).content
    assert summary["rule_name"] == "top_k_insufficient"
    assert summary["configured_top_k"] == 3
    assert summary["required_evidence_count"] == 5
    assert summary["retrieved_evidence_count"] == 3
    assert summary["missing_gold_count"] == 4
    types = {i.type for i in out.bundle.items}
    assert "configuration_evidence" in types


def test_case_b_top_k_missing_never_diagnoses_top_k() -> None:
    """context_recall low, top_k datum absent -> NOT top_k_issue (partial gold
    present means missing_evidence cannot fire either -> undetermined)."""
    rec = _record(
        contexts=["chunk-1 (含 gold-A)", "chunk-2", "chunk-3"],
        reference_contexts=["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"],
    )
    out = evaluate_retrieval(rec)
    assert out.status == "undetermined"
    assert out.code is not CODE_TOP_K and out.candidate is None


def test_top_k_invalid_datum_is_not_conclusive() -> None:
    for bad in (0, -1, "3", True, 2.5):
        rec = _record(
            contexts=["c1 (含 gold-A)", "c2", "c3"],
            reference_contexts=["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"],
            rag_meta={"top_k": bad},
        )
        out = evaluate_retrieval(rec)
        assert out.status == "undetermined", bad
        assert "invalid" in out.reason


def test_case_c_cap_not_saturated_is_not_top_k_issue() -> None:
    """retrieved_count < top_k: the cap did not bind -> never top_k_issue."""
    rec = _record(
        contexts=["chunk-1", "chunk-2"],  # 2 < top_k=5
        reference_contexts=["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"],
        rag_meta={"top_k": 5},
    )
    out = evaluate_retrieval(rec)
    assert out.code is not CODE_TOP_K
    # all gold missing + comparison reliable -> the observation-level rule holds
    assert out.status == "diagnosed" and out.code == CODE_MISSING


def test_case_c_partial_missing_with_unsaturated_cap_is_undetermined() -> None:
    rec = _record(
        contexts=["chunk-1 (含 gold-A)", "chunk-2"],  # 2 < top_k=5
        reference_contexts=["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"],
        rag_meta={"top_k": 5},
    )
    out = evaluate_retrieval(rec)
    assert out.status == "undetermined" and out.code is None


def test_case_d_unreliable_comparison_is_undetermined() -> None:
    """Counts look right (top_k=3, retrieved=3, gold=5) but the gold items
    cannot be reliably compared -> undetermined, never a guess."""
    rec = _record(
        contexts=["chunk-1", "chunk-2", "chunk-3"],
        reference_contexts=["  ", "", "gold-C", "gold-D", "gold-E"],
        rag_meta={"top_k": 3},
    )
    out = evaluate_retrieval(rec)
    assert out.status == "undetermined"
    assert out.code is None
    assert "not reliably comparable" in out.reason


def test_blank_retrieved_contexts_are_not_usable_evidence() -> None:
    rec = _record(contexts=["   ", ""], reference_contexts=["gold-A"])
    out = evaluate_retrieval(rec)
    assert out.status == "undetermined"
    assert "retrieved_evidence" in out.missing_evidence


# ---------------- conflict strategy (approval §9) ----------------


def test_both_conclusive_top_k_wins_with_also_supported() -> None:
    rec = _record(
        contexts=["chunk-1", "chunk-2", "chunk-3"],  # no gold at all, cap saturated
        reference_contexts=["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"],
        rag_meta={"top_k": 3},
    )
    out = evaluate_retrieval(rec)
    assert out.status == "diagnosed" and out.code == CODE_TOP_K
    summary = _summary_item(out).content
    assert summary["also_supported"] == [CODE_MISSING]  # preserved, never dropped


# ---------------- architecture guard ----------------


def test_retrieval_module_imports_no_engines_orm_or_http() -> None:
    """Guard applies to IMPORT statements — the module must not reach engines,
    ORM models/sessions, repositories, or HTTP clients. Prose mentions of the
    words in the module docstring are not imports and are ignored."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/diagnosis/retrieval.py").read_text(
        encoding="utf-8")
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import ", "from "))]
    assert import_lines  # sanity: the scan actually saw the module imports
    for line in import_lines:
        for forbidden in ("app.engines", "app.models", "app.services",
                          "app.repositories", "sqlalchemy", "httpx", "JudgeClient"):
            assert forbidden not in line, (forbidden, line)


@pytest.mark.parametrize("metric", ["context_recall", "context_precision"])
def test_trigger_metric_set_covers_retrieval_metrics(metric: str) -> None:
    from app.diagnosis.retrieval import RETRIEVAL_TRIGGER_METRICS

    assert metric in RETRIEVAL_TRIGGER_METRICS


# ---------------- service integration (approval §18) ----------------


def _row(rid: str, contexts: list[str], reference_contexts: list[str],
         rag_meta: dict | None = None):
    from app.models import DatasetRecord

    return DatasetRecord(
        id=rid, dataset_id="ds1", row_index=0, question="q?",
        reference_answer=None, reference_contexts=reference_contexts,
        metadata_={"answer": "a", "contexts": contexts, "rag": rag_meta or {}},
    )


class _RecallStubEngine:
    """Emits a low context_recall score — the TRIGGER only; the diagnosis is
    decided by the deterministic evidence comparison on the record."""

    name = "recall_stub"

    def metric_names(self):
        return ["context_recall"]

    async def evaluate(self, record, metrics, params):
        return [MetricResult(
            record_id=record.id, metric_name="context_recall", category="retrieval",
            score=0.2, threshold=0.8, metric_version="stub-v1",
        )]


def _service_records(repo, records):
    repo.records.extend(records)


def test_service_level_retrieval_diagnosis_end_to_end() -> None:
    repo = FakeEvaluationRepo(
        {"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}},
        [_row("r1", ["完全无关的文档内容"], ["gold 营业收入为1亿元"])],
    )
    svc = EvaluationService(repo)
    run = svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1",
                         reproducibility_meta={"config_version": "x", "metric_version": "stub-v1"})
    summary = asyncio.run(svc.execute_run(
        run.id, engines=[_RecallStubEngine()], enabled_metrics=["context_recall"],
        params=EvalParams(),  # default DiagnosisEngine — retrieval dispatch active
    ))
    assert summary["status"] == "completed"

    # sample-level retrieval diagnosis persisted with full evidence
    assert len(repo.diagnoses) == 1
    d = repo.diagnoses[0]
    assert d["status"] == "diagnosed"
    assert d["failure_type"] == "retrieval.missing_evidence"
    assert d["related_metric"] == "context_recall"
    assert d["evidence_contract"] == "retrieval.missing_evidence.v1"
    evidence_types = {e["type"] for e in d["evidence"]}
    assert {"query_evidence", "reference_evidence", "retrieved_evidence",
            "execution_evidence"} <= evidence_types
    summary_item = next(e for e in d["evidence"] if e["type"] == "execution_evidence")
    assert summary_item["content"]["rule_name"] == "missing_gold_evidence"
    assert d["root_cause"] and "0/1" in d["root_cause"]

    # run-level aggregation surfaces the retrieval.* bucket (Phase 4, untouched)
    rep = ReportService(repo).build_report(run.id)
    rld = rep["run_level_diagnoses"]
    assert rld["status"] == "available"
    (bucket,) = rld["items"]
    assert bucket["failure_type"] == "retrieval.missing_evidence"
    assert bucket["count"] == 1 and bucket["ratio"] == pytest.approx(1.0)


def test_service_level_top_k_issue_end_to_end() -> None:
    repo = FakeEvaluationRepo(
        {"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}},
        [_row("r1",
              ["chunk-1 (含 gold-A)", "chunk-2", "chunk-3"],
              ["gold-A", "gold-B", "gold-C", "gold-D", "gold-E"],
              rag_meta={"top_k": 3})],
    )
    svc = EvaluationService(repo)
    run = svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1",
                         reproducibility_meta={"config_version": "x", "metric_version": "stub-v1"})
    asyncio.run(svc.execute_run(
        run.id, engines=[_RecallStubEngine()], enabled_metrics=["context_recall"],
        params=EvalParams(), diagnosis_engine=DiagnosisEngine(),
    ))
    assert len(repo.diagnoses) == 1
    d = repo.diagnoses[0]
    assert d["failure_type"] == "retrieval.top_k_issue"
    assert d["severity"] == "WARNING"  # taxonomy hint (no profile override)
    summary_item = next(e for e in d["evidence"] if e["type"] == "execution_evidence")
    assert summary_item["content"]["configured_top_k"] == 3
    assert summary_item["content"]["required_evidence_count"] == 5
    # interaction "top_k issue ONLY": gold-A IS retrieved, so missing_evidence
    # is not conclusively supported (approval §17 interaction matrix case 2)
    assert summary_item["content"]["also_supported"] == []
