"""Review frozen live scores and replay current diagnosis rules, without model calls or DB writes."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.diagnosis import DiagnosisEngine  # noqa: E402
from app.domain.schemas import EvaluationRecord, MetricResult  # noqa: E402

OUT = ROOT / "exports/judge-review-2026-10-11"
# Manually reviewed faithfulness labels for these small fixture cases, separate
# from the benchmark's numerical-consistency labels. Not a production-quality estimate.
SUPPORTED_BASELINE = {"B1-num-001", "B1-num-002", "B1-num-003", "B5-num-001", "B5-num-003", "B5-num-004"}


def main():
    data = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    cases = {c["input"]["question"]: c for c in data["cases"]}
    analysis = {"method": "Frozen API results; no metric rerun, model request or database mutation",
                "numeric_benchmark": [], "judge_calibration": {}, "diagnosis_changes": []}
    engine = DiagnosisEngine()
    for version, run in data["runs"].items():
        calibration = []
        for detail in run["details"]:
            case = cases[detail["question"]]
            record = EvaluationRecord(**{key: detail[key] for key in (
                "question", "answer", "contexts", "reference_answer", "reference_contexts")}, id=detail["record_id"])
            for raw in detail["metric_results"]:
                metric = MetricResult(**{key: raw[key] for key in (
                    "category", "score", "threshold", "passed", "metric_version", "comparison_basis", "error")},
                    record_id=record.id, metric_name=raw["name"])
                replay = engine.diagnose(record, metric)
                old = next((d for d in detail["diagnoses"] if d["related_metric"] == metric.metric_name), None)
                old_status, old_code = (old["status"], old["failure_type"]) if old else ("not_failed", None)
                new_code = replay.diagnosis.failure_type if replay.diagnosis else None
                if (old_status, old_code) != (replay.status, new_code):
                    analysis["diagnosis_changes"].append({"version": version, "case_id": case["case_id"],
                        "metric": metric.metric_name, "before": {"status": old_status, "failure_type": old_code},
                        "after": replay.model_dump()})
                if version == "baseline" and metric.metric_name == "numerical_consistency" and not case["case_id"].startswith("HTTP"):
                    expected = case["expected"]
                    ok = (raw["comparison_basis"]["comparison_type"] == expected["comparison_type"]
                          and raw["score"] == expected["score"] and replay.status == expected["diagnosis_status"])
                    if expected.get("failure_type"):
                        ok = ok and replay.diagnosis is not None and replay.diagnosis.failure_type == expected["failure_type"]
                        ok = ok and replay.diagnosis.evidence_contract == expected["evidence_contract"]
                        ok = ok and replay.diagnosis.severity == expected["severity"]
                        ok = ok and set(expected["evidence_types"]) <= {e.type for e in replay.diagnosis.evidence}
                    analysis["numeric_benchmark"].append({"case_id": case["case_id"], "matches_expected": ok,
                        "comparison_type": raw["comparison_basis"]["comparison_type"], "score": metric.score,
                        "diagnosis_status": replay.status, "failure_type": new_code})
                if metric.metric_name == "faithfulness":
                    expected = 1 if version == "fixed" or case["case_id"] in SUPPORTED_BASELINE else 0
                    calibration.append({"case_id": case["case_id"], "manual_expected": expected,
                        "actual_score": metric.score, "error": metric.error,
                        "matches_label": (metric.score >= 0.9) == bool(expected) if metric.score is not None else None})
        usable = [c for c in calibration if c["actual_score"] is not None]
        analysis["judge_calibration"][version] = {"persisted_records": len(run["details"]),
            "usable_scores": len(usable), "matches_manual_label": sum(c["matches_label"] for c in usable),
            "errors": dict(Counter(c["error"]["code"] for c in calibration if c["error"])), "cases": calibration}
    (OUT / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"numeric_matches": sum(c["matches_expected"] for c in analysis["numeric_benchmark"]),
        "numeric_cases": len(analysis["numeric_benchmark"]), "diagnosis_changes": len(analysis["diagnosis_changes"]),
        "judge": {k: {n: v for n, v in value.items() if n != "cases"} for k, value in analysis["judge_calibration"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
