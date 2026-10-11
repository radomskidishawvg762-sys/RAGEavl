"""QualityGateService (T-17) — absolute quality standard vs a single Run.

Independent of Regression (relative change): the Gate compares the CURRENT
run's metric scores against ABSOLUTE thresholds. Regression epsilon is NEVER
reused (§一/§四).

Data sources (§十一): persisted run + metric_results rows (via the existing
Report aggregation), and the Profile's quality_gate config. No Engine / Judge /
RAG Adapter / Diagnosis is called.

null semantics (§五): score=null is NEVER a quality FAIL — it is NOT_EVALUABLE
with a reason (not_configured / execution_error / undetermined / not_run).
The gate still FAILs (a required metric could not be verified), but the reason
is REQUIRED_METRIC_NOT_EVALUABLE, not QUALITY_THRESHOLD_FAILED. The exception is
a judged deterministic mismatch without a score (unit_mismatch): it is a
confirmed failure, so any such row FAILs the metric
(REQUIRED_METRIC_DETERMINISTIC_FAILURE) whatever the mean of the scored rows.

Thresholds come from the run snapshot's effective_metrics; only runs in a
completed* state are judged (RUN_NOT_COMPLETED otherwise).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.metrics.registry import metric_direction
from app.repositories.evaluation import EvaluationRepository
from app.services.config_resource_service import resolve_config_row
from app.services.report_service import ReportService

# gate-level reason codes (§九) — a different semantics layer from
# REGRESSION / UNDETERMINED / EXECUTION_ERROR
QUALITY_THRESHOLD_FAILED = "QUALITY_THRESHOLD_FAILED"
REQUIRED_METRIC_NOT_EVALUABLE = "REQUIRED_METRIC_NOT_EVALUABLE"
QUALITY_GATE_DISABLED = "QUALITY_GATE_DISABLED"
QUALITY_GATE_NOT_CONFIGURED = "QUALITY_GATE_NOT_CONFIGURED"
QUALITY_GATE_CONFIG_INVALID = "QUALITY_GATE_CONFIG_INVALID"
RUN_NOT_COMPLETED = "RUN_NOT_COMPLETED"
REQUIRED_METRIC_DETERMINISTIC_FAILURE = "REQUIRED_METRIC_DETERMINISTIC_FAILURE"

# only finished evaluations are judged; partial rows of a running / failed /
# cancelled run must never PASS or FAIL a gate
_EVALUABLE_RUN_STATES = {"completed", "completed_with_errors"}

# report metric_status -> per-metric not-evaluable reason (§五)
_STATUS_REASON = {
    "not_configured": "not_configured",
    "error": "execution_error",
    "undetermined": "undetermined",
    "not_run": "not_run",
}


class QualityGateService:
    def __init__(self, repo: EvaluationRepository, config_service) -> None:
        self._repo = repo
        self._config_service = config_service

    def evaluate(self, run_id: str) -> dict[str, Any]:
        run = self._repo.get_run(run_id)  # 404 BIZ_NOT_FOUND when missing
        if run.status not in _EVALUABLE_RUN_STATES:
            return self._result(run, "NOT_EVALUABLE", [RUN_NOT_COMPLETED], [])
        meta = run.reproducibility_meta or {}
        # Phase D invariant 8: gate config comes from the RUN SNAPSHOT when the
        # run carries one (key presence semantics — explicit null = unconfigured);
        # current YAML can NEVER override what the run actually executed.
        # Legacy runs (pre-snapshot) fall back to config resolution.
        if "quality_gate" in meta:
            raw = meta.get("quality_gate")
            qg = raw if isinstance(raw, dict) and raw else None
        else:
            qg = self._quality_gate_config(run)
        if qg is None:
            return self._result(run, "NOT_EVALUABLE", [QUALITY_GATE_NOT_CONFIGURED], [])
        if qg.get("enabled") is not True:
            return self._result(run, "NOT_EVALUABLE", [QUALITY_GATE_DISABLED], [])
        required = qg.get("required_metrics")
        if not isinstance(required, list) or not required:
            return self._result(run, "NOT_EVALUABLE", [QUALITY_GATE_NOT_CONFIGURED], [])

        enabled = list(meta.get("enabled_metrics") or [])
        weights = dict(meta.get("metric_weights") or {})
        rows = self._repo.list_metric_rows_for_run(run_id)
        agg = {m["name"]: m for m in ReportService._aggregate_metrics(rows, weights)}
        effective = meta.get("effective_metrics")
        total_records = run.total_records

        metrics: list[dict] = []
        config_invalid = False
        gate_reasons: set[str] = set()
        for name in required:
            if name not in enabled:
                config_invalid = True
                metrics.append(self._metric_not_evaluable(name, None, None, "not_in_enabled_metrics"))
                continue
            m = agg.get(name)
            threshold, threshold_issue = self._threshold_for(name, effective, rows)
            coverage = self._coverage(m, total_records)
            if m is not None and m.get("unscored_mismatch_count"):
                metrics.append({
                    "metric": name, "score": m.get("score"), "threshold": threshold,
                    "passed": False, "status": "FAIL",
                    "reason": "unscored_deterministic_mismatch", **coverage,
                })
                gate_reasons.add(REQUIRED_METRIC_DETERMINISTIC_FAILURE)
                continue
            if m is None or m.get("score") is None:
                status = (m or {}).get("status") or "not_run"
                reason = _STATUS_REASON.get(status, "not_run")
                metrics.append(self._metric_not_evaluable(
                    name, None, threshold, reason, coverage))
                gate_reasons.add(REQUIRED_METRIC_NOT_EVALUABLE)
                continue
            if threshold is None:
                config_invalid = True
                metrics.append(self._metric_not_evaluable(name, m["score"], None,
                                                          threshold_issue, coverage))
                continue
            direction = metric_direction(name)
            if direction == "lower_is_better":
                passed = m["score"] <= threshold
            else:
                passed = m["score"] >= threshold
            metrics.append({
                "metric": name,
                "score": m["score"],
                "threshold": threshold,
                "passed": passed,
                "status": "PASS" if passed else "FAIL",
                "reason": None,
                **coverage,
            })
            if not passed:
                gate_reasons.add(QUALITY_THRESHOLD_FAILED)

        if config_invalid:
            return self._result(run, "NOT_EVALUABLE", [QUALITY_GATE_CONFIG_INVALID], metrics)
        if gate_reasons:
            return self._result(run, "FAIL", sorted(gate_reasons), metrics)
        return self._result(run, "PASS", [], metrics)

    def _quality_gate_config(self, run) -> dict | None:
        cfg = self._repo.get_config(run.config_id)
        domain, profile, profile_body = resolve_config_row(cfg)
        # Phase B invariant 3: stored-profile runs resolve through the IMMUTABLE
        # stored body (config_id -> body is stable); current YAML can never
        # override what the run actually executed.
        merged = self._config_service.load(domain, profile, profile_body=profile_body)
        qg = merged.get("quality_gate")
        return qg if isinstance(qg, dict) and qg else None

    @staticmethod
    def _threshold_for(name: str, effective, rows: list[dict]) -> tuple[float | None, str]:
        """(threshold, reason when None). The run snapshot's effective_metrics is
        authoritative; legacy runs without it fall back to the persisted row
        thresholds only when every row agrees."""
        if isinstance(effective, dict) and name in effective:
            spec = effective.get(name)
            threshold = spec.get("threshold") if isinstance(spec, dict) else None
            return threshold, "threshold_not_configured"
        distinct = {r.get("threshold") for r in rows
                    if r.get("metric_name") == name and r.get("threshold") is not None}
        if len(distinct) > 1:
            return None, "threshold_inconsistent"
        return (next(iter(distinct)) if distinct else None), "threshold_not_configured"

    @staticmethod
    def _coverage(m: dict | None, total_records: int | None) -> dict:
        valid = (m or {}).get("valid_count", 0)
        return {
            "valid_count": valid,
            "total_records": total_records,
            "coverage": round(valid / total_records, 4) if total_records else None,
            "unscored_mismatch_count": (m or {}).get("unscored_mismatch_count", 0),
        }

    @staticmethod
    def _metric_not_evaluable(name, score, threshold, reason, coverage=None) -> dict:
        return {"metric": name, "score": score, "threshold": threshold,
                "passed": None, "status": "NOT_EVALUABLE", "reason": reason,
                **(coverage or {"valid_count": None, "total_records": None,
                                "coverage": None, "unscored_mismatch_count": 0})}

    @staticmethod
    def _result(run, status, reasons, metrics) -> dict:
        return {
            "run_id": run.id,
            "status": status,
            "reasons": reasons,
            "metrics": metrics,
            "overall_score": run.overall_score,  # display auxiliary only (§十)
            "evaluated_at": datetime.now(UTC),
        }
