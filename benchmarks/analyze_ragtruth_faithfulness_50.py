"""Golden-standard alignment: RAGTruth human hallucination labels vs faithfulness.

Inputs:
  - benchmarks/processed/ragtruth_golden_qa_50.json (labels, questions)
  - run50_scores.json  (per-question faithfulness from the live run)

Outputs:
  - benchmarks/processed/ragtruth_faithfulness_50_gold_align.json
  - stdout summary: alignment rate, correlation, label-count vs score trend.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "processed" / "ragtruth_golden_qa_50.json"
SCORES = Path(r"C:\Users\guozi\AppData\Local\Temp\opencode\run50_scores.json")
OUTPUT = ROOT / "processed" / "ragtruth_faithfulness_50_gold_align.json"


def main() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["records"]
    scores_raw = json.loads(SCORES.read_text(encoding="utf-8"))
    scores = {row["question"]: row for row in scores_raw}

    rows = []
    for rec in golden:
        question = rec["question"]
        score_row = scores.get(question) or {}
        labels = rec["metadata"].get("hallucination_labels") or []
        rows.append({
            "question": question,
            "model": rec["metadata"].get("model"),
            "source_id": rec["metadata"].get("source_id"),
            "faithfulness": score_row.get("score"),
            "status": score_row.get("status"),
            "label_count": len(labels),
            "label_types": sorted({label.get("label_type") for label in labels}),
        })

    scored = [r for r in rows if r["faithfulness"] is not None]

    # Direction consistency: lower faithfulness should co-occur with labels.
    # We treat every scored record as "has hallucination labels" (RAGTruth QA
    # split here is fully labelled), so measure whether label severity / count
    # correlates with lower scores instead of binary detection.
    import statistics

    by_label_count = {}
    for r in scored:
        by_label_count.setdefault(r["label_count"], []).append(r["faithfulness"])
    label_trend = {
        str(k): {"count": len(v), "mean_score": round(statistics.mean(v), 4)}
        for k, v in sorted(by_label_count.items())
    }

    # Spearman rank correlation between label_count and score.
    xs = [r["label_count"] for r in scored]
    ys = [r["faithfulness"] for r in scored]
    xr = {v: i for i, v in enumerate(sorted(set(xs)))}
    yr = {v: i for i, v in enumerate(sorted(set(ys)))}
    rx = [xr[v] for v in xs]
    ry = [yr[v] for v in ys]
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / (n - 1)
    sx = (sum((a - mx) ** 2 for a in rx) / (n - 1)) ** 0.5
    sy = (sum((b - my) ** 2 for b in ry) / (n - 1)) ** 0.5
    spearman = cov / (sx * sy) if sx and sy else None

    # 2x2 using a threshold-free view: label_count>0 is always true here, so
    # report distribution of scores on labelled records and the fraction of
    # labelled records scoring below the median (proxy for detection alignment).
    median = statistics.median(ys)
    below_median_labelled = sum(1 for r in scored if r["faithfulness"] < median)

    result = {
        "run": "b8759669-4352-432f-849c-f5cfcb3db570",
        "judge_model": "glm-5.3-flash",
        "summary": {
            "total_records": len(rows),
            "scored": len(scored),
            "errors": len(rows) - len(scored),
            "mean_faithfulness": round(statistics.mean(ys), 4),
            "median_faithfulness": round(median, 4),
            "spearman_label_count_vs_score": round(spearman, 4) if spearman is not None else None,
            "labelled_below_median": below_median_labelled,
            "scored_records_all_labelled": True,
            "note": "RAGTruth QA split is fully labelled; binary precision/recall is not "
                    "meaningful here, so we report label-count vs score correlation and "
                    "trend instead.",
        },
        "label_count_trend": label_trend,
        "records": rows,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("label_count_trend:", json.dumps(label_trend, ensure_ascii=False))
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
