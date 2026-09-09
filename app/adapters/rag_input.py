"""RAG Input Adapter boundary (T-14B).

Formal input contract:
  DatasetRecord   -> question / reference_answer / reference_contexts / metadata
  RagInputAdapter -> answer / contexts            (ACTUAL RAG output)
  assembly        -> EvaluationRecord

DatasetRecord has NO answer/contexts columns and must never gain them. The
metadata bridge survives ONLY behind the explicitly-named GoldenRunMetadata-
Adapter (test/golden-run compatibility) — the formal path is HttpRagAdapter
(system.rag_input.url configured) or any other RagInputAdapter implementation.
HTTP details (httpx) live ONLY inside HttpRagAdapter; Domain / Service /
Runner never import them.

Errors (EXT_ family, distinct from Judge codes — T-14B §七):
  EXT_RAG_INPUT_NOT_FOUND    upstream reports the sample unknown
  EXT_RAG_ADAPTER_TIMEOUT    request exceeded timeout
  EXT_RAG_ADAPTER_HTTP_ERROR non-2xx upstream response
  EXT_RAG_ADAPTER_PARSE_ERROR 2xx but body violates the RagOutput contract

A failed fetch NEVER fabricates answer/contexts — the record enters the
evaluation-execution-error path (FR-22 isolation) instead.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.errors import (
    ExtRagAdapterHttpError,
    ExtRagAdapterParseError,
    ExtRagAdapterTimeoutError,
    ExtRagInputNotFoundError,
)


class GoldenSample(BaseModel):
    """The golden-side view handed to adapters (from DatasetRecord)."""

    record_id: str
    question: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagOutput(BaseModel):
    """ACTUAL RAG output for one sample (the only sanctioned runtime source)."""

    answer: str
    contexts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class RagInputAdapter(Protocol):
    """Minimal boundary (T-14B §二). Stateless; concurrency is bounded by the
    LocalAsyncRunner semaphore — adapters must NOT spawn their own pools."""

    async def fetch(self, sample: GoldenSample) -> RagOutput: ...


class GoldenRunMetadataAdapter:
    """TEST / GOLDEN-RUN compatibility ONLY (T-14B §八).

    Reads answer/contexts from dataset_records.metadata_ — the legacy bridge,
    now confined behind this explicitly-named adapter. Never wire this in
    production; prefer HttpRagAdapter."""

    async def fetch(self, sample: GoldenSample) -> RagOutput:
        meta = sample.metadata or {}
        answer = meta.get("answer")
        contexts = meta.get("contexts")
        return RagOutput(
            answer=answer if isinstance(answer, str) else "",
            contexts=[c for c in contexts if isinstance(c, str)] if isinstance(contexts, list) else [],
            metadata={},
        )


class HttpRagAdapter:
    """MVP formal adapter (T-14B §五). Fixed contract:
      POST <url>  body {"question": ...}
      200 -> {"answer": str, "contexts": [str, ...]}
    request headers / auth templates / response mapping are P1 by design."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 30,
        retry: int = 2,
        transport: Any = None,
    ) -> None:
        # `transport` is a TEST SEAM (httpx.MockTransport) — production callers
        # never pass it; real traffic goes through a fresh AsyncClient.
        self._url = url
        self._timeout = timeout
        self._retry = max(1, retry)
        self._transport = transport

    async def fetch(self, sample: GoldenSample) -> RagOutput:
        import httpx  # lazy — network layer stays inside this adapter

        last_error: Exception | None = None
        for attempt in range(self._retry):
            try:
                # trust_env=False: the URL is EXPLICITLY user-configured —
                # transparently rerouting it through a system/env proxy silently
                # breaks loopback RAG endpoints (the primary MVP deployment:
                # Windows registry proxy 127.0.0.1:7890 answers loopback POSTs
                # with 502). Direct connection is the honest MVP default.
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport if self._transport is not None else None,
                    trust_env=False,
                ) as client:
                    resp = await client.post(
                        self._url, json={"question": sample.question}
                    )
                if resp.status_code == 404:
                    raise ExtRagInputNotFoundError(
                        f"RAG system reports sample unknown: {sample.record_id}",
                        context={"record_id": sample.record_id, "url": self._url},
                    )
                if resp.status_code >= 400:
                    raise ExtRagAdapterHttpError(
                        f"RAG system returned HTTP {resp.status_code}",
                        context={"record_id": sample.record_id, "status": resp.status_code},
                    )
                return self._parse(resp, sample.record_id)
            except (ExtRagInputNotFoundError, ExtRagAdapterParseError):
                raise  # deterministic per-sample outcomes — no retry
            except (ExtRagAdapterHttpError, ExtRagAdapterTimeoutError) as e:
                last_error = e
                if attempt < self._retry - 1:
                    import asyncio

                    await asyncio.sleep(0.2 * (attempt + 1))
            except Exception as e:  # noqa: BLE001 — transport (connect/timeout) -> TIMEOUT
                last_error = e
                if attempt < self._retry - 1:
                    import asyncio

                    await asyncio.sleep(0.2 * (attempt + 1))
        raise last_error if isinstance(last_error, (ExtRagAdapterTimeoutError, ExtRagAdapterHttpError)) else ExtRagAdapterTimeoutError(
            f"RAG adapter request failed: {type(last_error).__name__}: {last_error}",
            context={"record_id": sample.record_id, "url": self._url},
        )

    def _parse(self, resp: Any, record_id: str) -> RagOutput:
        try:
            body = resp.json()
        except Exception as e:  # noqa: BLE001 — malformed JSON
            raise ExtRagAdapterParseError(
                f"RAG response is not valid JSON: {type(e).__name__}",
                context={"record_id": record_id},
            ) from e
        if not isinstance(body, dict):
            raise ExtRagAdapterParseError(
                "RAG response must be a JSON object", context={"record_id": record_id}
            )
        answer = body.get("answer")
        contexts = body.get("contexts", [])
        if not isinstance(answer, str):
            raise ExtRagAdapterParseError(
                "RAG response field 'answer' must be a string",
                context={"record_id": record_id},
            )
        if not isinstance(contexts, list) or any(not isinstance(c, str) for c in contexts):
            raise ExtRagAdapterParseError(
                "RAG response field 'contexts' must be a list of strings",
                context={"record_id": record_id},
            )
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        return RagOutput(answer=answer, contexts=contexts, metadata=metadata)


def build_rag_adapter(cfg: dict | None) -> RagInputAdapter:
    """Effective rag_input -> adapter (Phase D, task §6): explicit mode wins;
    legacy configs infer mode from url presence. golden_replay and HTTP are
    SEPARATE modes — a golden_replay config with a url stays golden replay, and
    http without a url is a configuration error, never a silent fallback."""
    cfg = cfg or {}
    url = (cfg.get("url") or "").strip()
    mode = cfg.get("mode") or ("http" if url else "golden_replay")
    if mode == "golden_replay":
        return GoldenRunMetadataAdapter()
    if not url:
        from app.core.errors import ConfigInvalidError

        raise ConfigInvalidError(
            "rag_input.mode=http requires a non-empty url",
            code="BIZ_CONFIG_INVALID",
        )
    return HttpRagAdapter(url, timeout=float(cfg.get("timeout", 30)), retry=int(cfg.get("retry", 2)))
