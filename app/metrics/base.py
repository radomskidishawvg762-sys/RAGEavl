from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.domain.schemas import Category, EvaluationRecord, MetricResult


class MetricSpec(BaseModel):
    """Registry metadata for a metric (FR-14)."""

    name: str
    category: Category
    engine: str
    description: str = ""
    input_requirements: list[str] = Field(default_factory=list)
    version: str
    default_severity: str | None = None
    # T-16C: Regression direction — registry metadata, never frontend-hardcoded.
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"


@runtime_checkable
class MetricEvaluator(Protocol):
    """Per-metric evaluator; engines register these (T-08~T-10)."""

    async def __call__(self, record: EvaluationRecord, params: dict) -> MetricResult: ...


@dataclass
class RegisteredMetric:
    spec: MetricSpec
    evaluator: MetricEvaluator
