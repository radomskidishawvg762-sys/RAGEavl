"""Level-2 experiment: entity + temporal Integrity-engine discrimination.

Same methodology as level 1: controlled clean vs contaminated comparisons,
deterministic, no Judge.

Entity (alias-table driven):
  - clean_same / clean_alias  -> expect match
  - polluted_entity (other canonical) -> expect entity_mismatch
  - polluted_unknown (not in table)   -> expect non-match (ambiguous; honest, no guess)

Temporal (interval based):
  - clean_same / clean_equivalent     -> expect match
  - polluted year +/- / quarter swap / half swap -> expect temporal_mismatch
  - contained granularity (year vs Q1) -> expect non-match (ambiguous)
  - relative without anchor            -> expect non-match (ambiguous)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.metrics.integrity.comparison import (
    compare_entity,
    compare_temporal,
)
from app.metrics.integrity.entity import normalize_entity
from app.metrics.integrity.temporal import normalize_temporal

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "processed" / "entity_temporal_contamination_l2.json"

ALIASES = {
    "中国平安": "中国平安",
    "平安": "中国平安",
    "Ping An": "中国平安",
    "招商银行": "招商银行",
    "招行": "招商银行",
}

ENTITY_CASES: list[dict[str, Any]] = [
    {"kind": "clean_same", "ref": "中国平安", "ans": "中国平安", "expect": "match"},
    {"kind": "clean_alias", "ref": "中国平安", "ans": "平安", "expect": "match"},
    {"kind": "polluted_entity", "ref": "中国平安", "ans": "招商银行", "expect": "mismatch"},
    {"kind": "polluted_entity", "ref": "招商银行", "ans": "中国平安", "expect": "mismatch"},
    {"kind": "polluted_unknown", "ref": "中国平安", "ans": "某某公司", "expect": "non_match"},
]

TEMPORAL_CASES: list[dict[str, Any]] = [
    {"kind": "clean_same", "ref": "2024年", "ans": "2024年", "expect": "match"},
    {"kind": "clean_equivalent", "ref": "2024年", "ans": "2024年度", "expect": "match"},
    {"kind": "polluted_year_minus1", "ref": "2024年", "ans": "2023年", "expect": "mismatch"},
    {"kind": "polluted_year_plus1", "ref": "2024年", "ans": "2025年", "expect": "mismatch"},
    {"kind": "polluted_quarter", "ref": "2024Q1", "ans": "2024Q2", "expect": "mismatch"},
    {"kind": "polluted_half", "ref": "2024年上半年", "ans": "2024年下半年", "expect": "mismatch"},
    {"kind": "granularity_contained", "ref": "2024年", "ans": "2024Q1", "expect": "non_match"},
    {"kind": "relative_no_anchor", "ref": "2024年", "ans": "去年", "expect": "non_match"},
    {"kind": "polluted_fy_mismatch", "ref": "FY2024", "ans": "FY2023", "expect": "mismatch"},
]


def _run_entity(case: dict[str, Any]) -> dict[str, Any]:
    ref = normalize_entity(case["ref"], alias_table=ALIASES)
    ans = normalize_entity(case["ans"], alias_table=ALIASES)
    out = compare_entity(ref, ans)
    return {
        "ref": case["ref"], "ans": case["ans"],
        "ref_canonical": ref.canonical, "ans_canonical": ans.canonical,
        "ref_status": ref.parse_status, "ans_status": ans.parse_status,
        "comparison_type": out.comparison_type, "score": out.score,
    }


def _run_temporal(case: dict[str, Any]) -> dict[str, Any]:
    ref = normalize_temporal(case["ref"])
    ans = normalize_temporal(case["ans"])
    out = compare_temporal(ref, ans)
    return {
        "ref": case["ref"], "ans": case["ans"],
        "ref_interval": [ref.interval_start, ref.interval_end],
        "ans_interval": [ans.interval_start, ans.interval_end],
        "ref_status": ref.parse_status, "ans_status": ans.parse_status,
        "comparison_type": out.comparison_type, "score": out.score,
    }


def _classify(comparison_type: str, expect: str) -> bool:
    if expect == "match":
        return comparison_type == "match"
    if expect == "mismatch":
        return comparison_type.endswith("mismatch")
    if expect == "non_match":
        return comparison_type == "ambiguous"
    return False


def main() -> None:
    entity_rows, temporal_rows = [], []
    for case in ENTITY_CASES:
        actual = _run_entity(case)
        entity_rows.append({**case, **actual, "correct": _classify(actual["comparison_type"], case["expect"])})
    for case in TEMPORAL_CASES:
        actual = _run_temporal(case)
        temporal_rows.append({**case, **actual, "correct": _classify(actual["comparison_type"], case["expect"])})

    def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "total": len(rows),
            "correct": sum(1 for r in rows if r["correct"]),
            "accuracy": round(sum(1 for r in rows if r["correct"]) / len(rows), 4),
            "per_kind": {
                k: {"n": sum(1 for r in rows if r["kind"] == k),
                    "correct": sum(1 for r in rows if r["kind"] == k and r["correct"]),
                    "comparison_types": sorted({r["comparison_type"] for r in rows if r["kind"] == k})}
                for k in sorted({r["kind"] for r in rows})
            },
        }

    result = {
        "experiment": "level2_entity_temporal_contamination",
        "method": "deterministic",
        "entity": {"cases": entity_rows, "stats": _stats(entity_rows)},
        "temporal": {"cases": temporal_rows, "stats": _stats(temporal_rows)},
        "overall_accuracy": round(
            (sum(1 for r in entity_rows if r["correct"]) + sum(1 for r in temporal_rows if r["correct"]))
            / (len(entity_rows) + len(temporal_rows)), 4),
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("ENTITY", json.dumps(result["entity"]["stats"], ensure_ascii=False, indent=2))
    print("TEMPORAL", json.dumps(result["temporal"]["stats"], ensure_ascii=False, indent=2))
    print("overall_accuracy", result["overall_accuracy"])
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()