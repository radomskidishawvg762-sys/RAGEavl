"""An enabled metric with no engine backend must not be silently dropped.

Regression: `resolve_effective_pipeline` did `except KeyError: continue` for a
metric absent from the registry, and `app/api/deps.py` discarded the factory's
`skipped` list. So a profile enabling metrics whose engine is not installed (the
shipped Docker image builds without the `eval` extra, so ragas is absent) produced
zero rows for them while the run still finished `completed` — with an
overall_score computed from the remaining metrics and a snapshot claiming every
configured one.

The fix rejects at PLAN time in `resolve_profile`, which runs before any run row
is created and before the dataset row lock is taken (ADR-06: that lock is never
released, so failing later would consume the dataset version).
"""

from __future__ import annotations

import pytest

from app.core.errors import ConfigInvalidError
from app.services.run_planner import resolve_profile


def _merged(*enabled_names: str) -> dict:
    return {"metrics": {name: {"enabled": True} for name in enabled_names}}


def test_unregistered_enabled_metric_is_rejected() -> None:
    with pytest.raises(ConfigInvalidError) as ei:
        resolve_profile(_merged("no_such_metric_a"))

    err = ei.value
    assert err.code == "BIZ_CONFIG_INVALID"
    assert err.http_status == 409
    assert err.context["metrics"] == ["no_such_metric_a"]
    assert err.context["reason"] == "not_registered"


def test_rejection_names_every_offender_at_once() -> None:
    """Collected, not short-circuited: the user must see all four at once instead
    of fixing them one 409 at a time."""
    names = ["no_such_metric_a", "no_such_metric_b", "no_such_metric_c"]
    with pytest.raises(ConfigInvalidError) as ei:
        resolve_profile(_merged(*names))

    assert sorted(ei.value.context["metrics"]) == sorted(names)


def test_registered_metrics_still_resolve() -> None:
    """The new guard must not reject metrics the registry does know, so it cannot
    firehose every profile. The registry is normally filled at app startup
    (app/main.py calls bootstrap_metrics)."""
    from app.metrics.bootstrap import bootstrap_metrics

    bootstrap_metrics()

    plan = resolve_profile(_merged("temporal_consistency"))
    assert plan is not None


def test_all_metrics_disabled_is_rejected_at_plan_time() -> None:
    """A run with nothing to evaluate must not be CREATED.

    Without this guard the run was created and then finished `failed` with
    error_summary = NULL — the service counted every record as an error, but
    error_details is only appended on the exception branch, so there was no cause
    to read anywhere — while ADR-06 had already locked the dataset forever.
    """
    merged = {
        "metrics": {
            "temporal_consistency": {"enabled": False},
            "numerical_consistency": {"enabled": False},
        }
    }

    with pytest.raises(ConfigInvalidError) as ei:
        resolve_profile(merged)

    assert ei.value.code == "BIZ_CONFIG_INVALID"
    assert ei.value.http_status == 409
    assert ei.value.context["enabled_metrics"] == []


def test_one_enabled_metric_is_still_enough() -> None:
    """The guard must not reject a profile that kept at least one metric on."""
    from app.metrics.bootstrap import bootstrap_metrics

    bootstrap_metrics()

    plan = resolve_profile(
        {
            "metrics": {
                "temporal_consistency": {"enabled": True},
                "numerical_consistency": {"enabled": False},
            }
        }
    )

    assert plan.enabled_metrics == ["temporal_consistency"]
