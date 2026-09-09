"""Level-1 experiment: deterministic Integrity-engine discrimination test.

Controlled contamination over TAT-QA arithmetic answers. For each sample the
reference answer is the official numeric answer; the candidate answer is either
the clean answer (= reference) or a controlled perturbation. IntegrityEngine
must classify clean as match and contaminated as mismatch.

Perturbations (all numeric, unit-preserving unless stated):
  - multiply_by_10:   value * 10
  - multiply_by_15:   value * 1.5 (non-integer multiple, more subtle)
  - scale_confusion:  declared scale swapped (million <-> thousand, percent vs
                      decimal), which changes how the engine compares units
  - sign_flip:        value * -1

Output: discrimination rate, confusion matrix, per-metric verdicts.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.domain.schemas import EvaluationRecord
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw" / "tatqa_dataset_dev.json"
OUTPUT = ROOT / "processed" / "tatqa_contamination_l1.json"

SCALE_SWAP = {
    "million": "thousand",
    "thousand": "million",
    "billion": "million",
    "percent": "percent",  # percent -> percent: value kept, unit class same? handled below
}

N_CLEAN = 10
N_POLLUTED_PER = 10  # per perturbation type


def _load_samples() -> list[dict[str, Any]]:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    samples: list[dict[str, Any]] = []
    for item in raw:
        for q in item.get("questions", []):
            if not isinstance(q.get("answer"), (int, float)):
                continue
            if q.get("answer_type") not in ("arithmetic", "span"):
                continue
            scale = q.get("scale") or ""
            samples.append({
                "question": q["question"],
                "answer": q["answer"],
                "scale": scale,
                "uid": q["uid"],
            })
    return samples


def _perturb(sample: dict[str, Any], kind: str) -> tuple[float, str]:
    value = float(sample["answer"])
    if kind == "clean":
        return value, sample["scale"]
    if kind == "multiply_by_10":
        return value * 10, sample["scale"]
    if kind == "multiply_by_15":
        return value * 1.5, sample["scale"]
    if kind == "sign_flip":
        return -value, sample["scale"]
    if kind == "scale_confusion":
        swapped = SCALE_SWAP.get(sample["scale"], "")
        return value, swapped
    raise ValueError(kind)


def _fmt(value: float, scale: str) -> str:
    """Format in the engine's parseable space.

    For percent-type answers emit `%`; for CNY-magnitude scales emit the English
    magnitude word (million/thousand/billion — now supported by the engine).
    A blank scale emits a bare number.
    """
    text = str(int(value)) if float(value).is_integer() else f"{value:.6g}"
    if scale == "percent":
        return f"{text}%"
    if scale in ("million", "thousand", "billion", "trillion"):
        return f"{text} {scale}"
    return text


def _run(record: EvaluationRecord, metric: str) -> dict[str, Any]:
    engine = IntegrityEngine()
    result = asyncio.run(engine.evaluate(record, [metric], EvalParams(extra={"tolerance": {}})))[0]
    basis = result.comparison_basis
    return {
        "comparison_type": basis.comparison_type if basis else None,
        "score": result.score,
        "passed": result.passed,
    }


def main() -> None:
    samples = _load_samples()
    perturbations = ["clean", "multiply_by_10", "multiply_by_15", "sign_flip", "scale_confusion"]

    rows: list[dict[str, Any]] = []
    results_by_kind: dict[str, list[bool]] = {k: [] for k in perturbations}

    for idx, sample in enumerate(samples):
        kind = perturbations[idx % len(perturbations)]
        value, scale = _perturb(sample, kind)
        reference = _fmt(float(sample["answer"]), sample["scale"])
        candidate = _fmt(value, scale)
        # Build candidate context so the engine has something; integrity uses
        # answer vs reference_answer only for numerical_consistency.
        record = EvaluationRecord(
            id=sample["uid"],
            question=sample["question"],
            contexts=[candidate],
            answer=candidate,
            reference_answer=reference,
            reference_contexts=[reference],
        )
        out = _run(record, "numerical_consistency")
        # expected verdict
        expected_clean = kind == "clean"
        actual_clean = out["comparison_type"] == "match"
        is_correct = actual_clean == expected_clean
        results_by_kind[kind].append(actual_clean)
        rows.append({
            "uid": sample["uid"],
            "question": sample["question"],
            "kind": kind,
            "reference": reference,
            "candidate": candidate,
            "scale_note": sample["scale"] if kind != "scale_confusion" else f"{sample['scale']}->{_perturb(sample,'scale_confusion')[1]}",
            "comparison_type": out["comparison_type"],
            "score": out["score"],
            "expected_clean": expected_clean,
            "actual_clean": actual_clean,
            "correct": is_correct,
        })
        if all(len(v) >= max(N_CLEAN if k == "clean" else N_POLLUTED_PER, 1) for k, v in results_by_kind.items()) and len(rows) >= 50:
            break
        if len(rows) >= 80:
            break

    polluted_correct = sum(1 for r in rows if r["kind"] != "clean" and r["correct"])
    clean_total = sum(1 for r in rows if r["kind"] == "clean")
    clean_correct = sum(1 for r in rows if r["kind"] == "clean" and r["correct"])

    result = {
        "experiment": "level1_tatqa_contamination",
        "engine": "IntegrityEngine.numerical_consistency",
        "judge": None,
        "method": "deterministic",
        "summary": {
            "total_cases": len(rows),
            "clean_cases": clean_total,
            "polluted_cases": len(rows) - clean_total,
            "clean_accuracy": round(clean_correct / clean_total, 4) if clean_total else None,
            "polluted_detection_rate": round(polluted_correct / (len(rows) - clean_total), 4) if (len(rows) - clean_total) else None,
            "overall_accuracy": round(sum(1 for r in rows if r["correct"]) / len(rows), 4),
            "per_kind": {
                k: {"n": len(v), "detected_as_polluted": sum(1 for x in v if not x)}
                for k, v in results_by_kind.items()
            },
        },
        "rows": rows,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()