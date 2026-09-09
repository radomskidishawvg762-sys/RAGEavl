"""OpenAI-compatible Chat Completions judge adapter (T-14A).

Spec Appendix B pins the provider family: `provider: openai` with the
Chat Completions protocol — this adapter also serves protocol-compatible
endpoints (deepseek / one-api style gateways) via the same config shape.
The OpenAI SDK (or a bare httpx fallback) stays INSIDE this module; nothing
else in the codebase may import it (T-14A §二, ADR SDK isolation).

Errors: transport/API failures -> ExtJudgeUnavailableError (EXT_JUDGE_UNAVAILABLE).
Configuration absence never reaches here (build_judge degrades to
PlaceholderJudge first). The api_key is a SecretStr, never logged/described.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import ExtJudgeUnavailableError
from app.engines.judge import JudgeConfig


class OpenAIJudge:
    def __init__(self, config: JudgeConfig) -> None:
        self._config = config
        self._client = None  # lazy: SDK imported only on first call
        self._sdk_available: bool | None = None

    # ---- JudgeClient protocol ----

    async def ainvoke(self, prompt: str) -> str:
        try:
            return await self._invoke(prompt)
        except ExtJudgeUnavailableError:
            raise
        except Exception as e:  # noqa: BLE001 — any SDK/transport failure -> EXT
            raise ExtJudgeUnavailableError(
                f"judge provider call failed: {type(e).__name__}: {e}",
                context={"provider": self._config.provider, "model": self._config.model},
            ) from e

    def describe(self) -> dict[str, Any]:
        return {**self._config.public_dump(), "configured": True}

    # ---- internals ----

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI  # lazy import — SDK isolated here
            except ImportError as e:  # pragma: no cover - SDK optional extra
                raise ExtJudgeUnavailableError(
                    "openai SDK not installed (pip install -e '.[eval]')",
                    context={"provider": "openai"},
                ) from e
            key = self._config.api_key.get_secret_value() if self._config.api_key else None
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=self._config.base_url or None,
                timeout=self._config.timeout,
            )
        return self._client

    async def _invoke(self, prompt: str) -> str:
        client = self._get_client()
        retries = max(1, self._config.retry)
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                resp = await client.chat.completions.create(
                    model=self._config.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001 — retry, then wrap as EXT
                last_error = e
                if attempt < retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise ExtJudgeUnavailableError(
            f"judge provider call failed after {retries} attempts: {type(last_error).__name__}: {last_error}",
            context={"provider": self._config.provider, "model": self._config.model},
        ) from last_error
