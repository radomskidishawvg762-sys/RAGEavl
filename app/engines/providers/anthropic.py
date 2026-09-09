"""Anthropic Messages judge adapter (T-19).

opencode.ai/zen/go is an Anthropic-Messages-compatible gateway:
  POST {base_url}/v1/messages
  headers: x-api-key, anthropic-version
  body: {model, max_tokens, temperature?, messages: [{role: user, content}]}

Plain httpx — no anthropic SDK dependency (SDK isolation like openai.py).
Errors: transport/API failures -> ExtJudgeUnavailableError. The api_key is a
SecretStr, never logged/described.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.errors import ExtJudgeUnavailableError
from app.engines.judge import JudgeConfig

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicMessagesJudge:
    def __init__(self, config: JudgeConfig) -> None:
        self._config = config

    # ---- JudgeClient protocol ----

    async def ainvoke(self, prompt: str) -> str:
        try:
            return await self._invoke(prompt)
        except ExtJudgeUnavailableError:
            raise
        except Exception as e:  # noqa: BLE001 — any transport failure -> EXT
            raise ExtJudgeUnavailableError(
                f"judge provider call failed: {type(e).__name__}: {e}",
                context={"provider": self._config.provider, "model": self._config.model},
            ) from e

    def describe(self) -> dict[str, Any]:
        return {**self._config.public_dump(), "configured": True}

    # ---- internals ----

    async def _invoke(self, prompt: str) -> str:
        base = self._config.base_url.rstrip("/")
        key = self._config.api_key.get_secret_value() if self._config.api_key else ""
        headers = {
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._config.temperature != 0:
            body["temperature"] = self._config.temperature
        retries = max(1, self._config.retry)
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            for attempt in range(retries):
                try:
                    resp = await client.post(f"{base}/v1/messages", headers=headers, json=body)
                    if resp.status_code >= 400:
                        raise ExtJudgeUnavailableError(
                            f"anthropic messages endpoint HTTP {resp.status_code}: {resp.text[:200]}",
                            context={"provider": self._config.provider, "model": self._config.model},
                        )
                    data = resp.json()
                    blocks = data.get("content") or []
                    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                except ExtJudgeUnavailableError:
                    raise
                except Exception as e:  # noqa: BLE001 — retry, then wrap as EXT
                    last_error = e
                    if attempt < retries - 1:
                        await asyncio.sleep(0.5 * (attempt + 1))
        raise ExtJudgeUnavailableError(
            f"judge provider call failed after {retries} attempts: "
            f"{type(last_error).__name__}: {last_error}",
            context={"provider": self._config.provider, "model": self._config.model},
        ) from last_error
