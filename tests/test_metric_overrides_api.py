"""Phase 1B G4 — metric_overrides contract tests (Spec 6.3 run-level overrides).

Exercises the REAL execution path: POST /api/evaluations -> Router ->
apply_metric_overrides -> resolve_profile(effective) -> launcher -> REAL
EvaluationService + LocalAsyncRunner + REAL IntegrityEngine -> fake
repository persistence. This makes the mandated invariant testable:
"override 未进入执行路径时测试必须失败" — if overrides only reached the
snapshot but not EvalParams, the metric_results.threshold/passed assertions
below would fail.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_config_service, get_evaluation_launcher, get_evaluation_service
from app.engines.integrity import IntegrityEngine
from app.main import app
from app.runner.local import LocalAsyncRunner
from app.services.config_service import ConfigService
from app.services.evaluation_service import EvaluationService
from app.services.run_planner import MetricOverride, apply_metric_overrides
from tests.test_evaluation_service import FakeEvaluationRepo, _record_row

THRESHOLDED_PROFILE = """
metrics:
  temporal_consistency: { enabled: true, threshold: null, weight: 1.0 }
  numerical_consistency: { enabled: true, threshold: null, weight: 2.0 }
  entity_consistency: { enabled: true, threshold: 0.9, weight: 1.0 }
severity_mapping:
  numerical_mismatch: CRITICAL
  temporal_mismatch: ERROR
  entity_mismatch: ERROR
"""


def _write_profile(base: Path) -> None:
    (base / "domains").mkdir(parents=True, exist_ok=True)
    (base / "evaluations").mkdir(parents=True, exist_ok=True)
    (base / "system.yaml").write_text("system:\n  judge:\n    model: test-judge\n", encoding="utf-8")
    (base / "domains/general.yaml").write_text("domain: general\n", encoding="utf-8")
    (base / "evaluations/default.yaml").write_text(THRESHOLDED_PROFILE, encoding="utf-8")


class _RealChainLauncher:
    """Runs the REAL EvaluationService + LocalAsyncRunner + IntegrityEngine
    against the shared fake repo (same pattern as test_evaluation_api)."""

    def __init__(self, repo: FakeEvaluationRepo) -> None:
        self.repo = repo
        self.plans: list[dict] = []

    def __call__(self, run_id, *, enabled_metrics, params):
        self.plans.append({"metrics": list(enabled_metrics), "params": params})

        async def _run():
            runner = LocalAsyncRunner(concurrency=2)
            svc = EvaluationService(self.repo)
            await svc.execute_run(
                run_id, engines=[IntegrityEngine()], enabled_metrics=enabled_metrics,
                params=params, runner=runner,
            )

        return asyncio.get_running_loop().create_task(_run())


@pytest.fixture()
def ctx():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write_profile(base)
        config_service = ConfigService(base)
        repo = FakeEvaluationRepo(
            {"ds1": {"is_locked": False, "record_count": 3, "version": "v1"}},
            [
                _record_row("r1", "q1", "2024年度", "2024年"),
                _record_row("r2", "q2", "2亿元", "1亿元", ["ref 1亿元"]),
                _record_row("r3", "q3", "2023年", "2024年"),
            ],
            configs={
                "c1": SimpleNamespace(domain_config={"domain": "general"},
                                      profile_config={"profile": "default"}),
            },
        )
        launcher = _RealChainLauncher(repo)

        app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(repo)
        app.dependency_overrides[get_config_service] = lambda: config_service
        app.dependency_overrides[get_evaluation_launcher] = lambda: launcher
        client = TestClient(app)
        yield SimpleNamespace(client=client, repo=repo, launcher=launcher)
        app.dependency_overrides.clear()


def _post(ctx, overrides=None):
    body = {"project_id": "p1", "dataset_id": "ds1", "config_id": "c1"}
    if overrides is not None:
        body["metric_overrides"] = overrides
    return ctx.client.post("/api/evaluations", json=body)


def _wait_terminal(repo, run_id, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = repo.runs[run_id]["status"]
        if status in ("completed", "completed_with_errors", "failed", "cancelled"):
            return status
        time.sleep(0.005)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


def _metric_rows(repo, run_id, name):
    result_ids = {r["id"] for r in repo.eval_results if r["run_id"] == run_id}
    return [m for m in repo.metric_results
            if m["metric_name"] == name and m["result_id"] in result_ids]


# ---------------- 1: no override -> Profile verbatim ----------------


def test_1_no_override_uses_profile(ctx) -> None:
    resp = _post(ctx)
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert _wait_terminal(ctx.repo, run_id) == "completed"
    meta = resp.json()["reproducibility_meta"]
    # snapshot == profile (numerical weight 2.0 from YAML, threshold null)
    assert meta["metric_overrides"] == {}
    assert meta["metric_weights"]["numerical_consistency"] == 2.0
    assert meta["effective_metrics"]["numerical_consistency"] == {"threshold": None, "weight": 2.0}
    assert meta["effective_metrics"]["entity_consistency"] == {"threshold": 0.9, "weight": 1.0}
    # persisted results carry the profile threshold
    for m in _metric_rows(ctx.repo, run_id, "entity_consistency"):
        assert m["threshold"] == 0.9


# ---------------- 2/6/8: threshold override reaches execution ----------------


def test_2_threshold_override_flows_to_execution_and_snapshot(ctx) -> None:
    resp = _post(ctx, {"numerical_consistency": {"threshold": 0.5}})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert _wait_terminal(ctx.repo, run_id) == "completed"

    rows = _metric_rows(ctx.repo, run_id, "numerical_consistency")
    assert len(rows) == 3  # 3 records x 1 metric
    # requirement 8: metric_results.threshold == effective config
    assert all(m["threshold"] == 0.5 for m in rows)
    # requirement 6 (execution-path proof): profile threshold was null ->
    # passed was None; with the override the engine REALLY judged — persisted
    # `passed` follows the comparison outcome against the OVERRIDDEN threshold
    assert all(m["passed"] is not None for m in rows)
    assert sum(1 for m in rows if m["passed"] is False) == 1  # value_mismatch record
    assert sum(1 for m in rows if m["passed"] is True) == 2   # match records
    for m in rows:
        ctype = (m["comparison_basis"] or {}).get("comparison_type")
        assert m["passed"] is (ctype == "match")

    meta = resp.json()["reproducibility_meta"]
    assert meta["metric_overrides"] == {"numerical_consistency": {"threshold": 0.5}}
    assert meta["effective_metrics"]["numerical_consistency"]["threshold"] == 0.5


def test_3_threshold_null_override_clears_judgment(ctx) -> None:
    # profile entity threshold 0.9 -> explicit null clears PASS/FAIL for this run
    resp = _post(ctx, {"entity_consistency": {"threshold": None}})
    run_id = resp.json()["run_id"]
    _wait_terminal(ctx.repo, run_id)
    rows = _metric_rows(ctx.repo, run_id, "entity_consistency")
    assert all(m["threshold"] is None for m in rows)
    assert all(m["passed"] is None for m in rows)
    meta = resp.json()["reproducibility_meta"]
    assert meta["metric_overrides"] == {"entity_consistency": {"threshold": None}}
    assert meta["effective_metrics"]["entity_consistency"]["threshold"] is None


# ---------------- 3: weight override ----------------


def test_4_weight_override_enters_snapshot_weights(ctx) -> None:
    resp = _post(ctx, {"numerical_consistency": {"weight": 0.25}})
    assert resp.status_code == 202
    meta = resp.json()["reproducibility_meta"]
    assert meta["metric_weights"]["numerical_consistency"] == 0.25
    assert meta["effective_metrics"]["numerical_consistency"]["weight"] == 0.25
    assert meta["metric_overrides"] == {"numerical_consistency": {"weight": 0.25}}
    run_id = resp.json()["run_id"]
    assert _wait_terminal(ctx.repo, run_id) == "completed"
    # overall_score at completion used the OVERRIDDEN weights (snapshot source)
    assert ctx.repo.runs[run_id]["overall_score"] is not None


# ---------------- 4: disabled override ----------------


def test_5_disabled_override_excludes_metric_from_execution(ctx) -> None:
    resp = _post(ctx, {"entity_consistency": {"enabled": False}})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert _wait_terminal(ctx.repo, run_id) == "completed"
    assert "entity_consistency" not in resp.json()["reproducibility_meta"]["enabled_metrics"]
    assert _metric_rows(ctx.repo, run_id, "entity_consistency") == []
    assert set(resp.json()["reproducibility_meta"]["metric_weights"]) == {
        "temporal_consistency", "numerical_consistency",
    }


# ---------------- 5: invalid overrides ----------------


def test_6_unknown_metric_409(ctx) -> None:
    resp = _post(ctx, {"bogus_metric": {"threshold": 0.5}})
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_CONFIG_INVALID"
    assert "bogus_metric" in resp.json()["detail"]
    assert ctx.repo.runs == {}  # nothing persisted


def test_7_explicit_null_weight_409(ctx) -> None:
    resp = _post(ctx, {"numerical_consistency": {"weight": None}})
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_CONFIG_INVALID"


def test_8_negative_weight_409(ctx) -> None:
    resp = _post(ctx, {"numerical_consistency": {"weight": -1.0}})
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_CONFIG_INVALID"


def test_9_explicit_null_enabled_409(ctx) -> None:
    resp = _post(ctx, {"numerical_consistency": {"enabled": None}})
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_CONFIG_INVALID"


# ---------------- 7: snapshot contains effective config ----------------


def test_10_snapshot_carries_effective_config_and_overrides(ctx) -> None:
    resp = _post(ctx, {
        "numerical_consistency": {"threshold": 0.5, "weight": 0.5},
        "entity_consistency": {"enabled": False},
    })
    meta = resp.json()["reproducibility_meta"]
    # 8 mandatory fields still present
    for field in ("dataset_version", "config_version", "metric_version", "prompt_version",
                  "judge_model", "judge_model_version", "model_version", "timestamp"):
        assert field in meta
    assert meta["metric_overrides"] == {
        "numerical_consistency": {"threshold": 0.5, "weight": 0.5},
        "entity_consistency": {"enabled": False},
    }
    assert meta["effective_metrics"] == {
        "temporal_consistency": {"threshold": None, "weight": 1.0},
        "numerical_consistency": {"threshold": 0.5, "weight": 0.5},
    }
    # config_version keeps sha256(merged YAML) semantics — overrides never
    # recompute it (ADR-05 / G6)
    assert meta["config_version"]


# ---------------- pure merge semantics ----------------


def test_11_apply_metric_overrides_does_not_mutate_input() -> None:
    merged = {"metrics": {"m1": {"enabled": True, "threshold": 0.9, "weight": 1.0}}}
    effective, applied = apply_metric_overrides(
        merged, {"m1": MetricOverride(threshold=0.5)}
    )
    assert merged["metrics"]["m1"]["threshold"] == 0.9  # original untouched
    assert effective["metrics"]["m1"]["threshold"] == 0.5
    assert applied == {"m1": {"threshold": 0.5}}


def test_12_empty_overrides_return_original() -> None:
    merged = {"metrics": {"m1": {"enabled": True}}}
    effective, applied = apply_metric_overrides(merged, {})
    assert effective is merged
    assert applied == {}
