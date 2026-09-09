"""FR-12 Pipeline 装配专项审计（Freeze Gate §二）— 特征化测试。

审计链路：UI Pipeline Selection -> Create Request -> Run Snapshot -> Run Planner
-> EvalParams -> Pipeline Assembly -> Actual Engine Execution。

审计结论（Phase D 更新 —— Configuration Lifecycle v1 补齐 FR-12）：
  1. UI 能选择什么     : 指标级 enabled/threshold/weight（G4）+ pipeline.engines
                         白名单 + diagnosis.enabled（Profile/导入 YAML）。
  2. 是否进入 API      : 是 —— POST /api/evaluations.metric_overrides + effective merged。
  3. API 是否保存      : 是 —— reproducibility_meta.{enabled_metrics,
                         effective_metrics, metric_overrides, pipeline, rag_input,
                         quality_gate}。
  4. Snapshot 记录     : 是（指标级 + 引擎级 pipeline.engines + diagnosis 开关）。
  5. Service 是否读取  : 是 —— execute_run 只对 effective enabled_metrics 求值。
  6. Pipeline 组装     : 成立 —— resolve_effective_pipeline 将 pipeline.engines
                         白名单应用于 enabled_metrics（排除项显式记录）。
  7. Engine 与选择一致 : 是（snapshot.pipeline.engines == 实际创建 Engine 集合）。
  8. Diagnosis 开关    : **已生效** —— diagnosis.enabled=false 时 Service 编排层
                         跳过诊断（测试 C 锁定）。

最终判定：FR-12 = DONE（引擎白名单 + diagnosis.enabled 执行语义已落地；
pipeline_config DB 列仍仅由 ConfigResourceService 存储，执行路径不读取）。
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
from app.engines.factory import build_engines
from app.engines.judge import JudgeConfig
from app.main import app
from app.runner.local import LocalAsyncRunner
from app.services.evaluation_service import EvaluationService
from app.services.judge_settings_service import default_judge_settings_service
from tests.test_evaluation_service import FakeEvaluationRepo, _record_row

ALL_METRICS = [
    "context_recall", "context_precision", "faithfulness", "answer_relevancy",
    "entity_consistency", "temporal_consistency", "numerical_consistency",
]
RAGAS_METRICS = ALL_METRICS[:4]
INTEGRITY_METRICS = ALL_METRICS[4:]


# ---------------- A. 默认 Pipeline ----------------


def test_a_default_pipeline_assembles_all_registered_engines() -> None:
    engines, skipped = build_engines(ALL_METRICS)
    assert skipped == []
    names = sorted(e.name for e in engines)
    assert names == ["integrity", "ragas"]  # registry-driven, exactly the two builders
    for engine in engines:
        assert set(engine.metric_names())  # every engine owns its metrics


# ---------------- B/D. 指标级选择进入执行路径 ----------------


THRESHOLDED_PROFILE = """
metrics:
  context_recall: { enabled: true, threshold: null, weight: 1.0 }
  faithfulness: { enabled: true, threshold: null, weight: 1.0 }
  answer_relevancy: { enabled: true, threshold: null, weight: 1.0 }
  context_precision: { enabled: true, threshold: null, weight: 1.0 }
  entity_consistency: { enabled: true, threshold: 0.9, weight: 1.0 }
  temporal_consistency: { enabled: true, threshold: 0.9, weight: 1.0 }
  numerical_consistency: { enabled: true, threshold: 0.9, weight: 1.0 }
severity_mapping:
  numerical_mismatch: CRITICAL
"""


def _write_profile(base: Path, profile: str = "default") -> None:
    (base / "domains").mkdir(parents=True, exist_ok=True)
    (base / "evaluations").mkdir(parents=True, exist_ok=True)
    (base / "system.yaml").write_text("system:\n  judge:\n    model: test-judge\n", encoding="utf-8")
    (base / "domains/general.yaml").write_text("domain: general\n", encoding="utf-8")
    (base / f"evaluations/{profile}.yaml").write_text(THRESHOLDED_PROFILE, encoding="utf-8")


class _RealChainLauncher:
    def __init__(self, repo: FakeEvaluationRepo) -> None:
        self.repo = repo
        self.engines_used: list[list[str]] = []

    def __call__(self, run_id, *, enabled_metrics, params):
        # 真实装配路径：launcher 里 build_engines(enabled_metrics)（deps._default_launcher）
        from app.engines.factory import build_engines as real_build

        engines, _ = real_build(enabled_metrics)
        self.engines_used = [e.name for e in engines]

        async def _run():
            svc = EvaluationService(self.repo)
            await svc.execute_run(
                run_id, engines=engines, enabled_metrics=enabled_metrics,
                params=params, runner=LocalAsyncRunner(concurrency=2),
            )

        return asyncio.get_running_loop().create_task(_run())


@pytest.fixture()
def ctx(monkeypatch: pytest.MonkeyPatch):
    # Hermetic regardless of the host environment: a real JUDGE_* config in
    # .env.local would otherwise send this real-chain fixture to the network
    # (30s timeouts blow the 5s terminal-state deadline). Pin an UNCONFIGURED
    # judge so RagasEngine short-circuits into structured BIZ_JUDGE_NOT_
    # CONFIGURED metric errors — the state these FR-12 tests were authored in.
    monkeypatch.setattr(
        default_judge_settings_service, "config",
        lambda: JudgeConfig(provider="", model=""),
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write_profile(base)
        config_service = __import__("app.services.config_service", fromlist=["ConfigService"]).ConfigService(base)
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
        yield SimpleNamespace(client=TestClient(app), repo=repo, launcher=launcher, base=base)
        app.dependency_overrides.clear()


def _wait_terminal(repo, run_id, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = repo.runs[run_id]["status"]
        if status in ("completed", "completed_with_errors", "failed", "cancelled"):
            return status
        time.sleep(0.005)
    raise AssertionError(f"run {run_id} did not reach terminal state")


def _executed_metrics(repo, run_id: str) -> set[str]:
    result_ids = {r["id"] for r in repo.eval_results if r["run_id"] == run_id}
    return {m["metric_name"] for m in repo.metric_results if m["result_id"] in result_ids}


def test_b_integrity_only_selection_executes_only_integrity_engine(ctx) -> None:
    """B：通过 metric_overrides 停用全部 RAGAS 指标 -> 装配只含 integrity 引擎，
    且实际执行结果只有 integrity 指标行（UI 选择 == 引擎 == 执行结果）。"""
    overrides = {name: {"enabled": False} for name in RAGAS_METRICS}
    resp = ctx.client.post("/api/evaluations", json={
        "project_id": "p1", "dataset_id": "ds1", "config_id": "c1",
        "metric_overrides": overrides,
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert _wait_terminal(ctx.repo, run_id) == "completed"

    assert ctx.launcher.engines_used == ["integrity"]  # 装配结果
    assert _executed_metrics(ctx.repo, run_id) == set(INTEGRITY_METRICS)  # 执行结果
    meta = resp.json()["reproducibility_meta"]
    assert set(meta["enabled_metrics"]) == set(INTEGRITY_METRICS)  # snapshot


def test_d_selection_change_changes_execution(ctx) -> None:
    """D：修改选择（停用 faithfulness + entity_consistency）-> 执行集合精确变化，
    snapshot 与执行结果一致。"""
    resp = ctx.client.post("/api/evaluations", json={
        "project_id": "p1", "dataset_id": "ds1", "config_id": "c1",
        "metric_overrides": {
            "faithfulness": {"enabled": False},
            "entity_consistency": {"enabled": False},
        },
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    _wait_terminal(ctx.repo, run_id)

    expected = set(ALL_METRICS) - {"faithfulness", "entity_consistency"}
    assert _executed_metrics(ctx.repo, run_id) == expected
    meta = resp.json()["reproducibility_meta"]
    assert set(meta["enabled_metrics"]) == expected
    assert meta["metric_overrides"] == {
        "faithfulness": {"enabled": False},
        "entity_consistency": {"enabled": False},
    }


# ---------------- C. diagnosis.enabled 执行语义（Phase D 已接通） ----------------


def test_c_diagnosis_disabled_profile_is_now_honored(ctx) -> None:
    """C（Phase D 反转）：Profile diagnosis.enabled=false 现在真实生效 ——
    Service 编排层跳过诊断（DiagnosisEngine 本体不变），results/metric_results
    照常持久化，diagnoses 行为零。诊断开关为唯一行为变更点。"""
    # 在部署 Profile YAML 中写入 diagnosis.enabled=false（ConfigService 每次重新读文件）
    profile_path = ctx.base / "evaluations" / "default.yaml"
    profile_path.write_text(THRESHOLDED_PROFILE + "\ndiagnosis:\n  enabled: false\n", encoding="utf-8")
    try:
        resp = ctx.client.post("/api/evaluations", json={
            "project_id": "p1", "dataset_id": "ds1", "config_id": "c1",
        })
        run_id = resp.json()["run_id"]
        meta = resp.json()["reproducibility_meta"]
        _wait_terminal(ctx.repo, run_id)
        assert meta["pipeline"]["diagnosis"] == {"enabled": False}
        assert len(ctx.repo.metric_results) > 0   # 指标照常执行
        assert len(ctx.repo.eval_results) > 0     # 结果照常持久化
        assert len(ctx.repo.diagnoses) == 0       # 诊断被跳过

        # 源码级：诊断开关由 Service 编排层读取（Effective Config flag 权威）
        from pathlib import Path as _P

        root = _P(__file__).resolve().parents[1]
        service_src = (root / "app/services/evaluation_service.py").read_text(encoding="utf-8")
        assert "diagnosis_enabled" in service_src
        planner_src = (root / "app/services/run_planner.py").read_text(encoding="utf-8")
        assert "diagnosis_enabled" in planner_src
    finally:
        profile_path.write_text(THRESHOLDED_PROFILE, encoding="utf-8")


def test_9_pipeline_config_is_stored_but_never_executed() -> None:
    """Phase D 更新：执行路径读取的是 MERGED EFFECTIVE pipeline（run_planner），
    而非 evaluation_configs.pipeline_config 列（该列仍仅由 ConfigResourceService
    存储，防止第二配置事实来源）。引擎集合由 enabled_metrics + 白名单推导。"""
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[1]
    planner = (root / "app/services/run_planner.py").read_text(encoding="utf-8")
    service = (root / "app/services/evaluation_service.py").read_text(encoding="utf-8")
    # DB 列 pipeline_config 仍不在执行路径
    assert "pipeline_config" not in planner
    assert "pipeline_config" not in service
    # Effective pipeline 现在真实进入执行链
    assert "resolve_effective_pipeline" in planner
    assert "diagnosis_enabled" in service
