"""Shared scan for the "frontend must not compute business decisions" invariant.

The frontend may RENDER backend-computed decisions — a regression verdict, a
quality-gate status, an overall_score — status.ts maps the received value to a
display label. It must never DERIVE them. Deriving requires computation operators
(delta/epsilon, a score-or-threshold comparison, per-category tally, reassigning
overall_score). These tests forbid the *operators*, not the enum strings a renderer
legitimately switches on; comments are stripped so doc text is not a false positive.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REGRESSION_PATTERNS = [
    # Derivation only (Freeze Gate refinement): since Phase 2 the frontend
    # legitimately READS m.delta / c.improvement_count etc. to RENDER backend
    # values — this docstring's own intent ("forbid the operators, not the
    # strings a renderer legitimately switches on"). Patterns below forbid
    # assignments with arithmetic RHS and arithmetic operand use; pure reads
    # (m.delta, data-delta={delta}, delta.toFixed) stay legal.
    r"(?<![.\w-])delta\s*=[^=].*[+\-*]",   # delta = candidate - baseline ('/' excluded: JSX '/>' poison)
    r"(?<![.\w-])delta\s*[+\-*/]",         # delta * 100 (operand use)
    r"(?<![.\w-])epsilon\s*=[^=].*[+\-*]",
    r"(?<![.\w-])epsilon\s*[+\-*/]",
    r"\boverall_score\s*=[^=]",            # recompute an overall score
    r"\boverallScore\s*=[^=]",
    r"\bscore\s*[<>]",                     # compare a score to derive a verdict
    r"(?<![.\w-])(?:improvement_count|regression_count|stable_count)\s*=[^=].*[-+*/]",
    r"(?<![.\w-])(?:improvement_count|regression_count|stable_count)\s*[+\-*/]",
]

GATE_PATTERNS = [
    r"\bscore\s*[<>]",            # threshold-compare to derive PASS/FAIL
    r"\bthreshold\s*[<>]=?",
    r"[<>]=?\s*threshold",
]


def strip_comments(src: str) -> str:
    """Remove block + line comments and rewrite arrow-functions so a stray `=>`
    never reads as a `>` comparison operator in the patterns below."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src.replace("=>", " → ")


def frontend_source_files() -> list[Path]:
    """All non-api frontend .ts/.tsx files. The api/ layer is the sanctioned
    snake->camel mapping boundary and is excluded from the invariant."""
    frontend = ROOT / "frontend" / "src"
    return sorted(
        p for p in frontend.rglob("*")
        if p.suffix in {".ts", ".tsx"} and "api" not in p.parts
    )


def all_frontend_source_files() -> list[Path]:
    """Every frontend .ts/.tsx file, including api/ — for checks that apply to
    the whole client (e.g. the page_size cap), not just business components."""
    frontend = ROOT / "frontend" / "src"
    return sorted(p for p in frontend.rglob("*") if p.suffix in {".ts", ".tsx"})


MAX_PAGE_SIZE = 100  # backend pagination cap (Query(..., le=100))


def frontend_page_size_violations() -> list[tuple[str, int, str]]:
    """`[(relpath, size, matched)]` for any frontend request whose page_size /
    pageSize numeric literal exceeds the backend cap. The api/ layer is
    included — its endpoint defaults (20/50) and MAX_PAGE_SIZE (100) are all
    legal — so the entire source set is proven free of over-cap pagination."""
    out: list[tuple[str, int, str]] = []
    for path in all_frontend_source_files():
        src = strip_comments(path.read_text(encoding="utf-8"))
        for pat in (r"page_size\s*[=:]\s*(\d+)", r"pageSize\s*[=:]\s*(\d+)"):
            for m in re.finditer(pat, src):
                val = int(m.group(1))
                if val > MAX_PAGE_SIZE:
                    out.append((path.relative_to(ROOT).as_posix(), val, m.group(0)))
    return out


def frontend_compute_violations(patterns: list[str] | None = None) -> list[tuple[str, str, str]]:
    """Return `[(relpath, pattern, matched)]` for any non-api frontend file whose
    code (comments stripped) uses a business-computation operator."""
    patterns = patterns or REGRESSION_PATTERNS + GATE_PATTERNS
    violations: list[tuple[str, str, str]] = []
    for path in frontend_source_files():
        src = strip_comments(path.read_text(encoding="utf-8"))
        for pat in patterns:
            m = re.search(pat, src)
            if m:
                violations.append((path.relative_to(ROOT).as_posix(), pat, m.group(0)))
    return violations


def frontend_enum_files(tokens: list[str]) -> set[str]:
    """Files whose (comment-stripped) code mentions any given enum string."""
    out: set[str] = set()
    for path in frontend_source_files():
        src = strip_comments(path.read_text(encoding="utf-8"))
        if any(t in src for t in tokens):
            out.add(path.relative_to(ROOT).as_posix())
    return out
