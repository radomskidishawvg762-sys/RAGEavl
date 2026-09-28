"""T-08 RagasEngine tests.

Covers: 4-metric normal path, missing-input errors, judge-failure isolation,
metric_version recording, registry access, registry-driven pipeline execution,
and the static no-engine-name-branching check. All scoring goes through the
MetricScorer test seam — no network, no real judge (S-1 pending).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.errors import ExtJudgeUnavailableError
from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.judge import JudgeConfig, PlaceholderJudge, build_judge
from app.engines.ragas import (
    METRIC_VERSION,
    RAGAS_METRIC_DEFS,
    RagasEngine,
    build_sample,
)
from app.engines.ragas_bridge import RagasEmbeddingsBridge, RagasLLMBridge
from app.metrics.registry import MetricRegistry
from app.runner.local import LocalAsyncRunner

SCORES = {
    "faithfulness": 0.9,
    "answer_relevancy": 0.5,
    "context_precision": 0.8,
    "context_recall": 0.7,
}


class FakeScorer:
    async def score(self, metric_name: str, sample) -> float:
        return SCORES[metric_name]


class FailingScorer:
    """Simulates judge/ragas outage for specific records only."""

    def __init__(self, fail_record_ids: set[str]) -> None:
        self.fail_ids = fail_record_ids
        self.calls = 0

    async def score(self, metric_name: str, sample) -> float:
        self.calls += 1
        if getattr(sample, "user_input", None) in self.fail_ids:
            raise RuntimeError("judge upstream 500")
        return SCORES[metric_name]


def _record(record_id: str = "r1", **overrides) -> EvaluationRecord:
    fields = dict(
        id=record_id,
        question="公司2024年营收是多少？",
        contexts=["2024年营收为100亿元。"],
        answer="2024年营收为100亿元。",
        reference_answer="2024年营收100亿元。",
        reference_contexts=["2024年营收100亿元（来源：年报）。"],
        metadata={"domain": "financial"},
    )
    fields.update(overrides)
    return EvaluationRecord(**fields)


ALL_METRICS = [d.name for d in RAGAS_METRIC_DEFS]


# --- 1. normal record -> 4 MetricResults ---

async def test_normal_record_produces_four_results() -> None:
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=FakeScorer())
    results = await engine.evaluate(_record(), ALL_METRICS, EvalParams())
    assert [r.metric_name for r in results] == ALL_METRICS
    assert all(r.score is not None and r.error is None for r in results)
    cats = {r.metric_name: r.category for r in results}
    assert cats == {
        "faithfulness": "generation",
        "answer_relevancy": "generation",
        "context_precision": "retrieval",
        "context_recall": "retrieval",
    }


# --- 2. missing required input -> explicit error result ---

async def test_missing_required_input_returns_clear_error() -> None:
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=FakeScorer())
    results = await engine.evaluate(
        _record(reference_answer=None), ["context_recall", "faithfulness"], EvalParams()
    )
    by_name = {r.metric_name: r for r in results}
    err = by_name["context_recall"]
    assert err.score is None and err.passed is None
    assert err.error is not None
    assert err.error["code"] == "BIZ_METRIC_INPUT_MISSING"
    assert err.error["missing"] == ["reference_answer"]
    # faithfulness unaffected on the same record
    assert by_name["faithfulness"].score == SCORES["faithfulness"]


async def test_empty_contexts_count_as_missing() -> None:
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=FakeScorer())
    results = await engine.evaluate(
        _record(contexts=[]), ["context_precision"], EvalParams()
    )
    assert results[0].error is not None
    assert "contexts" in results[0].error["missing"]


# --- 3. judge/scorer failure -> record fails, others unaffected ---

async def test_scorer_failure_isolated_per_record() -> None:
    engine = RagasEngine(
        judge=PlaceholderJudge(JudgeConfig()),
        scorer=FailingScorer(fail_record_ids={"公司2024年营收是多少？"}),
    )
    bad = await engine.evaluate(_record(), ["faithfulness"], EvalParams())
    assert bad[0].score is None
    assert bad[0].error is not None
    assert bad[0].error["code"] == "SYS_METRIC_ERROR"

    good = await engine.evaluate(
        _record(question="另一个问题", answer="另一答案"), ["faithfulness"], EvalParams()
    )
    assert good[0].score == SCORES["faithfulness"]

    # batch level: engine soft-fails per metric (structured error result, no
    # exception) so the runner sees the record as evaluated; error_records
    # classification from persisted metric_results.error is T-12's job (Spec 7.1)
    runner = LocalAsyncRunner(concurrency=2)
    summary = await runner.run(
        [_record("r1"), _record("r2", question="正常问题"), _record("r3", question="第三个问题")],
        [engine],
        ["faithfulness"],
        EvalParams(),
    )
    assert summary.status == "completed"
    assert summary.evaluated == 3 and summary.errors == 0


async def test_judge_unavailable_maps_to_ext_code() -> None:
    class UnavailableScorer:
        async def score(self, metric_name: str, sample) -> float:
            raise ExtJudgeUnavailableError("judge provider/model not configured")

    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=UnavailableScorer())
    results = await engine.evaluate(_record(), ["faithfulness"], EvalParams())
    assert results[0].error is not None
    assert results[0].error["code"] == "EXT_JUDGE_UNAVAILABLE"


async def test_wrapped_judge_unavailable_recovered_via_cause_chain() -> None:
    """ragas/langchain wrap upstream errors; __cause__ chain must still yield
    EXT_JUDGE_UNAVAILABLE, not the SYS_METRIC_ERROR fallback."""

    class RagasStyleWrappedScorer:
        async def score(self, metric_name: str, sample) -> float:
            try:
                raise ExtJudgeUnavailableError("judge provider/model not configured")
            except ExtJudgeUnavailableError as inner:
                raise RuntimeError("metric execution failed") from inner  # ragas-style wrap

    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=RagasStyleWrappedScorer())
    results = await engine.evaluate(_record(), ["faithfulness"], EvalParams())
    assert results[0].error is not None
    assert results[0].error["code"] == "EXT_JUDGE_UNAVAILABLE"


# --- 4. metric_version recorded ---

async def test_metric_version_recorded_on_every_result() -> None:
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=FakeScorer())
    results = await engine.evaluate(_record(), ALL_METRICS, EvalParams())
    assert results, "no results"
    assert all(r.metric_version == METRIC_VERSION for r in results)
    assert METRIC_VERSION.startswith("ragas-")
    specs = engine.specs()
    assert all(s.version == METRIC_VERSION for s in specs)


# --- 5. registry access ---

def test_registry_can_resolve_ragas_metrics() -> None:
    registry = MetricRegistry()
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=FakeScorer())
    engine.register_into(registry)
    for name in ALL_METRICS:
        entry = registry.get_metric(name)
        assert entry.spec.engine == "ragas"
        assert entry.spec.input_requirements  # T-07 missing_field check consumes these
    assert sorted(registry.list_metrics(category="retrieval"), key=lambda s: s.name) == [
        registry.get_metric("context_precision").spec,
        registry.get_metric("context_recall").spec,
    ]


# --- 6. pipeline runs engine via registry, unconditionally ---

async def test_pipeline_runs_engine_through_registry() -> None:
    registry = MetricRegistry()
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=FakeScorer())
    engine.register_into(registry)
    enabled = [s.name for s in registry.list_metrics()]
    from app.engines import pipeline

    results = await pipeline.run_record([engine], _record(), enabled, EvalParams(threshold=0.8))
    by_name = {r.metric_name: r for r in results}
    assert set(by_name) == set(ALL_METRICS)
    assert by_name["faithfulness"].passed is True  # 0.9 >= 0.8
    assert by_name["answer_relevancy"].passed is False  # 0.5 < 0.8
    assert by_name["context_precision"].passed is True  # 0.8 >= 0.8
    assert by_name["context_recall"].passed is False  # 0.7 < 0.8


async def test_threshold_none_means_no_judgment() -> None:
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig()), scorer=FakeScorer())
    results = await engine.evaluate(_record(), ["faithfulness"], EvalParams(threshold=None))
    assert results[0].threshold is None and results[0].passed is None


# --- 7. no engine-name branching in business code (static check) ---

def test_no_engine_name_branching_anywhere() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    pattern = re.compile(r"(if\s+.*engine\s*==|==\s*[\"']ragas[\"']|[\"']ragas[\"']\s*==)")
    for path in app_dir.rglob("*.py"):
        if path.name == "ragas.py":  # the engine module itself may name itself
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == []


# --- judge config/params interface (FR-13 hooks) ---

def test_judge_describe_exposes_model_and_params() -> None:
    cfg = JudgeConfig(
        provider="acme", model="m-1", model_version="v2", temperature=0,
        max_tokens=512, timeout=30, retry=3,
    )
    judge = build_judge(cfg)
    desc = judge.describe()
    assert set(desc) >= {
        "provider", "model", "model_version", "temperature", "max_tokens", "timeout", "retry",
    }


def test_unconfigured_provider_builds_placeholder() -> None:
    # explicit empty config — deterministic regardless of ambient .env.local
    judge = build_judge(JudgeConfig(provider="", model="", api_key=None))
    assert isinstance(judge, PlaceholderJudge)


def test_build_sample_maps_evaluation_record() -> None:
    sample = build_sample(_record())
    assert sample.user_input == "公司2024年营收是多少？"
    assert sample.response == "2024年营收为100亿元。"
    assert sample.retrieved_contexts == ["2024年营收为100亿元。"]
    assert sample.reference == "2024年营收100亿元。"
    assert sample.reference_contexts == ["2024年营收100亿元（来源：年报）。"]


def test_ragas_scorer_constructs_with_placeholder_judge() -> None:
    """Real ragas wiring (no LLM call): metrics configure, bridges subclass ok."""
    from app.engines.ragas import RagasScorer

    judge = RagasLLMBridge(PlaceholderJudge(JudgeConfig()))
    assert judge._llm_type == "rageval-judge"
    emb = RagasEmbeddingsBridge(RagasEmbeddingsBridge.default_from_judge(PlaceholderJudge(JudgeConfig())))
    scorer = RagasScorer(PlaceholderJudge(JudgeConfig()), embeddings=emb)
    assert set(scorer._metrics) == set(ALL_METRICS)


@pytest.mark.parametrize("name", ALL_METRICS)
def test_input_requirements_mirror_ragas(name: str) -> None:
    """input_requirements must stay in sync with ragas required_columns."""
    import ragas.metrics as rm

    d = next(d for d in RAGAS_METRIC_DEFS if d.name == name)
    field_map = {
        "user_input": "question",
        "response": "answer",
        "retrieved_contexts": "contexts",
        "reference": "reference_answer",
    }
    metric = getattr(rm, name)  # module uses lazy __getattr__ — not in __dict__
    expected = sorted(field_map[c] for c in metric.required_columns["SINGLE_TURN"])
    assert sorted(d.input_requirements) == expected


def test_scorer_does_not_mutate_the_ragas_module_singletons() -> None:
    """Regression: .llm/.embeddings were assigned onto the module-level ragas
    metric singletons. Those are process-global, so constructing a second scorer
    silently rewrote the first run's judge — while each run's snapshot still
    claimed its own configured judge."""
    import ragas.metrics as rm

    from app.engines.ragas import RagasScorer

    emb = RagasEmbeddingsBridge(
        RagasEmbeddingsBridge.default_from_judge(PlaceholderJudge(JudgeConfig()))
    )
    first = RagasScorer(PlaceholderJudge(JudgeConfig()), embeddings=emb)
    second = RagasScorer(PlaceholderJudge(JudgeConfig()), embeddings=emb)

    for name in ALL_METRICS:
        # each scorer owns its own metric instance...
        assert first._metrics[name] is not second._metrics[name]
        # ...and the process-global singleton was never configured
        singleton = getattr(rm, name)
        assert singleton.llm is None
        if hasattr(singleton, "embeddings"):  # only some metrics take embeddings
            assert singleton.embeddings is None
