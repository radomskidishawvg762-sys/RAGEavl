"""Phase D — Effective Configuration == Run Snapshot == Actual Execution.

End-to-end invariant tests (task §7/§10): whatever the Run Snapshot records is
EXACTLY what engines/metric_results/quality-gate/diagnosis actually use.

  1  override threshold        8  diagnosis.enabled=true
  2  override weight           9  diagnosis.enabled=false
  3  disable metric           10 rag_input golden replay
  4  effective config         11 rag_input http (mode dispatch + snapshot)
  5  snapshot completeness    12 historical run unaffected by new version
  6  snapshot == results      13 quality gate uses historical snapshot
  7  pipeline engine select   14 secret isolation

Real execution chain: POST /api/evaluations -> apply_metric_overrides ->
resolve_profile(effective) -> REAL LocalAsyncRunner + IntegrityEngine -> fake
repo persistence. Only the Repository and ConfigService config dir are faked.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.adapters.rag_input import GoldenRunMetadataAdapter, HttpRagAdapter, build_rag_adapter
from app.api.deps import get_config_service, get_evaluation_launcher, get_evaluation_service
from app.core.errors import ConfigInvalidError
from app.main import app
from app.runner.local import LocalAsyncRunner
from app.services.evaluation_service import EvaluationService
from tests.test_evaluation_service import FakeEvaluationRepo, _record_row

RECORDS = [
    _record_row("r1", "q1", "2024年度", "2024年"),
    _record_row("r2", "q2", "2亿元", "1亿元", ["ref 1亿元"]),
    _record_row("r3", "q3", "2023年", "2024年"),
]


def _body(**extra) -> dict:
    body = {
        "profile": {"name": "phase_d"},
        "metrics": {
            "entity_consistency": {"enabled": True, "threshold": 0.85, "weight": 1.0},
            "numerical_consistency": {"enabled": True, "threshold": 0.9, "weight": 2.0},
        },
        "severity_mapping": {"numerical_mismatch": "CRITICAL"},
    }
    body.update(extra)
    return body


class _Launcher:
    def __init__(self, repo: FakeEvaluationRepo) -> None:
        self.repo = repo
        self.engines_used: list[str] = []

    def __call__(self, run_id, *, enabled_metrics, params):
        from app.engines.factory import build_engines

        engines, _skipped = build_engines(enabled_metrics, judge_config=params.extra.get("judge_config"))
        self.engines_used = sorted(e.name for e in engines)
        self.last_params = params

        async def _run():
            await EvaluationService(self.repo).execute_run(
                run_id, engines=engines, enabled_metrics=enabled_metrics,
                params=params, runner=LocalAsyncRunner(concurrency=2),
            )

        return asyncio.get_running_loop().create_task(_run())


@pytest.fixture()
def factory():
    """Builds a full run context against a stored (imported) profile body."""
    created: list = []

    def _make(body: dict, *, profile_yaml: str | None = None) -> SimpleNamespace:
        tmp = tempfile.TemporaryDirectory()
        created.append(tmp)
        base = Path(tmp.name)
        (base / "domains").mkdir(parents=True, exist_ok=True)
        (base / "evaluations").mkdir(parents=True, exist_ok=True)
        (base / "system.yaml").write_text("system:\n  judge:\n    model: test-judge\n", encoding="utf-8")
        (base / "domains/general.yaml").write_text("domain: general\n", encoding="utf-8")
        (base / "evaluations/default.yaml").write_text(
            profile_yaml or "metrics:\n  entity_consistency: { enabled: true, threshold: null }\n",
            encoding="utf-8")
        config_service = __import__("app.services.config_service", fromlist=["ConfigService"]).ConfigService(base)

        stored_row = SimpleNamespace(
            id="c1", domain_config={"domain": "general"},
            profile_config={"profile": body["profile"]["name"], "version": "v1",
                            "source": "imported", "yaml": "n/a", "body": body},
            config_version=config_service.config_version("general", body["profile"]["name"], profile_body=body),
        )
        repo = FakeEvaluationRepo(
            {"ds1": {"is_locked": False, "record_count": len(RECORDS), "version": "v1"}},
            list(RECORDS), configs={"c1": stored_row},
        )
        launcher = _Launcher(repo)
        from app.api.deps import get_quality_gate_service
        from app.services.quality_gate_service import QualityGateService

        app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(repo)
        app.dependency_overrides[get_config_service] = lambda: config_service
        app.dependency_overrides[get_evaluation_launcher] = lambda: launcher
        app.dependency_overrides[get_quality_gate_service] = lambda: QualityGateService(repo, config_service)
        return SimpleNamespace(
            client=TestClient(app), repo=repo, launcher=launcher,
            config_service=config_service, row=stored_row, body=body,
        )

    yield _make
    for tmp in created:
        tmp.cleanup()
    app.dependency_overrides.clear()


def _start(ctx, overrides=None) -> tuple[str, dict]:
    payload: dict = {"project_id": "p1", "dataset_id": "ds1", "config_id": "c1"}
    if overrides:
        payload["metric_overrides"] = overrides
    resp = ctx.client.post("/api/evaluations", json=payload)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    _wait(ctx.repo, data["run_id"])
    return data["run_id"], data["reproducibility_meta"]


def _wait(repo, run_id, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if repo.runs[run_id]["status"] in ("completed", "completed_with_errors", "failed", "cancelled"):
            return repo.runs[run_id]["status"]
        time.sleep(0.005)
    raise AssertionError(f"run {run_id} stuck")


def _metric_rows(repo, run_id, name):
    result_ids = {r["id"] for r in repo.eval_results if r["run_id"] == run_id}
    return [m for m in repo.metric_results
            if m["metric_name"] == name and m["result_id"] in result_ids]


def _all_metric_rows(repo, run_id):
    result_ids = {r["id"] for r in repo.eval_results if r["run_id"] == run_id}
    return [m for m in repo.metric_results if m["result_id"] in result_ids]


# ---------------- 1/2/3: Run Overrides ----------------


def test_1_override_threshold_reaches_result_and_snapshot(factory):
    ctx = factory(_body())
    _run_id, meta = _start(ctx, {"entity_consistency": {"threshold": 0.92}})
    assert meta["effective_metrics"]["entity_consistency"]["threshold"] == 0.92
    assert meta["effective_metrics"]["numerical_consistency"]["threshold"] == 0.9  # untouched
    rows = _metric_rows(ctx.repo, _run_id, "entity_consistency")
    assert rows and all(r["threshold"] == 0.92 for r in rows)  # NOT the profile 0.85


def test_2_override_weight_changes_weights_and_overall(factory):
    ctx = factory(_body())
    _run_id, meta = _start(ctx, {"entity_consistency": {"weight": 3.0}})
    assert meta["metric_weights"]["entity_consistency"] == 3.0
    assert meta["metric_weights"]["numerical_consistency"] == 2.0
    # overall_score is the weighted mean over VALID scores using the SNAPSHOT weights
    rows = _all_metric_rows(ctx.repo, _run_id)
    num = sum(float(r["score"]) * meta["metric_weights"][r["metric_name"]]
              for r in rows if r["score"] is not None)
    den = sum(meta["metric_weights"][r["metric_name"]] for r in rows if r["score"] is not None)
    run = ctx.repo.runs[_run_id]
    assert run["overall_score"] == pytest.approx(num / den)


def test_3_override_disable_metric(factory):
    ctx = factory(_body())
    _run_id, meta = _start(ctx, {"numerical_consistency": {"enabled": False}})
    assert "numerical_consistency" not in meta["enabled_metrics"]
    assert meta["effective_metrics"].get("numerical_consistency") is None
    assert _metric_rows(ctx.repo, _run_id, "numerical_consistency") == []
    assert len(_metric_rows(ctx.repo, _run_id, "entity_consistency")) > 0


# ---------------- 4/5/6: Effective Config / Snapshot invariants ----------------


def test_4_effective_config_is_the_single_execution_input(factory):
    body = _body()
    ctx = factory(body)
    _run_id, meta = _start(ctx, {"entity_consistency": {"threshold": 0.7}})
    # effective == profile merged + overrides, exactly
    assert meta["effective_metrics"] == {
        "entity_consistency": {"threshold": 0.7, "weight": 1.0},
        "numerical_consistency": {"threshold": 0.9, "weight": 2.0},
    }
    # and the execution params carried EXACTLY the same values
    extra = ctx.launcher.last_params.extra
    assert extra["entity_consistency"]["threshold"] == 0.7


def test_5_snapshot_completeness(factory):
    ctx = factory(_body(quality_gate=None))
    _run_id, meta = _start(ctx)
    for key in ("profile", "profile_version", "config_version", "enabled_metrics",
                "metric_weights", "effective_metrics", "metric_overrides",
                "pipeline", "rag_input", "quality_gate", "dataset_version",
                "config_version", "metric_version", "timestamp",
                "judge_model", "judge_model_version"):
        assert key in meta, key
    assert meta["profile"] == "phase_d"
    assert meta["profile_version"] == "v1"
    assert meta["pipeline"]["diagnosis"] == {"enabled": True}
    assert meta["rag_input"]["mode"] == "golden_replay"
    # snapshot config_version == sha256(canonical effective config of the stored body)
    assert meta["config_version"] == ctx.config_service.config_version(
        "general", "phase_d", profile_body=ctx.body)


def test_6_snapshot_thresholds_equal_metric_results(factory):
    ctx = factory(_body())
    overrides = {"entity_consistency": {"threshold": 0.5}, "numerical_consistency": {"threshold": 0.4}}
    _run_id, meta = _start(ctx, overrides)
    for name, threshold in (
        ("entity_consistency", meta["effective_metrics"]["entity_consistency"]["threshold"]),
        ("numerical_consistency", meta["effective_metrics"]["numerical_consistency"]["threshold"]),
    ):
        for row in _metric_rows(ctx.repo, _run_id, name):
            assert row["threshold"] == threshold


# ---------------- 7: pipeline engine selection ----------------


def test_7_pipeline_engine_whitelist_filters_metrics(factory):
    body = _body(pipeline={"engines": ["integrity"], "diagnosis": {"enabled": True}})
    body["metrics"]["faithfulness"] = {"enabled": True, "threshold": None, "weight": 1.0}
    ctx = factory(body)
    _run_id, meta = _start(ctx)
    assert meta["pipeline"]["selected_engines"] == ["integrity"]
    assert meta["pipeline"]["engines"] == ["integrity"]          # == actual engines
    assert meta["pipeline"]["excluded_metrics"] == {"faithfulness": "ragas"}
    assert ctx.launcher.engines_used == ["integrity"]            # == actual engines (runner side)
    assert meta["enabled_metrics"] == ["entity_consistency", "numerical_consistency"]
    assert _metric_rows(ctx.repo, _run_id, "faithfulness") == []  # explicit, not silent


# ---------------- 8/9: diagnosis.enabled ----------------


def test_8_diagnosis_enabled_true_executes(factory):
    ctx = factory(_body())
    _run_id, meta = _start(ctx)
    assert meta["pipeline"]["diagnosis"] == {"enabled": True}
    assert len(ctx.repo.diagnoses) > 0  # mismatching records produce diagnoses


def test_9_diagnosis_enabled_false_skips_orchestration(factory):
    body = _body(pipeline={"diagnosis": {"enabled": False}})
    ctx = factory(body)
    _run_id, meta = _start(ctx)
    assert meta["pipeline"]["diagnosis"] == {"enabled": False}
    assert len(ctx.repo.metric_results) > 0       # metrics ran
    assert len(ctx.repo.eval_results) == len(RECORDS)
    assert len(ctx.repo.diagnoses) == 0           # diagnosis skipped


# ---------------- 10/11: RAG input modes ----------------


def test_10_rag_golden_replay_mode(factory):
    ctx = factory(_body())
    _run_id, meta = _start(ctx)
    assert meta["rag_input"] == {"mode": "golden_replay", "url": None,
                                 "timeout": None, "retry": None}
    # golden metadata answers actually used (metadata_.answer -> contexts via golden bridge)
    result = next(r for r in ctx.repo.eval_results if r["run_id"] == _run_id)
    assert result["answer"] != ""


def test_11_rag_http_mode_dispatch_and_snapshot(factory):
    # adapter dispatch: explicit mode wins, never mixed (task §6)
    assert isinstance(build_rag_adapter({"mode": "http", "url": "http://x", "timeout": 5, "retry": 1}),
                      HttpRagAdapter)
    assert isinstance(build_rag_adapter({"mode": "golden_replay", "url": "http://x"}),
                      GoldenRunMetadataAdapter)  # url present but mode=golden -> replay
    assert isinstance(build_rag_adapter({"url": "http://x"}), HttpRagAdapter)  # legacy inference
    with pytest.raises(ConfigInvalidError):
        build_rag_adapter({"mode": "http"})  # http without url = config error, no silent fallback

    # snapshot records the http selection (network execution covered by adapter tests)
    body = _body(rag_input={"mode": "http", "url": "http://127.0.0.1:1/query",
                            "timeout": 1, "retry": 1})
    ctx = factory(body)
    _run_id, meta = _start(ctx)
    assert meta["rag_input"]["mode"] == "http"
    assert meta["rag_input"]["url"] == "http://127.0.0.1:1/query"


# ---------------- 12: historical run unaffected by later versions ----------------


def test_12_historical_run_immune_to_new_profile_version(factory):
    v1_body = _body()
    ctx = factory(v1_body)
    run_a, meta_a = _start(ctx)
    row_v1 = ctx.row
    snapshot_v1 = dict(row_v1.profile_config)
    hash_v1 = row_v1.config_version

    # second unlocked dataset for run B (ds1 is locked by run A — version immutable)
    ctx.repo.datasets["ds2"] = {"is_locked": False, "record_count": len(RECORDS), "version": "v1"}
    for r in RECORDS:
        ctx.repo.records.append(_record_row(r.id + "-b", r.question,
                                            r.metadata_.get("answer"),
                                            r.reference_answer, r.reference_contexts))

    # "import" v2 (stricter threshold) as a NEW row — old row untouched
    v2_body = _body()
    v2_body["metrics"]["entity_consistency"]["threshold"] = 0.99
    row_v2 = SimpleNamespace(
        id="c2", domain_config={"domain": "general"},
        profile_config={"profile": "phase_d", "version": "v2", "source": "imported",
                        "yaml": "n/a", "body": v2_body},
        config_version=ctx.config_service.config_version("general", "phase_d", profile_body=v2_body),
    )
    ctx.repo.configs["c2"] = row_v2

    resp = ctx.client.post("/api/evaluations", json={
        "project_id": "p1", "dataset_id": "ds2", "config_id": "c2"})
    assert resp.status_code == 202, resp.text
    meta_b = resp.json()["reproducibility_meta"]
    run_b = resp.json()["run_id"]
    _wait(ctx.repo, run_b)

    # v1 row byte-stable (invariant 2/4)
    assert dict(row_v1.profile_config) == snapshot_v1
    assert row_v1.config_version == hash_v1
    # hashes differ; run A keeps v1 identity
    assert meta_b["config_version"] != meta_a["config_version"]
    assert meta_a["config_version"] == hash_v1
    # run A results still carry v1 thresholds (0.85), NOT v2's 0.99
    assert all(r["threshold"] == 0.85 for r in _metric_rows(ctx.repo, run_a, "entity_consistency"))
    # and run B actually executes v2's threshold
    assert all(r["threshold"] == 0.99 for r in _metric_rows(ctx.repo, run_b, "entity_consistency"))


# ---------------- 13: quality gate uses the historical snapshot ----------------


def test_13_quality_gate_uses_snapshot_not_current_yaml(factory):
    profile_yaml = (
        "profile: {name: default, version: v1}\n"
        "metrics:\n"
        "  entity_consistency: { enabled: true, threshold: null, weight: 1.0 }\n"
        "quality_gate:\n"
        "  enabled: true\n"
        "  required_metrics: [entity_consistency]\n"
    )
    ctx = factory(_body(), profile_yaml=profile_yaml)
    ctx.row.profile_config = {"profile": "default"}  # pointer row -> YAML file source
    ctx.row.config_version = ctx.config_service.config_version("general", "default")

    run_id, meta = _start(ctx)
    assert meta["quality_gate"] is not None  # gate snapshotted at run creation

    before = ctx.client.get(f"/api/evaluations/{run_id}/quality-gate").json()

    # NOW mutate the deployment YAML: disable the gate entirely
    yaml_path = Path(ctx.config_service.config_dir) / "evaluations" / "default.yaml"
    yaml_path.write_text(profile_yaml.replace("  enabled: true\n", "  enabled: false\n"), encoding="utf-8")

    after = ctx.client.get(f"/api/evaluations/{run_id}/quality-gate").json()
    # historical run stays on its SNAPSHOT gate — current YAML cannot override it
    assert after["status"] == before["status"]
    assert after["reasons"] == before["reasons"]


# ---------------- 14: secret isolation in the snapshot ----------------


def test_14_snapshot_contains_no_secrets(factory):
    ctx = factory(_body())
    _run_id, meta = _start(ctx)
    import json

    dump = json.dumps(meta).lower()
    for forbidden in ("api_key", "password", "secret", "authorization"):
        assert forbidden not in dump, forbidden
    judge_keys = {k for k in meta if k.startswith("judge_")}
    assert judge_keys and all(k in {"judge_provider", "judge_model", "judge_model_version",
                                    "judge_temperature", "judge_max_tokens", "judge_timeout",
                                    "judge_retry"} for k in judge_keys)
