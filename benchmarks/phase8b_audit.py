"""Phase 8B: Evaluation Coverage Audit + Threshold Calibration — read-only.

Classifies every record of the 50/50 run into coverage buckets:
  - failure_with_diagnosis     (detected + explained)
  - undetermined_insufficient  (ambiguous / missing reference — genuinely no basis)
  - undetermined_untriggered   (metric valid but threshold=null -> no failure trigger)
  - undetermined_no_rule       (metric detected low/bad but no diagnosis rule / not explainable)
  - not_evaluated_error        (metric error row)
  - never_reached_diagnosis    (only shown when diagnosis engine skipped)

No code changes. Output written to benchmarks/processed/phase8b_audit.json.
"""

from __future__ import annotations

import json
from pathlib import Path

RUN_DETAILS = Path(r"C:\Users\guozi\AppData\Local\Temp\opencode\run50_details.json")
GOLDEN = Path("benchmarks/processed/ragtruth_golden_qa_50.json")
OUTPUT = Path("benchmarks/processed/phase8b_audit.json")

_AMBIGUOUS_REASONS = {
    "entity": "entity not in alias table / ambiguous entity",
    "numerical": "numerical parse ambiguous / unit outside scope",
    "temporal": "relative time without anchor / interval containment",
    "unknown": "other ambiguous",
}


def _classify_metric(mr: dict) -> str:
    """Per-metric outcome classification."""
    if mr.get("status") == "error":
        return "error"
    if mr.get("score") is None:
        basis = mr.get("comparison_basis") or {}
        ctype = basis.get("comparison_type")
        if ctype == "missing_reference":
            return "undetermined_missing_reference"
        if ctype == "ambiguous":
            return "undetermined_ambiguous"
        return "undetermined_none_or_unknown"
    # has a real score
    return "scored"


def _ambiguity_reason(mr: dict) -> str | None:
    basis = mr.get("comparison_basis") or {}
    if basis.get("comparison_type") != "ambiguous":
        return None
    # reference/answer side_status + ambiguity_reason
    ref = basis.get("reference") or {}
    ans = basis.get("answer") or {}
    reasons = []
    for side, label in ((ref, "ref"), (ans, "ans")):
        status = side.get("side_status")
        if status == "ambiguous":
            cands = side.get("candidates") or []
            if cands:
                r = (cands[0] or {}).get("ambiguity_reason")
                if r:
                    reasons.append(f"{label}:{r}")
    return "; ".join(reasons) if reasons else str(basis.get("comparison_type"))


def main() -> None:
    rows = json.loads(RUN_DETAILS.read_text(encoding="utf-8"))

    coverage = {
        "failure_with_diagnosis": [],
        "undetermined_insufficient": [],   # ambiguous / missing_reference
        "undetermined_untriggered": [],    # scored but threshold null -> not a failure
        "undetermined_no_rule": [],        # scored low / bad but no rule (or judge error)
        "not_evaluated_error": [],
        "scored_no_judgment": [],          # valid score, not flagged failure (normal)
    }

    for row in rows:
        question = row["question"]
        is_failure = bool(row.get("isFailure"))
        mrs = json.loads(row["metricResults"]) if isinstance(row["metricResults"], str) else row["metricResults"]
        if not isinstance(mrs, list):
            mrs = [mrs]

        # per-metric breakdown for this record
        per_metric = {}
        for mr in mrs:
            name = mr.get("name")
            if not name:
                continue
            per_metric[name] = {
                "outcome": _classify_metric(mr),
                "score": mr.get("score"),
                "status": mr.get("status"),
                "ambiguity": _ambiguity_reason(mr),
            }

        # record-level bucket (a record is a failure if is_failure)
        if is_failure:
            coverage["failure_with_diagnosis"].append({
                "question": question, "is_failure": True, "per_metric": per_metric,
            })
            continue

        # not a failure: inspect per-metric outcomes
        ambiguous = [m for m, v in per_metric.items() if v["outcome"] == "undetermined_ambiguous"]
        missing_ref = [m for m, v in per_metric.items() if v["outcome"] == "undetermined_missing_reference"]
        errors = [m for m, v in per_metric.items() if v["outcome"] == "error"]
        scored = [m for m, v in per_metric.items() if v["outcome"] == "scored"]

        if ambiguous or missing_ref:
            coverage["undetermined_insufficient"].append({
                "question": question, "per_metric": per_metric,
                "ambiguous_metrics": ambiguous, "missing_reference_metrics": missing_ref,
            })
        elif errors:
            coverage["not_evaluated_error"].append({"question": question, "per_metric": per_metric, "error_metrics": errors})
        elif scored:
            # scored but not flagged failure -> either threshold null (untriggered)
            # or genuinely fine (scored_no_judgment). Need threshold info from profile:
            # default profile has threshold=null for all metrics -> UNTRIGGERED.
            coverage["undetermined_untriggered"].append({"question": question, "per_metric": per_metric})
        else:
            coverage["undetermined_no_rule"].append({"question": question, "per_metric": per_metric})

    # Build a summary
    def _bucket_stats(bucket: list) -> dict:
        return {"count": len(bucket), "metrics_seen": sorted({m for r in bucket for m in r["per_metric"]})}

    summary = {k: _bucket_stats(v) for k, v in coverage.items()}

    # ambiguity reason histogram (for insufficient bucket)
    reason_hist: dict[str, int] = {}
    for r in coverage["undetermined_insufficient"]:
        for _m, v in r["per_metric"].items():
            a = v.get("ambiguity")
            if a:
                key = a.split(":")[0] if ":" in a else a
                reason_hist.setdefault(key, 0)
                reason_hist[key] += 1

    result = {
        "phase": "8B",
        "note": "read-only audit; no code changes",
        "n_records": len(rows),
        "summary": summary,
        "ambiguity_reason_histogram": reason_hist,
        "buckets": coverage,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"n_records": len(rows), "summary": summary,
                      "ambiguity_reason_histogram": reason_hist}, ensure_ascii=False, indent=2))
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
