"""RAGEval Studio Internal Benchmark v0.1 — CLI entry.

Usage:
    python scripts/run_benchmark.py
    python scripts/run_benchmark.py --cases benchmarks/v0.1/cases.json
    python scripts/run_benchmark.py --json     # raw JSON report

Renders the aggregate report from benchmarks.runner.run_benchmark(). This is a
DIAGNOSTIC tool — a failing case is reported, never silently patched (§十).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import runner  # noqa: E402


def _fmt_ratio(acc: float | None) -> str:
    return "n/a" if acc is None else f"{acc * 100:.1f}%"


def _print_report(r: dict) -> None:
    print(f"RAGEval Studio Internal Benchmark {r['benchmark_version']}")
    print(f"  total={r['total']}  passed={r['passed']}  failed={r['failed']}"
          f"  accuracy={_fmt_ratio(r['accuracy'])}")
    print("\nper-category accuracy:")
    for cat, b in r["by_category"].items():
        print(f"  {cat:24s} {b['passed']}/{b['total']}  {_fmt_ratio(b['accuracy'])}")
    print("\nper-metric accuracy (integrity):")
    for m, b in r["by_metric"].items():
        print(f"  {m:28s} {b['passed']}/{b['total']}  {_fmt_ratio(b['accuracy'])}")
    print(f"\ndiagnosis accuracy:          {_fmt_ratio(r['diagnosis_accuracy'])}")
    print(f"evidence-contract accuracy:  {_fmt_ratio(r['evidence_contract_accuracy'])}")
    print(f"compare accuracy:            {_fmt_ratio(r['compare_accuracy'])}")
    print(f"regression accuracy:         {_fmt_ratio(r['regression_accuracy'])}")
    print(f"quality-gate accuracy:       {_fmt_ratio(r['quality_gate_accuracy'])}")
    if r["failed_cases"]:
        print("\nfailed cases:")
        for f in r["failed_cases"]:
            print(f"  {f['case_id']} ({f['category']}):")
            for mm in f["mismatches"]:
                print(f"    - {mm}")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGEval Studio Internal Benchmark v0.1")
    parser.add_argument("--cases", default=str(ROOT / "benchmarks" / "v0.1" / "cases.json"))
    parser.add_argument("--json", action="store_true", help="emit raw JSON report")
    args = parser.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        data = json.load(f)
    version = data.get("benchmark_version")
    if version != runner.BENCHMARK_VERSION:
        print(f"benchmark version mismatch: dataset={version!r} runner={runner.BENCHMARK_VERSION!r}",
              file=sys.stderr)
        return 2

    report = runner.run_benchmark(data["cases"])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
