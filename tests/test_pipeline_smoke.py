from __future__ import annotations

from app.domain.schemas import EvaluationRecord, MetricResult
from app.engines.base import EvalParams
from app.runner.local import LocalAsyncRunner


class _GoodEngine:
    name = "good"

    def metric_names(self) -> list[str]:
        return ["m1"]

    async def evaluate(self, record, metrics, params):
        return [
            MetricResult(
                record_id=record.id,
                metric_name="m1",
                category="retrieval",
                metric_version="t",
            )
        ]


class _SelectiveFailEngine:
    name = "selective"

    def metric_names(self) -> list[str]:
        return ["m1"]

    async def evaluate(self, record, metrics, params):
        if record.id == "bad":
            raise RuntimeError("boom")
        return [
            MetricResult(
                record_id=record.id,
                metric_name="m1",
                category="retrieval",
                metric_version="t",
            )
        ]


class _FailEngine:
    name = "fail"

    def metric_names(self) -> list[str]:
        return ["m1"]

    async def evaluate(self, record, metrics, params):
        raise RuntimeError("always")


def _rec(i: str) -> EvaluationRecord:
    return EvaluationRecord(id=i, question="q", contexts=["c"], answer="a")


async def test_all_ok_completed() -> None:
    runner = LocalAsyncRunner(concurrency=2)
    s = await runner.run([_rec(f"r{i}") for i in range(3)], [_GoodEngine()], ["m1"], EvalParams())
    assert s.status == "completed"
    assert s.evaluated == 3 and s.errors == 0
    assert s.coverage == 1.0


async def test_partial_error_isolation() -> None:
    runner = LocalAsyncRunner(concurrency=2)
    s = await runner.run(
        [_rec("r1"), _rec("bad"), _rec("r2")], [_SelectiveFailEngine()], ["m1"], EvalParams()
    )
    assert s.status == "completed_with_errors"
    assert s.total == 3 and s.evaluated == 2 and s.errors == 1
    assert abs(s.coverage - 2 / 3) < 1e-6


async def test_all_errors_failed() -> None:
    runner = LocalAsyncRunner(concurrency=2)
    s = await runner.run([_rec("a")], [_FailEngine()], ["m1"], EvalParams())
    assert s.status == "failed"
    assert s.evaluated == 0 and s.errors == 1
