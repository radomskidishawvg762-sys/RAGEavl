"""Engine factory (T-13/T-14A) — builds the engine list for a run from enabled
metric names via the MetricRegistry.

Dispatch is a builder MAP keyed by engine name (ADR-03: no engine-name
conditionals in business code; construction-time selection is a dict lookup).

T-14A §五: a metric is NEVER silently dropped. RagasEngine is always
constructible (its scorer is lazy); with no judge configured its per-metric
results carry error code BIZ_JUDGE_NOT_CONFIGURED (score=null) so "did not
run" stays distinguishable from "ran and scored low". Only metrics unknown to
the registry are reported skipped.
"""

from __future__ import annotations

from app.engines.base import EvaluationEngine
from app.engines.integrity import IntegrityEngine
from app.metrics.registry import default_registry


def build_engines(
    enabled_metrics: list[str],
    *,
    alias_table: dict | None = None,
    judge_config: dict | None = None,
) -> tuple[list[EvaluationEngine], list[str]]:
    """Returns (engines, skipped_metric_names). judge_config is the PUBLIC
    JudgeConfig dump (no api_key) — the builder re-attaches the key from env."""
    by_engine: dict[str, list[str]] = {}
    unknown: list[str] = []
    for name in enabled_metrics:
        try:
            spec = default_registry.get_metric(name).spec
        except KeyError:
            unknown.append(name)
            continue
        by_engine.setdefault(spec.engine, []).append(name)

    engines: list[EvaluationEngine] = []
    skipped: list[str] = list(unknown)
    for engine_name, metric_names in by_engine.items():
        builder = _ENGINE_BUILDERS.get(engine_name)
        if builder is None:
            skipped.extend(metric_names)
            continue
        engine = builder(alias_table=alias_table, judge_config=judge_config)
        engines.append(engine)
    return engines, skipped


def _build_integrity(*, alias_table: dict | None, judge_config: dict | None) -> EvaluationEngine:
    return IntegrityEngine(alias_table=alias_table)


def _build_ragas(*, alias_table: dict | None, judge_config: dict | None) -> EvaluationEngine:
    """Always constructible. Judge config: public fields from the profile +
    api_key re-attached from Settings (env -> SecretStr -> adapter). An
    unconfigured judge surfaces as BIZ_JUDGE_NOT_CONFIGURED per metric at
    evaluation time — never a silent skip."""
    from app.engines.judge import build_judge
    from app.engines.ragas import RagasEngine
    from app.services.judge_settings_service import default_judge_settings_service

    runtime = default_judge_settings_service.config()
    cfg = runtime.model_copy(update=judge_config or {})
    return RagasEngine(build_judge(cfg))


_ENGINE_BUILDERS: dict[str, callable] = {
    "integrity": _build_integrity,
    "ragas": _build_ragas,
}
