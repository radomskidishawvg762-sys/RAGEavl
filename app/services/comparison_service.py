"""ComparisonService (T-16B) — READ-ONLY two-run comparison.

Hard boundaries (§七/§十一):
  - Never calls an Engine / Judge / RAG Adapter / metric evaluation. Everything
    is derived from already-persisted runs + metric_results rows.
  - Never mutates any row; no new Repository methods.
  - Metric-level scores are means of persisted scores over the records both
    runs scored (paired on record_id); statuses reuse
    ReportService._aggregate_metrics. Differing scored-record sets mark the
    comparison LIMITED. The run's own overall_score is read as-is (§五).

delta rules (§二): delta = candidate - baseline; relative_delta = delta /
baseline (None when baseline is 0 or missing). A null score is NEVER coerced
to 0 — the metric is marked comparable=false with an incomparable_reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.repositories.evaluation import EvaluationRepository
from app.services.report_service import (
    ReportService,
    compute_overall_score,
    is_unscored_mismatch,
)

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
            uneven = [m["name"] for m in metrics if not m["coverage_consistent"]]
            if uneven:
                status = "LIMITED"
                reasons = [*reasons, f"metric coverage differs (paired on common scored "
                                     f"records only): {uneven}"]
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
    def _by_record(rows: list[dict]) -> dict[str, dict[str, dict]]:
        out: dict[str, dict[str, dict]] = {}
        for row in rows:
            out.setdefault(row["metric_name"], {})[row.get("record_id")] = row
        return out

    @staticmethod
    def _compare_metrics(b_rows, b_weights, c_rows, c_weights) -> list[dict]:
        """Scores are means over the records BOTH runs scored (paired on
        record_id) — a run-level mean over different record sets is not a
        like-for-like delta. Statuses come from the full-run aggregation."""
        b_agg = {m["name"]: m for m in ReportService._aggregate_metrics(b_rows, b_weights)}
        c_agg = {m["name"]: m for m in ReportService._aggregate_metrics(c_rows, c_weights)}
        b_rec, c_rec = ComparisonService._by_record(b_rows), ComparisonService._by_record(c_rows)
        out: list[dict] = []
        for name in sorted(set(b_agg) | set(c_agg)):
            b, c = b_agg.get(name), c_agg.get(name)
            b_by, c_by = b_rec.get(name, {}), c_rec.get(name, {})
            b_valid = {rid for rid, r in b_by.items() if r.get("score") is not None}
            c_valid = {rid for rid, r in c_by.items() if r.get("score") is not None}
            paired = b_valid & c_valid
            common = set(b_by) & set(c_by)
            b_score = b["score"] if b else None
            c_score = c["score"] if c else None
            if paired:
                b_score = compute_overall_score([b_by[rid] for rid in paired], b_weights)[0]
                c_score = compute_overall_score([c_by[rid] for rid in paired], c_weights)[0]
            comparable = bool(paired)
            delta = None
            relative_delta = None
            if comparable:
                delta = c_score - b_score
                if b_score != 0:
                    relative_delta = delta / b_score
            if comparable:
                reason = None
            elif b_valid and c_valid:
                reason = "no_paired_records"
            else:
                reason = ComparisonService._incomparable_reason(b, c)
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
                "incomparable_reason": reason,
                "baseline_valid_count": len(b_valid),
                "candidate_valid_count": len(c_valid),
                "paired_count": len(paired),
                # a side with no valid score at all is already metric-level
                # not comparable; coverage only qualifies deltas both sides have
                "coverage_consistent": not (b_valid and c_valid) or b_valid == c_valid,
                "new_unscored_mismatches": sum(
                    1 for rid in common
                    if is_unscored_mismatch(c_by[rid]) and not is_unscored_mismatch(b_by[rid])),
                "resolved_unscored_mismatches": sum(
                    1 for rid in common
                    if is_unscored_mismatch(b_by[rid]) and not is_unscored_mismatch(c_by[rid])),
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
