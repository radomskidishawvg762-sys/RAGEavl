"""Generation diagnosis checks (Phase 6) — pure deterministic evidence tests.

Covers: diagnosed / undetermined per rule, false-positive protection
(faithfulness low ≠ unsupported_claim), numeric base equivalence
(1亿元 == 10000万元), conflict strategy, BLOCKED irrelevance guard, and the
source guard (import-line scan).
"""

from __future__ import annotations

import asyncio

import pytest

from app.diagnosis import DiagnosisEngine  # noqa: F401 — engine dispatch under test
from app.diagnosis.generation import (
    CODE_PARTIAL,
    CODE_UNSUPPORTED,
    evaluate_generation,
)
from app.domain.schemas import EvaluationRecord, MetricResult
from app.engines.base import EvalParams
from app.services.evaluation_service import EvaluationService
from app.services.report_service import ReportService
from tests.test_evaluation_service import FakeEvaluationRepo


def _record(
    *,
    answer: str,
    contexts: list[str],
    reference_answer: str | None = None,
    reference_contexts: list[str] | None = None,
) -> EvaluationRecord:
    return EvaluationRecord(
        id="r1", question="q?", contexts=contexts, answer=answer,
        reference_answer=reference_answer, reference_contexts=reference_contexts,
        metadata={},
    )


def _summary_item(outcome):
    return next(i for i in outcome.bundle.items if i.type == "execution_evidence")


# ---------------- Rule 1: generation.unsupported_claim ----------------


def test_unsupported_claim_diagnosed() -> None:
    rec = _record(
        answer="公司营收为2亿元，净利润3百万元",
        contexts=["2024年营业收入为1亿元，同比增长10%"],
    )
    out = evaluate_generation(rec)
    assert out.status == "diagnosed" and out.code == CODE_UNSUPPORTED
    assert out.contract == "generation.unsupported_claim.v1"
    assert out.root_cause and "2/2" in out.root_cause
    types = {i.type for i in out.bundle.items}
    assert {"query_evidence", "answer_claim", "retrieved_evidence",
            "execution_evidence"} <= types
    summary = _summary_item(out).content
    assert summary["rule_name"] == "unsupported_claim"
    assert summary["answer_claim_count"] == 2
    assert summary["grounded_claim_count"] == 0
    assert summary["also_supported"] == []


def test_faithfulness_low_with_grounded_claim_is_not_unsupported() -> None:
    """Case: faithfulness low BUT the claim IS grounded -> never unsupported."""
    rec = _record(
        answer="2024年营业收入为1亿元",
        contexts=["2024年营业收入为1亿元，同比增长10%"],
    )
    out = evaluate_generation(rec)
    assert out.status == "undetermined" and out.code is None


def test_numeric_base_equivalence_grounded() -> None:
    """1亿元 must be recognized as grounded by 10000万元 (same base value)."""
    rec = _record(
        answer="营业收入为1亿元",
        contexts=["全年收入达到10000万元"],
    )
    out = evaluate_generation(rec)
    assert out.code is not CODE_UNSUPPORTED


def test_partial_grounding_is_undetermined() -> None:
    """Only SOME claims ungrounded -> conservative undetermined (extraction/
    paraphrase noise must not fabricate a grounding diagnosis)."""
    rec = _record(
        answer="营业收入为1亿元，净利润3千万",
        contexts=["2024年营业收入为1亿元"],
    )
    out = evaluate_generation(rec)
    assert out.status == "undetermined" and out.code is None


def test_unsupported_undetermined_without_answer_claims() -> None:
    out = evaluate_generation(_record(answer="无法从文档得出结论", contexts=["ctx"]))
    assert out.status == "undetermined"
    assert "answer_claim.checkable" in out.missing_evidence


def test_unsupported_undetermined_without_contexts() -> None:
    out = evaluate_generation(_record(answer="营收2亿元", contexts=[]))
    assert out.status == "undetermined"
    assert "retrieved_evidence" in out.missing_evidence


# ---------------- Rule 2: generation.partial_answer ----------------


def test_partial_answer_diagnosed() -> None:
    rec = _record(
        answer="2024年营业收入为1亿元",
        contexts=["2024年营业收入为1亿元，净利润3百万元，经营现金流5百万元"],
        reference_answer="营业收入1亿元、净利润3百万元、经营现金流5百万元",
    )
    out = evaluate_generation(rec)
    assert out.status == "diagnosed" and out.code == CODE_PARTIAL
    assert out.contract == "generation.partial_answer.v1"
    assert out.root_cause and "1/3" in out.root_cause
    summary = _summary_item(out).content
    assert summary["rule_name"] == "partial_answer"
    assert summary["required_reference_claims"] == 3
    assert summary["matched_reference_claims"] == 1
    assert summary["reference_claim_source"] == "reference_answer"


def test_partial_not_diagnosed_when_all_reference_claims_restated() -> None:
    rec = _record(
        answer="营收1亿元，净利润3百万元，现金流5百万元",
        contexts=["2024年营业收入为1亿元，净利润3百万元，经营现金流5百万元"],
        reference_answer="营业收入1亿元、净利润3百万元、经营现金流5百万元",
    )
    out = evaluate_generation(rec)
    assert out.status == "undetermined" and out.code is None


def test_partial_not_diagnosed_when_zero_reference_claims_restated() -> None:
    """0/N restated is ambiguous with irrelevance -> undetermined (never guess).
    The answer's own claim is grounded so unsupported_claim cannot fire either."""
    rec = _record(
        answer="现金流9百万元，负债7百万元",
        contexts=["经营现金流为9百万元"],
        reference_answer="营业收入1亿元、净利润3百万元、经营现金流5百万元",
    )
    out = evaluate_generation(rec)
    assert out.status == "undetermined" and out.code is not CODE_PARTIAL


def test_partial_requires_at_least_two_reference_claims() -> None:
    rec = _record(
        answer="与参考无关的内容",
        contexts=["ctx"],
        reference_answer="营业收入1亿元",
    )
    out = evaluate_generation(rec)
    assert out.status == "undetermined" and out.code is None


def test_partial_reference_contexts_source_traced() -> None:
    rec = _record(
        answer="营收1亿元",  # grounded in contexts -> unsupported cannot fire
        contexts=["2024年营业收入为1亿元"],
        reference_contexts=["营业收入1亿元", "净利润3百万元", "现金流5百万元"],
    )
    out = evaluate_generation(rec)
    assert out.code == CODE_PARTIAL
    assert _summary_item(out).content["reference_claim_source"] == "reference_contexts"


# ---------------- conflict strategy (§9 analogue) ----------------


def test_both_conclusive_unsupported_wins_with_also_supported() -> None:
    rec = _record(
        answer="营收2亿元，净利润3百万元",  # none grounded, 1/2 reference restated
        contexts=["2024年营业收入为1亿元"],
        reference_answer="营业收入1亿元、净利润3百万元",
    )
    out = evaluate_generation(rec)
    assert out.status == "diagnosed" and out.code == CODE_UNSUPPORTED
    summary = _summary_item(out).content
    assert summary["also_supported"] == [CODE_PARTIAL]  # preserved, never dropped


# ---------------- BLOCKED: generation.irrelevant_answer ----------------


def test_irrelevant_answer_never_diagnosed_without_semantic_evidence() -> None:
    """Irrelevance is semantic; with no checkable evidence the outcome must be
    undetermined — never generation.irrelevant_answer (BLOCKED rule)."""
    rec = _record(answer="无法从文档得出结论", contexts=["ctx"])
    out = evaluate_generation(rec)
    assert out.status == "undetermined"
    assert out.code is None and out.candidate is None


# ---------------- architecture guard ----------------


def test_generation_module_imports_are_pure() -> None:
    """Import-line guard: no engines / judge / ORM / HTTP / repositories /
    services. The T-09 extraction library (app.metrics.integrity, pure
    normalization) is the ONE sanctioned cross-package import."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/diagnosis/generation.py").read_text(
        encoding="utf-8")
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import ", "from "))]
    assert import_lines
    for line in import_lines:
        for forbidden in ("app.engines", "app.models", "app.services",
                          "app.repositories", "sqlalchemy", "httpx", "JudgeClient"):
            assert forbidden not in line, (forbidden, line)
    assert any("app.metrics.integrity" in ln for ln in import_lines)


# ---------------- triggers ----------------


@pytest.mark.parametrize("metric", ["faithfulness", "answer_relevancy"])
def test_trigger_metric_set_covers_generation_metrics(metric: str) -> None:
    from app.diagnosis.generation import GENERATION_TRIGGER_METRICS

    assert metric in GENERATION_TRIGGER_METRICS


# ---------------- service integration (§18 analogue) ----------------


class _FaithfulnessStubEngine:
    """Emits a low faithfulness score — the TRIGGER only; the diagnosis is
    decided by the deterministic numeric-claim comparison on the record."""

    name = "faith_stub"

    def metric_names(self):
        return ["faithfulness"]

    async def evaluate(self, record, metrics, params):
        return [MetricResult(
            record_id=record.id, metric_name="faithfulness", category="generation",
            score=0.3, threshold=0.8, metric_version="stub-v1",
        )]


def _seed_repo(answer: str, contexts: list[str], reference_answer: str) -> FakeEvaluationRepo:
    from app.models import DatasetRecord

    return FakeEvaluationRepo(
        {"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}},
        [DatasetRecord(
            id="r1", dataset_id="ds1", row_index=0, question="q?",
            reference_answer=reference_answer, reference_contexts=None,
            metadata_={"answer": answer, "contexts": contexts},
        )],
    )


def test_service_level_unsupported_claim_end_to_end() -> None:
    repo = _seed_repo(
        answer="公司营收为2亿元，净利润3百万元",  # claims absent from contexts
        contexts=["2024年营业收入为1亿元，同比增长10%"],
        reference_answer="营业收入1亿元、净利润3百万元",
    )
    svc = EvaluationService(repo)
    run = svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1",
                         reproducibility_meta={"config_version": "x", "metric_version": "stub-v1"})
    summary = asyncio.run(svc.execute_run(
        run.id, engines=[_FaithfulnessStubEngine()], enabled_metrics=["faithfulness"],
        params=EvalParams(),  # default DiagnosisEngine — generation dispatch active
    ))
    assert summary["status"] == "completed"

    assert len(repo.diagnoses) == 1
    d = repo.diagnoses[0]
    assert d["status"] == "diagnosed"
    assert d["failure_type"] == "generation.unsupported_claim"
    assert d["related_metric"] == "faithfulness"
    assert d["evidence_contract"] == "generation.unsupported_claim.v1"
    assert d["severity"] == "CRITICAL"  # taxonomy hint
    summary_item = next(e for e in d["evidence"] if e["type"] == "execution_evidence")
    assert summary_item["content"]["rule_name"] == "unsupported_claim"
    assert summary_item["content"]["grounded_claim_count"] == 0

    # run-level aggregation surfaces the generation.* bucket (Phase 4, untouched)
    rep = ReportService(repo).build_report(run.id)
    (bucket,) = rep["run_level_diagnoses"]["items"]
    assert bucket["failure_type"] == "generation.unsupported_claim"
    assert bucket["count"] == 1


def test_service_level_faithfulness_low_but_grounded_stays_undetermined() -> None:
    """Score-only protection: faithfulness failure + fully grounded claims ->
    NO generation diagnosis is fabricated."""
    repo = _seed_repo(
        answer="2024年营业收入为1亿元",
        contexts=["2024年营业收入为1亿元，同比增长10%"],
        reference_answer="营业收入1亿元",
    )
    svc = EvaluationService(repo)
    run = svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1",
                         reproducibility_meta={"config_version": "x", "metric_version": "stub-v1"})
    asyncio.run(svc.execute_run(
        run.id, engines=[_FaithfulnessStubEngine()], enabled_metrics=["faithfulness"],
        params=EvalParams(),
    ))
    # a diagnosis row exists (undetermined persistence contract) but attributes nothing
    assert all(d["status"] == "undetermined" and d["root_cause"] is None
               for d in repo.diagnoses)
    assert all(d["failure_type"] != "generation.unsupported_claim" for d in repo.diagnoses)
