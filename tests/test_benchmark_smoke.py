"""T-18 — benchmark smoke test (a diagnostic guard, NOT a quality gate).

Validates the internal-benchmark dataset schema and runs the full benchmark.
Per §十 a failing case is a REPORT, not a test failure — so no pass-rate is
asserted here; the test only guarantees the benchmark stays runnable and that
every case is executed.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.runner import run_benchmark

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "benchmarks" / "v0.1" / "cases.json"
KINDS = {"integrity", "analysis", "gate"}
REQUIRED = ("case_id", "category", "kind", "input", "expected")


def _load() -> dict:
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def test_dataset_schema_is_valid() -> None:
    data = _load()
    assert data["benchmark_version"].startswith("benchmark-v")
    assert isinstance(data["cases"], list) and data["cases"]
    for case in data["cases"]:
        for field in REQUIRED:
            assert field in case, f"{case.get('case_id')} missing {field}"
        assert case["kind"] in KINDS, case["case_id"]
        assert case["case_id"] and case["category"]
        if case["kind"] == "integrity":
            assert case["metric"] in {
                "numerical_consistency", "temporal_consistency", "entity_consistency",
            }, case["case_id"]


def test_benchmark_runs_and_reports_accuracy() -> None:
    data = _load()
    report = run_benchmark(data["cases"])
    assert report["total"] == len(data["cases"])  # every case executed
    assert report["passed"] + report["failed"] == report["total"]
    assert report["accuracy"] is not None
    print("benchmark report:", report)
