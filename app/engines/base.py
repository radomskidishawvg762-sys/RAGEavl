from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.domain.schemas import EvaluationRecord, MetricResult


class EvalParams(BaseModel):
    threshold: float | None = None
    weight: float = 1.0
    judge_params: dict = Field(default_factory=dict)
    extra: dict = Field(default_factory=dict)
    # Pre-T13 C3: Profile severity_mapping flows ConfigService -> MergedConfig
    # -> EvalParams -> DiagnosisEngine. Keys: full taxonomy code or short
    # suffix (e.g. "numerical_mismatch"). Empty = use platform default hints.
    severity_mapping: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class EvaluationEngine(Protocol):
    """Unified engine interface. Business layer never branches on engine name (ADR-03)."""

    name: str

    def metric_names(self) -> list[str]: ...

    async def evaluate(
        self,
        record: EvaluationRecord,
        metrics: list[str],
        params: EvalParams,
    ) -> list[MetricResult]: ...
