"""T-14B tests — Real RAG Input Adapter (14 mandated points).

HTTP semantics are exercised through httpx.MockTransport (adapter test seam)
plus ONE genuine localhost fake HTTP server for the API-level end-to-end
chain (no third-party network is contacted).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from app.adapters.rag_input import (
    GoldenRunMetadataAdapter,
    GoldenSample,
    HttpRagAdapter,
    RagOutput,
    build_rag_adapter,
)
from app.core.errors import (
    ExtRagAdapterHttpError,
    ExtRagAdapterParseError,
    ExtRagAdapterTimeoutError,
)
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.runner.local import LocalAsyncRunner
from app.services.evaluation_service import EvaluationService
from tests.test_evaluation_service import FakeEvaluationRepo, _record_row

INTEGRITY = ["temporal_consistency", "numerical_consistency"]


def _adapter(handler, *, retry: int = 1, timeout: float = 5.0) -> HttpRagAdapter:
    return HttpRagAdapter(
        "http://rag.test/query",
        timeout=timeout,
        retry=retry,
        transport=httpx.MockTransport(handler),
    )


def _json_handler(payload: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _raw_handler(text: str, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(status, text=text)

    return handler


def _run(coro):
    return asyncio.run(coro)


# ---- 1-6: adapter HTTP semantics ----

def test_1_http_200_valid_response_returns_ragoutput() -> None:
    adapter = _adapter(_json_handler({"answer": "A", "contexts": ["c1", "c2"]}))
    out = _run(adapter.fetch(GoldenSample(record_id="r1", question="q")))
    assert isinstance(out, RagOutput)
    assert out.answer == "A" and out.contexts == ["c1", "c2"]


def test_2_missing_answer_is_parse_error() -> None:
    adapter = _adapter(_json_handler({"contexts": ["c1"]}))
    with pytest.raises(ExtRagAdapterParseError) as ei:
        _run(adapter.fetch(GoldenSample(record_id="r1", question="q")))
    assert ei.value.code == "EXT_RAG_ADAPTER_PARSE_ERROR"


def test_3_wrong_contexts_type_is_parse_error() -> None:
    adapter = _adapter(_json_handler({"answer": "A", "contexts": [1, 2]}))
    with pytest.raises(ExtRagAdapterParseError):
        _run(adapter.fetch(GoldenSample(record_id="r1", question="q")))


def test_4_timeout_maps_to_adapter_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.TimeoutException("too slow")

    adapter = _adapter(handler)
    with pytest.raises(ExtRagAdapterTimeoutError) as ei:
        _run(adapter.fetch(GoldenSample(record_id="r1", question="q")))
    assert ei.value.code == "EXT_RAG_ADAPTER_TIMEOUT"


def test_5_http_500_maps_to_adapter_http_error() -> None:
    adapter = _adapter(_json_handler({"error": "boom"}, status=500))
    with pytest.raises(ExtRagAdapterHttpError) as ei:
        _run(adapter.fetch(GoldenSample(record_id="r1", question="q")))
    assert ei.value.code == "EXT_RAG_ADAPTER_HTTP_ERROR"


def test_6_malformed_json_is_parse_error() -> None:
    adapter = _adapter(_raw_handler("<not json>"))
    with pytest.raises(ExtRagAdapterParseError):
        _run(adapter.fetch(GoldenSample(record_id="r1", question="q")))


def test_6b_build_rag_adapter_dispatch() -> None:
    assert isinstance(build_rag_adapter({"url": "http://x"}), HttpRagAdapter)
    assert isinstance(build_rag_adapter({}), GoldenRunMetadataAdapter)
    assert isinstance(build_rag_adapter(None), GoldenRunMetadataAdapter)


# ---- 7-9: EvaluationRecord assembly ----

def _service_with(repo: FakeEvaluationRepo):
    return EvaluationService(repo)


def _run_service(repo, adapter, *, records, runner=None):
    svc = _service_with(repo)
    run = svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1",
                         reproducibility_meta={"config_version": "x"})
    summary = asyncio.run(svc.execute_run(
        run.id, engines=[IntegrityEngine()], enabled_metrics=INTEGRITY,
        params=EvalParams(), rag_adapter=adapter,
        runner=runner or LocalAsyncRunner(concurrency=2),
    ))
    return run.id, summary


def test_7_evaluation_record_assembled_correctly() -> None:
    row = _record_row("r1", "q1", "GOLDEN-BRIDGE", "2024年", ["ref ctx"])
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, [row])

    class _Stub:
        async def fetch(self, sample: GoldenSample) -> RagOutput:
            return RagOutput(answer="ADAPTER-ANSWER", contexts=["adapter-ctx"],
                             metadata={"latency_ms": 12})

    run_id, _ = _run_service(repo, _Stub(), records=[row])
    result = repo.eval_results[0]
    assert result["question"] == "q1"          # <- DatasetRecord
    assert result["answer"] == "ADAPTER-ANSWER"  # <- adapter
    assert result["contexts"] == ["adapter-ctx"]  # <- adapter
    assert result["reference_answer"] == "2024年"   # <- DatasetRecord
    assert result["reference_contexts"] == ["ref ctx"]  # <- DatasetRecord
    assert result["is_failure"] in (True, False)


def test_8_reference_data_still_from_dataset_record() -> None:
    row = _record_row("r1", "q1", "x", "2024年度", ["golden-ref"])
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, [row])

    class _Stub:
        async def fetch(self, sample: GoldenSample) -> RagOutput:
            return RagOutput(answer="a", contexts=["c"], metadata={"reference_answer": "HACK"})

    _run_service(repo, _Stub(), records=[row])
    assert repo.eval_results[0]["reference_answer"] == "2024年度"  # adapter cannot override
    assert repo.eval_results[0]["reference_contexts"] == ["golden-ref"]


def test_9_answer_and_contexts_come_from_adapter() -> None:
    row = _record_row("r1", "q1", "x", "2亿元")
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, [row])
    adapter = _adapter(_json_handler({"answer": "1亿元", "contexts": ["ctx-a"]}))
    _run_service(repo, adapter, records=[row])
    assert repo.eval_results[0]["answer"] == "1亿元"
    assert repo.eval_results[0]["contexts"] == ["ctx-a"]


# ---- 10: metadata bridge is NOT the formal path ----

def test_10_formal_http_path_ignores_metadata_bridge() -> None:
    """Records carry metadata answer/contexts (legacy bridge); with the formal
    HTTP adapter configured the persisted answer MUST come from the adapter."""
    row = _record_row("r1", "q1", "BRIDGE-ANSWER", "2024年度")
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, [row])
    adapter = _adapter(_json_handler({"answer": "ADAPTER-ANSWER", "contexts": ["http-ctx"]}))
    _run_service(repo, adapter, records=[row])
    assert repo.eval_results[0]["answer"] == "ADAPTER-ANSWER"
    assert repo.eval_results[0]["contexts"] == ["http-ctx"]


def test_10b_goldenrun_adapter_is_the_only_bridge_user() -> None:
    golden = GoldenRunMetadataAdapter()
    out = _run(golden.fetch(GoldenSample(record_id="r", question="q",
                                         metadata={"answer": "A", "contexts": ["c"]})))
    assert out.answer == "A" and out.contexts == ["c"]
    src = Path("app/services/evaluation_service.py").read_text(encoding="utf-8")
    assert 'metadata.get("answer")' not in src  # service never reads the bridge


# ---- 11: adapter boundary ----

def test_11_adapter_never_enters_domain_or_runner_layer() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        # HTTP lives only at the two sanctioned external boundaries:
        # app/adapters (RAG input) and app/engines/providers (LLM judge —
        # openai.py's docstring sanctions "a bare httpx fallback" there).
        if "adapters" in path.parts or "providers" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "import httpx" in line or "from httpx" in line:
                offenders.append(f"{path.relative_to(app_dir)}:{lineno}")
    assert offenders == []  # HTTP only inside adapters / engine providers


# ---- 12: failed fetch never fabricates evaluation ----

def test_12_failed_rag_fetch_yields_no_fabricated_results() -> None:
    rows = [
        _record_row("ok", "q", "x", "2024年度", None),
        _record_row("bad", "q", "x", "2024年度", None),
    ]
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 2, "version": "v1"}}, rows)

    class _SelectiveFail:
        async def fetch(self, sample: GoldenSample) -> RagOutput:
            if sample.record_id == "bad":
                raise ExtRagAdapterTimeoutError("rag timeout")
            return RagOutput(answer="2024年度", contexts=["c"])

    run_id, summary = _run_service(repo, _SelectiveFail(), records=rows,
                                   runner=LocalAsyncRunner(concurrency=1))
    assert summary["status"] == "completed_with_errors"
    assert summary["errors"] == 1 and summary["evaluated"] == 1
    persisted = {r["record_id"] for r in repo.eval_results}
    assert persisted == {"ok"}  # failed sample produced NO evaluation rows
    assert repo.metric_results  # healthy sample still evaluated
    details = repo.runs[run_id]["error_summary"]["details"]
    assert details[0]["error"]["code"] == "EXT_RAG_ADAPTER_TIMEOUT"


# ---- 13: runner concurrency bounds RAG requests ----

def test_13_runner_concurrency_bounds_rag_requests() -> None:
    rows = [_record_row(f"r{i}", "q", "x", "2024年度") for i in range(4)]
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 4, "version": "v1"}}, rows)
    state = {"inflight": 0, "max": 0}

    class _Counting:
        async def fetch(self, sample: GoldenSample) -> RagOutput:
            state["inflight"] += 1
            state["max"] = max(state["max"], state["inflight"])
            await asyncio.sleep(0.01)
            state["inflight"] -= 1
            return RagOutput(answer="2024年度", contexts=["c"])

    _run_service(repo, _Counting(), records=rows, runner=LocalAsyncRunner(concurrency=1))
    assert state["max"] == 1  # semaphore governs RAG fetching — no second pool

    state.update({"inflight": 0, "max": 0})
    repo2 = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 4, "version": "v1"}}, rows)
    _run_service(repo2, _Counting(), records=rows, runner=LocalAsyncRunner(concurrency=2))
    assert state["max"] <= 2


# ---- 14: API -> Service -> Adapter -> Runner (localhost fake RAG server) ----

class _RagHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        payload = {"answer": f"HTTP-ANSWER:{body.get('question', '')}", "contexts": ["http-ctx"]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # silence test server logging
        return


def test_14_api_chain_uses_real_http_rag_adapter() -> None:
    from fastapi.testclient import TestClient

    from app.api.deps import get_config_service, get_evaluation_launcher, get_evaluation_service
    from app.main import app
    from app.services.config_service import ConfigService

    server = HTTPServer(("127.0.0.1", 0), _RagHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "domains").mkdir()
            (base / "evaluations").mkdir()
            (base / "system.yaml").write_text(
                "system:\n"
                f"  rag_input:\n    url: http://127.0.0.1:{port}/query\n    timeout: 5\n    retry: 1\n",
                encoding="utf-8",
            )
            (base / "domains/general.yaml").write_text("domain: general\n", encoding="utf-8")
            (base / "evaluations/default.yaml").write_text(
                "metrics:\n  temporal_consistency: { enabled: true }\n"
                "  numerical_consistency: { enabled: true }\n",
                encoding="utf-8",
            )
            config_service = ConfigService(base)
            rows = [_record_row("r1", "2024年营收多少", "BRIDGE", "2024年度")]
            repo = FakeEvaluationRepo(
                {"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, rows,
                configs={"c1": type("C", (), {"domain_config": {"domain": "general"},
                                              "profile_config": {"profile": "default"}})()},
            )

            errors: list[BaseException] = []

            def _launcher():
                def launch(run_id, *, enabled_metrics, params):
                    async def _bg():
                        from app.adapters.rag_input import build_rag_adapter
                        from app.engines.factory import build_engines

                        try:
                            engines, _ = build_engines(enabled_metrics)
                            rag = build_rag_adapter(params.extra.get("rag_input"))
                            await EvaluationService(repo).execute_run(
                                run_id, engines=engines, enabled_metrics=enabled_metrics,
                                params=params, rag_adapter=rag,
                                runner=LocalAsyncRunner(concurrency=1),
                            )
                        except BaseException as e:  # noqa: BLE001 — surface task errors
                            errors.append(e)
                            raise

                    return asyncio.get_running_loop().create_task(_bg())

                return launch

            app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(repo)
            app.dependency_overrides[get_config_service] = lambda: config_service
            app.dependency_overrides[get_evaluation_launcher] = _launcher
            try:
                # context manager keeps the TestClient portal alive so the
                # background run task is NOT cancelled at response time
                with TestClient(app) as client:
                    resp = client.post("/api/evaluations",
                                       json={"project_id": "p1", "dataset_id": "ds1", "config_id": "c1"})
                    assert resp.status_code == 202
                    run_id = resp.json()["run_id"]
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if repo.runs[run_id]["status"] != "running":
                            break
                        time.sleep(0.01)
                assert repo.runs[run_id]["status"] == "completed" or errors, (
                    f"run stuck: {repo.runs[run_id]['status']}; task errors={errors}"
                )
                assert not errors, errors
                assert repo.runs[run_id]["status"] == "completed"
                assert repo.eval_results[0]["answer"].startswith("HTTP-ANSWER:")
                assert repo.eval_results[0]["contexts"] == ["http-ctx"]
                assert repo.eval_results[0]["is_failure"] is False
                assert len(repo.metric_results) == 2
            finally:
                app.dependency_overrides.clear()
    finally:
        server.shutdown()
        server.server_close()
