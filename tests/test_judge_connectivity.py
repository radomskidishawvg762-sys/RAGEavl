"""Real probe and provider failure semantics; no external network or secrets."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.deps import get_judge_settings_service
from app.core.errors import ExtJudgeUnavailableError
from app.engines.judge import JudgeConfig
from app.engines.providers.openai import OpenAIJudge
from app.main import create_app
from app.services.judge_settings_service import JudgeSettingsService


def config(**kw):
    return JudgeConfig(provider="openai", model="saved", base_url="https://example.invalid/v1",
                       api_key=SecretStr("test-secret-never-echo"), **kw)


def service(monkeypatch):
    monkeypatch.setattr("app.services.judge_settings_service.judge_config_from_settings", config)
    return JudgeSettingsService()


def test_probe_invokes_draft_once_without_saving(monkeypatch):
    svc = service(monkeypatch)
    seen = []

    class Judge:
        async def ainvoke(self, prompt):
            seen.append(prompt)
            return "OK"

        def describe(self):
            return {"configured": True}

    def build(cfg):
        assert cfg.model == "draft-model"
        assert cfg.retry == 1 and cfg.max_tokens <= 64 and cfg.timeout <= 10
        assert cfg.api_key.get_secret_value() == "draft-secret"
        return Judge()

    monkeypatch.setattr("app.services.judge_settings_service.build_judge", build)
    result = asyncio.run(svc.test({"model": "draft-model", "api_key": SecretStr("draft-secret")}))
    assert len(seen) == 1
    assert result["test_status"] == "success" and result["reachable"] is True
    assert result["latency_ms"] >= 0
    assert svc.config().model == "saved" and not svc.has_runtime_override()
    assert "draft-secret" not in str(result) and "test-secret" not in str(result)


def test_probe_api_awaits_real_invocation(monkeypatch):
    svc = service(monkeypatch)
    calls = []

    class Judge:
        async def ainvoke(self, prompt):
            calls.append(prompt)
            return "OK"

        def describe(self):
            return {"configured": True}

    monkeypatch.setattr("app.services.judge_settings_service.build_judge", lambda cfg: Judge())
    app = create_app()
    app.dependency_overrides[get_judge_settings_service] = lambda: svc
    response = TestClient(app).post("/api/settings/judge/test", json={"model": "draft"})
    assert response.status_code == 200
    assert calls and response.json()["test_status"] == "success"


def test_unconfigured_probe_makes_no_provider_call(monkeypatch):
    svc = JudgeSettingsService()
    monkeypatch.setattr("app.services.judge_settings_service.judge_config_from_settings", JudgeConfig)
    result = asyncio.run(svc.test())
    assert result["test_status"] == "not_configured"
    assert result["error_code"] == "BIZ_JUDGE_NOT_CONFIGURED"


def test_partial_settings_update_preserves_unspecified_parameters(monkeypatch):
    svc = service(monkeypatch)
    svc.update({"temperature": 0.7, "timeout": 57, "retry": 6, "max_tokens": 4096})
    app = create_app()
    app.dependency_overrides[get_judge_settings_service] = lambda: svc
    response = TestClient(app).put("/api/settings/judge", json={"model": "replacement"})
    assert response.status_code == 200
    saved = response.json()
    assert saved["model"] == "replacement"
    assert (saved["temperature"], saved["timeout"], saved["retry"], saved["max_tokens"]) == (0.7, 57, 6, 4096)


class Completions:
    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, 0

    async def create(self, **kw):
        result = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


class Client:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def status_error(status, body="upstream error test-secret-never-echo https://private.invalid"):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"), text=body)
    return httpx.HTTPStatusError(body, request=response.request, response=response)


@pytest.mark.parametrize(("status", "body", "category", "attempts"), [
    (400, "bad request", "request_invalid", 1),
    (401, "unauthorized", "authentication", 1),
    (403, "model access denied", "permission", 1),
    (404, "unknown model", "request_invalid", 1),
    (429, "insufficient_quota weekly usage limit", "quota_exhausted", 1),
    (429, "rate limit exceeded", "rate_limited", 3),
    (503, "service unavailable", "unavailable", 3),
])
def test_provider_classifies_and_only_retries_transient_failures(monkeypatch, status, body, category, attempts):
    judge = OpenAIJudge(config(retry=3))
    completion = Completions([status_error(status, body)])
    monkeypatch.setattr(judge, "_get_client", lambda: Client(completion))

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.engines.providers.failures.asyncio.sleep", no_sleep)
    with pytest.raises(ExtJudgeUnavailableError) as caught:
        asyncio.run(judge.ainvoke("probe"))
    assert completion.calls == attempts
    error = caught.value
    assert error.context["category"] == category
    assert error.context["upstream_status"] == status
    assert error.context["retryable"] is (attempts > 1)
    assert "test-secret" not in str(error) and "private.invalid" not in str(error)


def test_sdk_has_no_nested_retries():
    judge = OpenAIJudge(config())
    client = judge._get_client()
    try:
        assert client.max_retries == 0
    finally:
        asyncio.run(client.close())


def test_empty_provider_response_is_explicit_format_error(monkeypatch):
    judge = OpenAIJudge(config(retry=3))
    completion = Completions([SimpleNamespace(choices=[])])
    monkeypatch.setattr(judge, "_get_client", lambda: Client(completion))
    with pytest.raises(ExtJudgeUnavailableError) as caught:
        asyncio.run(judge.ainvoke("probe"))
    assert completion.calls == 1
    assert caught.value.context["category"] == "response_invalid"


def test_ragas_does_not_repeat_permanent_provider_errors():
    from app.domain.schemas import EvaluationRecord
    from app.engines.base import EvalParams
    from app.engines.ragas import RagasEngine, RagasScorer

    class DeniedJudge:
        calls = 0

        async def ainvoke(self, prompt):
            self.calls += 1
            raise ExtJudgeUnavailableError("当前套餐无权限", code="EXT_JUDGE_PERMISSION_DENIED",
                                           context={"category": "permission", "retryable": False})

        def describe(self):
            return {"configured": True, "timeout": 3}

    judge = DeniedJudge()
    scorer = RagasScorer(judge)
    # Keep the old implementation's ten retries fast enough for a deterministic red test.
    scorer._metrics["faithfulness"].llm.run_config.max_wait = 0
    record = EvaluationRecord(id="r", question="收入多少？", answer="100元", contexts=["收入100元"])
    result = asyncio.run(RagasEngine(judge, scorer).evaluate(record, ["faithfulness"], EvalParams()))[0]
    assert judge.calls == 1
    assert result.error["code"] == "EXT_JUDGE_PERMISSION_DENIED"
    assert result.error["context"]["category"] == "permission"
    assert result.score is None


@pytest.mark.parametrize("kind,code", [("timeout", "EXT_JUDGE_TIMEOUT"),
                                       ("format", "EXT_JUDGE_RESPONSE_INVALID")])
def test_ragas_deadline_and_output_errors_keep_actionable_categories(kind, code):
    from ragas.exceptions import RagasOutputParserException

    from app.domain.schemas import EvaluationRecord
    from app.engines.base import EvalParams
    from app.engines.ragas import RagasEngine

    class Judge:
        def describe(self):
            return {"configured": True, "provider": "openai", "model": "saved"}

    class Scorer:
        async def score(self, metric_name, sample):
            if kind == "timeout":
                raise TimeoutError("private test-secret")
            raise RagasOutputParserException()

    record = EvaluationRecord(id="r", question="收入多少？", answer="100元", contexts=["收入100元"])
    result = asyncio.run(RagasEngine(Judge(), Scorer()).evaluate(record, ["faithfulness"], EvalParams()))[0]
    assert result.score is None and result.error["code"] == code
    assert "test-secret" not in str(result.error)


@pytest.mark.parametrize("status,category,attempts", [(400,"request_invalid",1),(403,"permission",1),(429,"rate_limited",3),(503,"unavailable",3)])
def test_anthropic_uses_the_same_classification_and_retry_policy(monkeypatch, status, category, attempts):
    from app.engines.providers.anthropic import AnthropicMessagesJudge
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["x-opencode-session"] == "routing-test"
        return httpx.Response(status, json={"error": {"message": "upstream test-secret-never-echo"}})

    factory = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr("app.engines.providers.anthropic.httpx.AsyncClient", lambda **kw: factory(transport=transport, **kw))

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.engines.providers.failures.asyncio.sleep", no_sleep)
    cfg = config(retry=3, extra_headers={"x-opencode-session": "routing-test"})
    cfg.provider = "anthropic"
    with pytest.raises(ExtJudgeUnavailableError) as caught:
        asyncio.run(AnthropicMessagesJudge(cfg).ainvoke("probe"))
    assert len(calls) == attempts
    assert caught.value.context["category"] == category
    assert "test-secret" not in str(caught.value)


def test_long_rate_limit_cooldown_does_not_block_the_task(monkeypatch):
    error = status_error(429, "rate limited")
    error.response.headers["Retry-After"] = "120"
    completion = Completions([error])
    judge = OpenAIJudge(config(retry=3))
    monkeypatch.setattr(judge, "_get_client", lambda: Client(completion))
    with pytest.raises(ExtJudgeUnavailableError) as caught:
        asyncio.run(judge.ainvoke("probe"))
    assert completion.calls == 1
    assert caught.value.context["retryable"] is True
    assert caught.value.context["retry_after_seconds"] == 120


def test_probe_preserves_safe_failure_category(monkeypatch):
    svc = service(monkeypatch)

    class Judge:
        def describe(self):
            return {"configured": True}

        async def ainvoke(self, prompt):
            raise ExtJudgeUnavailableError("当前模型无权限", code="EXT_JUDGE_PERMISSION_DENIED",
                                           context={"category": "permission", "upstream_status": 403, "retryable": False})

    monkeypatch.setattr("app.services.judge_settings_service.build_judge", lambda cfg: Judge())
    result = asyncio.run(svc.test())
    assert result["test_status"] == "failed" and result["reachable"] is False
    assert result["error_category"] == "permission" and result["upstream_status"] == 403
    assert result["error_code"] == "EXT_JUDGE_PERMISSION_DENIED"
    assert "test-secret" not in str(result)


def test_probe_cancels_a_stalled_provider_at_the_deadline(monkeypatch):
    svc = service(monkeypatch)
    cancelled = []

    class Judge:
        def describe(self):
            return {"configured": True}

        async def ainvoke(self, prompt):
            try:
                await asyncio.sleep(5)
                return "too late"
            finally:
                cancelled.append(True)

    monkeypatch.setattr("app.services.judge_settings_service.build_judge", lambda cfg: Judge())
    result = asyncio.run(svc.test({"timeout": 1}))
    assert result["error_code"] == "EXT_JUDGE_TIMEOUT" and result["retryable"] is True
    assert result["test_status"] == "failed" and cancelled == [True]
    assert 900 <= result["latency_ms"] < 3000


@pytest.mark.parametrize("error,category", [(TimeoutError("private test-secret"), "timeout"),
                                           (ConnectionError("private test-secret"), "network")])
def test_transport_failures_are_safe_and_retryable(error, category):
    from app.engines.providers.failures import provider_failure

    failure = provider_failure(error, config())
    assert failure.context["category"] == category and failure.context["retryable"] is True
    assert "test-secret" not in failure.detail


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_local_model_endpoint_bypasses_environment_proxy(monkeypatch, provider):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.engines.judge import build_judge

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(self.path)
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"model denied"}}')

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    cfg = config(timeout=1, retry=1)
    cfg.provider, cfg.base_url = provider, f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(ExtJudgeUnavailableError) as caught:
            asyncio.run(build_judge(cfg).ainvoke("probe"))
        assert caught.value.code == "EXT_JUDGE_PERMISSION_DENIED"
        assert len(received) == 1
    finally:
        server.shutdown()
        server.server_close()
