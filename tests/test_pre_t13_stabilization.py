"""Pre-T13 Stabilization tests — C1 (Runner integration), C2 (is_failure DDL),
C3 (severity_mapping wiring). No REST API work this round.
"""

from __future__ import annotations

import asyncio

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.domain.schemas import EvaluationRecord, MetricResult
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.models import EvaluationResult
from app.runner.base import RunSummary
from app.runner.local import LocalAsyncRunner
from app.services.config_service import ConfigService
from tests.test_evaluation_service import _make_service, _record_row

INTEGRITY = ["entity_consistency", "temporal_consistency", "numerical_consistency"]


def _run(service, repo, engines, metrics, params=None, **kw):
    run = service.create_run(
        project_id="p1", dataset_id="ds1", config_id="c1",
        reproducibility_meta={"config_version": "x", "metric_version": "y"},
    )
    return run.id, asyncio.run(service.execute_run(
        run.id, engines=engines, enabled_metrics=metrics,
        params=params or EvalParams(), **kw
    ))


# ============================== C1: Runner ==============================


class _SpyRunner:
    """Records that it was invoked; evaluates via the real pipeline and drives
    on_record_done per record (service-side logic runs unchanged)."""

    def __init__(self) -> None:
        self.called = False
        self.records_seen: list[str] = []

    async def run(self, records, engines, enabled_metrics, params,
                  on_progress=None, on_record_done=None, is_cancelled=None,
                  resolve_record=None):
        from app.engines.pipeline import run_record

        self.called = True
        for r in records:
            self.records_seen.append(r.id)
            record = r
            if resolve_record is not None:
                record = await resolve_record(r.id)  # faithful: runner resolves first
            if isinstance(record, Exception):
                cb = on_record_done(r.id, record)
                if asyncio.iscoroutine(cb):
                    await cb
                continue
            results = await run_record(engines, record, enabled_metrics, params)
            if on_record_done:
                cb = on_record_done(r.id, results)
                if asyncio.iscoroutine(cb):
                    await cb
        return RunSummary(total=len(records), evaluated=len(records), errors=0,
                          coverage=1.0, status="completed")


def test_c1_runner_is_actually_invoked_by_service() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2024年度", "2024年")])
    spy = _SpyRunner()
    run_id, summary = _run(svc, repo, [IntegrityEngine()], ["temporal_consistency"], runner=spy)
    assert spy.called and spy.records_seen == ["r1"]
    assert summary["status"] == "completed"
    assert len(repo.eval_results) == 1 and len(repo.metric_results) == 1
    assert repo.metric_results[0]["score"] == 1.0  # results flowed through the callback


def test_c1_results_delivered_per_record() -> None:
    """Real runner: every record id is delivered with its full results list."""
    delivered: dict[str, list[MetricResult]] = {}

    def on_record_done(record_id, payload):
        if not isinstance(payload, Exception):
            delivered[record_id] = payload

    runner = LocalAsyncRunner(concurrency=2)
    records = [
        EvaluationRecord(id="r1", question="q", contexts=[], answer="2024年度", reference_answer="2024年"),
        EvaluationRecord(id="r2", question="q", contexts=[], answer="2023年", reference_answer="2024年"),
    ]
    summary = asyncio.run(runner.run(
        records, [IntegrityEngine()], ["temporal_consistency"], EvalParams(),
        on_record_done=on_record_done,
    ))
    assert summary.status == "completed"
    assert set(delivered) == {"r1", "r2"}
    r1 = delivered["r1"][0]
    assert r1.metric_name == "temporal_consistency" and r1.score == 1.0


def test_c1_single_error_does_not_break_batch() -> None:
    class _Boom:
        name = "boom"

        def metric_names(self):
            return ["m1"]

        async def evaluate(self, record, metrics, params):
            if record.id == "bad":
                raise RuntimeError("judge timeout")
            return [MetricResult(record_id=record.id, metric_name="m1", category="generation",
                                 score=1.0, threshold=None, metric_version="t")]

    delivered: dict[str, object] = {}

    def on_record_done(record_id, payload):
        delivered[record_id] = payload

    runner = LocalAsyncRunner(concurrency=2)
    records = [
        EvaluationRecord(id="good", question="q", contexts=[], answer="a", reference_answer="b"),
        EvaluationRecord(id="bad", question="q", contexts=[], answer="a", reference_answer="b"),
    ]
    summary = asyncio.run(runner.run(records, [_Boom()], ["m1"], EvalParams(), on_record_done=on_record_done))
    assert summary.status == "completed_with_errors" and summary.errors == 1
    assert isinstance(delivered["bad"], Exception)
    assert not isinstance(delivered["good"], Exception)
    # service-level: good record persisted, bad counted as error
    svc, repo = _make_service([_record_row("good", "q", "a", "b"), _record_row("bad", "q", "a", "b")])
    _, s = _run(svc, repo, [_Boom()], ["m1"])
    assert s["status"] == "completed_with_errors"
    assert len(repo.eval_results) == 1 and repo.eval_results[0]["record_id"] == "good"


def test_c1_progress_callback_fires() -> None:
    calls: list[tuple[int, int, int]] = []

    def on_progress(evaluated, errors, total):
        calls.append((evaluated, errors, total))

    runner = LocalAsyncRunner(concurrency=2)
    records = [EvaluationRecord(id=f"r{i}", question="q", contexts=[], answer="2024年度", reference_answer="2024年")
               for i in range(3)]
    asyncio.run(runner.run(records, [IntegrityEngine()], ["temporal_consistency"],
                           EvalParams(), on_progress=on_progress))
    assert calls and calls[-1] == (3, 0, 3)
    assert all(e == 0 for _, e, _ in calls)  # no errors


def test_c1_cancellation_preserves_persisted_results() -> None:
    """concurrency=1 + is_cancelled flips after the first record: first record
    stays persisted, run ends cancelled, nothing partial is corrupted."""
    processed = {"n": 0}

    def is_cancelled() -> bool:
        return processed["n"] >= 1

    svc, repo = _make_service([
        _record_row("r1", "q", "2024年度", "2024年"),
        _record_row("r2", "q", "2023年", "2024年"),
    ])
    run_id, summary = _run(
        svc, repo, [IntegrityEngine()], ["temporal_consistency"],
        runner=LocalAsyncRunner(concurrency=1),
        is_cancelled=is_cancelled,
        on_progress=lambda e, err, t: processed.__setitem__("n", e + err),
    )
    assert summary["status"] == "cancelled"
    run = repo.runs[run_id]
    assert run["status"] == "cancelled" and run["finished_at"] is not None
    # only the first record's rows were persisted — and they are intact
    assert len(repo.eval_results) == 1
    assert repo.eval_results[0]["is_failure"] is False
    assert repo.metric_results[0]["score"] == 1.0


# ============================== C2: is_failure DDL ==============================


def test_c2_orm_column_not_null_with_default_false() -> None:
    col = EvaluationResult.__table__.c.is_failure
    assert col.nullable is False
    assert col.server_default is not None
    assert "false" in str(col.server_default.arg).lower()


def test_c2_migration_0003_is_head_and_minimal() -> None:
    from pathlib import Path

    cfg = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0003"]
    migration_path = Path(__file__).resolve().parents[1] / "alembic/versions/0003_eval_result_is_failure_not_null.py"
    src = migration_path.read_text(encoding="utf-8")
    assert 'revision: str = "0003"' in src and 'down_revision: Union[str, None] = "0002"' in src
    # only the one column is touched: one alter_column per direction, no table ops
    assert src.count("alter_column") == 2  # upgrade + downgrade
    assert "server_default=sa.false()" in src and "nullable=False" in src
    assert "create_table" not in src and "drop_table" not in src


def test_c2_model_matches_migration() -> None:
    """Model ↔ 0003 ↔ Remote-PostgreSQL consistency.
    ORM and migration agree on NOT NULL DEFAULT false; the remote-PG check
    runs on TEST_DATABASE_URL (deferred to T-22 per project convention)."""
    col = EvaluationResult.__table__.c.is_failure
    assert col.nullable is False
    assert str(col.server_default.arg).lower() == "false"


# ============================== C3: severity_mapping ==============================


def test_c3_profile_severity_mapping_overrides_default_through_service() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2亿元", "1亿元")])
    params = EvalParams(severity_mapping={"numerical_mismatch": "WARNING"})
    _run(svc, repo, [IntegrityEngine()], ["numerical_consistency"], params=params)
    d = repo.diagnoses[0]
    assert d["status"] == "diagnosed"
    assert d["severity"] == "WARNING"  # Profile over default hint (CRITICAL)


def test_c3_unmapped_uses_default_hint() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2亿元", "1亿元")])
    _run(svc, repo, [IntegrityEngine()], ["numerical_consistency"], params=EvalParams())
    assert repo.diagnoses[0]["severity"] == "CRITICAL"  # spec appendix B hint


def test_c3_full_code_key_supported() -> None:
    svc, repo = _make_service([_record_row("r1", "q", "2亿元", "1亿元")])
    params = EvalParams(severity_mapping={"integrity.numerical_mismatch": "INFO"})
    _run(svc, repo, [IntegrityEngine()], ["numerical_consistency"], params=params)
    assert repo.diagnoses[0]["severity"] == "INFO"


def test_c3_config_service_accessor_reads_profile() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "domains").mkdir()
        (base / "evaluations").mkdir()
        (base / "system.yaml").write_text("system:\n  x: 1\n", encoding="utf-8")
        (base / "domains/general.yaml").write_text("domain: general\n", encoding="utf-8")
        (base / "evaluations/fin.yaml").write_text(
            "severity_mapping:\n  numerical_mismatch: CRITICAL\n  unit_mismatch: WARNING\n",
            encoding="utf-8",
        )
        cs = ConfigService(base)
        assert cs.severity_mapping("general", "fin") == {
            "numerical_mismatch": "CRITICAL", "unit_mismatch": "WARNING",
        }
        assert cs.severity_mapping("general", "missing-profile") == {}


def test_c3_frozen_contracts_unchanged() -> None:
    """C1/C2/C3 must not alter frozen domain contracts."""
    from app.domain.schemas import ComparisonType, DiagnosisResult, Failure

    assert ComparisonType.__args__ == ("match", "value_mismatch", "unit_mismatch", "scale_mismatch",
                                       "missing_reference", "ambiguous", "entity_mismatch",
                                       "temporal_mismatch")
    assert DiagnosisResult.model_fields["status"].annotation.__args__ == (
        "diagnosed", "undetermined", "not_failed",
    )
    assert Failure.model_fields["source"].annotation.__args__ == ("threshold", "deterministic_mismatch")
