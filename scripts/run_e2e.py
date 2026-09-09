"""MVP End-to-End Acceptance (T-19) — real Supabase Cloud + real API + real Judge.

Validates the complete MVP chain on the real database:
  POST /api/evaluations -> poll -> EvaluationRunner -> GoldenRunMetadataAdapter
  -> IntegrityEngine/RagasEngine -> MetricResult -> FailureClassifier ->
  Evidence Contract -> Diagnosis -> Persistence -> Report -> Compare ->
  Regression -> Quality Gate.

Judge = opencode.ai/zen/go (OpenAI-compatible, model deepseek-v4-flash). RAGAS
faithfulness/context_recall are scored by the real LLM (non-deterministic,
range/consistency-checked); answer_relevancy/context_precision need embeddings
(unconfigured) and persist BIZ_JUDGE_NOT_CONFIGURED. Integrity/compare/gate
assertions are exact/deterministic.

Phases:
  A. real uvicorn server + httpx API flow
  B. in-process Supabase audit, cross-checked with API responses
  C. no-re-execution spy (engine/judge/rag constructors raised -> read-only
     services must still succeed against the real DB)

Harness-seeded runs exist because the dataset lock (ADR-06) makes two API runs
on one dataset version impossible by design; the analysis layer consumes ONLY
persisted rows. H1/H2 (fixed scores) reproduce Benchmark CMP-001 exactly.

Usage:
    python scripts/run_e2e.py [--port 8000] [--skip-judge-prefetch]
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env.local")  # expose Judge/DB env to the server subprocess

from app.db.session import open_session  # noqa: E402
from app.models import (  # noqa: E402
    DatasetRecord,
    Diagnosis,
    EvaluationConfig,
    EvaluationResult,
    EvaluationRun,
    MetricResult,
    Project,
    Recommendation,
)
from app.repositories.evaluation import EvaluationRepository  # noqa: E402
from app.services.comparison_service import ComparisonService  # noqa: E402
from app.services.config_service import default_config_service  # noqa: E402
from app.services.quality_gate_service import QualityGateService  # noqa: E402
from app.services.regression_service import RegressionService  # noqa: E402
from app.services.report_service import ReportService  # noqa: E402

TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled"}
INTEGRITY = ("numerical_consistency", "temporal_consistency", "entity_consistency")
EPSILON = 0.01
_BASE = ""

# Golden E2E dataset (§三): normal, numerical mismatch, temporal mismatch,
# entity mismatch, ambiguous, normal(multi-metric). metadata = GoldenRun adapter.
E2E_RECORDS = [
    {"question": "公司2024年营收是多少？", "reference_answer": "营收为1亿元",
     "reference_contexts": ["营收为1亿元"],
     "metadata": {"answer": "营收为10000万元", "contexts": ["营收为1亿元"]}},
    {"question": "公司2024年营收具体数值是多少？", "reference_answer": "营收为1亿元",
     "reference_contexts": ["营收为1亿元"],
     "metadata": {"answer": "营收为12000万元", "contexts": ["营收为1亿元"]}},
    {"question": "公司2024年的报告期是什么？", "reference_answer": "报告期为2024年",
     "reference_contexts": ["报告期为2024年"],
     "metadata": {"answer": "报告期为2023年", "contexts": ["报告期为2024年"]}},
    {"question": "公司本次报告的主体是谁？", "reference_answer": "主体为中国平安",
     "reference_contexts": ["主体为中国平安"],
     "metadata": {"answer": "主体为中国人寿", "contexts": ["主体为中国平安"]}},
    {"question": "公司披露的金额是多少？", "reference_answer": "金额为100美元",
     "reference_contexts": ["金额为100美元"],
     "metadata": {"answer": "金额为100元", "contexts": ["金额为100美元"]}},
    {"question": "公司2024年全年的营收金额是多少？", "reference_answer": "2024年营收为1亿元",
     "reference_contexts": ["2024年营收为1亿元"],
     "metadata": {"answer": "2024年营收为10000万元", "contexts": ["2024年营收为1亿元"]}},
]

# §七: quality-failure / undetermined / error never conflated (record index -> metric -> type)
EXPECTED_RECORD_TYPES = {
    1: {"numerical_consistency": "value_mismatch"},
    2: {"temporal_consistency": "temporal_mismatch"},
    3: {"entity_consistency": "entity_mismatch"},
    4: {"numerical_consistency": "ambiguous"},
}


class Checks:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, label: str, ok: bool, detail: str = "") -> None:
        self.items.append({"label": label, "ok": bool(ok), "detail": detail})

    def summary(self) -> bool:
        passed = sum(1 for i in self.items if i["ok"])
        print(f"\n== E2E checks: {passed}/{len(self.items)} passed ==")
        for i in self.items:
            mark = "PASS" if i["ok"] else "FAIL"
            print(f"  [{mark}] {i['label']}" + (f"  <- {i['detail']}" if not i["ok"] else ""))
        return passed == len(self.items)


def _uid() -> str:
    return str(uuid.uuid4())


def _start_server(port: int) -> subprocess.Popen:
    # handle must stay open for the subprocess's lifetime
    log = open(ROOT / ".e2e_server.log", "w", encoding="utf-8")  # noqa: SIM115
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
    )


def _wait_health(timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{_BASE}/api/health", timeout=10).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("server did not become healthy")


def _judge_prefetch() -> dict:
    """One real chat-completion call proving the Judge gateway accepts the key."""
    from app.engines.judge import build_judge, judge_config_from_settings

    judge = build_judge(judge_config_from_settings())

    async def _go():
        return await judge.ainvoke("Reply with the single word: ok")

    try:
        return {"ok": True, "reply": asyncio.run(_go())[:40]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def _setup() -> dict:
    ts = datetime.now(UTC).strftime("%H%M%S")
    with open_session() as s:
        p = Project(name=f"e2e-acceptance-{ts}", domain="general", status="active")
        s.add(p)
        s.commit()
        project_id = p.id
    cfg_ids: dict[str, str] = {}
    with open_session() as s:
        for label, profile in (("e2e", "e2e"), ("default", "default"),
                               ("disabled", "e2e_gate_disabled")):
            cfg = EvaluationConfig(
                project_id=project_id, name=f"cfg-{label}",
                domain_config={"domain": "general"}, profile_config={"profile": profile},
                pipeline_config=None, judge_model=None, judge_params=None,
                config_version=default_config_service.config_version("general", profile))
            s.add(cfg)
            s.commit()
            cfg_ids[label] = cfg.id
    payload = {"name": "e2e-golden", "domain": "general", "duplicate_policy": "strict",
               "records": E2E_RECORDS}
    resp = httpx.post(f"{_BASE}/api/projects/{project_id}/datasets:import",
                      content=json.dumps(payload, ensure_ascii=False), timeout=60)
    if resp.status_code != 201:
        raise RuntimeError(f"dataset import failed: {resp.status_code} {resp.text}")
    ds = resp.json()
    return {"project_id": project_id, "configs": cfg_ids, "dataset_id": ds["id"],
            "dataset_version": ds["version"]}


def _wait_terminal(run_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = httpx.get(f"{_BASE}/api/evaluations/{run_id}/progress", timeout=60).json()
        if last["status"] in TERMINAL:
            return last
        time.sleep(1)
    raise RuntimeError(f"run {run_id} did not reach terminal: {last}")


def _first_record_id(dataset_id: str) -> str:
    with open_session() as s:
        return s.execute(
            select(DatasetRecord.id).where(DatasetRecord.dataset_id == dataset_id).limit(1)
        ).scalar_one()


def _seed_harness_run(*, run_id: str, project_id: str, dataset_id: str, config_id: str,
                      meta: dict, overall_score: float | None, metric_rows: list[dict],
                      record_id: str) -> None:
    with open_session() as s:
        s.add(EvaluationRun(id=run_id, project_id=project_id, dataset_id=dataset_id,
                            config_id=config_id, status="completed", overall_score=overall_score,
                            total_records=1, evaluated_records=1, error_records=0,
                            evaluation_coverage=1.0, reproducibility_meta=meta))
        s.flush()
        result_id = _uid()  # evaluation_results.id is VARCHAR(36) — a fresh uuid
        s.add(EvaluationResult(id=result_id, run_id=run_id, record_id=record_id,
                               row_index=0, question="q", contexts=[], answer="a",
                               reference_answer="ref", reference_contexts=None, is_failure=False))
        s.flush()
        for m in metric_rows:
            s.add(MetricResult(result_id=result_id, metric_name=m["metric_name"],
                               category=m.get("category", "generation"), score=m.get("score"),
                               threshold=m.get("threshold"), passed=None,
                               comparison_basis=m.get("comparison_basis"),
                               metric_version="e2e-harness-v0.1", error=m.get("error")))
        s.commit()


def _m(name: str, cat: str, score: float | None, *, threshold=None, error=None) -> dict:
    return {"metric_name": name, "category": cat, "score": score,
            "threshold": threshold, "error": error}


def _seed_harness(ctx: dict, run_a: str, record_id: str) -> dict:
    with open_session() as s:
        run_a_meta = dict(s.execute(
            select(EvaluationRun).where(EvaluationRun.id == run_a)
        ).scalar_one().reproducibility_meta)

    run_b = _uid()
    _seed_harness_run(
        run_id=run_b, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
        config_id=ctx["configs"]["e2e"], meta=dict(run_a_meta), overall_score=0.9,
        record_id=record_id,
        metric_rows=[
            _m("numerical_consistency", "integrity", 0.6, threshold=0.9),
            _m("temporal_consistency", "integrity", 0.8333333333333334, threshold=0.9),
            _m("entity_consistency", "integrity", 1.0, threshold=0.9),
            _m("faithfulness", "generation", 0.84),
            _m("context_recall", "retrieval", 0.88),
            _m("answer_relevancy", "generation", None,
               error={"code": "BIZ_JUDGE_NOT_CONFIGURED"}),
            _m("context_precision", "retrieval", None,
               error={"code": "BIZ_JUDGE_NOT_CONFIGURED"}),
        ])

    cmp_meta = {
        "dataset_version": ctx["dataset_version"], "config_version": "cfg-abc",
        "metric_version": "ragas-0.4.3",
        "enabled_metrics": ["context_recall", "faithfulness"],
        "metric_weights": {"context_recall": 1.0, "faithfulness": 1.0},
        "judge_provider": "openai", "judge_model": "deepseek-v4-flash",
    }
    h1, h2 = _uid(), _uid()
    _seed_harness_run(run_id=h1, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["e2e"], meta=dict(cmp_meta), overall_score=None,
                      record_id=record_id,
                      metric_rows=[_m("context_recall", "retrieval", 0.80),
                                   _m("faithfulness", "generation", 0.90)])
    _seed_harness_run(run_id=h2, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["e2e"], meta=dict(cmp_meta), overall_score=None,
                      record_id=record_id,
                      metric_rows=[_m("context_recall", "retrieval", 0.88),
                                   _m("faithfulness", "generation", 0.84)])

    lat_meta = {"dataset_version": ctx["dataset_version"], "config_version": "cfg",
                "metric_version": "e2e-harness-v0.1",
                "enabled_metrics": ["answer_latency"],
                "metric_weights": {"answer_latency": 1.0}}
    h3, h4 = _uid(), _uid()
    _seed_harness_run(run_id=h3, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["e2e"], meta=dict(lat_meta), overall_score=None,
                      record_id=record_id, metric_rows=[_m("answer_latency", "generation", 5.0)])
    _seed_harness_run(run_id=h4, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["e2e"], meta=dict(lat_meta), overall_score=None,
                      record_id=record_id, metric_rows=[_m("answer_latency", "generation", 6.0)])

    gate_meta = {"dataset_version": ctx["dataset_version"], "config_version": "cfg",
                 "metric_version": "e2e-harness-v0.1",
                 "enabled_metrics": list(INTEGRITY),
                 "metric_weights": {n: 1.0 for n in INTEGRITY}}
    g_pass, g_ne, g_dis, g_nc = _uid(), _uid(), _uid(), _uid()
    _seed_harness_run(run_id=g_pass, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["e2e"], meta=dict(gate_meta), overall_score=None,
                      record_id=record_id,
                      metric_rows=[_m(n, "integrity", 1.0, threshold=0.9) for n in INTEGRITY])
    _seed_harness_run(run_id=g_ne, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["e2e"], meta=dict(gate_meta), overall_score=None,
                      record_id=record_id,
                      metric_rows=[_m("numerical_consistency", "integrity", None, threshold=0.9,
                                      error={"code": "BIZ_JUDGE_NOT_CONFIGURED"}),
                                   _m("entity_consistency", "integrity", 1.0, threshold=0.9),
                                   _m("temporal_consistency", "integrity", 1.0, threshold=0.9)])
    dis_meta = dict(gate_meta)
    dis_meta["config_version"] = default_config_service.config_version("general", "e2e_gate_disabled")
    _seed_harness_run(run_id=g_dis, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["disabled"], meta=dis_meta, overall_score=None,
                      record_id=record_id,
                      metric_rows=[_m("numerical_consistency", "integrity", 0.1)])
    nc_meta = dict(gate_meta)
    nc_meta["config_version"] = default_config_service.config_version("general", "default")
    _seed_harness_run(run_id=g_nc, project_id=ctx["project_id"], dataset_id=ctx["dataset_id"],
                      config_id=ctx["configs"]["default"], meta=nc_meta, overall_score=None,
                      record_id=record_id,
                      metric_rows=[_m("numerical_consistency", "integrity", 1.0)])
    return {"run_b": run_b, "h1": h1, "h2": h2, "h3": h3, "h4": h4,
            "gate": {"pass": g_pass, "ne": g_ne, "disabled": g_dis, "nc": g_nc}}


def _verify_compare_regression(checks: Checks, run_a: str, h: dict) -> None:
    rb = h["run_b"]
    cmp_ab = httpx.get(f"{_BASE}/api/comparisons",
                       params={"baseline_run_id": run_a, "candidate_run_id": rb}, timeout=60).json()
    checks.add("Compare RunA->RunB is DIRECT (same reproducibility meta)",
               cmp_ab["comparability"]["status"] == "DIRECT", str(cmp_ab["comparability"]))
    m = {x["name"]: x for x in cmp_ab["metrics"]}
    checks.add("numerical delta = candidate - baseline (-0.2)",
               abs(m["numerical_consistency"]["delta"] + 0.2) < 1e-9,
               str(m.get("numerical_consistency")))
    checks.add("temporal delta ~ 0 (same 5/6)",
               abs(m["temporal_consistency"]["delta"]) < 1e-9,
               str(m.get("temporal_consistency")))
    checks.add("entity delta = 1.0 - 5/6 (~ +0.1667)",
               abs(m["entity_consistency"]["delta"] - (1.0 - 5 / 6)) < 1e-9,
               str(m.get("entity_consistency")))

    reg_ab = httpx.get(f"{_BASE}/api/comparisons/regression",
                       params={"baseline_run_id": run_a, "candidate_run_id": rb}, timeout=60).json()
    rv = {x["metric"]: x for x in reg_ab["metrics"]}
    checks.add("regression epsilon+source surfaced",
               reg_ab["metrics"][0]["epsilon"] == EPSILON
               and reg_ab["metrics"][0]["epsilon_source"] == "provisional_default",
               str(reg_ab["metrics"][0]))
    checks.add("regression numerical REGRESSION", rv["numerical_consistency"]["verdict"] == "REGRESSION")
    checks.add("regression temporal STABLE", rv["temporal_consistency"]["verdict"] == "STABLE")
    checks.add("regression entity IMPROVEMENT", rv["entity_consistency"]["verdict"] == "IMPROVEMENT")
    for name in ("faithfulness", "context_recall"):
        item = m.get(name)
        checks.add(f"{name} scored by real Judge (comparable)",
                   bool(item and item.get("comparable")), str(item))
        if item and item.get("comparable"):
            checks.add(f"{name} delta == candidate - baseline (real scores)",
                       abs(item["delta"] - (item["candidate_score"] - item["baseline_score"])) < 1e-9,
                       str(item))
            delta = item["delta"]
            exp = "STABLE" if abs(delta) <= EPSILON else ("IMPROVEMENT" if delta > 0 else "REGRESSION")
            checks.add(f"{name} verdict consistent with delta ({exp})",
                       rv[name]["verdict"] == exp, f"{rv[name]['verdict']} vs {exp}")
    for name in ("answer_relevancy", "context_precision"):
        item = m.get(name)
        checks.add(f"{name} NOT comparable (embeddings not configured)",
                   bool(item) and item.get("comparable") is False, str(item))

    cmp_12 = httpx.get(f"{_BASE}/api/comparisons",
                       params={"baseline_run_id": h["h1"], "candidate_run_id": h["h2"]}, timeout=60).json()
    checks.add("H1->H2 DIRECT", cmp_12["comparability"]["status"] == "DIRECT")
    m12 = {x["name"]: x for x in cmp_12["metrics"]}
    checks.add("H1->H2 context_recall delta +0.08, relative_delta 0.10 (= CMP-001)",
               abs(m12["context_recall"]["delta"] - 0.08) < 1e-9
               and abs(m12["context_recall"]["relative_delta"] - 0.10) < 1e-9,
               str(m12.get("context_recall")))
    checks.add("H1->H2 faithfulness delta -0.06", abs(m12["faithfulness"]["delta"] + 0.06) < 1e-9)
    reg12 = httpx.get(f"{_BASE}/api/comparisons/regression",
                      params={"baseline_run_id": h["h1"], "candidate_run_id": h["h2"]}, timeout=60).json()
    r12 = {x["metric"]: x for x in reg12["metrics"]}
    checks.add("H1->H2 recall IMPROVEMENT / faithfulness REGRESSION (= CMP-001)",
               r12["context_recall"]["verdict"] == "IMPROVEMENT"
               and r12["faithfulness"]["verdict"] == "REGRESSION", str(r12))
    checks.add("H1->H2 trade_off true (= CMP-001)", reg12["trade_off"] is True)

    _verify_lower_is_better(checks, h)


def _verify_lower_is_better(checks: Checks, h: dict) -> None:
    from app.metrics.base import MetricSpec
    from app.metrics.registry import default_registry

    if not default_registry.has("answer_latency"):
        async def _never(record, params):  # noqa: ANN001
            raise AssertionError("e2e fixture evaluator must never run")

        default_registry.register(MetricSpec(name="answer_latency", category="generation",
                                             engine="e2e", version="e2e-v0.1",
                                             direction="lower_is_better"), _never)
    with open_session() as s:
        repo = EvaluationRepository(s)
        reg = RegressionService(ComparisonService(repo), epsilon=EPSILON,
                                epsilon_source="provisional_default")
        up = reg.compute(baseline_run_id=h["h3"], candidate_run_id=h["h4"])
        checks.add("lower_is_better delta +1.0 -> REGRESSION",
                   up["metrics"][0]["verdict"] == "REGRESSION", str(up["metrics"]))
        down = reg.compute(baseline_run_id=h["h4"], candidate_run_id=h["h3"])
        checks.add("lower_is_better delta -1.0 -> IMPROVEMENT",
                   down["metrics"][0]["verdict"] == "IMPROVEMENT", str(down["metrics"]))


def _verify_gate(checks: Checks, run_a: str, gate: dict) -> None:
    g_a = httpx.get(f"{_BASE}/api/evaluations/{run_a}/quality-gate", timeout=60).json()
    checks.add("Run A gate FAIL (threshold 0.9, integrity aggregates < 0.9)",
               g_a["status"] == "FAIL" and "QUALITY_THRESHOLD_FAILED" in g_a["reasons"],
               f"{g_a['status']} {g_a['reasons']}")
    for label, rid, exp_status, exp_reason in [
        ("gate PASS", gate["pass"], "PASS", None),
        ("gate FAIL required_metric_not_evaluable", gate["ne"], "FAIL", "REQUIRED_METRIC_NOT_EVALUABLE"),
        ("gate NOT_EVALUABLE disabled", gate["disabled"], "NOT_EVALUABLE", "QUALITY_GATE_DISABLED"),
        ("gate NOT_EVALUABLE not_configured", gate["nc"], "NOT_EVALUABLE", "QUALITY_GATE_NOT_CONFIGURED"),
    ]:
        g = httpx.get(f"{_BASE}/api/evaluations/{rid}/quality-gate", timeout=60).json()
        ok = g["status"] == exp_status and (exp_reason is None or exp_reason in g["reasons"])
        checks.add(label, ok, f"{g['status']} {g['reasons']}")


def _audit(run_a: str) -> dict:
    with open_session() as s:
        run = s.execute(select(EvaluationRun).where(EvaluationRun.id == run_a)).scalar_one()
        results = list(s.execute(select(EvaluationResult).where(EvaluationResult.run_id == run_a)).scalars())
        rows = list(s.execute(
            select(MetricResult).join(EvaluationResult, MetricResult.result_id == EvaluationResult.id)
            .where(EvaluationResult.run_id == run_a)).scalars())
        diags = list(s.execute(select(Diagnosis).where(Diagnosis.run_id == run_a)).scalars())
        recs = list(s.execute(
            select(Recommendation).join(Diagnosis, Recommendation.diagnosis_id == Diagnosis.id)
            .where(Diagnosis.run_id == run_a)).scalars())
        meta = dict(run.reproducibility_meta or {})
    null_scores = 0
    null_by_metric: dict[str, int] = {}
    for r in rows:
        if r.score is None:
            null_scores += 1
            null_by_metric[r.metric_name] = null_by_metric.get(r.metric_name, 0) + 1
    metric_level: dict[str, float] = {}
    for name in {r.metric_name for r in rows}:
        vals = [r.score for r in rows if r.metric_name == name and r.score is not None]
        if vals:
            metric_level[name] = sum(vals) / len(vals)
    return {
        "run": {"status": run.status, "total": run.total_records, "evaluated": run.evaluated_records,
                "errors": run.error_records, "coverage": run.evaluation_coverage,
                "overall": run.overall_score},
        "results": len(results), "metric_rows": len(rows), "diagnoses": len(diags),
        "recommendations": len(recs), "null_scores": null_scores, "null_by_metric": null_by_metric,
        "diagnosed": sum(1 for d in diags if d.status == "diagnosed"),
        "undetermined": sum(1 for d in diags if d.status == "undetermined"),
        "undetermined_failure_type_none": all(
            d.failure_type is None for d in diags if d.status == "undetermined"),
        "metric_level": metric_level,
        "meta_fields": [k for k in ("dataset_version", "config_version", "metric_version",
                                    "prompt_version", "judge_model", "judge_model_version",
                                    "model_version", "timestamp") if k in meta],
    }


def _cross_check(checks: Checks, report: dict, audit: dict) -> None:
    s = report.get("summary", {})
    checks.add("report.summary.overall_score == persisted overall_score",
               s.get("overall_score") is not None
               and abs(s["overall_score"] - audit["run"]["overall"]) < 1e-9,
               f"{s.get('overall_score')} vs {audit['run']['overall']}")
    checks.add("report.summary.coverage == persisted coverage",
               abs(s.get("evaluation_coverage", 0) - audit["run"]["coverage"]) < 1e-9,
               f"{s.get('evaluation_coverage')} vs {audit['run']['coverage']}")
    checks.add("report failures == diagnosed diagnoses",
               len(report.get("failures", [])) == audit["diagnosed"])
    checks.add("report undetermined == undetermined diagnoses",
               len(report.get("undetermined", [])) == audit["undetermined"])
    checks.add("report execution_errors present (embeddings not configured)",
               len(report.get("execution_errors", [])) >= 1)
    checks.add("8 mandatory reproducibility fields persisted",
               set(audit["meta_fields"]) == {"dataset_version", "config_version", "metric_version",
                                             "prompt_version", "judge_model", "judge_model_version",
                                             "model_version", "timestamp"},
               str(audit["meta_fields"]))


def _no_reexec_spy(checks: Checks, run_a: str, run_b: str, report_api: dict) -> None:
    import app.adapters.rag_input as rag_mod
    import app.engines.factory as eng_fac
    import app.engines.judge as judge_mod

    def _boom(*a, **k):
        raise AssertionError("read-only service attempted to construct an Engine/Judge/RAG adapter")

    orig = (eng_fac.build_engines, judge_mod.build_judge, rag_mod.build_rag_adapter)
    with open_session() as s:
        before = s.execute(select(func.count()).select_from(MetricResult)).scalar_one()
    eng_fac.build_engines = _boom
    judge_mod.build_judge = _boom
    rag_mod.build_rag_adapter = _boom
    try:
        with open_session() as s:
            repo = EvaluationRepository(s)
            report = ReportService(repo).build_report(run_a)
            cmp = ComparisonService(repo).compare(baseline_run_id=run_a, candidate_run_id=run_b)
            RegressionService(ComparisonService(repo), epsilon=EPSILON,
                              epsilon_source="provisional_default").analyze(cmp)
            QualityGateService(repo, default_config_service).evaluate(run_a)
        with open_session() as s:
            after = s.execute(select(func.count()).select_from(MetricResult)).scalar_one()
        checks.add("read-only services succeed with Engine/Judge/RAG construction forbidden", True)
        checks.add("read-only services wrote no metric_results",
                   before == after, f"{before} vs {after}")
        checks.add("in-process report matches API report (overall + failure count)",
                   abs(report["summary"]["overall_score"] - report_api["summary"]["overall_score"]) < 1e-9
                   and len(report["failures"]) == len(report_api["failures"]))
    except Exception as e:  # noqa: BLE001
        checks.add("read-only services succeed with Engine/Judge/RAG construction forbidden",
                   False, f"{type(e).__name__}: {e}")
    finally:
        eng_fac.build_engines, judge_mod.build_judge, rag_mod.build_rag_adapter = orig


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="RAGEval Studio MVP E2E acceptance (T-19)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip-judge-prefetch", action="store_true")
    parser.add_argument("--keep-server", action="store_true")
    args = parser.parse_args()

    global _BASE
    _BASE = f"http://127.0.0.1:{args.port}"
    checks = Checks()

    server = _start_server(args.port)
    try:
        _wait_health()
        print(f"[server] healthy at {_BASE}")

        if not args.skip_judge_prefetch:
            jf = _judge_prefetch()
            checks.add("Judge gateway reachable + key accepted",
                       jf.get("ok") and str(jf.get("reply", "")).strip().lower().startswith("ok"),
                       str(jf)[:160])
            if not jf.get("ok"):
                print("[warn] Judge prefetch failed — RAGAS metrics will persist errors; "
                      "integrity/compare/gate stay validated deterministically.")

        ctx = _setup()
        print(f"[setup] project={ctx['project_id']} dataset={ctx['dataset_id']} "
              f"version={ctx['dataset_version']}")

        resp = httpx.post(f"{_BASE}/api/evaluations",
                          json={"project_id": ctx["project_id"], "dataset_id": ctx["dataset_id"],
                                "config_id": ctx["configs"]["e2e"]}, timeout=60)
        checks.add("POST /api/evaluations -> 202 + run_id",
                   resp.status_code == 202 and bool(resp.json().get("run_id")), resp.text[:160])
        run_a = resp.json()["run_id"]

        final = _wait_terminal(run_a)
        checks.add(f"run reached terminal ({final['status']})", final["status"] in TERMINAL, str(final))
        checks.add("status completed/with_errors (embeddings errors isolated)",
                   final["status"] in ("completed", "completed_with_errors"), str(final))

        ds = httpx.get(f"{_BASE}/api/datasets/{ctx['dataset_id']}", timeout=60).json()
        checks.add("datasets.is_locked == true after run", ds.get("is_locked") is True)
        locked = httpx.post(f"{_BASE}/api/evaluations",
                            json={"project_id": ctx["project_id"], "dataset_id": ctx["dataset_id"],
                                  "config_id": ctx["configs"]["e2e"]}, timeout=60)
        checks.add("re-run on locked dataset -> 409 BIZ_DATASET_LOCKED",
                   locked.status_code == 409 and locked.json().get("code") == "BIZ_DATASET_LOCKED",
                   f"{locked.status_code} {locked.text[:120]}")

        report = httpx.get(f"{_BASE}/api/evaluations/{run_a}/report", timeout=60).json()
        record_id = _first_record_id(ctx["dataset_id"])
        h = _seed_harness(ctx, run_a, record_id)

        _verify_compare_regression(checks, run_a, h)
        _verify_gate(checks, run_a, h["gate"])

        audit = _audit(run_a)
        checks.add("evaluation_results count == 6 records", audit["results"] == 6, str(audit["results"]))
        checks.add("metric_results count == 6 records x 7 metrics (42)",
                   audit["metric_rows"] == 42, str(audit["metric_rows"]))
        checks.add("answer_relevancy embeddings-error rows stay null (6, never 0)",
                   audit["null_by_metric"].get("answer_relevancy") == 6, str(audit["null_by_metric"]))
        checks.add("numerical ambiguous record stays null (1)",
                   audit["null_by_metric"].get("numerical_consistency") == 1)
        checks.add("3 integrity mismatches diagnosed (entity/numerical/temporal)",
                   audit["diagnosed"] == 3, str(audit["diagnosed"]))
        checks.add("undetermined diagnoses (errors/ambiguous) never fabricate a root cause",
                   audit["undetermined"] >= 2 and audit["undetermined_failure_type_none"],
                   f"{audit['undetermined']} undetermined")
        checks.add("recommendations linked to diagnosed diagnoses (>=1 per diagnosed)",
                   audit["recommendations"] >= audit["diagnosed"],
                   f"{audit['recommendations']} recs / {audit['diagnosed']} diag")
        _cross_check(checks, report, audit)

        _no_reexec_spy(checks, run_a, h["run_b"], report)

        ok = checks.summary()
        print(f"\nrun_a={run_a}  dataset={ctx['dataset_id']}  project={ctx['project_id']}")
        print(f"compare/regression harness: run_b={h['run_b']} h1={h['h1']} h2={h['h2']} "
              f"h3={h['h3']} h4={h['h4']}")
        print(f"gate harness: {h['gate']}")
        return 0 if ok else 1
    finally:
        if not args.keep_server:
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
