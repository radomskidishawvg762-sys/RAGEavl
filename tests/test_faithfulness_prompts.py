"""Offline wiring/contract tests. Scripted replies do not measure LLM accuracy."""
from __future__ import annotations

import json

import pytest

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.faithfulness_prompts import (
    CONTENT_SHA256,
    PROMPT_VERSION,
    REVISION,
    prompt_manifest,
)
from app.engines.ragas import RagasEngine, RagasScorer, metric_version_for
from app.services.run_planner import build_reproducibility_meta


class ScriptedJudge:
    def __init__(self, statement: str, verdict: int):
        self.statement = statement
        self.verdict = verdict
        self.calls: list[str] = []

    def describe(self):
        return {"configured": True, "timeout": 5}

    async def ainvoke(self, prompt):
        self.calls.append(prompt)
        if len(self.calls) == 1:
            assert "copy them without completing an omitted subject" in prompt
            return json.dumps({"statements": [self.statement]}, ensure_ascii=False)
        assert "using ONLY the retrieved context" in prompt
        # Drive the real RAGAS schema/bridge/aggregation path with a fixed verdict.
        return json.dumps({"statements": [{"statement": self.statement,
                           "reason": "scripted contract test", "verdict": self.verdict}]}, ensure_ascii=False)


@pytest.mark.parametrize("answer,context,verdict", [
    ("营收为1亿元", "营收为1亿元", 1),
    ("营收为10000万元", "营收为1亿元", 1),
    ("营收为10亿元", "营收为十亿元", 1),
    ("甲公司营收为1亿元", "乙公司营收为1亿元", 0),
    ("营收为1万元", "营收为1亿元", 0),
    ("利润为1亿元", "营收为1亿元", 0),
])
async def test_real_ragas_path_preserves_claim_and_aggregates_verdict(answer, context, verdict):
    judge = ScriptedJudge(answer, verdict)
    engine = RagasEngine(judge=judge)
    record = EvaluationRecord(id="contract", question="公司营收是多少？", answer=answer, contexts=[context])
    result, = await engine.evaluate(record, ["faithfulness"], EvalParams(threshold=0.9))
    assert result.error is None and result.score == verdict
    assert result.passed == bool(verdict)
    assert result.metric_version == metric_version_for("faithfulness")
    # Assert what reaches NLI, not merely the mock's final score.
    payload = judge.calls[1].rsplit("\ninput: ", 1)[1].split("\nOutput:", 1)[0]
    assert json.loads(payload) == {"context": context, "statements": [answer]}
    assert len(judge.calls) == 2


def test_prompts_are_private_and_do_not_change_other_metrics():
    from ragas.metrics._faithfulness import Faithfulness

    a, b = RagasScorer(ScriptedJudge("unused", 0)), RagasScorer(ScriptedJudge("unused", 0))
    first, second = a._metrics["faithfulness"], b._metrics["faithfulness"]
    assert first.statement_generator_prompt is not second.statement_generator_prompt
    first.statement_generator_prompt.examples.clear()
    assert second.statement_generator_prompt.examples
    assert "Extract every" not in Faithfulness().statement_generator_prompt.instruction
    assert metric_version_for("answer_relevancy") == "ragas-0.4.3"


def test_manifest_content_hash_is_frozen():
    assert REVISION == "faithfulness-claims-v1"
    assert CONTENT_SHA256 == "a36a58e048b6c3239b2ec4651882121fac5b9ab989598b815e8f0fbfa2e6e13f"
    manifest = prompt_manifest()
    manifest["statement"]["examples"].clear()
    assert prompt_manifest()["statement"]["examples"]


def test_run_snapshot_records_executed_prompt_not_unused_config_label():
    from app.metrics.bootstrap import bootstrap_metrics

    bootstrap_metrics()
    meta = build_reproducibility_meta(config_version="cfg", dataset_version="v1",
        merged={"system": {"judge": {"provider": "openai", "model": "fixed-model",
                "model_version": "revision-123", "prompt_version": "unused-label",
                "temperature": 0, "max_tokens": 1024, "timeout": 60, "retry": 1}}},
        enabled_metrics=["faithfulness"])
    assert meta["prompt_version"] == PROMPT_VERSION
    assert meta["metric_prompts"] == {"faithfulness": PROMPT_VERSION}
    assert meta["metric_version"] == metric_version_for("faithfulness")
    assert meta["judge_model_version"] == "revision-123"
    assert meta["judge_max_tokens"] == 1024 and meta["judge_temperature"] == 0
