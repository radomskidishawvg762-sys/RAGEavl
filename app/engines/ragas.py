"""RagasEngine (T-08 / FR-15) — the 4 P0 general metrics via RAGAS.

Boundary rules honored here:
  - Metric ≠ Diagnosis: this engine ONLY produces MetricResult; no Diagnosis,
    no Recommendation, no Evidence (P-1).
  - No Repository/ORM access; no business-layer engine-name branching (ADR-03).
  - All RAGAS/SDK imports are lazy — the app boots without the `eval` extra.
  - RAGAS original exceptions are converted into structured MetricResult.error;
    a single metric/record failure never propagates (FR-22 error isolation).

RAGAS version is locked in pyproject (eval extra) and recorded in every
MetricResult.metric_version as "ragas-<version>".
"""

from __future__ import annotations

from importlib.metadata import version as _pkg_version
from importlib.util import find_spec
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.errors import ExtJudgeUnavailableError, JudgeNotConfiguredError
from app.domain.schemas import EvaluationRecord, MetricResult
from app.engines.base import EvalParams
from app.engines.judge import EmbeddingsClient, JudgeClient
from app.metrics.base import MetricSpec
from app.metrics.registry import MetricRegistry

RAGAS_VERSION: str | None = None
try:  # importlib.metadata does not import the package — safe when ragas absent
    RAGAS_VERSION = _pkg_version("ragas")
except Exception:  # pragma: no cover - eval extra not installed
    RAGAS_VERSION = None

METRIC_VERSION = f"ragas-{RAGAS_VERSION}" if RAGAS_VERSION else "ragas-unavailable"


def ragas_available() -> bool:
    return find_spec("ragas") is not None


class RagasMetricDef(BaseModel):
    name: str
    category: str  # retrieval | generation
    input_requirements: list[str]  # EvaluationRecord field names (Spec A.2)


# input_requirements mirror ragas required_columns (verified on ragas 0.4.3):
#   faithfulness        {user_input, response, retrieved_contexts}
#   answer_relevancy    {user_input, response}        (+ embeddings at runtime)
#   context_precision   {user_input, retrieved_contexts, reference}
#   context_recall      {user_input, retrieved_contexts, reference}
RAGAS_METRIC_DEFS: list[RagasMetricDef] = [
    RagasMetricDef(
        name="faithfulness",
        category="generation",
        input_requirements=["question", "answer", "contexts"],
    ),
    RagasMetricDef(
        name="answer_relevancy",
        category="generation",
        input_requirements=["question", "answer"],
    ),
    RagasMetricDef(
        name="context_precision",
        category="retrieval",
        input_requirements=["question", "contexts", "reference_answer"],
    ),
    RagasMetricDef(
        name="context_recall",
        category="retrieval",
        input_requirements=["question", "contexts", "reference_answer"],
    ),
]


def build_sample(record: EvaluationRecord) -> Any:
    """Map EvaluationRecord -> ragas SingleTurnSample (lazy import)."""
    from ragas.dataset_schema import SingleTurnSample

    return SingleTurnSample(
        user_input=record.question,
        response=record.answer,
        retrieved_contexts=list(record.contexts) if record.contexts else None,
        reference=record.reference_answer,
        reference_contexts=list(record.reference_contexts)
        if record.reference_contexts
        else None,
    )


@runtime_checkable
class MetricScorer(Protocol):
    """Scoring seam: production = RagasScorer (real RAGAS); tests inject fakes."""

    async def score(self, metric_name: str, sample: Any) -> float: ...


class RagasScorer:
    """Real RAGAS call path. Metric objects are configured once with the
    JudgeClient bridge; single_turn_ascore is per-record (async)."""

    def __init__(self, judge: JudgeClient, embeddings: EmbeddingsClient | None = None) -> None:
        if not ragas_available():
            raise ExtJudgeUnavailableError(
                "ragas is not installed (pip install -e '.[eval]')"
            )
        from ragas import metrics as ragas_metrics
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper

        from app.engines.ragas_bridge import RagasEmbeddingsBridge, RagasLLMBridge

        self._judge = judge
        self._metrics: dict[str, Any] = {}
        llm_wrapper = LangchainLLMWrapper(RagasLLMBridge(judge))
        emb_client = embeddings  # None -> bridge builds placeholder from judge config
        emb_wrapper = None
        for name, ragas_name in _RAGAS_NAME_MAP.items():
            metric = getattr(ragas_metrics, ragas_name)
            metric.llm = llm_wrapper
            if hasattr(metric, "embeddings"):
                if emb_wrapper is None:
                    client = emb_client or RagasEmbeddingsBridge.default_from_judge(judge)
                    emb_wrapper = LangchainEmbeddingsWrapper(
                        RagasEmbeddingsBridge(client)
                    )
                metric.embeddings = emb_wrapper
            self._metrics[name] = metric
        self._timeout: float | None = None
        desc = judge.describe()
        if desc.get("timeout"):
            self._timeout = float(desc["timeout"])

    async def score(self, metric_name: str, sample: Any) -> float:
        metric = self._metrics[metric_name]
        value = await metric.single_turn_ascore(sample, timeout=self._timeout)
        return float(value)


_RAGAS_NAME_MAP = {
    "faithfulness": "faithfulness",
    "answer_relevancy": "answer_relevancy",
    "context_precision": "context_precision",
    "context_recall": "context_recall",
}


def _judge_error_code_in_chain(exc: BaseException, max_depth: int = 10) -> str | None:
    """ragas/langchain wrap upstream judge errors in their own exception types;
    walk __cause__/__context__ to recover the original error code. Returns the
    error code or None when no judge error is buried in the chain (T-14A §六:
    not_configured / unavailable / internal stay distinguishable)."""
    current: BaseException | None = exc
    depth = 0
    while current is not None and depth < max_depth:
        if isinstance(current, JudgeNotConfiguredError):
            return "BIZ_JUDGE_NOT_CONFIGURED"
        if isinstance(current, ExtJudgeUnavailableError):
            return "EXT_JUDGE_UNAVAILABLE"
        current = current.__cause__ or current.__context__
        depth += 1
    return None


class RagasEngine:
    """EvaluationEngine implementation (Protocol-compatible with M1)."""

    name = "ragas"

    def __init__(self, judge: JudgeClient, scorer: MetricScorer | None = None) -> None:
        self._judge = judge
        self._scorer: MetricScorer | None = scorer

    def metric_names(self) -> list[str]:
        return [d.name for d in RAGAS_METRIC_DEFS]

    def specs(self) -> list[MetricSpec]:
        return [
            MetricSpec(
                name=d.name,
                category=d.category,  # type: ignore[arg-type]
                engine=self.name,
                description=f"RAGAS {d.name} (P0 general metric)",
                input_requirements=d.input_requirements,
                version=METRIC_VERSION,
                direction="higher_is_better",
            )
            for d in RAGAS_METRIC_DEFS
        ]

    def _scorer_or_build(self) -> MetricScorer:
        if self._scorer is None:
            self._scorer = RagasScorer(self._judge)
        return self._scorer

    @staticmethod
    def _missing_inputs(record: EvaluationRecord, required: list[str]) -> list[str]:
        missing = []
        for field in required:
            value = getattr(record, field, None)
            if value is None or (isinstance(value, (str, list)) and len(value) == 0):
                missing.append(field)
        return missing

    @staticmethod
    def _threshold_for(metric_name: str, params: EvalParams) -> float | None:
        per_metric = params.extra.get(metric_name) if params.extra else None
        if isinstance(per_metric, dict) and "threshold" in per_metric:
            return per_metric["threshold"]
        return params.threshold

    async def evaluate(
        self,
        record: EvaluationRecord,
        metrics: list[str],
        params: EvalParams,
    ) -> list[MetricResult]:
        owned = {d.name: d for d in RAGAS_METRIC_DEFS}
        # T-14A §五/§六: an unconfigured judge must NOT enter RAGAS internals —
        # ragas swallows the immediate error and stalls until its own timeout.
        # Pre-check describe() (JudgeClient protocol field `configured`) and
        # emit explicit BIZ_JUDGE_NOT_CONFIGURED per metric instead. When a
        # scorer is EXPLICITLY injected (test seam / alternate backend) the
        # judge never participates, so the check only guards the real path.
        judge_ready = bool(self._judge.describe().get("configured", True)) or self._scorer is not None
        results: list[MetricResult] = []
        for metric_name in metrics:
            d = owned.get(metric_name)
            if d is None:
                continue  # not ours; pipeline asks engines only for enabled names
            if not judge_ready:
                results.append(
                    self._error_result(
                        record.id, metric_name, d.category,
                        code="BIZ_JUDGE_NOT_CONFIGURED",
                        message="judge provider/model not configured",
                        reason="judge_not_configured",
                    )
                )
                continue
            missing = self._missing_inputs(record, d.input_requirements)
            if missing:
                results.append(
                    self._error_result(
                        record.id,
                        metric_name,
                        d.category,
                        code="BIZ_METRIC_INPUT_MISSING",
                        message="record lacks fields required by this metric",
                        missing=missing,
                    )
                )
                continue
            threshold = self._threshold_for(metric_name, params)
            try:
                score = await self._scorer_or_build().score(metric_name, build_sample(record))
                results.append(
                    MetricResult(
                        record_id=record.id,
                        metric_name=metric_name,
                        category=d.category,  # type: ignore[arg-type]
                        score=score,
                        threshold=threshold,
                        passed=(score >= threshold) if threshold is not None else None,
                        metric_version=METRIC_VERSION,
                    )
                )
            except JudgeNotConfiguredError as e:
                # configuration absence is NOT a runtime failure: score=null +
                # BIZ_JUDGE_NOT_CONFIGURED so Machine Report can distinguish
                # "did not run" from "ran and scored low" (T-14A §五/§六)
                results.append(
                    self._error_result(
                        record.id, metric_name, d.category, code="BIZ_JUDGE_NOT_CONFIGURED",
                        message=str(e), reason="judge_not_configured",
                    )
                )
            except ExtJudgeUnavailableError as e:
                results.append(
                    self._error_result(
                        record.id, metric_name, d.category, code="EXT_JUDGE_UNAVAILABLE",
                        message=str(e),
                    )
                )
            except Exception as e:  # noqa: BLE001 — RAGAS original error -> structured error
                code = _judge_error_code_in_chain(e) or "SYS_METRIC_ERROR"
                results.append(
                    self._error_result(
                        record.id,
                        metric_name,
                        d.category,
                        code=code,
                        message=f"{type(e).__name__}: {e}"[:500],
                        reason="judge_not_configured" if code == "BIZ_JUDGE_NOT_CONFIGURED" else None,
                    )
                )
        return results

    @staticmethod
    def _error_result(
        record_id: str,
        metric_name: str,
        category: str,
        *,
        code: str,
        message: str,
        missing: list[str] | None = None,
        reason: str | None = None,
    ) -> MetricResult:
        error: dict[str, Any] = {"code": code, "message": message}
        if reason:
            error["reason"] = reason
        if missing:
            error["missing"] = missing
        return MetricResult(
            record_id=record_id,
            metric_name=metric_name,
            category=category,  # type: ignore[arg-type]
            score=None,
            threshold=None,
            passed=None,
            metric_version=METRIC_VERSION,
            error=error,
        )

    def register_into(self, registry: MetricRegistry) -> None:
        """Registry-driven access (FR-14). Business layer resolves metrics from
        the registry; nothing branches on this engine's name."""
        for spec in self.specs():
            registry.register(spec, self._make_evaluator(spec.name))

    def _make_evaluator(self, metric_name: str):
        async def evaluator(record: EvaluationRecord, params: dict) -> MetricResult:
            eval_params = params if isinstance(params, EvalParams) else EvalParams.model_validate(params)
            results = await self.evaluate(record, [metric_name], eval_params)
            return results[0]

        return evaluator
