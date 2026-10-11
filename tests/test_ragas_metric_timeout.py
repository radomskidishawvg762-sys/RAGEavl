"""The timeout passed to a whole RAGAS metric is not the single-request timeout.

judge.timeout stays the per-request HTTP budget. The metric budget is
system.judge.metric_timeout when set, otherwise
expected serial calls × request timeout × attempt count. JudgeConfig.retry
already counts the first request, which is (extra retries + 1)."""

from __future__ import annotations

import pytest

from app.domain.schemas import EvaluationRecord
from app.engines.judge import JudgeConfig, PlaceholderJudge
from app.engines.ragas import RagasScorer, build_sample


def _record(contexts: list[str] | None = None) -> EvaluationRecord:
    return EvaluationRecord(
        id="r1",
        question="q",
        contexts=contexts or ["c1"],
        answer="a",
        reference_answer="ref",
    )


async def _captured_timeout(judge: PlaceholderJudge, metric: str, record: EvaluationRecord) -> float:
    scorer = RagasScorer(judge)
    seen: dict[str, float | None] = {}

    async def _fake(_sample, callbacks=None, timeout=None):
        seen["timeout"] = timeout
        return 1.0

    scorer._metrics[metric].single_turn_ascore = _fake
    await scorer.score(metric, build_sample(record))
    assert "timeout" in seen
    return seen["timeout"]


@pytest.mark.parametrize(("metric", "contexts", "calls"), [
    ("faithfulness", ["c1"], 2),
    ("context_precision", ["c1", "c2", "c3", "c4"], 4),
    ("context_recall", ["c1", "c2"], 1),
    ("answer_relevancy", ["c1"], 3),
])
async def test_unset_metric_timeout_scales_with_calls_and_attempts(
    metric: str, contexts: list[str], calls: int,
) -> None:
    judge = PlaceholderJudge(JudgeConfig(timeout=30, retry=3))
    budget = await _captured_timeout(judge, metric, _record(contexts))
    assert judge.describe()["timeout"] == 30
    assert budget == calls * 30 * 3


async def test_configured_metric_timeout_overrides_the_estimate() -> None:
    judge = PlaceholderJudge(JudgeConfig(timeout=30, retry=3, metric_timeout=90))
    budget = await _captured_timeout(judge, "faithfulness", _record())
    assert judge.describe()["timeout"] == 30
    assert budget == 90
