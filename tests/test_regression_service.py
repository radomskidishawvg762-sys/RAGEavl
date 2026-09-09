"""T-16C tests — RegressionService (pure analysis + chain integration).

The 17 mandated points. analyze() is exercised with hand-built ComparisonService
output dicts (pure, no repo); the compute() chain is covered by one integration
test reusing the shared FakeEvaluationRepo. Engine/Judge/RAG/Diagnosis verified
absent by source reading (13-15); the frontend has no verdict logic (16).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.main import app  # noqa: F401 — ensures bootstrap_metrics registers specs
from app.services.comparison_service import ComparisonService
from app.services.regression_service import RegressionService, resolve_epsilon
from tests.frontend_scan import (
    REGRESSION_PATTERNS,
    frontend_compute_violations,
    frontend_enum_files,
)

ROOT = Path(__file__).resolve().parents[1]

EPSILON = 0.01


def _comparison(metrics, *, status="DIRECT", reasons=None, overall=None) -> dict:
    return {
        "baseline_run_id": "base",
        "candidate_run_id": "cand",
        "comparability": {"status": status, "reasons": reasons or []},
        "metrics": metrics,
        "overall": overall or {"baseline_overall": None, "candidate_overall": None,
                               "delta": None, "comparable": False, "reason": None},
        "generated_at": datetime.now(UTC),
    }


def _m(name, category, baseline, candidate, *, comparable=True, reason=None) -> dict:
    delta = None
    if baseline is not None and candidate is not None:
        delta = round(candidate - baseline, 9)
    return {
        "name": name,
        "category": category,
        "baseline_score": baseline,
        "candidate_score": candidate,
        "delta": delta,
        "relative_delta": None,
        "baseline_status": "completed" if baseline is not None else "error",
        "candidate_status": "completed" if candidate is not None else "error",
        "metric_version": "ragas-0.4.3",
        "comparable": comparable,
        "incomparable_reason": reason,
    }


def _svc(epsilon: float = EPSILON, source: str = "provisional_default") -> RegressionService:
    return RegressionService(None, epsilon=epsilon, epsilon_source=source)


def _analyze(metrics, **kw) -> dict:
    return _svc().analyze(_comparison(metrics, **kw))


# ---- 1-4: direction + delta verdicts ----

def test_1_higher_negative_delta_is_regression() -> None:
    resp = _analyze([_m("faithfulness", "generation", 0.91, 0.87)])
    item = resp["metrics"][0]
    assert item["verdict"] == "REGRESSION"
    assert item["direction"] == "higher_is_better"
    assert item["delta"] == pytest.approx(-0.04)
    assert resp["overall"]["verdict"] == "REGRESSION"


def test_2_higher_positive_delta_is_improvement() -> None:
    resp = _analyze([_m("faithfulness", "generation", 0.87, 0.91)])
    assert resp["metrics"][0]["verdict"] == "IMPROVEMENT"
    assert resp["overall"]["verdict"] == "IMPROVEMENT"


def test_3_abs_delta_within_epsilon_is_stable() -> None:
    resp = _analyze([_m("faithfulness", "generation", 0.90, 0.905)])  # +0.005 <= 0.01
    assert resp["metrics"][0]["verdict"] == "STABLE"
    assert resp["overall"]["verdict"] == "STABLE"


def test_4_lower_is_better_positive_delta_is_regression(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.regression_service._direction", lambda _n: "lower_is_better"
    )
    resp = _analyze([_m("answer_latency", "generation", 5.0, 6.0)])  # +1.0
    item = resp["metrics"][0]
    assert item["verdict"] == "REGRESSION"
    assert item["direction"] == "lower_is_better"
    resp2 = _analyze([_m("answer_latency", "generation", 6.0, 5.0)])  # -1.0
    assert resp2["metrics"][0]["verdict"] == "IMPROVEMENT"


# ---- 5-6: null score + epsilon provenance ----

def test_5_null_score_is_never_regression() -> None:
    err = _analyze([_m("faithfulness", "generation", 0.80, None,
                       comparable=False, reason="execution_error")])
    und = _analyze([_m("faithfulness", "generation", None, 0.80,
                       comparable=False, reason="undetermined")])
    assert err["metrics"][0]["verdict"] == "NOT_COMPARABLE"
    assert und["metrics"][0]["verdict"] == "UNDETERMINED"
    for r in (err, und):
        assert r["metrics"][0]["verdict"] not in {"REGRESSION", "IMPROVEMENT"}


def test_6_epsilon_is_provisional_not_financial_standard() -> None:
    assert resolve_epsilon({}) == (0.01, "provisional_default")
    assert resolve_epsilon({"system": {"regression": {"epsilon": 0.05}}}) == (0.05, "config")
    item = _analyze([_m("faithfulness", "generation", 0.90, 0.80)])["metrics"][0]
    assert item["epsilon"] == 0.01
    assert item["epsilon_source"] == "provisional_default"


# ---- 7-11: category aggregation + trade-off + overall ----

def test_7_retrieval_up_generation_down_is_trade_off() -> None:
    resp = _analyze([
        _m("context_recall", "retrieval", 0.80, 0.90),   # +0.10 IMPROVEMENT
        _m("faithfulness", "generation", 0.90, 0.80),    # -0.10 REGRESSION
    ])
    assert resp["categories"]["retrieval"]["verdict"] == "IMPROVEMENT"
    assert resp["categories"]["retrieval"]["improvement_count"] == 1
    assert resp["categories"]["generation"]["verdict"] == "REGRESSION"
    assert resp["categories"]["generation"]["regression_count"] == 1
    assert resp["trade_off"] is True
    assert resp["overall"]["verdict"] == "MIXED"


def test_8_retrieval_down_generation_up_is_trade_off() -> None:
    resp = _analyze([
        _m("context_recall", "retrieval", 0.90, 0.80),   # REGRESSION
        _m("faithfulness", "generation", 0.80, 0.90),    # IMPROVEMENT
    ])
    assert resp["trade_off"] is True


def test_9_all_metrics_improve_is_improvement() -> None:
    resp = _analyze([
        _m("context_recall", "retrieval", 0.80, 0.90),
        _m("context_precision", "retrieval", 0.70, 0.80),
        _m("faithfulness", "generation", 0.80, 0.90),
    ])
    assert resp["categories"]["retrieval"]["improvement_count"] == 2
    assert resp["categories"]["retrieval"]["verdict"] == "IMPROVEMENT"
    assert resp["overall"]["verdict"] == "IMPROVEMENT"
    assert resp["trade_off"] is False


def test_10_all_metrics_regress_is_regression() -> None:
    resp = _analyze([
        _m("context_recall", "retrieval", 0.90, 0.80),
        _m("faithfulness", "generation", 0.90, 0.80),
    ])
    assert resp["overall"]["verdict"] == "REGRESSION"


def test_11_mixed_directions_is_mixed() -> None:
    resp = _analyze([
        _m("context_recall", "retrieval", 0.80, 0.90),
        _m("faithfulness", "generation", 0.90, 0.80),
    ])
    assert resp["overall"]["verdict"] == "MIXED"
    assert resp["overall"]["reason"]


# ---- 12: overall score never drives the verdict ----

def test_12_overall_delta_does_not_drive_verdict() -> None:
    resp = _analyze([
        _m("context_recall", "retrieval", 0.80, 0.90),   # IMPROVEMENT
        _m("faithfulness", "generation", 0.90, 0.80),    # REGRESSION
    ], overall={"baseline_overall": 0.85, "candidate_overall": 0.88,
                "delta": 0.03, "comparable": True, "reason": None})
    assert resp["overall"]["verdict"] == "MIXED"  # NOT IMPROVEMENT despite +0.03
    assert resp["overall"]["overall_score_delta"] == pytest.approx(0.03)  # auxiliary only


# ---- 13-16: no Engine / Judge / RAG / frontend logic ----

def test_13_regression_never_calls_engine_or_diagnosis() -> None:
    src = (ROOT / "app/services/regression_service.py").read_text(encoding="utf-8")
    for forbidden in ("from app.engines", "build_engines", "from app.diagnosis", "async def"):
        assert forbidden not in src, forbidden


def test_14_regression_never_calls_judge() -> None:
    src = (ROOT / "app/services/regression_service.py").read_text(encoding="utf-8")
    for forbidden in ("JudgeClient", "from app.engines.judge", "JudgeEngine"):
        assert forbidden not in src, forbidden


def test_15_regression_never_calls_rag() -> None:
    src = (ROOT / "app/services/regression_service.py").read_text(encoding="utf-8")
    for forbidden in ("from app.adapters", "rag_adapter", "RagOutput", "HttpRagAdapter"):
        assert forbidden not in src, forbidden


def test_16_frontend_has_no_regression_business_logic() -> None:
    # Invariant: the frontend may RENDER a backend regression verdict (status.ts
    # maps the received enum to a label) but must never DERIVE it. Deriving needs
    # computation operators — delta/epsilon, a score comparison, hand-tallying
    # verdict categories — none of which may appear outside the sanctioned api/
    # mapping layer. Regression assertion: the verdict enums themselves stay in the
    # single presenter (status.ts), never leaking into business components.
    violations = frontend_compute_violations(REGRESSION_PATTERNS)
    assert violations == [], [
        f"{path} derives a regression verdict ({pat!r} -> {matched!r})"
        for path, pat, matched in violations
    ]
    verdict_files = frontend_enum_files(
        ["IMPROVEMENT", "REGRESSION", "STABLE", "MIXED", "NOT_COMPARABLE", "UNDETERMINED"]
    )
    assert verdict_files == {"frontend/src/status/status.ts"}, (
        f"regression verdict enums leaked outside the presenter: {verdict_files}"
    )


# ---- 17: BLOCKED comparison ----

def test_17_blocked_comparison_yields_no_regression() -> None:
    comparison = _comparison(
        [], status="BLOCKED", reasons=["dataset version differs (v3 vs v4)"],
        overall={"delta": None, "comparable": False, "reason": "comparability is not DIRECT"},
    )
    resp = _svc().analyze(comparison)
    assert resp["comparability_status"] == "BLOCKED"
    assert resp["metrics"] == []
    assert resp["overall"]["verdict"] == "NOT_COMPARABLE"
    assert resp["trade_off"] is False
    assert all(c["verdict"] == "NOT_COMPARABLE" for c in resp["categories"].values())


# ---- integration: compute() reuses ComparisonService ----

def test_compute_chain_reuses_comparison_service() -> None:
    from tests.test_comparison_service import _repo, _seed_run

    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.86,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", overall_score=0.80,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.80}])
    svc = RegressionService(ComparisonService(repo), epsilon=0.01)
    resp = svc.compute(baseline_run_id=base, candidate_run_id=cand)
    assert resp["comparability_status"] == "DIRECT"
    assert resp["metrics"][0]["verdict"] == "REGRESSION"  # -0.06 < -0.01
