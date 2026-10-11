"""OpenAI-compatible Chat Completions judge adapter (T-14A).

Spec Appendix B pins the provider family: `provider: openai` with the
Chat Completions protocol — this adapter also serves protocol-compatible
endpoints (deepseek / one-api style gateways) via the same config shape.
The OpenAI SDK stays INSIDE this module; nothing
else in the codebase may import it (T-14A §二, ADR SDK isolation).

Errors: transport/API failures -> classified ExtJudgeUnavailableError codes.
Configuration absence never reaches here (build_judge degrades to
PlaceholderJudge first). The api_key is a SecretStr, never logged/described.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ExtJudgeUnavailableError
from app.engines.judge import JudgeConfig
from app.engines.providers.failures import InvalidJudgeResponse, invoke_with_retry, provider_failure
from app.engines.providers.transport import uses_environment_proxy


class OpenAIJudge:
    def __init__(self, config: JudgeConfig) -> None:
        self._config = config
        self._sdk_available: bool | None = None

    # ---- JudgeClient protocol ----

    async def ainvoke(self, prompt: str) -> str:
        try:
            return await self._invoke(prompt)
        except ExtJudgeUnavailableError:
            raise
        except Exception as error:  # noqa: BLE001 -- client construction/configuration boundary
            raise provider_failure(error, self._config) from None

    def describe(self) -> dict[str, Any]:
        return {**self._config.public_dump(), "configured": True}

    # ---- internals ----

    def _get_client(self):
        """Build an SDK client for ONE call. Lazy import — the SDK stays in this module."""
        try:
            from openai import (  # lazy import — SDK isolated here
                AsyncOpenAI,
                DefaultAsyncHttpxClient,
            )
        except ImportError as e:  # pragma: no cover - SDK optional extra
            raise ExtJudgeUnavailableError(
                "openai SDK not installed (pip install -e '.[eval]')",
                context={"provider": "openai"},
            ) from e
        key = self._config.api_key.get_secret_value() if self._config.api_key else None
        return AsyncOpenAI(
            api_key=key,
            base_url=self._config.base_url or None,
            timeout=self._config.timeout,
            max_retries=0,  # One retry owner: invoke_with_retry, not nested SDK retries.
            http_client=DefaultAsyncHttpxClient(trust_env=uses_environment_proxy(self._config.base_url)),
            # Gateways may require routing headers (opencode zen: x-opencode-session,
            # absent -> 400 MissingSessionID). None keeps the SDK defaults.
            default_headers=self._config.extra_headers or None,
        )

    async def _invoke(self, prompt: str) -> str:
        async with self._get_client() as client:
            async def call() -> str:
                response = await client.chat.completions.create(
                    model=self._config.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                )
                choices = getattr(response, "choices", None)
                text = getattr(getattr(choices[0], "message", None), "content", None) if choices else None
                if not isinstance(text, str) or not text.strip():
                    raise InvalidJudgeResponse()
                return text

            return await invoke_with_retry(call, self._config)
