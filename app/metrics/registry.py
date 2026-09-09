from __future__ import annotations

from app.metrics.base import MetricEvaluator, MetricSpec, RegisteredMetric


class MetricNotFoundError(KeyError):
    pass


class MetricRegistry:
    """Code registration entry point (PRD Q8). NOT the metric_definitions table;
    that table only mirrors this registry for UI/Run snapshot."""

    def __init__(self) -> None:
        self._metrics: dict[str, RegisteredMetric] = {}

    def register(self, spec: MetricSpec, evaluator: MetricEvaluator) -> None:
        if spec.name in self._metrics:
            raise ValueError(f"metric already registered: {spec.name}")
        self._metrics[spec.name] = RegisteredMetric(spec=spec, evaluator=evaluator)

    def get_metric(self, name: str) -> RegisteredMetric:
        m = self._metrics.get(name)
        if m is None:
            raise MetricNotFoundError(name)
        return m

    def has(self, name: str) -> bool:
        return name in self._metrics

    def list_metrics(self, category: str | None = None) -> list[MetricSpec]:
        return [
            m.spec
            for m in self._metrics.values()
            if category is None or m.spec.category == category
        ]


def metric_direction(name: str) -> str:
    """MetricSpec.direction from the registry (additive default higher_is_better)."""
    try:
        return default_registry.get_metric(name).spec.direction
    except KeyError:
        return "higher_is_better"


default_registry = MetricRegistry()
