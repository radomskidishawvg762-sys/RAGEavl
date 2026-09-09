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
is REQUIRED_METRIC_NOT_EVALUABLE, not QUALITY_THRESHOLD_FAILED.
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

        metrics: list[dict] = []
        config_invalid = False
        gate_reasons: set[str] = set()
        for name in required:
            if name not in enabled:
                config_invalid = True
                metrics.append(self._metric_not_evaluable(name, None, None, "not_in_enabled_metrics"))
                continue
            m = agg.get(name)
            if m is None or m.get("score") is None:
                status = (m or {}).get("status") or "not_run"
                reason = _STATUS_REASON.get(status, "not_run")
                metrics.append(self._metric_not_evaluable(
                    name, None, m.get("threshold") if m else None, reason))
                gate_reasons.add(REQUIRED_METRIC_NOT_EVALUABLE)
                continue
            if m.get("threshold") is None:
                config_invalid = True
                metrics.append(self._metric_not_evaluable(name, m["score"], None,
                                                          "threshold_not_configured"))
                continue
            direction = metric_direction(name)
            if direction == "lower_is_better":
                passed = m["score"] <= m["threshold"]
            else:
                passed = m["score"] >= m["threshold"]
            metrics.append({
                "metric": name,
                "score": m["score"],
                "threshold": m["threshold"],
                "passed": passed,
                "status": "PASS" if passed else "FAIL",
                "reason": None,
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
    def _metric_not_evaluable(name, score, threshold, reason) -> dict:
        return {"metric": name, "score": score, "threshold": threshold,
                "passed": None, "status": "NOT_EVALUABLE", "reason": reason}

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
