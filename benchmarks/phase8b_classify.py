"""Phase 8B final classification — aligns report-level undetermined items to
their per-metric cause, answering the four coverage questions.

Read-only. Output: benchmarks/processed/phase8b_coverage_classification.json
"""

from __future__ import annotations

import json
from pathlib import Path

REPORT = Path(r"C:\Users\guozi\AppData\Local\Temp\opencode\run_full_report.json")
DETAILS = Path(r"C:\Users\guozi\AppData\Local\Temp\opencode\run50_details_rid.json")
OUTPUT = Path("benchmarks/processed/phase8b_coverage_classification.json")


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    details = json.loads(DETAILS.read_text(encoding="utf-8"))

    # record_id -> per-metric outcome map
    rec_metrics = {}
    for row in details:
        mrs = json.loads(row["metricResults"]) if isinstance(row["metricResults"], str) else row["metricResults"]
        if not isinstance(mrs, list):
            mrs = [mrs]
        rec_metrics[row["recordId"]] = {m.get("name"): m for m in mrs if m.get("name")}
    question_by_rid = {row["recordId"]: row["question"] for row in details}

    undetermined = report["undetermined"]  # diagnosis-level items
    failures = report["failures"]

    def classify(rid: str, metric: str) -> dict:
        mr = (rec_metrics.get(rid) or {}).get(metric)
        if not mr:
            return {"bucket": "no_metric_row", "reason": None}
        status = mr.get("status")
        basis = mr.get("comparison_basis") or {}
        score = mr.get("score")
        if status == "error":
            return {"bucket": "not_evaluated_error", "reason": (mr.get("error") or {}).get("code")}
        if score is None:
            ctype = basis.get("comparison_type")
            if ctype == "missing_reference":
                return {"bucket": "undetermined_true_insufficient", "reason": "missing_reference"}
            if ctype == "ambiguous":
                ref = basis.get("reference") or {}
                ans = basis.get("answer") or {}
                reasons = []
                for side, tag in ((ref, "ref"), (ans, "ans")):
                    cands = side.get("candidates") or []
                    if cands and (cands[0] or {}).get("ambiguity_reason"):
                        reasons.append(f"{tag}:{(cands[0] or {}).get('ambiguity_reason')}")
                reason = "; ".join(reasons) or "ambiguous (no side detail)"
                if ("unit outside MVP scope" in reason) or ("alias table" in reason):
                    bucket = "undetermined_scope_gap"
                else:
                    bucket = "undetermined_true_insufficient"
                return {"bucket": bucket, "reason": reason}
            return {"bucket": "undetermined_no_score_no_type", "reason": ctype}
        passed = mr.get("passed")
        if passed is None:
            return {"bucket": "scored_threshold_null_untriggered",
                    "reason": f"score={score} threshold=null passed=null", "score": score}
        if passed is False:
            return {"bucket": "scored_threshold_failure", "reason": f"score={score}", "score": score}
        return {"bucket": "scored_normal", "reason": f"score={score}", "score": score}

    items = []
    for u in undetermined:
        rid = u.get("record_id") or ""
        items.append({
            "record_id": rid,
            "question": question_by_rid.get(rid),
            "metric": u.get("related_metric"),
            **classify(rid, u.get("related_metric") or ""),
        })

    for f in failures:
        rid = f.get("record_id") or ""
        items.append({
            "record_id": rid,
            "question": question_by_rid.get(rid),
            "metric": f.get("related_metric"),
            "failure_type": f.get("failure_type"),
            "bucket": "failure_with_diagnosis",
            "reason": f.get("root_cause"),
        })

    from collections import Counter
    buckets = Counter(i["bucket"] for i in items)

    result = {
        "phase": "8B",
        "n_undetermined_report_items": len(undetermined),
        "n_failure_report_items": len(failures),
        "n_classified_items": len(items),
        "bucket_counts": dict(buckets),
        "items": items,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"n_undetermined": len(undetermined), "n_failures": len(failures),
                      "bucket_counts": dict(buckets)}, ensure_ascii=False, indent=2))
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()