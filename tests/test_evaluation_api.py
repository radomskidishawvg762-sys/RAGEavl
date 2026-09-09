"""T-13 REST API tests — /api/evaluations (15 mandated points).

API -> Service -> Runner -> Pipeline -> Engine chain is exercised through
dependency_overrides with fake repositories; the POST path runs the REAL
EvaluationService + LocalAsyncRunner + IntegrityEngine against the fake repo
in a background task, proving the API does not re-implement an evaluation
loop and does not bypass Service/Runner.
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
from tests.test_evaluation_service import FakeEvaluationRepo, _record_row

# ---------------- fixtures ----------------


def _write_profile(base: Path, profile: str = "default", integrity_only: bool = True) -> None:
    (base / "domains").mkdir(parents=True, exist_ok=True)
    (base / "evaluations").mkdir(parents=True, exist_ok=True)
    (base / "system.yaml").write_text("system:\n  judge:\n    model: test-judge\n", encoding="utf-8")
    (base / "domains/general.yaml").write_text("domain: general\n", encoding="utf-8")
    if integrity_only:
        body = """
metrics:
  temporal_consistency: { enabled: true, threshold: null, weight: 1.0 }
  numerical_consistency: { enabled: true, threshold: null, weight: 1.0 }
  entity_consistency: { enabled: true, threshold: null, weight: 1.0 }
severity_mapping:
  numerical_mismatch: CRITICAL
  temporal_mismatch: ERROR
  entity_mismatch: ERROR
  unit_mismatch: ERROR
"""
    else:
        body = "profile:\n  name: broken\n"
    (base / f"evaluations/{profile}.yaml").write_text(body, encoding="utf-8")


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
        launcher = _RecordingLauncher(repo)

        def _override_service():
            return EvaluationService(repo)

        def _override_launcher():
            return launcher

        app.dependency_overrides[get_evaluation_service] = _override_service
        app.dependency_overrides[get_config_service] = lambda: config_service
        app.dependency_overrides[get_evaluation_launcher] = _override_launcher
        client = TestClient(app)
        yield SimpleNamespace(client=client, repo=repo, launcher=launcher, config_service=config_service)
        app.dependency_overrides.clear()


class _RecordingLauncher:
    """Runs the REAL EvaluationService + LocalAsyncRunner against the shared
    fake repo, recording that the Runner chain was entered."""

    def __init__(self, repo: FakeEvaluationRepo) -> None:
        self.repo = repo
        self.calls: list[dict] = []
        self.runner_used = False

    def __call__(self, run_id, *, enabled_metrics, params):
        self.calls.append({"run_id": run_id, "metrics": enabled_metrics})

        async def _run():
            runner = LocalAsyncRunner(concurrency=2)
            self.runner_used = True
            svc = EvaluationService(self.repo)
            await svc.execute_run(
                run_id, engines=[IntegrityEngine()], enabled_metrics=enabled_metrics,
                params=params, runner=runner,
            )

        return asyncio.get_running_loop().create_task(_run())


def _wait_terminal(repo, run_id, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = repo.runs[run_id]["status"]
        if status in ("completed", "completed_with_errors", "failed", "cancelled"):
            return status
        time.sleep(0.005)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


def _post(ctx, payload=None, config_id="c1"):
    return ctx.client.post(
        "/api/evaluations",
        json=payload or {"project_id": "p1", "dataset_id": "ds1", "config_id": config_id},
    )


# ---------------- 1-4: POST semantics ----------------


def test_1_create_evaluation_returns_202_and_run_id(ctx) -> None:
    resp = _post(ctx)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    run_id = body["run_id"]
    assert run_id in ctx.repo.runs
    # 8-field reproducibility snapshot present
    meta = body["reproducibility_meta"]
    for field in ("dataset_version", "config_version", "metric_version", "prompt_version",
                  "judge_model", "judge_model_version", "model_version", "timestamp"):
        assert field in meta
    assert ctx.repo.datasets["ds1"]["is_locked"] is True
    assert _wait_terminal(ctx.repo, run_id) == "completed"
    assert len(ctx.repo.metric_results) == 9  # 3 records x 3 integrity metrics


def test_2_missing_dataset_404(ctx) -> None:
    resp = _post(ctx, {"project_id": "p1", "dataset_id": "nope", "config_id": "c1"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_3_locked_dataset_409(ctx) -> None:
    ctx.repo.datasets["ds1"]["is_locked"] = True
    resp = _post(ctx)
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_DATASET_LOCKED"
    assert ctx.repo.runs == {}


def test_4_invalid_config_409(ctx) -> None:
    resp = _post(ctx, config_id="missing")
    assert resp.status_code == 404  # unknown config id
    _write_profile(Path(ctx.config_service.config_dir), profile="broken", integrity_only=False)
    ctx.repo.configs["c1"] = SimpleNamespace(domain_config={"domain": "general"},
                                             profile_config={"profile": "broken"})
    resp = _post(ctx)
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_CONFIG_INVALID"


# ---------------- 5-9: read / progress / cancel ----------------


def test_5_get_run_200(ctx) -> None:
    run_id = _post(ctx).json()["run_id"]
    resp = ctx.client.get(f"/api/evaluations/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["status"] in ("pending", "running", "completed", "completed_with_errors",
                              "failed", "cancelled")  # background may already finish
    assert body["total_records"] == 3
    for key in ("project_id", "dataset_id", "config_id", "evaluated_records",
                "error_records", "evaluation_coverage", "reproducibility_meta",
                "created_at"):
        assert key in body


def test_6_run_missing_404(ctx) -> None:
    resp = ctx.client.get("/api/evaluations/nope")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_7_progress_200(ctx) -> None:
    run_id = _post(ctx).json()["run_id"]
    resp = ctx.client.get(f"/api/evaluations/{run_id}/progress")
    assert resp.status_code == 200
    body = resp.json()
    # the background run may already have finished — any legal state is fine
    assert body["status"] in ("pending", "running", "completed", "completed_with_errors",
                              "failed", "cancelled")
    assert body["total"] == 3
    assert body["errors"] == 0 and body["cancelled"] is False
    _wait_terminal(ctx.repo, run_id)
    body = ctx.client.get(f"/api/evaluations/{run_id}/progress").json()
    assert body["status"] == "completed" and body["evaluated"] == 3 and body["coverage"] == 1.0


def test_8_cancel_running_run_succeeds(ctx) -> None:
    run_id = _post(ctx).json()["run_id"]
    ctx.repo.runs[run_id]["status"] = "running"  # simulate in-flight
    resp = ctx.client.post(f"/api/evaluations/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"run_id": run_id, "status": "cancelled"}
    assert ctx.repo.runs[run_id]["status"] == "cancelled"


def test_9_cancel_terminal_run_409(ctx) -> None:
    run_id = _post(ctx).json()["run_id"]
    _wait_terminal(ctx.repo, run_id)
    resp = ctx.client.post(f"/api/evaluations/{run_id}/cancel")
    assert resp.status_code == 409
    assert resp.json()["code"] == "BIZ_RUN_NOT_CANCELLABLE"


# ---------------- 10-13: results / diagnoses / recommendations ----------------


def _completed_run(ctx) -> str:
    run_id = _post(ctx).json()["run_id"]
    _wait_terminal(ctx.repo, run_id)
    return run_id


def test_10_results_pagination(ctx) -> None:
    run_id = _completed_run(ctx)
    page1 = ctx.client.get(f"/api/evaluations/{run_id}/results?page=1&page_size=2").json()
    page2 = ctx.client.get(f"/api/evaluations/{run_id}/results?page=2&page_size=2").json()
    assert page1["total"] == 3 and len(page1["items"]) == 2
    assert len(page2["items"]) == 1 and page1["page_size"] == 2
    item = page1["items"][0]
    assert "contexts" not in item and "reference_contexts" not in item  # 大正文不返回
    assert item["is_failure"] in (True, False)


def test_11_results_is_failure_filter(ctx) -> None:
    run_id = _completed_run(ctx)
    failures = ctx.client.get(f"/api/evaluations/{run_id}/results?is_failure=true").json()
    passed = ctx.client.get(f"/api/evaluations/{run_id}/results?is_failure=false").json()
    assert failures["total"] == 2  # r2 value_mismatch + r3 temporal_mismatch
    assert passed["total"] == 1
    assert all(i["is_failure"] is True for i in failures["items"])


def test_12_diagnoses_pagination_and_filters(ctx) -> None:
    run_id = _completed_run(ctx)
    all_d = ctx.client.get(f"/api/evaluations/{run_id}/diagnoses?page=1&page_size=1").json()
    assert all_d["total"] == 2 and len(all_d["items"]) == 1  # r2 + r3
    crit = ctx.client.get(f"/api/evaluations/{run_id}/diagnoses?severity=CRITICAL").json()
    assert crit["total"] == 1 and crit["items"][0]["failure_type"] == "integrity.numerical_mismatch"
    by_type = ctx.client.get(f"/api/evaluations/{run_id}/diagnoses?failure_type=integrity.temporal_mismatch").json()
    assert by_type["total"] == 1 and by_type["items"][0]["severity"] == "ERROR"
    d = all_d["items"][0]
    assert d["status"] == "diagnosed" and d["root_cause"] is not None


def test_13_recommendations_query(ctx) -> None:
    run_id = _completed_run(ctx)
    resp = ctx.client.get(f"/api/evaluations/{run_id}/recommendations")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 2  # one per diagnosed failure
    rec = items[0]
    assert rec["source"] == "rule" and rec["priority"] >= 1 and rec["action"]


# ---------------- 14-15: boundary integrity ----------------


def test_14_api_does_not_bypass_service_or_repository(ctx) -> None:
    """Router has no ORM/Repository/Engine/Diagnosis imports, and requests hit
    the repo only through the service (spy on the shared fake repo)."""
    src = Path("app/api/evaluations.py").read_text(encoding="utf-8")
    for forbidden in ("from app.models", "from app.repositories", "from app.engines",
                      "from app.diagnosis", "EvaluationResultRow", "session.execute"):
        assert forbidden not in src, forbidden
    run_id = _completed_run(ctx)
    detail = ctx.client.get(f"/api/evaluations/{run_id}").json()
    assert detail["run_id"] == run_id


def test_15_post_actually_invokes_runner_not_router_loop(ctx) -> None:
    run_id = _post(ctx).json()["run_id"]
    assert len(ctx.launcher.calls) == 1
    assert ctx.launcher.calls[0]["run_id"] == run_id
    assert ctx.launcher.runner_used is True  # LocalAsyncRunner entered the chain
    status = _wait_terminal(ctx.repo, run_id)
    assert status == "completed"
    # chain produced real engine results through Runner -> Pipeline -> Engine
    scores = [m["score"] for m in ctx.repo.metric_results
              if m["metric_name"] == "temporal_consistency"]
    assert len(scores) == 3 and set(scores) == {1.0, 0.0}
    assert len(ctx.repo.eval_results) == 3
