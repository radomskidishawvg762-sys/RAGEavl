"""RAGEval Studio Internal Benchmark v0.1 — runner.

Executes benchmark cases against the REAL product code paths:
  integrity:  IntegrityEngine -> FailureClassifier -> DiagnosisEngine
  analysis:   ComparisonService -> RegressionService (on seeded runs)
  gate:       QualityGateService (seeded run + Profile quality_gate config)

Deterministic and offline — no Judge, no RAG Adapter, no network. Each case
carries a Gold Expected Result; this module checks actual vs expected
semantics and aggregates per-criterion accuracy. No output formatting here —
scripts/run_benchmark.py renders the report.

§十: a failing case is a REPORT, never an algorithm change. The runner only
reports mismatches; it never touches T-09/T-10/T-16C code.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.core.errors import NotFoundError
from app.diagnosis import DiagnosisEngine
from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.metrics.base import MetricSpec
from app.metrics.bootstrap import bootstrap_metrics
from app.metrics.registry import default_registry
from app.services.comparison_service import ComparisonService
from app.services.quality_gate_service import QualityGateService
from app.services.regression_service import RegressionService

BENCHMARK_VERSION = "benchmark-v0.1"
_EPSILON = 0.01
_EPSILON_SOURCE = "provisional_default"
_LOWER_IS_BETTER_METRIC = "answer_latency"  # benchmark fixture: proves lower_is_better
_NUM_TOL = 1e-9


class _MemoryRepo:
    """Minimal in-memory mirror of EvaluationRepository's read surface used by
    Compare / Regression / QualityGate (benchmark runs are seeded, not a DB)."""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.rows: dict[str, list[dict]] = {}
        self.configs: dict[str, dict] = {}

    def get_run(self, run_id: str):
        if run_id not in self.runs:
            raise NotFoundError(f"run {run_id} not found")
        return SimpleNamespace(**self.runs[run_id])

    def list_metric_rows_for_run(self, run_id: str) -> list[dict]:
        return self.rows.get(run_id, [])

    def get_config(self, config_id: str):
        if config_id not in self.configs:
            raise NotFoundError(f"config {config_id} not found")
        return SimpleNamespace(**self.configs[config_id])


class _FakeConfig:
    """Duck-typed ConfigService supplying only the Profile quality_gate section."""

    def __init__(self, quality_gate: Any) -> None:
        self._merged = {"quality_gate": quality_gate}

    def load(self, domain: str = "general", profile: str = "default", profile_body: dict | None = None) -> dict:
        return self._merged


def _row(metric: dict) -> dict:
    return {
        "metric_name": metric["name"],
        "category": metric.get("category", "generation"),
        "score": metric.get("score"),
        "threshold": metric.get("threshold"),
        "passed": None,
        "comparison_basis": metric.get("comparison_basis"),
        "error": metric.get("error"),
        "metric_version": BENCHMARK_VERSION,
        "record_id": "r1",
        "question": "",
        "is_failure": False,
    }


def _seed_run(repo: _MemoryRepo, run_id: str, *, meta: dict, metrics: list[dict],
              overall_score: float | None) -> None:
    repo.runs[run_id] = {
        "id": run_id, "project_id": "p1", "dataset_id": "ds1", "config_id": "c1",
        "status": "completed", "total_records": 1, "evaluated_records": 1, "error_records": 0,
        "evaluation_coverage": 1.0, "overall_score": overall_score,
        "reproducibility_meta": meta, "started_at": None, "finished_at": None,
        "error_summary": None, "created_at": datetime.now(UTC),
    }
    repo.rows[run_id] = [_row(m) for m in metrics]
    repo.configs["c1"] = {"domain_config": {"domain": "general"},
                          "profile_config": {"profile": "default"}}


def _meta(enabled: list[str], **overrides) -> dict:
    meta = {
        "dataset_version": "v3", "config_version": "cfg-abc",
        "metric_version": "ragas-0.4.3",
        "enabled_metrics": list(enabled),
        "metric_weights": {n: 1.0 for n in enabled},
        "judge_provider": "openai", "judge_model": "gpt-4o",
    }
    meta.update(overrides)
    return meta


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= _NUM_TOL


# ---------------- case executors ----------------


def _run_integrity(case: dict) -> dict:
    inp, metric = case["input"], case["metric"]
    record = EvaluationRecord(
        id=case["case_id"],
        question=inp.get("question", ""),
        contexts=inp.get("contexts", []),
        answer=inp.get("answer", ""),
        reference_answer=inp.get("reference_answer"),
        reference_contexts=inp.get("reference_contexts"),
        metadata={},
    )
    engine = IntegrityEngine(alias_table=inp.get("alias_table") or {})
    results = asyncio.run(engine.evaluate(record, [metric], EvalParams(extra={"tolerance": {}})))
    if not results:
        raise ValueError(f"{case['case_id']}: metric {metric} produced no result")
    result = results[0]
    outcome = DiagnosisEngine().diagnose(record, result)
    d = outcome.diagnosis
    basis = result.comparison_basis
    return {
        "actual": {
            "comparison_type": basis.comparison_type if basis else None,
            "score": result.score,
            "diagnosis_status": outcome.status,
            "failure_type": d.failure_type if d else None,
            "severity": d.severity if d else None,
            "root_cause": d.root_cause if d else None,
            "evidence_contract": d.evidence_contract if d else None,
            "confidence": d.confidence if d else None,
            "evidence_types": sorted({e.type for e in d.evidence}) if d else [],
        }
    }


def _run_analysis(case: dict) -> dict:
    inp = case["input"]
    repo = _MemoryRepo()
    base, cand = inp["baseline"], inp["candidate"]
    enabled = [m["name"] for m in base["metrics"]]
    meta = _meta(enabled, **(inp.get("meta") or {}))
    _seed_run(repo, "base", meta=meta, metrics=base["metrics"], overall_score=base.get("overall"))
    _seed_run(repo, "cand", meta=meta, metrics=cand["metrics"], overall_score=cand.get("overall"))

    comparison_service = ComparisonService(repo)
    comparison = comparison_service.compare(baseline_run_id="base", candidate_run_id="cand")
    regression = RegressionService(comparison_service, epsilon=_EPSILON,
                                   epsilon_source=_EPSILON_SOURCE).analyze(comparison)
    return {
        "actual": {
            "comparability": comparison["comparability"]["status"],
            "metrics": {m["name"]: m for m in comparison["metrics"]},
            "regression": {m["metric"]: m["verdict"] for m in regression["metrics"]},
            "trade_off": regression["trade_off"],
            "overall": comparison["overall"],
        }
    }


def _run_gate(case: dict) -> dict:
    inp = case["input"]
    repo = _MemoryRepo()
    meta = _meta(inp["enabled_metrics"])
    _seed_run(repo, "run1", meta=meta, metrics=inp["metrics"], overall_score=inp.get("overall"))
    resp = QualityGateService(repo, _FakeConfig(inp.get("quality_gate"))).evaluate("run1")
    return {"actual": {"status": resp["status"], "reasons": sorted(resp["reasons"])}}


# ---------------- checkers (actual vs Gold Expected) ----------------


def _check_integrity(actual: dict, expected: dict) -> dict:
    mismatches: list[str] = []
    metric_ok = True
    if expected.get("comparison_type") is not None and actual["comparison_type"] != expected["comparison_type"]:
        metric_ok = False
        mismatches.append(f"comparison_type: expected {expected['comparison_type']}, "
                          f"actual {actual['comparison_type']}")
    if "score" in expected and not _close(actual["score"], expected["score"]):
        metric_ok = False
        mismatches.append(f"score: expected {expected['score']}, actual {actual['score']}")

    diagnosis_ok = True
    for key in ("diagnosis_status", "failure_type", "severity", "confidence"):
        if key in expected and actual[key] != expected[key]:
            diagnosis_ok = False
            mismatches.append(f"{key}: expected {expected[key]}, actual {actual[key]}")
    if expected.get("diagnosis_status") == "undetermined" and actual["root_cause"] is not None:
        diagnosis_ok = False
        mismatches.append("undetermined must NOT fabricate a root_cause")

    evidence_ok: bool | None = None
    if expected.get("evidence_contract") is not None or expected.get("evidence_types") is not None:
        evidence_ok = True
        if "evidence_contract" in expected and actual["evidence_contract"] != expected["evidence_contract"]:
            evidence_ok = False
            mismatches.append(f"evidence_contract: expected {expected['evidence_contract']}, "
                              f"actual {actual['evidence_contract']}")
        if "evidence_types" in expected and set(actual["evidence_types"]) != set(expected["evidence_types"]):
            evidence_ok = False
            mismatches.append(f"evidence_types: expected {sorted(expected['evidence_types'])}, "
                              f"actual {actual['evidence_types']}")

    return {"passed": not mismatches, "metric_ok": metric_ok,
            "diagnosis_ok": diagnosis_ok, "evidence_ok": evidence_ok,
            "mismatches": mismatches}


def _check_analysis(actual: dict, expected: dict) -> dict:
    mismatches: list[str] = []
    compare_ok = True
    if "comparability" in expected and actual["comparability"] != expected["comparability"]:
        compare_ok = False
        mismatches.append(f"comparability: expected {expected['comparability']}, "
                          f"actual {actual['comparability']}")
    for name, exp in (expected.get("metrics") or {}).items():
        act = actual["metrics"].get(name)
        if act is None:
            compare_ok = False
            mismatches.append(f"metric {name}: missing from comparison")
            continue
        for key, val in exp.items():
            if key in ("delta", "relative_delta"):
                if not _close(act[key], val):
                    compare_ok = False
                    mismatches.append(f"{name}.{key}: expected {val}, actual {act[key]}")
            elif act.get(key) != val:
                compare_ok = False
                mismatches.append(f"{name}.{key}: expected {val}, actual {act.get(key)}")

    regression_ok = True
    for name, verdict in (expected.get("regression") or {}).items():
        if actual["regression"].get(name) != verdict:
            regression_ok = False
            mismatches.append(f"regression[{name}]: expected {verdict}, "
                              f"actual {actual['regression'].get(name)}")
    if "trade_off" in expected and actual["trade_off"] != expected["trade_off"]:
        regression_ok = False
        mismatches.append(f"trade_off: expected {expected['trade_off']}, actual {actual['trade_off']}")
    if "overall" in expected:
        for key, val in expected["overall"].items():
            act_val = actual["overall"].get(key)
            if key == "delta":
                if not _close(act_val, val):
                    compare_ok = False
                    mismatches.append(f"overall.delta: expected {val}, actual {act_val}")
            elif act_val != val:
                compare_ok = False
                mismatches.append(f"overall.{key}: expected {val}, actual {act_val}")

    return {"passed": not mismatches, "compare_ok": compare_ok,
            "regression_ok": regression_ok, "mismatches": mismatches}


def _check_gate(actual: dict, expected: dict) -> dict:
    mismatches: list[str] = []
    gate_ok = True
    if actual["status"] != expected.get("status"):
        gate_ok = False
        mismatches.append(f"status: expected {expected.get('status')}, actual {actual['status']}")
    if "reasons" in expected and actual["reasons"] != sorted(expected["reasons"]):
        gate_ok = False
        mismatches.append(f"reasons: expected {sorted(expected['reasons'])}, actual {actual['reasons']}")
    return {"passed": gate_ok, "gate_ok": gate_ok, "mismatches": mismatches}


_EXECUTORS = {"integrity": _run_integrity, "analysis": _run_analysis, "gate": _run_gate}
_CHECKERS = {"integrity": _check_integrity, "analysis": _check_analysis, "gate": _check_gate}


def _register_benchmark_metric() -> None:
    """Register the lower_is_better fixture metric. Only the spec is consumed by
    Regression (direction) — the evaluator is never called in the benchmark."""
    if not default_registry.has(_LOWER_IS_BETTER_METRIC):
        async def _never_called(record, params):  # noqa: ANN001
            raise AssertionError("benchmark fixture evaluator must never run")

        default_registry.register(
            MetricSpec(
                name=_LOWER_IS_BETTER_METRIC, category="generation",
                engine="benchmark", description="internal-benchmark latency fixture",
                version=BENCHMARK_VERSION, direction="lower_is_better",
            ),
            _never_called,
        )


def run_benchmark(cases: list[dict]) -> dict:
    bootstrap_metrics()
    _register_benchmark_metric()
    results: list[dict] = []
    for case in cases:
        kind = case["kind"]
        executed = _EXECUTORS[kind](case)
        check = _CHECKERS[kind](executed["actual"], case.get("expected") or {})
        results.append({
            "case_id": case["case_id"],
            "category": case.get("category", "?"),
            "kind": kind,
            "metric": case.get("metric"),
            "passed": check["passed"],
            "criteria": check,
            "mismatches": check["mismatches"],
        })
    return _aggregate(results)


# ---------------- aggregation ----------------


def _acc(passed: int, total: int) -> float | None:
    return round(passed / total, 4) if total else None


def _aggregate(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    by_category: dict[str, dict] = {}
    for r in results:
        b = by_category.setdefault(r["category"], {"passed": 0, "total": 0})
        b["total"] += 1
        b["passed"] += 1 if r["passed"] else 0

    by_metric: dict[str, dict] = {}
    for r in results:
        if r["kind"] != "integrity" or not r["metric"]:
            continue
        b = by_metric.setdefault(r["metric"], {"passed": 0, "total": 0})
        b["total"] += 1
        b["passed"] += 1 if r["passed"] else 0

    diag = [r for r in results if r["kind"] == "integrity"]
    diag_ok = sum(1 for r in diag if r["criteria"]["diagnosis_ok"])
    ev_cases = [r for r in diag if r["criteria"]["evidence_ok"] is not None]
    ev_ok = sum(1 for r in ev_cases if r["criteria"]["evidence_ok"])
    ana = [r for r in results if r["kind"] == "analysis"]
    cmp_ok = sum(1 for r in ana if r["criteria"]["compare_ok"])
    reg_ok = sum(1 for r in ana if r["criteria"]["regression_ok"])
    gate = [r for r in results if r["kind"] == "gate"]
    gate_ok = sum(1 for r in gate if r["criteria"]["gate_ok"])

    return {
        "benchmark_version": BENCHMARK_VERSION,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": _acc(passed, total),
        "by_category": {cat: {**b, "accuracy": _acc(b["passed"], b["total"])}
                        for cat, b in sorted(by_category.items())},
        "by_metric": {m: {**b, "accuracy": _acc(b["passed"], b["total"])}
                      for m, b in sorted(by_metric.items())},
        "diagnosis_accuracy": _acc(diag_ok, len(diag)),
        "evidence_contract_accuracy": _acc(ev_ok, len(ev_cases)),
        "compare_accuracy": _acc(cmp_ok, len(ana)),
        "regression_accuracy": _acc(reg_ok, len(ana)),
        "quality_gate_accuracy": _acc(gate_ok, len(gate)),
        "failed_cases": [
            {"case_id": r["case_id"], "category": r["category"], "mismatches": r["mismatches"]}
            for r in results if not r["passed"]
        ],
    }
