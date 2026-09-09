"""ReportService (T-14C) — READ-ONLY aggregation layer.

Hard boundaries:
  - Never re-executes a Metric / RAGAS / Integrity / Judge / RAG Adapter call.
    Everything is derived from already-persisted metric_results / diagnoses /
    evaluation_runs rows.
  - Never mutates MetricResult / EvaluationResult / Diagnosis.
  - Run-level diagnosis is the aggregation of PERSISTED sample-level
    diagnoses via DiagnosisEngine.diagnose_run_aggregate() — buckets exist
    only where the sample chain (Failure -> Evidence -> Diagnosis) already
    produced rows; a run with zero diagnoses reports
    run_diagnosis_status="not_available" instead of faking one.

Aggregation rules (§二/§四):
  - overall_score = Σ(score_i × weight_i) / Σ(weight_i) over rows with a REAL
    score. null is never treated as 0; ambiguous / not_configured /
    execution-error rows are excluded but still reported with their status.
  - Three orthogonal outcomes, never conflated:
      FAILURE           score present but below threshold / deterministic mismatch
      EVALUATION_ERROR  EXT_*/SYS_* error rows
      UNDETERMINED      ambiguous / unparsed / insufficient evidence
      (+ NOT_CONFIGURED / NOT_RUN as explicit non-quality states)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import redact_text
from app.diagnosis import DiagnosisEngine
from app.repositories.evaluation import EvaluationRepository

# comparison outcomes that mean "cannot judge" (never a score, never a failure)
_UNDETERMINED_COMPARISON_TYPES = {"ambiguous", "missing_reference"}

# error codes that mean "the evaluator never ran because it is not configured"
_NOT_CONFIGURED_CODES = {"BIZ_JUDGE_NOT_CONFIGURED"}

_RUN_TERMINAL_STATES = {"completed", "completed_with_errors", "failed", "cancelled"}


def metric_status(row: dict) -> str:
    """Per-row Metric Execution Status (§三).

    Accepts either the aggregation row shape (`comparison_type` scalar, from
    the trimmed report query) or a full metric row carrying the raw
    `comparison_basis` JSONB (result-detail / fake-repo callers)."""
    if row.get("score") is not None:
        # a real score exists — quality failure (threshold / mismatch) is still
        # "completed" execution; failure-ness lives in `passed` / Failures list
        return "completed"
    error = row.get("error") or {}
    if error:
        code = str(error.get("code", ""))
        if code in _NOT_CONFIGURED_CODES:
            return "not_configured"
        return "error"
    comparison_type = row.get("comparison_type")
    if comparison_type is None:
        basis = row.get("comparison_basis") or {}
        comparison_type = basis.get("comparison_type")
    if comparison_type in _UNDETERMINED_COMPARISON_TYPES:
        return "undetermined"
    return "not_run"


def compute_overall_score(
    rows: list[dict], weights: dict[str, float] | None = None
) -> tuple[float | None, int, int]:
    """Weighted mean over VALID scores only. Returns
    (overall_score | None, valid_count, total_row_count)."""
    weights = weights or {}
    numerator = 0.0
    denominator = 0.0
    valid = 0
    invalid = 0
    for row in rows:
        score = row.get("score")
        if score is None:
            invalid += 1
            continue
        valid += 1
        weight = float(weights.get(row.get("metric_name"), 1.0))
        numerator += float(score) * weight
        denominator += weight
    if valid == 0 or denominator == 0:
        return None, 0, valid + invalid
    return numerator / denominator, valid, valid + invalid


class ReportService:
    def __init__(self, repo: EvaluationRepository, diagnosis_engine: DiagnosisEngine | None = None) -> None:
        self._repo = repo
        self._diagnosis_engine = diagnosis_engine

    def build_report(self, run_id: str) -> dict[str, Any]:
        run = self._repo.get_run(run_id)  # 404 BIZ_NOT_FOUND when missing
        metric_rows = self._repo.list_metric_rows_for_run(run_id)
        results = self._repo.list_results_for_run(run_id)
        diagnoses = self._repo.list_diagnoses_for_run(run_id)

        meta = run.reproducibility_meta or {}
        enabled_metrics: list[str] = list(meta.get("enabled_metrics") or [])
        weights: dict[str, float] = dict(meta.get("metric_weights") or {})
        terminal = run.status in _RUN_TERMINAL_STATES

        # overall_score is persisted at run completion (§七); the report reads it
        # and only falls back to a computation for legacy/uncached runs.
        overall_score = run.overall_score
        if overall_score is None and terminal:
            overall_score, _valid, _total = compute_overall_score(metric_rows, weights)

        aggregated_metrics = self._aggregate_metrics(metric_rows, weights)
        failures = self._failures(diagnoses, results)
        undetermined = self._undetermined(diagnoses, results)
        execution_errors = self._execution_errors(metric_rows, run.error_summary)
        run_level_diagnoses = self._run_level_diagnoses(diagnoses, results)
        return {
            "summary": {
                "run_id": run.id,
                "status": run.status,
                "is_final": terminal,
                "overall_score": overall_score,
                "total_records": run.total_records,
                "evaluated_records": run.evaluated_records,
                "error_records": run.error_records,
                "evaluation_coverage": run.evaluation_coverage,
                "valid_metric_count": sum(1 for r in metric_rows if r.get("score") is not None),
                "total_enabled_metric_count": len(enabled_metrics)
                or len({r["metric_name"] for r in metric_rows}),
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "message": None if terminal else f"run is {run.status}: report is not final",
            },
            "metrics": aggregated_metrics,
            "raw_metrics": [
                {
                    "record_id": r.get("record_id"),
                    "name": r["metric_name"],
                    "category": r.get("category"),
                    "score": r.get("score"),
                    "threshold": r.get("threshold"),
                    "passed": r.get("passed"),
                    "status": metric_status(r),
                    "metric_version": r.get("metric_version"),
                }
                for r in metric_rows
            ],
            "failures": failures,
            "undetermined": undetermined,
            "execution_errors": execution_errors,
            "run_level_diagnoses": run_level_diagnoses,
            "quality_dimensions": self._quality_dimensions(
                aggregated_metrics, diagnoses, failures, undetermined
            ),
            "reproducibility": meta,
            "generated_at": datetime.now(UTC),
        }

    # ---- sections ----

    # Quality Dimension Model (Phase 9 Step 6/7): presentation-only aggregation
    # over persisted metrics/diagnoses. No business recomputation — scores come
    # from _aggregate_metrics, counts from the persisted failure/undetermined
    # lists. faithfulness deliberately appears in BOTH generation and
    # groundedness (dual-homed by design: it measures generation quality AND
    # groundedness of the answer in retrieved context).
    _DIMENSION_METRICS: dict[str, set[str]] = {
        "retrieval": {"context_precision", "context_recall"},
        "generation": {"faithfulness", "answer_relevancy"},
        "groundedness": {"faithfulness"},
        "correctness": {"entity_consistency", "temporal_consistency", "numerical_consistency"},
    }

    @classmethod
    def _quality_dimensions(
        cls,
        aggregated_metrics: list[dict],
        diagnoses,
        failures: list[dict],
        undetermined: list[dict],
    ) -> list[dict]:
        failures_by_metric: dict[str, int] = {}
        for f in failures:
            m = f.get("related_metric")
            if m:
                failures_by_metric[m] = failures_by_metric.get(m, 0) + 1
        undet_by_metric: dict[str, int] = {}
        for u in undetermined:
            m = u.get("related_metric")
            if m:
                undet_by_metric[m] = undet_by_metric.get(m, 0) + 1
        diagnosed_by_metric: dict[str, int] = {}
        evidence_by_metric: dict[str, int] = {}
        for d in diagnoses:
            if getattr(d, "status", None) == "diagnosed":
                m = getattr(d, "related_metric", None)
                if m:
                    diagnosed_by_metric[m] = diagnosed_by_metric.get(m, 0) + 1
                    if getattr(d, "evidence", None):
                        evidence_by_metric[m] = evidence_by_metric.get(m, 0) + 1

        dims: list[dict] = []
        for dimension, metric_names in cls._DIMENSION_METRICS.items():
            metric_entries = [m for m in aggregated_metrics if m["name"] in metric_names]
            scores = [m["score"] for m in metric_entries if m.get("score") is not None]
            valid_counts = sum(m.get("valid_count", 0) for m in metric_entries)
            total_counts = sum(m.get("valid_count", 0) + m.get("invalid_count", 0) for m in metric_entries)
            failure_count = sum(failures_by_metric.get(m["name"], 0) for m in metric_entries)
            diagnosed_count = sum(diagnosed_by_metric.get(m["name"], 0) for m in metric_entries)
            evidence_count = sum(evidence_by_metric.get(m["name"], 0) for m in metric_entries)
            dims.append({
                "dimension": dimension,
                "metrics": [m["name"] for m in metric_entries],
                "score": round(sum(scores) / len(scores), 4) if scores else None,
                "metric_count": len(metric_entries),
                "failure_count": failure_count,
                "undetermined_count": sum(undet_by_metric.get(m["name"], 0) for m in metric_entries),
                "diagnosed_count": diagnosed_count,
                "diagnosis_coverage": round(diagnosed_count / failure_count, 4)
                if failure_count else None,
                "evidence_count": evidence_count,
                "evidence_coverage": round(evidence_count / diagnosed_count, 4)
                if diagnosed_count else None,
                "evaluated_rows": valid_counts,
                "total_rows": total_counts,
            })
        scored = [d["score"] for d in dims if d["score"] is not None]
        weakest_score = min(scored) if scored else None
        for dimension in dims:
            dimension["is_weakest"] = (
                weakest_score is not None and dimension["score"] == weakest_score
            )
        return dims

    @staticmethod
    def _aggregate_metrics(metric_rows: list[dict], weights: dict[str, float]) -> list[dict]:
        buckets: dict[str, list[dict]] = {}
        for row in metric_rows:
            buckets.setdefault(row["metric_name"], []).append(row)
        out: list[dict] = []
        for name, rows in buckets.items():
            score, valid_count, total_rows = compute_overall_score(rows, weights)
            statuses = [metric_status(r) for r in rows]
            out.append(
                {
                    "name": name,
                    "category": rows[0].get("category"),
                    "score": score,  # metric-level weighted mean of VALID rows
                    "threshold": rows[0].get("threshold"),
                    "passed": None if score is None else (
                        all(r.get("passed") is not False for r in rows)
                        if rows[0].get("threshold") is not None else None
                    ),
                    "status": "completed" if valid_count else (
                        "error" if "error" in statuses else statuses[0]
                    ),
                    "valid_count": valid_count,
                    "invalid_count": total_rows - valid_count,
                    "error_count": statuses.count("error") + statuses.count("not_configured"),
                    "metric_version": rows[0].get("metric_version"),
                    "weight": float(weights.get(name, 1.0)),
                }
            )
        return out

    @staticmethod
    def _failures(diagnoses, results) -> list[dict]:
        question_by_result = {r.id: r.question for r in results}
        record_by_result = {r.id: r.record_id for r in results}
        out: list[dict] = []
        for d in diagnoses:
            if d.status != "diagnosed":
                continue
            out.append(
                {
                    "diagnosis_id": d.id,
                    "record_id": record_by_result.get(d.result_id),
                    "question": question_by_result.get(d.result_id),
                    "related_metric": d.related_metric,
                    "failure_type": d.failure_type,
                    "severity": d.severity,
                    "root_cause": d.root_cause,
                    "status": "failure",
                    "confidence": d.confidence,
                }
            )
        return out

    @staticmethod
    def _undetermined(diagnoses, results) -> list[dict]:
        record_by_result = {r.id: r.record_id for r in results}
        out: list[dict] = []
        for d in diagnoses:
            if d.status != "undetermined":
                continue
            detail = d.detail or {}
            out.append(
                {
                    "diagnosis_id": d.id,
                    "record_id": record_by_result.get(d.result_id),
                    "related_metric": d.related_metric,
                    "status": "undetermined",
                    "reason": detail.get("reason"),
                    "missing_evidence": detail.get("missing_evidence") or [],
                }
            )
        return out

    @staticmethod
    def _execution_errors(metric_rows: list[dict], error_summary: dict | None) -> list[dict]:
        out: list[dict] = []
        for row in metric_rows:
            code = (row.get("error") or {}).get("code")
            if not code:
                continue
            out.append(
                {
                    "record_id": row.get("record_id"),
                    "metric": row["metric_name"],
                    "error_code": code,
                    # sanitized: reports must never carry connection strings / keys
                    "message": redact_text(str((row.get("error") or {}).get("message", ""))[:300]),
                }
            )
        for detail in (error_summary or {}).get("details", []):
            err = detail.get("error") or {}
            out.append(
                {
                    "record_id": detail.get("record_id"),
                    "metric": None,  # record-level failure (e.g. RAG fetch)
                    "error_code": err.get("code"),
                    "message": redact_text(str(err.get("message", ""))[:300]),
                }
            )
        return out

    def _run_level_diagnoses(self, diagnoses, results) -> dict:
        """Run-level diagnosis = aggregation of PERSISTED sample-level
        diagnoses (Failure -> Evidence -> Diagnosis already happened per
        sample; this only counts and groups via DiagnosisEngine).
        Consumption-only: no Engine/Judge re-execution, no score-only
        inference — evidence-insufficient samples stay undetermined."""
        engine = self._diagnosis_engine or DiagnosisEngine()
        record_id_by_result = {r.id: r.record_id for r in results}
        summary = engine.diagnose_run_aggregate(diagnoses, record_id_by_result)
        payload = summary.model_dump()
        # the response contract field is `items`; the summary model calls them buckets
        payload["items"] = payload.pop("buckets")
        payload["status"] = "available" if summary.available else "not_available"
        return payload
