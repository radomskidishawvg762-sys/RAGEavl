"""answer_relevancy is off in the default profile. Enabling it without an
embeddings provider is rejected while resolving the profile, before a run
exists and before any Judge call."""

from __future__ import annotations

import pytest

from app.core.errors import ConfigInvalidError
from app.engines.judge import PlaceholderJudge
from app.engines.providers.openai import OpenAIJudge
from app.metrics.bootstrap import bootstrap_metrics
from app.services.config_service import ConfigService
from app.services.run_planner import resolve_profile


def _merged(*enabled: str) -> dict:
    return {"metrics": {name: {"enabled": True} for name in enabled}}


def test_default_profile_disables_answer_relevancy() -> None:
    bootstrap_metrics()
    merged = ConfigService().load()
    assert merged["metrics"]["answer_relevancy"]["enabled"] is False
    plan = resolve_profile(merged)
    assert "answer_relevancy" not in plan.enabled_metrics
    assert "faithfulness" in plan.enabled_metrics


def test_explicit_enable_without_embeddings_is_rejected_before_any_judge_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_metrics()
    calls: list[str] = []

    async def _forbidden(*_args, **_kwargs):
        calls.append("ainvoke")
        raise AssertionError("judge was called")

    def _forbidden_build(*_args, **_kwargs):
        calls.append("build_judge")
        raise AssertionError("judge was built")

    monkeypatch.setattr(PlaceholderJudge, "ainvoke", _forbidden)
    monkeypatch.setattr(OpenAIJudge, "ainvoke", _forbidden)
    monkeypatch.setattr("app.engines.judge.build_judge", _forbidden_build)
    monkeypatch.setattr("app.engines.factory.build_engines", _forbidden_build)

    with pytest.raises(ConfigInvalidError) as ei:
        resolve_profile(_merged("faithfulness", "answer_relevancy"))

    err = ei.value
    assert err.code == "BIZ_CONFIG_INVALID"
    assert err.context["reason"] == "embeddings_not_configured"
    assert err.context["metric"] == "answer_relevancy"
    assert "embeddings" in err.detail
    assert calls == []


def test_explicit_enable_is_allowed_when_embeddings_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_metrics()
    monkeypatch.setattr("app.engines.judge.embeddings_available", lambda: True)
    plan = resolve_profile(_merged("answer_relevancy"))
    assert plan.enabled_metrics == ["answer_relevancy"]
