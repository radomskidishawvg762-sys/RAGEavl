"""RegressionService (T-16C) — structured Regression / Improvement / Stable.

Pure result-analysis layer (§一): consumes the ComparisonService output
(aligned, already-persisted metric deltas). Never re-queries or re-computes a
metric; never calls Engine / Judge / RAG Adapter / Diagnosis.

Direction (§二) is metric metadata — MetricSpec.direction (registry),
additively defaulted to higher_is_better. Verdicts are derived HERE, never in
the frontend (§十 test 16).

epsilon (§四): an Engineering Target, provisional ONLY — never a product
baseline, never a financial threshold. Reads system.regression.epsilon from
merged config when present (epsilon_source="config"); otherwise uses the
minimal default 0.01 (epsilon_source="provisional_default"). Every verdict
carries its epsilon + source.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.metrics.registry import metric_direction
from app.services.comparison_service import ComparisonService

_DEFAULT_EPSILON = 0.01
_DEFAULT_EPSILON_SOURCE = "provisional_default"

_CATEGORIES = ("retrieval", "generation", "integrity")
_DECISIVE = {"IMPROVEMENT", "REGRESSION", "STABLE"}


def resolve_epsilon(merged: dict) -> tuple[float, str]:
    """system.regression.epsilon from merged config, else provisional default."""
    system = merged.get("system") if isinstance(merged.get("system"), dict) else {}
    reg = system.get("regression") if isinstance(system.get("regression"), dict) else {}
    eps = reg.get("epsilon")
    if isinstance(eps, (int, float)) and not isinstance(eps, bool):
        return float(eps), "config"
    return _DEFAULT_EPSILON, _DEFAULT_EPSILON_SOURCE


def _direction(metric_name: str) -> str:
    return metric_direction(metric_name)


class RegressionService:
    def __init__(
        self,
        comparison: ComparisonService,
        *,
        epsilon: float = _DEFAULT_EPSILON,
        epsilon_source: str = _DEFAULT_EPSILON_SOURCE,
    ) -> None:
        self._comparison = comparison
        self._epsilon = epsilon
        self._epsilon_source = epsilon_source

    def compute(self, *, baseline_run_id: str, candidate_run_id: str) -> dict[str, Any]:
        comparison = self._comparison.compare(
            baseline_run_id=baseline_run_id, candidate_run_id=candidate_run_id
        )
        return self.analyze(comparison)

    # ---- core: pure analysis over a ComparisonService output dict ----

    def analyze(self, comparison: dict) -> dict[str, Any]:
        status = comparison["comparability"]["status"]
        if status == "BLOCKED":
            return self._blocked(comparison)
        items = [self._verdict_for(m) for m in comparison.get("metrics") or []]
        categories = self._categories(items)
        return {
            "baseline_run_id": comparison["baseline_run_id"],
            "candidate_run_id": comparison["candidate_run_id"],
            "comparability_status": status,
            "metrics": items,
            "categories": categories,
            "trade_off": self._trade_off(categories),
            "overall": self._overall(items, comparison),
            "generated_at": datetime.now(UTC),
        }

    # ---- per-metric verdict (§三/§五) ----

    def _verdict_for(self, m: dict) -> dict:
        direction = _direction(m["name"])
        base = {
            "metric": m["name"],
            "category": m.get("category"),
            "direction": direction,
            "baseline_score": m.get("baseline_score"),
            "candidate_score": m.get("candidate_score"),
            "delta": m.get("delta"),
            "relative_delta": m.get("relative_delta"),
            "epsilon": self._epsilon,
            "epsilon_source": self._epsilon_source,
        }
        if not m.get("comparable"):
            # score=null is NEVER a regression (§五)
            base["verdict"] = (
                "UNDETERMINED" if m.get("incomparable_reason") == "undetermined"
                else "NOT_COMPARABLE"
            )
            return base
        delta = m["delta"]
        if abs(delta) <= self._epsilon:
            base["verdict"] = "STABLE"
        elif direction == "higher_is_better":
            base["verdict"] = "IMPROVEMENT" if delta > 0 else "REGRESSION"
        else:  # lower_is_better
            base["verdict"] = "REGRESSION" if delta > 0 else "IMPROVEMENT"
        return base

    # ---- category aggregation (§七) ----

    @staticmethod
    def _categories(items: list[dict]) -> dict[str, dict]:
        cats = {
            c: {"improvement_count": 0, "regression_count": 0,
                "stable_count": 0, "comparable_count": 0, "verdict": "NOT_COMPARABLE"}
            for c in _CATEGORIES
        }
        for it in items:
            cat = it.get("category") or "generation"
            bucket = cats.setdefault(cat, {"improvement_count": 0, "regression_count": 0,
                                           "stable_count": 0, "comparable_count": 0,
                                           "verdict": "NOT_COMPARABLE"})
            v = it["verdict"]
            if v in _DECISIVE:
                bucket["comparable_count"] += 1
                bucket[f"{v.lower()}_count"] += 1
        for bucket in cats.values():
            if bucket["comparable_count"] == 0:
                bucket["verdict"] = "NOT_COMPARABLE"
            elif bucket["improvement_count"] and bucket["regression_count"]:
                bucket["verdict"] = "MIXED"
            elif bucket["improvement_count"]:
                bucket["verdict"] = "IMPROVEMENT"
            elif bucket["regression_count"]:
                bucket["verdict"] = "REGRESSION"
            else:
                bucket["verdict"] = "STABLE"
        return cats

    @staticmethod
    def _trade_off(categories: dict) -> bool:
        """§六: retrieval ↑ + generation ↓, or retrieval ↓ + generation ↑."""
        r = categories.get("retrieval", {}).get("verdict")
        g = categories.get("generation", {}).get("verdict")
        return (r == "IMPROVEMENT" and g == "REGRESSION") or (
            r == "REGRESSION" and g == "IMPROVEMENT"
        )

    # ---- overall verdict (§八) ----

    @staticmethod
    def _overall(items: list[dict], comparison: dict) -> dict:
        decisive = [it["verdict"] for it in items if it["verdict"] in _DECISIVE]
        has_improve = "IMPROVEMENT" in decisive
        has_regress = "REGRESSION" in decisive
        if not decisive:
            verdict, reason = "NOT_COMPARABLE", "no comparable metric verdicts"
        elif has_improve and has_regress:
            verdict, reason = "MIXED", "metrics moved in opposing directions"
        elif has_improve:
            verdict, reason = "IMPROVEMENT", None
        elif has_regress:
            verdict, reason = "REGRESSION", None
        else:
            verdict, reason = "STABLE", None
        return {
            "verdict": verdict,
            "reason": reason,
            # auxiliary ONLY — the verdict never comes from the overall delta (§八)
            "overall_score_delta": comparison.get("overall", {}).get("delta"),
        }

    @staticmethod
    def _blocked(comparison: dict) -> dict[str, Any]:
        """§十 test 17: BLOCKED -> no regression rows, explicitly not comparable."""
        not_comparable = {"improvement_count": 0, "regression_count": 0,
                          "stable_count": 0, "comparable_count": 0, "verdict": "NOT_COMPARABLE"}
        return {
            "baseline_run_id": comparison["baseline_run_id"],
            "candidate_run_id": comparison["candidate_run_id"],
            "comparability_status": "BLOCKED",
            "metrics": [],
            "categories": {c: dict(not_comparable) for c in _CATEGORIES},
            "trade_off": False,
            "overall": {
                "verdict": "NOT_COMPARABLE",
                "reason": "; ".join(comparison.get("comparability", {}).get("reasons") or []),
                "overall_score_delta": None,
            },
            "generated_at": datetime.now(UTC),
        }
