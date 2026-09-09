"""ComparisonService (T-16B) — READ-ONLY two-run comparison.

Hard boundaries (§七/§十一):
  - Never calls an Engine / Judge / RAG Adapter / metric evaluation. Everything
    is derived from already-persisted runs + metric_results rows.
  - Never mutates any row; no new Repository methods.
  - Metric-level scores are the SAME numbers the Report shows — reused via
    ReportService._aggregate_metrics (pure aggregation of persisted scores,
    never a recompute). The run's own overall_score is read as-is (§五).

delta rules (§二): delta = candidate - baseline; relative_delta = delta /
baseline (None when baseline is 0 or missing). A null score is NEVER coerced
to 0 — the metric is marked comparable=false with an incomparable_reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.repositories.evaluation import EvaluationRepository
from app.services.report_service import ReportService

_TERMINAL_STATES = {"completed", "completed_with_errors"}

# per-side metric status -> incomparable_reason (when that side has no score)
_STATUS_REASON = {
    "undetermined": "undetermined",
    "not_configured": "not_configured",
    "error": "execution_error",
    "not_run": "not_run",
}
# priority when BOTH sides are scoreless with different reasons (pick the
# more informative one): structural absence > execution error > not_configured
# > undetermined > nothing recorded
_REASON_PRIORITY = {
    "not_in_candidate": 5, "not_in_baseline": 5,
    "execution_error": 4, "not_configured": 3, "undetermined": 2, "not_run": 1,
}


def _meta(run) -> dict:
    return dict(run.reproducibility_meta or {})


class ComparisonService:
    def __init__(self, repo: EvaluationRepository) -> None:
        self._repo = repo

    def compare(self, *, baseline_run_id: str, candidate_run_id: str) -> dict[str, Any]:
        baseline = self._repo.get_run(baseline_run_id)  # 404 BIZ_NOT_FOUND when missing
        candidate = self._repo.get_run(candidate_run_id)

        status, reasons = self._comparability(baseline, candidate)

        metrics: list[dict] = []
        if status != "BLOCKED":
            b_rows = self._repo.list_metric_rows_for_run(baseline_run_id)
            c_rows = self._repo.list_metric_rows_for_run(candidate_run_id)
            metrics = self._compare_metrics(
                b_rows, _meta(baseline).get("metric_weights") or {},
                c_rows, _meta(candidate).get("metric_weights") or {},
            )
        overall = self._compare_overall(baseline, candidate, status)

        return {
            "baseline_run_id": baseline_run_id,
            "candidate_run_id": candidate_run_id,
            "comparability": {"status": status, "reasons": reasons},
            "metrics": metrics,
            "overall": overall,
            "generated_at": datetime.now(UTC),
        }

    # ---- comparability (§三) ----

    @staticmethod
    def _comparability(baseline, candidate) -> tuple[str, list[str]]:
        checks: list[tuple[str, str]] = []  # (severity, reason); BLOCKED > LIMITED

        for run in (baseline, candidate):
            if run.status not in _TERMINAL_STATES:
                checks.append(("BLOCKED", f"run {run.id} not finalized (status={run.status})"))

        bm, cm = _meta(baseline), _meta(candidate)

        if baseline.dataset_id != candidate.dataset_id:
            checks.append(("BLOCKED", "runs target different datasets"))

        bv, cv = bm.get("dataset_version"), cm.get("dataset_version")
        if bv and cv:
            if bv != cv:
                checks.append(("BLOCKED", f"dataset version differs ({bv} vs {cv})"))
        else:
            checks.append(("LIMITED", "dataset version not snapshotted"))

        be, ce = bm.get("enabled_metrics"), cm.get("enabled_metrics")
        if be is None or ce is None:
            checks.append(("BLOCKED", "enabled metrics not snapshotted"))
        else:
            bset, cset = set(be), set(ce)
            if bset != cset:
                if not (bset & cset):
                    checks.append(("BLOCKED", "no common enabled metrics"))
                else:
                    added, removed = sorted(cset - bset), sorted(bset - cset)
                    checks.append(
                        ("LIMITED", f"enabled metrics differ "
                                    f"(baseline-only: {removed}, candidate-only: {added})")
                    )

        bmv, cmv = bm.get("metric_version"), cm.get("metric_version")
        if bmv and cmv:
            if bmv != cmv:
                checks.append(("LIMITED", f"metric version differs ({bmv} vs {cmv})"))
        else:
            checks.append(("LIMITED", "metric version not snapshotted"))

        bcv, ccv = bm.get("config_version"), cm.get("config_version")
        if bcv and ccv and bcv != ccv:
            checks.append(("LIMITED", "config version differs (profile key semantics)"))

        judge_reason = ComparisonService._judge_mismatch_reason(bm, cm)
        if judge_reason:
            checks.append(("LIMITED", judge_reason))

        if not checks:
            return "DIRECT", []
        status = "BLOCKED" if any(s == "BLOCKED" for s, _ in checks) else "LIMITED"
        return status, [r for _, r in checks]

    @staticmethod
    def _judge_mismatch_reason(bm: dict, cm: dict) -> str | None:
        """Compare judge fingerprint only when a judge exists on either side."""
        b_provider, c_provider = bm.get("judge_provider"), cm.get("judge_provider")
        b_model, c_model = bm.get("judge_model"), cm.get("judge_model")
        if not (b_provider or c_provider or b_model or c_model):
            return None
        bits: list[str] = []
        if b_provider and c_provider and b_provider != c_provider:
            bits.append(f"provider ({b_provider} vs {c_provider})")
        if b_model and c_model and b_model != c_model:
            bits.append(f"model ({b_model} vs {c_model})")
        if (b_model and not c_model) or (c_model and not b_model):
            bits.append(f"model present on one side only ({b_model or '-'} vs {c_model or '-'})")
        if (b_provider and not c_provider) or (c_provider and not b_provider):
            bits.append("provider present on one side only")
        if not bits:
            return None
        return "judge configuration differs: " + "; ".join(bits)

    # ---- per-metric comparison (§二/§四) ----

    @staticmethod
    def _compare_metrics(b_rows, b_weights, c_rows, c_weights) -> list[dict]:
        # same numbers the Report shows (persisted-score aggregation only)
        b_agg = {m["name"]: m for m in ReportService._aggregate_metrics(b_rows, b_weights)}
        c_agg = {m["name"]: m for m in ReportService._aggregate_metrics(c_rows, c_weights)}
        out: list[dict] = []
        for name in sorted(set(b_agg) | set(c_agg)):
            b, c = b_agg.get(name), c_agg.get(name)
            b_score = b["score"] if b else None
            c_score = c["score"] if c else None
            comparable = b_score is not None and c_score is not None
            delta = None
            relative_delta = None
            if comparable:
                delta = c_score - b_score
                if b_score != 0:
                    relative_delta = delta / b_score
            out.append({
                "name": name,
                "category": (b or c).get("category"),
                "baseline_score": b_score,
                "candidate_score": c_score,
                "delta": delta,
                "relative_delta": relative_delta,
                "baseline_status": b["status"] if b else "missing",
                "candidate_status": c["status"] if c else "missing",
                "metric_version": (b or c).get("metric_version"),
                "comparable": comparable,
                "incomparable_reason": None if comparable else ComparisonService._incomparable_reason(b, c),
            })
        return out

    @staticmethod
    def _incomparable_reason(b, c) -> str:
        candidates: dict[str, int] = {}
        if b is None:
            candidates["not_in_baseline"] = _REASON_PRIORITY["not_in_baseline"]
        else:
            st = b["status"]
            if st in _STATUS_REASON:
                candidates[_STATUS_REASON[st]] = _REASON_PRIORITY[_STATUS_REASON[st]]
        if c is None:
            candidates["not_in_candidate"] = _REASON_PRIORITY["not_in_candidate"]
        else:
            st = c["status"]
            if st in _STATUS_REASON:
                candidates[_STATUS_REASON[st]] = _REASON_PRIORITY[_STATUS_REASON[st]]
        if not candidates:
            return "not_run"
        return max(candidates, key=candidates.get)

    # ---- overall (§五) ----

    @staticmethod
    def _compare_overall(baseline, candidate, status: str) -> dict:
        b_score, c_score = baseline.overall_score, candidate.overall_score
        bm, cm = _meta(baseline), _meta(candidate)
        b_weights = dict(bm.get("metric_weights") or {})
        c_weights = dict(cm.get("metric_weights") or {})
        b_enabled, c_enabled = set(bm.get("enabled_metrics") or []), set(cm.get("enabled_metrics") or [])

        blockers: list[str] = []
        if status != "DIRECT":
            blockers.append("comparability is not DIRECT")
        if b_score is None:
            blockers.append(f"overall score unavailable for run {baseline.id}")
        if c_score is None:
            blockers.append(f"overall score unavailable for run {candidate.id}")
        if b_enabled != c_enabled:
            blockers.append("enabled metrics differ")
        if b_weights != c_weights:
            blockers.append("metric weights differ")

        if blockers:
            return {
                "baseline_overall": b_score,
                "candidate_overall": c_score,
                "delta": None,
                "comparable": False,
                "reason": "; ".join(blockers),
            }
        return {
            "baseline_overall": b_score,
            "candidate_overall": c_score,
            "delta": c_score - b_score,
            "comparable": True,
            "reason": None,
        }
