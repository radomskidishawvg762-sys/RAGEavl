"""T-12 EvaluationService persistence & orchestration tests.

Fake-repository based (project convention: real-DB race I-1b deferred to
T-22, see tests/test_dataset_lock.py). Covers the 12 mandated points plus
the §1 error-semantics invariant (ambiguous/mismatch must NEVER persist an
error) and the run state machine.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.core.errors import BizError, DatasetLockedError, NotFoundError, RunNotCancellableError
from app.diagnosis import DiagnosisEngine
from app.domain.schemas import MetricResult
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.models import DatasetRecord
from app.repositories.evaluation import EvaluationRepository
from app.services.evaluation_service import _RUN_TRANSITIONS, EvaluationService

# ---------------- fakes ----------------


class FakeEvaluationRepo:
    """In-memory mirror of EvaluationRepository's surface."""

    def __init__(self, datasets: dict, records: list[DatasetRecord], configs: dict | None = None):
        self.datasets = datasets  # {id: SimpleNamespace(is_locked, record_count)}
        self.records = records
        self.configs = configs or {}
        self.runs: dict[str, dict] = {}
        self.eval_results: list[dict] = []
        self.metric_results: list[dict] = []
        self.diagnoses: list[dict] = []
        self.recommendations: list[dict] = []
        self.for_update_reads = 0
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"id-{self._seq}"

    # lifecycle

    def create_run_with_lock(self, *, project_id, dataset_id, config_id, reproducibility_meta):
        self.for_update_reads += 1  # fake of SELECT ... FOR UPDATE
        ds = self.datasets[dataset_id]
        if ds["is_locked"]:
            raise DatasetLockedError("locked")
        run_id = self._next_id()
        self.runs[run_id] = {
            "id": run_id, "project_id": project_id, "dataset_id": dataset_id,
            "config_id": config_id, "status": "pending",
            "total_records": ds["record_count"], "evaluated_records": 0,
            "error_records": 0, "evaluation_coverage": None, "overall_score": None,
            "reproducibility_meta": reproducibility_meta,
            "started_at": None, "finished_at": None, "error_summary": None,
            "created_at": datetime.now(UTC),
        }
        ds["is_locked"] = True
        return self.get_run(run_id)

    def get_run(self, run_id):
        if run_id not in self.runs:
            raise NotFoundError(f"run {run_id} not found")
        return SimpleNamespace(**self.runs[run_id])

    def update_run(self, run_id, **values):
        self.runs[run_id].update({k: v for k, v in values.items() if v is not None or k.endswith("_at")})
        return self.runs[run_id]

    def cancel_run_if_active(self, run_id, *, finished_at):
        run = self.runs[run_id]
        if run["status"] not in {"pending", "running"}:
            return None
        run.update(status="cancelled", finished_at=finished_at)
        return self.get_run(run_id)

    def start_run_if_pending(self, run_id, *, started_at):
        run = self.runs[run_id]
        if run["status"] != "pending":
            return None
        run.update(status="running", started_at=started_at)
        return self.get_run(run_id)

    def fail_run_if_active(self, run_id, *, finished_at, error_summary):
        run = self.runs[run_id]
        if run["status"] not in {"pending", "running"}:
            return None
        run.update(status="failed", finished_at=finished_at, error_summary=error_summary)
        return self.get_run(run_id)

    def finish_run_if_running(self, run_id, **values):
        run = self.runs[run_id]
        if run["status"] != "running":
            return None
        run.update({k: v for k, v in values.items() if v is not None or k.endswith("_at")})
        return self.get_run(run_id)

    def update_run_progress(self, run_id, **values):
        run = self.runs[run_id]
        if run["status"] in {"running", "cancelled"}:
            run.update(values)
        return self.get_run(run_id)

    def list_records(self, dataset_id):
        return self.records

    # ---- T-13 read surface (mirrors EvaluationRepository) ----

    def get_config(self, config_id):
        cfg = self.configs.get(config_id)
        if cfg is None:
            raise NotFoundError(f"config {config_id} not found")
        return cfg

    def get_dataset(self, dataset_id):
        ds = self.datasets.get(dataset_id)
        if ds is None:
            raise NotFoundError(f"dataset {dataset_id} not found")
        return SimpleNamespace(id=dataset_id, version=ds.get("version", "v1"))

    def list_runs(self, *, page, page_size, project_id=None, status=None):
        rows = sorted(self.runs.values(), key=lambda r: r["created_at"], reverse=True)
        if project_id is not None:
            rows = [r for r in rows if r["project_id"] == project_id]
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        total = len(rows)
        start = (page - 1) * page_size
        return [SimpleNamespace(**r) for r in rows[start:start + page_size]], total

    def list_results(self, run_id, *, page, page_size, is_failure=None):
        rows = [r for r in self.eval_results if r["run_id"] == run_id]
        if is_failure is not None:
            rows = [r for r in rows if r["is_failure"] == is_failure]
        total = len(rows)
        start = (page - 1) * page_size
        return [SimpleNamespace(**r) for r in rows[start:start + page_size]], total

    def list_diagnoses(self, run_id, *, page, page_size, severity=None, failure_type=None):
        rows = [d for d in self.diagnoses if d["run_id"] == run_id]
        if severity is not None:
            rows = [d for d in rows if d["severity"] == severity]
        if failure_type is not None:
            rows = [d for d in rows if d["failure_type"] == failure_type]
        total = len(rows)
        start = (page - 1) * page_size
        return [SimpleNamespace(**d) for d in rows[start:start + page_size]], total

    def list_recommendations(self, run_id):
        diag_ids = {d["id"] for d in self.diagnoses if d["run_id"] == run_id}
        return [SimpleNamespace(**r) for r in self.recommendations if r["diagnosis_id"] in diag_ids]

    # persistence

    def insert_evaluation_result(self, row):
        if row.id is None:
            row.id = self._next_id()  # emulate flush-time column default
        item = {"id": row.id, **{c: getattr(row, c) for c in (
            "run_id", "record_id", "row_index", "question", "contexts", "answer",
            "reference_answer", "reference_contexts", "is_failure")},
            "created_at": datetime.now(UTC)}
        self.eval_results.append(item)
        return SimpleNamespace(**item)

    def insert_metric_results(self, rows):
        for r in rows:
            if r.id is None:
                r.id = self._next_id()
            self.metric_results.append({c: getattr(r, c) for c in (
                "id", "result_id", "metric_name", "category", "score", "threshold",
                "passed", "comparison_basis", "metric_version", "error")})
        return rows

    def insert_diagnosis_with_recommendations(self, diagnosis, recommendations):
        if diagnosis.id is None:
            diagnosis.id = self._next_id()  # emulate flush before recommendations FK
        item = {"id": diagnosis.id, **{c: getattr(diagnosis, c) for c in (
            "run_id", "result_id", "status", "failure_type", "related_metric",
            "root_cause", "severity", "evidence", "evidence_contract",
            "confidence", "detail")},
            "created_at": datetime.now(UTC)}
        self.diagnoses.append(item)
        for rec in recommendations:
            rec.diagnosis_id = diagnosis.id  # real repo sets the FK before insert
            if rec.id is None:
                rec.id = self._next_id()
            self.recommendations.append({c: getattr(rec, c) for c in (
                "id", "diagnosis_id", "action", "priority", "source")})
        return SimpleNamespace(**item)


def _record_row(rid: str, question: str, answer: str, reference: str | None,
                reference_contexts: list[str] | None = None) -> DatasetRecord:
    return DatasetRecord(
        id=rid, dataset_id="ds1", row_index=0, question=question,
        reference_answer=reference, reference_contexts=reference_contexts,
        metadata_={"answer": answer},
    )


def _make_service(records: list[DatasetRecord], locked: bool = False):
    repo = FakeEvaluationRepo({"ds1": {"is_locked": locked, "record_count": len(records)}}, records)
    return EvaluationService(repo), repo


def _create_and_execute(service, repo, engines, metrics, threshold=None):
    run = service.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1",
        reproducibility_meta={"config_version": "x", "metric_version": "y"},
    )
    summary = asyncio.run(service.execute_run(
        run.id, engines=engines, enabled_metrics=metrics,
        params=EvalParams(threshold=threshold),
        diagnosis_engine=DiagnosisEngine(),
    ))
    return run.id, summary


INTEGRITY = ["entity_consistency", "temporal_consistency", "numerical_consistency"]

# ---------------- stub engines ----------------


class _ThresholdEngine:
    """RAGAS-like: fixed score, threshold-driven failure only."""

    name = "gen"

    def __init__(self, score: float) -> None:
        self._score = score

    def metric_names(self):
        return ["faithfulness"]

    async def evaluate(self, record, metrics, params):
        return [MetricResult(
            record_id=record.id, metric_name="faithfulness", category="generation",
            score=self._score, threshold=params.threshold, metric_version="gen-v1",
        )]


class _BoomEngine:
    name = "boom"

    def metric_names(self):
        return ["m1"]

    async def evaluate(self, record, metrics, params):
        if record.id.startswith("bad"):
            raise RuntimeError("provider unavailable")
        return [MetricResult(record_id=record.id, metric_name="m1", category="generation",
                             score=1.0, threshold=None, metric_version="t")]


class _BasisEngine:
    """Emits a hand-built deterministic-basis result (contract-unmet path)."""

    name = "basis"

    def metric_names(self):
        return ["numerical_consistency"]

    async def evaluate(self, record, metrics, params):
        from app.domain.schemas import ComparisonBasis

        return [MetricResult(
            record_id=record.id, metric_name="numerical_consistency", category="integrity",
            score=0.0, threshold=None,
            comparison_basis=ComparisonBasis(
                reference={"primary": {"raw": "1亿元"}, "side_status": "ok"},
                answer={"primary": {"raw": "2亿元"}, "side_status": "ok"},
                comparison_type="value_mismatch", method="deterministic",
                diff={"base": {"abs_diff": 1e8}},
            ),
            metric_version="integrity-normalization-v1",
        )]


# ---------------- 1-3: metric result persistence ----------------


def test_1_normal_metric_result_persisted() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2024年度", "2024年")])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["temporal_consistency"])
    row = next(m for m in repo.metric_results if m["metric_name"] == "temporal_consistency")
    assert row["score"] == 1.0 and row["error"] is None
    assert row["result_id"] == repo.eval_results[0]["id"]


def test_2_comparison_basis_persisted_as_jsonb_dict() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2024年度", "2024年")])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["temporal_consistency"])
    basis = repo.metric_results[0]["comparison_basis"]
    assert isinstance(basis, dict)
    assert basis["comparison_type"] == "match"
    assert basis["method"] == "deterministic"
    assert basis["reference"]["primary"]["interval_start"] == "2024-01-01"


def test_3_evaluator_error_persisted() -> None:
    # reference missing -> metric-level input error: score null, basis null, error kept
    svc, repo = _make_service([_record_row("r1", "q", "1亿元", None)])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["numerical_consistency"])
    row = repo.metric_results[0]
    assert row["score"] is None and row["comparison_basis"] is None
    assert row["error"]["code"] == "BIZ_METRIC_INPUT_MISSING"


# ---------------- 4: is_failure semantics ----------------


def test_4_is_failure_semantics() -> None:
    # deterministic mismatch -> True
    svc, repo = _make_service([_record_row("r1", "q", "2亿元", "1亿元")])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["numerical_consistency"])
    assert repo.eval_results[0]["is_failure"] is True

    # threshold failure -> True
    svc, repo = _make_service([_record_row("r1", "q", "a", "b")])
    _create_and_execute(svc, repo, [_ThresholdEngine(0.42)], ["faithfulness"], threshold=0.8)
    assert repo.eval_results[0]["is_failure"] is True

    # ambiguous -> False (cannot attribute != no failure; distinction via diagnosis status)
    svc, repo = _make_service([_record_row("r1", "q", "去年营收增长", "今年营收增长")])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["temporal_consistency"])
    assert repo.eval_results[0]["is_failure"] is False

    # evaluator error -> False (Evaluation Error, counted in error_records)
    svc, repo = _make_service([_record_row("r1", "q", "1亿元", None)])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["numerical_consistency"])
    assert repo.eval_results[0]["is_failure"] is False


# ---------------- 5-7: diagnosis / recommendation persistence ----------------


def test_5_diagnosed_diagnosis_persisted() -> None:
    svc, repo = _make_service([
        _record_row("r1", "q", "2亿元", "1亿元", ["2024年公司营业收入为1亿元。"])
    ])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["numerical_consistency"])
    d = repo.diagnoses[0]
    assert d["status"] == "diagnosed"
    assert d["failure_type"] == "integrity.numerical_mismatch"
    assert d["root_cause"] is not None
    assert d["result_id"] == repo.eval_results[0]["id"]  # FK semantics: evaluation_results.id
    assert d["evidence_contract"] == "integrity.numerical_mismatch.v1"
    assert {e["type"] for e in d["evidence"]} >= {"reference_evidence", "answer_claim"}
    assert d["detail"] is None


def test_6_undetermined_diagnosis_persisted() -> None:
    # contract unmet (no reference evidence) -> undetermined with candidate code
    svc, repo = _make_service([_record_row("r1", "q", "2亿元", None)])
    _create_and_execute(svc, repo, [_BasisEngine()], ["numerical_consistency"])
    d = repo.diagnoses[0]
    assert d["status"] == "undetermined"
    assert d["root_cause"] is None  # attribution forbidden
    assert d["failure_type"] == "integrity.numerical_mismatch"  # real code, not sentinel
    assert "reference_evidence" in d["detail"]["missing_evidence"]
    assert d["detail"]["reason"]

    # ambiguous -> undetermined with failure_type NULL (no applicable code)
    svc, repo = _make_service([_record_row("r1", "q", "去年", "今年")])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["temporal_consistency"])
    d = repo.diagnoses[-1]
    assert d["status"] == "undetermined"
    assert d["failure_type"] is None
    assert d["root_cause"] is None


def test_7_recommendations_persisted_with_link() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2亿元", "1亿元")])
    _create_and_execute(svc, repo, [IntegrityEngine()], ["numerical_consistency"])
    d = repo.diagnoses[0]
    recs = [r for r in repo.recommendations if r["diagnosis_id"] == d["id"]]
    assert len(recs) >= 1
    assert all(r["source"] == "rule" for r in recs)
    assert any("单位归一化" in r["action"] or "数值" in r["action"] for r in recs)


# ---------------- 8-11: run states & partial preservation ----------------


def test_8_run_completed() -> None:
    svc, repo = _make_service([
        _record_row("r1", "q", "2024年度", "2024年"),
        _record_row("r2", "q", "2024年", "2024年度"),
    ])
    _, summary = _create_and_execute(svc, repo, [IntegrityEngine()], ["temporal_consistency"])
    run = repo.runs[summary["run_id"]]
    assert run["status"] == "completed"
    assert run["evaluated_records"] == 2 and run["error_records"] == 0
    assert run["evaluation_coverage"] == 1.0
    assert run["started_at"] is not None and run["finished_at"] is not None


def test_9_completed_with_errors() -> None:
    svc, repo = _make_service([
        _record_row("good", "q", "2024年度", "2024年"),
        _record_row("bad", "q", "x", "y"),
    ])
    _, summary = _create_and_execute(svc, repo, [_BoomEngine()], ["m1"])
    run = repo.runs[summary["run_id"]]
    assert run["status"] == "completed_with_errors"
    assert run["error_records"] == 1 and run["evaluated_records"] == 1
    assert run["error_summary"]["details"][0]["record_id"] == "bad"


def test_10_run_failed_when_all_records_error() -> None:
    svc, repo = _make_service([_record_row("bad1", "q", "x", "y"), _record_row("bad2", "q", "x", "y")])
    _, summary = _create_and_execute(svc, repo, [_BoomEngine()], ["m1"])
    assert repo.runs[summary["run_id"]]["status"] == "failed"


def test_11_partial_results_preserved() -> None:
    """Record with one OK metric + one error metric: both rows persisted,
    record counts as evaluated, run completes with errors only if a record
    produced nothing evaluable."""
    from app.domain.schemas import MetricResult as MR

    class _MixedEngine:
        name = "mixed"

        def metric_names(self):
            return ["m1", "m2"]

        async def evaluate(self, record, metrics, params):
            return [
                MR(record_id=record.id, metric_name="m1", category="generation",
                   score=1.0, threshold=None, metric_version="t"),
                MR(record_id=record.id, metric_name="m2", category="generation",
                   metric_version="t",
                   error={"code": "SYS_METRIC_ERROR", "message": "judge timeout"}),
            ]

    svc, repo = _make_service([_record_row("r1", "q", "a", "b")])
    _, summary = _create_and_execute(svc, repo, [_MixedEngine()], ["m1", "m2"])
    names = sorted(m["metric_name"] for m in repo.metric_results)
    assert names == ["m1", "m2"]  # both rows kept — partial result preserved
    ok = next(m for m in repo.metric_results if m["metric_name"] == "m1")
    bad = next(m for m in repo.metric_results if m["metric_name"] == "m2")
    assert ok["error"] is None and bad["error"] is not None
    assert repo.runs[summary["run_id"]]["status"] == "completed"  # record evaluated
    assert repo.eval_results[0]["is_failure"] is False


# ---------------- 12: dataset lock transaction safety ----------------


def test_12_lock_uses_row_level_for_update() -> None:
    """The lock read must compile to SELECT ... FOR UPDATE (real-DB race I-1b
    stays deferred to T-22 per project convention)."""
    import re

    from sqlalchemy import select as sa_select

    from app.models import Dataset

    stmt = sa_select(Dataset).where(Dataset.id == "x").with_for_update()
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert re.search(r"FOR UPDATE", sql, re.IGNORECASE)
    # repository method must use with_for_update on the dataset row
    import inspect

    src = inspect.getsource(EvaluationRepository.create_run_with_lock)
    assert "with_for_update" in src


def test_12b_locked_dataset_rejects_run_and_persists_nothing() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "a", "b")], locked=True)
    with pytest.raises(DatasetLockedError):
        svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1",
                       reproducibility_meta={})
    assert repo.runs == {}  # nothing persisted


def test_12c_second_run_on_same_dataset_blocked() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "a", "b")])
    svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1", reproducibility_meta={})
    with pytest.raises(DatasetLockedError):
        svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1", reproducibility_meta={})
    assert len(repo.runs) == 1


# ---------------- §1 invariant: error must never leak from judged outcomes ----------------


def test_error_never_written_for_ambiguous_or_mismatch() -> None:
    """重点验证：ambiguous / value_mismatch / unit_mismatch / temporal_mismatch
    是正常判定结果 —— 落库行 error 必须为 null（T-10 引擎内存里挂的 reason 不落库）。"""
    records = [
        _record_row("r1", "q", "去年", "今年"),                 # ambiguous
        _record_row("r2", "q", "2亿元", "1亿元"),               # value_mismatch
        _record_row("r3", "q", "增长50%", "增长50元"),           # unit_mismatch
        _record_row("r4", "q", "2023年", "2024年"),             # temporal_mismatch
    ]
    svc, repo = _make_service(records)
    _create_and_execute(svc, repo, [IntegrityEngine()], INTEGRITY)
    judged = [m for m in repo.metric_results
              if m["comparison_basis"] is not None]
    # 4 records x 3 integrity metrics = 12 rows, ALL of them judged outcomes
    # (empty sides are vacuous matches with a basis; no evaluator errors here)
    assert len(judged) == 12
    for m in judged:
        assert m["error"] is None, f"{m['metric_name']}: {m['comparison_basis']['comparison_type']}"
    types = {m["comparison_basis"]["comparison_type"] for m in judged}
    assert {"ambiguous", "value_mismatch", "unit_mismatch", "temporal_mismatch"} <= types


# ---------------- run state machine ----------------


def test_state_machine_invalid_transitions_rejected() -> None:
    # terminal states have no outgoing transitions
    assert _RUN_TRANSITIONS["completed"] == set()
    svc, repo = _make_service([_record_row("r1", "q", "2024年度", "2024年")])
    run_id, _ = _create_and_execute(svc, repo, [IntegrityEngine()], ["temporal_consistency"])
    with pytest.raises(BizError):
        asyncio.run(svc.execute_run(run_id, engines=[IntegrityEngine()],
                                    enabled_metrics=["temporal_consistency"],
                                    params=EvalParams()))  # completed -> running forbidden
    with pytest.raises(BizError):
        svc.cancel_run(run_id)  # completed -> cancelled forbidden


def test_run_counters_and_reproducibility_snapshot() -> None:
    svc, repo = _make_service([
        _record_row("r1", "q", "2024年度", "2024年"),
        _record_row("r2", "q", "1亿元", None),  # metric input error -> error record
    ])
    run_id, summary = _create_and_execute(svc, repo, [IntegrityEngine()], ["numerical_consistency"])
    run = repo.runs[run_id]
    assert run["total_records"] == 2
    assert run["evaluated_records"] == 1 and run["error_records"] == 1
    assert summary["coverage"] == 0.5
    assert run["reproducibility_meta"] == {"config_version": "x", "metric_version": "y"}


# ---- T-14C: aggregation query surface on the shared fake ----

def _fake_list_metric_rows_for_run(self, run_id: str) -> list[dict]:
    """Join metric_results with evaluation_results (read-only, like SQL)."""
    by_result = {r["id"]: r for r in self.eval_results if r["run_id"] == run_id}
    rows = []
    for m in self.metric_results:
        res = by_result.get(m["result_id"])
        if res is None:
            continue
        row = dict(m)
        row["record_id"] = res["record_id"]
        row["question"] = res["question"]
        row["is_failure"] = res["is_failure"]
        rows.append(row)
    return rows


def _fake_list_results_for_run(self, run_id: str) -> list:
    from types import SimpleNamespace

    return [SimpleNamespace(**r) for r in sorted(
        (r for r in self.eval_results if r["run_id"] == run_id),
        key=lambda r: (r["row_index"] or 0),
    )]


def _fake_list_diagnoses_for_run(self, run_id: str) -> list:
    from types import SimpleNamespace

    return [SimpleNamespace(**d) for d in self.diagnoses if d["run_id"] == run_id]


FakeEvaluationRepo.list_metric_rows_for_run = _fake_list_metric_rows_for_run
FakeEvaluationRepo.list_results_for_run = _fake_list_results_for_run
FakeEvaluationRepo.list_diagnoses_for_run = _fake_list_diagnoses_for_run


def test_pending_run_can_be_cancelled_and_cannot_be_executed() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2024年度", "2024年")])
    run = svc.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1", reproducibility_meta={}
    )

    cancelled = svc.cancel_run(run.id)

    assert cancelled.status == "cancelled"
    assert repo.runs[run.id]["status"] == "cancelled"
    with pytest.raises(BizError):
        asyncio.run(
            svc.execute_run(
                run.id,
                engines=[IntegrityEngine()],
                enabled_metrics=["temporal_consistency"],
                params=EvalParams(),
            )
        )
    assert repo.eval_results == []
    assert repo.metric_results == []


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_terminal_run_cannot_be_cancelled(terminal: str) -> None:
    svc, repo = _make_service([_record_row("r1", "q", "a", "b")])
    run = svc.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1", reproducibility_meta={}
    )
    repo.runs[run.id]["status"] = terminal

    with pytest.raises(RunNotCancellableError) as exc_info:
        svc.cancel_run(run.id)

    assert getattr(exc_info.value, "code", None) == "BIZ_RUN_NOT_CANCELLABLE"
    assert repo.runs[run.id]["status"] == terminal


def test_cancel_wins_completion_race() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2024年度", "2024年")])
    run = svc.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1", reproducibility_meta={}
    )
    repo.runs[run.id]["status"] = "running"

    svc.cancel_run(run.id)
    finished = repo.finish_run_if_running(
        run.id,
        status="completed",
        overall_score=1.0,
        evaluated_records=1,
        error_records=0,
        evaluation_coverage=1.0,
        finished_at=datetime.now(UTC),
    )

    assert finished is None
    assert repo.runs[run.id]["status"] == "cancelled"


def test_completion_wins_cancel_race_with_terminal_state() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2024年度", "2024年")])
    run = svc.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1", reproducibility_meta={}
    )
    repo.runs[run.id]["status"] = "running"

    finished = repo.finish_run_if_running(
        run.id,
        status="completed",
        overall_score=1.0,
        evaluated_records=1,
        error_records=0,
        evaluation_coverage=1.0,
        finished_at=datetime.now(UTC),
    )

    assert finished is not None
    with pytest.raises(RunNotCancellableError) as exc_info:
        svc.cancel_run(run.id)
    assert getattr(exc_info.value, "code", None) == "BIZ_RUN_NOT_CANCELLABLE"
    assert repo.runs[run.id]["status"] == "completed"
