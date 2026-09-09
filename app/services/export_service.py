"""ExportService — one-click evaluation report export (post-freeze enhancement).

Hard boundaries (identical to Report/Comparison services):
  - READ-ONLY over persisted rows. Never calls Engine / Judge / RAG Adapter /
    Diagnosis / Recommendation generation, never writes.
  - Single source: ReportService.build_report (the same numbers the UI shows)
    + the persisted comparison_basis / diagnoses / recommendations the report
    surface intentionally omits.
  - Honesty rules inherited from the product: null is never 0; registered-but-
    not-enabled metrics are absent (never faked as executed); undetermined is a
    separate section (never merged into failures or diagnoses); evidence items
    are pass-through copies of persisted JSONB (never regenerated); execution
    errors are reported as errors, not quality.
  - Secrets: only reproducibility meta (public judge fields) and redacted
    error messages reach the document (both already sanitized upstream).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.repositories.evaluation import EvaluationRepository
from app.services.report_service import ReportService

UNDETERMINED_EXPLANATION = (
    "Undetermined ≠ Failure：证据不足或无法可靠判定，不折算为质量失败。"
    " (Insufficient evidence — never counted as a quality failure.)"
)

_MAX_UNDETERMINED_SAMPLES = 10


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


class ExportService:
    def __init__(self, repo: EvaluationRepository, report_service: ReportService) -> None:
        self._repo = repo
        self._report = report_service

    # ---- canonical payload (JSON format AND the data model the PDF renders) ----

    def build_export(self, run_id: str) -> dict[str, Any]:
        report = self._report.build_report(run_id)
        run = self._repo.get_run(run_id)
        dataset = self._repo.get_dataset(run.dataset_id)
        meta = report["reproducibility"] or {}

        basis_by_key = {
            (r["record_id"], r["metric_name"]): r["comparison_basis"]
            for r in self._repo.list_metric_basis_rows_for_run(run_id)
        }
        diagnoses = self._repo.list_diagnoses_for_run(run_id)
        recs = self._repo.list_recommendations(run_id)

        diagnosis_by_id = {d.id: d for d in diagnoses}
        # diagnosis -> record_id (failures + undetermined carry it on their rows)
        diagnosis_to_record: dict[str, Any] = {
            **{f["diagnosis_id"]: f["record_id"] for f in report["failures"]},
            **{u["diagnosis_id"]: u["record_id"] for u in report["undetermined"]},
        }

        failures = [
            {**f,
             "evidence": self._evidence_for(diagnosis_by_id, f["diagnosis_id"]),
             "comparison_basis": basis_by_key.get((f["record_id"], f["related_metric"]))}
            for f in report["failures"]
        ]
        evidence_sections = [
            {
                "diagnosis_id": d.id,
                "record_id": diagnosis_to_record.get(d.id),
                "failure_type": d.failure_type,
                "status": d.status,
                "evidence_contract": d.evidence_contract,
                "items": list(d.evidence or []),  # pass-through of persisted JSONB
            }
            for d in diagnoses
            if d.evidence
        ]
        recommendations = []
        for r in recs:
            owner = diagnosis_by_id.get(r.diagnosis_id)
            recommendations.append({
                "recommendation_id": r.id,
                "action": r.action,
                "priority": r.priority,
                "source": r.source,
                "diagnosis_id": r.diagnosis_id,
                "related_failure_type": owner.failure_type if owner else None,
                "related_metric": owner.related_metric if owner else None,
                "affected_record_id": diagnosis_to_record.get(r.diagnosis_id),
            })

        return {
            "run": {
                "run_id": run.id,
                "status": run.status,
                "is_final": report["summary"]["is_final"],
                "project_id": run.project_id,
                "dataset_id": dataset.id,
                "dataset_name": dataset.name,
                "dataset_version": dataset.version,
                "total_records": run.total_records,
                "evaluated_records": run.evaluated_records,
                "error_records": run.error_records,
                "evaluation_coverage": run.evaluation_coverage,
                "overall_score": report["summary"]["overall_score"],
                "created_at": report["summary"]["created_at"],
                "started_at": report["summary"]["started_at"],
                "finished_at": report["summary"]["finished_at"],
            },
            "quality_dimensions": report["quality_dimensions"],
            "metrics": report["metrics"],
            "enabled_metrics": list(meta.get("enabled_metrics") or []),
            "failures": failures,
            "undetermined": report["undetermined"],
            "execution_errors": report["execution_errors"],
            "diagnoses": [
                {
                    "diagnosis_id": d.id,
                    "status": d.status,
                    "failure_type": d.failure_type,
                    "related_metric": d.related_metric,
                    "root_cause": d.root_cause,
                    "severity": d.severity,
                    "confidence": d.confidence,
                    "evidence_contract": d.evidence_contract,
                    "evidence_available": bool(d.evidence),
                    "evidence_count": len(d.evidence or []),
                    "detail": d.detail,
                }
                for d in diagnoses
            ],
            "evidence": evidence_sections,
            "recommendations": recommendations,
            "summary": self._summary(report, meta),
            "reproducibility": {
                "dataset_version": meta.get("dataset_version"),
                "profile": meta.get("profile"),
                "profile_version": meta.get("profile_version"),
                "config_version": meta.get("config_version"),
                "metric_version": meta.get("metric_version"),
                "judge_provider": meta.get("judge_provider"),
                "judge_model": meta.get("judge_model"),
                "judge_model_version": meta.get("judge_model_version"),
                "prompt_version": meta.get("prompt_version"),
                "rag_input": (meta.get("rag_input") or {}).get("mode"),
                "enabled_metrics": meta.get("enabled_metrics"),
                "timestamp": meta.get("timestamp"),
            },
            "generated_at": report["generated_at"],
        }

    # ---- PDF ----

    def render_pdf(self, run_id: str) -> bytes:
        """Render the same payload as a paginated bilingual PDF. Lazy import
        keeps reportlab out of app-boot path for non-export deployments."""
        from app.services.report_pdf import render_report_pdf

        return render_report_pdf(self.build_export(run_id))

    # ---- helpers ----

    @staticmethod
    def _evidence_for(diagnosis_by_id: dict, diagnosis_id: str) -> list[dict]:
        d = diagnosis_by_id.get(diagnosis_id)
        return list(d.evidence or []) if d else []  # pass-through, never regenerated

    def _summary(self, report: dict, meta: dict) -> dict[str, Any]:
        dims = report["quality_dimensions"] or []
        weakest = next((d for d in dims if d.get("is_weakest")), None)
        failures = report["failures"] or []
        undetermined = report["undetermined"] or []
        raw_count = len(report["raw_metrics"] or [])
        diagnosis_to_record = {
            **{f["diagnosis_id"]: f["record_id"] for f in failures},
            **{u["diagnosis_id"]: u["record_id"] for u in undetermined},
        }

        metric_counter = Counter(f["related_metric"] for f in failures if f.get("related_metric"))
        diag_counter = Counter(f["failure_type"] for f in failures if f.get("failure_type"))
        reason_counter = Counter(
            (u.get("reason") or "unspecified") for u in undetermined
        )
        rec_records: dict[str, set] = {}
        for r in self._repo.list_recommendations(report["summary"]["run_id"]):
            rec_records.setdefault(r.action, set()).add(
                diagnosis_to_record.get(r.diagnosis_id)
            )
        impacted = sorted(
            ((a, len({x for x in s if x})) for a, s in rec_records.items()),
            key=lambda t: (-t[1], t[0]),
        )

        return {
            "overall_score": report["summary"]["overall_score"],
            "weakest_dimension": (
                {"dimension": weakest["dimension"], "score": weakest["score"]} if weakest else None
            ),
            "failure_count": len(failures),
            "undetermined": {
                "count": len(undetermined),
                "ratio_of_metric_rows": (
                    round(len(undetermined) / raw_count, 4) if raw_count else None
                ),
                "top_reasons": [
                    {"reason": r, "count": c} for r, c in reason_counter.most_common(5)
                ],
                "explanation": UNDETERMINED_EXPLANATION,
            },
            "most_failed_metric": (
                {"metric": metric_counter.most_common(1)[0][0],
                 "count": metric_counter.most_common(1)[0][1]}
                if metric_counter else None
            ),
            "most_frequent_diagnosis": (
                {"failure_type": diag_counter.most_common(1)[0][0],
                 "count": diag_counter.most_common(1)[0][1]}
                if diag_counter else None
            ),
            "highest_impact_recommendation": (
                {"action": impacted[0][0], "affected_records": impacted[0][1]}
                if impacted and impacted[0][1] else None
            ),
            "assessment_lines": self._assessment_lines(report, meta),
        }

    @staticmethod
    def _assessment_lines(report: dict, meta: dict) -> list[str]:
        """Deterministic template sentences over persisted values only —
        no LLM, no invented business causes."""
        s = report["summary"]
        dims = report["quality_dimensions"] or []
        lines: list[str] = []
        cov = s["evaluation_coverage"]
        cov_txt = f"{cov * 100:.1f}%" if isinstance(cov, (int, float)) else "—"
        lines.append(
            f"总体分数 Overall score: {_fmt(s['overall_score'])}；"
            f"状态 status: {s['status']}；覆盖率 coverage: {cov_txt}。"
        )
        scored = [d for d in dims if d.get("score") is not None]
        if scored:
            weakest = min(scored, key=lambda d: d["score"])
            lines.append(
                f"最弱质量维度 weakest dimension: {weakest['dimension']}"
                f"（{_fmt(weakest['score'])}）——总体分数不代表单维度质量。"
            )
        else:
            lines.append("无有效维度分数 no scored dimension —— 不作总结性判断，不填充 0。")
        if s["error_records"]:
            lines.append(
                f"执行错误记录 {s['error_records']} 条（评估执行问题，非质量结论）。"
                " (Execution errors are not quality failures.)"
            )
        gate = meta.get("quality_gate")
        if not gate:
            lines.append(
                "质量门禁未配置 Quality gate: NOT_EVALUABLE（Profile 无 quality_gate 快照）。"
            )
        else:
            lines.append(
                "质量门禁已在 Profile 配置，判定结果以 Quality Gate 页/接口为准"
                " (gate configured; verdict lives on the quality-gate endpoint)。"
            )
        return lines
