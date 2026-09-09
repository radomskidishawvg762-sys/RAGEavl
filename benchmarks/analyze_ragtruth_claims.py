"""Deterministic numeric-claim grounding analysis over RAGTruth fixtures.

Uses the platform's own deterministic claim extraction (the same helper behind
the generation.unsupported_claim evidence path) to compare answer numeric
claims against retrieved contexts. No Judge, no reference answer, no
fabrication: non-numeric or ambiguous cases are reported as unresolved, and
RAGTruth human labels are echoed side-by-side for comparison only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.diagnosis.generation import _claims_of

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "processed" / "ragtruth_existing_qa_10.json"
OUTPUT = ROOT / "processed" / "ragtruth_claim_analysis.json"


def _label_summary(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "label_type": label.get("label_type"),
            "text": label.get("text"),
            "implicit_true": label.get("implicit_true"),
        }
        for label in labels or []
    ]


def analyze() -> dict[str, Any]:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for record in payload["records"]:
        answer = record.get("answer") or ""
        contexts = [c for c in (record.get("contexts") or []) if isinstance(c, str) and c.strip()]
        meta = record.get("metadata") or {}
        answer_claims = _claims_of(answer)
        context_claims: list[float] = []
        for context in contexts:
            for claim in _claims_of(context):
                context_claims.append(claim)
        ungrounded = [claim for claim in answer_claims if not any(abs(claim - c) <= 1e-6 * max(1.0, abs(claim), abs(c)) for c in context_claims)]
        labels = _label_summary(meta.get("hallucination_labels") or [])
        if not answer_claims:
            verdict = "no_numeric_claims_deterministic_path_unresolved"
        elif ungrounded:
            verdict = "ungrounded_numeric_claims"
        else:
            verdict = "all_numeric_claims_grounded"
        rows.append({
            "source_sample_id": meta.get("source_sample_id"),
            "model": meta.get("model"),
            "question": record.get("question"),
            "answer_numeric_claims": answer_claims,
            "grounded_numeric_claims": len(answer_claims) - len(ungrounded),
            "ungrounded_numeric_claims": ungrounded,
            "deterministic_verdict": verdict,
            "ragtruth_human_labels": labels,
        })
    summary = {
        "records": len(rows),
        "verdict_counts": {},
        "records_with_human_labels": sum(1 for r in rows if r["ragtruth_human_labels"]),
        "ungrounded_numeric_records": sum(1 for r in rows if r["ungrounded_numeric_claims"]),
        "limitation": (
            "RAGTruth hallucination spans are mostly non-numeric; the deterministic "
            "numeric-claim path resolves grounding only for reliably parsed numbers "
            "and stays unresolved otherwise. LLM-based faithfulness (Judge) is "
            "required to assess non-numeric unsupported spans."
        ),
    }
    for row in rows:
        key = row["deterministic_verdict"]
        summary["verdict_counts"][key] = summary["verdict_counts"].get(key, 0) + 1
    output = {"source_file": SOURCE.name, "summary": summary, "records": rows}
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    result = analyze()
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    for row in result["records"]:
        print(
            f"{row['source_sample_id']} {row['model']}: "
            f"claims={row['answer_numeric_claims']} "
            f"ungrounded={row['ungrounded_numeric_claims']} "
            f"-> {row['deterministic_verdict']}"
        )
