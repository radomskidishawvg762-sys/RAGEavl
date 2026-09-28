"""T-14A tests — Judge configuration, Provider Adapter, secret isolation,
error tri-state, RAGAS real-path entry, reproducibility.

12 mandated points + optional golden smoke (skipped without a real key).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.errors import ExtJudgeUnavailableError, JudgeNotConfiguredError
from app.domain.schemas import EvaluationRecord
from app.engines.factory import build_engines
from app.engines.judge import (
    JudgeConfig,
    PlaceholderJudge,
    build_judge,
    judge_config_from_merged,
    judge_config_from_settings,
)
from app.engines.providers.openai import OpenAIJudge
from app.engines.ragas import METRIC_VERSION, RagasEngine, RagasScorer
from app.services.run_planner import build_reproducibility_meta

SECRET = "sk-T14A-SECRET-VALUE"


@pytest.fixture(scope="module", autouse=True)
def _bootstrap_metrics() -> None:
    """Code-level metric registration (app.main does this at startup)."""
    from app.metrics.bootstrap import bootstrap_metrics

    bootstrap_metrics()


_RECORD = EvaluationRecord(
    id="r1", question="q", contexts=["ctx"], answer="a",
    reference_answer="ref", reference_contexts=["ref ctx"],
)


def _merged_with_judge(**overrides) -> dict:
    judge = {
        "provider": "openai", "model": "gpt-4o-mini", "model_version": "gpt-4o-mini-2024-07-18",
        "temperature": 0.0, "max_tokens": 1024, "timeout": 30, "retry": 3,
    }
    judge.update(overrides)
    return {"system": {"judge": judge}, "metrics": {}}


def _configured_cfg() -> JudgeConfig:
    return JudgeConfig(provider="openai", model="gpt-4o-mini",
                       model_version="v1", api_key=SecretStr(SECRET))


# ---- 1: config loading ----

def test_1_judge_config_loaded_from_merged() -> None:
    cfg = judge_config_from_merged(_merged_with_judge(temperature=0.1, max_tokens=512, timeout=15, retry=5))
    assert cfg.provider == "openai" and cfg.model == "gpt-4o-mini"
    assert (cfg.temperature, cfg.max_tokens, cfg.timeout, cfg.retry) == (0.1, 512, 15, 5)
    assert cfg.model_version == "gpt-4o-mini-2024-07-18"


def test_1b_settings_fallback_when_merged_empty() -> None:
    cfg = judge_config_from_merged({})
    assert cfg.provider == (settings.judge_provider or "")


# ---- 2-4: secret isolation ----

def test_2_secret_never_logged(caplog) -> None:
    judge = build_judge(_configured_cfg())
    assert isinstance(judge, OpenAIJudge)

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def chat_completions_create(self, **kw):  # noqa: ARG002
            raise RuntimeError("boom")

    async def _drive():
        judge._get_client = lambda: _FailingClient()  # type: ignore[assignment]
        with pytest.raises(ExtJudgeUnavailableError):
            await judge.ainvoke("p")

    with caplog.at_level(logging.DEBUG):
        import asyncio

        asyncio.run(_drive())
    assert SECRET not in caplog.text


def test_3_secret_not_in_db_boundary_describe() -> None:
    judge = build_judge(_configured_cfg())
    desc = judge.describe()
    assert "api_key" not in desc and SECRET not in str(desc)
    cfg = _configured_cfg()
    assert "api_key" not in cfg.public_dump()
    assert "api_key" not in cfg.model_dump(include={"provider", "model"})  # explicit fields
    assert SECRET not in str(cfg.public_dump())


def test_4_secret_not_in_machine_report_reproducibility() -> None:
    meta = build_reproducibility_meta(
        config_version="x", dataset_version="v1",
        merged=_merged_with_judge(), enabled_metrics=["faithfulness"],
    )
    assert "api_key" not in meta and SECRET not in str(meta)
    # full judge fingerprint present (T-14A §七)
    for key in ("judge_provider", "judge_model", "judge_model_version",
                "judge_temperature", "judge_max_tokens", "judge_timeout", "judge_retry"):
        assert key in meta
    assert meta["judge_provider"] == "openai" and meta["judge_retry"] == 3


# ---- 5-6: adapter construction & provider errors ----

def test_5_openai_adapter_built_from_config() -> None:
    judge = build_judge(_configured_cfg())
    assert isinstance(judge, OpenAIJudge)
    assert judge.describe()["model"] == "gpt-4o-mini"


def test_5b_missing_api_key_degrades_to_placeholder() -> None:
    judge = build_judge(JudgeConfig(provider="openai", model="gpt-4o-mini"))  # no key
    assert isinstance(judge, PlaceholderJudge)


def test_6_provider_error_maps_to_ext_judge_unavailable() -> None:
    judge = build_judge(_configured_cfg())

    class _BoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def chat_completions_create(self, **kw):  # noqa: ARG002
            raise ConnectionError("upstream refused")

    async def drive():
        judge._get_client = lambda: _BoomClient()  # type: ignore[assignment]
        with pytest.raises(ExtJudgeUnavailableError) as ei:
            await judge.ainvoke("p")
        assert ei.value.code == "EXT_JUDGE_UNAVAILABLE"

    import asyncio

    asyncio.run(drive())


# ---- 7: not configured is explicit, never silent ----

def test_7_unconfigured_builds_placeholder_and_errors_distinct() -> None:
    judge = build_judge(JudgeConfig())
    assert isinstance(judge, PlaceholderJudge)

    async def drive():
        with pytest.raises(JudgeNotConfiguredError) as ei:
            await judge.ainvoke("p")
        assert ei.value.code == "BIZ_JUDGE_NOT_CONFIGURED"

    import asyncio

    asyncio.run(drive())


def test_7b_factory_never_silently_drops_ragas() -> None:
    from app.metrics.bootstrap import bootstrap_metrics

    bootstrap_metrics()  # code registration (idempotent); app.main does this too
    engines, skipped = build_engines(["faithfulness", "answer_relevancy"], judge_config=None)
    assert skipped == []  # registry-known metrics are never skipped
    assert any(type(e).__name__ == "RagasEngine" for e in engines)


# ---- 8-9: RagasEngine <-> JudgeClient, all 4 metrics enter real path ----

def test_8_ragas_engine_accepts_judgeclient() -> None:
    judge = build_judge(_configured_cfg())
    engine = RagasEngine(judge=judge)
    assert engine._judge is judge  # the JudgeClient flows into the engine
    scorer = engine._scorer_or_build()
    assert isinstance(scorer, RagasScorer)
    assert scorer._judge is judge  # scorer bound the SAME client


def test_9_four_metrics_enter_real_path_with_explicit_not_configured() -> None:
    """Real RagasScorer + bridge against a PlaceholderJudge: all 4 metrics go
    through the genuine RAGAS evaluation path and each surfaces an explicit
    BIZ_JUDGE_NOT_CONFIGURED (score=null) — 'did not run' is distinguishable."""
    engine = RagasEngine(judge=PlaceholderJudge(JudgeConfig(model="m")))
    results = asyncio_run(engine.evaluate(_RECORD, list(engine.metric_names()), params()))
    assert {r.metric_name for r in results} == {
        "faithfulness", "answer_relevancy", "context_precision", "context_recall",
    }
    for r in results:
        assert r.score is None
        assert r.error is not None
        assert r.error["code"] == "BIZ_JUDGE_NOT_CONFIGURED", r
        assert r.error.get("reason") == "judge_not_configured"


def test_9b_sys_metric_error_still_distinct() -> None:
    """Internal code bug -> SYS_METRIC_ERROR (tri-state stays separable)."""

    class _ConfiguredJudge:
        """JudgeClient protocol stub reporting configured=True."""

        async def ainvoke(self, prompt: str) -> str:  # noqa: ARG002
            return "ok"

        def describe(self) -> dict:
            return {"provider": "openai", "model": "m", "configured": True}

    class _BrokenScorer:
        async def score(self, metric_name, sample):  # noqa: ARG002
            raise ValueError("internal bug")

    engine = RagasEngine(judge=_ConfiguredJudge(), scorer=_BrokenScorer())
    results = asyncio_run(engine.evaluate(_RECORD, ["faithfulness"], params()))
    assert results[0].error["code"] == "SYS_METRIC_ERROR"


# ---- 10: metric version ----

def test_10_metric_version_is_ragas_locked() -> None:
    assert METRIC_VERSION.startswith("ragas-")
    assert "unavailable" not in METRIC_VERSION  # ragas is installed in this env


# ---- 11: reproducibility_meta (covered in test_4, extra checks here) ----

def test_11_reproducibility_has_no_secret_and_full_fingerprint() -> None:
    meta = build_reproducibility_meta(
        config_version="cfg-1", dataset_version="v3",
        merged=_merged_with_judge(), enabled_metrics=["faithfulness", "numerical_consistency"],
    )
    assert meta["metric_version"].startswith("ragas-")
    from app.engines.integrity import INTEGRITY_METRIC_VERSION

    assert INTEGRITY_METRIC_VERSION in meta["metric_version"]
    assert meta["judge_model"] == "gpt-4o-mini"
    assert meta["judge_model_version"] == "gpt-4o-mini-2024-07-18"


# ---- 12: no cross-layer SDK dependency ----

def test_12_openai_sdk_isolated_in_providers_only() -> None:
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        if "providers" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "import openai" in line or "from openai" in line:
                offenders.append(f"{path.relative_to(app_dir)}:{lineno}")
    assert offenders == []


# ---- helpers ----

def params():
    from app.engines.base import EvalParams

    return EvalParams()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


# ---- provider retry semantics (T-14A §六: transport errors retry, then EXT) ----


class _FakeCompletions:
    """Deterministic stand-in for the OpenAI SDK call path — no network."""

    def __init__(self, script: list) -> None:
        self._script = script
        self.calls = 0

    async def create(self, **_kwargs):
        outcome = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _AsyncCM:
    """Minimal async context manager — _invoke() scopes its client per call."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *exc) -> bool:
        return False


def _wire(judge: OpenAIJudge, completions: _FakeCompletions) -> None:
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    judge._get_client = lambda: _AsyncCM(client)  # type: ignore[assignment]


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])


def test_provider_retries_then_succeeds() -> None:
    import asyncio

    judge = OpenAIJudge(_configured_cfg())
    completions = _FakeCompletions(
        [RuntimeError("429 GoUsageLimitError: weekly limit"), _ok_response()]
    )
    _wire(judge, completions)
    assert asyncio.run(judge.ainvoke("p")) == "ok"
    assert completions.calls == 2


def test_provider_exhausts_retries_into_ext_judge_unavailable() -> None:
    import asyncio

    judge = OpenAIJudge(_configured_cfg())
    completions = _FakeCompletions([RuntimeError("429 GoUsageLimitError")])
    _wire(judge, completions)
    with pytest.raises(ExtJudgeUnavailableError) as ei:
        asyncio.run(judge.ainvoke("p"))
    assert "429" in str(ei.value)  # root cause stays visible in the message
    assert completions.calls == 3  # retry=3 exhausted


def test_provider_timeout_maps_to_ext_judge_unavailable() -> None:
    import asyncio

    judge = OpenAIJudge(_configured_cfg())
    completions = _FakeCompletions([TimeoutError("request timed out")])
    _wire(judge, completions)
    with pytest.raises(ExtJudgeUnavailableError):
        asyncio.run(judge.ainvoke("p"))


# ---- optional golden smoke (real model configured) ----

@pytest.mark.skipif(
    settings.judge_api_key is None or not settings.judge_provider,
    reason="real judge not configured (JUDGE_API_KEY/JUDGE_PROVIDER unset)",
)
def test_golden_smoke_single_record() -> None:
    """Minimal golden sample against the real provider — one record, one metric
    (T-14A §九). Verifies the RagasEngine -> JudgeClient -> Provider -> LLM chain
    end-to-end. Never a large dataset."""
    from app.engines.base import EvalParams

    engine = RagasEngine(judge=build_judge(judge_config_from_settings()))
    record = EvaluationRecord(
        id="golden", question="2024年中国平安营业收入是多少？",
        contexts=["2024年中国平安营业收入约1.2万亿元。"],
        answer="1.2万亿元", reference_answer="约1.2万亿元",
    )
    result = asyncio_run(engine.evaluate(record, ["faithfulness"], EvalParams()))[0]
    # either a real score (chain worked) or a structured EXT error (network) —
    # but NEVER a fabricated score and NEVER a silent missing row
    if result.score is None:
        assert result.error is not None
    else:
        assert 0.0 <= result.score <= 1.0


# ---- extra headers: some gateways refuse calls without a routing header ----


def test_judge_extra_headers_parsed_from_settings(monkeypatch) -> None:
    """opencode zen answers 400 MissingSessionID unless x-opencode-session is sent,
    which made a correctly-configured judge unusable and every RAGAS metric error
    out on every record."""
    from app.core.config import settings
    from app.engines.judge import judge_config_from_settings

    monkeypatch.setattr(settings, "judge_extra_headers", '{"x-opencode-session": "rageval"}', raising=False)

    assert judge_config_from_settings().extra_headers == {"x-opencode-session": "rageval"}


def test_judge_extra_headers_absent_is_none(monkeypatch) -> None:
    from app.core.config import settings
    from app.engines.judge import judge_config_from_settings

    monkeypatch.setattr(settings, "judge_extra_headers", None, raising=False)

    assert judge_config_from_settings().extra_headers is None


def test_malformed_judge_extra_headers_is_ignored_not_fatal(monkeypatch) -> None:
    """A typo in the env must not turn every run into an error."""
    from app.core.config import settings
    from app.engines.judge import judge_config_from_settings

    monkeypatch.setattr(settings, "judge_extra_headers", "not json", raising=False)
    assert judge_config_from_settings().extra_headers is None

    monkeypatch.setattr(settings, "judge_extra_headers", '["a", "list"]', raising=False)
    assert judge_config_from_settings().extra_headers is None


def test_judge_extra_headers_never_reach_public_dump() -> None:
    """public_dump() feeds describe() / reproducibility_meta / Machine Report.
    A routing session id is operational noise there, and this is the one place a
    stray header could leak into a persisted artifact."""
    from app.engines.judge import JudgeConfig

    cfg = JudgeConfig(provider="openai", model="m", extra_headers={"x-opencode-session": "s"})

    assert cfg.extra_headers == {"x-opencode-session": "s"}
    assert "extra_headers" not in cfg.public_dump()
