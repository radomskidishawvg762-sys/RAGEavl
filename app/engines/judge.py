"""Judge LLM client (LLM-as-a-Judge, FR-13/15) — boundary and provider dispatch.

The judge SDK bridge is isolated here and in app/engines/providers/: business
code depends ONLY on the JudgeClient Protocol — RagasEngine never imports a
Provider SDK (T-14A §二).

Error semantics (T-14A §六) — three distinct states, never conflated:
  - BIZ_JUDGE_NOT_CONFIGURED  configuration absence (provider/model/api_key)
                              -> PlaceholderJudge raises this; RagasEngine emits
                                 error code BIZ_JUDGE_NOT_CONFIGURED
  - EXT_JUDGE_UNAVAILABLE     provider configured but unreachable/failing
                              -> OpenAIJudge wraps transport/API errors
  - SYS_METRIC_ERROR          internal code bug (RagasEngine fallback)

Secrets: Environment Variable -> Pydantic SecretStr -> Judge Adapter. YAML
never holds keys; describe() and reproducibility output NEVER include api_key.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, SecretStr

from app.core.config import settings
from app.core.errors import JudgeNotConfiguredError

logger = logging.getLogger(__name__)

_JUDGE_DESCRIBE_FIELDS = (
    "provider", "model", "model_version", "base_url",
    "temperature", "max_tokens", "timeout", "retry",
)


class JudgeConfig(BaseModel):
    """system.yaml system.judge. api_key is SecretStr sourced from the
    environment ONLY (${JUDGE_API_KEY} -> Settings.judge_api_key) — never YAML,
    never DB, never logs."""

    provider: str = ""
    model: str = ""
    model_version: str = ""
    base_url: str = ""  # OpenAI-compatible gateway endpoint (T-19), e.g. opencode.ai/zen/go
    temperature: float = 0
    max_tokens: int = 1024
    timeout: int = 30
    retry: int = 3
    api_key: SecretStr | None = None
    # Extra HTTP headers for the gateway (NOT in _JUDGE_DESCRIBE_FIELDS: a routing
    # session id is operational noise in a snapshot, not a reproducibility fact).
    extra_headers: dict[str, str] | None = None

    def public_dump(self) -> dict[str, Any]:
        """All fields EXCEPT api_key — the only shape allowed into describe() /
        reproducibility_meta / Machine Report (T-14A §三)."""
        return {k: getattr(self, k) for k in _JUDGE_DESCRIBE_FIELDS}


@runtime_checkable
class JudgeClient(Protocol):
    """Judge boundary. describe() feeds reproducibility_meta (FR-13:
    judge_model / judge_model_version / judge params) — never a secret."""

    async def ainvoke(self, prompt: str) -> str: ...

    def describe(self) -> dict[str, Any]: ...


class PlaceholderJudge:
    """Unconfigured judge: describe() still reports config so run snapshots stay
    structurally complete; calls fail with BIZ_JUDGE_NOT_CONFIGURED."""

    def __init__(self, config: JudgeConfig) -> None:
        self._config = config

    async def ainvoke(self, prompt: str) -> str:
        raise JudgeNotConfiguredError(
            "judge provider/model not configured",
            context={"reason": "judge_not_configured",
                     "provider": self._config.provider or "(empty)"},
        )

    def describe(self) -> dict[str, Any]:
        # `configured: False` lets RagasEngine short-circuit BEFORE entering
        # RAGAS internals (which would otherwise swallow the error and stall
        # until its internal timeout) — T-14A §五/§六.
        return {**self._config.public_dump(), "configured": False}


class EmbeddingsClient(Protocol):
    """RAGAS answer_relevancy needs an embedding model in addition to the judge
    LLM. Same isolation rules apply."""

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def describe(self) -> dict[str, Any]: ...


class PlaceholderEmbeddings:
    def __init__(self, model: str) -> None:
        self._model = model

    def _fail(self) -> None:
        raise JudgeNotConfiguredError(
            "embeddings model not configured",
            context={"reason": "embeddings_not_configured",
                     "embeddings_model": self._model or "(empty)"},
        )

    def embed_query(self, text: str) -> list[float]:
        self._fail()
        return []  # pragma: no cover

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._fail()
        return []  # pragma: no cover

    def describe(self) -> dict[str, Any]:
        return {"embeddings_model": self._model}


def judge_extra_headers() -> dict[str, str] | None:
    """JUDGE_EXTRA_HEADERS (JSON object) -> header dict, or None.

    Needed because some OpenAI-compatible gateways require a routing header:
    opencode zen answers every call with `400 MissingSessionID` unless
    `x-opencode-session` is present, so a correctly-configured judge was unusable
    and all four RAGAS metrics errored out on every record.
    """
    raw = (settings.judge_extra_headers or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("JUDGE_EXTRA_HEADERS is not valid JSON — ignoring it")
        return None
    if not isinstance(parsed, dict):
        logger.warning("JUDGE_EXTRA_HEADERS must be a JSON object — ignoring it")
        return None
    headers = {str(k): str(v) for k, v in parsed.items()}
    return headers or None


def judge_config_from_settings() -> JudgeConfig:
    """JUDGE_* env (Settings) is the secret-bearing source; YAML references the
    same values via ${JUDGE_*} tokens (config/system.yaml)."""
    return JudgeConfig(
        provider=settings.judge_provider or "",
        model=settings.judge_model or "",
        model_version=settings.judge_model_version or "",
        base_url=settings.judge_base_url or "",
        temperature=0,
        max_tokens=1024,
        timeout=30,
        retry=3,
        api_key=settings.judge_api_key,
        extra_headers=judge_extra_headers(),
    )


def judge_config_from_merged(merged: dict) -> JudgeConfig:
    """Full system.judge read (T-14A §三): provider/model/model_version/
    temperature/max_tokens/timeout/retry from merged config, api_key ALWAYS
    from Settings env (YAML carries no secrets by policy)."""
    judge = merged.get("system", {}).get("judge", {})
    if not isinstance(judge, dict):
        judge = {}
    cfg = judge_config_from_settings()
    return cfg.model_copy(
        update={
            "provider": judge.get("provider") or cfg.provider,
            "model": judge.get("model") or cfg.model,
            "model_version": judge.get("model_version") or cfg.model_version,
            "base_url": judge.get("base_url") or cfg.base_url,
            "temperature": float(judge.get("temperature", cfg.temperature)),
            "max_tokens": int(judge.get("max_tokens", cfg.max_tokens)),
            "timeout": int(judge.get("timeout", cfg.timeout)),
            "retry": int(judge.get("retry", cfg.retry)),
        }
    )


def build_judge(config: JudgeConfig) -> JudgeClient:
    """Provider dispatch lives ONLY here. Unknown/empty provider -> placeholder
    (BIZ_JUDGE_NOT_CONFIGURED); a mapped provider missing model/api_key also
    degrades to placeholder rather than guessing (T-14A §四)."""
    provider = config.provider.strip().lower()
    if provider in _PROVIDER_ADAPTERS:
        adapter = _PROVIDER_ADAPTERS[provider]
        if config.model and config.api_key:
            return adapter(config)
        return PlaceholderJudge(config)
    return PlaceholderJudge(config)


def build_embeddings(config: JudgeConfig) -> EmbeddingsClient:
    return PlaceholderEmbeddings(config.model)


# ---- provider registration (T-14A: Spec-mandated OpenAI protocol; T-19: Anthropic Messages) ----

def _build_openai(config: JudgeConfig) -> JudgeClient:
    from app.engines.providers.openai import OpenAIJudge

    return OpenAIJudge(config)


def _build_anthropic(config: JudgeConfig) -> JudgeClient:
    from app.engines.providers.anthropic import AnthropicMessagesJudge

    return AnthropicMessagesJudge(config)


_PROVIDER_ADAPTERS: dict[str, Any] = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
}
