"""The launcher must consume build_engines' skip list.

`build_engines` documents "a metric is NEVER silently dropped" and returns
`(engines, skipped_metric_names)`. The only production caller used to bind that
list to `_skipped` and throw it away, so a registered metric whose ENGINE has no
builder simply vanished from the report while the run completed.

`run_planner.resolve_profile` covers the other skip path — a metric the REGISTRY
does not know — by failing the request with a 409 before any run row exists. This
file covers the engine-level path, where a run row already exists and the correct
outcome is a `failed` run with a structured error, not a quiet partial report.
"""

from __future__ import annotations

import inspect

from app.api import deps


def test_launcher_does_not_discard_the_factory_skip_list() -> None:
    """Source-level guard, matching this repo's convention for invariants that no
    runtime test exercises (cf. tests/test_fr12_pipeline_audit.py).

    The engine-level skip path is unreachable today — both registered engines
    (integrity, ragas) have builders — so a behavioural test cannot cover it.
    """
    src = inspect.getsource(deps._default_launcher)

    assert "engines, _skipped" not in src, (
        "build_engines' skipped list is bound to _skipped and discarded again"
    )
    assert "if skipped:" in src, "the launcher no longer acts on dropped metrics"


def test_launcher_raises_a_structured_error_for_dropped_metrics() -> None:
    """The error must carry a code, so mark_failed persists something actionable
    rather than a bare exception repr."""
    src = inspect.getsource(deps._default_launcher)

    assert "SYS_METRIC_BACKEND_UNAVAILABLE" in src
    assert "raise SysError(" in src
