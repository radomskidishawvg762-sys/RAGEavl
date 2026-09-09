"""Integrity normalization library (T-09, Spec A.6 / FR-16).

STRICT SCOPE — Extraction + Normalization ONLY:
  - No match/mismatch decision (that is T-10 Comparison).
  - No Diagnosis, no Recommendation (P-1).
  - No Repository/ORM access; pure functions, independently testable.
  - No Judge LLM calls — deterministic-first (ADR-04); anything not reliably
    resolvable returns parse_status="ambiguous", never a guess.

Output models are the direct inputs to T-10's ComparisonBasis sides.
"""

from app.metrics.integrity.base import (
    ParseStatus,
    normalize_text_key,
)
from app.metrics.integrity.entity import (
    NormalizedEntity,
    extract_entities,
    normalize_entity,
)
from app.metrics.integrity.numerical import (
    NormalizedNumerical,
    extract_numerical,
    normalize_numerical,
)
from app.metrics.integrity.temporal import (
    NormalizedTemporal,
    extract_temporal,
    normalize_temporal,
)

__all__ = [
    "ParseStatus",
    "normalize_text_key",
    "NormalizedEntity",
    "extract_entities",
    "normalize_entity",
    "NormalizedTemporal",
    "extract_temporal",
    "normalize_temporal",
    "NormalizedNumerical",
    "extract_numerical",
    "normalize_numerical",
]
