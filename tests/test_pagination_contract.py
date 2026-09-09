"""T-18 pagination-contract regression — items 6 & 8 of the mandated set.

Items 1-5 and 7 (fetchAllPages unit tests + Workbench no-truncation) live in the
frontend Vitest suite (frontend/tests/pagination.test.tsx). Items 6 (no over-cap
page_size in frontend source) and 8 (no frontend business recomputation) are
*source* invariants over the frontend/src tree, enforced here in line with the
existing frontend_scan module.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.frontend_scan import (
    GATE_PATTERNS,
    MAX_PAGE_SIZE,
    REGRESSION_PATTERNS,
    frontend_compute_violations,
    frontend_page_size_violations,
)

ROOT = Path(__file__).resolve().parents[1]


def test_6_no_over_cap_page_size_in_frontend_source() -> None:
    violations = frontend_page_size_violations()
    assert violations == [], [
        f"{path} requests page_size={size} (> {MAX_PAGE_SIZE}) [{matched!r}]"
        for path, size, matched in violations
    ]

    # The literal the defect shipped with must not reappear anywhere.
    frontend = ROOT / "frontend" / "src"
    for path in frontend.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        src = path.read_text(encoding="utf-8")
        for forbidden in ("page_size=200", "pageSize:200", "pageSize: 200",
                          "page_size:200", "page_size: 200"):
            assert forbidden not in src, f"{path}: hardcoded over-cap pagination"


def test_8_fetch_all_pages_is_pure_and_hooks_do_not_recompute() -> None:
    # fetchAllPages is the sanctioned pagination helper: it only concatenates
    # pages, so it must contain no aggregation/recomputation operators.
    client_src = (ROOT / "frontend/src/api/client.ts").read_text(encoding="utf-8")
    for sentinel in (".reduce(", ".toFixed(", "Math."):
        assert sentinel not in client_src, f"fetchAllPages recomputes: {sentinel}"

    # The two pagination-consuming hooks read backend verdicts; they never
    # assign overall_score themselves.
    for rel in ("frontend/src/hooks/useEvaluationWorkspace.ts",
                "frontend/src/hooks/useEvaluations.ts"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"\boverallScore\s*=", src), rel
        assert not re.search(r"\boverall_score\s*=", src), rel

    # Belt-and-suspenders: no non-api frontend file derives any business value.
    violations = frontend_compute_violations(REGRESSION_PATTERNS + GATE_PATTERNS)
    assert violations == [], [
        f"{path} derives a business value ({pat!r} -> {matched!r})"
        for path, pat, matched in violations
    ]
