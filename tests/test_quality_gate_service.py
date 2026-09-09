"""T-17 tests — QualityGateService (pure evaluation + chain integration).

The 17 mandated points. evaluate() runs against the shared FakeEvaluationRepo
(no DB) with a fake config service supplying the Profile quality_gate config.
Engine/Judge/RAG/Diagnosis are verified absent by source reading (14-16); the
frontend has no gate logic (17).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.main import app  # noqa: F401 — ensures bootstrap_metrics registers specs
from app.models import EvaluationResult, MetricResult
from app.services.quality_gate_service import (
    QUALITY_GATE_CONFIG_INVALID,
    QUALITY_GATE_DISABLED,
    QUALITY_GATE_NOT_CONFIGURED,
    QUALITY_THRESHOLD_FAILED,
    REQUIRED_METRIC_NOT_EVALUABLE,
    QualityGateService,
)
from tests.frontend_scan import GATE_PATTERNS, frontend_compute_violations

ROOT = Path(__file__).resolve().parents[1]

QG = {"enabled": True, "required_metrics": ["faithfulness", "context_recall"]}


def _merged(quality_gate=None) -> dict:
    return {"quality_gate": quality_gate}


def _qg(required, enabled=True) -> dict:
    return {"enabled": enabled, "required_metrics": list(required)}


def _meta(enabled, weights=None) -> dict:
    return {
        "dataset_version": "v3", "config_version": "cfg", "metric_version": "ragas-0.4.3",
        "enabled_metrics": list(enabled),
        "metric_weights": weights or {n: 1.0 for n in enabled},
    }


class _FakeConfig:
    def __init__(self, merged: dict) -> None:
        self._merged = merged

    def load(self, domain: str = "general", profile: str = "default", profile_body: dict | None = None) -> dict:
        return self._merged


def _repo(configs=None):
    from tests.test_comparison_service import _repo as _make_repo

    repo = _make_repo()
    repo.configs = configs or {"c1": SimpleNamespace(
        domain_config={"domain": "general"}, profile_config={"profile": "default"})}
    return repo


def _add_metric(repo, run_id, *, metric_name, category, score, threshold,
                error=None, comparison_basis=None):
    result = repo.insert_evaluation_result(EvaluationResult(
        run_id=run_id, record_id="r1", row_index=0, question="q", contexts=[],
        answer="a", reference_answer=None, reference_contexts=None, is_failure=False,
    ))
    repo.insert_metric_results([MetricResult(
        result_id=result.id, metric_name=metric_name, category=category, score=score,
        threshold=threshold, passed=None, comparison_basis=comparison_basis,
        metric_version="ragas-0.4.3", error=error,
    )])


def _seed_run(repo, run_id, *, meta, metric_rows, overall_score=None):
    repo.runs[run_id] = {
        "id": run_id, "project_id": "p1", "dataset_id": "ds1", "config_id": "c1",
        "status": "completed", "total_records": 1, "evaluated_records": 1, "error_records": 0,
        "evaluation_coverage": 1.0, "overall_score": overall_score,
        "reproducibility_meta": meta, "started_at": None, "finished_at": None,
        "error_summary": None, "created_at": datetime.now(UTC),
    }
    for row in metric_rows:
        _add_metric(repo, run_id, **row)
    return run_id


def _gate(repo, merged) -> dict:
    return QualityGateService(repo, _FakeConfig(merged)).evaluate("run1")


def _metric(resp: dict, name: str) -> dict:
    return next(m for m in resp["metrics"] if m["metric"] == name)


# ---- 1-4: threshold verdicts + direction ----

def test_1_all_required_metrics_meet_threshold_pass() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness", "context_recall"]), overall_score=0.9,
              metric_rows=[
                  {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
                  {"metric_name": "context_recall", "category": "retrieval", "score": 0.85, "threshold": 0.8},
              ])
    resp = _gate(repo, _merged(QG))
    assert resp["status"] == "PASS"
    assert resp["reasons"] == []
    assert all(m["status"] == "PASS" and m["passed"] is True for m in resp["metrics"])
    assert resp["overall_score"] == pytest.approx(0.9)


def test_2_one_required_metric_below_threshold_fails() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness", "context_recall"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
        {"metric_name": "context_recall", "category": "retrieval", "score": 0.75, "threshold": 0.8},
    ])
    resp = _gate(repo, _merged(QG))
    assert resp["status"] == "FAIL"
    assert QUALITY_THRESHOLD_FAILED in resp["reasons"]
    assert _metric(resp, "context_recall")["status"] == "FAIL"
    assert _metric(resp, "context_recall")["passed"] is False
    assert _metric(resp, "faithfulness")["status"] == "PASS"


def test_3_higher_is_better_boundary() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.80, "threshold": 0.80},
    ])
    assert _gate(repo, _merged(_qg(["faithfulness"])))["status"] == "PASS"  # >=
    repo2 = _repo()
    _seed_run(repo2, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.79, "threshold": 0.80},
    ])
    assert _gate(repo2, _merged(_qg(["faithfulness"])))["status"] == "FAIL"  # <


def test_4_lower_is_better(monkeypatch) -> None:
    monkeypatch.setattr("app.services.quality_gate_service.metric_direction",
                        lambda _n: "lower_is_better")
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["answer_latency"]), metric_rows=[
        {"metric_name": "answer_latency", "category": "generation", "score": 5.0, "threshold": 6.0},
    ])
    assert _gate(repo, _merged(_qg(["answer_latency"])))["status"] == "PASS"  # 5 <= 6
    repo2 = _repo()
    _seed_run(repo2, "run1", meta=_meta(["answer_latency"]), metric_rows=[
        {"metric_name": "answer_latency", "category": "generation", "score": 7.0, "threshold": 6.0},
    ])
    assert _gate(repo2, _merged(_qg(["answer_latency"])))["status"] == "FAIL"  # 7 > 6


# ---- 5-8: null score is NOT a quality failure ----

def test_5_null_score_is_not_evaluable_not_quality_failure() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": None, "threshold": 0.8,
         "comparison_basis": {"comparison_type": "ambiguous", "method": "deterministic"}},
    ])
    resp = _gate(repo, _merged(_qg(["faithfulness"])))
    m = _metric(resp, "faithfulness")
    assert m["status"] == "NOT_EVALUABLE"
    assert m["reason"] == "undetermined"
    assert m["score"] is None and m["passed"] is None  # NEVER coerced to 0
    assert resp["status"] == "FAIL"
    assert REQUIRED_METRIC_NOT_EVALUABLE in resp["reasons"]


def test_6_not_configured_required_metric_fails_gate() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": None, "threshold": 0.8,
         "error": {"code": "BIZ_JUDGE_NOT_CONFIGURED", "reason": "judge_not_configured"}},
    ])
    resp = _gate(repo, _merged(_qg(["faithfulness"])))
    assert resp["status"] == "FAIL"
    assert REQUIRED_METRIC_NOT_EVALUABLE in resp["reasons"]
    assert _metric(resp, "faithfulness")["reason"] == "not_configured"


def test_7_execution_error_required_metric_fails_gate() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": None, "threshold": 0.8,
         "error": {"code": "SYS_METRIC_ERROR", "message": "boom"}},
    ])
    resp = _gate(repo, _merged(_qg(["faithfulness"])))
    assert resp["status"] == "FAIL"
    assert REQUIRED_METRIC_NOT_EVALUABLE in resp["reasons"]
    assert _metric(resp, "faithfulness")["reason"] == "execution_error"


def test_8_undetermined_required_metric_fails_gate() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": None, "threshold": 0.8,
         "comparison_basis": {"comparison_type": "ambiguous", "method": "deterministic"}},
    ])
    resp = _gate(repo, _merged(_qg(["faithfulness"])))
    assert resp["status"] == "FAIL"
    assert REQUIRED_METRIC_NOT_EVALUABLE in resp["reasons"]  # gate reason, not the raw word
    assert _metric(resp, "faithfulness")["reason"] == "undetermined"


# ---- 9-11: config-level NOT_EVALUABLE ----

def test_9_required_metric_not_in_enabled_is_config_invalid() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
    ])
    resp = _gate(repo, _merged(_qg(["faithfulness", "context_recall"])))
    assert resp["status"] == "NOT_EVALUABLE"
    assert QUALITY_GATE_CONFIG_INVALID in resp["reasons"]
    assert _metric(resp, "context_recall")["reason"] == "not_in_enabled_metrics"


def test_10_gate_disabled_is_not_evaluable() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
    ])
    resp = _gate(repo, _merged(_qg(["faithfulness"], enabled=False)))
    assert resp["status"] == "NOT_EVALUABLE"
    assert QUALITY_GATE_DISABLED in resp["reasons"]
    assert resp["metrics"] == []


def test_11_gate_missing_config_is_not_evaluable() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
    ])
    resp = _gate(repo, _merged(None))  # quality_gate: null in profile
    assert resp["status"] == "NOT_EVALUABLE"
    assert QUALITY_GATE_NOT_CONFIGURED in resp["reasons"]
    assert resp["metrics"] == []


# ---- 12-13: no epsilon, no overall substitution ----

def test_12_gate_never_uses_regression_epsilon() -> None:
    src = (ROOT / "app/services/quality_gate_service.py").read_text(encoding="utf-8")
    for forbidden in ("resolve_epsilon", "regression_service", "RegressionService", "delta"):
        assert forbidden not in src, forbidden
    assert "threshold" in src  # absolute-threshold compare, not delta/epsilon


def test_13_overall_score_does_not_substitute_required_metric() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness", "context_recall"]),
              overall_score=0.95, metric_rows=[
                  {"metric_name": "faithfulness", "category": "generation", "score": 0.70, "threshold": 0.80},
                  {"metric_name": "context_recall", "category": "retrieval", "score": 0.99, "threshold": 0.80},
              ])
    resp = _gate(repo, _merged(QG))
    assert resp["status"] == "FAIL"  # NOT PASS despite high overall
    assert QUALITY_THRESHOLD_FAILED in resp["reasons"]
    assert resp["overall_score"] == pytest.approx(0.95)  # auxiliary only


# ---- 14-17: no Engine / Judge / RAG / frontend logic ----

def test_14_gate_never_calls_engine_or_diagnosis() -> None:
    src = (ROOT / "app/services/quality_gate_service.py").read_text(encoding="utf-8")
    for forbidden in ("from app.engines", "build_engines", "from app.diagnosis", "async def"):
        assert forbidden not in src, forbidden


def test_15_gate_never_calls_judge() -> None:
    src = (ROOT / "app/services/quality_gate_service.py").read_text(encoding="utf-8")
    for forbidden in ("JudgeClient", "from app.engines.judge", "JudgeEngine"):
        assert forbidden not in src, forbidden


def test_16_gate_never_calls_rag() -> None:
    src = (ROOT / "app/services/quality_gate_service.py").read_text(encoding="utf-8")
    for forbidden in ("from app.adapters", "rag_adapter", "RagOutput", "HttpRagAdapter"):
        assert forbidden not in src, forbidden


def test_17_frontend_has_no_gate_business_logic() -> None:
    # Invariant (gate domain): the frontend renders the backend quality-gate status
    # (status.ts) and a graceful-degradation label (useEvaluations), but must never
    # derive PASS/FAIL by comparing a score/threshold. Any such comparison is a
    # computation violation. Regression assertion: no non-api file recomputes
    # overall_score — that value comes from the backend only.
    violations = frontend_compute_violations(GATE_PATTERNS)
    assert violations == [], [
        f"{path} derives a gate verdict ({pat!r} -> {matched!r})"
        for path, pat, matched in violations
    ]
    recompute = frontend_compute_violations(
        [r"\boverall_score\s*=[^=]", r"\boverallScore\s*=[^=]"]
    )
    assert recompute == [], [
        f"{path} recomputes overall_score ({pat!r} -> {matched!r})"
        for path, pat, matched in recompute
    ]


# ---- extras: chain + 404 ----

def test_required_metric_with_no_rows_is_not_run() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[])
    resp = _gate(repo, _merged(_qg(["faithfulness"])))
    assert resp["status"] == "FAIL"
    assert REQUIRED_METRIC_NOT_EVALUABLE in resp["reasons"]
    assert _metric(resp, "faithfulness")["reason"] == "not_run"


def test_missing_run_is_404() -> None:
    from app.core.errors import NotFoundError

    repo = _repo()
    with pytest.raises(NotFoundError):
        QualityGateService(repo, _FakeConfig(_merged(QG))).evaluate("nope")
